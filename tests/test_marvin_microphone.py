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
            "/usb/1-1.1.3/idVendor": "0451\n",
            "/usb/1-1.1.3/idProduct": "2046\n",
            "/usb/1-1.1.3.4/idVendor": "045e\n",
            "/usb/1-1.1.3.4/idProduct": "fff0\n",
            "/sys/bus/usb/devices/1-1.1.3.4/idVendor": "045e\n",
            "/sys/bus/usb/devices/1-1.1.3.4/idProduct": "fff0\n",
            "/proc/asound/Array/stream0": STREAM,
        }
        return values[str(path)]

    def live_options(self):
        usb = Path("/usb")
        microphone_node = Path("/devices/usb/1-1.1.3.4")
        return {
            "route": "marvin-internal",
            "hub_path": "1-1.1.3",
            "read_text": self.read_text,
            "path_exists": lambda path: True,
            "usb_root": usb,
            "sound_root": Path("/sound"),
            "list_entries": lambda root: [
                usb / "1-1.1.3", Path("/sys/bus/usb/devices/1-1.1.3.4")],
            "resolve_path": lambda path: (
                microphone_node / "1-1.1.3.4:1.2/sound/card1/pcmC1D0c"
                if path == Path("/sound/pcmC1D0c/device")
                else microphone_node / "1-1.1.3.4:1.1"
                if path == Path("/sound/card1/device")
                else microphone_node),
        }

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
            microphone.list_device(device=microphone.ALSA_DEVICE, runner=runner, **self.live_options())
        with self.assertRaises(ValueError):
            microphone.list_device(device="default", run=True, runner=runner, **self.live_options())
        with self.assertRaisesRegex(microphone.MicrophoneError, "override is missing"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, run=True, runner=runner,
                **(self.live_options() | {"path_exists": lambda path: False}))
        result = microphone.list_device(
            device=microphone.ALSA_DEVICE, run=True, runner=runner, **self.live_options())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["usb_id"], microphone.USB_ID)
        self.assertEqual(result["route"], "marvin-internal")
        self.assertEqual(result["hub_usb_id"], "0451:2046")
        self.assertEqual(result["usb_path"], "1-1.1.3.4")
        self.assertEqual(result["alsa_card_node"], "card1")
        self.assertEqual(result["alsa_pcm_node"], "pcmC1D0c")
        runner.assert_called_once_with(
            ["arecord", "-l"], capture_output=True, text=True, timeout=3, check=False)

    def test_live_list_stops_on_topology_ambiguity_or_ancestry_mismatch(self):
        listing = subprocess.CompletedProcess(
            [], 0, "card 1: Array [Microphone Array], device 0: USB Audio [USB Audio]\n", "")
        options = self.live_options()
        duplicate = Path("/sys/bus/usb/devices/1-1.1.3.5")
        values = {
            "/usb/1-1.1.3/idVendor": "0451\n",
            "/usb/1-1.1.3/idProduct": "2046\n",
            "/sys/bus/usb/devices/1-1.1.3.4/idVendor": "045e\n",
            "/sys/bus/usb/devices/1-1.1.3.4/idProduct": "fff0\n",
            str(duplicate / "idVendor"): "045e\n",
            str(duplicate / "idProduct"): "fff0\n",
            "/proc/asound/Array/stream0": STREAM,
        }
        with self.assertRaisesRegex(microphone.MicrophoneError, "found 2"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, run=True, runner=Mock(return_value=listing),
                **(options | {
                    "read_text": lambda path: values[str(path)],
                    "list_entries": lambda root: options["list_entries"](root) + [duplicate],
                }))
        with self.assertRaisesRegex(microphone.MicrophoneError, "does not descend"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, run=True, runner=Mock(return_value=listing),
                **(options | {
                    "resolve_path": lambda path: (
                        Path("/devices/other/pcmC1D0c")
                        if path == Path("/sound/pcmC1D0c/device")
                        else Path("/devices/usb/1-1.1.3.4/1-1.1.3.4:1.1")
                        if path == Path("/sound/card1/device")
                        else Path("/devices/usb/1-1.1.3.4")),
                }))

    def test_routes_are_closed_and_validate_distinct_hub_identities(self):
        listing = subprocess.CompletedProcess(
            [], 0, "card 1: Array [Microphone Array], device 0: USB Audio [USB Audio]\n", "")
        with self.assertRaisesRegex(ValueError, "Route must be one of"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, route="arbitrary", hub_path="1-1.1.3",
                run=True, runner=Mock(return_value=listing), **{
                    key: value for key, value in self.live_options().items()
                    if key not in ("route", "hub_path")
                })
        with self.assertRaisesRegex(microphone.MicrophoneError, "requires hub 2109:2817"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, route="historical-external",
                hub_path="1-1.1.3", run=True, runner=Mock(return_value=listing), **{
                    key: value for key, value in self.live_options().items()
                    if key not in ("route", "hub_path")
                })

    def test_direct_host_requires_exact_path_without_hub(self):
        listing = subprocess.CompletedProcess(
            [], 0, "card 1: Array [Microphone Array], device 0: USB Audio [USB Audio]\n", "")
        options = {
            key: value for key, value in self.live_options().items()
            if key not in ("route", "hub_path")
        }
        with self.assertRaisesRegex(ValueError, "requires one USB device"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, route="direct-host",
                run=True, runner=Mock(return_value=listing), **options)
        with self.assertRaisesRegex(ValueError, "forbids hub_path"):
            microphone.list_device(
                device=microphone.ALSA_DEVICE, route="direct-host",
                hub_path="1-1.1.3", usb_path="1-2.4", run=True,
                runner=Mock(return_value=listing), **options)
        result = microphone.list_device(
            device=microphone.ALSA_DEVICE, route="direct-host",
            usb_path="1-1.1.3.4", run=True, runner=Mock(return_value=listing),
            **options)
        self.assertEqual(result["route"], "direct-host")
        self.assertNotIn("hub_path", result)
        self.assertEqual(result["usb_path"], "1-1.1.3.4")

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
            authorize_audio_capture=True, runner=runner, **self.live_options())
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
                authorize_audio_capture=True, runner=Mock(), **self.live_options())
        failed = Mock(side_effect=[
            listing, subprocess.CompletedProcess([], 1, b"", b"pcm_read: Input/output error")])
        with self.assertRaisesRegex(microphone.MicrophoneError, "pcm_read"):
            microphone.capture(
                self.root / "failed.pcm", device=microphone.ALSA_DEVICE,
                duration_seconds=duration, max_bytes=limit, file_type=file_type,
                run=True, authorize_audio_capture=True, runner=failed, **self.live_options())
        self.assertFalse((self.root / "failed.pcm").exists())
        interrupted = Mock(side_effect=[listing, KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            microphone.capture(
                self.root / "interrupted.pcm", device=microphone.ALSA_DEVICE,
                duration_seconds=duration, max_bytes=limit, file_type=file_type,
                run=True, authorize_audio_capture=True, runner=interrupted, **self.live_options())
        self.assertFalse((self.root / "interrupted.pcm").exists())

    def test_cli_rejects_unbounded_or_unconsented_capture(self):
        for args in (
            ["capture", "--device", microphone.ALSA_DEVICE, "--route", "marvin-internal",
             "--hub-path", "1-1.1.3", "--duration", "5",
             "--max-bytes", "1280000", "--type", "raw", "--output", str(self.root / "a")],
            ["capture", "--run", "--authorize-audio-capture", "--device", microphone.ALSA_DEVICE,
             "--route", "marvin-internal", "--hub-path", "1-1.1.3",
             "--duration", "6", "--max-bytes", "1536000", "--type", "raw",
             "--output", str(self.root / "b")],
            ["capture", "--run", "--authorize-audio-capture", "--device", microphone.ALSA_DEVICE,
             "--route", "marvin-internal", "--hub-path", "1-1.1.3",
             "--duration", "5", "--max-bytes", "9999999", "--type", "raw",
             "--output", str(self.root / "c")],
        ):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(microphone.main(args), 1)


if __name__ == "__main__":
    unittest.main()
