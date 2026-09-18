import os
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"


def publish_script():
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step = lines.index("      - name: Publish GitHub Release")
    run = lines.index("        run: |", step)
    return "\n".join(line[10:] if line else "" for line in lines[run + 1 :])


def available_bash():
    executable = shutil.which("bash")
    if executable:
        return executable
    portable = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/PortableGit/bin/bash.exe"
    if portable.is_file():
        return str(portable)
    pytest.skip("bash is not available")


def run_publish(tmp_path, release_state):
    artifacts = tmp_path / "release-artifacts"
    artifacts.mkdir()
    for name in ("SHA256SUMS.txt", "application.dmg", "application.zip"):
        (artifacts / name).write_text(name, encoding="utf-8")

    fake_gh = r"""
gh() {
  printf '%s\n' "$*" >> "$GH_LOG"
  if [[ "$1 $2" == "release view" ]]; then
    case "$RELEASE_STATE" in
      missing) return 1 ;;
      draft) printf 'true\n'; return 0 ;;
      public) printf 'false\n'; return 0 ;;
    esac
  fi
}
"""
    script = publish_script().replace("${{ github.ref_name }}", "v9.9.9").replace(
        "${{ github.repository }}", "st70712/video_frame_cutter"
    )
    environment = os.environ | {"GH_LOG": "gh.log", "RELEASE_STATE": release_state}
    result = subprocess.run(
        [available_bash(), "-c", fake_gh + script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = (tmp_path / "gh.log").read_text(encoding="utf-8").splitlines()
    return result, calls


def test_publish_is_retryable_without_exposing_partial_release():
    publish = publish_script()

    view = publish.index("gh release view")
    create = publish.index("gh release create")
    upload_loop = publish.index("for asset in release-artifacts/*; do")
    upload = publish.index("gh release upload")
    make_public = publish.index('gh release edit "$tag"')

    assert view < create < upload_loop < upload < make_public
    assert "set -euo pipefail" in publish
    assert 'if [[ "$is_draft" != "true" ]]' in publish
    assert "--draft" in publish[create:upload_loop]
    assert "release-artifacts/*" not in publish[create:upload_loop]
    assert '"$asset"' in publish[upload:make_public]
    assert "--clobber" in publish[upload:make_public]
    assert "--draft=false" in publish[make_public:]


def test_publish_creates_draft_uploads_each_asset_and_makes_it_public(tmp_path):
    result, calls = run_publish(tmp_path, "missing")

    assert result.returncode == 0, result.stderr
    assert calls[0].startswith("release view v9.9.9")
    assert sum(call.startswith("release create v9.9.9") for call in calls) == 1
    uploads = [call for call in calls if call.startswith("release upload v9.9.9")]
    assert len(uploads) == 3
    assert all("--clobber" in call for call in uploads)
    assert calls[-1] == (
        "release edit v9.9.9 --repo st70712/video_frame_cutter --draft=false"
    )


def test_publish_reuses_an_existing_draft(tmp_path):
    result, calls = run_publish(tmp_path, "draft")

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("release create") for call in calls)
    assert sum(call.startswith("release upload v9.9.9") for call in calls) == 3
    assert calls[-1].startswith("release edit v9.9.9")


def test_publish_refuses_to_replace_a_public_release(tmp_path):
    result, calls = run_publish(tmp_path, "public")

    assert result.returncode != 0
    assert calls == [
        "release view v9.9.9 --repo st70712/video_frame_cutter --json isDraft --jq .isDraft"
    ]
    assert "already public; refusing to replace its assets" in result.stdout