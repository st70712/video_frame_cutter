from copy import deepcopy

import numpy as np
import pytest

from video_frame_cutter.analysis import StableDetector, difference, merge_markers
from video_frame_cutter.models import AnalysisSettings, FrameRef, Marker


def run_frames(pages, **kwargs):
    detector = StableDetector(AnalysisSettings(**kwargs))
    for index, pixels in enumerate(pages):
        detector.feed(FrameRef(index, 1, 10, index, index / 10), pixels)
    return detector.finish()


def test_static_and_hard_cut():
    dark = np.full((48, 80, 3), 30, dtype=np.uint8)
    light = np.full_like(dark, 220)
    result = run_frames([dark] * 10 + [light] * 10)
    assert [marker.frame.index for marker in result.markers] == [0, 10]
    assert len(result.curve) == 20
    assert all(not marker.review for marker in result.markers)


def test_recent_duplicate_window_suppresses_returned_page():
    page_a = np.full((48, 80, 3), 30, dtype=np.uint8)
    page_b = np.full_like(page_a, 220)

    result = run_frames(
        [page_a] * 10 + [page_b] * 10 + [page_a] * 10,
        duplicate_window_seconds=3.0,
    )

    assert [marker.frame.index for marker in result.markers] == [0, 10]


def test_recent_duplicate_window_can_be_disabled_or_expire():
    page_a = np.full((48, 80, 3), 30, dtype=np.uint8)
    page_b = np.full_like(page_a, 220)

    disabled = run_frames(
        [page_a] * 10 + [page_b] * 10 + [page_a] * 10,
        duplicate_window_seconds=0.0,
    )
    boundary = run_frames(
        [page_a] * 10 + [page_b] * 20 + [page_a] * 10,
        duplicate_window_seconds=3.0,
    )
    expired = run_frames(
        [page_a] * 10 + [page_b] * 40 + [page_a] * 10,
        duplicate_window_seconds=3.0,
    )

    assert [marker.frame.index for marker in disabled.markers] == [0, 10, 20]
    assert [marker.frame.index for marker in boundary.markers] == [0, 10]
    assert [marker.frame.index for marker in expired.markers] == [0, 10, 50]


def test_suppressed_duplicates_refresh_their_recent_timestamp():
    page_a = np.full((48, 80, 3), 30, dtype=np.uint8)
    page_b = np.full_like(page_a, 220)
    pages = [page_a] * 10 + [page_b] * 10 + [page_a] * 10 + [page_b] * 10 + [page_a] * 10

    result = run_frames(pages, duplicate_window_seconds=3.0)

    assert [marker.frame.index for marker in result.markers] == [0, 10]


def test_minimum_interval_does_not_remember_unmarked_page():
    page_a = np.full((48, 80, 3), 30, dtype=np.uint8)
    page_b = np.full_like(page_a, 220)
    pages = [page_a] * 4 + [page_b] * 4 + [page_a] * 4 + [page_b] * 4

    result = run_frames(
        pages,
        stable_seconds=0.1,
        min_interval=1.0,
        duplicate_window_seconds=3.0,
    )

    assert [marker.frame.index for marker in result.markers] == [0, 12]


@pytest.mark.parametrize("window", [-0.1, 60.1])
def test_recent_duplicate_window_must_be_within_range(window):
    with pytest.raises(ValueError, match="Invalid detection timing"):
        StableDetector(AnalysisSettings(duplicate_window_seconds=window))


def test_cursor_and_slow_transition():
    dark = np.full((48, 80, 3), 30, dtype=np.uint8)
    pages = []
    for index in range(20):
        page = dark.copy()
        page[5:7, index : index + 2] = 220
        pages.append(page)
    assert len(run_frames(pages).markers) == 1
    fade = [np.full_like(dark, value) for value in range(40, 221, 10)]
    result = run_frames([dark] * 10 + fade + [np.full_like(dark, 220)] * 10)
    assert len(result.markers) == 2
    assert result.markers[1].frame.index >= 28


def test_unstable_eof_and_merge():
    pages = [np.full((48, 80, 3), index * 20, dtype=np.uint8) for index in range(10)]
    result = run_frames(pages)
    assert result.markers[0].review and not result.markers[0].included
    manual = Marker(FrameRef(0, 1, 10, 0, 0))
    edited = deepcopy(result.markers[0])
    edited.modified = True
    assert merge_markers([manual, edited], result.markers) == [manual, edited]


def test_dynamic_timeout_before_eof_and_stable_recovery():
    detector = StableDetector(AnalysisSettings())
    dark = np.full((48, 80, 3), 30, dtype=np.uint8)
    light = np.full_like(dark, 220)
    for index in range(80):
        detector.feed(FrameRef(index, 1, 10, index, index / 10), dark if index % 2 else light)
        if index == 30:
            assert len(detector.markers) == 1
            assert detector.markers[0].review and not detector.markers[0].included
    assert len(detector.finish().markers) == 1
    for index in range(80, 90):
        detector.feed(FrameRef(index, 1, 10, index, index / 10), light)
    result = detector.finish()
    assert len(result.markers) == 2
    assert result.markers[-1].included and not result.markers[-1].review
    assert result.markers[-1].frame.index == 80


def test_timeout_candidate_can_become_stable_without_duplicate():
    pages = [np.full((48, 80, 3), 30 if index % 2 else 220, dtype=np.uint8)
             for index in range(31)]
    result = run_frames(pages + [pages[-1]] * 10)
    assert len(result.markers) == 1
    assert result.markers[0].frame.index == 30
    assert result.markers[0].included and not result.markers[0].review


def test_small_area_shortcut_preserves_detection(monkeypatch):
    detector = StableDetector(AnalysisSettings())
    base = np.full((48, 80, 3), 30, dtype=np.uint8)
    changed = base.copy()
    changed[0, 0] = 220
    score, area = difference(base, changed)
    expected = score >= detector.settings.threshold and area >= detector.settings.area

    def unexpected_ssim(*args, **kwargs):
        raise AssertionError("Below-area baseline comparisons must not calculate SSIM")

    monkeypatch.setattr("video_frame_cutter.analysis.structural_similarity", unexpected_ssim)
    assert detector.changed(base, changed, detector.settings.threshold) == expected
