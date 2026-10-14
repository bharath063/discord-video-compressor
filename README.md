# Discord Video Compressor

A desktop app that makes a video clip fit a selected maximum file size. The
interface places a large clip preview beside the output controls. The default
light theme is **Frost / White**. Use the **Light / Dark** switch for the
**Ink / Mint** dark theme. The app remembers the switch setting. Hardware
selection runs in the background. The interface has no technical panel.

The default is **19,500,000 bytes (19.5 decimal MB)**, which leaves space below
Discord's [20 MB free upload limit](https://support.discord.com/hc/en-us/articles/25444343291031-File-Attachments-FAQ).
Set a custom limit from **1 to 500 MB**, or use the 19.5, 25, and 50 MB preset
buttons. The app remembers the last selected limit. On upgrade, a saved 9.5 MB
default moves to 19.5 MB. Other custom limits are kept. You can still enter 9.5 MB.
AMD and Intel hardware encoders use a proportional safety buffer: 18 MB for the
default 19.5 MB limit. Every saved file is checked against the selected limit.

The app detects the operating system and GPU, tests the hardware encoder, and
uses two-pass software encoding when hardware encoding is not available. Release
packages contain FFmpeg and FFprobe. Users do not need to install Python or
FFmpeg. Processing stays on the user's computer.

## Project layout

```text
discord-video-compressor/
├── .github/workflows/release.yml
├── .gitignore
├── assets/
│   ├── app.svg
│   ├── app.png
│   ├── app.ico
│   └── app.icns
├── binaries/
│   └── .gitkeep
├── scripts/
│   ├── build_icons.py
│   └── fetch_ffmpeg.py
├── tests/
│   └── test_main.py
├── DiscordVideoCompressor.spec
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── README.md
├── requirements.txt
└── main.py
```

All GUI and compression logic is in `main.py`. The download script and spec file
are build tools. Tests use Python's standard `unittest` library.

## Development setup

Use Python 3.11 or 3.12. Run these commands from the project folder:

```bash
python -m venv .venv
```

Activate the environment on macOS or Linux:

```bash
source .venv/bin/activate
```

Activate it in Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the dependencies and download the tools for your computer:

```bash
python -m pip install -r requirements.txt
python scripts/fetch_ffmpeg.py
python main.py
```

