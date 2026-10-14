"""Download and verify the static tools used by the release build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
BTBN_RELEASE = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08"
)
MAC_RELEASE = "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2"

# Pin both the build and its published SHA-256. Update them together.
DOWNLOADS = {
    "macos-arm64": [
        (
            f"{MAC_RELEASE}/ffmpeg.zip",
            "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924",
        ),
        (
            f"{MAC_RELEASE}/ffprobe.zip",
            "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6",
        ),
    ],
    "windows-x64": [
        (
            f"{BTBN_RELEASE}/ffmpeg-n8.1.3-9-g29e619e767-win64-gpl-8.1.zip",
            "7b801cdd3a1a0bb54ae6f572187e68b4ed54f52086cfee133fab2180bfb429fa",
        ),
    ],
    "linux-x64": [
        (
            f"{BTBN_RELEASE}/ffmpeg-n8.1.3-9-g29e619e767-linux64-gpl-8.1.tar.xz",
            "97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79",
        ),
    ],
}


def current_target() -> str:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if machine in {"x86_64", "amd64"} and system in {"Windows", "Linux"}:
        return "windows-x64" if system == "Windows" else "linux-x64"
    raise RuntimeError(
        "Automatic downloads support macOS ARM64, Windows x64, and Linux x64. "
        "For another architecture, place matching static ffmpeg and ffprobe tools in binaries/."
    )


def download(url: str, destination: Path, digest: str):
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 DiscordVideoCompressor/1.0"}
            )
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                destination.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    with destination.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise RuntimeError(
            f"SHA-256 mismatch for {url}: expected {digest}, received {actual}"
        )


def extract_tools(archive: Path, destination: Path, target: str):
    expected = (
        {"ffmpeg.exe", "ffprobe.exe"}
        if target == "windows-x64"
        else {"ffmpeg", "ffprobe"}
    )
    seen: set[str] = set()

    def copy_member(name, source):
        basename = PurePosixPath(name).name
        if basename in expected:
            if basename in seen or (destination / basename).exists():
                raise RuntimeError(f"Archive contains a duplicate binary: {basename}")
            with (destination / basename).open("wb") as output:
                shutil.copyfileobj(source, output)
            (destination / basename).chmod(0o755)
            seen.add(basename)
        elif any(
            token in basename.upper() for token in ("LICENSE", "COPYING", "NOTICE")
        ):
            # Write selected regular files by basename. Never extract archive paths.
            license_folder = destination / "licenses"
            license_folder.mkdir(exist_ok=True)
            with (license_folder / basename).open("wb") as output:
                shutil.copyfileobj(source, output)

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if (
                    member.is_dir()
                    or (member.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    continue
                basename = PurePosixPath(member.filename).name
                if basename in expected or any(
                    t in basename.upper() for t in ("LICENSE", "COPYING", "NOTICE")
                ):
                    with bundle.open(member) as source:
                        copy_member(member.filename, source)
    else:
        with tarfile.open(archive, "r:xz") as bundle:
            for member in bundle:
                basename = PurePosixPath(member.name).name
                if member.isfile() and (
                    basename in expected
                    or any(
                        t in basename.upper() for t in ("LICENSE", "COPYING", "NOTICE")
                    )
                ):
                    source = bundle.extractfile(member)
                    if source:
                        with source:
                            copy_member(member.name, source)


def fetch(target: str, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ffmpeg-download-") as directory:
        stage = Path(directory)
        tools = stage / "tools"
        tools.mkdir()
        manifest = []
        for url, digest in DOWNLOADS[target]:
            archive = stage / url.rsplit("/", 1)[-1]
            print(f"Downloading {url}", flush=True)
            download(url, archive, digest)
            extract_tools(archive, tools, target)
            manifest.append({"url": url, "sha256": digest})
        suffix = ".exe" if target == "windows-x64" else ""
        for name in ("ffmpeg", "ffprobe"):
            binary = tools / (name + suffix)
            if not binary.is_file():
                raise RuntimeError(
                    f"The downloaded archives do not contain {binary.name}."
                )
        # Check the native files before copying them into the build inputs.
        if target == current_target():
            for name in ("ffmpeg", "ffprobe"):
                subprocess.run(
                    [str(tools / (name + suffix)), "-version"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    timeout=20,
                )
            encoders = subprocess.check_output(
                [str(tools / ("ffmpeg" + suffix)), "-hide_banner", "-encoders"],
                text=True,
                timeout=20,
            )
            required = {
                "macos-arm64": ["libx265", "hevc_videotoolbox"],
                "windows-x64": ["libx265", "hevc_nvenc", "hevc_amf", "hevc_qsv"],
                "linux-x64": ["libx265", "hevc_nvenc", "hevc_qsv"],
            }[target]
            for encoder in required:
                if encoder not in encoders:
                    raise RuntimeError(
                        f"The build does not contain required encoder {encoder}."
                    )
            license_folder = tools / "licenses"
            license_folder.mkdir(exist_ok=True)
            for option, filename in (
                ("-L", "FFmpeg-license.txt"),
                ("-buildconf", "FFmpeg-buildconf.txt"),
            ):
                text = subprocess.check_output(
                    [str(tools / ("ffmpeg" + suffix)), option],
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=20,
                )
                (license_folder / filename).write_text(text, encoding="utf-8")
        (tools / "build-manifest.json").write_text(
            json.dumps({"target": target, "downloads": manifest}, indent=2) + "\n",
            encoding="utf-8",
        )
        for item in tools.iterdir():
            if item.is_dir():
                shutil.copytree(item, destination / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination / item.name)
    print(f"Verified tools saved in {destination}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=DOWNLOADS,
        help="Defaults to the current OS and architecture.",
    )
    parser.add_argument("--destination", type=Path, default=PROJECT / "binaries")
    args = parser.parse_args()
    fetch(args.target or current_target(), args.destination.resolve())


if __name__ == "__main__":
    main()
