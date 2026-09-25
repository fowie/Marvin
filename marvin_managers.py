"""Stateful operator managers injected into the localhost runtime."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
from threading import Lock
import time

import marvin_camera
from marvin_leds import LEDS
from tools import marvin_legacy_drive_step as drive_step
from tools import marvin_microphone
from tools import marvin_paths


DEADMAN_LEASE_SECONDS = 0.75
MEDIA_LIMIT_SECONDS = 300
VIDEO_DURATION_SECONDS = MEDIA_LIMIT_SECONDS - 1
PROCESS_STOP_SECONDS = 2
_CORRUPT_VIDEO = ("corrupt", "invalid data", "ioctl(v4l2", "input/output error")


class DriveManager:
    """Compose only proved bounded drive and stop actions."""

    def __init__(self, drive_action, stop_action, *, clock=time.monotonic):
        self._drive = drive_action
        self._stop = stop_action
        self._clock = clock
        self._state = "new"
        self._owner = None
        self._direction = None
        self._lease_deadline = None
        self._last_error = None
        self._pulses = 0
        self._stop_lock = Lock()
        self._stop_requested = 0
        self._stop_completed = 0

    def start(self):
        if self._state != "new":
            raise RuntimeError("Drive manager start requires new state.")
        self._state = "ready"

    def action(self, name, values):
        now = self._clock()
        if self._state not in ("ready", "leased"):
            raise RuntimeError("Drive manager is not ready.")
        if name == "acquire":
            if values:
                raise ValueError("Drive acquire accepts no fields.")
            if self._owner is not None:
                raise ValueError("Drive dead-man already has an owner.")
            self._owner = secrets.token_urlsafe(24)
            self._state = "leased"
            self._lease_deadline = now + DEADMAN_LEASE_SECONDS
            return self.status() | {"lease": self._owner}
        if name == "release":
            self._require_lease(values)
            self._complete_stop(self.request_stop(), "released")
            return self.status()
        if name.startswith("heartbeat_"):
            direction = name.removeprefix("heartbeat_")
            stop_generation = values.pop("_stop_generation", self.movement_token())
            self._require_direction(direction)
            self._require_lease(values)
            if self._cancelled(stop_generation):
                self._complete_stop(self._requested_stop(), "priority_stop")
                return self.status() | {"result": "cancelled_before_pulse"}
            requested_at = values.get("requested_at")
            if type(requested_at) not in (int, float) or now - requested_at > DEADMAN_LEASE_SECONDS:
                self._complete_stop(self.request_stop(), "stale_heartbeat")
                raise ValueError("Heartbeat was not fresh; stop was requested.")
            try:
                self._drive(direction)
                self._pulses += 1
                if self._cancelled(stop_generation):
                    self._complete_stop(self._requested_stop(), "priority_stop")
                    return self.status() | {"result": "cancelled_after_pulse"}
                self._direction = direction
                self._lease_deadline = self._clock() + DEADMAN_LEASE_SECONDS
                self._state = "leased"
            except BaseException as error:
                self._fail_and_stop(error)
                raise
            return self.status()
        if name.startswith("fixed_"):
            direction = name.removeprefix("fixed_")
            stop_generation = values.pop("_stop_generation", self.movement_token())
            self._require_direction(direction)
            if values:
                raise ValueError("Fixed drive actions accept no fields.")
            if self._owner is not None:
                raise ValueError("Release the dead-man lease before fixed drive.")
            completed = 0
            try:
                for _ in range(4):
                    if self._cancelled(stop_generation):
                        self._complete_stop(self._requested_stop(), "priority_stop")
                        return self.status() | {
                            "result": "cancelled_by_priority_stop",
                            "completed_pulses": completed,
                        }
                    self._drive(direction)
                    completed += 1
                    self._pulses += 1
            except BaseException as error:
                self._fail_and_stop(error)
                raise
            return self.status() | {
                "result": "four_proved_250ms_pulses_completed",
                "completed_pulses": completed,
            }
        if name == "stop":
            generation = values.pop("_stop_generation", None)
            if values:
                raise ValueError("Stop accepts no fields.")
            if generation is None:
                generation = self.request_stop()
            self._complete_stop(generation, "explicit_stop")
            return self.status()
        raise ValueError("Unknown drive action.")

    def tick(self, now):
        if self._owner is not None and now >= self._lease_deadline:
            self._stop_now("lease_expired")
            return True
        return False

    def _require_direction(self, direction):
        if direction not in drive_step.DIRECTIONS:
            raise ValueError("Unsupported drive direction.")

    def _require_lease(self, values):
        if set(values) - {"lease", "requested_at"}:
            raise ValueError("Unexpected drive fields.")
        if self._owner is None or not secrets.compare_digest(
                str(values.get("lease", "")), self._owner):
            raise ValueError("The active dead-man lease is required.")

    def movement_token(self):
        with self._stop_lock:
            return self._stop_requested

    def request_stop(self):
        with self._stop_lock:
            self._stop_requested += 1
            return self._stop_requested

    def _requested_stop(self):
        with self._stop_lock:
            return self._stop_requested

    def _cancelled(self, movement_token):
        with self._stop_lock:
            return self._stop_requested > movement_token

    def _complete_stop(self, generation, reason):
        with self._stop_lock:
            if self._stop_completed >= generation:
                return
        self._stop_now(reason)
        with self._stop_lock:
            self._stop_completed = max(self._stop_completed, generation)

    def _stop_now(self, reason):
        try:
            self._stop()
        except BaseException as error:
            self._state = "failed"
            self._last_error = f"{type(error).__name__}: {error}"[:1024]
            self._owner = self._direction = self._lease_deadline = None
            raise
        self._state = "ready"
        self._owner = self._direction = self._lease_deadline = None
        self._last_error = None
        self._stop_reason = reason

    def _fail_and_stop(self, primary):
        message = f"{type(primary).__name__}: {primary}"[:1024]
        try:
            self._stop_now("action_error")
        except BaseException as cleanup:
            primary.add_note(f"Stop request also failed: {cleanup}")
        self._state = "failed"
        self._last_error = message

    def status(self):
        remaining = (
            None if self._lease_deadline is None else
            max(0.0, self._lease_deadline - self._clock())
        )
        return {
            "state": self._state,
            "mode": "deadman" if self._owner is not None else "idle",
            "direction": self._direction,
            "lease_seconds": DEADMAN_LEASE_SECONDS,
            "lease_remaining_seconds": remaining,
            "pulse_seconds": drive_step.DURATION_SECONDS,
            "fixed_action": "four sequential proved 250ms pulses; not calibrated distance or uninterrupted motion",
            "pulses_completed": self._pulses,
            "owner_active": self._owner is not None,
            "error": self._last_error,
        }

    def close(self):
        if self._state != "new":
            self._stop_now("manager_shutdown")
        self._state = "stopped"


class LedManager:
    """One captured baseline and one evidence-mapped LED at a time."""

    def __init__(self, read_state, write_state):
        self._read = read_state
        self._write = write_state
        self._state = "new"
        self._baseline = None
        self._payload = None
        self._active = None
        self._last_error = None

    def start(self):
        baseline = bytes(self._read())
        if len(baseline) != 18:
            raise OSError("Legacy LED baseline must be exactly 18 opaque bytes.")
        self._baseline = self._payload = baseline
        self._state = "ready"

    def action(self, name, values):
        if self._state != "ready":
            raise RuntimeError("LED manager is not ready.")
        if values:
            raise ValueError("LED actions accept no fields.")
        if name == "reset":
            self._restore()
            return self.status()
        prefix, enabled = (
            ("on_", True) if name.startswith("on_") else
            ("off_", False) if name.startswith("off_") else (None, None)
        )
        if prefix is None:
            raise ValueError("Unknown LED action.")
        channel = name.removeprefix(prefix).replace("_", "-")
        try:
            led = LEDS[channel]
        except KeyError:
            raise ValueError("Unknown evidence-mapped LED channel.") from None
        if not enabled and self._active != channel:
            raise ValueError("Only the active evidence-mapped LED channel can be turned off.")
        if enabled and self._active not in (None, channel):
            raise ValueError(
                "Combined simultaneous LED effects are not evidence-supported; "
                "turn off the active channel first.")
        payload = bytearray(self._payload)
        payload[led.index] = 255 if enabled else 0
        try:
            self._write(bytes(payload))
        except BaseException as error:
            primary = f"{type(error).__name__}: {error}"[:1024]
            try:
                self._restore()
            except BaseException as cleanup:
                error.add_note(f"Baseline restore also failed: {cleanup}")
                self._state = "failed"
            else:
                self._last_error = primary
            raise
        self._payload = bytes(payload)
        self._active = channel if enabled else None
        self._last_error = None
        return self.status()

    def tick(self, _now):
        return False

    def _restore(self):
        try:
            if self._baseline is not None:
                self._write(self._baseline)
                self._payload = self._baseline
        except BaseException as error:
            self._state = "failed"
            self._last_error = f"{type(error).__name__}: {error}"[:1024]
            raise
        self._active = None
        self._last_error = None
        self._state = "ready"

    def status(self):
        return {
            "state": self._state,
            "channels": {
                name: {
                    "index": led.index,
                    "value": None if self._payload is None else self._payload[led.index],
                    "observed_effect": led.observed_effect,
                }
                for name, led in LEDS.items()
            },
            "active_channel": self._active,
            "baseline_hex": None if self._baseline is None else self._baseline.hex(),
            "payload_hex": None if self._payload is None else self._payload.hex(),
            "combined_simultaneous_effects": "unsupported_by_existing_evidence",
            "raw80_raw82": "opaque",
            "error": self._last_error,
        }

    def close(self):
        try:
            if self._state != "new":
                self._restore()
        finally:
            self._state = "stopped"


class _RecorderManager:
    """Own one capped subprocess and atomically finalize successful output."""

    kind = None
    suffixes = ()

    def __init__(self, *, popen=subprocess.Popen, clock=time.monotonic):
        self._popen = popen
        self._clock = clock
        self._state = "new"
        self._process = None
        self._stream = None
        self._destination = None
        self._stage = None
        self._started = None
        self._elapsed = None
        self._error = None
        self._stderr = b""
        self._stderr_stream = None

    def start(self):
        self._state = "ready"

    def action(self, name, values):
        if name == "stop":
            if values:
                raise ValueError("Recorder stop accepts no fields.")
            self._finish(graceful=True)
            return self.status()
        if name != "start":
            raise ValueError("Unknown recorder action.")
        if self._process is not None:
            raise ValueError(f"{self.kind} recording is already active.")
        return self._start(values)

    def tick(self, now):
        if self._process is None:
            return False
        if self._process.poll() is not None:
            self._finish(graceful=False)
            return True
        if now - self._started >= MEDIA_LIMIT_SECONDS:
            self._finish(graceful=True)
            return True
        return False

    def _reserve(self, output):
        destination = marvin_paths.new_output_path(output)
        if destination.suffix.lower() not in self.suffixes:
            raise ValueError(
                f"{self.kind} output must use: {', '.join(self.suffixes)}.")
        owned = {"destination_fd": None, "destination_path": None,
                 "stage_fd": None, "stage_path": None}
        try:
            marvin_paths.create_private_output(destination, owned, "destination")
            os.close(owned["destination_fd"])
            owned["destination_fd"] = None
            stage = destination.with_name(
                f".{destination.name}.{secrets.token_hex(16)}.tmp")
            marvin_paths.create_private_output(stage, owned, "stage")
            return destination, stage, owned["stage_fd"]
        except BaseException:
            if owned["destination_fd"] is not None:
                os.close(owned["destination_fd"])
            if owned["stage_fd"] is not None:
                os.close(owned["stage_fd"])
            if owned["stage_path"] is not None:
                owned["stage_path"].unlink(missing_ok=True)
            if owned["destination_path"] is not None:
                owned["destination_path"].unlink(missing_ok=True)
            raise

    def _spawn(self, destination, stage, stage_fd, argv):
        stream = None
        diagnostic = None
        try:
            stream = os.fdopen(stage_fd, "wb")
            diagnostic = tempfile.TemporaryFile()
            process = self._popen(
                argv, stdin=subprocess.DEVNULL, stdout=stream,
                stderr=diagnostic, close_fds=True)
        except BaseException:
            if stream is None:
                os.close(stage_fd)
            else:
                stream.close()
            if diagnostic is not None:
                diagnostic.close()
            destination.unlink(missing_ok=True)
            stage.unlink(missing_ok=True)
            raise
        self._destination, self._stage = destination, stage
        self._stream, self._process = stream, process
        self._stderr_stream = diagnostic
        self._started = self._clock()
        self._elapsed = None
        self._state = "recording"
        self._error = None
        return self.status()

    def _stop_process(self):
        process = self._process
        if process.poll() is not None:
            return "exited"
        process.send_signal(signal.SIGINT)
        try:
            process.wait(PROCESS_STOP_SECONDS)
            return "sigint"
        except subprocess.TimeoutExpired:
            process.terminate()
        try:
            process.wait(PROCESS_STOP_SECONDS)
            return "terminate"
        except subprocess.TimeoutExpired:
            process.kill()
        process.wait(PROCESS_STOP_SECONDS)
        return "kill"

    def _finish(self, *, graceful):
        if self._process is None:
            if graceful:
                raise ValueError(f"{self.kind} recording is not active.")
            return
        process = self._process
        success = False
        try:
            stop_result = "natural"
            if graceful:
                stop_result = self._stop_process()
            else:
                process.wait(PROCESS_STOP_SECONDS)
            self._stderr_stream.seek(0)
            self._stderr = self._stderr_stream.read()
            self._stderr_stream.close()
            self._stderr_stream = None
            self._stream.close()
            self._stream = None
            if stop_result in ("terminate", "kill"):
                raise OSError(
                    f"{self.kind} recorder required {stop_result}; "
                    "staged output cannot be finalized.")
            self._validate_output(process.returncode, stop_result)
            os.replace(self._stage, self._destination)
            success = True
            self._state = "ready"
        except BaseException as error:
            self._state = "failed"
            self._error = f"{type(error).__name__}: {error}"[:1024]
            raise
        finally:
            if self._started is not None:
                self._elapsed = max(0.0, self._clock() - self._started)
                self._started = None
            if self._stream is not None:
                self._stream.close()
            if self._stderr_stream is not None:
                self._stderr_stream.close()
                self._stderr_stream = None
            if not success:
                self._destination.unlink(missing_ok=True)
                self._stage.unlink(missing_ok=True)
            self._process = self._stream = None
            self._stage = None

    def status(self):
        elapsed = self._elapsed if self._started is None else max(
            0.0, self._clock() - self._started)
        return {
            "state": self._state,
            "pid": None if self._process is None else self._process.pid,
            "elapsed_seconds": elapsed,
            "maximum_seconds": MEDIA_LIMIT_SECONDS,
            "output": None if self._destination is None else str(self._destination),
            "error": self._error,
        }

    def close(self):
        try:
            if self._process is not None:
                self._finish(graceful=True)
        finally:
            self._state = "stopped"


class MicrophoneManager(_RecorderManager):
    kind = "audio"
    suffixes = (".wav",)

    def __init__(self, *, device_check=marvin_microphone.list_device, **options):
        super().__init__(**options)
        self._device_check = device_check

    def _start(self, values):
        if set(values) != {"output", "usb_path", "privacy_authorized"}:
            raise ValueError("Audio start requires output, usb_path, and privacy_authorized.")
        if values["privacy_authorized"] is not True:
            raise ValueError("Audio recording requires literal privacy authorization.")
        self._device_check(
            device=marvin_microphone.ALSA_DEVICE, route="direct-host",
            usb_path=values["usb_path"], run=True)
        destination, stage, fd = self._reserve(values["output"])
        argv = [
            "arecord", "--quiet", f"--device={marvin_microphone.ALSA_DEVICE}",
            "--file-type=wav", f"--format={marvin_microphone.FORMAT}",
            f"--rate={marvin_microphone.RATE}",
            f"--channels={marvin_microphone.CHANNELS}",
            f"--duration={MEDIA_LIMIT_SECONDS}", "-",
        ]
        return self._spawn(destination, stage, fd, argv)

    def _validate_output(self, returncode, stop_result):
        size = self._stage.stat().st_size
        maximum = (
            MEDIA_LIMIT_SECONDS * marvin_microphone.RATE
            * marvin_microphone.CHANNELS * marvin_microphone.SAMPLE_BYTES + 44
        )
        detail = self._stderr.decode(errors="replace").lower()
        expected_sigint = (
            stop_result == "sigint" and returncode == 1
            and "pcm_read" in detail and "interrupted system call" in detail
        )
        if ((returncode and not expected_sigint) or not 44 <= size <= maximum
                or not self._complete_wave(size)):
            raise OSError(
                f"Invalid bounded WAV result (exit {returncode}, {size} bytes).")

    def _complete_wave(self, size):
        with self._stage.open("rb") as stream:
            header = stream.read(12)
            if (header[:4] != b"RIFF" or header[8:12] != b"WAVE"
                    or int.from_bytes(header[4:8], "little") + 8 != size):
                return False
            position = 12
            while position < size:
                stream.seek(position)
                chunk = stream.read(8)
                if len(chunk) != 8:
                    return False
                chunk_size = int.from_bytes(chunk[4:8], "little")
                end = position + 8 + chunk_size
                padded_end = end + (chunk_size & 1)
                if padded_end > size:
                    return False
                if chunk[:4] == b"data":
                    return chunk_size > 0 and padded_end == size
                position = padded_end
        return False


class CameraManager(_RecorderManager):
    kind = "video"
    suffixes = (".mkv",)

    def __init__(self, *, inventory=marvin_camera.inventory,
                 capture=marvin_camera.capture, **options):
        super().__init__(**options)
        self._inventory = inventory
        self._capture = capture

    def action(self, name, values):
        if name == "capture":
            if set(values) != {"output", "usb_path", "privacy_authorized"}:
                raise ValueError("Frame capture requires output, usb_path, and privacy_authorized.")
            return self._capture(
                values["output"], run=True, expected_usb_path=values["usb_path"],
                privacy_confirmed=values["privacy_authorized"])
        return super().action(name, values)

    def _start(self, values):
        if set(values) != {"output", "usb_path", "privacy_authorized"}:
            raise ValueError("Video start requires output, usb_path, and privacy_authorized.")
        if values["privacy_authorized"] is not True:
            raise ValueError("Video recording requires literal privacy authorization.")
        device = marvin_camera._select(
            self._inventory(values["usb_path"]), values["usb_path"])
        destination, stage, fd = self._reserve(values["output"])
        argv = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-f", "v4l2", "-input_format", marvin_camera.FFMPEG_FORMAT,
            "-video_size", marvin_camera.FRAME_SIZE, "-i", device["node"],
            "-t", str(VIDEO_DURATION_SECONDS), "-an", "-c:v", "copy",
            "-f", "matroska", "pipe:1",
        ]
        return self._spawn(destination, stage, fd, argv)

    def _validate_output(self, returncode, stop_result):
        detail = self._stderr.decode(errors="replace").lower()
        size = self._stage.stat().st_size
        with self._stage.open("rb") as stream:
            header = stream.read(4)
        expected_sigint = stop_result == "sigint" and returncode == 255
        if ((returncode and not expected_sigint) or header != b"\x1aE\xdf\xa3"
                or any(word in detail for word in _CORRUPT_VIDEO)):
            raise OSError(
                f"Invalid or incomplete V4L2 recording (exit {returncode}, "
                f"{size} bytes): {detail[-1000:] or 'no stderr'}")

    def status(self):
        return super().status() | {
            "acceptance": "direct-host planned/unverified until Jetson acceptance",
            "usb_identity": "045e:0721",
            "format": "MJPG",
            "size": marvin_camera.FRAME_SIZE,
        }


def build_managers(*, drive_action, stop_action, read_led_state, write_led_state,
                   microphone=None, camera=None):
    """Build the four named managers around production owner-bound callbacks."""
    return {
        "drive": DriveManager(drive_action, stop_action),
        "leds": LedManager(read_led_state, write_led_state),
        "microphone": MicrophoneManager() if microphone is None else microphone,
        "camera": CameraManager() if camera is None else camera,
    }
