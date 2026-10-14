# Discord Video Compressor

Compress video clips to a file size you choose. The default is **19.5 MB**, below
[Discord's 20 MB upload limit](https://support.discord.com/hc/en-us/articles/25444343291031-File-Attachments-FAQ).
Your clips stay on your computer, and your original files are kept.

**[Download the app](https://github.com/bharath063/discord-video-compressor/releases/latest)**
· [Website](https://www.bharathbalan.com/discord-video-compressor/)

## Install

Download the package for your computer and extract it.

| Platform | Open |
| --- | --- |
| macOS, Apple Silicon | Move `DiscordVideoCompressor.app` to Applications, then open it. |
| Windows, x64 | Open `DiscordVideoCompressor.exe`. |
| Linux, x64 | Run `DiscordVideoCompressor`. Qt system libraries are required. |

Python and FFmpeg are included. The macOS app is not notarized, and the Windows
app has no certificate signature. Your operating system may block the first launch.

## Use

1. Click **Choose video**, click the video area, or drag a clip into the app.
2. Set **Maximum file size**. Keep 19.5 MB for Discord, or choose a size from 1 to 500 MB.
3. Select **Compress video** and choose where to save it.
4. Check the final file size and elapsed time. Select **Show in folder** to find the result.

Use **Cancel** to stop compression. The **Light / Dark** switch changes the theme.

The output is an MP4 file. HEVC playback support depends on your device.

[Report a problem](https://github.com/bharath063/discord-video-compressor/issues)
· [GPL-3.0 license](LICENSE)
