import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .models import AnalysisSettings, CropRect, ExportSettings, FrameRef, Marker


def fingerprint(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def save_project(destination, info, markers, analysis, export, identity):
    destination = Path(destination).resolve()
    if destination == info.path:
        raise ValueError("Cannot overwrite the source video")
    analysis.validate()
    export.validate()
    try:
        video_path = os.path.relpath(info.path, destination.parent)
    except ValueError:
        video_path = str(info.path)
    document = {
        "version": 1,
        "video": video_path,
        "fingerprint": identity,
        "stream_index": info.stream_index,
        "markers": [asdict(marker) for marker in markers],
        "analysis": asdict(analysis),
        "export": asdict(export),
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_project(path):
    with Path(path).open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported project version")
    if not isinstance(document.get("video"), str) or not document["video"]:
        raise TypeError("Missing source video")
    identity = document.get("fingerprint", {})
    if not isinstance(identity, dict) or not isinstance(identity.get("sha256"), str):
        raise TypeError("Missing video fingerprint")
    analysis = AnalysisSettings(**document["analysis"])
    export = ExportSettings(**document["export"])
    analysis.validate()
    export.validate()
    markers = []
    for entry in document["markers"]:
        entry = dict(entry)
        entry["frame"] = FrameRef(**entry["frame"])
        entry["crop"] = CropRect(**entry["crop"])
        marker = Marker(**entry)
        if marker.source not in ("automatic", "manual") or not isinstance(marker.uid, str):
            raise ValueError("Invalid marker")
        if any(
            type(value) is not bool for value in (marker.modified, marker.included, marker.review)
        ):
            raise ValueError("Invalid marker flags")
        if (
            type(marker.frame.pts) is not int
            or type(marker.frame.index) is not int
            or marker.frame.index < 0
            or marker.frame.denominator <= 0
        ):
            raise ValueError("Invalid frame reference")
        markers.append(marker)
    if len({marker.uid for marker in markers}) != len(markers):
        raise ValueError("Duplicate marker IDs")
    if len({marker.frame.pts for marker in markers}) != len(markers):
        raise ValueError("Duplicate marker frames")
    return document, sorted(markers, key=lambda marker: marker.frame.seconds), analysis, export


def validate_source(document, info, markers, identity):
    if document["fingerprint"] != identity or document["stream_index"] != info.stream_index:
        raise ValueError("Selected video differs from the saved project")
    for marker in markers:
        if (
            marker.frame.index >= len(info.frames)
            or info.frames[marker.frame.index] != marker.frame
        ):
            raise ValueError("Saved frame does not match this video")
