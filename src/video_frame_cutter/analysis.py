from collections import deque
from concurrent.futures import Future, InvalidStateError, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from threading import local

import av
import numpy as np
from av.video.reformatter import VideoReformatter
from PIL import Image
from skimage.metrics import structural_similarity

from .models import AnalysisSettings, Marker
from .video import Cancelled, check_cancel, display_image

DEFAULT_WORKERS = 8
DEFAULT_BUFFER_SIZE = 12
DEFAULT_DECODER_THREADS = 0


def channel_difference(first, second):
    """Per-pixel maximum absolute channel difference as uint8.

    Equivalent to ``np.max(np.abs(first.astype(np.int16) - second.astype(np.int16)), axis=2)``
    for uint8 RGB input, without the int16 conversions or the slow reduction over the last axis.
    """
    absolute = np.maximum(first, second)
    np.subtract(absolute, np.minimum(first, second), out=absolute)
    combined = np.maximum(absolute[..., 0], absolute[..., 1])
    np.maximum(combined, absolute[..., 2], out=combined)
    return combined


def changed_fraction(absolute, minimum):
    """Fraction of pixels whose difference exceeds ``minimum``.

    Identical to ``float(np.mean(absolute > minimum))``: the count is an exact integer and the
    division of two exactly representable values rounds the same way.
    """
    return np.count_nonzero(absolute > minimum) / absolute.size


def difference(first, second, minimum_area=0.0, absolute=None):
    """Change score and changed area between two analysis images.

    ``absolute`` may pass in ``channel_difference(first, second)`` when the caller already has
    it; the result is the same either way.
    """
    if absolute is None:
        if np.array_equal(first, second):
            return 0.0, 0.0
        absolute = channel_difference(first, second)
    area = changed_fraction(absolute, 20)
    if area < minimum_area or not absolute.any():
        return 0.0, area
    score = 1.0 - float(structural_similarity(first, second, channel_axis=2, data_range=255))
    return max(0.0, score), area


@dataclass
class AnalysisResult:
    markers: list
    curve: list


class StableDetector:
    def __init__(self, settings: AnalysisSettings):
        settings.validate()
        self.settings = settings
        self.previous = None
        self.baseline = None
        self.anchor = None
        self.candidate = None
        self.change = 0.0
        self.peak = 0.0
        self.pending = True
        self.reviewed = False
        self.last_stable = None
        self.recent_stable = []
        self.markers = []
        self.curve = []

    def changed(self, first, second, threshold, absolute=None):
        score, area = difference(first, second, self.settings.area, absolute)
        return score >= threshold and area >= self.settings.area

    def duplicate_pixels(self, pixels):
        if pixels.shape[1] <= 160:
            return pixels.copy()
        height = max(1, round(pixels.shape[0] * 160 / pixels.shape[1]))
        return np.asarray(
            Image.fromarray(pixels).resize((160, height), Image.Resampling.BILINEAR)
        ).copy()

    def recently_seen(self, reference, pixels):
        window = self.settings.duplicate_window_seconds
        if window == 0:
            return False
        self.recent_stable = [
            entry for entry in self.recent_stable
            if reference.seconds - entry[1] <= window
        ]
        candidate_pixels = self.duplicate_pixels(pixels)
        for index, (stable_pixels, _) in enumerate(self.recent_stable):
            if not self.changed(stable_pixels, candidate_pixels, self.settings.threshold):
                self.recent_stable[index] = (stable_pixels, reference.seconds)
                return True
        return False

    def remember_stable(self, reference, pixels):
        if self.settings.duplicate_window_seconds > 0:
            self.recent_stable.append((self.duplicate_pixels(pixels), reference.seconds))

    def feed(self, reference, pixels, precomputed=None):
        """Feed the next frame; ``precomputed`` may carry ``difference(previous, pixels)``."""
        if self.previous is None:
            self.previous = self.baseline = self.anchor = pixels
            self.candidate = reference
            self.change = reference.seconds
            self.curve.append((reference.seconds, 0.0))
            return
        if precomputed is None:
            precomputed = difference(self.previous, pixels)
        score, area = precomputed
        self.curve.append((reference.seconds, score))
        if not self.pending and self.changed(self.baseline, pixels, self.settings.threshold):
            self.pending = True
            self.reviewed = False
            self.change = reference.seconds
            self.candidate = reference
            self.anchor = pixels
            self.peak = score
        if self.pending:
            self.peak = max(self.peak, score)
            stable_threshold = min(0.025, self.settings.threshold * 0.35)
            moved = score >= stable_threshold and area >= self.settings.area
            if not moved:
                drift = channel_difference(self.anchor, pixels)
                moved = (
                    self.changed(self.anchor, pixels, stable_threshold, drift)
                    or changed_fraction(drift, 4) >= max(self.settings.area, 0.001)
                )
            if moved:
                self.anchor = pixels
                self.candidate = reference
            elif reference.seconds - self.candidate.seconds >= self.settings.stable_seconds:
                duplicate = self.recently_seen(self.candidate, self.anchor)
                if not duplicate and (
                    self.last_stable is None
                    or self.candidate.seconds - self.last_stable.seconds
                    >= self.settings.min_interval
                ):
                    self.markers = [
                        marker for marker in self.markers
                        if marker.frame.pts != self.candidate.pts
                    ]
                    self.markers.append(
                        Marker(
                            self.candidate,
                            source="automatic",
                            score=self.peak,
                            change_seconds=self.change,
                        )
                    )
                    self.last_stable = self.candidate
                    self.remember_stable(self.candidate, self.anchor)
                self.baseline = pixels
                self.pending = False
                self.peak = 0.0
            if (
                self.pending and not self.reviewed
                and reference.seconds - self.change >= max(3.0, self.settings.stable_seconds)
            ):
                self.markers.append(
                    Marker(
                        self.candidate, source="automatic", review=True, included=False,
                        score=self.peak, change_seconds=self.change,
                    )
                )
                self.reviewed = True
        self.previous = pixels

    def finish(self):
        if (
            self.pending
            and not self.reviewed
            and self.candidate is not None
            and (not self.markers or self.candidate.pts != self.markers[-1].frame.pts)
        ):
            self.markers.append(
                Marker(
                    self.candidate,
                    source="automatic",
                    review=True,
                    included=False,
                    score=self.peak,
                    change_seconds=self.change,
                )
            )
        return AnalysisResult(self.markers, self.curve)


