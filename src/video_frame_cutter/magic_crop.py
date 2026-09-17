"""Shrink a crop to the text regions inside it.

Visual detection only: this looks for small, stroke-like connected components and the
lines they form. It does not recognise characters and does not use OCR.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image
from skimage.measure import label, regionprops
from skimage.morphology import dilation

from .models import CropRect


@dataclass(frozen=True)
class MagicCropSettings:
    work_width: int = 1280
    tolerance: int = 45
    border_band: float = 0.02
    border_fraction: float = 0.004
    min_height: float = 0.008
    min_area: int = 12
    max_height: float = 0.28
    max_width: float = 0.55
    block_area: float = 0.020
    block_fill: float = 0.60
    gap_ratio: float = 0.9
    min_group_ink: int = 25
    mass_fraction: float = 0.005
    padding: float = 0.01
    aspect: tuple[int, int] | None = None


DEFAULT_SETTINGS = MagicCropSettings()


@dataclass(frozen=True)
class MagicCropResult:
    crop: CropRect
    kind: str
    lines: int = 0


def _median_colour(pixels):
    return np.median(pixels.reshape(-1, 3), axis=0)


def _border_colour(region, band):
    height, width, _ = region.shape
    margin = max(1, round(min(height, width) * band))
    edges = (region[:margin], region[-margin:], region[:, :margin], region[:, -margin:])
    return _median_colour(np.concatenate([edge.reshape(-1, 3) for edge in edges]))


def _span(mask, row_minimum, column_minimum):
    """Outermost rows and columns whose set-pixel count reaches the given minimum."""
    rows = np.flatnonzero(mask.sum(axis=1) >= row_minimum)
    columns = np.flatnonzero(mask.sum(axis=0) >= column_minimum)
    if not rows.size or not columns.size:
        return None
    return (int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1)


def content_box(region, settings=DEFAULT_SETTINGS):
    """Box that drops uniform background margins such as letterbox bars."""
    height, width, _ = region.shape
    difference = np.abs(region - _border_colour(region, settings.border_band)).max(axis=2)
    mask = difference > settings.tolerance
    row_minimum = max(1, settings.border_fraction * width)
    column_minimum = max(1, settings.border_fraction * height)
    return _span(mask, row_minimum, column_minimum)


def ink_mask(region, settings=DEFAULT_SETTINGS):
    """Pixels that differ from the dominant colour of the region."""
    return np.abs(region - _median_colour(region)).max(axis=2) > settings.tolerance


def text_mask(ink, settings=DEFAULT_SETTINGS):
    """Keep glyph-shaped components, then merge each row of them into one line."""
    height, width = ink.shape
    kept = np.zeros_like(ink)
    heights = []
    for part in regionprops(label(ink, connectivity=2)):
        top, left, bottom, right = part.bbox
        box_height, box_width = bottom - top, right - left
        area = box_height * box_width
        if box_height < settings.min_height * height or part.area < settings.min_area:
            continue
        if box_height > settings.max_height * height or box_width > settings.max_width * width:
            continue
        if area > settings.block_area * height * width and part.area / area > settings.block_fill:
            continue
        kept[part.slice] |= part.image
        heights.append(box_height)
    if not heights:
        return kept, 0
    gap = max(3, round(float(np.median(heights)) * settings.gap_ratio))
    selected = np.zeros_like(kept)
    count = 0
    for group in regionprops(label(dilation(kept, footprint=np.ones((1, gap), bool)))):
        top, left, bottom, right = group.bbox
        window = kept[top:bottom, left:right]
        if window.sum() < settings.min_group_ink:
            continue
        selected[top:bottom, left:right] |= window
        count += 1
    return selected, count


def mass_box(mask, settings=DEFAULT_SETTINGS):
    """Tightest box left after dropping outer rows and columns that hold almost no ink."""
    total = int(mask.sum())
    if not total:
        return None
    rows = np.cumsum(mask.sum(axis=1))
    columns = np.cumsum(mask.sum(axis=0))
    margin = total * settings.mass_fraction
    return (
        int(np.searchsorted(columns, margin)),
        int(np.searchsorted(rows, margin)),
        int(np.searchsorted(columns, total - margin)) + 1,
        int(np.searchsorted(rows, total - margin)) + 1,
    )


def detect_region(region, settings=DEFAULT_SETTINGS):
    """Pixel box of the text in ``region``, falling back to its content box."""
    content = content_box(region, settings)
    if content is None:
        return None, "unchanged", 0
    left, top, right, bottom = content
    mask, lines = text_mask(ink_mask(region[top:bottom, left:right], settings), settings)
    box = mass_box(mask, settings) if lines else None
    if box is None:
        return content, "content", 0
    return (left + box[0], top + box[1], left + box[2], top + box[3]), "text", lines


def _clamped(left, top, right, bottom, bounds):
    left = min(max(left, bounds.left), bounds.right)
    right = min(max(right, bounds.left), bounds.right)
    top = min(max(top, bounds.top), bounds.bottom)
    bottom = min(max(bottom, bounds.top), bounds.bottom)
    if right <= left:
        left, right = bounds.left, bounds.right
    if bottom <= top:
        top, bottom = bounds.top, bounds.bottom
    return CropRect(left, top, right, bottom)


def _fit(low, high, span, start_limit, end_limit):
    """Centre ``span`` on the same midpoint, sliding it inside the limits when it fits."""
    if span >= end_limit - start_limit:
        return start_limit, end_limit
    start = min(max((low + high) / 2 - span / 2, start_limit), end_limit - span)
    return start, start + span


def expand_to_aspect(rect, aspect, bounds, size):
    """Grow ``rect`` about its centre to ``aspect``, never leaving ``bounds``.

    When ``bounds`` cannot hold the target ratio the result fills that axis instead, so
    the ratio is approached rather than guaranteed.
    """
    width, height = size
    target = aspect[0] / aspect[1]
    box_width = (rect.right - rect.left) * width
    box_height = (rect.bottom - rect.top) * height
    if box_width / box_height < target:
        box_width = box_height * target
    else:
        box_height = box_width / target
    left, right = _fit(rect.left, rect.right, box_width / width, bounds.left, bounds.right)
    top, bottom = _fit(rect.top, rect.bottom, box_height / height, bounds.top, bounds.bottom)
    return CropRect(left, top, right, bottom)


def detect_crop(image, crop=None, settings=DEFAULT_SETTINGS):
    """Shrink ``crop`` to the text it contains. The result never grows beyond ``crop``."""
    crop = crop or CropRect()
    width, height = image.size
    left, top, right, bottom = crop.pixels(image.size)
    region = image.crop((left, top, right, bottom)).convert("RGB")
    scale = min(1.0, settings.work_width / region.width)
    if scale < 1:
        region = region.resize(
            (max(1, round(region.width * scale)), max(1, round(region.height * scale))),
            Image.Resampling.BILINEAR,
        )
    else:
        scale = 1.0
    box, kind, lines = detect_region(np.asarray(region, dtype=np.int16), settings)
    if box is None:
        return MagicCropResult(crop, "unchanged", 0)
    detected = (
        (left + box[0] / scale) / width,
        (top + box[1] / scale) / height,
        (left + box[2] / scale) / width,
        (top + box[3] / scale) / height,
    )
    pad_x = (detected[2] - detected[0]) * settings.padding
    pad_y = (detected[3] - detected[1]) * settings.padding
    result = _clamped(
        detected[0] - pad_x, detected[1] - pad_y, detected[2] + pad_x, detected[3] + pad_y, crop
    )
    if settings.aspect:
        result = expand_to_aspect(result, settings.aspect, crop, image.size)
    if result == crop:
        return MagicCropResult(crop, "unchanged", 0)
    return MagicCropResult(result, kind, lines)
