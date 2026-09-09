from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from threading import Event
from threading import enumerate as active_threads

import av
import numpy as np
import pytest
from PIL import Image

from video_frame_cutter.analysis import (
    StableDetector,
    analysis_pixels,
    analyze,
    iter_analysis_frames,
)
from video_frame_cutter.models import AnalysisSettings
from video_frame_cutter.video import Cancelled, display_image, index_video


@pytest.mark.parametrize("width", [64, 160, 480])
@pytest.mark.parametrize("aspect", [None, Fraction(1), Fraction(4, 3)])
def test_analysis_pixels_equal_original(video_path, width, aspect):
    with av.open(str(video_path)) as container:
        for frame in container.decode(video=0):
            image = display_image(frame, aspect)
            actual_width = min(width, image.width)
            height = max(7, round(image.height * actual_width / image.width))
            expected = np.asarray(image.resize((actual_width, height), Image.Resampling.BILINEAR))
            actual = analysis_pixels(frame, aspect, width)
            assert actual.dtype == np.uint8
            assert np.array_equal(actual, expected)


@pytest.mark.parametrize("width", [64, 150, 480])
def test_odd_width_analysis_pixels_equal_original(odd_video_path, width):
    with av.open(str(odd_video_path)) as container:
        for frame in container.decode(video=0):
            assert frame.width == 150
            plane = frame.reformat(format="rgb0").planes[0]
            assert plane.line_size > 150 * 4
            image = display_image(frame)
            actual_width = min(width, image.width)
            height = max(7, round(image.height * actual_width / image.width))
            expected = np.asarray(image.resize((actual_width, height), Image.Resampling.BILINEAR))
            actual = analysis_pixels(frame, None, width)
            assert actual.dtype == np.uint8 and actual.flags.c_contiguous
            assert np.array_equal(actual, expected)


def test_rotated_analysis_preserves_display_path():
    class RotatedFrame:
        rotation = 90

        def to_image(self):
            pixels = np.arange(60 * 90 * 3, dtype=np.uint8).reshape(60, 90, 3)
            return Image.fromarray(pixels)

    frame = RotatedFrame()
    expected = display_image(frame).resize((60, 90), Image.Resampling.BILINEAR)
    assert np.array_equal(analysis_pixels(frame, Fraction(1), 160), np.asarray(expected))


def marker_values(result):
    return [
        (marker.frame, marker.change_seconds, marker.score, marker.review, marker.included)
        for marker in result.markers
    ]


@pytest.mark.parametrize("workers", [1, 2, 4])
@pytest.mark.parametrize("options", [{}, {"area": 0, "threshold": 0.001, "stable_seconds": 5}])
def test_pipeline_matches_original(video_path, workers, options):
    info = index_video(video_path)
    settings = AnalysisSettings(width=160, **options)
    original = StableDetector(settings)
    expected_pixels = []
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for reference, frame in zip(info.frames, container.decode(stream), strict=True):
            image = display_image(frame, info.aspect)
            width = min(settings.width, image.width)
            height = max(7, round(image.height * width / image.width))
            pixels = np.asarray(image.resize((width, height), Image.Resampling.BILINEAR))
            expected_pixels.append(pixels)
            original.feed(reference, pixels)
    actual_frames = list(iter_analysis_frames(info, settings, workers=workers))
    assert [reference for reference, pixels in actual_frames] == info.frames
    for (_, pixels), expected in zip(actual_frames, expected_pixels, strict=True):
        assert np.array_equal(pixels, expected)
    progress = []
    actual = analyze(info, settings, workers=workers, progress=progress.append)
    expected = original.finish()
    assert actual.curve == expected.curve
    assert marker_values(actual) == marker_values(expected)
    assert progress == sorted(set(progress))
    assert progress[-1] == 100
    assert not any(thread.name.startswith("vfc-analysis") for thread in active_threads())


def test_pipeline_cancel_and_close_join_workers(video_path):
    info = index_video(video_path)
    cancel = Event()
    with closing(iter_analysis_frames(info, AnalysisSettings(), cancel)) as frames:
        next(frames)
        cancel.set()
        with pytest.raises(Cancelled):
            next(frames)
    assert not any(thread.name.startswith("vfc-analysis") for thread in active_threads())
    with closing(iter_analysis_frames(info, AnalysisSettings())) as frames:
        next(frames)
    assert not any(thread.name.startswith("vfc-analysis") for thread in active_threads())


@pytest.mark.parametrize("damage", ["pts", "short", "long"])
def test_pipeline_rejects_changed_video(video_path, damage):
    info = index_video(video_path)
    if damage == "pts":
        info.frames[3] = replace(info.frames[3], pts=-1)
    elif damage == "short":
        info.frames.pop()
    else:
        info.frames.append(info.frames[-1])
    progress = []
    with pytest.raises(ValueError, match="Video"):
        analyze(info, AnalysisSettings(), progress=progress.append)
    assert 100 not in progress
    assert not any(thread.name.startswith("vfc-analysis") for thread in active_threads())


def test_pipeline_worker_error_is_not_partial_success(video_path, monkeypatch):
    info = index_video(video_path)

    def broken_pixels(*args):
        raise RuntimeError("conversion failed")

    monkeypatch.setattr("video_frame_cutter.analysis.analysis_pixels", broken_pixels)
    progress = []
    with pytest.raises(RuntimeError, match="conversion failed"):
        analyze(info, AnalysisSettings(), progress=progress.append)
    assert 100 not in progress
    assert not any(thread.name.startswith("vfc-analysis") for thread in active_threads())


def test_out_of_order_workers_preserve_order_and_bound_prefetch(video_path, monkeypatch):
    info = index_video(video_path)
    second_finished = Event()
    completed = []
    original = analysis_pixels

    def out_of_order(frame, aspect, width):
        if frame.pts == info.frames[0].pts:
            assert second_finished.wait(timeout=5)
        pixels = original(frame, aspect, width)
        completed.append(frame.pts)
        if frame.pts == info.frames[1].pts:
            second_finished.set()
        return pixels

    monkeypatch.setattr("video_frame_cutter.analysis.analysis_pixels", out_of_order)
    with closing(iter_analysis_frames(
        info, AnalysisSettings(), workers=2, buffer_size=4,
    )) as frames:
        reference, _pixels = next(frames)
        assert reference == info.frames[0]
        assert completed.index(info.frames[1].pts) < completed.index(info.frames[0].pts)
        assert len(completed) <= 5
        assert [reference, *(reference for reference, pixels in frames)] == info.frames
    assert not any(thread.name.startswith("vfc-analysis") for thread in active_threads())


def test_detector_failure_joins_workers_and_no_false_completion(video_path, monkeypatch):
    info = index_video(video_path)

    def broken_feed(*args):
        raise RuntimeError("detector failed")

    monkeypatch.setattr(StableDetector, "feed", broken_feed)
    progress = []
    with pytest.raises(RuntimeError, match="detector failed"):
        analyze(info, AnalysisSettings(), progress=progress.append)
    assert 100 not in progress
    assert not any(thread.name.startswith("vfc-analysis") for thread in active_threads())