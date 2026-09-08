import os
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt

from .models import timecode
from .video import FrameReader, check_cancel


def render_marker(reader, marker, settings, cancel=None):
    settings.validate()
    image = reader.get(marker.frame, cancel)
    image = image.crop(marker.crop.pixels(image.size))
    image = image.resize((settings.width, settings.height), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.convert("RGB").save(output, "JPEG", quality=settings.quality)
    return output.getvalue()


def export_markers(
    info,
    markers,
    settings,
    destination,
    kind,
    cancel=None,
    progress=lambda value: None,
    overwrite=False,
):
    settings.validate()
    markers = sorted(
        [marker for marker in markers if marker.included], key=lambda marker: marker.frame.seconds
    )
    if not markers:
        raise ValueError("No markers selected for export")
    if kind not in ("jpg", "pptx"):
        raise ValueError("Unsupported output format")
    destination = Path(destination).resolve()
    if destination == info.path:
        raise ValueError("Cannot overwrite the source video")
    if kind == "pptx" and destination.exists() and not overwrite:
        raise FileExistsError(destination)
    parent = destination if kind == "jpg" else destination.parent
    with tempfile.TemporaryDirectory(prefix=".frame-export-", dir=parent) as temporary:
        staging = Path(temporary)
        deck = None
        if kind == "pptx":
            deck = Presentation()
            ratio = settings.width / settings.height
            image_height = Inches(min(7.5, 50 / ratio))
            image_width = round(image_height * ratio)
            footer = Inches(0.35) if settings.timestamp else 0
            deck.slide_width = max(Inches(2.5 if settings.timestamp else 1), image_width)
            deck.slide_height = max(Inches(1), image_height + footer)
            image_left = (deck.slide_width - image_width) // 2
            image_top = (deck.slide_height - footer - image_height) // 2
        with FrameReader(info, cache_size=1) as reader:
            for index, marker in enumerate(markers):
                check_cancel(cancel)
                content = render_marker(reader, marker, settings, cancel)
                if deck is None:
                    name = f"{index + 1:04}_{timecode(marker.frame.seconds).replace(':', '-')}.jpg"
                    (staging / name).write_bytes(content)
                else:
                    slide = deck.slides.add_slide(deck.slide_layouts[6])
                    slide.shapes.add_picture(
                        BytesIO(content), image_left, image_top, image_width, image_height
                    )
                    if settings.timestamp:
                        text = slide.shapes.add_textbox(
                            Inches(0.15), deck.slide_height - footer,
                            deck.slide_width - Inches(0.3), footer
                        )
                        paragraph = text.text_frame.paragraphs[0]
                        paragraph.text = timecode(marker.frame.seconds)
                        paragraph.font.size = Pt(12)
                progress(int((index + 1) / len(markers) * 100))
        check_cancel(cancel)
        if deck is not None:
            output = staging / "export.pptx"
            deck.save(str(output))
            check_cancel(cancel)
            if destination.exists() and not overwrite:
                raise FileExistsError(destination)
            os.replace(output, destination)
            return destination
        name = f"frames_{datetime.now().astimezone():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        output = destination / name
        os.rename(staging, output)
        return output
