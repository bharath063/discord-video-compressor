"""Regression tests for sizing, GPU fallback, paths, and cancellation."""

from __future__ import annotations

import json
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock

import main
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest


class UpdateVersionTests(unittest.TestCase):
    def release(self, tag, installed="0.0.1", **extra):
        payload = {"tag_name": tag, "draft": False, "prerelease": False, **extra}
        return main.parse_release(json.dumps(payload).encode(), installed)

    def test_numeric_versions_prereleases_and_build_metadata(self):
        for tag, installed, expected in (
            ("v0.0.10", "0.0.9", True),
            ("v0.0.9", "0.0.10", False),
            ("v1.0.0", "1.0.0-rc.2", True),
            ("v0.0.1+build.2", "0.0.1+build.1", False),
            ("v0.0.1", "0.0.1", False),
        ):
            with self.subTest(tag=tag, installed=installed):
                self.assertEqual(self.release(tag, installed).newer, expected)

    def test_draft_prerelease_and_invalid_versions_are_rejected(self):
        for tag, options in (
            ("v1.0.0", {"draft": True}),
            ("v1.0.0-rc.1", {"prerelease": True}),
            ("v1.0.0-rc.1", {}),
            ("v01.0.0", {}),
            ("<b>1.0.0</b>", {}),
            ("v1.0.0/../../other", {}),
            (None, {}),
        ):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.release(tag, **options)
        with self.assertRaises(ValueError):
            main.version_parts("1.0.0-rc.01")
        for data in (b"[]", b"not JSON", b"x" * (main.MAX_UPDATE_BYTES + 1)):
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                main.parse_release(data, "0.0.1")

    def test_remote_download_urls_are_ignored(self):
        release = self.release(
            "v1.2.3+build.4", html_url="https://untrusted.example/installer"
        )
        self.assertEqual(
            release.url, main.PROJECT_URL + "/releases/tag/v1.2.3%2Bbuild.4"
        )

    def test_packaged_version_has_priority_over_source_version(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "VERSION").write_text("1.2.3-rc.2\n")
            with patch.object(main.sys, "_MEIPASS", folder, create=True):
                self.assertEqual(main.get_app_version(), "1.2.3-rc.2")
            (Path(folder) / "VERSION").unlink()
            with (
                patch.object(main.sys, "_MEIPASS", folder, create=True),
                patch.object(main.sys, "frozen", True, create=True),
                self.assertRaisesRegex(RuntimeError, "version is missing"),
            ):
                main.get_app_version()


class UpdateNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = main.QApplication.instance() or main.QApplication([])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests += 1
                time.sleep(cls.delay)
                self.send_response(cls.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(cls.body)))
                self.end_headers()
                try:
                    self.wfile.write(cls.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server_thread = threading.Thread(
            target=lambda: cls.server.serve_forever(poll_interval=0.01), daemon=True
        )
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=1)

    def setUp(self):
        cls = type(self)
        cls.requests, cls.delay, cls.status = 0, 0, 200
        cls.body = json.dumps(
            {"tag_name": "v0.0.2", "draft": False, "prerelease": False}
        ).encode()
        self.api_patch = patch(
            "main.UPDATE_API_URL", f"http://127.0.0.1:{self.server.server_port}/latest"
        )
        self.api_patch.start()
        self.checker = main.UpdateChecker("0.0.1")
        self.results, self.errors, self.busy = [], [], []
        self.checker.checked.connect(self.results.append)
        self.checker.failed.connect(self.errors.append)
        self.checker.busy_changed.connect(self.busy.append)

    def tearDown(self):
        self.checker.cancel()
        self.wait_done()
        self.checker.deleteLater()
        self.app.processEvents()
        self.api_patch.stop()

    def wait_done(self):
        deadline = time.monotonic() + 3
        while self.checker.reply is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.checker.reply, "Update request did not finish")

    def test_check_is_async_keeps_timers_running_and_prevents_duplicate_requests(self):
        type(self).delay = 0.15
        ticks = []
        timer = main.QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        self.checker.check()
        self.checker.check()
        self.assertEqual(self.busy, [True])
        self.wait_done()
        timer.stop()
        self.assertGreater(len(ticks), 2)
        self.assertEqual(type(self).requests, 1)
        self.assertEqual(self.busy, [True, False])
        self.assertFalse(self.errors)
        self.assertTrue(self.results[0].newer)

    def test_rate_limit_invalid_json_and_large_responses_report_errors(self):
        for status, body, expected in (
            (403, b"{}", "GitHub is busy"),
            (200, b"not JSON", "could not be read"),
            (200, b"x" * (main.MAX_UPDATE_BYTES + 1), "could not be read"),
        ):
            with self.subTest(status=status, bytes=len(body)):
                type(self).status, type(self).body = status, body
                self.errors.clear()
                self.checker.check()
                self.wait_done()
                self.assertEqual(len(self.errors), 1)
                self.assertIn(expected, self.errors[0])
                self.assertFalse(self.results)

    def test_timeout_resets_state_and_a_later_check_can_succeed(self):
        type(self).delay = 0.2
        with patch("main.UPDATE_TIMEOUT_MS", 40):
            self.checker.check()
            self.wait_done()
        self.assertTrue(self.errors)
        self.assertEqual(self.busy[-1], False)
        type(self).delay = 0
        self.checker.check()
        self.wait_done()
        self.assertTrue(self.results)

    def test_cancel_does_not_emit_a_user_error(self):
        type(self).delay = 0.15
        self.checker.check()
        QTest.qWait(20)
        self.checker.cancel()
        self.wait_done()
        self.assertFalse(self.results)
        self.assertFalse(self.errors)
        self.assertEqual(self.busy, [True, False])


class BitrateTests(unittest.TestCase):
    def test_budget_includes_audio_and_container(self):
        for hardware in (
            main.SOFTWARE,
            main.Hardware("hevc_amf", "AMD", main.BUFFERED_TARGET_MB),
        ):
            info = main.MediaInfo(120, True, 0, 1920, 1080)
            bitrate = main.calculate_video_bitrate(info, hardware)
            expected_bytes = (bitrate + main.AUDIO_BITRATE) * info.duration / 8
            self.assertLess(expected_bytes, hardware.target_mb * 1_000_000)

    def test_silent_video_uses_audio_budget_for_video(self):
        audio = main.MediaInfo(60, True, 0, 640, 360)
        silent = main.MediaInfo(60, False, 0, 640, 360)
        self.assertEqual(
            main.calculate_video_bitrate(silent, main.SOFTWARE)
            - main.calculate_video_bitrate(audio, main.SOFTWARE),
            main.AUDIO_BITRATE,
        )

    def test_bad_and_infeasible_durations_are_rejected(self):
        for duration in (0, -1, float("nan"), float("inf"), 3600):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                main.calculate_video_bitrate(
                    main.MediaInfo(duration, True, 0, 640, 360), main.SOFTWARE
                )

    def test_custom_limits_change_the_budget(self):
        info = main.MediaInfo(120, True, 0, 1920, 1080)
        small = main.calculate_video_bitrate(info, main.SOFTWARE, 3.0)
        large = main.calculate_video_bitrate(info, main.SOFTWARE, 25.0)
        self.assertLess(small, large)
        self.assertLess((small + main.AUDIO_BITRATE) * info.duration / 8, 3_000_000)
        self.assertGreater(
            (large + main.AUDIO_BITRATE) * info.duration / 8, main.MAX_OUTPUT_BYTES
        )
        amd = main.Hardware("hevc_amf", "AMD", main.BUFFERED_TARGET_MB)
        self.assertEqual(
            main.encoder_target_bytes(amd, main.DEFAULT_TARGET_MB), 18_000_000
        )
        self.assertLess(main.encoder_target_bytes(amd, 25.0), 25_000_000)

    def test_default_uses_more_of_the_new_discord_limit(self):
        info = main.MediaInfo(60, True, 0, 1920, 1080)
        self.assertEqual(main.DEFAULT_TARGET_MB, 19.5)
        self.assertEqual(main.MAX_OUTPUT_BYTES, 19_500_000)
        bitrate = main.calculate_video_bitrate(info, main.SOFTWARE)
        expected_bytes = (bitrate + main.AUDIO_BITRATE) * info.duration / 8
        self.assertGreater(expected_bytes, 19_000_000)
        self.assertLess(expected_bytes, 19_500_000)

    def test_invalid_file_size_settings_are_rejected(self):
        for size in (0, -1, 0.9, 501, float("nan"), float("inf")):
            with self.subTest(size=size), self.assertRaises(ValueError):
                main.output_limit_bytes(size)


