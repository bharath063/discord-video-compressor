"""A local PyQt6 video compressor. SPDX-License-Identifier: GPL-3.0-only."""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
from string import Template
import subprocess
import sys
import tempfile
import threading
import time

from PyQt6.QtCore import (
    QObject,
    QPointF,
    QRectF,
    QSettings,
    QThread,
    QTimer,
    Qt,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QCloseEvent,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
    QSslSocket,
)
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Discord Video Compressor"
APP_ID = "io.github.discord-video-compressor"
PROJECT_URL = "https://github.com/bharath063/discord-video-compressor"
WEBSITE_URL = "https://bharath063.github.io/discord-video-compressor/"
RELEASES_URL = PROJECT_URL + "/releases/latest"
UPDATE_API_URL = (
    "https://api.github.com/repos/bharath063/discord-video-compressor/releases/latest"
)
AFFILIATION_NOTICE = "This app is not affiliated with Discord."
WINDOWS_SECURITY_URL = "windowsdefender://allowappthroughfolder"
WINDOWS_ACCESS_HELP = (
    "Windows did not allow access to this file or folder.\n\n"
    "If Windows Security shows a Controlled folder access notice:\n"
    "1. Open Windows Security > Virus & threat protection.\n"
    "2. Select Manage ransomware protection.\n"
    "3. Select Allow an app through Controlled folder access.\n"
    "4. Select Add an allowed app > Recently blocked apps.\n"
    "5. Allow the program named in the blocked access notice. It may be "
    "DiscordVideoCompressor.exe, ffmpeg.exe, or ffprobe.exe.\n\n"
    "Restart the app, then try again. You can also choose another folder."
)
_DLL_DIRECTORY_LOCK = threading.Lock()
UPDATE_TIMEOUT_MS = 15_000
MAX_UPDATE_BYTES = 256 * 1024
DEFAULT_TARGET_MB = 19.5
BUFFERED_TARGET_MB = 18.0
MIN_TARGET_MB = 1.0
MAX_TARGET_MB = 500.0
MAX_OUTPUT_BYTES = int(
    DEFAULT_TARGET_MB * 1_000_000
)  # Each job uses its selected limit.
AUDIO_BITRATE = 128_000
MIN_VIDEO_BITRATE = 32_000
MAX_ATTEMPTS = 4

THEMES = {
    "frost": {
        "name": "Frost / White",
        "background": "#f0f2f4",
        "surface": "#ffffff",
        "raised": "#e8ecef",
        "text": "#242b33",
        "muted": "#626c77",
        "border": "#d3dae0",
        "accent": "#35424f",
        "hover": "#25313c",
        "on_accent": "#ffffff",
    },
    "ink": {
        "name": "Ink / Mint",
        "background": "#090d12",
        "surface": "#111820",
        "raised": "#1b2631",
        "text": "#eef3f7",
        "muted": "#a6b4c2",
        "border": "#30404f",
        "accent": "#83f2cf",
        "hover": "#a6fbe0",
        "on_accent": "#092219",
    },
}

THEME_QSS = Template("""
QWidget { background: $background; color: $text; font-size: 14px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 30px; font-weight: 800; }
QLabel#muted { color: $muted; font-size: 13px; }
QLabel#eyebrow { color: $muted; font-size: 11px; font-weight: 600; }
QLabel#sectionLabel { font-size: 22px; font-weight: 700; }
QLabel#clipName { font-size: 17px; font-weight: 600; }
QLabel#metricValue { font-size: 17px; font-weight: 600; }
QLabel#status { font-size: 17px; font-weight: 600; }
QLabel#badge { background: $raised; color: $text; padding: 7px 10px;
               border-left: 3px solid $accent; font-size: 11px; font-weight: 600; }
QFrame#dropArea, QFrame#outputPanel { background: $surface;
                  border: 1px solid $border; border-radius: 4px; }
QFrame#actionBar { background: $surface; border: 1px solid $border; border-radius: 4px; }
QFrame#dropArea[dragActive="true"] { border: 2px solid $accent; }
QFrame#divider { background: $border; border: none; }
QFrame#accentLine { background: $accent; border: none; }
QFrame#metrics { background: transparent; border-top: 1px solid $border; }
QPushButton { background: $raised; border: 1px solid $border; border-radius: 3px;
              padding: 9px 14px; font-weight: 600; }
QPushButton:hover { border-color: $accent; }
QPushButton:pressed { background: $surface; }
QPushButton:disabled { color: $muted; border-color: $border; }
QPushButton#primary { background: $accent; color: $on_accent;
                      border-color: $accent; font-size: 14px; font-weight: 700;
                      padding: 14px 28px; }
QPushButton#primary:hover { background: $hover; border-color: $hover; }
QPushButton#primary:pressed { background: $accent; }
QPushButton#primary:disabled { background: $raised; color: $muted; border-color: $border; }
QPushButton#preset { background: $surface; border: 1px solid $border;
                     padding: 10px 5px; font-size: 12px; }
QPushButton#preset:hover { border-color: $accent; }
QPushButton#preset[selected="true"] { background: $accent; color: $on_accent;
                                       border-color: $accent; }
QPushButton:focus { border: 1px solid $accent; }
QPushButton#headerAction { padding: 8px 12px; }
QDoubleSpinBox { background: $background; color: $text; border: 1px solid $border;
                  border-radius: 3px; padding: 9px 12px; font-size: 32px; font-weight: 600; }
QDoubleSpinBox:focus { border-color: $accent; }
QDoubleSpinBox:disabled { color: $muted; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 26px; }
QProgressBar { background: $raised; border: none; border-radius: 0;
               min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: $accent; }
""")


@dataclass(frozen=True)
class Hardware:
    encoder: str
    description: str
    target_mb: float = DEFAULT_TARGET_MB


SOFTWARE = Hardware("libx265", "CPU via libx265 (two passes)")


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_audio: bool
    video_index: int
    width: int
    height: int


class Cancelled(Exception):
    """The user stopped a job."""


class ProcessError(RuntimeError):
    """A child process did not complete."""


def is_file_access_error(error: Exception | str) -> bool:
    text = str(error).casefold()
    if isinstance(error, PermissionError) or re.search(
        r"\[(?:winerror 5|errno (?:1|13))\]", text
    ):
        return True
    denied = "permission denied" in text or "access is denied" in text
    return denied and any(
        marker in text
        for marker in (
            "opening input",
            "opening output",
            "open file",
            "writing",
            "write header",
        )
    )


def encoding_device(hardware: Hardware) -> str:
    return {
        "hevc_nvenc": "NVIDIA GPU",
        "hevc_amf": "AMD GPU",
        "hevc_qsv": "Intel GPU",
        "hevc_videotoolbox": "Apple GPU",
    }.get(hardware.encoder, "CPU")


def version_parts(value: str) -> tuple[tuple[int, int, int], bool]:
    """Read SemVer without treating numeric versions as decimal numbers."""
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("Invalid app version.")
    match = re.fullmatch(
        r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        value,
    )
    if not match:
        raise ValueError("Invalid app version.")
    prerelease = match.group(4)
    if prerelease and any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in prerelease.split(".")
    ):
        raise ValueError("Invalid app version.")
    return tuple(int(match.group(index)) for index in (1, 2, 3)), bool(prerelease)


def get_app_version() -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    try:
        value = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        # main.py also works alone during development. Releases include VERSION.
        if getattr(sys, "frozen", False):
            raise RuntimeError("The app version is missing. Reinstall the app.")
        value = "0.0.2"
    version_parts(value)
    return value.removeprefix("v")


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str
    newer: bool


