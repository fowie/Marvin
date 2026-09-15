"""Shared-clock campaign/session/serial integration with no device access."""

from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_campaign as campaign
from tools import marvin_probe as probe
from tools import marvin_session as session


ROOT = Path(__file__).resolve().parents[1]
BASELINE = {
    "usb": {"usb_path": "/fake/usb", "busnum": 1, "devnum": 7,
            "descriptors_sha256": campaign.DESCRIPTOR_HASH},
    "tty": "offline", "tty_rdev": 1,
}


class SharedDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=".deadline-test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "capture"
        self.now = 1000.0
        self.deadline = None
        self.preflight_delays = []
        self.identity_delays = []
        self.device_delay = 0
        self.ready_delay = 0
        self.spawn_delay = 0
        self.open_delay = 0
        self.read_delay = 0
        self.write_delay = 0
        self.close_delay = 0
        self.shutdown_delay = 0
        self.write_error = None
        self.close_error = None
        self.ignore_stop = False
        self.received_chunks = []
        self.write_times = []
        self.read_timeouts = []
        self.process = Mock(returncode=None)
        self.process.poll.side_effect = self.poll
        self.process.wait.side_effect = self.wait
        self.transport = Mock(timeout=0.1, write_timeout=0.1)
        self.transport.open.side_effect = lambda: self.advance(self.open_delay)
        self.transport.read.side_effect = self.read
        self.transport.write.side_effect = self.write
        self.transport.close.side_effect = self.close
        self.ready_written = False
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(probe.time, "monotonic", side_effect=lambda: self.now))
        stack.enter_context(patch.object(session.time, "sleep", side_effect=self.advance))
        stack.enter_context(patch.object(session.os, "geteuid", return_value=1000))
        self.preflight = stack.enter_context(patch.object(session, "preflight", side_effect=self.inspect))
        self.identity = stack.enter_context(patch.object(session, "check_identity", side_effect=self.inspect_identity))
        self.device = stack.enter_context(patch.object(probe, "check_device", side_effect=self.inspect_device))
        self.ownership = stack.enter_context(patch.object(probe, "check_port_available"))
        self.serial = stack.enter_context(patch.object(probe.serial, "Serial", return_value=self.transport))
        self.ioctl = stack.enter_context(patch.object(probe.fcntl, "ioctl"))
        self.spawned = stack.enter_context(patch.object(subprocess, "Popen", side_effect=self.spawn))
        self.assess = stack.enter_context(patch.object(campaign, "assess_segment", side_effect=self.assess_segment))
        stack.enter_context(redirect_stdout(io.StringIO()))
        segment = {
            "id": "first", "baudrate": 115200, "bytesize": 8, "parity": "N", "stopbits": 1,
            "dtr": False, "rts": False,
            "steps": [{"id": f"query-{i}", "chunks_hex": ["0d"], "interval_seconds": 0,
                       "response_seconds": 0.2, "classification": "synthetic",
                       "rationale": "offline deadline regression"} for i in range(3)],
        }
        second = copy.deepcopy(segment)
        second["id"] = "second"
        self.plan = {"schema_version": 1, "segments": [segment, second]}
        self.budget = campaign.compile_segment(segment)[1] + 20 + campaign.USB_CLOSE_GRACE_SECONDS

    def advance(self, seconds):
        self.now += seconds

    def inspect(self, port, *, deadline=None):
        self.assertEqual(deadline, self.deadline)
        if self.preflight_delays:
            self.advance(self.preflight_delays.pop(0))
        return copy.deepcopy(BASELINE)

    def inspect_identity(self, *args):
        if self.identity_delays:
            self.advance(self.identity_delays.pop(0))

    def inspect_device(self, port, *, deadline=None):
        self.assertEqual(deadline, self.deadline)
        self.advance(self.device_delay)
        return {}

    def spawn(self, command, **kwargs):
        self.usb_output = Path(command[command.index("--output") + 1])
        self.usb_output.mkdir()
        seconds = float(command[command.index("--seconds") + 1])
        self.ready = dict(BASELINE["usb"], monotonic=self.now, seconds=seconds,
                          deadline_monotonic=self.now + seconds,
                          coordinator_stop="--coordinator-stop" in command,
                          coordinator_stop_file=session.marvin_usbmon.COORDINATOR_STOP_FILE)
        self.advance(self.spawn_delay)
        return self.process

    def usb_metadata(self, *, status, reason, interrupted=False):
        value = dict(self.ready, status=status, stop_reason=reason, identity=BASELINE["usb"],
                     started_monotonic=self.ready["monotonic"], stopped_monotonic=self.now,
                     signal=signal.SIGINT if interrupted else None,
                     monitor_final_stats={"queued": 0, "dropped": 0})
        (self.usb_output / "metadata.json").write_text(json.dumps(value))

    def poll(self):
        if self.process.returncode is not None:
            return self.process.returncode
        if not self.ready_written and self.now >= self.ready["monotonic"] + self.ready_delay:
            (self.usb_output / "ready.json").write_text(json.dumps(self.ready))
            self.ready_written = True
        stopped = (not self.ignore_stop
                   and (self.usb_output / session.marvin_usbmon.COORDINATOR_STOP_FILE).exists())
        if stopped or self.now >= self.ready["deadline_monotonic"]:
            self.usb_metadata(status="completed", reason="coordinator_stop" if stopped else "duration")
            self.process.returncode = 0
            return 0
        return None

    def wait(self, *, timeout):
        self.advance(self.shutdown_delay)
        self.usb_metadata(status="interrupted", reason="signal", interrupted=True)
        self.process.returncode = 1
        return 1

    def read(self, size):
        if self.deadline is not None:
            self.assertLess(self.now, self.deadline, "no new serial reads after expiry")
            self.assertLessEqual(self.transport.timeout, self.deadline - self.now)
        self.read_timeouts.append(self.transport.timeout)
        self.advance(self.transport.timeout + self.read_delay)
        return self.received_chunks.pop(0) if self.received_chunks else b""

    def write(self, data):
        self.assertLess(self.now, self.deadline, "no application write may start after expiry")
        self.assertLessEqual(self.transport.write_timeout, self.deadline - self.now)
        self.write_times.append((self.now, data))
        self.advance(self.write_delay)
        if self.write_error is not None:
            raise self.write_error
        return len(data)

    def close(self):
        self.advance(self.close_delay)
        if self.close_error is not None:
            raise self.close_error

    def assess_segment(self, directory, result, schedule):
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["serial"]["status"], "completed")
        self.assertEqual(result["serial"]["scheduled_writes_completed"], len(schedule))
        return {"outcome": "silent_out_confirmed",
                "usb_out_confirmed_bytes": result["serial"]["application_bytes_written"]}

    def run_campaign(self):
        self.deadline = self.now + self.budget
        return campaign.run_campaign(
            self.plan, self.output, port="offline", actuators_isolated=True,
            allow_unknown_command=True, allow_telemetry_state_change=True,
            allow_line_state_trials=True, switch_position="RUN", max_seconds=self.budget,
        )

    def run_capture(self, *, remaining=0.05, **options):
        self.deadline = self.now + remaining
        return probe.capture("offline", self.output, seconds=1, baudrate=115200,
                             max_bytes=65536, actuators_isolated=True, deadline=self.deadline, **options)

    def metadata(self, relative="metadata.json"):
        return json.loads((self.output / relative).read_text())

    def assert_stopped(self, result, *, segment=True):
        self.assertEqual(result["status"], "stopped_wall_limit")
        self.assertTrue(result["deadline_expired"])
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.assertFalse((self.output / "second").exists())
        if segment:
            self.assertEqual(result["segments"][0]["status"], "stopped_wall_limit")
        self.assertTrue(all(stamp < self.deadline for stamp, _ in self.write_times))

    def test_admission_then_slow_identity_stops_before_session(self):
        self.identity_delays = [self.budget]
        result = self.run_campaign()
        self.assert_stopped(result, segment=False)
        self.assertEqual(result["segments"], [])
        self.preflight.assert_called_once()
        self.spawned.assert_not_called()
        self.serial.assert_not_called()

    def test_initial_preflight_is_in_the_budget_and_expiry_creates_no_output(self):
        self.preflight_delays = [self.budget]
        with self.assertRaises(probe.DeadlineExpired):
            self.run_campaign()
        self.assertFalse(self.output.exists())
        self.identity.assert_not_called()
        self.spawned.assert_not_called()

    def test_session_preflight_shares_deadline_and_cannot_restart_the_clock(self):
        self.preflight_delays = [0, self.budget]
        result = self.run_campaign()
        self.assert_stopped(result)
        self.assertEqual(self.preflight.call_count, 2)
        self.assertEqual(result["segments"][0]["partial"]["serial_metadata"], "not_created")
        self.assertFalse((self.output / "first").exists())
        self.spawned.assert_not_called()

    def test_slow_process_launch_cannot_enable_serial_after_deadline(self):
        self.spawn_delay = self.budget + 1
        result = self.run_campaign()
        self.assert_stopped(result)
        self.assertEqual(self.metadata("first/metadata.json")["status"], "failed")
        self.serial.assert_not_called()

    def test_readiness_wait_is_clamped_to_remaining_campaign_time(self):
        self.identity_delays = [45]
        self.ready_delay = 9
        result = self.run_campaign()
        self.assert_stopped(result)
        self.assertAlmostEqual(self.now, self.deadline)
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        self.serial.assert_not_called()

    def test_preopen_quiet_window_cannot_start_serial_after_expiry(self):
        self.identity_delays = [self.budget - 0.5]
        result = self.run_campaign()
        self.assert_stopped(result)
        self.assertAlmostEqual(self.now, self.deadline)
        self.serial.assert_not_called()
        self.device.assert_not_called()

    def test_serial_preflight_expiry_prevents_open_and_preserves_session_evidence(self):
        self.device_delay = self.budget
        result = self.run_campaign()
        self.assert_stopped(result)
        self.serial.assert_not_called()
        self.ownership.assert_not_called()
        self.assertTrue((self.output / "first/SHA256SUMS").is_file())

    def test_serial_open_overrun_closes_without_application_io(self):
        self.open_delay = self.budget
        result = self.run_campaign()
        self.assert_stopped(result)
        self.transport.open.assert_called_once()
        self.transport.close.assert_called_once()
        self.transport.read.assert_not_called()
        self.transport.write.assert_not_called()
        self.ioctl.assert_not_called()

    def test_tail_expiry_is_not_completed_and_prevents_next_segment(self):
        self.identity_delays = [45]
        result = self.run_campaign()
        self.assert_stopped(result)
        self.assertEqual([data for _, data in self.write_times], [b"\r"] * 3)
        self.assertEqual(self.metadata("first/serial/metadata.json")["status"], "completed")
        self.assertEqual(self.metadata("first/metadata.json")["status"], "failed")
        self.assertAlmostEqual(self.now, self.deadline)
        self.assess.assert_not_called()

    def test_close_grace_wait_cannot_extend_campaign_when_stop_request_is_ignored(self):
        self.identity_delays = [30]
        self.ignore_stop = True
        result = self.run_campaign()
        self.assert_stopped(result)
        child = self.metadata("first/metadata.json")
        self.assertLess(child["usb_stop_requested_monotonic"], self.deadline)
        self.assertLess(self.deadline, child["usb_hard_deadline_monotonic"])
        self.assertAlmostEqual(self.now, self.deadline)
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(len(self.write_times), 3)
        self.assess.assert_not_called()

    def test_slow_close_fails_completion_but_keeps_written_counts_and_hashes(self):
        self.close_delay = self.budget
        result = self.run_campaign()
        self.assert_stopped(result)
        serial = self.metadata("first/serial/metadata.json")
        self.assertEqual(serial["status"], "failed")
        self.assertEqual(serial["stop_reason"], "shared_deadline")
        self.assertEqual(serial["application_bytes_written"], 3)
        self.assertEqual(serial["scheduled_writes_completed"], 3)
        self.transport.close.assert_called_once()
        self.assertTrue((self.output / "first/SHA256SUMS").is_file())

    def test_expiry_during_inflight_write_preserves_accepted_prefix_without_retry(self):
        self.write_delay = self.budget
        result = self.run_campaign()
        self.assert_stopped(result)
        serial = self.metadata("first/serial/metadata.json")
        self.assertEqual(serial["application_bytes_written"], 1)
        self.assertEqual(serial["known_application_bytes_written"], 1)
        self.assertEqual(serial["scheduled_writes_completed"], 1)
        self.assertEqual(len(self.write_times), 1)
        self.transport.close.assert_called_once()

    def test_rejected_settings_cannot_continue_after_cleanup_exhausts_deadline(self):
        self.transport.open.side_effect = probe.SerialSettingsRejected(22, "synthetic rejected settings")
        self.close_delay = self.budget
        result = self.run_campaign()
        self.assert_stopped(result)
        serial = self.metadata("first/serial/metadata.json")
        self.assertTrue(serial["settings_rejected_before_open"])
        self.assertIn("synthetic rejected settings", serial["error"])
        self.transport.write.assert_not_called()
        self.assertGreater(self.now, self.deadline)
        self.assess.assert_not_called()

    def test_unknown_write_remains_primary_when_close_and_shutdown_overrun(self):
        error = probe.serial.SerialTimeoutException("original unknown write")
        self.write_error = error
        self.close_delay = self.budget
        self.close_error = OSError("secondary close failure")
        self.shutdown_delay = 2
        with self.assertRaises(type(error)) as raised:
            self.run_campaign()
        self.assertIs(raised.exception, error)
        serial = self.metadata("first/serial/metadata.json")
        self.assertEqual(serial["error"], str(error))
        self.assertEqual(serial["close_error"], str(self.close_error))
        self.assertIsNone(serial["application_bytes_written"])
        self.assertEqual(serial["transmit_status"], "unknown")
        self.assertEqual(self.metadata()["status"], "failed")
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.assertEqual(len(self.write_times), 1)
        self.assertFalse((self.output / "second").exists())

    def test_close_failure_does_not_mistake_callers_handled_exception_for_capture_failure(self):
        error = OSError("actual serial close failure")
        self.close_error = error
        try:
            raise ValueError("unrelated handled caller exception")
        except ValueError:
            with self.assertRaises(OSError) as raised:
                self.run_capture(remaining=5)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.metadata()["status"], "failed")
        self.assertEqual(self.metadata()["close_error"], str(error))

    def test_deadline_shutdown_keeps_evidence_even_when_cleanup_outlasts_budget(self):
        self.identity_delays = [self.budget - 0.5]
        self.shutdown_delay = 2
        result = self.run_campaign()
        self.assert_stopped(result)
        self.assertAlmostEqual(self.now, self.deadline + 2)
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertTrue((self.output / "first/SHA256SUMS").is_file())
        self.transport.write.assert_not_called()

    def test_read_timeout_is_capped_and_returned_bytes_survive_expiry(self):
        self.received_chunks = [b"last bytes"]
        with self.assertRaises(probe.DeadlineExpired):
            self.run_capture()
        self.assertEqual(len(self.read_timeouts), 1)
        self.assertAlmostEqual(self.read_timeouts[0], 0.05)
        self.assertEqual((self.output / "received.bin").read_bytes(), b"last bytes")
        self.assertEqual(self.metadata()["bytes_received"], 10)
        self.assertEqual(self.metadata()["status"], "failed")
        self.transport.write.assert_not_called()
        self.transport.close.assert_called_once()

    def test_expiry_during_write_metadata_prevents_write_and_keeps_known_zero(self):
        save = probe.save_metadata

        def slow_metadata(path, value):
            if value.get("transmit_status") == "attempting":
                self.now = self.deadline
            save(path, value)

        with patch.object(probe, "save_metadata", side_effect=slow_metadata):
            with self.assertRaises(probe.DeadlineExpired):
                self.run_capture(probe=b"\r", allow_unknown_command=True)
        self.transport.write.assert_not_called()
        self.assertEqual(self.metadata()["application_bytes_written"], 0)
        self.assertEqual(self.metadata()["transmit_status"], "not_sent_before_deadline")
        self.assertIn("write_cancelled", (self.output / "events.jsonl").read_text())

    def test_write_timeout_is_capped_at_the_actual_write_boundary(self):
        self.write_delay = 0.05
        with self.assertRaises(probe.DeadlineExpired):
            self.run_capture(probe=b"\r", allow_unknown_command=True)
        self.assertAlmostEqual(self.transport.write_timeout, 0.05)
        self.assertEqual(len(self.write_times), 1)
        self.assertEqual(self.metadata()["application_bytes_written"], 1)
        self.transport.read.assert_not_called()

    def test_readiness_callback_cannot_start_serial_preflight_after_expiry(self):
        self.deadline = self.now + 2
        with self.assertRaises(probe.DeadlineExpired):
            session.run_session("offline", self.output, seconds=1, actuators_isolated=True,
                                deadline=self.deadline, ready_callback=lambda ready: self.advance(2))
        self.assertEqual(self.metadata()["status"], "failed")
        self.device.assert_not_called()
        self.serial.assert_not_called()
        self.assertTrue((self.output / "SHA256SUMS").is_file())

    def test_completed_work_is_not_retroactively_failed_by_slow_root_sealing(self):
        self.plan["segments"] = self.plan["segments"][:1]
        seal = session.seal_evidence

        def slow_seal(directory, metadata):
            if directory == self.output:
                self.assertLess(self.now, self.deadline)
                self.advance(self.budget)
            return seal(directory, metadata)

        with patch.object(session, "seal_evidence", side_effect=slow_seal):
            result = self.run_campaign()
        self.assertEqual(result["status"], "completed_silent")
        self.assertEqual(result["application_bytes_confirmed"], 3)
        self.assertEqual(len(self.write_times), 3)
        for relative in ("metadata.json", "first/metadata.json", "first/serial/metadata.json"):
            self.assertEqual(self.metadata(relative)["shared_deadline_monotonic"], self.deadline)
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.assertGreater(self.now, self.deadline)

    def test_invalid_or_expired_deadlines_fail_before_preflight_and_output(self):
        for call in (
            lambda deadline: probe.capture("offline", self.output, seconds=1, baudrate=115200,
                                          max_bytes=10, actuators_isolated=True, deadline=deadline),
            lambda deadline: session.run_session("offline", self.output, actuators_isolated=True,
                                                 deadline=deadline),
        ):
            for deadline in (True, False, "1001", [], 10**500, float("inf"), float("nan")):
                with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                    call(deadline)
            for deadline in (self.now, self.now - 1):
                with self.assertRaises(probe.DeadlineExpired):
                    call(deadline)
        self.preflight.assert_not_called()
        self.device.assert_not_called()
        self.serial.assert_not_called()
        self.spawned.assert_not_called()
        self.assertFalse(self.output.exists())


class PreflightDeadlineTests(unittest.TestCase):
    def test_udev_and_fuser_share_the_remaining_timeout_without_real_subprocesses(self):
        clock = [8.0]
        results = [
            subprocess.CompletedProcess([], 0, "ID_VENDOR_ID=045e\nID_MODEL_ID=4444\n"
                                        "ID_MM_DEVICE_IGNORE=1\nID_MM_PORT_IGNORE=1\n", ""),
            subprocess.CompletedProcess([], 1, "", ""),
        ]
        expected_timeouts = [2.0, 0.5]

        def run(command, **options):
            self.assertEqual(options["timeout"], expected_timeouts.pop(0))
            clock[0] += 1.5 if command[0] == "udevadm" else 0.5
            return results.pop(0)

        with patch.object(probe.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(probe.subprocess, "run", side_effect=run) as process:
            probe.check_device("offline", deadline=10)
            with self.assertRaises(probe.DeadlineExpired):
                probe.check_port_available("offline", deadline=10)
        self.assertEqual(process.call_count, 2)
        self.assertEqual(clock[0], 10)


if __name__ == "__main__":
    unittest.main()
