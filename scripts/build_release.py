import argparse
import platform
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = PROJECT_ROOT / "build" / "release"
RELEASE_ROOT = PROJECT_ROOT / "release"
SPEC_PATH = PROJECT_ROOT / "packaging" / "video-frame-cutter.spec"
APPLICATION_NAME = "Video Frame Cutter"
EXECUTABLE_NAME = "VideoFrameCutter"
VERSION_PATTERN = re.compile(r"(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def project_version(project_file=PROJECT_ROOT / "pyproject.toml"):
    with project_file.open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def normalized_version(value):
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Release version must use vX.Y.Z or X.Y.Z, got {value!r}")
    return ".".join(match.groups())


def validate_expected_version(expected, actual):
    normalized = normalized_version(expected)
    if normalized != actual:
        raise ValueError(f"Release version {normalized} does not match pyproject version {actual}")
    return normalized


def current_target(system=sys.platform, machine=None):
    machine = (machine or platform.machine()).lower()
    if system == "win32" and machine in {"amd64", "x86_64"}:
        return "windows", "x64"
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos", "arm64"
    raise RuntimeError(f"Unsupported release target: {system}/{machine}")


def artifact_name(version, target=None):
    system, architecture = target or current_target()
    extension = "zip" if system == "windows" else "dmg"
    return f"Video-Frame-Cutter-v{version}-{system}-{architecture}.{extension}"


def run_pyinstaller():
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = BUILD_ROOT / "pyinstaller.log"
    print(f"Building application; PyInstaller log: {log_path}")
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(BUILD_ROOT / "dist"),
                "--workpath",
                str(BUILD_ROOT / "work"),
                str(SPEC_PATH),
            ],
            cwd=PROJECT_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        started = time.monotonic()
        try:
            while True:
                try:
                    return_code = process.wait(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    elapsed = round(time.monotonic() - started)
                    print(f"PyInstaller is still running ({elapsed}s)", flush=True)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise
        if return_code:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n".join(lines[-80:]), file=sys.stderr)
            raise subprocess.CalledProcessError(return_code, process.args)


def package_windows(version):
    bundle = BUILD_ROOT / "dist" / EXECUTABLE_NAME
    executable = bundle / f"{EXECUTABLE_NAME}.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"PyInstaller output is missing: {executable}")
    shutil.copy2(PROJECT_ROOT / "README.md", bundle / "README.md")
    archive_base = RELEASE_ROOT / artifact_name(version, ("windows", "x64")).removesuffix(".zip")
    return Path(
        shutil.make_archive(str(archive_base), "zip", root_dir=bundle.parent, base_dir=bundle.name)
    )


def package_macos(version):
    application = BUILD_ROOT / "dist" / f"{APPLICATION_NAME}.app"
    if not application.is_dir():
        raise FileNotFoundError(f"PyInstaller output is missing: {application}")
    staging = BUILD_ROOT / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(application, staging / application.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
    artifact = RELEASE_ROOT / artifact_name(version, ("macos", "arm64"))
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            APPLICATION_NAME,
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            str(artifact),
        ],
        check=True,
    )
    return artifact


def build_release(expected_version):
    version = project_version()
    validate_expected_version(expected_version, version)
    system, _ = current_target()
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    shutil.rmtree(RELEASE_ROOT, ignore_errors=True)
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    run_pyinstaller()
    if system == "windows":
        return package_windows(version)
    return package_macos(version)


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Build a native Video Frame Cutter release")
    parser.add_argument("--expected-version", required=True)
    options = parser.parse_args(arguments)
    try:
        artifact = build_release(options.expected_version)
    except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())