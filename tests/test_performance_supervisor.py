import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts import verify_performance
from scripts.verify_performance import (
    inspect_status,
    json_passed,
    monitor_command,
    process_identity,
)
from video_frame_cutter.verification_io import open_snapshot, read_snapshot


@pytest.mark.parametrize("mode", ["passed", "crash", "missing"])
def test_supervisor_requires_exit_and_success_report(tmp_path, mode):
    report = tmp_path / "child.json"
    code = (
        "import json, os; from pathlib import Path; "
        f"Path({str(report)!r}).write_text(json.dumps({{'status': 'running'}})); "
        "print('ANALYZING frames=12', flush=True); "
    )
    if mode == "passed":
        code += f"Path({str(report)!r}).write_text(json.dumps({{'status': 'passed'}}))"
    elif mode == "crash":
        code += "os._exit(7)"
    else:
        code += "raise SystemExit(0)"
    state = monitor_command(
        [sys.executable, "-u", "-c", code], tmp_path, tmp_path / "monitor", 10,
        lambda: json_passed(report), heartbeat=0.05,
    )
    assert state["status"] == ("passed" if mode == "passed" else "failed")
    assert state["exit_code"] == (7 if mode == "crash" else 0)
    saved = json.loads((tmp_path / "monitor/status.json").read_text())
    assert saved == state
    assert "ANALYZING frames=12" in saved["last_output"]
    assert saved["child_pid"]


def test_supervisor_times_out_silent_child_and_persists_heartbeat(tmp_path):
    state = monitor_command(
        [sys.executable, "-u", "-c", "from threading import Event; Event().wait()"],
        tmp_path, tmp_path / "monitor", 0.3, lambda: True, heartbeat=0.05,
    )
    assert state["status"] == "timeout"
    assert state["exit_code"] is not None
    assert state["elapsed_seconds"] < 8
    assert state["log_bytes"] == 0
    assert state["updated_at"] != state["started_at"]


def test_benchmark_reports_frame_progress(video_path):
    from scripts.benchmark_analysis import run_pass
    from video_frame_cutter import analysis
    from video_frame_cutter.models import AnalysisSettings
    from video_frame_cutter.video import index_video

    info = index_video(video_path)
    updates = []
    result = run_pass(analysis, info, AnalysisSettings(width=160), progress_callback=updates.append)
    assert updates[0]["frames"] == 0
    assert updates[0]["stage_complete"] is False
    assert updates[-1]["frames"] == result["signature"]["frames"] == len(info.frames)
    assert updates[-1]["stage_complete"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity")
def test_status_detects_lost_supervisor_and_stale_heartbeat(tmp_path):
    now = datetime.now().astimezone()
    report = {
        "status": "running", "started_at": now.isoformat(), "supervisor_pid": os.getpid(),
        "supervisor_identity": process_identity(os.getpid()),
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(report))
    assert inspect_status(tmp_path)["status"] == "running"
    report["started_at"] = (now - timedelta(seconds=30)).isoformat()
    path.write_text(json.dumps(report))
    assert inspect_status(tmp_path)["status"] == "unresponsive"
    report["supervisor_identity"] += 1
    path.write_text(json.dumps(report))
    assert inspect_status(tmp_path)["status"] == "interrupted"
    assert json.loads(path.read_text())["status"] == "running"


@pytest.mark.parametrize("failure", [None, "benchmark", "gui"])
def test_pipeline_stops_on_failure_and_runs_regression(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(verify_performance, "__file__", str(tmp_path / "scripts/verify_performance.py"))
    monkeypatch.setattr(sys, "argv", ["verify_performance.py"])
    real_popen = verify_performance.subprocess.Popen
    launched = []

    def launch(command, **kwargs):
        if "--progress-output" in command:
            stage = "benchmark"
            report_path = command[command.index("--output") + 1]
            content = json.dumps({"status": "passed"})
        elif "--output-directory" in command:
            stage = "gui"
            report_path = str(Path(
                command[command.index("--output-directory") + 1],
            ) / "result.json")
            content = json.dumps({"status": "passed"})
        else:
            stage = "regression"
            report_path = next(item.split("=", 1)[1] for item in command if item.startswith("--junitxml="))
            content = '<testsuites><testsuite><testcase name="fixture"/></testsuite></testsuites>'
        launched.append(stage)
        code = (
            f"from pathlib import Path; report = Path({report_path!r}); "
            f"report.parent.mkdir(parents=True, exist_ok=True); report.write_text({content!r}); "
            f"raise SystemExit({7 if failure == stage else 0})"
        )
        return real_popen([sys.executable, "-u", "-c", code], **kwargs)

    monkeypatch.setattr(verify_performance.subprocess, "Popen", launch)
    assert verify_performance.main() == (1 if failure else 0)
    report_path = next((tmp_path / "outputs/performance").glob("*/result.json"))
    report = json.loads(report_path.read_text())
    assert report["status"] == ("failed" if failure else "passed")
    expected = ["benchmark", "gui", "regression"]
    assert launched == (expected[:expected.index(failure) + 1] if failure else expected)


def test_real_benchmark_cli_under_supervisor(video_path, tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "benchmark.json"
    progress = tmp_path / "progress.json"
    command = [sys.executable, "-u", str(root / "scripts/benchmark_analysis.py"), str(video_path),
               "--width", "160", "--full", "--workers", "2", "--output", str(report),
               "--progress-output", str(progress)]
    state = monitor_command(
        command, root, tmp_path / "monitor", 30, lambda: json_passed(report),
        heartbeat=0.1, progress_path=progress,
    )
    assert state["status"] == "passed", state
    assert state["analysis_progress"]["status"] == "passed"
    assert state["analysis_progress"]["stage"] == "source_verification"
    document = json.loads(report.read_text())
    assert document["runs"][0]["matches_baseline"]
    assert document["baseline"]["signature"]["frames"] == 30


def test_snapshot_reader_allows_atomic_replacement(tmp_path):
    from scripts.benchmark_analysis import save_report

    path = tmp_path / "progress.json"
    save_report(path, {"frames": 1})
    with open_snapshot(path) as original:
        save_report(path, {"frames": 2})
        assert json.load(original) == {"frames": 1}
        assert read_snapshot(path) == {"frames": 2}
    path.unlink()
    assert not path.exists()