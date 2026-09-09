from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass

import av
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

from .models import AnalysisSettings, Marker
from .video import check_cancel, display_image


def difference(first, second, minimum_area=0.0):
    absolute = np.max(np.abs(first.astype(np.int16) - second.astype(np.int16)), axis=2)
    area = float(np.mean(absolute > 20))
    if area < minimum_area or not np.any(absolute):
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

    def changed(self, first, second, threshold):
        score, area = difference(first, second, self.settings.area)
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

    def feed(self, reference, pixels):
        if self.previous is None:
            self.previous = self.baseline = self.anchor = pixels
            self.candidate = reference
            self.change = reference.seconds
            self.curve.append((reference.seconds, 0.0))
            return
        score, area = difference(self.previous, pixels)
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
            drift = np.max(np.abs(self.anchor.astype(np.int16) - pixels.astype(np.int16)), axis=2)
            if (
                (score >= stable_threshold and area >= self.settings.area)
                or self.changed(self.anchor, pixels, stable_threshold)
                or np.mean(drift > 4) >= max(self.settings.area, 0.001)
            ):
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


def analysis_pixels(frame, aspect, width):
    if not frame.rotation and (not aspect or aspect == 1):
        image = Image.fromarray(frame.to_ndarray(format="rgb24"))
    else:
        image = display_image(frame, aspect)
    width = min(width, image.width)
    height = max(7, round(image.height * width / image.width))
    return np.asarray(image.resize((width, height), Image.Resampling.BILINEAR))


def iter_analysis_frames(
    info, settings, cancel=None, *, workers=4, buffer_size=8, decoder_threads=2,
):
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

        def decoded():
            count = 0
            for index, frame in enumerate(container.decode(stream)):
                check_cancel(cancel)
                if index >= len(info.frames) or frame.pts != info.frames[index].pts:
                    raise ValueError("Video changed since indexing")
                count += 1
                yield info.frames[index], frame
            if count != len(info.frames):
                raise ValueError("Video ended before all indexed frames were decoded")

        def prepare(item):
            check_cancel(cancel)
            reference, frame = item
            pixels = analysis_pixels(frame, info.aspect, settings.width)
            check_cancel(cancel)
            return reference, pixels

        if workers == 1:
            for item in decoded():
                yield prepare(item)
        else:
            executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vfc-analysis")
            try:
                with closing(executor.map(prepare, decoded(), buffersize=buffer_size)) as results:
                    for result in results:
                        check_cancel(cancel)
                        yield result
            finally:
                executor.shutdown(wait=True, cancel_futures=True)


def analyze(
    info, settings, cancel=None, progress=lambda value: None, *,
    workers=4, buffer_size=8, decoder_threads=2,
):
    detector = StableDetector(settings)
    last_percent = -1
    with closing(iter_analysis_frames(
        info, settings, cancel, workers=workers, buffer_size=buffer_size,
        decoder_threads=decoder_threads,
    )) as frames:
        for count, (reference, pixels) in enumerate(frames, 1):
            check_cancel(cancel)
            detector.feed(reference, pixels)
            percent = min(99, int(count / len(info.frames) * 100))
            if percent != last_percent:
                progress(percent)
                last_percent = percent
    result = detector.finish()
    check_cancel(cancel)
    progress(100)
    return result


def merge_markers(existing, candidates):
    kept = [marker for marker in existing if marker.source == "manual" or marker.modified]
    occupied = {marker.frame.pts for marker in kept}
    return sorted(
        kept + [marker for marker in candidates if marker.frame.pts not in occupied],
        key=lambda marker: marker.frame.seconds,
    )
