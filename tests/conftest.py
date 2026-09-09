from fractions import Fraction

import av
import numpy as np
import pytest


def write_fixture_video(path, width=160, height=96):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for index in range(30):
            pixels = np.full((height, width, 3), 30 if index < 15 else 220, dtype=np.uint8)
            pixels[10:40, 20 + index : 50 + index] = (220, 20, 40)
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
def odd_video_path(tmp_path):
    # 150 px wide RGBX rows are 600 bytes, so swscale pads the line size and the
    # zero-copy analysis path must honour the stride.
    return write_fixture_video(tmp_path / "odd.mp4", width=150, height=94)
