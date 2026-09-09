import argparse
import ctypes
import hashlib
import importlib.util
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from threading import Event, Lock
from unittest.mock import patch
from uuid import uuid4

from video_frame_cutter import analysis
from video_frame_cutter.models import AnalysisSettings
from video_frame_cutter.project import fingerprint
from video_frame_cutter.verification_io import save_snapshot as save_report
from video_frame_cutter.video import index_video

BASE_COMMIT = "e0dc42235eaf7f7cb8fbcf96f8177e4541c30295"


def memory_usage():
    if os.name != "nt":
        return {}
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "peak", "working", "paged_peak", "paged", "nonpaged_peak", "nonpaged",
                "pagefile", "pagefile_peak",
            )
        ]

    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    if not query(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return {"working_set_bytes": counters.working, "process_peak_bytes": counters.peak}


def git_executable():
    found = shutil.which("git")
    if found:
        return found
    portable = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/PortableGit/cmd/git.exe"
    if portable.is_file():
        return str(portable)
    raise RuntimeError("Git is required to load the independent baseline")


def load_baseline(root):
    source = subprocess.check_output(
        [git_executable(), "show", f"{BASE_COMMIT}:src/video_frame_cutter/analysis.py"],
        cwd=root,
    )
    with tempfile.TemporaryDirectory(prefix="vfc-baseline-") as directory:
        path = Path(directory) / "analysis.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("video_frame_cutter._baseline_analysis", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module.__name__] = module
        spec.loader.exec_module(module)
    return module, hashlib.sha256(source).hexdigest()


def result_signature(result, pixel_digest):
    curve_digest = hashlib.sha256()
    for seconds, score in result.curve:
        curve_digest.update(struct.pack("!dd", seconds, score))
    markers = []
    for marker in result.markers:
        values = asdict(marker)
        values.pop("uid")
        markers.append(values)
    return {
        "frames": len(result.curve), "pixels_sha256": pixel_digest,
        "curve_sha256": curve_digest.hexdigest(), "markers": markers,
    }


class AnalysisRecorder:
    def __init__(self, module=analysis, sample_frames=0, progress_callback=None):
        self.module = module
        self.sample_frames = sample_frames
        self.progress_callback = progress_callback
        self.pixel_digest = hashlib.sha256()
        self.frames = 0
        self.detector_seconds = 0.0
        self.preparation_seconds = 0.0
        self.lock = Lock()
        self.detector = None
        self.started = self.last_report = time.perf_counter()
        self.patches = []

    def __enter__(self):
        recorder = self
        original_detector = self.module.StableDetector

        class RecordedDetector(original_detector):
            def __init__(self, settings):
                super().__init__(settings)
                recorder.detector = self

            def feed(self, reference, pixels, *args, **kwargs):
                recorder.pixel_digest.update(struct.pack("!qII", reference.pts, *pixels.shape[:2]))
                recorder.pixel_digest.update(memoryview(pixels))
                started = time.perf_counter()
                super().feed(reference, pixels, *args, **kwargs)
                recorder.detector_seconds += time.perf_counter() - started
                recorder.frames += 1
                now = time.perf_counter()
                if now - recorder.last_report >= 5:
                    print(
                        f"ANALYZING frames={recorder.frames} elapsed={now - recorder.started:.1f}s",
                        flush=True,
                    )
                    if recorder.progress_callback is not None:
                        recorder.progress_callback({
                            "frames": recorder.frames,
                            "stage_elapsed_seconds": now - recorder.started,
                            "stage_complete": False,
                        })
                    recorder.last_report = now
                if recorder.sample_frames and recorder.frames >= recorder.sample_frames:
                    raise SampleComplete()

        self.patches.append(patch.object(self.module, "StableDetector", RecordedDetector))
        if hasattr(self.module, "analysis_pixels"):
            original_prepare = self.module.analysis_pixels

            def prepare(*args):
                started = time.perf_counter()
                pixels = original_prepare(*args)
                elapsed = time.perf_counter() - started
                with self.lock:
                    self.preparation_seconds += elapsed
                return pixels

            self.patches.append(patch.object(self.module, "analysis_pixels", prepare))
        for replacement in self.patches:
            replacement.start()
        return self

    def __exit__(self, *args):
        for replacement in reversed(self.patches):
            replacement.stop()


class SampleComplete(Exception):
    pass


def run_pass(module, info, settings, sample_frames=0, progress_callback=None, **configuration):
    memory_before = memory_usage()
    if progress_callback is not None:
        progress_callback({"frames": 0, "stage_elapsed_seconds": 0, "stage_complete": False})
    started = time.perf_counter()
    with AnalysisRecorder(module, sample_frames, progress_callback) as recorder:
        try:
            result = module.analyze(info, settings, Event(), **configuration)
        except SampleComplete:
            result = recorder.detector.finish()
    elapsed = time.perf_counter() - started
    if progress_callback is not None:
        progress_callback({
            "frames": recorder.frames, "stage_elapsed_seconds": elapsed, "stage_complete": True,
        })
    return {
        "analysis_elapsed_seconds": elapsed,
        "frames_per_second": recorder.frames / elapsed,
        "detector_seconds": recorder.detector_seconds,
        "preparation_worker_seconds": recorder.preparation_seconds,
        "configuration": configuration,
        "memory_before": memory_before, "memory_after": memory_usage(),
        "complete_video": not sample_frames or recorder.frames == len(info.frames),
        "signature": result_signature(result, recorder.pixel_digest.hexdigest()),
    }


def main():
    parser = argparse.ArgumentParser(description="Compare ordered analysis against the original commit")
    parser.add_argument("video", type=Path, nargs="?", default=Path("video.mp4"))
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--workers", type=int, nargs="+", default=[2, 3, 4])
    parser.add_argument("--decoder-threads", type=int, nargs="+", default=[2])
    parser.add_argument("--sample-frames", type=int, default=600)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit-seconds", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--progress-output", type=Path)
    arguments = parser.parse_args()
    if (arguments.sample_frames < 1 or arguments.repeats < 1
            or min(arguments.workers) < 1 or min(arguments.decoder_threads) < 0):
        parser.error("Invalid sample count, repeat count or thread configuration")
    root = Path(__file__).resolve().parents[1]
    output = arguments.output or root / "outputs/benchmarks" / uuid4().hex[:10] / "result.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    progress_path = arguments.progress_output or output.with_name("progress.json")
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    baseline, source_digest = load_baseline(root)
    report = {
        "status": "running", "started_at": datetime.now().astimezone().isoformat(),
        "baseline_commit": BASE_COMMIT, "baseline_source_sha256": source_digest,
        "revision": subprocess.check_output(
            [git_executable(), "rev-parse", "HEAD"], cwd=root, text=True,
        ).strip(),
        "working_tree_diff_sha256": hashlib.sha256(subprocess.check_output(
            [git_executable(), "diff", "--", "src"], cwd=root,
        )).hexdigest(),
        "python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "logical_cpus": os.cpu_count(),
        "versions": {name: version(name) for name in ("av", "numpy", "Pillow", "scikit-image")},
        "settings": asdict(AnalysisSettings(width=arguments.width)), "runs": [],
        "sample_frames": 0 if arguments.full else arguments.sample_frames,
        "timing_includes": "pixel hashing, decoder open/close and worker startup/shutdown",
    }
    save_report(output, report)
    print(f"REPORT {output.resolve()}", flush=True)
    stage = "fingerprint"

    def record_progress(values):
        progress = {
            "pid": os.getpid(), "updated_at": datetime.now().astimezone().isoformat(),
            "stage": stage, "width": arguments.width, "status": report["status"], **values,
        }
        if "frames" in values and "indexed_frames" in report:
            total = report["indexed_frames"]
            progress.update(total_frames=total, percent=round(values["frames"] / total * 100, 2))
        save_report(progress_path, progress)

    def index_progress(value):
        record_progress({"percent": value, "stage_complete": value == 100})
        if value % 10 == 0:
            print(f"INDEX {value}%", flush=True)

    try:
        record_progress({})
        report["source"] = fingerprint(arguments.video)
        stage = "indexing"
        started = time.perf_counter()
        info = index_video(arguments.video, progress=index_progress)
        report["index_elapsed_seconds"] = time.perf_counter() - started
        report["indexed_frames"] = len(info.frames)
        save_report(output, report)
        settings = AnalysisSettings(width=arguments.width)
        stage = "baseline"
        print("RUN baseline", flush=True)
        reference = run_pass(
            baseline, info, settings, report["sample_frames"], progress_callback=record_progress,
        )
        report["baseline"] = reference
        save_report(output, report)
        for decoder_threads in arguments.decoder_threads:
            for workers in arguments.workers:
                for repeat in range(arguments.repeats):
                    stage = f"optimized workers={workers} decoder={decoder_threads} run={repeat + 1}"
                    print(f"RUN workers={workers} decoder={decoder_threads} repeat={repeat + 1}",
                          flush=True)
                    measured = run_pass(
                        analysis, info, settings, report["sample_frames"], workers=workers,
                        buffer_size=workers * 2, decoder_threads=decoder_threads,
                        progress_callback=record_progress,
                    )
                    measured["matches_baseline"] = measured["signature"] == reference["signature"]
                    report["runs"].append(measured)
                    save_report(output, report)
                    print(f"RESULT seconds={measured['analysis_elapsed_seconds']:.3f} "
                          f"exact={measured['matches_baseline']}", flush=True)
                    if not measured["matches_baseline"]:
                        raise AssertionError("Pixel, curve or marker result differs from baseline")
                    if (arguments.limit_seconds is not None
                            and measured["analysis_elapsed_seconds"] >= arguments.limit_seconds):
                        raise AssertionError("Analysis exceeded the requested time limit")
        stage = "source_verification"
        record_progress({})
        if fingerprint(arguments.video) != report["source"]:
            raise AssertionError("Source changed during benchmark")
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        save_report(output, report)
        record_progress({"stage_complete": report["status"] == "passed"})
        print(f"BENCHMARK {report['status']}: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())