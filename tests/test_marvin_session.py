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

from tools import marvin_boot_capture, marvin_campaign, marvin_legacy_protocol, marvin_session, marvin_trials


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
        self.real_serial_capture = marvin_session.marvin_probe.capture
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
                                 status=self.usb_status, signal=None,
                                 monitor_final_stats={"queued": 0, "dropped": 0})
        self.usb_metadata.update(self.usb_final_overrides)
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

    def test_dangling_output_symlink_cannot_redirect_evidence(self):
        root = Path(self.temp.name)
        for relative in (False, True):
            with self.subTest(relative=relative):
                target = root / f"missing-target-{relative}"
                self.output = root / f"output-link-{relative}"
                destination = Path(target.name) if relative else target
                self.output.symlink_to(destination, target_is_directory=True)
                with self.assertRaises(FileExistsError) as raised:
                    self.run_capture()
                self.assertEqual(raised.exception.filename, str(self.output))
                self.assertEqual(self.output.readlink(), destination)
                self.assertFalse(target.exists())
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()

    def test_new_nested_output_parents_remain_supported(self):
        self.output = self.output / "missing" / "nested" / "session"
        result = self.run_capture()
        self.assertEqual(result["status"], "completed")
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.preflight.assert_called_once()
        self.popen.assert_called_once()
        self.serial.assert_called_once()

    def test_ready_before_one_serial_open_and_trace_lasts_through_close(self):
        result = self.run_capture(dtr=True, rts=True, allow_line_state_change=True)
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

    def test_asserted_lines_cannot_infer_consent_from_isolation_or_any_probe(self):
        schedule = (marvin_session.marvin_probe.ScheduledWrite(0.1, b"\r", "query"),)
        for selection in ({}, {"probe_cr": True}, {"probe_get_config": True},
                          {"probe_get_unit_info": True}, {"probe_get_sensor_info": True},
                          {"probe_schedule": schedule}):
            for dtr, rts in ((True, False), (False, True), (True, True)):
                with self.subTest(selection=selection, lines=(dtr, rts)):
                    with self.assertRaisesRegex(ValueError, "line-state authorization"):
                        self.run_capture(dtr=dtr, rts=rts, allow_unknown_command=True,
                                         allow_telemetry_state_change=True, **selection)
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_generic_consent_never_relaxes_named_query_or_trial_restrictions(self):
        for probe in ("probe_get_config", "probe_get_unit_info", "probe_get_sensor_info"):
            for invalid in ({"dtr": False}, {"rts": False}, {"baudrate": 9600},
                            {"bytesize": 7}, {"probe_profile": "legacy"}):
                with self.subTest(probe=probe, invalid=invalid), self.assertRaises(ValueError):
                    self.run_capture(**({
                        probe: True, "dtr": True, "rts": True,
                        "allow_unknown_command": True, "allow_telemetry_state_change": True,
                        "allow_line_state_change": True,
                    } | invalid))
        for selection in ({}, {"probe_cr": True}, {"probe_get_unit_info": True},
                          {"probe_get_sensor_info": True}):
            with self.subTest(selection=selection), self.assertRaisesRegex(ValueError, "Line-state trials"):
                self.run_capture(dtr=True, rts=True, allow_line_state_change=True,
                                 allow_line_state_trial=True, allow_unknown_command=True,
                                 allow_telemetry_state_change=True, **selection)
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_either_valid_consent_does_not_mask_malformed_other_consent(self):
        for bad_flag, valid_flag in (("allow_line_state_change", "allow_line_state_trial"),
                                     ("allow_line_state_trial", "allow_line_state_change")):
            for value in (1, 0, "true", "false", None, [], [True], object()):
                with self.subTest(flag=bad_flag, value=value), self.assertRaisesRegex(ValueError, bad_flag):
                    self.run_capture(probe_get_config=True, allow_unknown_command=True, dtr=True, rts=True,
                                     **{valid_flag: True, bad_flag: value})
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_generic_line_only_cli_requires_consent_without_a_fabricated_trial(self):
        argv = ["marvin_session", "--output", str(self.output), "--actuators-isolated",
                "--dtr", "--rts", "--seconds", "0.5"]
        with patch("sys.argv", argv), redirect_stderr(io.StringIO()):
            self.assertEqual(marvin_session.main(), 1)
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.assertFalse(self.output.exists())
        with patch("sys.argv", [*argv, "--allow-line-state-change"]), redirect_stdout(io.StringIO()):
            self.assertEqual(marvin_session.main(), 0)
        self.assertIs(self.serial.call_args.kwargs["allow_line_state_change"], True)
        self.assertNotIn("probe", self.serial.call_args.kwargs)
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertIs(result["line_state_change_authorized"], True)
        self.assertIs(result["line_state_trial_authorized"], False)
        self.assertEqual(result["requested_application_bytes"], 0)

    def test_modern_schedule_settings_cannot_bypass_shared_named_query_policy(self):
        config = marvin_session.marvin_protocol.get_config_request(65535)
        unit = marvin_session.marvin_protocol.get_unit_info_request(42)
        for data in (config, unit, config + unit):
            schedule = tuple(marvin_session.marvin_probe.ScheduledWrite(
                0.1 + index * 0.1, data[offset:offset + 12], f"query-{index}")
                for index, offset in enumerate(range(0, len(data), 12)))
            for invalid in ({"dtr": False}, {"rts": False}, {"baudrate": 9600}, {"bytesize": 7}):
                with self.subTest(data=data, invalid=invalid), self.assertRaisesRegex(ValueError, "115200/8N1"):
                    self.run_capture(**({
                        "probe_schedule": schedule, "allow_unknown_command": True,
                        "allow_telemetry_state_change": True, "allow_line_state_change": True,
                        "dtr": True, "rts": True,
                    } | invalid))
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()
        self.assertFalse(self.output.exists())

    def run_real_consent_chain(self, runner, *, disconnect_first=False):
        real_session = marvin_session.run_session
        transport = Mock()
        transport.write.side_effect = lambda data: len(data)

        def read(size):
            if disconnect_first and self.output.name == "before-cycle":
                raise OSError("synthetic disconnect")
            self.clock.sleep(transport.timeout)
            return b""

        def segment(port, output, **options):
            self.output = output
            self.finished = False
            self.preflight.return_value = dict(BASELINE, usb=options["expected_usb_identity"])
            self.ready = dict(options["expected_usb_identity"], pid=9999, monotonic=self.clock.now)
            self.usb_finish_time = self.clock.now + options["seconds"] + options.get("usb_tail_seconds", 30)
            return real_session(port, output, **options)

        transport.read.side_effect = read
        self.serial.side_effect = self.real_serial_capture
        with patch.object(marvin_session, "run_session", side_effect=segment) as sessions, \
                patch.object(marvin_session.marvin_probe, "check_device", return_value={}), \
                patch.object(marvin_session.marvin_probe, "check_port_available"), \
                patch.object(marvin_session.marvin_probe.serial, "Serial", return_value=transport), \
                patch.object(marvin_session.marvin_probe.fcntl, "ioctl"), redirect_stdout(io.StringIO()):
            result = runner()
        return result, transport, sessions.call_args_list

    def test_boot_runs_real_session_and_serial_validation_without_fake_trial_or_probe(self):
        returned = dict(BASELINE, usb=dict(BASELINE["usb"], devnum=9))
        for reconnect in (False, True):
            with self.subTest(reconnect=reconnect):
                output = Path(self.temp.name) / f"boot-{reconnect}"
                self.clock.now = 0
                self.preflight.return_value = BASELINE
                with patch.object(marvin_boot_capture, "wait_for_identity_change", return_value=True), \
                        patch.object(marvin_boot_capture, "wait_for_return", return_value=returned):
                    result, transport, calls = self.run_real_consent_chain(
                        lambda: marvin_boot_capture.run_boot_capture(
                            "/dev/test-marvin", output, actuators_isolated=True,
                            allow_line_state_change=True,
                        ),
                        disconnect_first=reconnect,
                    )
                self.assertEqual(result["status"], (
                    "completed_with_reconnect_gap" if reconnect else "completed_without_reenumeration"))
                self.assertEqual(len(calls), 2 if reconnect else 1)
                transport.write.assert_not_called()
                self.assertEqual(transport.open.call_count, len(calls))
                for call in calls:
                    self.assertIs(call.kwargs["allow_line_state_change"], True)
                    self.assertNotIn("allow_line_state_trial", call.kwargs)
                    session = json.loads((call.args[1] / "metadata.json").read_text())
                    serial = json.loads((call.args[1] / "serial/metadata.json").read_text())
                    self.assertIs(session["line_state_change_authorized"], True)
                    self.assertIs(session["line_state_trial_authorized"], False)
                    self.assertIs(serial["line_state_change_authorized"], True)
                    self.assertEqual(serial["application_bytes_written"], 0)
                    self.assertIsNone(session["probe_name"])

    def test_named_queries_forward_actual_consent_through_real_serial_validation(self):
        cases = [
            ("probe_get_config", marvin_session.marvin_protocol.get_config_request(),
             dtr, rts, False, True)
            for dtr, rts in ((False, False), (False, True), (True, False), (True, True))
        ]
        cases.extend((name, encode(), True, True, True, False) for name, encode in (
            ("probe_get_config", marvin_session.marvin_protocol.get_config_request),
            ("probe_get_unit_info", marvin_session.marvin_protocol.get_unit_info_request),
            ("probe_get_sensor_info", marvin_session.marvin_protocol.get_sensor_info_request),
        ))
        for index, (name, data, dtr, rts, generic, trial) in enumerate(cases):
            with self.subTest(name=name, dtr=dtr, rts=rts, generic=generic, trial=trial):
                output = Path(self.temp.name) / f"named-{index}"
                self.clock.now = 0
                result, transport, calls = self.run_real_consent_chain(
                    lambda: marvin_session.run_session(
                        "/dev/test-marvin", output, seconds=0.5, probe_delay=0.1,
                        actuators_isolated=True, expected_usb_identity=BASELINE["usb"],
                        allow_unknown_command=True, allow_telemetry_state_change=True,
                        dtr=dtr, rts=rts, allow_line_state_change=generic,
                        allow_line_state_trial=trial, **{name: True},
                    )
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(calls), 1)
                transport.write.assert_called_once_with(data)
                forwarded = self.serial.call_args.kwargs
                self.assertIs(forwarded["allow_line_state_change"], generic)
                self.assertIs(forwarded["allow_line_state_trial"], trial)
                serial = json.loads((output / "serial/metadata.json").read_text())
                self.assertIs(serial["line_state_trial_authorized"], trial)
                self.assertEqual(serial["requested_probe_hex"], data.hex())
                self.assertEqual(serial["application_bytes_written"], 12)

    def test_boot_does_not_accept_a_success_shaped_recorder_with_an_unread_tail(self):
        output = Path(self.temp.name) / "boot-queued"
        self.usb_final_overrides = {"monitor_final_stats": {"queued": 1, "dropped": 0}}
        with patch.object(marvin_boot_capture, "wait_for_identity_change", return_value=False), \
                patch.object(marvin_boot_capture, "wait_for_return") as returned, \
                self.assertRaisesRegex(ValueError, "final queued/dropped"):
            self.run_real_consent_chain(lambda: marvin_boot_capture.run_boot_capture(
                "/dev/test-marvin", output, actuators_isolated=True, allow_line_state_change=True,
            ))
        returned.assert_not_called()
        self.popen.assert_called_once()
        for path in (output / "metadata.json", output / "before-cycle/metadata.json"):
            self.assertEqual(json.loads(path.read_text())["status"], "failed")

    def test_campaign_and_trials_delegate_consent_through_real_session_and_serial_validation(self):
        data = marvin_session.marvin_protocol.get_config_request()
        for wrapper in ("campaign", "trials"):
            with self.subTest(wrapper=wrapper):
                output = Path(self.temp.name) / wrapper
                self.clock.now = 0
                self.preflight.return_value = dict(
                    BASELINE, usb=dict(BASELINE["usb"], descriptors_sha256=marvin_campaign.DESCRIPTOR_HASH))
                assessment = {"outcome": "silent_out_confirmed", "usb_out_confirmed_bytes": len(data)}
                if wrapper == "campaign":
                    plan = {"schema_version": 1, "segments": [{
                        "id": "query", "baudrate": 115200, "bytesize": 8, "parity": "N",
                        "stopbits": 1, "dtr": True, "rts": False, "steps": [{
                            "id": "config", "chunks_hex": [data.hex()], "interval_seconds": 0,
                            "response_seconds": 0.2, "classification": "query", "rationale": "synthetic",
                        }],
                    }]}
                    runner = lambda: marvin_campaign.run_campaign(
                        plan, output, actuators_isolated=True, allow_unknown_command=True,
                        allow_telemetry_state_change=True, allow_line_state_trials=True, switch_position="RUN")
                else:
                    runner = lambda: marvin_trials.run_trials(
                        "/dev/test-marvin", output, case_names=["low-high"], actuators_isolated=True,
                        allow_unknown_command=True, allow_line_state_trials=True)
                with patch.object(marvin_campaign, "assess_segment", return_value=assessment), \
                        patch.object(marvin_trials, "assess_case", return_value=assessment):
                    result, transport, calls = self.run_real_consent_chain(runner)
                self.assertEqual(result["status"], "completed_silent")
                self.assertEqual(len(calls), 1)
                self.assertIs(calls[0].kwargs["allow_line_state_trial"], True)
                transport.write.assert_called_once_with(data)
                serial = json.loads((calls[0].args[1] / "serial/metadata.json").read_text())
                self.assertIs(serial["line_state_change_authorized"], True)
                self.assertIs(serial["line_state_trial_authorized"], True)
                self.assertEqual(serial["application_bytes_written"], len(data))
                self.assertEqual(serial["transmit_status"], "written")

    def test_non_boolean_acknowledgments_and_selectors_stop_before_preflight(self):
        for name in (
            "actuators_isolated", "sudo_usbmon", "allow_unknown_command",
            "allow_telemetry_state_change", "allow_line_state_trial", "allow_line_state_change", "dtr", "rts",
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

    def test_stateless_legacy_schedule_does_not_invent_state_authorization(self):
        schedule = (marvin_session.marvin_probe.ScheduledWrite(
            0.1, marvin_legacy_protocol.get_config_request(), "legacy-config"),)
        result = self.run_capture(probe_schedule=schedule, probe_profile="legacy",
                                  allow_unknown_command=True)
        self.assertFalse(result["telemetry_state_change_authorized"])
        self.assertFalse(self.serial.call_args.kwargs["allow_telemetry_state_change"])
        self.assertFalse(result["line_state_trial_authorized"])
        self.assertFalse(result["line_state_change_authorized"])
        self.assertFalse(self.serial.call_args.kwargs["allow_line_state_change"])
        self.assertFalse(self.serial.call_args.kwargs["dtr"])
        self.assertFalse(self.serial.call_args.kwargs["rts"])

    def test_stateful_legacy_schedule_still_requires_explicit_authorization(self):
        schedule = (marvin_session.marvin_probe.ScheduledWrite(
            0.1, marvin_legacy_protocol.get_unit_info_request(), "legacy-unit"),)
        with self.assertRaisesRegex(ValueError, "telemetry-state"):
            self.run_capture(probe_schedule=schedule, probe_profile="legacy",
                             allow_unknown_command=True)
        self.preflight.assert_not_called()
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
                self.run_capture(probe_get_config=True, allow_line_state_change=True, **options)
        self.preflight.assert_not_called()
        self.popen.assert_not_called()

    def test_get_config_uses_one_exact_frame_after_recorder_readiness(self):
        result = self.run_capture(probe_get_config=True, allow_unknown_command=True,
                                  dtr=True, rts=True, allow_line_state_change=True)
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
            self.run_capture(probe_get_config=True, allow_unknown_command=True,
                             dtr=True, rts=True, allow_line_state_change=True)
        self.serial.assert_called_once()
        self.process.send_signal.assert_called_once_with(signal.SIGINT)
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["requested_probe_hex"], "efbe0000040000001133adde")

    def test_unit_info_requires_separate_state_change_acknowledgment(self):
        for extra in ({}, {"probe_get_config": True}, {"probe_cr": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.run_capture(probe_get_unit_info=True, allow_unknown_command=True,
                                 dtr=True, rts=True, allow_line_state_change=True, **extra)
        self.preflight.assert_not_called()

    def test_unit_info_is_one_explicit_stateful_request_not_a_handshake_sequence(self):
        result = self.run_capture(probe_get_unit_info=True, allow_unknown_command=True,
                                  allow_telemetry_state_change=True, dtr=True, rts=True,
                                  allow_line_state_change=True)
        self.serial.assert_called_once()
        self.assertEqual(self.serial.call_args.kwargs["probe"].hex(), "efbe01001b0000001736adde")
        self.assertEqual(result["probe_name"], "GetUnitInfo")
        self.assertEqual(result["source_expected_response_payload_bytes"], 12)
        self.assertTrue(result["telemetry_state_change_authorized"])

    def test_unit_info_uncertain_write_is_not_retried(self):
        self.serial.side_effect = marvin_session.marvin_probe.serial.SerialTimeoutException("write outcome unknown")
        with self.assertRaisesRegex(OSError, "outcome unknown"):
            self.run_capture(probe_get_unit_info=True, allow_unknown_command=True,
                             allow_telemetry_state_change=True, dtr=True, rts=True,
                             allow_line_state_change=True)
        self.serial.assert_called_once()
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_sensor_info_requires_all_guards_before_hardware(self):
        defaults = {"probe_get_sensor_info": True, "allow_unknown_command": True,
                    "allow_telemetry_state_change": True, "dtr": True, "rts": True,
                    "allow_line_state_change": True}
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
                                  allow_line_state_change=True,
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
                             allow_telemetry_state_change=True, dtr=True, rts=True,
                             allow_line_state_change=True)
        self.serial.assert_called_once()
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")

    def test_sensor_info_cli_forwards_explicit_flags_and_rejects_combined_probes(self):
        argv = ["marvin_session", "--output", str(self.output), "--actuators-isolated",
                "--probe-get-sensor-info", "--allow-unknown-command",
                "--allow-telemetry-state-change", "--dtr", "--rts",
                "--allow-line-state-change",
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
            {"allow_unknown_command": True, "allow_telemetry_state_change": True, "dtr": True},
            {"allow_unknown_command": True, "allow_line_state_trial": True, "rts": True},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.run_capture(probe_schedule=schedule, **extra)
        self.preflight.assert_not_called()

    def test_nondefault_schedule_lines_require_separate_exact_boolean_authorization(self):
        for profile in ("modern", "legacy", "experimental-successor"):
            data = marvin_legacy_protocol.get_config_request() if profile == "legacy" else b"\r"
            schedule = (marvin_session.marvin_probe.ScheduledWrite(0.1, data, "query/0"),)
            for dtr, rts in ((True, True), (True, False), (False, True)):
                for consent in ({}, {"allow_line_state_trial": False},
                                *({"allow_line_state_trial": value}
                                  for value in (1, 0, "true", "false", None, [], [True]))):
                    with self.subTest(profile=profile, dtr=dtr, rts=rts, consent=consent):
                        with self.assertRaisesRegex(ValueError, "line-state|allow_line_state_trial"):
                            self.run_capture(
                                probe_schedule=schedule, probe_profile=profile, dtr=dtr, rts=rts,
                                allow_unknown_command=True, allow_telemetry_state_change=True,
                                **consent,
                            )
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_authorized_schedule_lines_are_recorded_for_all_profiles(self):
        for profile in ("modern", "legacy", "experimental-successor"):
            data = marvin_legacy_protocol.get_config_request() if profile == "legacy" else b"\r"
            schedule = (marvin_session.marvin_probe.ScheduledWrite(0.1, data, "query/0"),)
            for dtr, rts in ((True, True), (True, False), (False, False), (False, True)):
                with self.subTest(profile=profile, dtr=dtr, rts=rts):
                    self.output = Path(self.temp.name) / f"{profile}-{dtr}-{rts}"
                    self.clock.now = 0
                    result = self.run_capture(
                        probe_schedule=schedule, probe_profile=profile, dtr=dtr, rts=rts,
                        allow_unknown_command=True, allow_telemetry_state_change=profile != "legacy",
                        allow_line_state_trial=True,
                    )
                    self.assertEqual(result["status"], "completed")
                    self.assertIs(result["line_state_trial_authorized"], True)
                    recorded = json.loads((self.output / "metadata.json").read_text())
                    self.assertIs(recorded["line_state_trial_authorized"], True)
                    options = self.serial.call_args.kwargs
                    self.assertIs(options["allow_line_state_change"], False)
                    self.assertIs(options["allow_line_state_trial"], True)
                    self.assertIs(result["line_state_change_authorized"], True)
                    self.assertIs(options["dtr"], dtr)
                    self.assertIs(options["rts"], rts)
                    self.assertEqual(options["probe_schedule"], schedule)
                    self.assertEqual(options["probe_profile"], profile)
                    self.assertEqual(result["requested_application_bytes"], len(data))
                    self.assertIs(result["telemetry_state_change_authorized"], profile != "legacy")

    def test_schedule_line_authorization_does_not_bypass_transmit_allowlist(self):
        for profile in ("modern", "legacy", "experimental-successor"):
            schedule = (marvin_session.marvin_probe.ScheduledWrite(0.1, b"erase\r", "bad/0"),)
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                self.run_capture(
                    probe_schedule=schedule, probe_profile=profile, dtr=True, rts=True,
                    allow_unknown_command=True, allow_telemetry_state_change=True,
                    allow_line_state_trial=True,
                )
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_generic_consent_also_authorizes_valid_schedules_without_a_trial(self):
        for profile in ("modern", "legacy", "experimental-successor"):
            with self.subTest(profile=profile):
                self.output = Path(self.temp.name) / profile
                self.clock.now = 0
                data = marvin_legacy_protocol.get_config_request() if profile == "legacy" else b"\r"
                schedule = (marvin_session.marvin_probe.ScheduledWrite(0.1, data, "query"),)
                result = self.run_capture(
                    probe_schedule=schedule, probe_profile=profile, dtr=True,
                    allow_unknown_command=True, allow_telemetry_state_change=profile != "legacy",
                    allow_line_state_change=True,
                )
                self.assertIs(result["line_state_trial_authorized"], False)
                self.assertIs(result["line_state_change_authorized"], True)
                self.assertIs(self.serial.call_args.kwargs["allow_line_state_change"], True)
                self.assertEqual(self.serial.call_args.kwargs["probe_schedule"], schedule)

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
        self.assertFalse(result["line_state_trial_authorized"])
        self.assertFalse(self.serial.call_args.kwargs["dtr"])
        self.assertFalse(self.serial.call_args.kwargs["rts"])

    def test_named_queries_still_reject_changed_framing(self):
        for probe in ("probe_get_config", "probe_get_unit_info", "probe_get_sensor_info"):
            with self.subTest(probe=probe), self.assertRaises(ValueError):
                self.run_capture(**{probe: True}, allow_unknown_command=True,
                                 allow_telemetry_state_change=True, dtr=True, rts=True,
                                 allow_line_state_change=True,
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
            {"monotonic": 10**500}, {"monotonic": -(10**500)},
        ):
            with self.subTest(overrides=overrides):
                self.output = Path(self.temp.name) / f"bad-ready-{len(list(Path(self.temp.name).iterdir()))}"
                self.finished = False
                self.ready["monotonic"] = self.clock.now
                self.ready_overrides = overrides
                with self.assertRaisesRegex(ValueError, "capability or hard deadline"):
                    self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
                metadata = json.loads((self.output / "metadata.json").read_text())
                self.assertEqual(metadata["status"], "failed")
        self.serial.assert_not_called()

    def test_final_capability_deadline_and_stop_time_must_agree(self):
        for overrides in (
            {"coordinator_stop": False}, {"deadline_monotonic": 5.5},
            {"stopped_monotonic": 1}, {"stop_reason": "duration"},
            {"stop_reason": "signal"}, {"signal": signal.SIGINT},
            {"stopped_monotonic": float("nan")}, {"stopped_monotonic": 999},
            {"stopped_monotonic": 10**500}, {"stopped_monotonic": -(10**500)},
        ):
            with self.subTest(overrides=overrides):
                self.output = Path(self.temp.name) / f"bad-final-{len(list(Path(self.temp.name).iterdir()))}"
                self.finished = False
                self.ready["monotonic"] = self.clock.now
                self.usb_final_overrides = overrides
                with self.assertRaises(OSError):
                    self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
                metadata = json.loads((self.output / "metadata.json").read_text())
                self.assertEqual(metadata["status"], "failed")

    def test_invalid_grace_and_total_budget_fail_before_any_io(self):
        for grace in (-1, 31, float("nan"), float("inf"), True, "30", None,
                      10**500, -(10**500)):
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
        self.assertIs(self.serial.call_args.kwargs["allow_line_state_trial"], True)
        self.assertIs(self.serial.call_args.kwargs["allow_line_state_change"], False)
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

    def test_numeric_type_validation_precedes_coordinator_preflight(self):
        for field, values in (
            ("seconds", (True, False, None, "1", [], float("inf"), float("nan"))),
            ("baudrate", (True, False, 1.5, 115200.0, None, "115200", [], float("inf"),
                          299, 1_000_001, 10**500)),
            ("usb_tail_seconds", (True, False, None, "5", [])),
            ("probe_delay", (True, False, None, "0", [])),
        ):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.run_capture(**{field: value})
        self.preflight.assert_not_called()
        self.popen.assert_not_called()
        self.serial.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_no_clobber(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_capture()
        self.preflight.assert_not_called()
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

    def test_completed_recorder_metadata_with_evidence_gaps_cannot_complete_session(self):
        for field in ("unretained_partial_line_bytes", "unprocessed_records",
                      "unprocessed_record_bytes", "unaccounted_retained_bytes"):
            with self.subTest(field=field):
                self.output = Path(self.temp.name) / field
                self.clock.now = 0
                self.finished = False
                self.ready["monotonic"] = 0
                self.usb_final_overrides = {field: 1}
                with self.assertRaisesRegex(ValueError, "Incomplete USB capture"):
                    self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
                result = json.loads((self.output / "metadata.json").read_text())
                self.assertEqual(result["status"], "failed")
                self.assertIn(field, result["error"])
                self.assertTrue((self.output / "SHA256SUMS").exists())

    def test_identity_loss_aborts_before_open_and_stops_recorder(self):
        self.identity.side_effect = OSError("device removed")
        with self.assertRaisesRegex(OSError, "device removed"):
            self.run_capture()
        self.serial.assert_not_called()
        self.process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_binary_final_statistics_must_be_explicit_integer_zeros(self):
        invalid = (None, {}, [], {"queued": 0}, {"queued": 1, "dropped": 0},
                   {"queued": 0, "dropped": 1}, {"queued": False, "dropped": 0},
                   {"queued": 0, "dropped": 0.0}, {"queued": -1, "dropped": 0},
                   {"queued": "0", "dropped": 0})
        for index, stats in enumerate(invalid):
            with self.subTest(stats=stats):
                self.output = Path(self.temp.name) / f"stats-{index}"
                self.clock.now = 0
                self.finished = False
                self.ready["monotonic"] = 0
                self.usb_final_overrides = {"monitor_final_stats": stats}
                with self.assertRaisesRegex(ValueError, "final queued/dropped"):
                    self.run_capture(usb_tail_seconds=5, usb_close_grace_seconds=30)
                result = json.loads((self.output / "metadata.json").read_text())
                self.assertEqual(result["status"], "failed")
                self.assertIn("final queued/dropped", result["error"])
                self.assertTrue((self.output / "SHA256SUMS").exists())

    def test_missing_binary_stats_fail_but_text_does_not_invent_them(self):
        capture = self.serial.side_effect

        def without_stats(*args, **kwargs):
            (self.output / "usb/metadata.json").write_text(json.dumps({
                "status": "completed", "signal": None,
            }))
            return capture(*args, **kwargs)

        self.serial.side_effect = without_stats
        with self.assertRaisesRegex(ValueError, "final queued/dropped"):
            self.run_capture()
        self.output = Path(self.temp.name) / "text"
        self.clock.now = 0
        self.finished = False
        self.assertEqual(self.run_capture(usbmon_backend="text")["status"], "completed")

    def test_sealing_failure_is_failed_and_preserves_transport_or_interrupt(self):
        for index, original in enumerate((None, OSError("original transport"), KeyboardInterrupt())):
            with self.subTest(original=original):
                self.output = Path(self.temp.name) / f"sealing-{index}"
                self.clock.now = 0
                self.finished = False
                self.serial.side_effect = original if original is not None else self.capture
                error = OSError("manifest write failed")
                with patch.object(marvin_session, "evidence_manifest", side_effect=error):
                    with self.assertRaises(type(original) if original is not None else OSError) as raised:
                        self.run_capture()
                self.assertIs(raised.exception, original if original is not None else error)
                metadata = json.loads((self.output / "metadata.json").read_text())
                self.assertEqual(metadata["status"], "failed")
                self.assertEqual(metadata["evidence_sealing_error"], str(error))
                if isinstance(original, OSError):
                    self.assertEqual(metadata["error"], str(original))
                if original is not None:
                    self.assertIn("Evidence sealing also failed", original.__notes__[-1])

    def test_sealing_failure_preserves_recorder_shutdown_failure(self):
        original = OSError("recorder shutdown failed")
        with patch.object(marvin_session, "stop_recorder", side_effect=original), \
                patch.object(marvin_session, "evidence_manifest", side_effect=OSError("manifest failed")), \
                self.assertRaises(OSError) as raised:
            self.run_capture()
        self.assertIs(raised.exception, original)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["shutdown_error"], str(original))
        self.assertEqual(metadata["evidence_sealing_error"], "manifest failed")

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
    def test_hash_failure_marks_metadata_failed_without_modifying_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence = output / "data"
            evidence.write_bytes(b"retained evidence")
            with patch.object(marvin_session.hashlib, "file_digest", side_effect=ValueError("hash failed")), \
                    self.assertRaisesRegex(ValueError, "hash failed"):
                marvin_session.seal_evidence(output, {"status": "completed"})
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "failed")
            self.assertEqual(metadata["evidence_sealing_error"], "hash failed")
            self.assertEqual(evidence.read_bytes(), b"retained evidence")
            self.assertFalse((output / "SHA256SUMS").exists())

    def test_partial_manifest_is_retained_but_never_success_shaped(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            def partial_manifest(path):
                (path / "SHA256SUMS").write_bytes(b"partial digest")
                raise OSError("manifest disk full")

            with patch.object(marvin_session, "evidence_manifest", side_effect=partial_manifest), \
                    self.assertRaisesRegex(OSError, "disk full"):
                marvin_session.seal_evidence(output, {"status": "completed"})
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "failed")
            self.assertEqual(metadata["evidence_sealing_error"], "manifest disk full")
            self.assertEqual((output / "SHA256SUMS").read_bytes(), b"partial digest")

    def test_root_manifest_includes_nested_manifests_and_detects_their_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            child = output / "segment"
            child.mkdir()
            (child / "data").write_bytes(b"retained evidence")
            marvin_session.evidence_manifest(child)
            nested = child / "SHA256SUMS"
            marvin_session.evidence_manifest(output)
            hashes = dict(line.split("  ", 1)[::-1]
                          for line in (output / "SHA256SUMS").read_text().splitlines())
            self.assertEqual(set(hashes), {"segment/data", "segment/SHA256SUMS"})
            self.assertEqual(hashes["segment/SHA256SUMS"], hashlib.sha256(nested.read_bytes()).hexdigest())
            nested.write_bytes(b"changed nested manifest")
            self.assertNotEqual(hashes["segment/SHA256SUMS"], hashlib.sha256(nested.read_bytes()).hexdigest())

    def test_final_metadata_write_failure_is_recorded_and_seal_is_not_attempted(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            metadata = {"status": "completed"}
            error = OSError("final metadata write failed")
            with patch.object(marvin_session, "write_json", side_effect=[error, None]) as write, \
                    patch.object(marvin_session, "evidence_manifest") as manifest:
                with self.assertRaises(OSError) as raised:
                    marvin_session.seal_evidence(output, metadata)
            self.assertIs(raised.exception, error)
            self.assertEqual(metadata["status"], "failed")
            self.assertEqual(metadata["evidence_sealing_error"], str(error))
            self.assertEqual(write.call_count, 2)
            manifest.assert_not_called()

    def test_persistent_metadata_failure_never_masks_an_active_capture_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            original = OSError("original capture error")
            metadata = {"status": "failed", "error": str(original)}
            with patch.object(marvin_session, "write_json", side_effect=OSError("disk unavailable")), \
                    self.assertRaises(OSError) as raised:
                try:
                    raise original
                finally:
                    marvin_session.seal_evidence(output, metadata)
            self.assertIs(raised.exception, original)
            self.assertEqual(metadata["error"], str(original))
            self.assertEqual(metadata["evidence_failure_metadata_error"], "disk unavailable")
            self.assertTrue(any("Could not persist" in note for note in original.__notes__))

    def test_successful_scope_does_not_suppress_sealing_error_inside_unrelated_except(self):
        with tempfile.TemporaryDirectory() as directory:
            error = OSError("manifest error")
            try:
                raise ValueError("unrelated caller error")
            except ValueError:
                with patch.object(marvin_session, "evidence_manifest", side_effect=error), \
                        self.assertRaises(OSError) as raised:
                    marvin_session.seal_evidence(Path(directory), {"status": "completed"})
            self.assertIs(raised.exception, error)

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
