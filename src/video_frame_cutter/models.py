from dataclasses import dataclass, field
from fractions import Fraction
from math import isfinite
from uuid import uuid4


@dataclass(frozen=True)
class FrameRef:
    pts: int
    numerator: int
    denominator: int
    index: int
    seconds: float

    @property
    def time_base(self):
        return Fraction(self.numerator, self.denominator)


@dataclass(frozen=True)
class CropRect:
    left: float = 0.0
    top: float = 0.0
    right: float = 1.0
    bottom: float = 1.0

    def __post_init__(self):
        values = (self.left, self.top, self.right, self.bottom)
        if not all(isfinite(value) for value in values):
            raise ValueError("Invalid crop coordinates")
        if not (0 <= self.left < self.right <= 1 and 0 <= self.top < self.bottom <= 1):
            raise ValueError("Crop must be a nonempty rectangle within the image")

    def pixels(self, size):
        width, height = size
        left = min(width - 1, round(self.left * width))
        top = min(height - 1, round(self.top * height))
        return (
            left,
            top,
            max(left + 1, round(self.right * width)),
            max(top + 1, round(self.bottom * height)),
        )


@dataclass
class Marker:
    frame: FrameRef
    uid: str = field(default_factory=lambda: str(uuid4()))
    crop: CropRect = field(default_factory=CropRect)
    source: str = "manual"
    modified: bool = False
    included: bool = True
    review: bool = False
    score: float = 0.0
    change_seconds: float = 0.0


@dataclass
class AnalysisSettings:
    threshold: float = 0.08
    area: float = 0.008
    stable_seconds: float = 0.35
    min_interval: float = 0.5
    width: int = 480
    duplicate_window_seconds: float = 0.0

    def validate(self):
        if not (0.001 <= self.threshold <= 1 and 0 <= self.area <= 1):
            raise ValueError("Invalid detection threshold")
        if not (
            0.05 <= self.stable_seconds <= 10
            and 0 <= self.min_interval <= 60
            and 0 <= self.duplicate_window_seconds <= 60
        ):
            raise ValueError("Invalid detection timing")
        if not 64 <= self.width <= 1920:
            raise ValueError("Invalid analysis resolution")


@dataclass
class ExportSettings:
    width: int = 1920
    height: int = 1080
    quality: int = 95
    timestamp: bool = False

    def validate(self):
        if not (16 <= self.width <= 8192 and 16 <= self.height <= 8192):
            raise ValueError("Output dimensions must be between 16 and 8192")
        if not 1 <= self.quality <= 100:
            raise ValueError("JPEG quality must be between 1 and 100")


def timecode(seconds):
    milliseconds = round(max(0, seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"
