import os
import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


PROJECT_ROOT = Path(SPECPATH).parent
PACKAGE_VERSION = version("video-frame-cutter")
APPLICATION_NAME = "Video Frame Cutter"
EXECUTABLE_NAME = "VideoFrameCutter"
ICON_PATH = PROJECT_ROOT / "src" / "video_frame_cutter" / "resources" / "app-icon.png"

av_datas, av_binaries, av_hidden_imports = collect_all("av")
datas = copy_metadata("video-frame-cutter") + av_datas + [
    (str(ICON_PATH), "video_frame_cutter/resources"),
]

windows_version = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version_parts = tuple(int(part) for part in PACKAGE_VERSION.split("."))
    file_version = (*version_parts, *(0 for _ in range(4 - len(version_parts))))
    windows_version = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=file_version,
            prodvers=file_version,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Video Frame Cutter"),
                            StringStruct("FileDescription", APPLICATION_NAME),
                            StringStruct("FileVersion", PACKAGE_VERSION),
                            StringStruct("InternalName", EXECUTABLE_NAME),
                            StringStruct("OriginalFilename", f"{EXECUTABLE_NAME}.exe"),
                            StringStruct("ProductName", APPLICATION_NAME),
                            StringStruct("ProductVersion", PACKAGE_VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

analysis = Analysis(
    [str(PROJECT_ROOT / "packaging" / "entrypoint.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=av_binaries,
    datas=datas,
    hiddenimports=av_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytestqt"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=EXECUTABLE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get("VFC_BUILD_CONSOLE") == "1",
    icon=str(ICON_PATH),
    version=windows_version,
    target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=EXECUTABLE_NAME,
)

if sys.platform == "darwin":
    application = BUNDLE(
        collection,
        name=f"{APPLICATION_NAME}.app",
        icon=str(ICON_PATH),
        bundle_identifier="io.github.st70712.video-frame-cutter",
        version=PACKAGE_VERSION,
        info_plist={
            "CFBundleDisplayName": APPLICATION_NAME,
            "CFBundleShortVersionString": PACKAGE_VERSION,
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
        },
    )