def parse_release(payload: bytes, current_version: str) -> ReleaseInfo:
    if len(payload) > MAX_UPDATE_BYTES:
        raise ValueError("Update information is too large.")
    data = json.loads(payload)
    if (
        not isinstance(data, dict)
        or data.get("draft") is not False
        or data.get("prerelease") is not False
    ):
        raise ValueError("A published release is required.")
    tag = data.get("tag_name")
    latest, prerelease = version_parts(tag)
    if prerelease:
        raise ValueError("A stable release is required.")
    installed, installed_prerelease = version_parts(current_version)
    # Build the link from a validated tag. Never open an API-supplied URL.
    release_url = (
        PROJECT_URL
        + "/releases/tag/"
        + bytes(QUrl.toPercentEncoding(tag)).decode("ascii")
    )
    return ReleaseInfo(
        tag.removeprefix("v"),
        release_url,
        latest > installed or (latest == installed and installed_prerelease),
    )


class UpdateChecker(QObject):
    """Use Qt's asynchronous HTTPS client and the platform's trusted certificates."""

    checked = pyqtSignal(object)
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.version = version
        self.manager = QNetworkAccessManager(self)
        self.reply: QNetworkReply | None = None
        self.buffer = bytearray()
        self.failure = ""
        self.cancelled = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.timed_out)

    def check(self):
        if self.reply is not None:
            return
        self.buffer.clear()
        self.failure = ""
        self.cancelled = False
        request = QNetworkRequest(QUrl(UPDATE_API_URL))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(
            b"User-Agent", f"DiscordVideoCompressor/{self.version}".encode("ascii")
        )
        request.setTransferTimeout(UPDATE_TIMEOUT_MS)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy,
        )
        self.reply = self.manager.get(request)
        self.reply.setReadBufferSize(MAX_UPDATE_BYTES + 1)
        self.reply.readyRead.connect(self.read_reply)
        self.reply.finished.connect(self.finished)
        self.timer.start(UPDATE_TIMEOUT_MS)
        self.busy_changed.emit(True)

    def read_reply(self):
        if (
            self.reply is not None
            and self.reply.isOpen()
            and self.reply.bytesAvailable() > 0
        ):
            chunk = self.reply.read(MAX_UPDATE_BYTES + 1 - len(self.buffer))
            if chunk:
                self.buffer.extend(chunk)
            if len(self.buffer) > MAX_UPDATE_BYTES:
                self.failure = (
                    "The update information could not be read. Try again later."
                )
                self.reply.abort()

    def timed_out(self):
        if self.reply is not None:
            self.failure = "The update check timed out. Try again later."
            self.reply.abort()

    def cancel(self):
        self.cancelled = True
        self.timer.stop()
        if self.reply is not None:
            self.reply.abort()

    def finished(self):
        self.timer.stop()
        self.read_reply()
        reply = self.reply
        if reply is None:
            return
        self.reply = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        reply.deleteLater()
        self.busy_changed.emit(False)
        if self.cancelled:
            return
        if self.failure:
            self.failed.emit(self.failure)
        elif status in (403, 429):
            self.failed.emit(
                "GitHub is busy. Try again later, or open the downloads page."
            )
        elif status == 404:
            self.failed.emit("No release is available yet. Try again later.")
        elif error != QNetworkReply.NetworkError.NoError or status != 200:
            self.failed.emit("Check your internet connection and try again.")
        else:
            try:
                release = parse_release(bytes(self.buffer), self.version)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                self.failed.emit(
                    "The update information could not be read. Try again later."
                )
            else:
                self.checked.emit(release)


def get_app_icon() -> QIcon:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return QIcon(str(root / "assets" / "app.png"))


def create_application(arguments: list[str]) -> QApplication:
    # Set the name before Qt creates the native application menu.
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_NAME)
    app = QApplication(arguments)
    app.setDesktopFileName(APP_ID)
    app.setApplicationVersion(get_app_version())
    app.setWindowIcon(get_app_icon())
    return app


def get_ffmpeg_path(binary_name: str) -> str:
    """Use packaged tools first. Use local tools or PATH during development."""
    if binary_name not in {"ffmpeg", "ffprobe"}:
        raise ValueError("Only ffmpeg and ffprobe are supported.")
    filename = binary_name + (".exe" if sys.platform == "win32" else "")
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots = [Path(bundle_root) / "binaries", Path(bundle_root)]
    else:
        project = Path(__file__).resolve().parent
        roots = [project / "binaries", project]
    for root in roots:
        candidate = root / filename
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    # A broken release must not silently use an unrelated system installation.
    if not getattr(sys, "frozen", False) and not bundle_root:
        found = shutil.which(filename)
        if found:
            return str(Path(found).resolve())
    raise FileNotFoundError(
        f"Cannot find {filename}. Place it in the binaries folder, "
        "or install FFmpeg on PATH for development."
    )


def child_environment() -> dict[str, str]:
    """Do not pass PyInstaller's library path to system tools or GPU drivers."""
    env = os.environ.copy()
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if getattr(sys, "frozen", False):
            original = env.pop(key + "_ORIG", None)
            if original is None:
                env.pop(key, None)
            else:
                env[key] = original
    return env


def process_options() -> dict:
    options = {"env": child_environment()}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    return options


@contextmanager
def system_dll_directory():
    """Let Windows children load system GPU drivers, then restore Qt's path."""
    root = getattr(sys, "_MEIPASS", None)
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or not root:
        yield
        return
    with _DLL_DIRECTORY_LOCK:
        setter = ctypes.WinDLL("kernel32", use_last_error=True).SetDllDirectoryW
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_int
        if not setter(None):
            raise ctypes.WinError()
        try:
            yield
        finally:
            if not setter(str(root)):
                raise ctypes.WinError()


def output_limit_bytes(target_mb: float) -> int:
    if not math.isfinite(target_mb) or not MIN_TARGET_MB <= target_mb <= MAX_TARGET_MB:
        raise ValueError("Choose a file size from 1 to 500 MB.")
    return math.floor(target_mb * 1_000_000)


def encoder_target_bytes(hardware: Hardware, target_mb: float) -> int:
    """Keep the AMD/Intel safety margin proportional to the selected limit."""
    limit = output_limit_bytes(target_mb)
    return min(limit, math.floor(limit * hardware.target_mb / DEFAULT_TARGET_MB))


def calculate_video_bitrate(
    info: MediaInfo,
    hardware: Hardware,
    target_mb: float = DEFAULT_TARGET_MB,
) -> int:
    """Keep the requested formula, then cap it to the actual byte budget."""
    if not math.isfinite(info.duration) or info.duration <= 0:
        raise ValueError("The video duration must be greater than zero.")
    byte_limit = encoder_target_bytes(hardware, target_mb)
    requested_total = (byte_limit / 1_000_000) * 8192 * 1000 / info.duration
    # Reserve 1.5% or 64 KiB for MP4 tables and AAC rate variation.
    overhead = max(65_536, int(byte_limit * 0.015))
    safe_total = (byte_limit - overhead) * 8 / info.duration
    audio_rate = AUDIO_BITRATE if info.has_audio else 0
    bitrate = math.floor(min(requested_total, safe_total) - audio_rate)
    if bitrate < MIN_VIDEO_BITRATE:
        raise ValueError(
            "This clip needs a larger file size. Increase the limit or choose a shorter clip."
        )
    return bitrate


def parse_media_info(payload: str) -> MediaInfo:
    data = json.loads(payload)
    streams = data.get("streams", [])
    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video"
        and not s.get("disposition", {}).get("attached_pic")
    ]
    if not videos:
        raise ValueError("The input file has no video stream.")
    video = videos[0]
    durations = [video.get("duration"), data.get("format", {}).get("duration")]
    duration = None
    for value in durations:
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds > 0:
                duration = seconds
                break
        except (TypeError, ValueError):
            continue
    if duration is None:
        raise ValueError("FFprobe could not read a valid video duration.")
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if width < 2 or height < 2:
        raise ValueError("The input video dimensions are not valid.")
    return MediaInfo(
        duration,
        any(s.get("codec_type") == "audio" for s in streams),
        int(video["index"]),
        width,
        height,
    )