class ProbeTests(unittest.TestCase):
    def test_cover_art_is_skipped_and_video_duration_has_priority(self):
        payload = {
            "format": {"duration": "100"},
            "streams": [
                {"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}},
                {
                    "index": 2,
                    "codec_type": "video",
                    "duration": "12.5",
                    "width": 641,
                    "height": 361,
                },
                {"index": 3, "codec_type": "audio"},
            ],
        }
        info = main.parse_media_info(json.dumps(payload))
        self.assertEqual(info.duration, 12.5)
        self.assertEqual(info.video_index, 2)
        self.assertTrue(info.has_audio)

    def test_missing_video_or_duration_is_rejected(self):
        for streams in (
            [],
            [{"index": 0, "codec_type": "video", "width": 640, "height": 360}],
        ):
            with self.assertRaises(ValueError):
                main.parse_media_info(json.dumps({"streams": streams}))


class PathTests(unittest.TestCase):
    def test_nvidia_cuda_frames_stay_on_the_gpu(self):
        command = main.encoding_commands(
            "ffmpeg",
            Path("input.mp4"),
            Path("output.mp4"),
            main.MediaInfo(30, False, 0, 1920, 1080),
            main.Hardware("hevc_nvenc", "NVIDIA"),
            500_000,
            gpu_decode=True,
        )[0]
        self.assertLess(command.index("-hwaccel"), command.index("-i"))
        self.assertEqual(command[command.index("-hwaccel") + 1], "cuda")
        self.assertEqual(command[command.index("-hwaccel_output_format") + 1], "cuda")
        self.assertIn("scale_cuda", command[command.index("-vf") + 1])
        self.assertEqual(command[command.index("-c:v") + 1], "hevc_nvenc")
        self.assertNotIn("-pix_fmt", command)

    def test_bundle_takes_priority_and_does_not_use_path_when_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "binaries").mkdir()
            name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
            binary = root / "binaries" / name
            binary.write_bytes(b"test")
            binary.chmod(0o755)
            with (
                patch.object(sys, "_MEIPASS", folder, create=True),
                patch("main.shutil.which") as which,
            ):
                self.assertEqual(main.get_ffmpeg_path("ffmpeg"), str(binary.resolve()))
                which.assert_not_called()
                with self.assertRaises(FileNotFoundError):
                    main.get_ffmpeg_path("ffprobe")
                which.assert_not_called()

    def test_paths_are_passed_as_single_arguments(self):
        source = Path(tempfile.gettempdir()) / "a file;$(touch DO_NOT_CREATE).mp4"
        output = Path(tempfile.gettempdir()) / "output name.mp4"
        commands = main.encoding_commands(
            "ffmpeg",
            source,
            output,
            main.MediaInfo(60, True, 2, 640, 360),
            main.SOFTWARE,
            500_000,
        )
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertEqual(command[command.index("-i") + 1], str(source))
            self.assertEqual(command[command.index("-map") + 1], "0:2")
        self.assertEqual(commands[1][-1], str(output))
        self.assertNotIn("-fs", commands[1])


class DetectionTests(unittest.TestCase):
    def test_windows_nvenc_is_checked_without_inventory_tools(self):
        worker = main.DetectionWorker()
        detected = []
        worker.detected.connect(detected.append)

        def fake_driver(command, **kwargs):
            if "-encoders" in command:
                return " V....D hevc_nvenc NVIDIA\n V....D libx265 HEVC"
            self.assertEqual(command[command.index("-c:v") + 1], "hevc_nvenc")
            source = command[command.index("-i") + 1]
            dimensions = source.split("size=", 1)[1].split(":", 1)[0]
            width, height = map(int, dimensions.split("x"))
            if width < 256 or height < 144:
                raise main.ProcessError("The driver rejects a test frame this small.")
            return ""

        with (
            patch("main.platform.system", return_value="Windows"),
            patch("main.shutil.which", return_value=None) as inventory,
            patch("main.get_ffmpeg_path", return_value="ffmpeg"),
            patch.object(worker, "run_process", side_effect=fake_driver),
        ):
            worker.run()
        self.assertEqual([item.encoder for item in detected], ["hevc_nvenc"])
        inventory.assert_not_called()

    def test_old_nvidia_driver_reports_an_actionable_notice(self):
        worker = main.DetectionWorker()
        detected, notices = [], []
        worker.detected.connect(detected.append)
        worker.notice.connect(notices.append)
        with (
            patch("main.get_ffmpeg_path", return_value="ffmpeg"),
            patch(
                "main.hardware_candidates",
                return_value=[main.Hardware("hevc_nvenc", "NVIDIA")],
            ),
            patch.object(
                worker,
                "run_process",
                side_effect=[
                    " V....D hevc_nvenc NVIDIA\n V....D libx265 HEVC",
                    main.ProcessError(
                        "Driver does not support the required nvenc API version."
                    ),
                ],
            ),
        ):
            worker.run()
        self.assertEqual(detected, [main.SOFTWARE])
        self.assertEqual(len(notices), 1)
        self.assertIn("Update your NVIDIA driver", notices[0])

    def test_apple_silicon_and_intel_mac(self):
        worker = main.ProcessThread()
        with patch("main.platform.system", return_value="Darwin"):
            with patch("main.platform.machine", return_value="arm64"):
                self.assertEqual(
                    main.hardware_candidates(worker)[0].encoder, "hevc_videotoolbox"
                )
            with (
                patch("main.platform.machine", return_value="x86_64"),
                patch.object(worker, "run_process", return_value="0"),
            ):
                self.assertEqual(main.hardware_candidates(worker), [])
            with (
                patch("main.platform.machine", return_value="x86_64"),
                patch.object(worker, "run_process", return_value="1"),
            ):
                self.assertEqual(
                    main.hardware_candidates(worker)[0].encoder, "hevc_videotoolbox"
                )

    def test_windows_vendor_priority_and_targets(self):
        worker = main.ProcessThread()
        with (
            patch("main.platform.system", return_value="Windows"),
            patch("main.shutil.which", return_value="powershell.exe"),
            patch.object(
                worker,
                "run_process",
                return_value="NVIDIA GeForce\nAMD Radeon\nIntel Arc",
            ),
        ):
            candidates = main.hardware_candidates(worker)
        self.assertEqual(
            [c.encoder for c in candidates], ["hevc_nvenc", "hevc_amf", "hevc_qsv"]
        )
        self.assertEqual([c.target_mb for c in candidates], [19.5, 18.0, 18.0])

    def test_broken_driver_uses_software(self):
        worker = main.DetectionWorker()
        results, errors, notices = [], [], []
        worker.detected.connect(results.append)
        worker.diagnostic.connect(errors.append)
        worker.notice.connect(notices.append)
        listing = " V....D hevc_nvenc NVIDIA\n V....D libx265 HEVC"
        with (
            patch("main.get_ffmpeg_path", return_value="ffmpeg"),
            patch(
                "main.hardware_candidates",
                return_value=[main.Hardware("hevc_nvenc", "NVIDIA")],
            ),
            patch.object(
                worker,
                "run_process",
                side_effect=[listing, main.ProcessError("Driver unavailable")],
            ),
        ):
            worker.run()
        self.assertEqual(results, [main.SOFTWARE])
        self.assertIn("Driver unavailable", errors[0])
        self.assertIn("Using CPU", notices[0])


class FileAccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name).resolve()
        self.source, self.output = root / "input with spaces.mp4", root / "output.mp4"
        self.source.write_bytes(b"original video")
        self.worker = main.CompressionWorker(
            self.source, self.output, main.Hardware("hevc_nvenc", "NVIDIA")
        )
        self.info = main.MediaInfo(30, False, 0, 640, 360)

    def fake_tool(self, command, **kwargs):
        if "lavfi" in command:
            Path(command[-1]).write_bytes(b"one test frame")
        return ""

    def test_app_cannot_read_source_stops_before_ffprobe(self):
        original = Path.open

        def open_file(path, mode="r", *args, **kwargs):
            if path == self.source and mode == "rb":
                raise PermissionError(13, "Permission denied", str(path))
            return original(path, mode, *args, **kwargs)

        with (
            patch.object(Path, "open", open_file),
            patch.object(self.worker, "probe") as probe,
            self.assertRaises(main.FileOperationError) as caught,
        ):
            self.worker.read_clip(self.source, "ffprobe.exe")
        probe.assert_not_called()
        self.assertEqual(caught.exception.program, sys.executable)
        self.assertEqual(caught.exception.path, self.source)
        self.assertTrue(caught.exception.denied)

    def test_ffprobe_has_its_own_read_permission(self):
        with (
            patch.object(
                self.worker,
                "probe",
                side_effect=main.ProcessError(f"{self.source}: Permission denied"),
            ),
            self.assertRaises(main.FileOperationError) as caught,
        ):
            self.worker.read_clip(self.source, "ffprobe.exe")
        self.assertEqual(caught.exception.program, "ffprobe.exe")
        self.assertEqual(caught.exception.operation, "read the video information")
        self.assertTrue(caught.exception.denied)

    def test_missing_file_is_not_a_permission_error(self):
        missing = Path(self.folder.name) / "missing.mp4"
        with self.assertRaises(main.FileOperationError) as caught:
            self.worker.read_clip(missing, "ffprobe.exe")
        self.assertFalse(caught.exception.denied)
        self.assertIn("missing.mp4", str(caught.exception))

    def test_ffmpeg_cannot_read_source_stops_before_writing(self):
        changes = []
        self.worker.hardware_changed.connect(changes.append)
        with (
            patch("main.get_ffmpeg_path", side_effect=lambda name: name + ".exe"),
            patch.object(self.worker, "probe", return_value=self.info),
            patch.object(
                self.worker,
                "run_process",
                side_effect=main.ProcessError(
                    "Error opening input files: Permission denied"
                ),
            ) as process,
            self.assertRaises(main.FileOperationError) as caught,
        ):
            self.worker.compress()
        self.assertEqual(process.call_count, 1)
        self.assertEqual(caught.exception.program, "ffmpeg.exe")
        self.assertEqual(caught.exception.path, self.source)
        self.assertEqual(changes, [])
        self.assertEqual(
            [p.name for p in Path(self.folder.name).iterdir()], [self.source.name]
        )

    def test_app_write_block_is_distinct_from_ffmpeg_write_block(self):
        with (
            patch("main.get_ffmpeg_path", return_value="ffmpeg.exe"),
            patch.object(self.worker, "probe", return_value=self.info),
            patch.object(self.worker, "run_process", return_value="") as process,
            patch(
                "main.tempfile.TemporaryFile",
                side_effect=PermissionError(13, "Permission denied"),
            ) as write,
            self.assertRaises(main.FileOperationError) as caught,
        ):
            self.worker.compress()
        self.assertEqual(process.call_count, 1)  # FFmpeg input read only.
        self.assertEqual(caught.exception.program, sys.executable)
        self.assertEqual(caught.exception.operation, "write to the output folder")
        self.assertEqual(caught.exception.path, self.output.parent)
        self.assertEqual(write.call_args.kwargs["dir"], self.output.parent)
        self.assertTrue(caught.exception.denied)
        self.assertFalse(self.output.exists())

    def test_ffmpeg_write_denial_stops_before_gpu_encoding(self):
        def run_tool(command, **kwargs):
            if "lavfi" in command:
                self.assertEqual(command[command.index("-c:v") + 1], "mpeg4")
                raise main.ProcessError("Error opening output file: Permission denied")
            return ""

        changes, statuses = [], []
        self.worker.hardware_changed.connect(changes.append)
        self.worker.status.connect(statuses.append)
        with (
            patch("main.get_ffmpeg_path", side_effect=lambda name: name + ".exe"),
            patch.object(self.worker, "probe", return_value=self.info),
            patch.object(self.worker, "run_process", side_effect=run_tool) as process,
            self.assertRaises(main.FileOperationError) as caught,
        ):
            self.worker.compress()
        self.assertEqual(process.call_count, 2)
        self.assertEqual(caught.exception.program, "ffmpeg.exe")
        self.assertEqual(caught.exception.operation, "write to the temporary work folder")
        self.assertTrue(caught.exception.denied)
        self.assertEqual(changes, [])
        self.assertEqual(statuses, ["Checking file access…"])
        self.assertEqual(self.source.read_bytes(), b"original video")
        self.assertEqual(
            [p.name for p in Path(self.folder.name).iterdir()], [self.source.name]
        )

    def test_ffprobe_must_read_temporary_work_folder_too(self):
        def run_tool(command, **kwargs):
            if command[0] == "ffprobe.exe":
                raise main.ProcessError("Permission denied")
            return self.fake_tool(command, **kwargs)

        with (
            patch("main.get_ffmpeg_path", side_effect=lambda name: name + ".exe"),
            patch.object(self.worker, "probe", return_value=self.info),
            patch.object(self.worker, "run_process", side_effect=run_tool),
            self.assertRaises(main.FileOperationError) as caught,
        ):
            self.worker.compress()
        self.assertEqual(caught.exception.program, "ffprobe.exe")
        self.assertEqual(caught.exception.operation, "read from the temporary work folder")
        self.assertTrue(caught.exception.denied)
        self.assertFalse(self.output.exists())

    def test_successful_checks_leave_no_dummy_files(self):
        workdir = Path(self.folder.name) / "work"
        workdir.mkdir()
        with patch.object(
            self.worker, "run_process", side_effect=self.fake_tool
        ) as process:
            self.worker.check_file_access(
                "ffmpeg.exe", "ffprobe.exe", workdir, self.info
            )
        self.assertEqual(list(workdir.iterdir()), [])
        self.assertEqual(process.call_count, 3)
        self.assertEqual(self.source.read_bytes(), b"original video")
        self.assertFalse(self.output.exists())

    def test_helpers_never_write_into_the_selected_output_folder(self):
        workdirs = []

        def run_tool(command, **kwargs):
            if "lavfi" in command or command[-1].endswith("encoded.mp4"):
                destination = Path(command[-1]).resolve()
                if destination.is_relative_to(self.output.parent):
                    raise main.ProcessError("Error opening output file: Permission denied")
                self.assertEqual(
                    destination.parent.parent, Path(tempfile.gettempdir()).resolve()
                )
                workdirs.append(destination.parent)
                destination.write_bytes(b"encoded video")
            return ""

        with (
            patch("main.get_ffmpeg_path", side_effect=lambda name: name + ".exe"),
            patch.object(self.worker, "probe", return_value=self.info),
            patch.object(self.worker, "run_process", side_effect=run_tool),
        ):
            path, size = self.worker.compress()
        self.assertEqual(path, self.output)
        self.assertEqual(size, len(b"encoded video"))
        self.assertEqual(self.output.read_bytes(), b"encoded video")
        self.assertEqual(self.source.read_bytes(), b"original video")
        self.assertEqual(len(workdirs), 2)
        self.assertTrue(all(not folder.exists() for folder in workdirs))

    def test_failed_temp_directory_creation_names_the_temporary_folder(self):
        with (
            patch("main.get_ffmpeg_path", return_value="ffmpeg.exe"),
            patch.object(self.worker, "probe", return_value=self.info),
            patch(
                "main.tempfile.TemporaryDirectory",
                side_effect=PermissionError(13, "Permission denied"),
            ),
            self.assertRaises(main.FileOperationError) as caught,
        ):
            self.worker.compress()
        self.assertEqual(caught.exception.program, sys.executable)
        self.assertEqual(caught.exception.path, Path(tempfile.gettempdir()))
        self.assertEqual(caught.exception.operation, "create a temporary work folder")
        self.assertTrue(caught.exception.denied)

    def test_cancelled_access_check_leaves_no_test_file(self):
        def cancel_read(command, **kwargs):
            self.worker.cancel()
            raise main.Cancelled()

        with (
            patch("main.get_ffmpeg_path", return_value="ffmpeg"),
            patch.object(self.worker, "probe", return_value=self.info),
            patch.object(self.worker, "run_process", side_effect=cancel_read),
            self.assertRaises(main.Cancelled),
        ):
            self.worker.compress()
        self.assertEqual(
            [p.name for p in Path(self.folder.name).iterdir()], [self.source.name]
        )