_thread_state = local()


def thread_reformatter():
    reformatter = getattr(_thread_state, "reformatter", None)
    if reformatter is None:
        reformatter = _thread_state.reformatter = VideoReformatter()
    return reformatter


def analysis_pixels(frame, aspect, width):
    if not frame.rotation and (not aspect or aspect == 1):
        # The same swscale conversion as ``frame.to_ndarray(format="rgb24")``; the cached
        # reformatter only saves the per-frame context setup, and Pillow reads the plane with
        # its stride so the only copy is the one Pillow makes. (``rgb0`` output would avoid that
        # copy but is not bit-identical for widths that are not multiples of 16.)
        converted = thread_reformatter().reformat(frame, format="rgb24")
        plane = converted.planes[0]
        image = Image.frombuffer(
            "RGB", (converted.width, converted.height), plane, "raw", "RGB", plane.line_size, 1
        )
    else:
        image = display_image(frame, aspect)
    width = min(width, image.width)
    height = max(7, round(image.height * width / image.width))
    return np.asarray(image.resize((width, height), Image.Resampling.BILINEAR))


def frames_identical(first, second):
    """True when two decoded frames carry byte-identical picture buffers.

    Identical input produces identical analysis pixels, so the expensive conversion and
    resize can be skipped. Padding bytes and the colour metadata swscale may consult are
    compared as well; a difference there only costs a redundant conversion, never a wrong
    result.
    """
    if (
        first.format.name != second.format.name
        or first.width != second.width
        or first.height != second.height
        or first.rotation != second.rotation
        or first.color_range != second.color_range
        or first.colorspace != second.colorspace
        or len(first.planes) != len(second.planes)
    ):
        return False
    for left, right in zip(first.planes, second.planes):
        if left.buffer_size != right.buffer_size or left.line_size != right.line_size:
            return False
        left_bytes = np.frombuffer(left, np.uint8)
        right_bytes = np.frombuffer(right, np.uint8)
        aligned = left_bytes.size - left_bytes.size % 8
        if not np.array_equal(
            left_bytes[:aligned].view(np.uint64), right_bytes[:aligned].view(np.uint64)
        ) or not np.array_equal(left_bytes[aligned:], right_bytes[aligned:]):
            return False
    return True


@dataclass
class PreparedFrame:
    reference: object
    pixels: np.ndarray
    difference: tuple


def prepare_frame(reference, frame, previous_frame, previous_pixels, pixels_ready, aspect, width,
                  cancel=None):
    """Worker task: analysis pixels of ``frame`` plus its difference against the previous frame.

    ``pixels_ready`` is resolved as soon as the pixels exist, so the next frame's task only
    waits for this conversion and never for this frame's SSIM. ``previous_pixels`` is the
    previous task's ``pixels_ready`` (``None`` for the first frame).
    """
    check_cancel(cancel)
    if previous_pixels is not None and frames_identical(frame, previous_frame):
        pixels = previous_pixels.result()
        pixels_ready.set_result(pixels)
        return PreparedFrame(reference, pixels, (0.0, 0.0))
    pixels = analysis_pixels(frame, aspect, width)
    pixels_ready.set_result(pixels)
    if previous_pixels is None:
        return PreparedFrame(reference, pixels, (0.0, 0.0))
    check_cancel(cancel)
    return PreparedFrame(reference, pixels, difference(previous_pixels.result(), pixels))


