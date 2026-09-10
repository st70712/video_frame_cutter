from fractions import Fraction

import av
import numpy as np
import pytest


def write_fixture_video(path, width=160, height=96, content="blocks"):
    """Write a 30-frame libx264 clip with a PTS jump in the middle.

    ``content`` selects the pictures: ``blocks`` moves a red block one pixel per frame over a
    hard cut, ``repeat`` holds every picture for three frames so consecutive decoded frames
    are byte-identical, and ``noise`` fills every frame with random colour so edge and chroma
    handling of the converters is exercised.
    """
    rng = np.random.default_rng(width * 1000 + height)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for index in range(30):
            if content == "noise":
                pixels = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
            else:
                step = index // 3 if content == "repeat" else index
                pixels = np.full((height, width, 3), 30 if index < 15 else 220, dtype=np.uint8)
                pixels[10:40, 20 + step : 50 + step] = (220, 20, 40)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index * 100 + (50 if index >= 15 else 0)
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.fixture
def video_path(tmp_path):
    return write_fixture_video(tmp_path / "fixture.mp4")


@pytest.fixture
def noise_video_path(tmp_path):
    # 162 is not a multiple of 16, so swscale takes its edge paths and pads the RGB line
    # size; random content makes any conversion difference visible.
    return write_fixture_video(tmp_path / "noise.mp4", width=162, height=94, content="noise")


@pytest.fixture
def repeated_video_path(tmp_path):
    # Every picture is encoded three times in a row, so the pipeline's identical-frame reuse
    # path runs in the regular suite.
    return write_fixture_video(tmp_path / "repeated.mp4", content="repeat")
