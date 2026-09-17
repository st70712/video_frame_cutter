import numpy as np
import pytest
from conftest import draw_block, draw_text_lines, slide_pixels
from PIL import Image

from video_frame_cutter.magic_crop import (
    DEFAULT_SETTINGS,
    MagicCropSettings,
    detect_crop,
    expand_to_aspect,
)
from video_frame_cutter.models import CropRect

EXACT = MagicCropSettings(padding=0.0)


def assert_box(result, image, expected, slack=1):
    """Compare a detected crop with a pixel box, allowing for the ink-mass trim."""
    actual = result.crop.pixels(image.size)
    assert all(abs(a - b) <= slack for a, b in zip(actual, expected)), f"{actual} != {expected}"


def test_text_block_box_is_tight(slide_image):
    image, box = slide_image

    result = detect_crop(image, None, EXACT)

    assert result.kind == "text"
    assert result.lines == 3
    assert_box(result, image, box)


def test_solid_photo_block_is_excluded():
    pixels = slide_pixels()
    draw_block(pixels, (40, 20, 600, 24), colour=0)
    box = draw_text_lines(pixels, 120, 110)
    draw_block(pixels, (420, 80, 620, 280))
    image = Image.fromarray(pixels)

    result = detect_crop(image, None, EXACT)

    assert result.kind == "text"
    assert_box(result, image, box)
    assert result.crop.right * image.width < 420


def test_blank_canvas_falls_back():
    image = Image.new("RGB", (320, 180), "red")

    result = detect_crop(image, None, EXACT)

    assert result.kind == "unchanged"
    assert result.lines == 0
    assert result.crop == CropRect()


def test_letterbox_bars_are_trimmed():
    pixels = slide_pixels(background=0)
    pixels[60:300] = 255
    box = draw_text_lines(pixels, 120, 110)
    image = Image.fromarray(pixels)

    result = detect_crop(image, None, EXACT)

    assert result.kind == "text"
    assert_box(result, image, box)


def test_letterbox_bars_without_text_fall_back_to_the_content_box():
    pixels = slide_pixels(background=0)
    pixels[60:300] = 255
    image = Image.fromarray(pixels)

    result = detect_crop(image, None, EXACT)

    assert result.kind == "content"
    assert_box(result, image, (0, 60, 640, 300))


@pytest.mark.parametrize(
    "background, ink",
    [(255, 0), (0, 255), ((20, 40, 90), (240, 240, 240))],
)
def test_detection_ignores_polarity_and_background(background, ink):
    pixels = slide_pixels(background=background)
    draw_block(pixels, (40, 20, 600, 24), colour=ink)
    box = draw_text_lines(pixels, 120, 110, colour=ink)
    image = Image.fromarray(pixels)

    result = detect_crop(image, None, EXACT)

    assert result.kind == "text"
    assert_box(result, image, box)


def test_detection_never_expands_when_reapplied(slide_image):
    image, _ = slide_image

    first = detect_crop(image, None, EXACT)
    second = detect_crop(image, first.crop, EXACT)

    assert detect_crop(image, None, EXACT).crop == first.crop
    assert second.crop.left >= first.crop.left
    assert second.crop.top >= first.crop.top
    assert second.crop.right <= first.crop.right
    assert second.crop.bottom <= first.crop.bottom


def test_detection_stays_inside_the_input_crop():
    pixels = slide_pixels()
    draw_block(pixels, (180, 60, 460, 64), colour=0)
    inside = draw_text_lines(pixels, 200, 130, glyphs=8)
    draw_text_lines(pixels, 20, 330, lines=1, glyphs=5)
    image = Image.fromarray(pixels)
    crop = CropRect(0.25, 0.1, 0.75, 0.9)

    result = detect_crop(image, crop, EXACT)

    assert result.crop.left >= crop.left
    assert result.crop.top >= crop.top
    assert result.crop.right <= crop.right
    assert result.crop.bottom <= crop.bottom
    assert_box(result, image, inside)


