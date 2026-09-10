import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped"])
def test_runner_records_real_exit_and_rejects_skipped(tmp_path, monkeypatch, outcome):
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_real_video.py"
    spec = importlib.util.spec_from_file_location("verification_runner", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "__file__", str(tmp_path / "scripts" / "verify_real_video.py"))
    monkeypatch.setattr(sys, "argv", [str(script)])
    real_popen = subprocess.Popen

    def start_fake_test(command, **kwargs):
        report = next(argument.split("=", 1)[1] for argument in command if argument.startswith("--junitxml="))
        tag = {"passed": "", "failed": "<failure/>", "skipped": "<skipped/>"}[outcome]
        xml = f"<testsuites><testsuite><testcase>{tag}</testcase></testsuite></testsuites>"
        code = (
            f"from pathlib import Path; Path({report!r}).write_text({xml!r}); "
            f"print('STAGE simulated {outcome}', flush=True); "
            f"raise SystemExit({1 if outcome == 'failed' else 0})"
        )
        return real_popen([sys.executable, "-u", "-c", code], **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", start_fake_test)
    assert runner.main() == (0 if outcome == "passed" else 1)
    report = next((tmp_path / "outputs" / "verification").glob("*/result.json"))
    state = json.loads(report.read_text(encoding="utf-8"))
    assert state["status"] == ("passed" if outcome == "passed" else "failed")
    assert state["exit_code"] == (1 if outcome == "failed" else 0)
    assert state["analysis_width"] == 480
    assert f"STAGE simulated {outcome}" in report.with_name("run.log").read_text()

def test_save_snapshot_retries_transient_windows_replace_errors(tmp_path, monkeypatch):
    from video_frame_cutter import verification_io

    path = tmp_path / "progress.json"
    path.write_text("{}", encoding="utf-8")
    attempts = []

    def flaky_replace(target, temporary):
        attempts.append(target)
        if len(attempts) < 3:
            raise OSError(22, "held by another process", None, 1175)
        temporary.replace(target)

    monkeypatch.setattr(verification_io, "replace_file", flaky_replace)
    monkeypatch.setattr(verification_io, "REPLACE_RETRY_SECONDS", 0)
    verification_io.save_snapshot(path, {"frames": 5})
    assert len(attempts) == 3
    assert json.loads(path.read_text(encoding="utf-8")) == {"frames": 5}

    def permanent_failure(target, temporary):
        raise OSError(13, "access denied", None, 5)

    monkeypatch.setattr(verification_io, "replace_file", permanent_failure)
    with pytest.raises(OSError, match="access denied"):
        verification_io.save_snapshot(path, {"frames": 6})
