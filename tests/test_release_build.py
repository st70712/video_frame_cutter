from pathlib import Path

import pytest

from scripts import build_release


def test_project_version_reads_pyproject(tmp_path):
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    assert build_release.project_version(project_file) == "1.2.3"


@pytest.mark.parametrize("value", ["1.2", "v1.2", "1.2.3.4", "release-1.2.3", "01.2.3"])
def test_normalized_version_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="vX.Y.Z"):
        build_release.normalized_version(value)


def test_validate_expected_version_accepts_tag_and_rejects_mismatch():
    assert build_release.validate_expected_version("v1.2.3", "1.2.3") == "1.2.3"

    with pytest.raises(ValueError, match="does not match"):
        build_release.validate_expected_version("v1.2.4", "1.2.3")


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("win32", "AMD64", ("windows", "x64")),
        ("darwin", "arm64", ("macos", "arm64")),
    ],
)
def test_current_target_accepts_supported_native_targets(system, machine, expected):
    assert build_release.current_target(system, machine) == expected


@pytest.mark.parametrize(
    ("system", "machine"),
    [("win32", "ARM64"), ("darwin", "x86_64"), ("linux", "x86_64")],
)
def test_current_target_rejects_unsupported_targets(system, machine):
    with pytest.raises(RuntimeError, match="Unsupported release target"):
        build_release.current_target(system, machine)


def test_artifact_names_are_platform_specific():
    assert build_release.artifact_name("1.2.3", ("windows", "x64")) == (
        "Video-Frame-Cutter-v1.2.3-windows-x64.zip"
    )
    assert build_release.artifact_name("1.2.3", ("macos", "arm64")) == (
        "Video-Frame-Cutter-v1.2.3-macos-arm64.dmg"
    )


def test_run_pyinstaller_writes_build_log(tmp_path, monkeypatch):
    build_root = tmp_path / "build"
    calls = []

    class FakeProcess:
        args = ("PyInstaller",)

        def wait(self, timeout):
            return 0

    def fake_popen(command, **options):
        options["stdout"].write("build output\n")
        calls.append((command, options))
        return FakeProcess()

    monkeypatch.setattr(build_release, "BUILD_ROOT", build_root)
    monkeypatch.setattr(build_release.subprocess, "Popen", fake_popen)

    build_release.run_pyinstaller()

    assert (build_root / "pyinstaller.log").read_text(encoding="utf-8") == "build output\n"
    assert calls[0][1]["stderr"] == build_release.subprocess.STDOUT


def test_package_windows_includes_readme(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    bundle = project_root / "build" / "release" / "dist" / "VideoFrameCutter"
    bundle.mkdir(parents=True)
    (bundle / "VideoFrameCutter.exe").write_bytes(b"executable")
    (project_root / "README.md").write_text("release notes", encoding="utf-8")
    monkeypatch.setattr(build_release, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(build_release, "BUILD_ROOT", project_root / "build" / "release")
    monkeypatch.setattr(build_release, "RELEASE_ROOT", project_root / "release")
    monkeypatch.setattr(build_release, "current_target", lambda: ("macos", "arm64"))
    build_release.RELEASE_ROOT.mkdir()

    artifact = build_release.package_windows("1.2.3")

    assert artifact == Path(
        project_root / "release" / "Video-Frame-Cutter-v1.2.3-windows-x64.zip"
    )
    assert artifact.is_file()