def settle_pixels(pixels_ready, future):
    """Resolve ``pixels_ready`` when its task ends without publishing pixels.

    Cancelled or failed tasks would otherwise leave the next task waiting forever.
    """
    if pixels_ready.done():
        return
    try:
        if future.cancelled():
            pixels_ready.set_exception(Cancelled())
        else:
            error = future.exception()
            pixels_ready.set_exception(
                error if error is not None else RuntimeError("Frame pixels were not published")
            )
    except InvalidStateError:
        pass


def iter_prepared_frames(
    info, settings, cancel=None, *, workers=DEFAULT_WORKERS, buffer_size=DEFAULT_BUFFER_SIZE,
    decoder_threads=DEFAULT_DECODER_THREADS,
):
    """Decode in order and prepare analysis input on a bounded worker pool.

    Every yielded item carries the analysis pixels of one indexed frame plus the
    ``difference`` against the previous frame, computed with the very same function the
    detector would otherwise call. Frames whose decoded buffers equal the previous frame
    reuse its pixels. Items are yielded strictly in presentation order; at most
    ``buffer_size`` frames are in flight, and each un-started task also keeps the previous
    decoded frame alive.
    """
    settings.validate()
    if workers < 1 or buffer_size < workers or decoder_threads < 0:
        raise ValueError("Invalid analysis pipeline configuration")
    if not info.frames:
        raise ValueError("Video contains no indexed frames")
    check_cancel(cancel)
    with av.open(str(info.path)) as container:
        stream = container.streams[info.stream_index]
        stream.thread_type = "AUTO"
        stream.thread_count = decoder_threads
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vfc-analysis")
        pending = deque()
        try:
            previous_frame = previous_pixels = None
            count = 0
            for index, frame in enumerate(container.decode(stream)):
                check_cancel(cancel)
                if index >= len(info.frames) or frame.pts != info.frames[index].pts:
                    raise ValueError("Video changed since indexing")
                count += 1
                pixels_ready = Future()
                future = executor.submit(
                    prepare_frame, info.frames[index], frame, previous_frame, previous_pixels,
                    pixels_ready, info.aspect, settings.width, cancel,
                )
                future.add_done_callback(partial(settle_pixels, pixels_ready))
                pending.append(future)
                previous_frame, previous_pixels = frame, pixels_ready
                if len(pending) >= buffer_size:
                    yield pending.popleft().result()
                    check_cancel(cancel)
            if count != len(info.frames):
                raise ValueError("Video ended before all indexed frames were decoded")
            previous_frame = previous_pixels = None
            while pending:
                yield pending.popleft().result()
                check_cancel(cancel)
        finally:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)


def iter_analysis_frames(info, settings, cancel=None, **configuration):
    for item in iter_prepared_frames(info, settings, cancel, **configuration):
        yield item.reference, item.pixels


def analyze(
    info, settings, cancel=None, progress=lambda value: None, *, workers=DEFAULT_WORKERS,
    buffer_size=DEFAULT_BUFFER_SIZE, decoder_threads=DEFAULT_DECODER_THREADS,
):
    detector = StableDetector(settings)
    last_percent = -1
    frames = iter_prepared_frames(
        info, settings, cancel, workers=workers, buffer_size=buffer_size,
        decoder_threads=decoder_threads,
    )
    try:
        for count, item in enumerate(frames, 1):
            check_cancel(cancel)
            detector.feed(item.reference, item.pixels, item.difference)
            percent = min(99, int(count / len(info.frames) * 100))
            if percent != last_percent:
                progress(percent)
                last_percent = percent
    finally:
        frames.close()
    result = detector.finish()
    check_cancel(cancel)
    progress(100)
    return result


def analysis_defaults():
    """Default pipeline configuration of :func:`analyze`, for reports, scripts and tests."""
    return {
        "workers": DEFAULT_WORKERS,
        "buffer_size": DEFAULT_BUFFER_SIZE,
        "decoder_threads": DEFAULT_DECODER_THREADS,
    }


def merge_markers(existing, candidates):
    kept = [marker for marker in existing if marker.source == "manual" or marker.modified]
    occupied = {marker.frame.pts for marker in kept}
    return sorted(
        kept + [marker for marker in candidates if marker.frame.pts not in occupied],
        key=lambda marker: marker.frame.seconds,
    )
