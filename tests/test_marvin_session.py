import hashlib
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_session


BASELINE = {
    "usb": {"usb_path": "/fake/sys/1-3.3", "busnum": 1, "devnum": 8},
    "tty": "/dev/test-marvin",
    "tty_rdev": 123,
}


class Clock:
    now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "capture"
        self.clock = Clock()
        self.process = Mock()
        self.process.poll.side_effect = self.poll
        self.ready = dict(BASELINE["usb"], pid=9999, monotonic=0)
        self.ready_delay = 0
        self.ready_overrides = {}
        self.usb_final_overrides = {}
        self.coordinated = False
        self.usb_status = "completed"
        self.start_error = None
        self.finished = False
        self.usb_finish_time = 30.5
        self.clock_patch = patch.object(marvin_session.time, "monotonic", self.clock.monotonic).start()
        patch.object(marvin_session.time, "sleep", self.clock.sleep).start()
        patch.object(marvin_session.os, "geteuid", return_value=1000).start()
        self.preflight = patch.object(marvin_session, "preflight", return_value=BASELINE).start()
        self.identity = patch.object(marvin_session, "check_identity").start()
        self.popen = patch.object(marvin_session.subprocess, "Popen", side_effect=self.spawn).start()
        self.serial = patch.object(marvin_session.marvin_probe, "capture", side_effect=self.capture).start()
        self.addCleanup(patch.stopall)

    def poll(self):
        requested = self.coordinated and (
            self.output / "usb" / marvin_session.marvin_usbmon.COORDINATOR_STOP_FILE).exists()
        if self.finished or self.clock.now >= self.usb_finish_time or requested:
            if self.coordinated and not self.finished:
                metadata = dict(
                    self.usb_metadata,
                    stopped_monotonic=self.clock.now if requested else self.usb_finish_time,
                    stop_reason="coordinator_stop" if requested else "duration",
                )
                metadata.update(self.usb_final_overrides)
                (self.output / "usb/metadata.json").write_text(json.dumps(metadata))
                self.finished = True
            self.process.returncode = 0
            return 0
        return None

    def spawn(self, command, **kwargs):
        if self.start_error:
            raise self.start_error
        usb = self.output / "usb"
        usb.mkdir()
        self.coordinated = "--coordinator-stop" in command
        if self.coordinated:
            maximum = float(command[command.index("--seconds") + 1])
            self.usb_finish_time = self.ready["monotonic"] + maximum
            self.ready.update(coordinator_stop=True,
                              coordinator_stop_file=marvin_session.marvin_usbmon.COORDINATOR_STOP_FILE,
                              seconds=maximum, deadline_monotonic=self.usb_finish_time)
        self.ready.update(self.ready_overrides)
        (usb / "ready.json").write_text(json.dumps(self.ready))
        self.usb_metadata = dict(self.ready, started_monotonic=self.ready["monotonic"],
                                 status=self.usb_status, signal=None)
        (usb / "metadata.json").write_text(json.dumps(self.usb_metadata))
        (usb / "usbmon.txt").write_text("")
        self.process.wait.side_effect = lambda **kw: setattr(self, "finished", True)
        self.clock.sleep(self.ready_delay)
        return self.process

    def capture(self, port, output, **kwargs):
        kwargs["guard"]()
        self.assertGreaterEqual(self.clock.now, 1)
        self.clock.sleep(kwargs["seconds"])
        output.mkdir()
        (output / "received.bin").write_bytes(b"")
        return {"status": "completed", "application_bytes_written": 0, "bytes_received": 0}

    def run_capture(self, **kwargs):
        defaults = {"seconds": 0.5, "actuators_isolated": True}
        defaults.update(kwargs)
        return marvin_session.run_session("/dev/test-marvin", self.output, **defaults)

    def test_ready_before_one_serial_open_and_trace_lasts_through_close(self):
        result = self.run_capture(dtr=True, rts=True)
        self.assertEqual(result["status"], "completed")
        self.serial.assert_called_once()
        self.assertTrue(self.serial.call_args.kwargs["line_state_at_open"])
        self.assertNotIn("probe", self.serial.call_args.kwargs)
        self.assertGreaterEqual(self.clock.now, 30.5)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.process.send_signal.assert_not_called()
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            digest, path = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.output / path).read_bytes()).hexdigest(), digest)

    def test_callback_runs_after_usb_ready_and_before_serial_open(self):
        callback = Mock(side_effect=lambda ready: self.serial.assert_not_called())
        self.run_capture(ready_callback=callback, expected_usb_identity=BASELINE["usb"])
        callback.assert_called_once_with(self.ready)
        self.serial.assert_called_once()

    def test_expected_identity_mismatch_stops_before_recorder(self):
        with self.assertRaisesRegex(OSError, "before the requested"):
            self.run_capture(expected_usb_identity={"devnum": 99})
        self.popen.assert_not_called()
        self.serial.assert_not_called()

    def test_rejects_root_and_missing_isolation_before_hardware(self):
        with patch.object(marvin_session.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "ordinary user"):
                self.run_capture()
        with self.assertRaisesRegex(ValueError, "isolation"):
            self.run_capture(actuators_isolated=False)
        self.preflight.assert_not_called()

    def test_probe_requires_authorization_before_preflight(self):
        with self.assertRaisesRegex(ValueError, "authorization"):
            self.run_capture(probe_cr=True)
        self.preflight.assert_not_called()
        self.popen.assert_not_called()

    def test_non_boolean_acknowledgments_and_selectors_stop_before_preflight(self):
        for name in (
            "actuators_isolated", "sudo_usbmon", "allow_unknown_command",
            "allow_telemetry_state_change", "allow_line_state_trial", "dtr", "rts",
            "probe_cr", "probe_get_config", "probe_get_unit_info", "probe_get_sensor_info",
        ):
            for value in (1, 0, "false", "true", None, [], [True]):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, "boolean"):
                    self.run_capture(**{name: value})
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()

    def test_raw_schedule_is_allowlisted_before_any_hardware_preflight(self):
        for data in (b"\x80", b"erase\r", bytes.fromhex("efbe0000080000000000adde")):
            schedule = (
                marvin_session.marvin_probe.ScheduledWrite(0.1, data[:1], "first"),
                marvin_session.marvin_probe.ScheduledWrite(0.2, data[1:] or b"\x80", "second"),
            )
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.run_capture(probe_schedule=schedule, allow_unknown_command=True,
                                 allow_telemetry_state_change=True)
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()

    def test_approved_probe_uses_existing_one_shot_path_after_usb_readiness(self):
        result = self.run_capture(probe_cr=True, allow_unknown_command=True)
        self.serial.assert_called_once()
        self.assertEqual(self.serial.call_args.kwargs["probe"], b"\r")
        self.assertTrue(self.serial.call_args.kwargs["allow_unknown_command"])
        self.assertEqual(result["requested_application_bytes"], 1)
        self.assertEqual(result["requested_probe_hex"], "0d")
        self.assertTrue(result["unknown_command_authorized"])

    def test_get_config_requires_authorization_and_source_settings_before_preflight(self):
        cases = (
            {"dtr": True, "rts": True},
            {"allow_unknown_command": True, "dtr": True},
            {"allow_unknown_command": True, "rts": True},
            {"allow_unknown_command": True, "dtr": True, "rts": True, "baudrate": 9600},
            {"allow_unknown_command": True, "dtr": True, "rts": True, "probe_cr": True},
        )
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_capture(probe_get_config=True, **options)
        self.preflight.assert_not_called()
        self.popen.assert_not_called()

    def test_get_config_uses_one_exact_frame_after_recorder_readiness(self):
        result = self.run_capture(probe_get_config=True, allow_unknown_command=True, dtr=True, rts=True)
        self.serial.assert_called_once()
        self.assertEqual(self.serial.call_args.kwargs["probe"].hex(), "efbe0000040000001133adde")
        self.assertTrue(self.serial.call_args.kwargs["line_state_at_open"])
        self.assertTrue(self.serial.call_args.kwargs["allow_unknown_command"])
        self.assertEqual(result["requested_application_bytes"], 12)
        self.assertEqual(result["source_expected_response_payload_bytes"], 108)
        self.assertEqual(result["probe_name"], "GetConfig")

    def test_get_config_uncertain_write_is_never_retried(self):
        self.serial.side_effect = marvin_session.marvin_probe.serial.SerialTimeoutException("write outcome unknown")
        with self.assertRaisesRegex(OSError, "outcome unknown"):
            self.run_capture(probe_get_config=True, allow_unknown_command=True, dtr=True, rts=True)
        self.serial.assert_called_once()
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["requested_probe_hex"], "efbe0000040000001133adde")

    def test_unit_info_requires_separate_state_change_acknowledgment(self):
        for extra in ({}, {"probe_get_config": True}, {"probe_cr": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.run_capture(probe_get_unit_info=True, allow_unknown_command=True,
                                 dtr=True, rts=True, **extra)
        self.preflight.assert_not_called()

    def test_unit_info_is_one_explicit_stateful_request_not_a_handshake_sequence(self):
        result = self.run_capture(probe_get_unit_info=True, allow_unknown_command=True,
                                  allow_telemetry_state_change=True, dtr=True, rts=True)
        self.serial.assert_called_once()
        self.assertEqual(self.serial.call_args.kwargs["probe"].hex(), "efbe01001b0000001736adde")
        self.assertEqual(result["probe_name"], "GetUnitInfo")
        self.assertEqual(result["source_expected_response_payload_bytes"], 12)
        self.assertTrue(result["telemetry_state_change_authorized"])

    def test_unit_info_uncertain_write_is_not_retried(self):
        self.serial.side_effect = marvin_session.marvin_probe.serial.SerialTimeoutException("write outcome unknown")
        with self.assertRaisesRegex(OSError, "outcome unknown"):
            self.run_capture(probe_get_unit_info=True, allow_unknown_command=True,
                             allow_telemetry_state_change=True, dtr=True, rts=True)
        self.serial.assert_called_once()
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_sensor_info_requires_all_guards_before_hardware(self):
        defaults = {"probe_get_sensor_info": True, "allow_unknown_command": True,
                    "allow_telemetry_state_change": True, "dtr": True, "rts": True}
        for invalid in (
            {"allow_unknown_command": False}, {"allow_telemetry_state_change": False},
            {"dtr": False}, {"rts": False}, {"baudrate": 9600},
            {"probe_cr": True}, {"probe_get_config": True}, {"probe_get_unit_info": True},
            {"allow_line_state_trial": True}, {"probe_delay": 0.5},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.run_capture(**(defaults | invalid))
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()

    def test_sensor_info_sends_one_frame_not_an_automatic_handshake(self):
        result = self.run_capture(probe_get_sensor_info=True, allow_unknown_command=True,
                                  allow_telemetry_state_change=True, dtr=True, rts=True,
                                  probe_delay=0.2)
        self.serial.assert_called_once()
        request = self.serial.call_args.kwargs["probe"]
        packet = marvin_session.marvin_protocol.decode_packet(request)
        self.assertEqual((packet.sequence, packet.command, packet.payload), (2, 29, b""))
        self.assertEqual(len(request), 12)
        self.assertEqual(self.serial.call_args.kwargs["probe_delay"], 0.2)
        self.assertEqual(result["requested_probe_hex"], request.hex())
        self.assertEqual(result["requested_application_bytes"], 12)
        self.assertEqual(result["probe_name"], "GetSensorInfo")
        self.assertEqual(result["source_expected_response_payload_bytes"], 128)
        self.assertTrue(result["telemetry_state_change_authorized"])
        self.assertTrue(result["unknown_command_authorized"])

    def test_sensor_info_uncertain_write_is_not_retried(self):
        self.serial.side_effect = marvin_session.marvin_probe.serial.SerialTimeoutException("write outcome unknown")
        with self.assertRaisesRegex(OSError, "outcome unknown"):
            self.run_capture(probe_get_sensor_info=True, allow_unknown_command=True,
                             allow_telemetry_state_change=True, dtr=True, rts=True)
        self.serial.assert_called_once()
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_sensor_info_cli_forwards_explicit_flags_and_rejects_combined_probes(self):
        argv = ["marvin_session", "--output", str(self.output), "--actuators-isolated",
                "--probe-get-sensor-info", "--allow-unknown-command",
                "--allow-telemetry-state-change", "--dtr", "--rts",
                "--seconds", "15", "--probe-delay", "5"]
        with patch.object(marvin_session, "run_session", return_value={"status": "completed"}) as run:
            with patch("sys.argv", argv), redirect_stdout(io.StringIO()):
                self.assertEqual(marvin_session.main(), 0)
            self.assertTrue(run.call_args.kwargs["probe_get_sensor_info"])
            self.assertTrue(run.call_args.kwargs["allow_telemetry_state_change"])
            self.assertEqual(run.call_args.kwargs["probe_delay"], 5)
            run.reset_mock()
            for conflicting in ("--probe-cr", "--probe-get-config", "--probe-get-unit-info"):
                with patch("sys.argv", [*argv, conflicting]), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        marvin_session.main()
                    self.assertEqual(error.exception.code, 2)
            run.assert_not_called()

    def test_campaign_schedule_requires_separate_acks_and_no_named_probe(self):
        schedule = (marvin_session.marvin_probe.ScheduledWrite(0.1, b"\r", "query/0"),)
        for extra in (
            {}, {"allow_unknown_command": True}, {"allow_telemetry_state_change": True},
            {"allow_unknown_command": True, "allow_telemetry_state_change": True, "probe_cr": True},
            {"allow_unknown_command": True, "allow_telemetry_state_change": True, "probe_get_config": True},
            {"allow_unknown_command": True, "allow_telemetry_state_change": True, "probe_get_unit_info": True},
            {"allow_unknown_command": True, "allow_telemetry_state_change": True, "probe_get_sensor_info": True},
            {"allow_unknown_command": True, "allow_telemetry_state_change": True, "probe_delay": 0.1},
            {"allow_unknown_command": True, "allow_telemetry_state_change": True, "allow_line_state_trial": True},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.run_capture(probe_schedule=schedule, **extra)
        self.preflight.assert_not_called()

    def test_campaign_forwards_schedule_framing_and_short_bounded_usb_tail(self):
        schedule = (marvin_session.marvin_probe.ScheduledWrite(0.1, b"help\r", "help/0"),)
        self.usb_finish_time = 5.5
        result = self.run_capture(probe_schedule=schedule, allow_unknown_command=True,
                                  probe_profile="experimental-successor",
                                  allow_telemetry_state_change=True, baudrate=9600,
                                  bytesize=7, parity="E", stopbits=2, usb_tail_seconds=5)
        self.serial.assert_called_once()
        self.assertEqual(self.serial.call_args.kwargs["probe_schedule"], schedule)
        self.assertEqual(self.serial.call_args.kwargs["probe_profile"], "experimental-successor")
        self.assertTrue(self.serial.call_args.kwargs["allow_telemetry_state_change"])
        self.assertEqual(self.serial.call_args.kwargs["bytesize"], 7)
        self.assertEqual(self.serial.call_args.kwargs["parity"], "E")
        self.assertEqual(self.serial.call_args.kwargs["stopbits"], 2)
        self.assertEqual(result["probe_name"], "Campaign")
        self.assertEqual(result["requested_application_bytes"], 5)
        self.assertEqual(result["usb_duration_seconds"], 5.5)
        self.assertEqual(result["framing"], "7E2")
        self.assertTrue(result["telemetry_state_change_authorized"])

    def test_named_queries_still_reject_changed_framing(self):
        for probe in ("probe_get_config", "probe_get_unit_info", "probe_get_sensor_info"):
            with self.subTest(probe=probe), self.assertRaises(ValueError):
                self.run_capture(**{probe: True}, allow_unknown_command=True,
                                 allow_telemetry_state_change=True, dtr=True, rts=True,
                                 bytesize=7, parity="E")
        self.preflight.assert_not_called()

    def test_invalid_usb_tail_rejected_before_preflight(self):
        for tail in (0, 4, 31, float("nan"), float("inf")):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                self.run_capture(usb_tail_seconds=tail)
        self.preflight.assert_not_called()

    def test_close_grace_stops_at_ready_origin_nominal_budget_not_maximum(self):
        self.ready_delay = 2
        with patch.object(marvin_session, "request_recorder_stop",
                          wraps=marvin_session.request_recorder_stop) as request:
            result = self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        request.assert_called_once()
        self.assertGreaterEqual(self.clock.now, 5.5)
        self.assertLess(self.clock.now, 5.8)
        self.assertEqual(result["usb_duration_seconds"], 35.5)
        self.assertEqual(result["usb_nominal_duration_seconds"], 5.5)
        self.assertEqual(result["usb_nominal_deadline_monotonic"], 5.5)
        self.assertEqual(result["usb"]["stop_reason"], "coordinator_stop")
        self.assertEqual(result["usb_close_grace_seconds"], 30)
        self.process.send_signal.assert_not_called()
        stop = self.output / "usb" / marvin_session.marvin_usbmon.COORDINATOR_STOP_FILE
        self.assertEqual(stop.stat().st_size, 0)
        self.assertEqual(stop.stat().st_mode & 0o777, 0o600)

    def test_slow_successful_close_receives_drain_beyond_nominal_budget(self):
        def close_late(*args, **kwargs):
            result = self.capture(*args, **kwargs)
            self.clock.sleep(30.49)
            self.assertIsNone(self.process.poll())
            return result
        self.serial.side_effect = close_late
        result = self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.assertEqual(result["status"], "completed")
        self.assertGreaterEqual(result["usb_stop_requested_monotonic"] - result["serial_returned_monotonic"],
                                marvin_session.USB_POST_CLOSE_DRAIN_SECONDS)
        self.assertGreater(self.clock.now, 32)
        self.assertLess(self.clock.now, 35.5)
        self.process.send_signal.assert_not_called()

    def test_slow_close_error_is_retained_after_nominal_deadline_and_not_retried(self):
        error = marvin_session.marvin_probe.serial.SerialTimeoutException("unknown write after slow close")
        def close_late(*args, **kwargs):
            self.clock.sleep(30.49)
            self.assertIsNone(self.process.poll())
            raise error
        self.serial.side_effect = close_late
        with self.assertRaises(type(error)) as caught:
            self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.assertIs(caught.exception, error)
        self.serial.assert_called_once()
        self.assertGreaterEqual(self.clock.now, 31.49 + marvin_session.USB_POST_CLOSE_DRAIN_SECONDS)
        self.assertLess(self.clock.now, 35.5)
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["error"], str(error))
        self.assertNotIn("usb_stop_requested_monotonic", metadata)
        self.assertTrue((self.output / "SHA256SUMS").is_file())

    def test_failure_drain_and_shutdown_errors_do_not_mask_original_uncertainty(self):
        error = marvin_session.marvin_probe.serial.SerialTimeoutException("original unknown write")
        def fail(*args, **kwargs):
            self.identity.side_effect = OSError("identity lost after close")
            raise error
        self.serial.side_effect = fail
        with patch.object(marvin_session, "stop_recorder", side_effect=OSError("shutdown failed")):
            with self.assertRaises(type(error)) as caught:
                self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.assertIs(caught.exception, error)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["error"], str(error))
        self.assertIn("identity lost", metadata["drain_error"])
        self.assertIn("shutdown failed", metadata["shutdown_error"])

    def test_premature_completed_recorder_is_rejected_without_stop_request(self):
        def premature(*args, **kwargs):
            result = self.capture(*args, **kwargs)
            self.usb_finish_time = 2
            return result
        self.serial.side_effect = premature
        with self.assertRaisesRegex(OSError, "prematurely"):
            self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.assertLess(self.clock.now, 5.5)

    def test_close_beyond_hard_deadline_is_not_accepted(self):
        def overdue(*args, **kwargs):
            result = self.capture(*args, **kwargs)
            self.clock.sleep(40)
            return result
        self.serial.side_effect = overdue
        with self.assertRaisesRegex(OSError, "USB recorder stopped"):
            self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)

    def test_hard_deadline_guard_stops_serial_even_if_recorder_process_is_still_alive(self):
        def overdue(*args, **kwargs):
            self.usb_finish_time = 99
            self.clock.now = 35.5
            kwargs["guard"]()
        self.serial.side_effect = overdue
        with self.assertRaisesRegex(OSError, "hard deadline reached"):
            self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_ignored_stop_request_has_a_bounded_wait_and_is_not_repeated(self):
        real_poll = self.poll
        def ignore_request():
            if self.coordinated:
                self.process.poll.side_effect = lambda: None
            return real_poll()
        self.process.poll.side_effect = ignore_request
        with patch.object(marvin_session, "request_recorder_stop",
                          wraps=marvin_session.request_recorder_stop) as request:
            with self.assertRaisesRegex(TimeoutError, "bounded observation window"):
                self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        request.assert_called_once()
        self.assertLess(self.clock.now, 40.7)
        self.process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_hard_deadline_remains_valid_when_post_close_drain_uses_last_grace(self):
        def near_deadline(*args, **kwargs):
            result = self.capture(*args, **kwargs)
            self.clock.now = 35.4
            return result
        self.serial.side_effect = near_deadline
        result = self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.assertEqual(result["usb"]["stop_reason"], "duration")
        self.assertNotIn("usb_stop_requested_monotonic", result)
        self.assertLess(self.clock.now, 35.8)

    def test_required_ready_capability_and_hard_budget_precede_serial_open(self):
        for overrides in (
            {"coordinator_stop": False}, {"coordinator_stop_file": "../stop"},
            {"seconds": 5.5}, {"deadline_monotonic": 5.5}, {"monotonic": -1},
            {"monotonic": float("nan")}, {"monotonic": 99},
        ):
            with self.subTest(overrides=overrides):
                self.output = Path(self.temp.name) / f"bad-ready-{len(list(Path(self.temp.name).iterdir()))}"
                self.finished = False
                self.ready["monotonic"] = self.clock.now
                self.ready_overrides = overrides
                with self.assertRaisesRegex(ValueError, "capability or hard deadline"):
                    self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.serial.assert_not_called()

    def test_final_capability_deadline_and_stop_time_must_agree(self):
        for overrides in (
            {"coordinator_stop": False}, {"deadline_monotonic": 5.5},
            {"stopped_monotonic": 1}, {"stop_reason": "duration"},
            {"stop_reason": "signal"}, {"signal": signal.SIGINT},
            {"stopped_monotonic": float("nan")}, {"stopped_monotonic": 999},
        ):
            with self.subTest(overrides=overrides):
                self.output = Path(self.temp.name) / f"bad-final-{len(list(Path(self.temp.name).iterdir()))}"
                self.finished = False
                self.ready["monotonic"] = self.clock.now
                self.usb_final_overrides = overrides
                with self.assertRaises(OSError):
                    self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)

    def test_invalid_grace_and_total_budget_fail_before_any_io(self):
        for grace in (-1, 31, float("nan"), float("inf"), True, "30", None):
            with self.subTest(grace=grace), self.assertRaises(ValueError):
                self.run_capture(usb_close_grace_seconds=grace)
        for options in (
            {"seconds": 90, "usb_tail_seconds": 5, "usb_close_grace_seconds": 30},
            {"seconds": 85, "usb_tail_seconds": 30, "usb_close_grace_seconds": 6},
        ):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "120"):
                self.run_capture(**options)
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_campaign_maximum_85_plus_5_plus_30_is_allowed(self):
        result = self.run_capture(seconds=85, usb_tail_seconds=5, usb_close_grace_seconds=30)
        self.assertEqual(result["usb_duration_seconds"], 120)
        self.assertEqual(result["usb_nominal_duration_seconds"], 90)
        self.assertLess(self.clock.now, 90.3)

    def test_close_grace_cli_forwards_explicit_opt_in(self):
        argv = ["marvin_session", "--output", str(self.output), "--actuators-isolated",
                "--usb-close-grace-seconds", "30"]
        with patch.object(marvin_session, "run_session", return_value={"status": "completed"}) as run:
            with patch("sys.argv", argv), redirect_stdout(io.StringIO()):
                self.assertEqual(marvin_session.main(), 0)
            self.assertEqual(run.call_args.kwargs["usb_close_grace_seconds"], 30)

    def test_rejected_setting_keeps_error_and_drains_usb_before_stop(self):
        self.serial.side_effect = marvin_session.marvin_probe.SerialSettingsRejected(22, "unsupported")
        with self.assertRaises(marvin_session.marvin_probe.SerialSettingsRejected):
            self.run_capture()
        self.assertGreaterEqual(self.clock.now, 6)
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_delayed_get_config_line_trial_is_explicit_and_forwarded(self):
        result = self.run_capture(probe_get_config=True, allow_unknown_command=True,
                                  allow_line_state_trial=True, dtr=False, rts=True, probe_delay=0.2)
        self.assertEqual(self.serial.call_args.kwargs["probe_delay"], 0.2)
        self.assertFalse(self.serial.call_args.kwargs["dtr"])
        self.assertTrue(result["line_state_trial_authorized"])
        self.assertEqual(result["probe_delay_seconds"], 0.2)

    def test_delay_and_line_trial_errors_precede_hardware(self):
        for options in (
            {"probe_delay": 0.1},
            {"probe_get_config": True, "allow_unknown_command": True, "dtr": True, "rts": True, "probe_delay": 0.5},
            {"allow_line_state_trial": True},
            {"probe_get_unit_info": True, "allow_unknown_command": True, "allow_telemetry_state_change": True,
             "allow_line_state_trial": True, "dtr": False},
            {"probe_get_config": True, "allow_unknown_command": True, "allow_line_state_trial": True, "baudrate": 9600},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_capture(**options)
        self.preflight.assert_not_called()

    def test_probe_is_not_retried_when_write_outcome_is_uncertain(self):
        self.serial.side_effect = marvin_session.marvin_probe.serial.SerialTimeoutException(
            "write outcome unknown"
        )
        with self.assertRaisesRegex(OSError, "outcome unknown"):
            self.run_capture(probe_cr=True, allow_unknown_command=True)
        self.serial.assert_called_once()
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_authorization_flag_without_probe_does_not_send_data(self):
        result = self.run_capture(allow_unknown_command=True)
        self.assertNotIn("probe", self.serial.call_args.kwargs)
        self.assertEqual(result["requested_application_bytes"], 0)

    def test_invalid_limits_do_not_run_preflight(self):
        for seconds in (0, -1, 91, float("inf"), float("nan")):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.run_capture(seconds=seconds)
        self.preflight.assert_not_called()

    def test_no_clobber(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_capture()
        self.popen.assert_not_called()

    def test_recorder_identity_mismatch_prevents_open(self):
        self.ready["devnum"] = 9
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.run_capture()
        self.serial.assert_not_called()
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_missing_sudo_or_recorder_start_failure_preserves_error(self):
        self.start_error = OSError("sudo unavailable")
        with self.assertRaisesRegex(OSError, "unavailable"):
            self.run_capture(sudo_usbmon=True)
        command = self.popen.call_args.args[0]
        self.assertEqual(command[:2], ["sudo", "-n"])
        self.assertIn("--drop-to-invoking-user", command)
        self.serial.assert_not_called()
        self.assertTrue((self.output / "SHA256SUMS").exists())

    def test_recorder_exit_before_readiness_prevents_serial_open(self):
        self.finished = True
        with self.assertRaisesRegex(OSError, "before readiness"):
            self.run_capture()
        self.serial.assert_not_called()

    def test_readiness_timeout_prevents_open(self):
        missing = Path(self.temp.name) / "not-ready"
        with self.assertRaises(TimeoutError):
            marvin_session.wait_ready(self.process, missing, BASELINE, "/dev/test", timeout=0.2)
        self.serial.assert_not_called()

    def test_failed_usb_metadata_does_not_look_successful(self):
        self.usb_status = "failed"
        with self.assertRaisesRegex(OSError, "completed capture"):
            self.run_capture()
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_identity_loss_aborts_before_open_and_stops_recorder(self):
        self.identity.side_effect = OSError("device removed")
        with self.assertRaisesRegex(OSError, "device removed"):
            self.run_capture()
        self.serial.assert_not_called()
        self.process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_serial_failure_stops_recorder_and_keeps_failure_evidence(self):
        self.serial.side_effect = OSError("serial disconnected")
        with self.assertRaisesRegex(OSError, "disconnected"):
            self.run_capture()
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertTrue((self.output / "SHA256SUMS").is_file())

    def test_recorder_loss_inside_serial_guard_is_fatal(self):
        def fail_during_capture(*args, **kwargs):
            self.finished = True
            kwargs["guard"]()
        self.serial.side_effect = fail_during_capture
        with self.assertRaisesRegex(OSError, "USB recorder stopped"):
            self.run_capture()

    def test_interrupt_stops_recorder_and_records_incomplete_session(self):
        self.serial.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_capture()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "interrupted")
        self.process.send_signal.assert_called_once_with(signal.SIGINT)


class CleanupTests(unittest.TestCase):
    def test_stop_request_never_clobbers_existing_file_or_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            stop = output / marvin_session.marvin_usbmon.COORDINATOR_STOP_FILE
            other = output / "other"
            other.write_bytes(b"original")
            for create in (lambda: stop.write_bytes(b"original"), lambda: stop.symlink_to(other)):
                create()
                with self.assertRaises(FileExistsError):
                    marvin_session.request_recorder_stop(output)
                self.assertEqual(other.read_bytes(), b"original")
                self.assertEqual(stop.read_bytes(), b"original")
                stop.unlink()

    def test_unresponsive_recorder_is_not_silently_abandoned(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("recorder", 5), None]
        with self.assertRaisesRegex(OSError, "required termination"):
            marvin_session.stop_recorder(process)
        process.terminate.assert_called_once()

    def test_tty_mapping_uses_usb_ancestor_and_requires_acm_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usb = root / "devices" / "1-3.3"
            interface = usb / "1-3.3:1.0"
            tty = interface / "tty" / "ttyACM0"
            tty.mkdir(parents=True)
            (usb / "idVendor").write_text("045e\n")
            driver = root / "drivers" / "cdc_acm"
            driver.mkdir(parents=True)
            (interface / "driver").symlink_to(driver)
            sys_class_tty = root / "class" / "tty"
            (sys_class_tty / "ttyACM0").mkdir(parents=True)
            device_link = sys_class_tty / "ttyACM0" / "device"
            device_link.symlink_to(tty)
            self.assertEqual(marvin_session.usb_path_for_tty("ttyACM0", sys_class_tty), usb)
            (interface / "driver").unlink()
            with self.assertRaisesRegex(ValueError, "cdc_acm"):
                marvin_session.usb_path_for_tty("ttyACM0", sys_class_tty)
            (usb / "idVendor").unlink()
            with self.assertRaisesRegex(ValueError, "USB parent"):
                marvin_session.usb_path_for_tty("ttyACM0", sys_class_tty)

    def test_manifest_rejects_symlinks_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "data").write_bytes(b"abc")
            (output / "alias").symlink_to(output / "data")
            with self.assertRaisesRegex(ValueError, "symlink"):
                marvin_session.evidence_manifest(output)
            (output / "alias").unlink()
            marvin_session.evidence_manifest(output)
            with self.assertRaises(FileExistsError):
                marvin_session.evidence_manifest(output)


if __name__ == "__main__":
    unittest.main()