class OutputTests(unittest.TestCase):
    def setUp(self):
        # Access checks have their own tests below. These cases exercise the
        # size and publication rules with a mocked encoder, not a real video.
        check = patch.object(main.CompressionWorker, "check_file_access")
        check.start()
        self.addCleanup(check.stop)

    def test_cpu_decode_retry_keeps_nvidia_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"original")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            commands, changes, notices = [], [], []
            worker.hardware_changed.connect(changes.append)
            worker.notice.connect(notices.append)

            def run_tool(command, **kwargs):
                commands.append(command)
                if "-hwaccel" in command:
                    raise main.ProcessError("GPU decoder does not support this format")
                Path(command[-1]).write_bytes(b"encoded with NVENC")
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(
                    worker, "probe", return_value=main.MediaInfo(30, False, 0, 640, 360)
                ),
                patch.object(worker, "run_process", side_effect=run_tool),
            ):
                worker.compress()
            self.assertEqual(
                [c[c.index("-c:v") + 1] for c in commands], ["hevc_nvenc"] * 2
            )
            self.assertNotIn("-hwaccel", commands[1])
            self.assertEqual(changes, [])
            self.assertIn("NVIDIA GPU encoding", notices[0])
            self.assertEqual(output.read_bytes(), b"encoded with NVENC")

    def test_nvidia_encode_failure_keeps_the_driver_error(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"original")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            encoders, errors, changes = [], [], []
            worker.hardware_changed.connect(changes.append)
            worker.diagnostic.connect(errors.append)

            def run_tool(command, **kwargs):
                encoder = command[command.index("-c:v") + 1]
                encoders.append(encoder)
                if encoder == "hevc_nvenc":
                    raise main.ProcessError(
                        "OpenEncodeSessionEx failed: driver error 10"
                    )
                if command[-1] != main.os.devnull:
                    Path(command[-1]).write_bytes(b"encoded with CPU")
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(
                    worker, "probe", return_value=main.MediaInfo(30, False, 0, 640, 360)
                ),
                patch.object(worker, "run_process", side_effect=run_tool),
            ):
                worker.compress()
            self.assertEqual(
                encoders, ["hevc_nvenc", "hevc_nvenc", "libx265", "libx265"]
            )
            self.assertEqual(changes, [main.SOFTWARE])
            self.assertTrue(all("driver error 10" in error for error in errors))

    def test_blocked_output_does_not_retry_using_the_cpu(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            changes, statuses = [], []
            worker.hardware_changed.connect(changes.append)
            worker.status.connect(statuses.append)
            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(
                    worker, "probe", return_value=main.MediaInfo(30, False, 0, 640, 360)
                ),
                patch.object(
                    worker,
                    "run_process",
                    side_effect=main.ProcessError(
                        "ffmpeg.exe exited with code 1: Error opening output file: Permission denied"
                    ),
                ) as encoder,
                self.assertRaises(main.FileOperationError),
            ):
                worker.compress()
            encoder.assert_called_once()
            self.assertEqual(changes, [])
            self.assertIn("Compressing with NVIDIA GPU…", statuses)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), b"input")

    def test_default_can_publish_a_file_larger_than_the_old_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            info = main.MediaInfo(60, True, 0, 1920, 1080)

            def fake_encode(command, **kwargs):
                with Path(command[-1]).open("wb") as stream:
                    stream.truncate(18_500_000)
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(worker, "probe", return_value=info),
                patch.object(worker, "run_process", side_effect=fake_encode),
            ):
                _, size = worker.compress()
            self.assertEqual(size, 18_500_000)
            self.assertLess(size, 19_500_000)
            self.assertEqual(output.stat().st_size, size)

    def test_oversized_output_is_retried_before_saving(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            info = main.MediaInfo(30, False, 0, 640, 360)
            rates = []

            def fake_encode(command, **kwargs):
                self.assertFalse(output.exists())
                rates.append(int(command[command.index("-b:v") + 1]))
                encoded = Path(command[-1])
                with encoded.open("wb") as stream:
                    stream.truncate(
                        main.MAX_OUTPUT_BYTES + 1 if len(rates) == 1 else 1000
                    )
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(worker, "probe", return_value=info),
                patch.object(worker, "run_process", side_effect=fake_encode),
            ):
                path, size = worker.compress()
            self.assertEqual(path, output.resolve())
            self.assertEqual(size, 1000)
            self.assertEqual(output.stat().st_size, 1000)
            self.assertLess(rates[1], rates[0])
            self.assertEqual(
                sorted(p.name for p in Path(folder).iterdir()),
                ["input.mp4", "output.mp4"],
            )

    def test_persistent_oversize_never_publishes_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            info = main.MediaInfo(30, False, 0, 640, 360)

            def fake_encode(command, **kwargs):
                with Path(command[-1]).open("wb") as stream:
                    stream.truncate(main.MAX_OUTPUT_BYTES + 1)
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(worker, "probe", return_value=info),
                patch.object(worker, "run_process", side_effect=fake_encode),
            ):
                with self.assertRaisesRegex(RuntimeError, "four attempts"):
                    worker.compress()
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), b"input")

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"original input")
            output.write_bytes(b"existing output")
            worker = main.CompressionWorker(source, output, main.SOFTWARE)
            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                self.assertRaises(FileExistsError),
            ):
                worker.compress()
            self.assertEqual(source.read_bytes(), b"original input")
            self.assertEqual(output.read_bytes(), b"existing output")

    def test_input_specific_hardware_failure_uses_two_software_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source,
                output,
                main.Hardware("hevc_amf", "AMD", main.BUFFERED_TARGET_MB),
                target_mb=25.0,
            )
            info = main.MediaInfo(30, False, 0, 640, 360)
            commands = []
            changes = []
            worker.hardware_changed.connect(changes.append)

            def fake_encode(command, **kwargs):
                commands.append(command)
                encoder = command[command.index("-c:v") + 1]
                if encoder == "hevc_amf":
                    raise main.ProcessError("Unsupported source dimensions")
                if command[-1] != main.os.devnull:
                    Path(command[-1]).write_bytes(b"complete output")
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(worker, "probe", return_value=info),
                patch.object(worker, "run_process", side_effect=fake_encode),
            ):
                worker.compress()
            self.assertEqual(
                [c[c.index("-c:v") + 1] for c in commands],
                ["hevc_amf", "libx265", "libx265"],
            )
            self.assertEqual(changes, [main.SOFTWARE])
            self.assertEqual(
                int(commands[-1][commands[-1].index("-b:v") + 1]),
                main.calculate_video_bitrate(info, main.SOFTWARE, 25.0),
            )
            self.assertEqual(output.read_bytes(), b"complete output")

    def test_duration_mismatch_is_not_published(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            full = main.MediaInfo(30, False, 0, 640, 360)
            cut = main.MediaInfo(15, False, 0, 640, 360)

            def fake_encode(command, **kwargs):
                Path(command[-1]).write_bytes(b"truncated video")
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(worker, "probe", side_effect=[full, cut]),
                patch.object(worker, "run_process", side_effect=fake_encode),
            ):
                with self.assertRaisesRegex(RuntimeError, "duration"):
                    worker.compress()
            self.assertFalse(output.exists())

    def test_cancel_removes_private_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA")
            )
            info = main.MediaInfo(30, False, 0, 640, 360)
            workdirs = []

            def fake_encode(command, **kwargs):
                encoded = Path(command[-1])
                workdirs.append(encoded.parent)
                encoded.write_bytes(b"partial output")
                worker.cancel()
                raise main.Cancelled()

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(worker, "probe", return_value=info),
                patch.object(worker, "run_process", side_effect=fake_encode),
            ):
                with self.assertRaises(main.Cancelled):
                    worker.compress()
            self.assertFalse(output.exists())
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["input.mp4"])
            self.assertTrue(workdirs)
            self.assertTrue(all(not workdir.exists() for workdir in workdirs))

    def test_larger_selected_limit_is_used_when_saving(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"input")
            worker = main.CompressionWorker(
                source, output, main.Hardware("hevc_nvenc", "NVIDIA"), target_mb=25.0
            )
            info = main.MediaInfo(30, False, 0, 640, 360)

            def fake_encode(command, **kwargs):
                with Path(command[-1]).open("wb") as stream:
                    stream.truncate(12_000_000)
                return ""

            with (
                patch("main.get_ffmpeg_path", return_value="ffmpeg"),
                patch.object(worker, "probe", return_value=info),
                patch.object(worker, "run_process", side_effect=fake_encode),
            ):
                _, size = worker.compress()
            self.assertEqual(size, 12_000_000)
            self.assertEqual(output.stat().st_size, size)


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = main.QApplication.instance() or main.create_application([])

    def test_icon_loads_from_source_and_bundled_assets(self):
        icon = main.get_app_icon().pixmap(64, 64)
        self.assertFalse(icon.isNull())
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder) / "assets"
            assets.mkdir()
            source = Path(main.__file__).parent / "assets" / "app.png"
            (assets / "app.png").write_bytes(source.read_bytes())
            with patch.object(sys, "_MEIPASS", folder, create=True):
                bundled = main.get_app_icon().pixmap(64, 64)
            self.assertFalse(bundled.isNull())
            self.assertEqual(icon.toImage(), bundled.toImage())

    def test_native_icon_assets_include_small_and_retina_sizes(self):
        assets = Path(main.__file__).parent / "assets"
        image = QImage(str(assets / "app.png"))
        self.assertEqual((image.width(), image.height()), (1024, 1024))
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        ico = (assets / "app.ico").read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", ico)
        self.assertEqual((reserved, kind), (0, 1))
        sizes = []
        for index in range(count):
            width, height, _, _, _, depth, length, offset = struct.unpack_from(
                "<BBBBHHII", ico, 6 + 16 * index
            )
            image = QImage.fromData(ico[offset : offset + length], "PNG")
            self.assertFalse(image.isNull())
            self.assertEqual(image.width(), width or 256)
            self.assertEqual(image.height(), height or 256)
            self.assertEqual(depth, 32)
            sizes.append(image.width())
        self.assertEqual(sizes, [16, 24, 32, 48, 64, 128, 256])
        icns = (assets / "app.icns").read_bytes()
        self.assertEqual(icns[:4], b"icns")
        self.assertEqual(struct.unpack_from(">I", icns, 4)[0], len(icns))
        self.assertIn(b"ic10", icns)  # 1024-pixel icon for Retina displays.


class SizeSettingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = main.QApplication.instance() or main.QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.settings = main.QSettings(
            str(Path(self.folder.name) / "settings.ini"),
            main.QSettings.Format.IniFormat,
        )
        with patch.object(main.DetectionWorker, "start"):
            self.window = main.MainWindow(self.settings)
        self.window.job_finished()
        self.window.set_hardware(main.SOFTWARE)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.folder.cleanup()

    def test_about_button_shows_version_and_disclaimer(self):
        boxes = []
        with patch.object(main.QMessageBox, "exec", lambda box: boxes.append(box) or 0):
            self.window.about_button.click()
        self.assertEqual(len(boxes), 1)
        self.assertIn(f"Version {main.get_app_version()}", boxes[0].informativeText())
        self.assertIn(main.AFFILIATION_NOTICE, boxes[0].informativeText())
        self.assertEqual(boxes[0].textFormat(), main.Qt.TextFormat.PlainText)
        self.assertEqual(
            self.window.about_action.menuRole(), main.QAction.MenuRole.AboutRole
        )

    def test_windows_access_error_opens_help_for_the_blocked_app(self):
        boxes = []

        def choose_security(box):
            boxes.append(box)
            box._selected_for_test = next(
                button
                for button in box.buttons()
                if button.text() == "Open Windows Security"
            )
            return 0

        with (
            patch("main.sys.platform", "win32"),
            patch.object(main.QMessageBox, "exec", choose_security),
            patch.object(
                main.QMessageBox, "clickedButton", lambda box: box._selected_for_test
            ),
            patch.object(main.QDesktopServices, "openUrl") as open_security,
        ):
            self.window.show_error("[WinError 5] Access is denied")
        self.assertEqual(boxes[0].text(), "File access blocked")
        self.assertIn("Recently blocked apps", boxes[0].informativeText())
        self.assertIn("DiscordVideoCompressor.exe", boxes[0].informativeText())
        self.assertEqual(boxes[0].textFormat(), main.Qt.TextFormat.PlainText)
        self.assertEqual(
            open_security.call_args.args[0].toString(), main.WINDOWS_SECURITY_URL
        )

    def test_access_error_names_failed_operation_and_preserves_the_cause(self):
        blocked = Path(self.folder.name) / "Videos"
        error = main.FileOperationError(
            "write to the output folder",
            blocked,
            "ffmpeg.exe",
            main.ProcessError("Error opening output file: Permission denied"),
        )
        boxes = []
        with (
            patch("main.sys.platform", "win32"),
            patch.object(main.QMessageBox, "exec", lambda box: boxes.append(box) or 0),
        ):
            self.window.show_error(error)
        self.assertEqual(boxes[0].text(), "File access blocked")
        self.assertIn("ffmpeg.exe could not write", boxes[0].informativeText())
        self.assertIn(str(blocked), boxes[0].informativeText())
        self.assertIn("Permission denied", boxes[0].informativeText())
        self.assertNotIn("larger file size", boxes[0].informativeText())

    def test_non_access_encoder_error_is_not_reported_as_defender(self):
        boxes = []
        self.window.worker = main.CompressionWorker(
            Path(self.folder.name) / "input.mp4",
            Path(self.folder.name) / "output.mp4",
            main.SOFTWARE,
        )
        with (
            patch("main.sys.platform", "win32"),
            patch.object(main.QMessageBox, "exec", lambda box: boxes.append(box) or 0),
        ):
            self.window.show_error(
                main.ProcessError("Error writing packet: encoder access is denied")
            )
        self.assertEqual(boxes[0].text(), "Compression failed")
        self.assertIn("encoder access is denied", boxes[0].informativeText())
        self.assertFalse(
            any(b.text() == "Open Windows Security" for b in boxes[0].buttons())
        )
        self.window.worker = None

    def test_why_cpu_shows_and_copies_the_real_gpu_error(self):
        error = "hevc_nvenc:\nOpenEncodeSessionEx failed: error 10"
        self.window.set_gpu_diagnostic(error)
        self.assertFalse(self.window.encoding_help_button.isHidden())
        boxes = []

        def copy_error(box):
            boxes.append(box)
            box._selected_for_test = next(
                b for b in box.buttons() if b.text() == "Copy error"
            )
            return 0

        with (
            patch.object(main.QMessageBox, "exec", copy_error),
            patch.object(
                main.QMessageBox, "clickedButton", lambda box: box._selected_for_test
            ),
        ):
            self.window.encoding_help_button.click()
        self.assertIn("error 10", boxes[0].informativeText())
        self.assertIn(error, self.app.clipboard().text())
        self.assertIn(self.window.version, self.app.clipboard().text())
        self.window.set_hardware(main.Hardware("hevc_nvenc", "NVIDIA"))
        self.assertTrue(self.window.encoding_help_button.isHidden())

    def test_blocked_initial_file_read_does_not_escape_the_gui_slot(self):
        boxes = []
        with (
            patch("main.sys.platform", "win32"),
            patch.object(
                Path, "is_file", side_effect=PermissionError(13, "Permission denied")
            ),
            patch.object(main.QMessageBox, "exec", lambda box: boxes.append(box) or 0),
        ):
            self.window.load_video(str(Path(self.folder.name) / "blocked.mp4"))
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].text(), "File access blocked")
        self.assertIsNone(self.window.source)

    def test_blocked_save_path_is_reported_before_encoding(self):
        self.window.source = Path(self.folder.name) / "clip.mp4"
        self.window.set_media(main.MediaInfo(30, False, 0, 640, 360))
        boxes = []
        with (
            patch("main.sys.platform", "win32"),
            patch.object(
                Path, "exists", side_effect=PermissionError(13, "Permission denied")
            ),
            patch.object(main.QMessageBox, "exec", lambda box: boxes.append(box) or 0),
            patch.object(self.window, "launch") as launch,
        ):
            self.window.start_compression()
        self.assertEqual(boxes[0].text(), "File access blocked")
        launch.assert_not_called()

    def test_mac_app_menu_and_header_buttons_use_the_same_actions(self):
        with (
            patch.object(main.sys, "platform", "darwin"),
            patch.object(main.QApplication, "platformName", return_value="cocoa"),
            patch.object(main.DetectionWorker, "start"),
            patch.object(main.UpdateChecker, "check") as check,
        ):
            window = main.MainWindow(self.settings)
            window.job_finished()
            native_menu = window.menuBar().actions()[0].menu()
            self.assertEqual(
                native_menu.actions(), [window.about_action, window.update_action]
            )
            window.show()
            self.app.processEvents()
            QTest.mouseClick(window.update_button, main.Qt.MouseButton.LeftButton)
            check.assert_called_once()
            boxes = []
            with patch.object(
                main.QMessageBox, "exec", lambda box: boxes.append(box) or 0
            ):
                window.about_action.trigger()
                QTest.mouseClick(window.about_button, main.Qt.MouseButton.LeftButton)
            self.assertEqual(len(boxes), 2)
            window.update_check_state(True)
            self.assertFalse(window.update_button.isEnabled())
            self.assertFalse(window.update_action.isEnabled())
            window.update_check_state(False)
            self.assertTrue(window.update_button.isEnabled())
            self.assertTrue(window.update_action.isEnabled())
            window.close()
            window.deleteLater()

    def test_update_results_do_not_change_compression_progress(self):
        self.window._started_at = time.monotonic()
        self.window.elapsed_timer.start()
        self.window.progress.setValue(42)
        boxes = []
        with patch.object(main.QMessageBox, "exec", lambda box: boxes.append(box) or 0):
            self.window.show_update_error(
                "Check your internet connection and try again."
            )
            self.window.show_update_result(
                main.ReleaseInfo("0.0.2", main.RELEASES_URL, True)
            )
        self.assertEqual(len(boxes), 2)
        self.assertTrue(self.window.elapsed_timer.isActive())
        self.assertEqual(self.window.progress.value(), 42)
        self.window.update_check_state(True)
        self.assertFalse(self.window.update_action.isEnabled())
        self.assertTrue(self.window.about_action.isEnabled())
        self.window.update_check_state(False)
        self.assertTrue(self.window.update_action.isEnabled())

    def test_old_default_is_moved_to_the_new_preset(self):
        self.settings.remove("default_target_mb")
        self.settings.setValue("target_mb", 9.5)
        with patch.object(main.DetectionWorker, "start"):
            upgraded = main.MainWindow(self.settings)
        upgraded.job_finished()
        self.assertEqual(upgraded.target_size.value(), 19.5)
        self.assertTrue(upgraded.preset_buttons[19.5].property("selected"))
        self.assertEqual(float(self.settings.value("target_mb")), 19.5)
        self.assertEqual(upgraded.size_hint.text(), "Discord default: 19.5 MB")
        upgraded.close()
        upgraded.deleteLater()

    def test_upgrade_preserves_other_custom_limits(self):
        for size in (3.0, 25.0, 50.0):
            with self.subTest(size=size):
                self.settings.remove("default_target_mb")
                self.settings.setValue("target_mb", size)
                with patch.object(main.DetectionWorker, "start"):
                    upgraded = main.MainWindow(self.settings)
                upgraded.job_finished()
                self.assertEqual(upgraded.target_size.value(), size)
                upgraded.close()
                upgraded.deleteLater()

    def test_legacy_size_can_be_saved_as_a_custom_limit_after_upgrade(self):
        self.window.target_size.setValue(9.5)
        with patch.object(main.DetectionWorker, "start"):
            reopened = main.MainWindow(self.settings)
        reopened.job_finished()
        self.assertEqual(reopened.target_size.value(), 9.5)
        self.assertEqual(reopened.size_hint.text(), "File size limit: 9.5 MB")
        reopened.close()
        reopened.deleteLater()

    def test_increasing_limit_keeps_the_clip_and_enables_compression(self):
        info = main.MediaInfo(3600, True, 0, 640, 360)
        self.window.set_media(info)
        self.window.update_controls()
        self.assertFalse(self.window.start_button.isEnabled())
        self.window.target_size.setValue(100.0)
        self.assertIs(self.window.info, info)
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertEqual(float(self.settings.value("target_mb")), 100.0)

    def test_selected_size_is_passed_to_the_worker(self):
        source = Path(self.folder.name) / "clip.mp4"
        source.write_bytes(b"input")
        self.window.source = source
        self.window.set_media(main.MediaInfo(60, True, 0, 640, 360))
        self.window.target_size.setValue(25.0)
        output = Path(self.folder.name) / "smaller.mp4"
        with (
            patch.object(
                main.QFileDialog, "getSaveFileName", return_value=(str(output), "")
            ),
            patch.object(self.window, "launch") as launch,
        ):
            self.window.start_compression()
        worker = launch.call_args.args[0]
        self.assertEqual(worker.target_mb, 25.0)
        self.window.target_size.setValue(50.0)
        self.assertEqual(worker.target_mb, 25.0)

    def test_buttons_and_drop_area_work_after_resizes_and_theme_changes(self):
        self.window.show()
        with patch.object(
            main.QFileDialog, "getOpenFileName", return_value=("", "")
        ) as picker:
            for width, height in ((900, 620), (1280, 850), (1060, 740), (1500, 900)):
                self.window.resize(width, height)
                self.app.processEvents()
                for button in self.window.preset_buttons.values():
                    self.assertLess(
                        self.window.target_size.geometry().bottom(),
                        button.geometry().top(),
                        "Size buttons must not overlap the size field after a resize",
                    )
                for size in (25.0, 50.0, main.DEFAULT_TARGET_MB):
                    QTest.mouseClick(
                        self.window.preset_buttons[size], main.Qt.MouseButton.LeftButton
                    )
                    self.assertEqual(self.window.target_size.value(), size)
                    self.assertEqual(
                        [
                            s
                            for s, button in self.window.preset_buttons.items()
                            if button.property("selected")
                        ],
                        [size],
                    )
                old_theme = self.window.theme_name
                QTest.mouseClick(
                    self.window.theme_switch, main.Qt.MouseButton.LeftButton
                )
                self.app.processEvents()
                self.assertNotEqual(self.window.theme_name, old_theme)
                calls = picker.call_count
                QTest.mouseClick(
                    self.window.drop.thumbnail, main.Qt.MouseButton.LeftButton
                )
                QTest.mouseClick(
                    self.window.drop, main.Qt.MouseButton.LeftButton, pos=QPoint(5, 5)
                )
                self.assertEqual(picker.call_count, calls + 2)

    def test_drop_area_opens_picker_with_keyboard_and_after_thumbnail_load(self):
        self.window.show()
        self.app.processEvents()
        selected = []
        self.window.drop.selected.disconnect(self.window.load_video)
        self.window.drop.selected.connect(selected.append)
        path = str(Path(self.folder.name) / "a video.mp4")
        image = main.QPixmap(160, 90)
        image.fill(main.QColor("#333333"))
        self.window.drop.thumbnail.set_image(image)
        with patch.object(
            main.QFileDialog, "getOpenFileName", return_value=(path, "")
        ) as picker:
            QTest.mouseClick(self.window.drop.thumbnail, main.Qt.MouseButton.LeftButton)
            QTest.keyClick(self.window.drop.thumbnail, main.Qt.Key.Key_Return)
            QTest.keyClick(self.window.drop.thumbnail, main.Qt.Key.Key_Space)
            QTest.keyClick(self.window.drop, main.Qt.Key.Key_Return)
        self.assertEqual(selected, [path] * 4)
        self.assertEqual(picker.call_count, 4)

    def test_light_default_two_themes_and_saved_dark_switch(self):
        self.assertEqual(set(main.THEMES), {"frost", "ink"})
        self.assertEqual(self.window.theme_name, "frost")
        self.assertFalse(self.window.theme_switch.isChecked())
        self.window.show()
        QTest.keyClick(self.window.theme_switch, main.Qt.Key.Key_Space)
        self.assertEqual(self.window.theme_name, "ink")
        self.assertTrue(self.settings.value("dark_mode", type=bool))
        with patch.object(main.DetectionWorker, "start"):
            restored = main.MainWindow(self.settings)
        restored.job_finished()
        self.assertTrue(restored.theme_switch.isChecked())
        self.assertEqual(restored.theme_name, "ink")
        restored.close()
        restored.deleteLater()

    def test_theme_change_does_not_replace_the_base_style(self):
        with patch.object(self.app, "setStyle") as set_style:
            for theme in ("ink", "frost", "ink", "frost"):
                main.apply_theme(self.app, theme)
        set_style.assert_not_called()

    def test_progress_and_elapsed_time_are_shown_and_kept_in_the_result(self):
        source, output = (
            Path(self.folder.name) / "input.mp4",
            Path(self.folder.name) / "output.mp4",
        )
        source.write_bytes(b"input")
        self.window.source = source
        self.window.set_media(main.MediaInfo(60, True, 0, 640, 360))
        self.window.show()
        with (
            patch.object(
                main.QFileDialog, "getSaveFileName", return_value=(str(output), "")
            ),
            patch.object(main.CompressionWorker, "start"),
            patch("main.time.monotonic", return_value=100.0),
        ):
            self.window.start_compression()
        self.window.worker.progress.emit(42)
        with patch("main.time.monotonic", return_value=103.5):
            self.window.update_elapsed()
            self.assertEqual(self.window.progress.value(), 42)
            self.assertEqual(self.window.percentage.text(), "42%")
            self.assertEqual(self.window.elapsed_label.text(), "Elapsed 3.5 s")
            self.assertTrue(self.window.elapsed_label.isVisible())
            self.window.job_completed(str(output), 2_200_000)
            self.window.job_finished()
        self.assertEqual(self.window.status_label.text(), "Saved 2.20 MB in 3.5 s")
        self.assertFalse(self.window.elapsed_timer.isActive())
        self.assertEqual(self.window.percentage.text(), "100%")
        self.assertTrue(self.window.percentage.isVisible())
        self.assertTrue(self.window.progress.isVisible())
        self.window.resize(900, 620)
        self.app.processEvents()
        self.assertEqual(self.window.status_label.text(), "Saved 2.20 MB in 3.5 s")

    def test_show_in_folder_uses_saved_output_after_resize(self):
        self.window.source = Path(self.folder.name) / "original.mp4"
        output = Path(self.folder.name) / "compressed, with spaces.mp4"
        output.write_bytes(b"result")
        self.window.job_completed(str(output), output.stat().st_size)
        self.window.update_controls()
        self.window.show()
        self.window.resize(1200, 850)
        self.app.processEvents()
        with patch.object(main.RevealWorker, "start"):
            QTest.mouseClick(self.window.open_button, main.Qt.MouseButton.LeftButton)
        self.assertIsInstance(self.window.worker, main.RevealWorker)
        self.assertEqual(self.window.worker.path, output)
        self.window.job_finished()


