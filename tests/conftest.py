from fractions import Fraction

import av
import numpy as np
import pytest
from PIL import Image


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


def slide_pixels(width=640, height=360, background=255):
    """Blank slide canvas as an ``(height, width, 3)`` uint8 array."""
    return np.full((height, width, 3), background, dtype=np.uint8)


def draw_text_lines(pixels, left, top, lines=3, glyphs=15, size=12, gap=8, colour=0):
    """Draw rows of small squares standing in for glyphs and return their bounding box.

    Squares rather than bars keep every magic-crop threshold clear of its limit: a glyph is
    far below the solid-block area, well above the minimum glyph height, and ``gap`` is
    narrower than the line-merging dilation so one row collapses into one line group.
    """
    for line in range(lines):
        row = top + line * (size + gap)
        for glyph in range(glyphs):
            column = left + glyph * (size + gap)
            pixels[row : row + size, column : column + size] = colour
    pitch = size + gap
    return (left, top, left + (glyphs - 1) * pitch + size, top + (lines - 1) * pitch + size)


def draw_block(pixels, box, colour=90):
    """Fill a solid rectangle standing in for a photo or a colour block."""
    left, top, right, bottom = box
    pixels[top:bottom, left:right] = colour


@pytest.fixture
def slide_image():
    """White slide with a full-width rule and three lines of glyphs, plus the glyph box.

    The rule is wider than the text filter allows, so it shapes the content box without
    shaping the text box; without it the content box would collapse onto the glyphs and the
    text path would never run.
    """
    pixels = slide_pixels()
    draw_block(pixels, (40, 20, 600, 24), colour=0)
    box = draw_text_lines(pixels, 120, 110)
    return Image.fromarray(pixels), box
