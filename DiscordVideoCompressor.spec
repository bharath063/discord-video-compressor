# Build on the target OS. SPDX-License-Identifier: GPL-3.0-only.
import os
from pathlib import Path
import sys
from PyInstaller.utils.hooks import copy_metadata

project = Path(SPECPATH)
release_version = (project / "VERSION").read_text(encoding="utf-8").strip()
bundle_version = release_version.split("-", 1)[0].split("+", 1)[0]
suffix = ".exe" if sys.platform == "win32" else ""
binaries = []
for name in ("ffmpeg", "ffprobe"):
    source = project / "binaries" / (name + suffix)
    if not source.is_file():
        raise FileNotFoundError(f"Missing {source}. Run scripts/fetch_ffmpeg.py first.")
    binaries.append((str(source), "binaries"))

icon_files = {
    name: project / "assets" / name for name in ("app.png", "app.ico", "app.icns")
}
for source in icon_files.values():
    if not source.is_file():
        raise FileNotFoundError(
            f"Missing {source}. Run scripts/build_icons.py on macOS first."
        )
datas = [
    (str(project / name), ".")
    for name in ("README.md", "THIRD_PARTY_NOTICES.md", "LICENSE", "VERSION")
]
datas.append((str(icon_files["app.png"]), "assets"))
for package in ("PyQt6", "PyQt6-Qt6", "PyQt6-sip"):
    datas += copy_metadata(package)
for name in ("build-manifest.json", "licenses"):
    source = project / "binaries" / name
    if source.exists():
        datas.append(
            (str(source), "binaries/licenses" if name == "licenses" else "binaries")
        )

analysis = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt6.QtWebEngine", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

common = dict(
    name="DiscordVideoCompressor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(icon_files["app.ico"]) if sys.platform == "win32" else None,
)

if sys.platform == "darwin":
    # Use an onedir .app so helper executables remain inside the signed bundle.
    executable = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        argv_emulation=False,
        codesign_identity=os.environ.get("MACOS_CODESIGN_IDENTITY") or None,
        **common,
    )
    collection = COLLECT(
        executable,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name="DiscordVideoCompressor",
    )
    app = BUNDLE(
        collection,
        name="DiscordVideoCompressor.app",
        icon=str(icon_files["app.icns"]),
        bundle_identifier="io.github.discord-video-compressor",
        version=bundle_version,
        info_plist={
            "CFBundleName": "Discord Video Compressor",
            "CFBundleDisplayName": "Discord Video Compressor",
            "NSHighResolutionCapable": True,
        },
    )
else:
    # The onefile payload includes both tools and Qt's platform plugins.
    executable = EXE(
        pyz, analysis.scripts, analysis.binaries, analysis.datas, [], **common
    )
