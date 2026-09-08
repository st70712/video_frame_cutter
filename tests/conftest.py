from fractions import Fraction

import av
import numpy as np
import pytest


@pytest.fixture
def video_path(tmp_path):
    path = tmp_path / "fixture.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width = 160
        stream.height = 96
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for index in range(30):
            pixels = np.full((96, 160, 3), 30 if index < 15 else 220, dtype=np.uint8)
            pixels[10:40, 20 + index : 50 + index] = (220, 20, 40)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index * 100 + (50 if index >= 15 else 0)
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path