def encoder_arguments(hardware: Hardware, bitrate: int) -> list[str]:
    args = ["-c:v", hardware.encoder, "-b:v", str(bitrate)]
    if hardware.encoder == "libx265":
        return args + ["-preset", "slow", "-pix_fmt", "yuv420p"]
    args += ["-maxrate", str(bitrate), "-bufsize", str(bitrate * 2)]
    if hardware.encoder == "hevc_videotoolbox":
        args += ["-allow_sw", "0", "-realtime", "0", "-pix_fmt", "yuv420p"]
    elif hardware.encoder == "hevc_nvenc":
        args += ["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-pix_fmt", "yuv420p"]
    elif hardware.encoder == "hevc_amf":
        args += ["-quality", "quality", "-rc", "vbr_peak", "-pix_fmt", "nv12"]
    elif hardware.encoder == "hevc_qsv":
        args += ["-preset", "slow", "-pix_fmt", "nv12"]
    return args


def encoding_commands(
    ffmpeg: str,
    source: Path,
    output: Path,
    info: MediaInfo,
    hardware: Hardware,
    bitrate: int,
) -> list[list[str]]:
    common = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-nostats",
        "-loglevel",
        "info",
        "-progress",
        "pipe:1",
        "-i",
        str(source),
        "-map",
        f"0:{info.video_index}",
        "-t",
        f"{info.duration:.9f}",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    ]
    video = encoder_arguments(hardware, bitrate)
    audio = (
        [
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            str(AUDIO_BITRATE),
            "-ac",
            "2",
            "-ar",
            "48000",
        ]
        if info.has_audio
        else ["-an"]
    )
    container = [
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-sn",
        "-dn",
        "-tag:v",
        "hvc1",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(output),
    ]
    if hardware.encoder != "libx265":
        return [common + video + audio + container]
    # Use a relative stats name and a private working directory. Windows drive
    # letters and colons in filenames cannot break x265's parameter parser.
    first = (
        common
        + video
        + ["-pass", "1", "-passlogfile", "x265-pass", "-an", "-f", "null", os.devnull]
    )
    second = (
        common + video + ["-pass", "2", "-passlogfile", "x265-pass"] + audio + container
    )
    return [first, second]


class ProcessThread(QThread):
    log = pyqtSignal(str)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen | None = None

    def check_cancelled(self):
        if self._cancel_requested.is_set() or self.isInterruptionRequested():
            raise Cancelled()

    def cancel(self):
        self._cancel_requested.set()
        self.requestInterruption()
        with self._lock:
            if self._process and self._process.poll() is None:
                # The output is private until verified. Killing it is safe and
                # avoids blocking the GUI while a pipe read waits for output.
                try:
                    self._process.kill()
                except OSError:
                    pass

    def run_process(self, args, *, cwd=None, timeout=None, on_line=None) -> str:
        self.check_cancelled()
        with self._lock:
            self.check_cancelled()
            proc = None
            try:
                with system_dll_directory():
                    proc = subprocess.Popen(
                        args,
                        cwd=cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        **process_options(),
                    )
                    self._process = proc
            except BaseException:
                if proc is not None:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                    if proc.stdout:
                        proc.stdout.close()
                self._process = None
                raise
        timer = None
        timed_out = threading.Event()

        def stop_on_timeout():
            timed_out.set()
            try:
                proc.kill()
            except OSError:
                pass

        if timeout:
            timer = threading.Timer(timeout, stop_on_timeout)
            timer.daemon = True
            timer.start()
        captured: list[str] = []
        tail: deque[str] = deque(maxlen=15)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.check_cancelled()
                line = line.rstrip("\r\n")
                tail.append(line)
                if on_line:
                    on_line(line)
                else:
                    captured.append(line)
            code = proc.wait()
            self.check_cancelled()
            if timed_out.is_set():
                raise ProcessError(f"{Path(args[0]).name} exceeded its time limit.")
            if code:
                raise ProcessError(
                    f"{Path(args[0]).name} exited with code {code}:\n" + "\n".join(tail)
                )
            return "\n".join(captured)
        finally:
            if timer:
                timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout:
                proc.stdout.close()
            with self._lock:
                self._process = None

    def probe(self, source: Path, ffprobe: str) -> MediaInfo:
        payload = self.run_process(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(source),
            ],
            timeout=45,
        )
        return parse_media_info(payload)


def hardware_candidates(worker: ProcessThread) -> list[Hardware]:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin":
        silicon = machine in {"arm64", "aarch64"}
        if not silicon:
            try:
                silicon = (
                    worker.run_process(
                        ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
                        timeout=5,
                    ).strip()
                    == "1"
                )
            except (OSError, ProcessError):
                pass
        return (
            [Hardware("hevc_videotoolbox", "Apple Silicon GPU via VideoToolbox")]
            if silicon
            else []
        )

    if system == "Windows":
        # The driver encode checks below identify usable GPUs directly. This
        # works even when PowerShell, WMI, or nvidia-smi are unavailable.
        return [
            Hardware("hevc_nvenc", "NVIDIA GPU via NVENC"),
            Hardware("hevc_amf", "AMD GPU via AMF", BUFFERED_TARGET_MB),
            Hardware("hevc_qsv", "Intel GPU via Quick Sync", BUFFERED_TARGET_MB),
        ]
    vendors: set[str] = set()
    if system == "Linux":
        vendor_ids = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
        for device in Path("/sys/bus/pci/devices").glob("*"):
            try:
                if (device / "class").read_text().strip().startswith("0x03"):
                    vendor = vendor_ids.get((device / "vendor").read_text().strip())
                    if vendor:
                        vendors.add(vendor)
            except OSError:
                continue
    if system in {"Windows", "Linux"} and "nvidia" not in vendors:
        smi = shutil.which("nvidia-smi")
        if smi:
            try:
                if worker.run_process(
                    [smi, "--query-gpu=name", "--format=csv,noheader"], timeout=8
                ).strip():
                    vendors.add("nvidia")
            except (OSError, ProcessError):
                pass
    candidates = []
    if "nvidia" in vendors:
        candidates.append(Hardware("hevc_nvenc", "NVIDIA GPU via NVENC"))
    if system == "Windows" and "amd" in vendors:
        candidates.append(Hardware("hevc_amf", "AMD GPU via AMF", BUFFERED_TARGET_MB))
    if "intel" in vendors:
        candidates.append(
            Hardware("hevc_qsv", "Intel GPU via Quick Sync", BUFFERED_TARGET_MB)
        )
    return candidates