The downloader supports macOS ARM64, Windows x64, and Linux x64. It uses pinned
static builds and checks each archive against a pinned SHA-256. It writes the
tools and a download manifest into `binaries/`. It does not extract archive paths.
See [Martin Riedl's build server](https://ffmpeg.martin-riedl.de/) for macOS builds
and [BtbN FFmpeg Builds](https://github.com/BtbN/FFmpeg-Builds) for Windows and Linux
builds.

For Intel macOS or another architecture, put matching static tools in
`binaries/` yourself. Development also supports tools on the system PATH. The
required filenames are:

| Operating system | FFmpeg | FFprobe |
| --- | --- | --- |
| Windows | `binaries/ffmpeg.exe` | `binaries/ffprobe.exe` |
| macOS / Linux | `binaries/ffmpeg` | `binaries/ffprobe` |

On macOS and Linux, the files must have execute permission:

```bash
chmod +x binaries/ffmpeg binaries/ffprobe
```

Use a GPL build with `libx265`. The downloader checks that the native FFmpeg build
also contains the hardware encoders listed below. GPU drivers must be installed
on the user's computer. FFmpeg does not supply GPU drivers.

## Use the app

1. Open the app and wait for the device check to finish.
2. Click the clip area, drop a video onto it, or select **Choose video**. Check its preview,
   duration, resolution, and original size.
3. Set **Maximum file size** in MB. The preset buttons set 19.5, 25, or 50 MB.
   You can also type a custom value. A larger limit can keep more video detail.
4. Select **Compress video**, then choose where to save the result.
5. Check the saved size and elapsed time. Select **Show in folder** to select the
   saved file in the file manager.

The app does not overwrite existing files. **Cancel** stops the active FFmpeg
process and removes temporary files. Closing the window during a job stops the
job before the app exits. FFprobe, GPU checks, and encoding run in worker threads.
The full clip is kept. If a limit is too small for its duration, the app asks you
to increase the limit or choose a shorter clip. Increasing the limit keeps the
selected clip ready for another attempt. Existing output names receive a new
number in the save dialog, so another compression can be saved as a separate copy.

During compression, the app shows a progress bar, percentage, elapsed time, and
the current task. The completed result shows the saved size and total elapsed
time. The total includes file checks and any retries. The progress bar stays at
100% until you select another video or change the size limit.

**Show in folder** reveals the exact output in Finder on macOS. Windows uses
[the Shell selection API](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shopenfolderandselectitems).
Linux uses the [FileManager1 interface](https://wiki.freedesktop.org/www/Specifications/file-manager-interface/)
through `dbus-send`. If a Linux desktop cannot select files, the app opens the
folder and shows the saved filename. File-manager requests run in a worker
thread to keep the window responsive.

## Encoder selection

| Platform and hardware | Encoder | Method | Budget at the default 19.5 MB limit |
| --- | --- | --- | --- |
| macOS, Apple Silicon | `hevc_videotoolbox` | One pass, hardware required | 19.5 MB |
| Windows / Linux, NVIDIA | `hevc_nvenc` | One pass, VBR, quality preset | 19.5 MB |
| Windows, AMD | `hevc_amf` | One pass, peak VBR | 18 MB |
| Windows / Linux, Intel | `hevc_qsv` | One pass, constrained bitrate | 18 MB |
| Intel Mac, generic CPU, unsupported hardware | `libx265` | Two passes, slow preset | 19.5 MB |

On a computer with multiple GPU vendors, the app tries NVIDIA, then AMD on
Windows, then Intel. Windows uses a fixed PowerShell CIM query. Linux reads the
PCI display-device vendor IDs from `/sys/bus/pci/devices`; `nvidia-smi` provides an
additional NVIDIA check. macOS checks the architecture and detects Apple Silicon
when Python runs under Rosetta.

An encoder name in `ffmpeg -encoders` is not proof that the GPU can encode HEVC.
The app tests four frames with the selected encoder and the same rate-control
options used for compression. If the test fails, it tries the next candidate.
If hardware encoding fails on the actual input, it retries with `libx265`.
Each hardware encoder uses `-b:v`, `-maxrate`, and `-bufsize`. VideoToolbox disables
its own software fallback so the selected hardware status remains accurate.

Input decoding uses FFmpeg's standard path. Hardware acceleration applies to
encoding. This avoids device-specific decode and filter failures. Video retains
the source frame rate and resolution; odd dimensions are reduced to even values.
The app selects the first video stream that is not attached cover art and the
first audio stream. It removes subtitles, other streams, chapters, and metadata.
Audio is AAC at 128 kb/s, stereo, 48 kHz. Video is 8-bit HEVC with an `hvc1` tag.
HDR tone mapping is not included. HEVC playback depends on the client and system
codecs; a valid upload size does not ensure inline playback in every client.

## File-size calculation and verification

The requested starting formula is:

```text
requested_total_bitrate = target_mb × 8192 × 1000 / duration_seconds
video_bitrate = total_bitrate - 128000
```

That conversion assigns 1,024,000 bytes to each MB. To enforce the stricter decimal
byte limit, the app caps the total bitrate. `size_mb` is the selected maximum;
`hardware_target_mb` is 19.5 for Apple Silicon, NVIDIA, and CPU, and 18 for AMD and
Intel:

```text
output_limit = floor(size_mb × 1000000)
byte_limit = min(output_limit, floor(output_limit × hardware_target_mb / 19.5))
target_mb = byte_limit / 1000000
requested_total_bitrate = target_mb × 8192 × 1000 / duration_seconds
overhead = max(65536, byte_limit × 0.015)
safe_total_bitrate = (byte_limit - overhead) × 8 / duration_seconds
video_bitrate = floor(min(requested_total_bitrate, safe_total_bitrate) - audio_bitrate)
```

`audio_bitrate` is 128,000 when audio is present and zero for a silent video.
The overhead reserve covers MP4 tables and small AAC bitrate changes. The app
rejects inputs whose duration leaves less than 32 kb/s for video. Increase the
selected limit or use a shorter clip in that case. The selected limit applies to
all attempts, including a switch to software encoding.

Bitrate control, including two-pass encoding, does not guarantee an exact file
size. After encoding, the app checks the full MP4 size and probes its duration.
An oversized file causes another full encode at a lower video bitrate. There are
at most four attempts, including a hardware-to-software fallback. If all attempts
fail, the app reports an error and does not save an oversized file. It does not
use `-fs` to cut the video short.

FFmpeg runs with an argument list and without a shell. Spaces, Unicode, shell
characters, and option-like filenames remain path arguments. Input paths are
absolute. A private temporary folder beside the output holds MP4 files and
two-pass statistics. This also keeps Windows drive-letter colons out of x265's
statistics options.

## Build with PyInstaller

Build separately on each target operating system and architecture. PyInstaller
does not cross-compile. Run:

```bash
python -m pip install -r requirements.txt pyinstaller==6.22.3
python scripts/fetch_ffmpeg.py
python -m PyInstaller --clean --noconfirm DiscordVideoCompressor.spec
```

The maintained spec file supplies both tools to `Analysis.binaries`:

```python
suffix = ".exe" if sys.platform == "win32" else ""
binaries = [
    (str(project / "binaries" / ("ffmpeg" + suffix)), "binaries"),
    (str(project / "binaries" / ("ffprobe" + suffix)), "binaries"),
]
analysis = Analysis([str(project / "main.py")], binaries=binaries, ...)
```

The tuple's second value is the destination inside the application payload.
Use `binaries`, as shown, so `get_ffmpeg_path()` finds
`Path(sys._MEIPASS) / "binaries" / filename`. PyInstaller uses its temporary
extraction folder for a onefile build and its internal bundle folder for an
onedir build. See the official [spec file documentation](https://pyinstaller.org/en/stable/spec-files.html)
and [runtime path documentation](https://pyinstaller.org/en/stable/runtime-information.html).

The spec uses `EXE` plus `COLLECT` and `BUNDLE` on macOS to make an onedir `.app`.
On Windows and Linux, `EXE` receives the complete binary and data lists to make a
onefile executable. Both paths include Qt's platform plugins through PyInstaller's
PyQt6 hooks. UPX is disabled. The spec also includes project documentation,
licensing notices, and the FFmpeg download manifest.

| Platform | Build output | Release archive |
| --- | --- | --- |
| macOS ARM64 | `dist/DiscordVideoCompressor.app` | `.zip` made with `ditto` |
| Windows x64 | `dist/DiscordVideoCompressor.exe` | `.zip` |
| Linux x64 | `dist/DiscordVideoCompressor` | `.tar.gz` |

The macOS archive preserves symlinks and execute permissions. Extract it and
copy the `.app` into Applications. Open the `.app` from Finder to run it as
**Discord Video Compressor**, with its own menu-bar name and Dock icon. Running
`python main.py` directly uses the Python interpreter's macOS application bundle.
The default build uses PyInstaller's ad-hoc
signing. For a Developer ID build, set `MACOS_CODESIGN_IDENTITY` before packaging,
then notarize and staple the final app with your Apple developer account. Developer
ID signing and notarization credentials are not configured in this project.
Windows certificate signing is also not configured.

### App icon

`assets/app.svg` is the editable icon source. The committed PNG, ICO, and ICNS
files are ready for each platform build. The spec embeds the ICNS in the macOS
bundle and the ICO in the Windows executable. Qt loads the bundled PNG for its
window icon on all platforms. The Mac bundle sets both `CFBundleName` and
`CFBundleDisplayName` to **Discord Video Compressor**.

To change the icon, edit the SVG and run this command on macOS:

```bash
python scripts/build_icons.py
```

The script uses the installed PyQt6 libraries and macOS `iconutil`. It creates
icons at multiple sizes, including Retina sizes. On Windows and Linux, it updates
the PNG and ICO; use macOS to update the ICNS. Commit the source and generated
files together. Icon generation runs locally.

Linux packages require a compatible glibc desktop and Qt system libraries. The
workflow installs the required libraries on its Ubuntu runner. For broader Linux
support, build on the oldest glibc distribution that you intend to support and
that the selected Qt wheels support. These builds do not target Alpine/musl.

## Tests

```bash
python -m unittest discover -s tests -v
python main.py --self-test
```

The self-test uses an offscreen Qt widget, the resolved FFmpeg and FFprobe paths,
and a real two-pass HEVC encode with audio. It checks output size, duration, full
decoding, preview generation, Qt's JPEG image support, and the bundled app icon.
On macOS, it also checks the bundle name and native icon. It requires no GPU.
Regression tests also check custom size limits and their connection to the GUI.
Test the packaged application as well:

```bash
# macOS
dist/DiscordVideoCompressor.app/Contents/MacOS/DiscordVideoCompressor --self-test
# Linux
dist/DiscordVideoCompressor --self-test
```

On Windows PowerShell, wait for the GUI executable and check its exit code:

```powershell
$process = Start-Process dist\DiscordVideoCompressor.exe -ArgumentList '--self-test' -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Packaged self-test failed' }
```

GPU operation must also be checked on a physical system with the correct GPU and
driver. Hosted build runners do not cover all GPU vendors. The packaged self-test
tests software encoding; the GUI performs the hardware test at startup.

## GitHub releases

Push this project to a GitHub repository with Actions enabled. Then push a
semantic version tag, for example:

```bash
git tag v1.0.0
git push origin v1.0.0
```

`.github/workflows/release.yml` builds on `macos-14` (ARM64), `windows-latest`
(x64), and `ubuntu-latest` (x64). It uses Python 3.12 for macOS and Windows, and
Python 3.11 for Linux. The workflow validates the tag and native architecture,
downloads and checks the pinned tools, runs tests, builds the app, and tests the
packaged executable with an empty PATH. It then uploads all three platform
archives and their SHA-256 files to a **draft release** for the pushed tag.

Only the final release job has `contents: write`. Actions are pinned to commit
IDs. The `v*` trigger accepts version tags; a validation step rejects tags that
do not use semantic version syntax. To publish releases directly, change
`draft: true` to `draft: false` in the release job.

When updating FFmpeg, update each URL and SHA-256 together in
`scripts/fetch_ffmpeg.py`, then run the source and packaged tests. The BtbN build
uses a retained monthly release. Retention is limited, so refresh the pinned
build before its upstream archive expires. To add another architecture, add its
verified static downloads, matrix entry, and package path.

## License and bundled components

This project's source uses GPL-3.0-only; see `LICENSE`. See
`THIRD_PARTY_NOTICES.md` for PyQt6, Qt, FFmpeg, x265, and PyInstaller references.
Preserve their license notices and provide the exact corresponding source and
build materials required by the selected component licenses when you distribute
binaries. A link to the latest upstream source alone does not identify the source
used to make a particular binary. The build manifest records the downloaded
archives and hashes so you can keep the matching build materials.
