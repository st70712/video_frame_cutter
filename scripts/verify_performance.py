import argparse
import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from video_frame_cutter.analysis import analysis_defaults
from video_frame_cutter.verification_io import read_snapshot
from video_frame_cutter.verification_io import save_snapshot as save_state


def process_identity(pid):
    if os.name != "nt":
        return None
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        exit_code = wintypes.DWORD()
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in (
            created, exited, system, user,
        ))) or not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 259:
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def inspect_status(output):
    report = read_snapshot(output / "result.json")
    if report["status"] != "running":
        return report
    status_path = Path(report.get("active_status_file", output / "result.json"))
    stage = read_snapshot(status_path) if status_path.exists() else report
    checked = datetime.now().astimezone()
    updated = datetime.fromisoformat(stage.get("updated_at", report["started_at"]))
    report["heartbeat_age_seconds"] = max(0, (checked - updated).total_seconds())
    report["stage_status"] = stage
    if os.name == "nt":
        identity = process_identity(report["supervisor_pid"])
        if identity is None or identity != report.get("supervisor_identity"):
            report.update(status="interrupted", error="Supervisor no longer exists; result is incomplete")
            return report
    if report["heartbeat_age_seconds"] > 15:
        report.update(status="unresponsive", error="Supervisor heartbeat is stale; completion is unverified")
    return report


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def monitor_command(command, cwd, output, timeout, validate, *, heartbeat=5, progress_path=None):
    output.mkdir(parents=True, exist_ok=False)
    log_path = output / "run.log"
    started = time.monotonic()
    state = {
        "status": "starting", "supervisor_pid": os.getpid(), "child_pid": None,
        "started_at": datetime.now().astimezone().isoformat(), "command": list(command),
        "timeout_seconds": timeout, "heartbeat_seconds": heartbeat,
        "exit_code": None,
    }

    def checkpoint():
        state["updated_at"] = datetime.now().astimezone().isoformat()
        state["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if log_path.exists():
            stat = log_path.stat()
            state["log_bytes"] = stat.st_size
            state["seconds_since_log_update"] = round(max(0, time.time() - stat.st_mtime), 3)
            with log_path.open("rb") as log:
                log.seek(max(0, stat.st_size - 2048))
                state["last_output"] = log.read().decode("utf-8", errors="replace")
        if progress_path is not None and progress_path.exists():
            try:
                state["analysis_progress"] = read_snapshot(progress_path)
            except (OSError, ValueError):
                pass
        save_state(output / "status.json", state)

    checkpoint()
    process = None
    try:
        with log_path.open("wb") as logfile:
            process = subprocess.Popen(
                command, cwd=cwd, stdout=logfile, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1"),
            )
            state.update(status="running", child_pid=process.pid)
            checkpoint()
            print(f"STAGE pid={process.pid} status={output / 'status.json'}", flush=True)
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    state.update(status="timeout", error="Stage time limit exceeded")
                    break
                try:
                    state["exit_code"] = process.wait(timeout=min(heartbeat, remaining))
                    state["status"] = "failed"
                    if state["exit_code"] != 0:
                        state["error"] = f"Child exited with code {state['exit_code']}"
                    elif validate():
                        state["status"] = "passed"
                    else:
                        state["error"] = "Child exited without a valid successful report"
                    break
                except subprocess.TimeoutExpired:
                    checkpoint()
                    print(
                        f"WATCHDOG pid={process.pid} elapsed={state['elapsed_seconds']:.1f}s "
                        f"log_age={state['seconds_since_log_update']:.1f}s",
                        flush=True,
                    )
    except KeyboardInterrupt:
        state.update(status="cancelled", error="Interrupted by user")
    except Exception as error:
        logging.getLogger(__name__).exception("Supervised stage failed")
        state.update(status="error", error=f"{type(error).__name__}: {error}")
    finally:
        try:
            if process is not None:
                stop_process(process)
                state["exit_code"] = process.returncode
        except (OSError, subprocess.SubprocessError) as error:
            state["cleanup_error"] = str(error)
            state["status"] = "error"
        checkpoint()
        print(f"STAGE {state['status']} exit={state['exit_code']}", flush=True)
    return state


def json_passed(path):
    return path.is_file() and read_snapshot(path).get("status") == "passed"


def junit_passed(path):
    if not path.is_file():
        return False
    cases = ElementTree.parse(path).findall(".//testcase")
    return bool(cases) and any(case.find("skipped") is None for case in cases) and not any(
        case.find(tag) is not None for case in cases for tag in ("failure", "error")
    )


def main():
    parser = argparse.ArgumentParser(description="Supervise full analysis and GUI acceptance")
    parser.add_argument("--width", type=int, choices=(160, 480), default=480)
    parser.add_argument("--timeout", type=float, default=5400)
    parser.add_argument("--benchmark-timeout", type=float, default=3600)
    parser.add_argument("--gui-timeout", type=int, default=1800)
    parser.add_argument("--status", type=Path, help="Inspect an existing run without starting any work")
    arguments = parser.parse_args()
    if arguments.status:
        state = inspect_status(arguments.status.resolve())
        print(json.dumps(state, indent=2), flush=True)
        return 0 if state["status"] in ("running", "passed") else 1
    if min(arguments.timeout, arguments.benchmark_timeout, arguments.gui_timeout) < 30:
        parser.error("Timeouts must be at least 30 seconds")
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output = root / "outputs/performance" / f"{stamp}_{uuid4().hex[:6]}"
    output.mkdir(parents=True)
    report = {"status": "running", "supervisor_pid": os.getpid(),
              "supervisor_identity": process_identity(os.getpid()), "width": arguments.width,
              "started_at": datetime.now().astimezone().isoformat(), "stages": [],
              "timeout_seconds": arguments.timeout}
    started = time.monotonic()
    benchmark_path = output / "benchmark.json"
    progress_path = output / "progress.json"
    python = sys.executable
    defaults = analysis_defaults()
    benchmark = [python, "-u", str(root / "scripts/benchmark_analysis.py"), "--full",
                 "--width", str(arguments.width), "--workers", str(defaults["workers"]),
                 "--decoder-threads", str(defaults["decoder_threads"]),
                 "--output", str(benchmark_path), "--progress-output", str(progress_path)]
    gui_directory = output / "gui-results"
    gui = [python, "-u", str(root / "scripts/verify_real_video.py"),
           "--analysis-width", str(arguments.width), "--baseline-report", str(benchmark_path),
           "--timeout", str(arguments.gui_timeout), "--output-directory", str(gui_directory)]
    regression_path = output / "regression.xml"
    stages = [
        ("benchmark", benchmark, arguments.benchmark_timeout, lambda: json_passed(benchmark_path)),
        ("gui", gui, arguments.gui_timeout + 30, lambda: json_passed(gui_directory / "result.json")),
        ("regression", [python, "-m", "pytest", "-q", f"--junitxml={regression_path}"],
         120, lambda: junit_passed(regression_path)),
    ]
    save_state(output / "result.json", report)
    print(f"PERFORMANCE DIRECTORY {output}", flush=True)
    try:
        for name, command, limit, validate in stages:
            report["active_stage"] = name
            report["active_status_file"] = str(output / name / "status.json")
            save_state(output / "result.json", report)
            remaining = arguments.timeout - (time.monotonic() - started)
            if remaining <= 0:
                report["status"] = "timeout"
                break
            state = monitor_command(
                command, root, output / name, min(limit, remaining), validate,
                progress_path=progress_path if name == "benchmark" else None,
            )
            report["stages"].append({"name": name, **state})
            if state["status"] != "passed":
                report["status"] = state["status"]
                break
        else:
            report["status"] = "passed"
    except KeyboardInterrupt:
        report["status"] = "cancelled"
    except Exception as error:
        logging.getLogger(__name__).exception("Performance verification failed")
        report.update(status="error", error=f"{type(error).__name__}: {error}")
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished_at"] = datetime.now().astimezone().isoformat()
        save_state(output / "result.json", report)
        print(f"PERFORMANCE {report['status']}: {output / 'result.json'}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())