class DetectionWorker(ProcessThread):
    detected = pyqtSignal(object)
    notice = pyqtSignal(str)

    def run(self):
        try:
            ffmpeg = get_ffmpeg_path("ffmpeg")
            get_ffmpeg_path("ffprobe")
            listing = self.run_process(
                [ffmpeg, "-hide_banner", "-encoders"], timeout=15
            )
            available = {
                m.group(1)
                for m in re.finditer(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", listing, re.M)
            }
            for candidate in hardware_candidates(self):
                if candidate.encoder not in available:
                    self.log.emit(f"{candidate.encoder} is not in this FFmpeg build.")
                    continue
                try:
                    self.run_process(
                        [
                            ffmpeg,
                            "-hide_banner",
                            "-nostdin",
                            "-loglevel",
                            "error",
                            "-f",
                            "lavfi",
                            "-i",
                            "color=size=1280x720:rate=30",
                            "-frames:v",
                            "4",
                        ]
                        + encoder_arguments(candidate, 500_000)
                        + ["-an", "-f", "null", os.devnull],
                        timeout=20,
                    )
                    self.detected.emit(candidate)
                    return
                except (OSError, ProcessError) as exc:
                    if is_file_access_error(exc):
                        raise
                    self.log.emit(f"{candidate.description} is not available: {exc}")
                    if candidate.encoder == "hevc_nvenc" and any(
                        text in str(exc).casefold()
                        for text in (
                            "required nvenc api",
                            "minimum required nvidia driver",
                        )
                    ):
                        self.notice.emit(
                            "NVIDIA video encoding needs a newer driver. "
                            "Update your NVIDIA driver, then restart the app."
                        )
            if "libx265" not in available:
                raise RuntimeError(
                    "FFmpeg has no working hardware encoder and no libx265 encoder."
                )
            self.detected.emit(SOFTWARE)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class ProbeWorker(ProcessThread):
    inspected = pyqtSignal(object)
    preview = pyqtSignal(bytes)

    def __init__(self, source: Path, parent=None):
        super().__init__(parent)
        self.source = source

    def run(self):
        try:
            info = self.probe(self.source, get_ffmpeg_path("ffprobe"))
            try:
                with tempfile.TemporaryDirectory(prefix="clip-preview-") as folder:
                    preview = Path(folder) / "preview.jpg"
                    self.run_process(
                        [
                            get_ffmpeg_path("ffmpeg"),
                            "-hide_banner",
                            "-nostdin",
                            "-y",
                            "-v",
                            "error",
                            "-ss",
                            str(min(2.0, info.duration / 3)),
                            "-i",
                            str(self.source),
                            "-map",
                            f"0:{info.video_index}",
                            "-frames:v",
                            "1",
                            "-an",
                            "-vf",
                            "scale=960:540:force_original_aspect_ratio=decrease,"
                            "pad=960:540:(ow-iw)/2:(oh-ih)/2:color=0x141414",
                            "-q:v",
                            "3",
                            str(preview),
                        ],
                        timeout=15,
                    )
                    if preview.is_file():
                        self.preview.emit(preview.read_bytes())
            except (OSError, ProcessError) as exc:
                if is_file_access_error(exc):
                    raise
                self.log.emit(f"Preview unavailable: {exc}")
            self.inspected.emit(info)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class CompressionWorker(ProcessThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    hardware_changed = pyqtSignal(object)
    completed = pyqtSignal(str, int)

    def __init__(
        self,
        source: Path,
        output: Path,
        hardware: Hardware,
        parent=None,
        *,
        target_mb: float = DEFAULT_TARGET_MB,
    ):
        super().__init__(parent)
        self.source = source.resolve()
        self.output = output.resolve()
        self.hardware = hardware
        self.target_mb = target_mb

    def compress(self) -> tuple[Path, int]:
        ffmpeg, ffprobe = get_ffmpeg_path("ffmpeg"), get_ffmpeg_path("ffprobe")
        if not self.source.is_file():
            raise ValueError("The input file does not exist.")
        if self.source.resolve() == self.output.resolve():
            raise ValueError("The output path must differ from the input path.")
        if self.output.exists():
            raise FileExistsError("The output file exists. Select a new name.")
        info = self.probe(self.source, ffprobe)
        hardware = self.hardware
        bitrate = calculate_video_bitrate(info, hardware, self.target_mb)
        with tempfile.TemporaryDirectory(
            prefix=".discord-compressor-", dir=self.output.parent
        ) as folder:
            workdir = Path(folder)
            encoded = workdir / "encoded.mp4"
            for attempt in range(1, MAX_ATTEMPTS + 1):
                self.check_cancelled()
                self.status.emit(
                    f"Compressing with {encoding_device(hardware)}…"
                    if attempt == 1
                    else f"Adjusting file size with {encoding_device(hardware)}…"
                )
                self.log.emit(
                    f"Attempt {attempt} of {MAX_ATTEMPTS}; selected limit {self.target_mb:g} MB"
                )
                self.log.emit(
                    f"{hardware.description}; video {bitrate / 1000:.0f} kb/s; "
                    f"audio {128 if info.has_audio else 0} kb/s"
                )
                commands = encoding_commands(
                    ffmpeg, self.source, encoded, info, hardware, bitrate
                )
                try:
                    for pass_index, command in enumerate(commands):
                        self.log.emit(f"Pass {pass_index + 1} of {len(commands)}")
                        self.log.emit(
                            subprocess.list2cmdline(command)
                            if sys.platform == "win32"
                            else shlex.join(command)
                        )

                        def receive_line(line):
                            key, _, value = line.partition("=")
                            if key == "out_time_us":
                                try:
                                    fraction = min(
                                        1.0,
                                        max(
                                            0.0, int(value) / 1_000_000 / info.duration
                                        ),
                                    )
                                    self.progress.emit(
                                        min(
                                            99,
                                            int(
                                                (pass_index + fraction)
                                                / len(commands)
                                                * 99
                                            ),
                                        )
                                    )
                                except ValueError:
                                    pass
                            # Keep FFmpeg output available to diagnostic signal handlers.
                            self.log.emit(line)

                        self.run_process(command, cwd=workdir, on_line=receive_line)
                except ProcessError as exc:
                    if hardware.encoder == "libx265" or is_file_access_error(exc):
                        raise
                    self.log.emit(
                        "Hardware encoding failed. Retrying with software encoding."
                    )
                    hardware = SOFTWARE
                    self.hardware_changed.emit(hardware)
                    bitrate = calculate_video_bitrate(info, hardware, self.target_mb)
                    continue
                self.check_cancelled()
                self.status.emit("Checking file size…")
                size = encoded.stat().st_size
                verified = self.probe(encoded, ffprobe)
                if abs(verified.duration - info.duration) > max(
                    0.5, info.duration * 0.001
                ):
                    raise RuntimeError(
                        "The output duration differs from the input. No file was saved."
                    )
                if size <= 0:
                    raise RuntimeError("FFmpeg produced an empty file.")
                target = encoder_target_bytes(hardware, self.target_mb)
                self.log.emit(f"Output size: {size:,} bytes; limit: {target:,} bytes.")
                if size <= target:
                    self.check_cancelled()
                    # Exclusive creation prevents accidental overwrite, including
                    # files created by another process while FFmpeg was running.
                    with self.output.open("xb") as destination:
                        try:
                            with encoded.open("rb") as source:
                                shutil.copyfileobj(source, destination)
                            self.check_cancelled()
                        except BaseException:
                            destination.close()
                            self.output.unlink(missing_ok=True)
                            raise
                    self.progress.emit(100)
                    return self.output, size
                # Correct only the video budget; keep the fixed AAC budget.
                audio_bytes = (
                    (AUDIO_BITRATE * info.duration / 8) if info.has_audio else 0
                )
                overhead = max(65_536, int(target * 0.015))
                ratio = (target - audio_bytes - overhead) / max(
                    1, size - audio_bytes - overhead
                )
                reduced = math.floor(bitrate * min(0.90, max(0.1, ratio * 0.95)))
                if reduced < MIN_VIDEO_BITRATE:
                    raise RuntimeError(
                        "The full video cannot fit at the minimum video bitrate."
                    )
                bitrate = reduced
                self.log.emit(
                    "The output exceeds the size limit. Retrying at a lower bitrate."
                )
            raise RuntimeError(
                "The encoder could not meet the size limit after four attempts. No file was saved."
            )

    def run(self):
        try:
            path, size = self.compress()
            self.completed.emit(str(path), size)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class RevealWorker(ProcessThread):
    """Select the saved file without blocking the GUI on a file manager."""

    revealed = pyqtSignal(bool)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path

    @staticmethod
    def reveal_windows(path: Path):
        # Use the Shell API so spaces, commas, and Unicode need no quoting.
        import ctypes
        from ctypes import wintypes

        shell = ctypes.WinDLL("shell32")
        ole = ctypes.WinDLL("ole32")
        ole.CoInitialize.argtypes = [ctypes.c_void_p]
        ole.CoInitialize.restype = ctypes.c_long
        ole.CoUninitialize.argtypes = []
        ole.CoUninitialize.restype = None
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree.restype = None
        shell.SHParseDisplayName.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        shell.SHParseDisplayName.restype = ctypes.c_long
        shell.SHOpenFolderAndSelectItems.argtypes = [
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        shell.SHOpenFolderAndSelectItems.restype = ctypes.c_long
        result = ole.CoInitialize(None)
        if result < 0:
            raise OSError("Could not open the file manager.")
        item = ctypes.c_void_p()
        try:
            result = shell.SHParseDisplayName(
                str(path), None, ctypes.byref(item), 0, None
            )
            if result >= 0:
                result = shell.SHOpenFolderAndSelectItems(item, 0, None, 0)
            if result < 0:
                raise OSError("Could not select the saved file.")
        finally:
            if item:
                ole.CoTaskMemFree(item)
            ole.CoUninitialize()

    def run(self):
        try:
            self.check_cancelled()
            if not self.path.is_file():
                raise FileNotFoundError("The saved file was moved or removed.")
            if sys.platform == "darwin":
                self.run_process(["/usr/bin/open", "-R", str(self.path)], timeout=10)
            elif sys.platform == "win32":
                self.reveal_windows(self.path)
            else:
                command = shutil.which("dbus-send")
                if not command:
                    self.revealed.emit(False)
                    return
                try:
                    self.run_process(
                        [
                            command,
                            "--session",
                            "--print-reply",
                            "--reply-timeout=3000",
                            "--dest=org.freedesktop.FileManager1",
                            "/org/freedesktop/FileManager1",
                            "org.freedesktop.FileManager1.ShowItems",
                            "array:string:" + self.path.as_uri(),
                            "string:",
                        ],
                        timeout=5,
                    )
                except ProcessError:
                    self.revealed.emit(False)
                    return
            self.check_cancelled()
            self.revealed.emit(True)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))


def ui_label(text: str, name: str = "muted") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def format_size(size: int) -> str:
    return (
        f"{size / 1_000_000:.2f} MB" if size >= 1_000_000 else f"{size / 1000:.0f} KB"
    )


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{max(0, seconds):.1f} s"
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes} min {seconds:02d} s"