class RevealTests(unittest.TestCase):
    def test_platform_reveal_uses_exact_file_and_preserves_special_characters(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "saved, clip;$(do not run) ü.mp4"
            path.write_bytes(b"result")
            for platform_name in ("darwin", "win32", "linux"):
                with self.subTest(platform=platform_name):
                    worker = main.RevealWorker(path)
                    revealed, errors = [], []
                    worker.revealed.connect(revealed.append)
                    worker.error.connect(errors.append)
                    with (
                        patch("main.sys.platform", platform_name),
                        patch("main.shutil.which", return_value="/usr/bin/dbus-send"),
                        patch.object(worker, "run_process", return_value="") as run,
                        patch.object(worker, "reveal_windows") as windows,
                    ):
                        worker.run()
                    self.assertEqual(errors, [])
                    self.assertEqual(revealed, [True])
                    if platform_name == "win32":
                        windows.assert_called_once_with(path)
                        run.assert_not_called()
                    elif platform_name == "darwin":
                        self.assertEqual(
                            run.call_args.args[0], ["/usr/bin/open", "-R", str(path)]
                        )
                    else:
                        self.assertIn(
                            "array:string:" + path.as_uri(), run.call_args.args[0]
                        )
                        self.assertIn(
                            "org.freedesktop.FileManager1.ShowItems",
                            run.call_args.args[0],
                        )

    def test_linux_falls_back_when_selection_is_not_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.mp4"
            path.write_bytes(b"result")
            worker = main.RevealWorker(path)
            revealed = []
            worker.revealed.connect(revealed.append)
            with (
                patch("main.sys.platform", "linux"),
                patch("main.shutil.which", return_value="/usr/bin/dbus-send"),
                patch.object(
                    worker, "run_process", side_effect=main.ProcessError("No service")
                ),
            ):
                worker.run()
            self.assertEqual(revealed, [False])

    def test_missing_output_reports_error_instead_of_selecting_another_file(self):
        worker = main.RevealWorker(
            Path(tempfile.gettempdir()) / "missing-compressed-clip.mp4"
        )
        errors, revealed = [], []
        worker.error.connect(errors.append)
        worker.revealed.connect(revealed.append)
        with patch.object(worker, "run_process") as run:
            worker.run()
        self.assertTrue(errors)
        self.assertEqual(revealed, [])
        run.assert_not_called()


class WindowsProcessTests(unittest.TestCase):
    def test_restore_failure_does_not_leave_a_child_running(self):
        setter = MagicMock(side_effect=[1, 0])
        child = MagicMock(stdout=io.StringIO("output\n"))
        child.poll.return_value = None
        runner = main.ProcessThread()
        with (
            patch("main.sys.platform", "win32"),
            patch("main.sys.frozen", True, create=True),
            patch("main.sys._MEIPASS", "C:\\Compressor\\_internal", create=True),
            patch(
                "main.ctypes.WinDLL",
                return_value=MagicMock(SetDllDirectoryW=setter),
                create=True,
            ),
            patch(
                "main.ctypes.WinError",
                return_value=OSError("Restore failed"),
                create=True,
            ),
            patch("main.subprocess.CREATE_NO_WINDOW", 0, create=True),
            patch("main.subprocess.Popen", return_value=child),
            self.assertRaisesRegex(OSError, "Restore failed"),
        ):
            runner.run_process(["ffmpeg.exe"])
        child.kill.assert_called_once()
        child.wait.assert_called_once()
        self.assertTrue(child.stdout.closed)
        self.assertIsNone(runner._process)

    def test_child_uses_system_dll_paths_and_parent_path_is_restored(self):
        setter = MagicMock(return_value=1)
        kernel = MagicMock(SetDllDirectoryW=setter)
        child = MagicMock(stdout=io.StringIO("output\n"))
        child.wait.return_value = 0
        child.poll.return_value = 0

        def spawn(*args, **kwargs):
            self.assertEqual(setter.call_args.args, (None,))
            return child

        with (
            patch("main.sys.platform", "win32"),
            patch("main.sys.frozen", True, create=True),
            patch("main.sys._MEIPASS", "C:\\Compressor\\_internal", create=True),
            patch("main.ctypes.WinDLL", return_value=kernel, create=True),
            patch("main.subprocess.CREATE_NO_WINDOW", 0, create=True),
            patch("main.subprocess.Popen", side_effect=spawn),
        ):
            self.assertEqual(main.ProcessThread().run_process(["ffmpeg.exe"]), "output")
        self.assertEqual(
            [call.args for call in setter.call_args_list],
            [(None,), ("C:\\Compressor\\_internal",)],
        )

    def test_parent_dll_path_is_restored_when_launch_fails(self):
        setter = MagicMock(return_value=1)
        with (
            patch("main.sys.platform", "win32"),
            patch("main.sys.frozen", True, create=True),
            patch("main.sys._MEIPASS", "C:\\Compressor\\_internal", create=True),
            patch(
                "main.ctypes.WinDLL",
                return_value=MagicMock(SetDllDirectoryW=setter),
                create=True,
            ),
            self.assertRaises(OSError),
        ):
            with main.system_dll_directory():
                raise OSError("Could not start the child.")
        self.assertEqual(setter.call_args.args, ("C:\\Compressor\\_internal",))

    def test_gpu_driver_errors_are_not_reported_as_folder_blocks(self):
        self.assertFalse(
            main.is_file_access_error(
                "[hevc_nvenc] OpenEncodeSessionEx failed: access is denied"
            )
        )
        self.assertTrue(main.is_file_access_error("[WinError 5] Zugriff verweigert"))
        self.assertTrue(
            main.is_file_access_error("Error opening input files: Permission denied")
        )
        self.assertFalse(
            main.is_file_access_error(
                "[hevc_nvenc] OpenEncodeSessionEx failed: access is denied\n"
                "Error while opening encoder - maybe incorrect parameters\n"
                "Error opening output files: Permission denied"
            )
        )


class ProcessTests(unittest.TestCase):
    def test_timeout_kills_process(self):
        worker = main.ProcessThread()
        started = time.monotonic()
        with self.assertRaisesRegex(main.ProcessError, "time limit"):
            worker.run_process(
                [sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.1
            )
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(worker._process)

    def test_cancel_unblocks_a_silent_child(self):
        worker = main.ProcessThread()
        failures = []

        def run():
            try:
                worker.run_process(
                    [sys.executable, "-c", "import time; time.sleep(20)"]
                )
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 5
        while worker._process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        worker.cancel()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], main.Cancelled)


if __name__ == "__main__":
    unittest.main()
