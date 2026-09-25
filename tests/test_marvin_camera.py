"""Offline tests for exact-device LifeCam capture."""

from pathlib import Path
from contextlib import redirect_stdout
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import marvin
import marvin_camera


INFO = """Driver Info:
\tDriver name      : uvcvideo
\tCard type        : Microsoft LifeCam NX-3000
\tBus info         : usb-0000:00:14.0-2.3
"""
FORMATS = """ioctl: VIDIOC_ENUM_FMT
\tType: Video Capture
\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 352x288
"""


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.usb_root = self.root / "usb"
        self.video_root = self.root / "video4linux"
        self.dev_root = self.root / "dev"
        self.usb = self.usb_root / "1-2.3"
        self.usb.mkdir(parents=True)
        (self.usb / "idVendor").write_text("045e\n", encoding="ascii")
        (self.usb / "idProduct").write_text("0721\n", encoding="ascii")
        self.interface = self.usb / "1-2.3:1.0"
        self.interface.mkdir()
        self.video_root.mkdir()
        self.dev_root.mkdir()
        self.add_node("video14")

    def tearDown(self):
        self.temporary.cleanup()

    def add_node(self, name):
        video = self.video_root / name
        video.mkdir()
        (video / "device").symlink_to(self.interface, target_is_directory=True)

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] == "ffmpeg":
            return subprocess.CompletedProcess(
                argv, 0, b"\xff\xd8fixture\xff\xd9", b"")
        output = INFO if argv[-1] == "--all" else FORMATS
        return subprocess.CompletedProcess(argv, 0, output, "")

    def options(self):
        return {
            "sys_usb_root": self.usb_root,
            "sys_video_root": self.video_root,
            "dev_root": self.dev_root,
            "runner": self.runner,
        }

    def test_import_and_offline_plans_have_zero_host_access(self):
        root = Path(marvin_camera.__file__).parent
        script = """
import pathlib
import subprocess

def forbidden(*args, **kwargs):
    raise AssertionError("host boundary reached")

subprocess.run = forbidden
pathlib.Path.read_text = forbidden
pathlib.Path.glob = forbidden
import marvin_camera
assert marvin_camera.status()["hardware_access"] is False
assert marvin_camera.capture("new.jpg")["status"] == "offline_ready"
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=root, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = io.StringIO()
        with patch.object(
                marvin_camera, "inventory",
                side_effect=AssertionError("host boundary reached")), \
                redirect_stdout(output):
            self.assertEqual(
                marvin.main(["camera", "capture", "private/new.jpg"]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "offline_ready")

    def test_exact_inventory_and_single_frame_argv(self):
        self.calls = []
        devices = marvin_camera.inventory("1-2.3", **self.options())
        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0]["matches_topology"])
        self.assertTrue(devices[0]["supports_capture"])
        node = str(self.dev_root / "video14")
        self.assertEqual(
            [call[0] for call in self.calls],
            [
                ["v4l2-ctl", "--device", node, "--all"],
                ["v4l2-ctl", "--device", node, "--list-formats-ext"],
            ],
        )

        self.calls = []
        output = self.root / "frame.jpg"
        result = marvin_camera.capture(
            output, run=True, expected_usb_path="1-2.3",
            privacy_confirmed=True, **self.options())
        expected = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-input_format", "mjpeg",
            "-video_size", "352x288", "-i", node,
            "-frames:v", "1", "-an", "-c:v", "copy",
            "-fs", str(marvin_camera.MAX_CAPTURE_BYTES),
            "-f", "image2pipe", "pipe:1",
        ]
        self.assertEqual(self.calls[-1][0], expected)
        self.assertEqual(self.calls[-1][1]["timeout"], 10)
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["argv"], expected)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(output.read_bytes(), b"\xff\xd8fixture\xff\xd9")

    def test_identity_ambiguity_output_and_unsupported_nodes_refuse(self):
        self.calls = []
        (self.usb / "idProduct").write_text("4444\n", encoding="ascii")
        with self.assertRaisesRegex(OSError, "not the proved 045e:0721"):
            marvin_camera.inventory("1-2.3", **self.options())
        self.assertEqual(self.calls, [])
        (self.usb / "idProduct").write_text("0721\n", encoding="ascii")

        existing = self.root / "existing.jpg"
        existing.write_bytes(b"keep")
        with patch.object(
                marvin_camera, "inventory",
                side_effect=AssertionError("inventory must not run")):
            with self.assertRaises(FileExistsError):
                marvin_camera.capture(
                    existing, run=True, expected_usb_path="1-2.3",
                    privacy_confirmed=True)
        self.assertEqual(existing.read_bytes(), b"keep")

        candidate = {
            "node": "/dev/video14", "driver": "uvcvideo",
            "card": "Microsoft LifeCam NX-3000",
            "bus_info": "usb-host-2.3", "sysfs_usb_path": "1-2.3",
            "metadata_complete": True,
            "matches_topology": True, "supports_capture": True,
            "rejected": False,
        }
        for devices, message in (
                ([candidate, dict(candidate, node="/dev/video15")], "found 2"),
                ([dict(candidate, supports_capture=False)], "found 0"),
                ([dict(candidate, card="", metadata_complete=False)], "found 0"),
                ([dict(candidate, card="REAR CAM", rejected=True)], "found 0"),
                ([dict(candidate, driver="ipu3-cio2", rejected=True)], "found 0")):
            refused = self.root / f"refused-{len(devices)}.jpg"
            with self.subTest(devices=devices), \
                    patch.object(marvin_camera, "inventory", return_value=devices), \
                    self.assertRaisesRegex(OSError, message):
                marvin_camera.capture(
                    refused,
                    run=True, expected_usb_path="1-2.3",
                    privacy_confirmed=True)
            self.assertFalse(refused.exists())

    def test_privacy_timeout_and_subprocess_error_are_explicit(self):
        with patch.object(
                marvin_camera, "inventory",
                side_effect=AssertionError("inventory must not run")):
            with self.assertRaisesRegex(ValueError, "privacy_confirmed=True"):
                marvin_camera.capture(
                    self.root / "private.jpg", run=True,
                    expected_usb_path="1-2.3")

        self.calls = []

        def timeout(argv, **kwargs):
            if argv[0] == "ffmpeg":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return self.runner(argv, **kwargs)

        with self.assertRaisesRegex(TimeoutError, "10-second capture limit"):
            marvin_camera.capture(
                self.root / "timeout.jpg", run=True,
                expected_usb_path="1-2.3", privacy_confirmed=True,
                **{**self.options(), "runner": timeout})
        self.assertFalse((self.root / "timeout.jpg").exists())

        def failed(argv, **kwargs):
            if argv[0] == "ffmpeg":
                return subprocess.CompletedProcess(
                    argv, 23, b"", b"camera permission denied")
            return self.runner(argv, **kwargs)

        with self.assertRaisesRegex(OSError, "exit 23.*permission denied"):
            marvin_camera.capture(
                self.root / "failed.jpg", run=True,
                expected_usb_path="1-2.3", privacy_confirmed=True,
                **{**self.options(), "runner": failed})
        self.assertFalse((self.root / "failed.jpg").exists())

        def interrupted(argv, **kwargs):
            if argv[0] == "ffmpeg":
                raise KeyboardInterrupt
            return self.runner(argv, **kwargs)

        with self.assertRaises(KeyboardInterrupt):
            marvin_camera.capture(
                self.root / "interrupted.jpg", run=True,
                expected_usb_path="1-2.3", privacy_confirmed=True,
                **{**self.options(), "runner": interrupted})
        self.assertFalse((self.root / "interrupted.jpg").exists())
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_creation_boundaries_cannot_leak_on_keyboard_interrupt(self):
        self.calls = []

        def capture(name):
            output = self.root / name
            with self.assertRaises(KeyboardInterrupt):
                marvin_camera.capture(
                    output, run=True, expected_usb_path="1-2.3",
                    privacy_confirmed=True, **self.options())
            self.assertFalse(output.exists())
            self.assertEqual(list(self.root.glob(f".{name}.*.tmp")), [])

        mask_calls = 0

        def interrupt_after_destination_create(how, mask):
            nonlocal mask_calls
            mask_calls += 1
            if mask_calls == 2:
                raise KeyboardInterrupt
            return set()

        with patch.object(
                marvin_camera.signal, "pthread_sigmask",
                side_effect=interrupt_after_destination_create):
            capture("destination-boundary.jpg")

        real_open = marvin_camera.os.open
        def interrupt_during_stage_create(path, flags, mode):
            if str(path).endswith(".tmp"):
                raise KeyboardInterrupt
            return real_open(path, flags, mode)

        with patch.object(
                marvin_camera.os, "open",
                side_effect=interrupt_during_stage_create):
            capture("stage-during.jpg")

        mask_calls = 0

        def interrupt_after_stage_create(how, mask):
            nonlocal mask_calls
            mask_calls += 1
            if mask_calls == 4:
                raise KeyboardInterrupt
            return set()

        with patch.object(
                marvin_camera.signal, "pthread_sigmask",
                side_effect=interrupt_after_stage_create):
            capture("stage-after.jpg")


if __name__ == "__main__":
    unittest.main()