class ThemeSwitch(QAbstractButton):
    """A keyboard-accessible switch for dark mode."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(50, 30)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Dark mode")
        self.setToolTip("Switch between light and dark")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            palette.color(
                QPalette.ColorRole.Highlight
                if self.isChecked()
                else QPalette.ColorRole.Mid
            )
        )
        painter.drawRoundedRect(QRectF(2, 4, 46, 22), 11, 11)
        painter.setBrush(
            palette.color(
                QPalette.ColorRole.HighlightedText
                if self.isChecked()
                else QPalette.ColorRole.Base
            )
        )
        painter.drawEllipse(QRectF(29 if self.isChecked() else 5, 7, 16, 16))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Highlight), 1))
            painter.drawRoundedRect(QRectF(0.5, 1, 49, 28), 14, 14)


class ClipPreview(QAbstractButton):
    """A clickable video still. Qt manages its hit area after each resize."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Choose a video")
        self.setToolTip("Click to choose a video, or drop a file here")
        self.image = QPixmap()

    def set_image(self, image: QPixmap):
        self.image = image
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        width, height = self.width(), self.height()
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        clip = QPainterPath()
        clip.moveTo(14, 0)
        for point in (
            (width, 0),
            (width, height - 14),
            (width - 14, height),
            (0, height),
            (0, 14),
        ):
            clip.lineTo(*point)
        clip.closeSubpath()
        painter.setClipPath(clip)
        if not self.image.isNull():
            painter.fillRect(self.rect(), QColor("#10151e"))
            size = self.image.size().scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio
            )
            target = QRectF(
                (width - size.width()) / 2,
                (height - size.height()) / 2,
                size.width(),
                size.height(),
            )
            painter.drawPixmap(target, self.image, QRectF(self.image.rect()))
        else:
            painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Base))
            tint = QColor(accent)
            tint.setAlpha(18)
            gradient = QLinearGradient(0, height, width, 0)
            gradient.setColorAt(0, self.palette().color(QPalette.ColorRole.Base))
            gradient.setColorAt(1, tint)
            painter.fillRect(self.rect(), gradient)
            line_color = self.palette().color(QPalette.ColorRole.Text)
            line_color.setAlpha(10)
            painter.setPen(QPen(line_color, 1))
            for x in range(-height, width, 44):
                painter.drawLine(QPointF(x, height), QPointF(x + height, 0))
            center = QPointF(width / 2, height / 2 - 40)
            painter.setPen(QPen(accent, 2))
            icon = QRectF(center.x() - 30, center.y() - 20, 60, 40)
            painter.drawRoundedRect(icon, 3, 3)
            play = QPainterPath()
            play.moveTo(center.x() - 6, center.y() - 9)
            play.lineTo(center.x() + 10, center.y())
            play.lineTo(center.x() - 6, center.y() + 9)
            play.closeSubpath()
            painter.fillPath(play, accent)
            font = QFont(self.font())
            font.setPixelSize(22)
            font.setWeight(QFont.Weight.Bold)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
            painter.setFont(font)
            painter.setPen(self.palette().color(QPalette.ColorRole.Text))
            painter.drawText(
                QRectF(12, height / 2, width - 24, 36),
                Qt.AlignmentFlag.AlignCenter,
                "Choose a video",
            )
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.Normal)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0)
            painter.setFont(font)
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(
                QRectF(16, height / 2 + 40, width - 32, 28),
                Qt.AlignmentFlag.AlignCenter,
                "Click here or drop a video file",
            )
        painter.setClipping(False)
        painter.setPen(QPen(accent, 2))
        for x, y, direction in ((18, 18, 1), (width - 18, height - 18, -1)):
            painter.drawLine(x, y, x + direction * 18, y)
            painter.drawLine(x, y, x, y + direction * 18)
        if self.hasFocus():
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


