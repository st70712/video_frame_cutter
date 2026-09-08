from pathlib import Path

from scripts.benchmark_analysis import load_baseline, run_pass
from video_frame_cutter import analysis
from video_frame_cutter.models import AnalysisSettings
from video_frame_cutter.video import index_video


def test_benchmark_compares_independent_baseline(video_path):
    baseline, digest = load_baseline(Path(__file__).resolve().parents[1])
    assert baseline.StableDetector is not analysis.StableDetector
    assert len(digest) == 64
    info = index_video(video_path)
    settings = AnalysisSettings(width=160)
    for sample_frames in (8, 0):
        reference = run_pass(baseline, info, settings, sample_frames)
        actual = run_pass(analysis, info, settings, sample_frames)
        assert actual["signature"] == reference["signature"]
        assert actual["complete_video"] == (sample_frames == 0)
        assert actual["signature"]["frames"] == (sample_frames or len(info.frames))
        assert actual["analysis_elapsed_seconds"] > 0