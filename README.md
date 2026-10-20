# Discord Video Compressor

Compress video clips to a file size you choose. The default is **19.5 MB**, below
[Discord's 20 MB upload limit](https://support.discord.com/hc/en-us/articles/25444343291031-File-Attachments-FAQ).
Your clips stay on your computer, and your original files are kept.

**[Download the app](https://github.com/bharath063/discord-video-compressor/releases/latest)**
· [Website](https://bharath063.github.io/discord-video-compressor/)

## Install

Download the package for your computer and extract it.

| Platform | Open |
| --- | --- |
| macOS, Apple Silicon | Move `DiscordVideoCompressor.app` to Applications, then open it. |
| Windows, x64 | Keep the extracted folder together and open `DiscordVideoCompressor.exe`. |
| Linux, x64 | Run `DiscordVideoCompressor`. Qt system libraries are required. |

Python and FFmpeg are included. The macOS app is not notarized, and the Windows
app has no certificate signature. Your operating system may block the first launch.

## Use

1. Click the video area, or drag a clip into the app.
2. Set **Maximum file size**. Keep 19.5 MB for Discord, or choose a size from 1 to 500 MB.
3. Select **Compress video** and choose where to save it.
4. Check the final file size and elapsed time. Select **Show in folder** to find the result.

Use **Cancel** to stop compression. The **Light / Dark** switch changes the theme.
Use **About** to see the app version. Use **Check for updates** to find a newer release.

The output is an MP4 file. HEVC playback support depends on your device.

## Windows file access

Windows protects folders such as Documents and Videos with Controlled folder
access. If saving there is blocked, open **Virus & threat protection → Manage
ransomware protection → Allow an app through Controlled folder access → Add an
allowed app → Recently blocked apps** and allow `DiscordVideoCompressor.exe`.
Restart the app and retry.

The approval applies to the executable's full path. Keep the app in one fixed
folder and replace its contents when updating; opening each build in a different
folder requires another approval. For local builds, keep launching
`dist\DiscordVideoCompressor\DiscordVideoCompressor.exe`.
[Microsoft's instructions](https://learn.microsoft.com/en-us/defender-endpoint/controlled-folder-access-overview#allow-apps-to-modify-files-in-protected-folders).

Compression and previews use private folders in the system temporary directory,
which are removed when the job finishes, fails, or is cancelled. FFmpeg and
FFprobe do not write into your selected output folder; the app copies only the
verified video there. If you prefer to save without changing Windows Security
settings, select a folder outside the protected locations.

The app checks file access before compression. An access error shows the program
and folder that failed. Select **Copy error** if you need to report it.

The progress text shows the GPU or CPU used for encoding. If GPU encoding fails,
select **Why CPU?** to see the error. In Task Manager, check **Video Encode**.

[Report a problem](https://github.com/bharath063/discord-video-compressor/issues)
· [GPL-3.0 license](LICENSE)

This app is not affiliated with Discord.
