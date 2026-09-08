from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from threading import Event

import av
from PIL import Image

from .models import FrameRef


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def display_image(frame, aspect=Fraction(1)):
    image = frame.to_image().convert("RGB")
    if aspect and aspect != 1:
        image = image.resize(
            (max(1, round(image.width * aspect)), image.height), Image.Resampling.LANCZOS
        )
    rotation = frame.rotation
    if rotation:
        image = image.rotate(rotation, expand=True)
    return image


@dataclass
class VideoInfo:
    path: Path
    stream_index: int
    duration: float
    width: int
    height: int
    audio: bool
    origin: Fraction
    aspect: Fraction
    frames: list[FrameRef] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    thumbnails: list = field(default_factory=list)

    def at(self, seconds):
        return self.frames[max(0, min(len(self.frames) - 1, bisect_right(self.times, seconds) - 1))]


def index_video(path, cancel=None, progress=lambda value: None):
    path = Path(path).resolve()
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("No video stream")
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        duration = float(container.duration / av.time_base) if container.duration else 0
        origin = Fraction(container.start_time or 0, av.time_base)
        aspect = stream.sample_aspect_ratio or Fraction(1)
        info = VideoInfo(
            path,
            stream.index,
            duration,
            stream.width,
            stream.height,
            bool(container.streams.audio),
            origin,
            aspect,
        )
        last_percent = -1
        next_thumb = 0.0
        thumb_interval = max(1.0, duration / 160)
        for index, frame in enumerate(container.decode(stream)):
            check_cancel(cancel)
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Video contains frames without presentation timestamps")
            seconds = float(frame.pts * frame.time_base - origin)
            if info.frames and frame.pts <= info.frames[-1].pts:
                raise ValueError("Non-increasing presentation timestamps are not supported")
            reference = FrameRef(
                frame.pts, frame.time_base.numerator, frame.time_base.denominator, index, seconds
            )
            info.frames.append(reference)
            info.times.append(seconds)
            if seconds >= next_thumb or index == 0:
                image = display_image(frame, aspect)
                info.width, info.height = image.size
                image.thumbnail((144, 82))
                info.thumbnails.append((seconds, image))
                next_thumb = seconds + thumb_interval
            percent = min(99, int(seconds / max(duration, 1) * 100))
            if percent != last_percent:
                progress(percent)
                last_percent = percent
        if not info.frames:
            raise ValueError("Video contains no decodable frames")
        last = info.frames[-1]
        interval = (last.seconds - info.frames[-2].seconds) if len(info.frames) > 1 else 0.04
        info.duration = max(info.duration, last.seconds + interval)
        progress(100)
        return info


class FrameReader:
    def __init__(self, info, cache_size=8):
        self.info = info
        self.container = av.open(str(info.path))
        self.stream = self.container.streams[info.stream_index]
        self.stream.thread_type = "AUTO"
        self.cache = OrderedDict()
        self.cache_size = cache_size

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.container.close()

    def get(self, reference, cancel: Event | None = None):
        check_cancel(cancel)
        if reference.pts in self.cache:
            self.cache.move_to_end(reference.pts)
            return self.cache[reference.pts].copy()
        self.container.seek(reference.pts, stream=self.stream, backward=True, any_frame=False)
        for frame in self.container.decode(self.stream):
            check_cancel(cancel)
            if frame.pts == reference.pts:
                image = display_image(frame, self.info.aspect)
                self.cache[reference.pts] = image
                while len(self.cache) > self.cache_size:
                    self.cache.popitem(last=False)
                return image.copy()
            if frame.pts is not None and frame.pts > reference.pts:
                break
        raise ValueError(f"Frame not found: PTS {reference.pts}")