def test_degenerate_input_never_raises():
    assert detect_crop(Image.new("RGB", (3, 3), "white"), None, EXACT).crop == CropRect()

    pixels = slide_pixels()
    draw_block(pixels, (300, 180, 304, 184), colour=0)
    lone = detect_crop(Image.fromarray(pixels), None, EXACT)

    assert lone.crop.right > lone.crop.left and lone.crop.bottom > lone.crop.top


def test_working_width_is_resolution_independent(slide_image):
    image, _ = slide_image
    scaled = image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST)

    small = detect_crop(image, None, EXACT).crop
    large = detect_crop(scaled, None, EXACT).crop

    for value, other in zip(
        (small.left, small.top, small.right, small.bottom),
        (large.left, large.top, large.right, large.bottom),
    ):
        assert value == pytest.approx(other, abs=0.005)


def test_settings_are_honoured(slide_image):
    image, _ = slide_image

    result = detect_crop(image, None, MagicCropSettings(padding=0.0, max_width=0.01))

    assert result.kind == "content"
    assert result.lines == 0


def test_padding_grows_the_box_without_leaving_the_input_crop(slide_image):
    image, box = slide_image

    tight = detect_crop(image, None, EXACT)
    padded = detect_crop(image, None, MagicCropSettings(padding=0.05))

    assert padded.crop.left < tight.crop.left
    assert padded.crop.right > tight.crop.right
    width = (box[2] - box[0]) / image.width
    assert padded.crop.left == pytest.approx(tight.crop.left - width * 0.05, abs=0.002)


def test_expand_to_aspect_reaches_the_ratio_when_there_is_room():
    size = (1920, 1080)
    rect = CropRect(0.4, 0.45, 0.6, 0.55)

    result = expand_to_aspect(rect, (1920, 1080), CropRect(), size)

    ratio = (result.right - result.left) * size[0] / ((result.bottom - result.top) * size[1])
    assert ratio == pytest.approx(16 / 9, rel=1e-6)
    assert (result.left + result.right) / 2 == pytest.approx(0.5)


def test_expand_to_aspect_is_capped_by_its_bounds():
    # The box needs to grow vertically to reach 16:9, but the bounds are too short, so it
    # fills that axis and stays wider than the target instead of spilling out.
    size = (1920, 1080)
    bounds = CropRect(0.2, 0.4, 0.8, 0.6)
    rect = CropRect(0.25, 0.45, 0.75, 0.55)

    result = expand_to_aspect(rect, (1920, 1080), bounds, size)

    assert result.left >= bounds.left and result.right <= bounds.right
    assert (result.top, result.bottom) == (bounds.top, bounds.bottom)
    ratio = (result.right - result.left) * size[0] / ((result.bottom - result.top) * size[1])
    assert ratio > 16 / 9


def test_aspect_expansion_applies_to_a_detected_crop(slide_image):
    image, _ = slide_image

    result = detect_crop(image, None, MagicCropSettings(padding=0.0, aspect=(4, 3)))

    ratio = (
        (result.crop.right - result.crop.left)
        * image.width
        / ((result.crop.bottom - result.crop.top) * image.height)
    )
    assert ratio == pytest.approx(4 / 3, rel=1e-6)


def test_noise_photo_still_returns_a_valid_crop_inside_the_input():
    # Dense noise fragments into many small components that each pass the glyph filters, so
    # the box is expected to stay loose. Only the contract is pinned, not the tightness.
    rng = np.random.default_rng(20260917)
    pixels = slide_pixels()
    pixels[80:280, 200:520] = rng.integers(0, 256, (200, 320, 3), dtype=np.uint8)
    crop = CropRect(0.1, 0.1, 0.9, 0.9)

    result = detect_crop(Image.fromarray(pixels), crop, DEFAULT_SETTINGS)

    assert result.crop.left >= crop.left and result.crop.right <= crop.right
    assert result.crop.top >= crop.top and result.crop.bottom <= crop.bottom