class DropArea(QFrame):
    selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("dropArea")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pressed = False
        self.source = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        layout.addWidget(ui_label("INPUT VIDEO", "eyebrow"))
        self.thumbnail = ClipPreview()
        self.thumbnail.clicked.connect(self.select_file)
        layout.addWidget(self.thumbnail, stretch=1)
        self.label = ui_label("No video selected", "clipName")
        self.label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.label)
        metrics = QFrame()
        metrics.setObjectName("metrics")
        metric_row = QHBoxLayout(metrics)
        metric_row.setContentsMargins(0, 12, 0, 0)
        self.metric_values = {}
        for name in ("DURATION", "RESOLUTION", "FILE SIZE"):
            column = QVBoxLayout()
            column.setSpacing(5)
            column.addWidget(ui_label(name, "eyebrow"))
            value = ui_label("—", "metricValue")
            value.setWordWrap(False)
            column.addWidget(value)
            metric_row.addLayout(column, stretch=1)
            self.metric_values[name] = value
        layout.addWidget(metrics)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mousePressEvent(self, event):
        self._pressed = event.button() == Qt.MouseButton.LeftButton
        if self._pressed:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pressed, self._pressed = self._pressed, False
        if pressed and event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.position().toPoint()):
                self.select_file()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.select_file()
            event.accept()
        else:
            super().keyPressEvent(event)

    def show_source(self, source: Path):
        self.source = source
        self.label.setText(source.name)
        self.label.setToolTip(str(source))
        self.thumbnail.set_image(QPixmap())
        for value in self.metric_values.values():
            value.setText("—")

    def set_preview(self, data: bytes):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.thumbnail.set_image(pixmap)

    def set_metadata(self, info: MediaInfo, size: int):
        minutes, seconds = divmod(round(info.duration), 60)
        self.metric_values["DURATION"].setText(f"{minutes}:{seconds:02d}")
        self.metric_values["RESOLUTION"].setText(f"{info.width} × {info.height}")
        self.metric_values["FILE SIZE"].setText(format_size(size) if size else "—")

    def select_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a video",
            "",
            "Video (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mts);;All files (*)",
        )
        if filename:
            self.selected.emit(filename)

    def set_drag_active(self, active: bool):
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent):
        urls = event.mimeData().urls()
        if (
            self.isEnabled()
            and len(urls) == 1
            and urls[0].isLocalFile()
            and Path(urls[0].toLocalFile()).is_file()
        ):
            self.set_drag_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.set_drag_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent):
        self.set_drag_active(False)
        urls = event.mimeData().urls()
        if self.isEnabled() and len(urls) == 1 and urls[0].isLocalFile():
            self.selected.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.version = get_app_version()
        self.updates = UpdateChecker(self.version, self)
        self.updates.checked.connect(self.show_update_result)
        self.updates.failed.connect(self.show_update_error)
        self.updates.busy_changed.connect(self.update_check_state)
        self.resize(1060, 740)
        self.setMinimumSize(900, 720)
        self.settings = (
            settings
            if settings is not None
            else QSettings("DiscordVideoCompressor", "DiscordVideoCompressor")
        )
        self.source: Path | None = None
        self.info: MediaInfo | None = None
        self.hardware: Hardware | None = None
        self.gpu_notice = ""
        self.output: Path | None = None
        self.worker: ProcessThread | None = None
        self._closing = False
        self._can_fit = False
        self._started_at: float | None = None
        self._elapsed_seconds = 0.0
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(250)
        self.elapsed_timer.timeout.connect(self.update_elapsed)
        dark_mode = self.settings.value("dark_mode", False, type=bool)
        self.theme_name = "ink" if dark_mode else "frost"
        apply_theme(QApplication.instance(), self.theme_name)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(18)
        marker = QFrame()
        marker.setObjectName("accentLine")
        marker.setFixedSize(4, 48)
        header.addWidget(marker)
        branding = QVBoxLayout()
        branding.setSpacing(3)
        wordmark = ui_label("Video compressor", "title")
        font = wordmark.font()
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        wordmark.setFont(font)
        branding.addWidget(wordmark)
        branding.addWidget(ui_label("FOR DISCORD", "eyebrow"))
        header.addLayout(branding)
        header.addStretch(1)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_row.addWidget(ui_label("Light"))
        self.theme_switch = ThemeSwitch()
        self.theme_switch.setChecked(dark_mode)
        self.theme_switch.toggled.connect(self.change_theme)
        theme_row.addWidget(self.theme_switch)
        theme_row.addWidget(ui_label("Dark"))
        header.addLayout(theme_row)
        self.about_action = QAction("About Discord Video Compressor", self)
        self.about_action.setMenuRole(QAction.MenuRole.AboutRole)
        self.about_action.triggered.connect(self.show_about)
        self.update_action = QAction("Check for updates…", self)
        self.update_action.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        self.update_action.triggered.connect(self.updates.check)
        if sys.platform == "darwin" and QApplication.platformName() == "cocoa":
            native_help = self.menuBar().addMenu("Help")
            native_help.addActions([self.about_action, self.update_action])
        self.about_button = QPushButton("About")
        self.about_button.setObjectName("headerAction")
        self.about_button.clicked.connect(self.about_action.trigger)
        header.addWidget(self.about_button)
        self.update_button = QPushButton("Check for updates")
        self.update_button.setObjectName("headerAction")
        self.update_button.clicked.connect(self.update_action.trigger)
        header.addWidget(self.update_button)
        layout.addLayout(header)

        workspace = QHBoxLayout()
        workspace.setSpacing(18)
        self.drop = DropArea()
        self.drop.selected.connect(self.load_video)
        workspace.addWidget(self.drop, stretch=1)

        output_panel = QFrame()
        output_panel.setObjectName("outputPanel")
        output_panel.setFixedWidth(320)
        output_layout = QVBoxLayout(output_panel)
        output_layout.setContentsMargins(20, 18, 20, 18)
        output_layout.setSpacing(16)
        output_layout.addWidget(ui_label("EXPORT", "eyebrow"))
        output_layout.addWidget(ui_label("Output settings", "sectionLabel"))
        accent_line = QFrame()
        accent_line.setObjectName("accentLine")
        accent_line.setFixedSize(36, 3)
        output_layout.addWidget(accent_line)
        size_column = QVBoxLayout()
        size_column.setSpacing(10)
        size_label = ui_label("MAXIMUM FILE SIZE", "eyebrow")
        size_column.addWidget(size_label)
        self.target_size = QDoubleSpinBox()
        self.target_size.setAccessibleName("Maximum output file size in MB")
        self.target_size.setDecimals(1)
        self.target_size.setRange(MIN_TARGET_MB, MAX_TARGET_MB)
        self.target_size.setSingleStep(0.5)
        self.target_size.setSuffix(" MB")
        self.target_size.setKeyboardTracking(False)
        self.target_size.setMinimumHeight(66)
        size_label.setBuddy(self.target_size)
        try:
            saved_size = float(self.settings.value("target_mb", DEFAULT_TARGET_MB))
            output_limit_bytes(saved_size)
            previous_default = float(self.settings.value("default_target_mb", 9.5))
            if abs(saved_size - previous_default) < 0.01:
                saved_size = DEFAULT_TARGET_MB
        except (TypeError, ValueError):
            saved_size = DEFAULT_TARGET_MB
        self.settings.setValue("default_target_mb", DEFAULT_TARGET_MB)
        self.settings.setValue("target_mb", saved_size)
        self.target_size.setValue(saved_size)
        size_column.addWidget(self.target_size)
        presets = QHBoxLayout()
        presets.setSpacing(8)
        self.preset_buttons = {}
        for size in (DEFAULT_TARGET_MB, 25.0, 50.0):
            button = QPushButton(f"{size:g} MB")
            button.setObjectName("preset")
            button.setAccessibleName(f"Set maximum size to {size:g} MB")
            button.clicked.connect(lambda checked, size=size: self.select_preset(size))
            presets.addWidget(button)
            self.preset_buttons[size] = button
        size_column.addLayout(presets)
        output_layout.addLayout(size_column)
        output_layout.addWidget(ui_label("Custom sizes from 1 to 500 MB."))
        output_layout.addStretch(1)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        output_layout.addWidget(divider)
        explanation = QVBoxLayout()
        explanation.setSpacing(8)
        explanation.addWidget(ui_label("VIDEO QUALITY", "eyebrow"))
        explanation.addWidget(
            ui_label("A larger file size keeps more detail in fast gameplay.")
        )
        self.size_hint = ui_label("", "badge")
        explanation.addWidget(self.size_hint)
        output_layout.addLayout(explanation)
        workspace.addWidget(output_panel)
        layout.addLayout(workspace, stretch=1)

        action_panel = QFrame()
        action_panel.setObjectName("actionBar")
        action_row = QHBoxLayout(action_panel)
        action_row.setContentsMargins(20, 16, 20, 16)
        action_row.setSpacing(12)
        status = QVBoxLayout()
        status.setSpacing(4)
        self.status_label = ui_label("Preparing…", "status")
        self.status_hint = ui_label("Preparing video tools.")
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.status_hint.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        status.addWidget(self.status_label)
        status.addWidget(self.status_hint)
        action_row.addLayout(status, stretch=1)
        progress_info = QVBoxLayout()
        progress_info.setSpacing(4)
        self.percentage = ui_label("0%")
        self.percentage.setWordWrap(False)
        self.elapsed_label = ui_label("Elapsed 0.0 s")
        self.elapsed_label.setWordWrap(False)
        progress_info.addWidget(self.percentage)
        progress_info.addWidget(self.elapsed_label)
        action_row.addLayout(progress_info)
        self.open_button = QPushButton("Show in folder")
        self.open_button.clicked.connect(self.open_output)
        action_row.addWidget(self.open_button)
        self.start_button = QPushButton("Compress video")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_compression)
        action_row.addWidget(self.start_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumWidth(120)
        self.cancel_button.clicked.connect(self.cancel_job)
        action_row.addWidget(self.cancel_button)
        layout.addWidget(action_panel)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        self.progress.valueChanged.connect(
            lambda value: self.percentage.setText(f"{value}%")
        )
        layout.addWidget(self.progress)

        self.target_size.valueChanged.connect(self.target_changed)
        self.update_size_hint()
        self.update_controls()
        worker = DetectionWorker(self)
        worker.detected.connect(self.set_hardware)
        worker.notice.connect(self.set_gpu_notice)
        self.launch(worker)

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setIconPixmap(get_app_icon().pixmap(64, 64))
        box.setText(APP_NAME)
        box.setInformativeText(
            f"Version {self.version}\n\n"
            "Compress video clips to the file size you choose.\n"
            "Your clips stay on your computer.\n\n"
            f"{AFFILIATION_NOTICE}\n\nGPL-3.0 license"
        )
        website = box.addButton("Website", QMessageBox.ButtonRole.ActionRole)
        license_button = box.addButton("License", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is website:
            QDesktopServices.openUrl(QUrl(WEBSITE_URL))
        elif box.clickedButton() is license_button:
            QDesktopServices.openUrl(QUrl(PROJECT_URL + "/blob/main/LICENSE"))

    def update_check_state(self, busy: bool):
        self.update_action.setEnabled(not busy)
        self.update_action.setText(
            "Checking for updates…" if busy else "Check for updates…"
        )
        self.update_button.setEnabled(not busy)
        self.update_button.setText(
            "Checking for updates…" if busy else "Check for updates"
        )

    def show_update_result(self, release: ReleaseInfo):
        if self._closing:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Check for updates")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"Version {release.version} is available"
            if release.newer
            else "No update available"
        )
        box.setInformativeText(
            f"Installed: {self.version}\nLatest release: {release.version}"
        )
        download = None
        if release.newer:
            download = box.addButton(
                "Download update", QMessageBox.ButtonRole.AcceptRole
            )
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if download is not None and box.clickedButton() is download:
            QDesktopServices.openUrl(QUrl(release.url))

    def show_update_error(self, message: str):
        if self._closing:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Check for updates")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Could not check for updates")
        box.setInformativeText(message)
        downloads = box.addButton("Open downloads", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is downloads:
            QDesktopServices.openUrl(QUrl(RELEASES_URL))

    def change_theme(self, dark_mode: bool):
        self.theme_name = "ink" if dark_mode else "frost"
        self.settings.setValue("dark_mode", dark_mode)
        apply_theme(QApplication.instance(), self.theme_name)
        self.drop.thumbnail.update()

    def update_size_hint(self):
        value = self.target_size.value()
        for size, button in self.preset_buttons.items():
            selected = abs(value - size) < 0.01
            if button.property("selected") != selected:
                button.setProperty("selected", selected)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()
        self.size_hint.setText(
            f"Discord default: {DEFAULT_TARGET_MB:g} MB"
            if value == DEFAULT_TARGET_MB
            else f"File size limit: {value:g} MB"
        )

    def select_preset(self, size: float):
        self.target_size.setValue(size)
        self.update_size_hint()

    def target_changed(self, value: float):
        self.settings.setValue("target_mb", value)
        self.update_size_hint()
        self.output = None
        self.progress.setValue(0)
        self.refresh_ready_state()
        self.update_controls()

    def refresh_ready_state(self):
        self._can_fit = False
        if self.info is not None and self.hardware is not None:
            try:
                calculate_video_bitrate(
                    self.info, self.hardware, self.target_size.value()
                )
                self._can_fit = True
            except ValueError as exc:
                self.status_label.setText("Increase the size limit")
                self.status_hint.setText(str(exc))
                return
            if not isinstance(self.worker, CompressionWorker):
                self.status_label.setText("Ready to compress")
                self.status_hint.setText(
                    self.gpu_notice
                    or f"Save the full clip under {self.target_size.value():g} MB."
                )
        elif self.worker is None:
            self.status_label.setText("Choose a video to start")
            self.status_hint.setText(
                self.gpu_notice or "Your original clip stays unchanged."
            )

    def update_controls(self):
        busy = self.worker is not None
        compressing = isinstance(self.worker, CompressionWorker)
        revealing = isinstance(self.worker, RevealWorker)
        self.drop.setEnabled(not busy)
        self.target_size.setEnabled(not compressing and not revealing)
        for button in self.preset_buttons.values():
            button.setEnabled(not compressing and not revealing)
        self.start_button.setVisible(not compressing)
        self.start_button.setEnabled(not busy and self._can_fit)
        self.start_button.setText("Compress again" if self.output else "Compress video")
        self.cancel_button.setVisible(compressing)
        self.cancel_button.setEnabled(compressing)
        self.open_button.setVisible(not busy and self.output is not None)
        self.percentage.setVisible(compressing or self.output is not None)
        self.elapsed_label.setVisible(compressing)
        self.progress.setVisible(compressing or self.output is not None)

    def launch(self, worker: ProcessThread):
        self.worker = worker
        worker.error.connect(self.show_error)
        worker.cancelled.connect(self.job_cancelled)
        worker.finished.connect(self.job_finished)
        self.update_controls()
        worker.start()

    def set_hardware(self, hardware: Hardware):
        self.hardware = hardware
        if hardware.encoder != "libx265":
            self.gpu_notice = ""
        if not isinstance(self.worker, CompressionWorker):
            self.refresh_ready_state()

    def set_gpu_notice(self, message: str):
        self.gpu_notice = message
        self.refresh_ready_state()

    def load_video(self, filename: str):
        if self.worker is not None:
            return
        try:
            source = Path(filename).expanduser().resolve()
            if not source.is_file():
                self.show_error("Choose a video file from your computer.")
                return
        except OSError as exc:
            self.show_error(str(exc))
            return
        self.source, self.info, self.output = source, None, None
        self._can_fit = False
        self.drop.show_source(source)
        self.status_label.setText("Reading clip…")
        self.status_hint.setText("Preparing the preview.")
        self.progress.setValue(0)
        worker = ProbeWorker(source, self)
        worker.preview.connect(self.drop.set_preview)
        worker.inspected.connect(self.set_media)
        self.launch(worker)

    def set_media(self, info: MediaInfo):
        self.info = info
        try:
            size = self.source.stat().st_size if self.source else 0
        except OSError:
            size = 0
        self.drop.set_metadata(info, size)
        self.refresh_ready_state()

    def start_compression(self):
        if (
            self.worker
            or not self.source
            or not self.info
            or not self.hardware
            or not self._can_fit
        ):
            return
        try:
            default = self.source.with_name(self.source.stem + "-discord.mp4")
            copy = 2
            while default.exists():
                default = self.source.with_name(
                    f"{self.source.stem}-discord-{copy}.mp4"
                )
                copy += 1
        except OSError as exc:
            self.show_error(str(exc))
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save compressed video",
            str(default),
            "MP4 video (*.mp4)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not filename:
            return
        output = Path(filename).expanduser()
        if output.suffix.lower() != ".mp4":
            output = output.with_suffix(".mp4")
        try:
            output = output.resolve()
            if output.exists():
                self.show_error("A file with that name exists. Choose a new filename.")
                return
        except OSError as exc:
            self.show_error(str(exc))
            return
        self.output = None
        self.progress.setValue(0)
        self.status_hint.setText(
            f"Keeping the full clip under {self.target_size.value():g} MB."
        )
        worker = CompressionWorker(
            self.source, output, self.hardware, self, target_mb=self.target_size.value()
        )
        worker.progress.connect(self.progress.setValue)
        worker.status.connect(self.status_label.setText)
        worker.hardware_changed.connect(self.set_hardware)
        worker.completed.connect(self.job_completed)
        self._started_at = time.monotonic()
        self._elapsed_seconds = 0.0
        self.update_elapsed()
        self.elapsed_timer.start()
        self.launch(worker)

    def update_elapsed(self):
        if self._started_at is not None:
            self._elapsed_seconds = time.monotonic() - self._started_at
        self.elapsed_label.setText(f"Elapsed {format_elapsed(self._elapsed_seconds)}")

    def stop_elapsed(self):
        self.update_elapsed()
        self.elapsed_timer.stop()
        self._started_at = None

    def cancel_job(self):
        if self.worker:
            self.status_label.setText("Stopping…")
            self.cancel_button.setEnabled(False)
            self.worker.cancel()

    def job_completed(self, filename: str, size: int):
        self.stop_elapsed()
        self.output = Path(filename)
        self.progress.setValue(100)
        self.status_label.setText(
            f"Saved {format_size(size)} in {format_elapsed(self._elapsed_seconds)}"
        )
        self.status_hint.setText(self.output.name)
        self.status_hint.setToolTip(filename)

    def job_cancelled(self):
        self.stop_elapsed()
        self.status_label.setText("Compression cancelled")
        self.status_hint.setText("Your original clip stays unchanged.")
        self.progress.setValue(0)

    def show_error(self, message: str):
        self.stop_elapsed()
        access_blocked = sys.platform == "win32" and is_file_access_error(message)
        if access_blocked:
            title, help_text = "File access blocked", WINDOWS_ACCESS_HELP
        elif isinstance(self.worker, DetectionWorker):
            title, help_text = (
                "Video tools are unavailable",
                "Reinstall the app, then try again.",
            )
        elif isinstance(self.worker, ProbeWorker):
            title, help_text = "Could not open this clip", "Choose another video file."
        elif isinstance(self.worker, CompressionWorker):
            title, help_text = (
                "Could not save this clip",
                "Try a larger file size or choose another clip.",
            )
        elif isinstance(self.worker, RevealWorker):
            title, help_text = (
                "Could not show this file",
                "The saved file may have been moved or removed.",
            )
        else:
            title, help_text = "Check the selected file", message
        self.status_label.setText(title)
        self.status_hint.setText(
            "Choose another folder, or allow the blocked app in Windows Security."
            if access_blocked
            else help_text
        )
        if not self._closing:
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(title)
            box.setInformativeText(help_text)
            security = None
            if access_blocked:
                security = box.addButton(
                    "Open Windows Security", QMessageBox.ButtonRole.ActionRole
                )
                box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if security is not None and box.clickedButton() is security:
                QDesktopServices.openUrl(QUrl(WINDOWS_SECURITY_URL))

    def job_finished(self):
        self.stop_elapsed()
        worker = self.worker
        self.worker = None
        if isinstance(worker, DetectionWorker) and self.hardware:
            self.refresh_ready_state()
        if worker:
            worker.deleteLater()
        self.update_controls()
        if self._closing:
            QTimer.singleShot(0, self.close)

    def open_output(self):
        if self.output and self.worker is None:
            worker = RevealWorker(self.output, self)
            worker.revealed.connect(self.file_revealed)
            self.launch(worker)

    def file_revealed(self, selected: bool):
        if not selected and self.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output.parent)))
            self.status_hint.setText(
                f"Saved as {self.output.name}. Select this file in the folder."
            )

    def closeEvent(self, event: QCloseEvent):
        self._closing = True
        self.updates.cancel()
        if self.worker:
            self.worker.cancel()
            self.status_label.setText("Stopping before closing…")
            event.ignore()
        else:
            self.settings.sync()
            event.accept()


