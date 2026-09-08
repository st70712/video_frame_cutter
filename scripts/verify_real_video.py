import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from video_frame_cutter.verification_io import save_snapshot


def main():
    parser = argparse.ArgumentParser(description="Run monitored original-video acceptance tests")
    parser.add_argument("--analysis-width", type=int, default=480, choices=(160, 320, 480, 640, 960))
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--analysis-repeats", type=int, default=1)
    parser.add_argument("--analysis-limit", type=float, default=0)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--output-directory", type=Path)
    arguments = parser.parse_args()
    if arguments.timeout < 30:
        parser.error("--timeout must be at least 30 seconds")
    if arguments.analysis_repeats < 1 or arguments.analysis_limit < 0:
        parser.error("Invalid analysis repeat count or limit")
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output = (arguments.output_directory.resolve() if arguments.output_directory
              else root / "outputs" / "verification" / f"{stamp}_{uuid4().hex[:6]}")
    output.mkdir(parents=True)
    environment = dict(os.environ, VFC_REAL_TEST="1", PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    environment.update(
        VFC_ANALYSIS_WIDTH=str(arguments.analysis_width), VFC_VERIFY_DIR=str(output),
        VFC_TEST_TIMEOUT=str(arguments.timeout),
        VFC_ANALYSIS_REPEATS=str(arguments.analysis_repeats),
        VFC_ANALYSIS_LIMIT=str(arguments.analysis_limit),
    )
    environment.pop("VFC_BASELINE_REPORT", None)
    if arguments.baseline_report:
        environment["VFC_BASELINE_REPORT"] = str(arguments.baseline_report.resolve())
    command = [sys.executable, "-u", "-m", "pytest", "tests/test_real_video.py", "-v", "-s",
               f"--junitxml={output / 'pytest.xml'}"]
    started = time.monotonic()
    state = {
        "status": "running", "started_at": datetime.now().astimezone().isoformat(),
        "python": sys.executable, "analysis_width": arguments.analysis_width,
        "timeout_seconds": arguments.timeout, "command": command,
        "analysis_repeats": arguments.analysis_repeats,
        "analysis_limit_seconds": arguments.analysis_limit,
        "limitations": ["Subjective audio sync and PowerPoint rendering require manual verification"],
    }

    def save_state():
        state["elapsed_seconds"] = round(time.monotonic() - started, 2)
        save_snapshot(output / "result.json", state)

    save_state()
    print(f"Verification directory: {output}", flush=True)
    process = None
    try:
        with (output / "run.log").open("w", encoding="utf-8", buffering=1) as logfile:
            process = subprocess.Popen(
                command, cwd=root, env=environment, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            )
            state["pid"] = process.pid
            save_state()
            messages = queue.Queue()

            def read_output():
                try:
                    for line in process.stdout:
                        messages.put(line)
                finally:
                    messages.put(None)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            while True:
                if time.monotonic() - started > arguments.timeout:
                    state["status"] = "timeout"
                    print("Total timeout reached; stopping test process.", flush=True)
                    break
                try:
                    line = messages.get(timeout=10)
                except queue.Empty:
                    line = f"WATCHDOG pid={process.pid} elapsed={time.monotonic() - started:.0f}s waiting for test output\n"
                if line is None:
                    process.wait(timeout=10)
                    break
                print(line, end="", flush=True)
                logfile.write(line)
                state["last_output"] = line.strip()[-500:]
                save_state()
    except KeyboardInterrupt:
        state["status"] = "cancelled"
    except (OSError, subprocess.SubprocessError) as error:
        state["status"] = "error"
        state["error"] = f"{type(error).__name__}: {error}"
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            state["exit_code"] = process.wait()
        if state["status"] == "running":
            state["status"] = "failed"
            report = output / "pytest.xml"
            if state.get("exit_code") == 0 and report.exists():
                cases = ElementTree.parse(report).findall(".//testcase")
                if len(cases) == 1 and not any(
                    case.find(tag) is not None
                    for case in cases for tag in ("failure", "error", "skipped")
                ):
                    state["status"] = "passed"
        metrics_path = output / "analysis.json"
        if metrics_path.exists():
            state["analysis"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        save_state()
        print(f"Verification {state['status']}: {output / 'result.json'}", flush=True)
    return 0 if state["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())