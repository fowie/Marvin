"""Offline tests for the exact Marvin microphone subprocess boundary."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_microphone as microphone


STREAM = """Capture:
  Status: Stop
  Interface 2
    Altset 1
    Format: S16_LE
    Channels: 8
    Endpoint: 0x82 (2 IN) (SYNC)
    Rates: 16000
"""


class MicrophoneTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".microphone-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    @staticmethod
    def read_text(path):
        values = {
            "/sys/bus/usb/devices/1-1.1.2.4/idVendor": "045e\n",
            "/sys/bus/usb/devices/1-1.1.2.4/idProduct": "fff0\n",
            "/proc/asound/cards": (
                " 1 [Array          ]: USB-Audio - Microsoft Microphone Array\n"),
            "/proc/asound/Array/stream0": STREAM,
        }
        return values[str(path)]

    @staticmethod
    def resolve_path(path):
        usb = Path("/sys/devices/pci/usb1/1-1/1-1.1/1-1.1.2/1-1.1.2.4")
        if str(path).startswith("/sys/class/sound/"):
            return usb / "1-1.1.2.4:1.2" / "sound" / path.name
        return usb

    def test_import_and_default_status_are_offline(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("no subprocess")), \
                patch.object(Path, "read_text", side_effect=AssertionError("no device read")), \
                patch.object(Path, "is_file", return_value=True), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(microphone.main([]), 0)
        status = json.loads(output.getvalue())
        self.assertFalse(status["hardware_accessed"])
        self.assertEqual(status["device_readiness"], "not_checked")
        self.assertTrue(status["override_module_installed"])
        self.assertIn(microphone.KERNEL_FIX, status["kernel_prerequisite"])

    def test_live_list_requires_exact_explicit_selection_and_identity(self):
        listing = subprocess.CompletedProcess(
            [], 0, "card 1: Array [Microphone Array], device 0: USB Audio [USB Audio]\n", "")
        runner = Mock(return_value=listing)
        with self.assertRaises(ValueError):
            microphone.list_device(device=microphone.ALSA_DEVICE, read_text=self.read_text, runner=runner)
        with self.assertRaises(ValueError):
            microphone.list_device(device="default", run=True, read_text=self.read_text, runner=runner)
        with self.assertRaisesRegex(microphone.MicrophoneError, "override is missing"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, run=True, read_text=self.read_text,
                runner=runner, path_exists=lambda path: False)
        result = microphone.list_device(
            device=microphone.ALSA_DEVICE, run=True, read_text=self.read_text, runner=runner,
            path_exists=lambda path: True, resolve_path=self.resolve_path)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["usb_id"], microphone.USB_ID)
        self.assertEqual(result["alsa_card_index"], 1)
        runner.assert_called_once_with(
            ["arecord", "-l"], capture_output=True, text=True, timeout=3, check=False)

        def mismatched(path):
            if str(path).startswith("/sys/class/sound/"):
                return Path("/sys/devices/unrelated/sound") / path.name
            return self.resolve_path(path)

        with self.assertRaisesRegex(microphone.MicrophoneError, "not below reviewed USB"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, run=True, read_text=self.read_text,
                runner=Mock(), path_exists=lambda path: True,
                resolve_path=mismatched)
        with self.assertRaisesRegex(microphone.MicrophoneError, "exactly one ALSA card"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, run=True,
                read_text=lambda path: (
                    self.read_text(path)
                    + " 2 [Array          ]: USB-Audio - Other Array\n"
                    if str(path) == "/proc/asound/cards"
                    else self.read_text(path)),
                runner=Mock(), path_exists=lambda path: True,
                resolve_path=self.resolve_path)

    def test_capture_is_bounded_exclusive_and_surfaces_failures(self):
        duration, file_type = 1, "raw"
        limit = microphone.expected_bytes(duration, file_type)
        listing = subprocess.CompletedProcess(
            [], 0, "card 1: Array [Microphone Array], device 0: USB Audio [USB Audio]\n", "")
        successful_capture = subprocess.CompletedProcess([], 0, b"\1" * limit, b"")
        runner = Mock(side_effect=[listing, successful_capture])
        output = self.root / "capture.pcm"
        result = microphone.capture(
            output, device=microphone.ALSA_DEVICE, duration_seconds=duration,
            max_bytes=limit, file_type=file_type, run=True,
            authorize_audio_capture=True, read_text=self.read_text, runner=runner,
            path_exists=lambda path: True, resolve_path=self.resolve_path)
        self.assertEqual((result["bytes"], output.stat().st_size), (limit, limit))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        command = runner.call_args_list[1].args[0]
        self.assertIn("--device=hw:CARD=Array,DEV=0", command)
        self.assertIn("--duration=1", command)
        self.assertEqual(runner.call_args_list[1].kwargs["timeout"], 3)
        self.assertEqual(microphone.expected_bytes(5, "wav"), 1_280_044)
        with self.assertRaises(FileExistsError):
            microphone.capture(
                output, device=microphone.ALSA_DEVICE, duration_seconds=duration,
                max_bytes=limit, file_type=file_type, run=True,
                authorize_audio_capture=True, read_text=self.read_text, runner=Mock(),
                path_exists=lambda path: True, resolve_path=self.resolve_path)
        failed = Mock(side_effect=[
            listing, subprocess.CompletedProcess([], 1, b"", b"pcm_read: Input/output error")])
        with self.assertRaisesRegex(microphone.MicrophoneError, "pcm_read"):
            microphone.capture(
                self.root / "failed.pcm", device=microphone.ALSA_DEVICE,
                duration_seconds=duration, max_bytes=limit, file_type=file_type,
                run=True, authorize_audio_capture=True, read_text=self.read_text, runner=failed,
                path_exists=lambda path: True, resolve_path=self.resolve_path)
        self.assertFalse((self.root / "failed.pcm").exists())

    def test_cli_rejects_unbounded_or_unconsented_capture(self):
        for args in (
            ["capture", "--device", microphone.ALSA_DEVICE, "--duration", "5",
             "--max-bytes", "1280000", "--type", "raw", "--output", str(self.root / "a")],
            ["capture", "--run", "--authorize-audio-capture", "--device", microphone.ALSA_DEVICE,
             "--duration", "6", "--max-bytes", "1536000", "--type", "raw",
             "--output", str(self.root / "b")],
            ["capture", "--run", "--authorize-audio-capture", "--device", microphone.ALSA_DEVICE,
             "--duration", "5", "--max-bytes", "9999999", "--type", "raw",
             "--output", str(self.root / "c")],
        ):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(microphone.main(args), 1)


if __name__ == "__main__":
    unittest.main()
