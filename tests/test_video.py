from threading import Event

import av
import numpy as np
import pytest

from video_frame_cutter.models import CropRect
from video_frame_cutter.video import Cancelled, FrameReader, index_video


def test_precise_seek_and_vfr(video_path):
    info = index_video(video_path)
    assert len(info.frames) == 30
    assert info.times[15] - info.times[14] > info.times[14] - info.times[13]
    with av.open(str(video_path)) as container:
        oracle = {
            frame.pts: frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)
        }
    with FrameReader(info) as reader:
        for index in [0, 29, 14, 15, 2, 28, 0]:
            reference = info.frames[index]
            assert np.array_equal(np.asarray(reader.get(reference)), oracle[reference.pts])
            assert info.at(reference.seconds).pts == reference.pts
        assert info.at(-1).index == 0
        assert info.at(999).index == 29
        assert info.at((info.times[14] + info.times[15]) / 2).index == 14


def test_cancel_index(video_path):
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        index_video(video_path, cancel)


def test_crop_boundaries():
    assert CropRect().pixels((100, 50)) == (0, 0, 100, 50)
    assert CropRect(0.999, 0.999, 1, 1).pixels((100, 50)) == (99, 49, 100, 50)
    with pytest.raises(ValueError):
        CropRect(-1, 0, 1, 1)
