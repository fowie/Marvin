"""Consolidated offline manager and localhost API checks."""

import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import shutil

import marvin_managers
import marvin_operator


def wave_bytes(data=b"\0\0"):
    fmt = struct.pack("<HHIIHH", 1, 8, 16000, 256000, 16, 16)
    chunks = b"fmt " + len(fmt).to_bytes(4, "little") + fmt
    chunks += b"data" + len(data).to_bytes(4, "little") + data
    return b"RIFF" + (len(chunks) + 4).to_bytes(4, "little") + b"WAVE" + chunks


class ManagerTests(unittest.TestCase):
    def test_drive_and_led_state_cleanup(self):
        now = [10.0]
        pulses, stops = [], []
        drive = marvin_managers.DriveManager(
            pulses.append, lambda: stops.append("stop"), clock=lambda: now[0])
        drive.start()
        lease = drive.action("acquire", {})["lease"]
        drive.action("heartbeat_forward", {
            "lease": lease, "requested_at": now[0]})
        self.assertEqual(pulses, ["forward"])
        now[0] += marvin_managers.DEADMAN_LEASE_SECONDS
        self.assertTrue(drive.tick(now[0]))
        self.assertEqual(stops, ["stop"])
        fixed = drive.action("fixed_rotate-left", {})
        self.assertEqual(fixed["completed_pulses"], 4)
        self.assertEqual(pulses[-4:], ["rotate-left"] * 4)
        drive.close()
        self.assertEqual(stops[-1], "stop")

        attempts = []
        def fail_second(direction):
            attempts.append(direction)
            if len(attempts) == 2:
                raise OSError("bounded action failed")
        failed = marvin_managers.DriveManager(
            fail_second, lambda: stops.append("failure-stop"))
        failed.start()
        with self.assertRaisesRegex(OSError, "bounded action failed"):
            failed.action("fixed_forward", {})
        self.assertEqual(attempts, ["forward", "forward"])
        self.assertEqual(stops[-1], "failure-stop")

        baseline = bytes.fromhex("000000000000000000000000000000ff0000")
        writes = []
        leds = marvin_managers.LedManager(lambda: baseline, writes.append)
        leds.start()
        leds.action("on_left_position_0_red", {})
        self.assertEqual(writes[-1][0], 255)
        self.assertEqual(writes[-1][15], 255)
        with self.assertRaisesRegex(ValueError, "Combined simultaneous"):
            leds.action("on_right_position_0_blue", {})
        with self.assertRaisesRegex(ValueError, "Only the active"):
            leds.action("off_right_position_0_blue", {})
        with self.assertRaisesRegex(ValueError, "Combined simultaneous"):
            leds.action("on_front_left_blue", {})
        leds.action("off_left_position_0_red", {})
        leds.action("on_right_position_0_blue", {})
        leds.close()
        self.assertEqual(writes[-1], baseline)

    def test_real_localhost_manager_routes_and_events(self):
        pulses, stops = [], []
        drive = marvin_managers.DriveManager(
            pulses.append, lambda: stops.append("stop"))
        baseline = bytes.fromhex("000000000000000000000000000000ff0000")
        writes = []
        runtime = marvin_operator.OperatorRuntime(
            None, managers={
                "drive": drive,
                "leds": marvin_managers.LedManager(lambda: baseline, writes.append),
            })
        server = marvin_operator.OperatorServer(("127.0.0.1", 0), runtime)
        runtime.start()
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def post(path, body):
            request = Request(
                base + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            return json.load(urlopen(request, timeout=3))

        try:
            lease = post("/api/drive/acquire", {})["drive"]["lease"]
            post("/api/drive/heartbeat/forward", {"lease": lease})
            post("/api/drive/release", {"lease": lease})
            fixed = post("/api/drive/fixed/rotate-right", {})
            self.assertEqual(fixed["drive"]["completed_pulses"], 4)
            post("/api/leds/bottom-green/on", {})
            led_status = json.load(urlopen(base + "/api/leds", timeout=2))
            self.assertEqual(led_status["active_channel"], "bottom-green")
            with self.assertRaises(HTTPError) as error:
                post("/api/drive/fixed/forward", {"duration": 1})
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            with urlopen(base + "/api/events", timeout=2) as events:
                payload = events.readline() + events.readline() + events.readline()
            self.assertIn(b"event: status", payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            runtime.close()
        self.assertEqual(pulses, ["forward"] + ["rotate-right"] * 4)
        self.assertEqual(writes[-1], baseline)
        self.assertGreaterEqual(len(stops), 2)

    def test_priority_stop_interrupts_fixed_action_after_current_pulse(self):
        entered, finish_pulse = Event(), Event()
        pulses, stops = [], []

        def pulse(direction):
            pulses.append(direction)
            entered.set()
            self.assertTrue(finish_pulse.wait(2))

        runtime = marvin_operator.OperatorRuntime(
            None, managers={
                "drive": marvin_managers.DriveManager(
                    pulse, lambda: stops.append("stop")),
            })
        runtime.start()
        fixed_result, stop_result = [], []
        fixed = Thread(target=lambda: fixed_result.append(
            runtime.manager_action("drive", "fixed_forward", {})))
        fixed.start()
        self.assertTrue(entered.wait(2))
        stop = Thread(target=lambda: stop_result.append(
            runtime.manager_action("drive", "stop", {})))
        stop.start()
        time.sleep(0.05)
        finish_pulse.set()
        fixed.join(2)
        stop.join(2)
        self.assertFalse(fixed.is_alive())
        self.assertFalse(stop.is_alive())
        self.assertEqual(pulses, ["forward"])
        self.assertEqual(stops, ["stop"])
        self.assertEqual(
            fixed_result[0]["result"], "cancelled_by_priority_stop")
        self.assertEqual(fixed_result[0]["completed_pulses"], 1)
        runtime.close()

    def test_real_subprocess_recorders_finalize_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def popen_with(payload, stderr=b"", exit_code=0):
                def start(argv, **options):
                    write_payload = (
                        "open(path,'wb').write(payload)"
                        if options["stdout"] == subprocess.DEVNULL
                        else "os.write(1,payload)"
                    )
                    script = (
                        "import os,signal,sys,time\n"
                        f"payload={payload!r}\n"
                        f"detail={stderr!r}\n"
                        f"path={str(argv[-1])!r}\n"
                        "def done(*_):\n"
                        f" {write_payload}\n"
                        f" os.write(2,detail); sys.exit({exit_code})\n"
                        "signal.signal(signal.SIGINT,done)\n"
                        "time.sleep(30)\n"
                    )
                    return subprocess.Popen(
                        [sys.executable, "-c", script],
                        stdin=options["stdin"], stdout=options["stdout"],
                        stderr=options["stderr"], close_fds=options["close_fds"],
                        pass_fds=options.get("pass_fds", ()))
                return start

            wave = wave_bytes()
            audio = marvin_managers.MicrophoneManager(
                device_check=lambda **_options: {"status": "ready"},
                popen=popen_with(wave))
            audio.start()
            wav = root / "operator.wav"
            audio.action("start", {
                "output": str(wav), "usb_path": "1-2.3",
                "privacy_authorized": True})
            time.sleep(0.1)
            audio.action("stop", {})
            self.assertEqual(wav.stat().st_mode & 0o777, 0o600)
            self.assertEqual(wav.stat().st_size, len(wave))

            candidate = {
                "node": "/dev/video-test", "rejected": False,
                "metadata_complete": True, "matches_topology": True,
                "supports_capture": True,
            }
            video = marvin_managers.CameraManager(
                inventory=lambda _path: [candidate],
                capture=lambda *args, **kwargs: {"status": "captured"},
                popen=popen_with(b"\x1aE\xdf\xa3fixture-video", exit_code=255))
            video.start()
            movie = root / "operator.mkv"
            video.action("start", {
                "output": str(movie), "usb_path": "1-2.3",
                "privacy_authorized": True})
            time.sleep(0.1)
            video.action("stop", {})
            self.assertEqual(movie.stat().st_mode & 0o777, 0o600)
            self.assertEqual(movie.read_bytes(), b"\x1aE\xdf\xa3fixture-video")
            self.assertIn("planned/unverified", video.status()["acceptance"])
            self.assertEqual(marvin_managers.VIDEO_DURATION_SECONDS, 299)

            corrupt = marvin_managers.CameraManager(
                inventory=lambda _path: [candidate],
                popen=popen_with(b"\x1aE\xdf\xa3partial", b"corrupt frame"))
            corrupt.start()
            bad = root / "bad.mkv"
            corrupt.action("start", {
                "output": str(bad), "usb_path": "1-2.3",
                "privacy_authorized": True})
            time.sleep(0.1)
            with self.assertRaisesRegex(OSError, "Invalid or incomplete"):
                corrupt.action("stop", {})
            self.assertFalse(bad.exists())
            self.assertEqual(list(root.glob(".bad.mkv.*.tmp")), [])

    def test_recorder_escalation_and_nonzero_exit_never_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wave = wave_bytes()

            def process(script):
                def start(argv, **options):
                    script_with_path = f"path={str(argv[-1])!r}\n" + script
                    return subprocess.Popen(
                        [sys.executable, "-c", script_with_path],
                        stdin=options["stdin"], stdout=options["stdout"],
                        stderr=options["stderr"], close_fds=options["close_fds"],
                        pass_fds=options.get("pass_fds", ()))
                return start

            nonzero = (
                "import os,signal,sys,time\n"
                f"payload={wave!r}\n"
                "def done(*_): open(path,'wb').write(payload); sys.exit(7)\n"
                "signal.signal(signal.SIGINT,done)\ntime.sleep(30)\n"
            )
            failed = marvin_managers.MicrophoneManager(
                device_check=lambda **_options: {}, popen=process(nonzero))
            failed.start()
            failed_output = root / "nonzero.wav"
            failed.action("start", {
                "output": str(failed_output), "usb_path": "1-2.3",
                "privacy_authorized": True})
            time.sleep(0.1)
            with self.assertRaisesRegex(OSError, "Invalid bounded WAV"):
                failed.action("stop", {})
            self.assertFalse(failed_output.exists())

            unexpected = (
                "import os,sys\n"
                f"open(path,'wb').write({wave!r})\n"
                "os.write(2,b'arecord: pcm_read: Interrupted system call\\n')\n"
                "sys.exit(1)\n"
            )
            unexpected_recorder = marvin_managers.MicrophoneManager(
                device_check=lambda **_options: {}, popen=process(unexpected))
            unexpected_recorder.start()
            unexpected_output = root / "unexpected.wav"
            unexpected_recorder.action("start", {
                "output": str(unexpected_output), "usb_path": "1-2.3",
                "privacy_authorized": True})
            time.sleep(0.1)
            with self.assertRaisesRegex(OSError, "Invalid bounded WAV"):
                unexpected_recorder.action("stop", {})
            self.assertFalse(unexpected_output.exists())

            malformed = (
                "import os,signal,sys,time\n"
                "def done(*_):\n"
                " open(path,'wb').write(b'RIFF\\x24\\0\\0\\0WAVEfmt ')\n"
                " os.write(2,b'arecord: pcm_read: Interrupted system call\\n')\n"
                " sys.exit(1)\n"
                "signal.signal(signal.SIGINT,done)\ntime.sleep(30)\n"
            )
            malformed_recorder = marvin_managers.MicrophoneManager(
                device_check=lambda **_options: {}, popen=process(malformed))
            malformed_recorder.start()
            malformed_output = root / "malformed.wav"
            malformed_recorder.action("start", {
                "output": str(malformed_output), "usb_path": "1-2.3",
                "privacy_authorized": True})
            time.sleep(0.1)
            with self.assertRaisesRegex(OSError, "Invalid bounded WAV"):
                malformed_recorder.action("stop", {})
            self.assertFalse(malformed_output.exists())

            scripts = {
                "terminate": (
                    "import signal,time\n"
                    "signal.signal(signal.SIGINT,signal.SIG_IGN)\n"
                    "time.sleep(30)\n"
                ),
                "kill": (
                    "import signal,time\n"
                    "signal.signal(signal.SIGINT,signal.SIG_IGN)\n"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                    "time.sleep(30)\n"
                ),
            }
            for outcome, script in scripts.items():
                recorder = marvin_managers.MicrophoneManager(
                    device_check=lambda **_options: {}, popen=process(script))
                recorder.start()
                output = root / f"{outcome}.wav"
                recorder.action("start", {
                    "output": str(output), "usb_path": "1-2.3",
                    "privacy_authorized": True})
                time.sleep(0.1)
                with self.subTest(outcome=outcome), patch.object(
                        marvin_managers, "PROCESS_STOP_SECONDS", 0.05), \
                        self.assertRaisesRegex(OSError, f"required {outcome}"):
                    recorder.action("stop", {})
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(f".{outcome}.wav.*.tmp")), [])

            candidate = {
                "node": "/dev/video-test", "rejected": False,
                "metadata_complete": True, "matches_topology": True,
                "supports_capture": True,
            }
            video_failures = {
                "nonzero": (
                    "import os,sys\n"
                    "os.write(1,b'\\x1aE\\xdf\\xa3complete-looking')\n"
                    "sys.exit(7)\n"
                ),
                "kill": scripts["kill"],
            }
            for outcome, script in video_failures.items():
                recorder = marvin_managers.CameraManager(
                    inventory=lambda _path: [candidate], popen=process(script))
                recorder.start()
                output = root / f"video-{outcome}.mkv"
                recorder.action("start", {
                    "output": str(output), "usb_path": "1-2.3",
                    "privacy_authorized": True})
                time.sleep(0.1)
                message = "required kill" if outcome == "kill" else "Invalid or incomplete"
                with self.subTest(video_outcome=outcome), patch.object(
                        marvin_managers, "PROCESS_STOP_SECONDS", 0.05), \
                        self.assertRaisesRegex(OSError, message):
                    recorder.action("stop", {})
                self.assertFalse(output.exists())
                self.assertEqual(
                    list(root.glob(f".video-{outcome}.mkv.*.tmp")), [])

    @unittest.skipUnless(shutil.which("arecord"), "arecord is not installed")
    def test_real_arecord_null_writes_complete_reserved_seekable_wave(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "null.wav"
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                completed = subprocess.run([
                    "arecord", "--device=null", "--file-type=wav",
                    "--format=S16_LE", "--rate=16000", "--channels=8",
                    "--duration=1", f"/proc/self/fd/{descriptor}",
                ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, timeout=5, check=False,
                    pass_fds=(descriptor,))
            finally:
                os.close(descriptor)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            data = output.read_bytes()
            self.assertEqual(data[:4], b"RIFF")
            self.assertEqual(data[8:12], b"WAVE")
            self.assertEqual(int.from_bytes(data[4:8], "little") + 8, len(data))
            self.assertIn(b"data", data[:44])


if __name__ == "__main__":
    unittest.main()
