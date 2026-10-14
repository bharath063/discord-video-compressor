# Third-party components

The project source is licensed under GPL-3.0-only. Third-party components retain
their own licenses.

| Component | License or source reference |
| --- | --- |
| PyQt6 and PyQt6-sip | [Riverbank PyQt licensing](https://www.riverbankcomputing.com/software/pyqt/intro), [PyQt6 source](https://pypi.org/project/PyQt6/), [PyQt6-sip source](https://pypi.org/project/PyQt6-sip/) |
| Qt 6 from PyQt6-Qt6 | [Qt licensing](https://www.qt.io/licensing/open-source-lgpl-obligations), [Qt source archives](https://download.qt.io/archive/qt/) |
| FFmpeg | [FFmpeg license](https://ffmpeg.org/legal.html), [FFmpeg source](https://ffmpeg.org/download.html) |
| x265 | [x265 project](https://www.videolan.org/developers/x265.html) |
| Windows and Linux static builds | [BtbN build scripts and dependencies](https://github.com/BtbN/FFmpeg-Builds), [pinned build release](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-09-30-13-08) |
| macOS static builds | [Martin Riedl's build server](https://ffmpeg.martin-riedl.de/), [build scripts](https://git.martin-riedl.de/ffmpeg/build-script) |
| PyInstaller bootloader | [PyInstaller license and distribution exception](https://pyinstaller.org/en/stable/license.html) |

Static GPL builds include other libraries. Check their exact build configuration
and preserve all applicable notices. The downloader copies license, copying, and
notice files that are present in the downloaded archives to `binaries/licenses/`.
The spec includes these files, the build manifest, and these notices in the app.

When distributing the app, supply the matching source and build materials for
the components that require them. Keep an archive of the exact FFmpeg build and
its dependencies. Do not substitute a general upstream link for required source
materials. No nonfree FFmpeg build is selected by this project.