def self_test() -> int:
    """Test bundled paths, Qt plugins, and a full two-pass encode without a GPU."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = create_application([sys.argv[0]])
    assert not app.windowIcon().isNull(), "The app icon is missing."
    assert app.applicationName() == APP_NAME
    assert app.applicationDisplayName() == APP_NAME
    assert app.applicationVersion() == get_app_version()
    assert QSslSocket.supportsSsl(), "Qt's HTTPS support is missing."
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        import plistlib

        contents = Path(sys.executable).parent.parent
        metadata = plistlib.loads((contents / "Info.plist").read_bytes())
        assert metadata["CFBundleName"] == APP_NAME
        assert metadata["CFBundleDisplayName"] == APP_NAME
        assert metadata["CFBundleIconFile"] == "app.icns"
        assert (contents / "Resources" / "app.icns").is_file()
    apply_theme(app)
    widget = ClipPreview()
    widget.resize(640, 360)
    widget.show()
    for theme_name in THEMES:
        apply_theme(app, theme_name)
        app.processEvents()
        assert not widget.grab().isNull(), "Qt could not render the clip preview."
    with tempfile.TemporaryDirectory(prefix="discord-compressor-test-") as directory:
        folder = Path(directory)
        source = folder / "input with spaces.mp4"
        output = folder / "output with spaces.mp4"
        runner = ProcessThread()
        ffmpeg, ffprobe = get_ffmpeg_path("ffmpeg"), get_ffmpeg_path("ffprobe")
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            app_folder = Path(sys.executable).resolve().parent
            for binary in (ffmpeg, ffprobe):
                assert Path(binary).is_relative_to(app_folder), (
                    "Windows helper paths must stay in the app folder."
                )
        runner.run_process(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=12",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "1",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                str(source),
            ],
            timeout=30,
        )
        worker = CompressionWorker(source, output, SOFTWARE)
        worker.log.connect(lambda line: None)
        path, size = worker.compress()
        info = runner.probe(path, ffprobe)
        assert 0 < size <= MAX_OUTPUT_BYTES
        assert info.has_audio and abs(info.duration - 1) < 0.2
        previews, errors = [], []
        preview_worker = ProbeWorker(source)
        preview_worker.preview.connect(previews.append)
        preview_worker.error.connect(errors.append)
        preview_worker.run()
        assert not errors and previews, "Clip preview could not be created."
        assert QPixmap().loadFromData(previews[0]), (
            "Qt could not display the clip preview."
        )
        runner.run_process(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(path),
                "-f",
                "null",
                os.devnull,
            ],
            timeout=30,
        )
    widget.close()
    if sys.stdout:
        print(f"Self-test passed. FFmpeg: {ffmpeg}; FFprobe: {ffprobe}", flush=True)
    return 0


def apply_theme(app: QApplication, theme_name: str = "frost"):
    colors = THEMES.get(theme_name, THEMES["frost"])
    # A stylesheet wraps QStyle and hides its name. Set the base style once.
    if not app.property("compressorStyleReady"):
        app.setStyle("Fusion")
        app.setProperty("compressorStyleReady", True)
    palette = app.palette()
    for role, color in (
        (QPalette.ColorRole.Window, colors["background"]),
        (QPalette.ColorRole.WindowText, colors["text"]),
        (QPalette.ColorRole.Base, colors["surface"]),
        (QPalette.ColorRole.Text, colors["text"]),
        (QPalette.ColorRole.Mid, colors["muted"]),
        (QPalette.ColorRole.PlaceholderText, colors["muted"]),
        (QPalette.ColorRole.Button, colors["raised"]),
        (QPalette.ColorRole.ButtonText, colors["text"]),
        (QPalette.ColorRole.Highlight, colors["accent"]),
        (QPalette.ColorRole.HighlightedText, colors["on_accent"]),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(THEME_QSS.substitute(colors))


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Test Qt, bundled tools, and software encoding.",
    )
    options = parser.parse_args()
    if options.self_test:
        return self_test()
    app = create_application(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Windowed Windows builds have no stdout or stderr handles.
        if sys.stderr:
            print(f"{APP_NAME}: {exc}", file=sys.stderr)
        sys.exit(1)
