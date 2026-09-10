import os
import subprocess
import sys

from video_frame_cutter.__main__ import parse_arguments


def test_parse_arguments_accepts_video_and_smoke_mode():
    arguments = parse_arguments(["--smoke-test", "sample.mp4"])

    assert arguments.smoke_test
    assert arguments.video == "sample.mp4"


def test_smoke_mode_starts_and_stops_qt_application():
    environment = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "video_frame_cutter", "--smoke-test"],
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr