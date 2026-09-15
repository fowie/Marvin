import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_protocol, marvin_probe, marvin_protocol


IDENTITY = (
    "ID_VENDOR_ID=045e\n"
    "ID_MODEL_ID=4444\n"
    "ID_MM_DEVICE_IGNORE=1\n"
    "ID_MM_PORT_IGNORE=1\n"
    "ID_SERIAL_SHORT=12345678\n"
)


class ListenTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "capture"
        self.transport = Mock()
        self.transport.read.return_value = b"abc"
        self.factory = patch.object(
            marvin_probe.serial, "Serial", return_value=self.transport
        ).start()
        self.udev = patch.object(
            marvin_probe.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, IDENTITY, ""),
        ).start()
        self.ownership = patch.object(marvin_probe, "check_port_available").start()
        self.ioctl = patch.object(marvin_probe.fcntl, "ioctl").start()
        self.addCleanup(patch.stopall)

    def capture(self, **overrides):
        options = dict(
            seconds=1,
            baudrate=115200,
            max_bytes=3,
            actuators_isolated=True,
        )
        options.update(overrides)
        return marvin_probe.capture("/dev/test-marvin", self.output, **options)

    def test_capture_preserves_bytes_and_never_writes_to_device(self):
        self.transport.open.side_effect = lambda: self.assertEqual(
            (self.transport.dtr, self.transport.rts), (False, False)
        )
        result = self.capture()
        self.assertEqual((self.output / "received.bin").read_bytes(), b"abc")
        chunk = json.loads((self.output / "chunks.jsonl").read_text())
        self.assertEqual(chunk["hex"], "616263")
        self.assertEqual(chunk["offset"], 0)
        self.assertEqual(result["stop_reason"], "byte_limit")
        self.transport.write.assert_not_called()
        self.transport.close.assert_called_once()
        self.assertTrue(self.factory.call_args.kwargs["exclusive"])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(result["kernel_exclusive_open"])
        events = [json.loads(line) for line in (self.output / "events.jsonl").read_text().splitlines()]
        names = [entry["event"] for entry in events]
        self.assertLess(names.index("open_attempt"), names.index("open_completed"))
        self.assertLess(names.index("received"), names.index("close_attempt"))
        self.assertEqual(names[-1], "close_completed")
        self.assertTrue(all("monotonic_seconds" in entry and "at" in entry for entry in events))

    def test_quiet_capture_exits_at_deadline(self):
        with patch.object(marvin_probe.time, "monotonic", side_effect=itertools.count()):
            result = self.capture(seconds=0.5)
        self.assertEqual(result["bytes_received"], 0)
        self.assertEqual(result["stop_reason"], "duration_limit")
        self.transport.read.assert_not_called()

    def test_dtr_is_asserted_only_after_open_when_requested(self):
        self.transport.open.side_effect = lambda: self.assertFalse(self.transport.dtr)
        result = self.capture(dtr=True, allow_line_state_change=True)
        self.assertTrue(self.transport.dtr)
        self.assertFalse(self.transport.rts)
        self.assertFalse(result["dtr_initial_requested"])
        self.assertTrue(result["dtr_requested"])
        self.assertIn("dtr_asserted_at", result)
        self.transport.write.assert_not_called()

    def test_both_control_lines_are_asserted_after_open_when_requested(self):
        self.transport.open.side_effect = lambda: self.assertEqual(
            (self.transport.dtr, self.transport.rts), (False, False)
        )
        result = self.capture(dtr=True, rts=True, allow_line_state_change=True)
        self.assertTrue(self.transport.dtr)
        self.assertTrue(self.transport.rts)
        self.assertFalse(result["rts_initial_requested"])
        self.assertTrue(result["rts_requested"])
        self.assertIn("rts_asserted_at", result)
        self.transport.write.assert_not_called()

    def test_requires_isolation_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "isolation"):
            self.capture(actuators_isolated=False)
        self.factory.assert_not_called()

    def test_non_boolean_flags_cannot_authorize_device_access(self):
        for name in ("actuators_isolated", "allow_unknown_command", "allow_telemetry_state_change",
                     "allow_line_state_change", "dtr", "rts", "line_state_at_open"):
            for value in (1, 0, "false", "true", None, [], [True]):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, "boolean"):
                    self.capture(**{name: value})
        self.udev.assert_not_called()
        self.factory.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_initial_requested_line_state_has_no_post_open_changes(self):
        self.transport.open.side_effect = lambda: self.assertEqual(
            (self.transport.dtr, self.transport.rts), (True, True)
        )
        result = self.capture(dtr=True, rts=True, line_state_at_open=True, allow_line_state_change=True)
        self.assertTrue(result["dtr_initial_requested"])
        self.assertTrue(result["rts_initial_requested"])
        events = (self.output / "events.jsonl").read_text()
        self.assertNotIn("dtr_change_attempt", events)
        self.assertNotIn("rts_change_attempt", events)
        self.transport.open.assert_called_once()
        self.transport.write.assert_not_called()

    def test_line_assertions_require_exact_separate_consent_before_any_device_or_output(self):
        for profile in ("modern", "legacy", "experimental-successor"):
            data = (marvin_legacy_protocol.get_config_request() if profile == "legacy"
                    else marvin_protocol.get_config_request())
            for dtr, rts in ((True, False), (False, True), (True, True)):
                for at_open in (False, True):
                    for selection in ({}, {"probe": data}, {"probe_schedule": (
                        marvin_probe.ScheduledWrite(0, data, "config"),)}):
                        for consent in ({}, {"allow_line_state_change": False},
                                        *({"allow_line_state_change": value}
                                          for value in (1, 0, "true", "false", None, [], [True], object()))):
                            with self.subTest(profile=profile, lines=(dtr, rts), at_open=at_open,
                                              selection=selection, consent=consent):
                                with self.assertRaisesRegex(ValueError, "line-state|allow_line_state_change"):
                                    self.capture(
                                        dtr=dtr, rts=rts, line_state_at_open=at_open,
                                        probe_profile=profile, allow_unknown_command=True,
                                        allow_telemetry_state_change=True, **selection, **consent,
                                    )
        self.udev.assert_not_called()
        self.ownership.assert_not_called()
        self.factory.assert_not_called()
        self.ioctl.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_authorized_lines_and_unconsented_low_defaults_keep_open_timing_and_metadata(self):
        for dtr, rts in ((False, False), (True, False), (False, True), (True, True)):
            for at_open in (False, True):
                with self.subTest(lines=(dtr, rts), at_open=at_open):
                    self.output = Path(self.directory.name) / f"lines-{dtr}-{rts}-{at_open}"
                    initial = (dtr, rts) if at_open else (False, False)
                    self.transport.open.side_effect = lambda: self.assertEqual(
                        (self.transport.dtr, self.transport.rts), initial)
                    result = self.capture(
                        dtr=dtr, rts=rts, line_state_at_open=at_open,
                        allow_line_state_change=dtr or rts,
                    )
                    self.assertEqual((self.transport.dtr, self.transport.rts), (dtr, rts))
                    self.assertIs(result["line_state_change_authorized"], dtr or rts)
                    recorded = json.loads((self.output / "metadata.json").read_text())
                    self.assertIs(recorded["line_state_change_authorized"], dtr or rts)
                    self.transport.write.assert_not_called()

    def test_cli_asserted_lines_require_and_forward_separate_consent(self):
        for lines in (["--dtr"], ["--rts"], ["--dtr", "--rts"]):
            for at_open in ([], ["--line-state-at-open"]):
                with self.subTest(lines=lines, at_open=at_open):
                    self.output = Path(self.directory.name) / f"cli-{len(list(Path(self.directory.name).iterdir()))}"
                    argv = ["marvin_probe", "--actuators-isolated", "--output", str(self.output),
                            "--seconds", "1", "--max-bytes", "3", *lines, *at_open]
                    self.udev.reset_mock()
                    self.factory.reset_mock()
                    with patch.object(sys, "argv", argv), patch.object(sys, "stderr"):
                        self.assertEqual(marvin_probe.main(), 1)
                    self.udev.assert_not_called()
                    self.factory.assert_not_called()
                    self.assertFalse(self.output.exists())
                    with patch.object(sys, "argv", [*argv, "--allow-line-state-change"]), \
                            patch.object(sys, "stdout"):
                        self.assertEqual(marvin_probe.main(), 0)
                    self.factory.assert_called_once()
                    self.assertIs(json.loads((self.output / "metadata.json").read_text())[
                        "line_state_change_authorized"], True)

    def test_guard_failure_prevents_open(self):
        guard = Mock(side_effect=OSError("USB recorder stopped"))
        with self.assertRaisesRegex(OSError, "USB recorder stopped"):
            self.capture(guard=guard)
        self.transport.open.assert_not_called()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")

    def test_guard_failure_during_reads_preserves_data_and_closes(self):
        self.transport.read.return_value = b"a"
        guard = Mock(side_effect=[None, None, OSError("USB identity changed")])
        with self.assertRaisesRegex(OSError, "USB identity changed"):
            self.capture(guard=guard)
        self.assertEqual((self.output / "received.bin").read_bytes(), b"a")
        self.transport.open.assert_called_once()
        self.transport.close.assert_called_once()

    def test_existing_owner_prevents_open(self):
        self.ownership.side_effect = ValueError("Serial port already has an owner")
        with self.assertRaisesRegex(ValueError, "owner"):
            self.capture()
        self.factory.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_exclusive_ioctl_failure_closes_and_reports_failure(self):
        self.ioctl.side_effect = OSError("TIOCEXCL failed")
        with self.assertRaisesRegex(OSError, "TIOCEXCL"):
            self.capture()
        self.transport.close.assert_called_once()
        self.transport.read.assert_not_called()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")

    def test_rejects_unbounded_or_invalid_inputs(self):
        for options in (
            {"seconds": 0},
            {"seconds": float("inf")},
            {"seconds": float("nan")},
            {"seconds": 121},
            {"baudrate": 0},
            {"max_bytes": 0},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.capture(**options)
        self.factory.assert_not_called()

    def test_rejects_wrong_usb_identity(self):
        self.udev.return_value.stdout = IDENTITY.replace("4444", "0721")
        with self.assertRaisesRegex(ValueError, "other than"):
            self.capture()
        self.factory.assert_not_called()

    def test_requires_both_modem_exclusion_tags(self):
        for tag in ("ID_MM_DEVICE_IGNORE", "ID_MM_PORT_IGNORE"):
            self.udev.return_value.stdout = IDENTITY.replace(f"{tag}=1\n", "")
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "exclusion"):
                self.capture()
        self.factory.assert_not_called()

    def test_does_not_overwrite_existing_capture(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.capture()
        self.factory.assert_not_called()

    def test_disconnect_preserves_partial_data_and_error(self):
        self.transport.read.side_effect = [
            b"a",
            marvin_probe.serial.SerialException("device disconnected"),
        ]
        with self.assertRaises(marvin_probe.serial.SerialException):
            self.capture()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["bytes_received"], 1)
        self.assertEqual((self.output / "received.bin").read_bytes(), b"a")
        self.transport.write.assert_not_called()
        self.transport.close.assert_called_once()

    def test_close_failure_is_not_reported_as_completed(self):
        self.transport.close.side_effect = OSError("close failed")
        with self.assertRaisesRegex(OSError, "close failed"):
            self.capture()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["close_error"], "close failed")
        self.assertEqual((self.output / "received.bin").read_bytes(), b"abc")

    def test_probe_requires_separate_authorization(self):
        with self.assertRaisesRegex(ValueError, "authorization"):
            self.capture(probe=b"\r")
        self.factory.assert_not_called()

    def test_probe_is_written_exactly_once_and_logged(self):
        self.transport.write.return_value = 1
        result = self.capture(probe=b"\r", allow_unknown_command=True)
        self.transport.write.assert_called_once_with(b"\r")
        self.assertEqual(result["requested_probe_hex"], "0d")
        self.assertEqual(result["application_bytes_written"], 1)
        self.assertEqual(result["transmit_status"], "written")

    def test_invalid_probe_is_rejected_before_open(self):
        for probe in (b"", b"x" * 17, "not bytes"):
            with self.subTest(probe=probe), self.assertRaisesRegex(ValueError, "16 bytes"):
                self.capture(probe=probe, allow_unknown_command=True)
        self.factory.assert_not_called()

    def test_authorization_never_allows_arbitrary_bytes_or_cross_profile_requests(self):
        getter = marvin_protocol.get_config_request()
        for data in (b"\x80", b"erase\r", b"\xef", getter[:-1], getter + b"\x80",
                     marvin_legacy_protocol.get_config_request(),
                     bytes.fromhex("efbe0000040000001033adde")):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.capture(probe=data, allow_unknown_command=True, allow_telemetry_state_change=True,
                             dtr=True, allow_line_state_change=True)
            schedule = [marvin_probe.ScheduledWrite(0, data[:1], "a")]
            if len(data) > 1:
                schedule.append(marvin_probe.ScheduledWrite(0.1, data[1:], "b"))
            with self.subTest(schedule=schedule), self.assertRaises(ValueError):
                self.capture(probe_schedule=schedule, allow_unknown_command=True,
                             allow_telemetry_state_change=True, dtr=True, allow_line_state_change=True)
        self.udev.assert_not_called()
        self.factory.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_stateful_raw_getter_requires_its_own_acknowledgment(self):
        with self.assertRaisesRegex(ValueError, "telemetry-state"):
            self.capture(probe=marvin_protocol.get_unit_info_request(), allow_unknown_command=True)
        self.udev.assert_not_called()
        self.factory.assert_not_called()

    def test_cli_exposes_fixed_named_requests_and_rejects_raw_hex(self):
        for name, expected in (
            ("cr", b"\r"), ("get-config", marvin_protocol.get_config_request()),
            ("get-unit-info", marvin_protocol.get_unit_info_request()),
            ("get-sensor-info", marvin_protocol.get_sensor_info_request()),
        ):
            args = ["marvin_probe", "--actuators-isolated", "--output", str(self.output),
                    "--probe", name, "--allow-unknown-command", "--allow-telemetry-state-change"]
            with self.subTest(name=name), patch.object(sys, "argv", args), \
                    patch.object(sys, "stdout"), patch.object(marvin_probe, "capture", return_value={
                        "bytes_received": 1, "application_bytes_written": len(expected),
                        "stop_reason": "byte_limit",
                    }) as capture:
                marvin_probe.main()
                self.assertEqual(capture.call_args.kwargs["probe"], expected)
                self.assertTrue(capture.call_args.kwargs["allow_telemetry_state_change"])
        with patch.object(sys, "argv", ["marvin_probe", "--actuators-isolated",
                                      "--output", str(self.output), "--probe-hex", "80"]), \
                patch.object(sys, "stderr"), patch.object(marvin_probe, "capture") as capture, \
                self.assertRaises(SystemExit):
            marvin_probe.main()
        capture.assert_not_called()

    def test_explicit_legacy_profile_accepts_only_a_complete_getter(self):
        data = marvin_legacy_protocol.get_config_request(12)
        self.transport.write.return_value = len(data)
        result = self.capture(probe=data, probe_profile="legacy", allow_unknown_command=True)
        self.transport.write.assert_called_once_with(data)
        self.assertEqual(result["probe_profile"], "legacy")

    def test_short_write_is_not_retried(self):
        self.transport.write.return_value = 0
        with self.assertRaises(marvin_probe.serial.SerialTimeoutException):
            self.capture(probe=b"\r", allow_unknown_command=True)
        self.transport.write.assert_called_once_with(b"\r")
        self.transport.read.assert_not_called()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["application_bytes_written"], 0)
        self.assertEqual(metadata["transmit_status"], "short_write")
        self.assertEqual(metadata["status"], "failed")

    def test_write_timeout_does_not_claim_zero_bytes_written(self):
        self.transport.write.side_effect = marvin_probe.serial.SerialTimeoutException(
            "write timed out"
        )
        with self.assertRaises(marvin_probe.serial.SerialTimeoutException):
            self.capture(probe=b"\r", allow_unknown_command=True)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertIsNone(metadata["application_bytes_written"])
        self.assertEqual(metadata["transmit_status"], "unknown")
        self.transport.write.assert_called_once_with(b"\r")

    def test_interrupted_write_records_unknown_count(self):
        self.transport.write.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.capture(probe=b"\r", allow_unknown_command=True)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertIsNone(metadata["application_bytes_written"])
        self.assertEqual(metadata["transmit_status"], "unknown")
        self.assertEqual(metadata["status"], "interrupted")

    def scheduled_capture(self, *, rx="none", failure=None, stall_first=False):
        clock = [0.0]
        writes = []
        supplied = [False]

        def read(size):
            clock[0] += self.transport.timeout
            if not supplied[0] and (rx == "early" or (rx == "after-first" and writes)):
                supplied[0] = True
                return b"reply"
            return b""

        def write(data):
            writes.append((clock[0], data))
            if len(writes) == 2:
                if failure == "timeout":
                    raise marvin_probe.serial.SerialTimeoutException("unknown scheduled write")
                if failure == "interrupt":
                    raise KeyboardInterrupt
                if failure == "short":
                    return 0
            return len(data)

        def guard():
            if stall_first and not writes and self.transport.timeout == 0:
                clock[0] = max(clock[0], 0.4)

        self.transport.read.side_effect = read
        self.transport.write.side_effect = write
        schedule = (
            marvin_probe.ScheduledWrite(0.1, b"h\r\n", "first/0"),
            marvin_probe.ScheduledWrite(0.3, b"?\r\n", "second/0"),
        )
        with patch.object(marvin_probe.time, "monotonic", side_effect=lambda: clock[0]):
            result = self.capture(seconds=0.9, max_bytes=64, allow_unknown_command=True,
                                  probe_schedule=schedule, guard=guard, line_state_at_open=True,
                                  probe_profile="experimental-successor",
                                  dtr=True, rts=True, allow_line_state_change=True,
                                  bytesize=7, parity="E", stopbits=2)
        return result, writes

    def test_schedule_keeps_one_open_and_preserves_minimum_response_spacing(self):
        result, writes = self.scheduled_capture(stall_first=True)
        self.assertEqual([data for _, data in writes], [b"h\r\n", b"?\r\n"])
        self.assertGreaterEqual(writes[0][0], 0.4)
        self.assertGreaterEqual(writes[1][0], writes[0][0] + (0.3 - 0.1))
        self.transport.open.assert_called_once()
        self.transport.close.assert_called_once()
        self.assertEqual(result["scheduled_writes_completed"], 2)
        self.assertEqual(result["application_bytes_written"], 6)
        self.assertEqual(result["known_application_bytes_written"], 6)
        self.assertEqual(result["transmit_status"], "written")
        self.assertEqual(result["framing"], "7E2")
        self.assertEqual(self.factory.call_args.kwargs["bytesize"], 7)
        self.assertEqual(self.factory.call_args.kwargs["parity"], "E")
        self.assertEqual(self.factory.call_args.kwargs["stopbits"], 2)

    def test_early_rx_suppresses_the_entire_schedule(self):
        result, writes = self.scheduled_capture(rx="early")
        self.assertEqual(writes, [])
        self.assertEqual(result["scheduled_writes_completed"], 0)
        self.assertEqual(result["application_bytes_written"], 0)
        self.assertEqual(result["transmit_status"], "suppressed_schedule_rx")
        self.assertEqual((self.output / "received.bin").read_bytes(), b"reply")

    def test_rx_after_first_write_suppresses_all_remaining_writes(self):
        result, writes = self.scheduled_capture(rx="after-first")
        self.assertEqual([data for _, data in writes], [b"h\r\n"])
        self.assertEqual(result["scheduled_writes_completed"], 1)
        self.assertEqual(result["application_bytes_written"], 3)
        self.assertEqual(result["transmit_status"], "suppressed_schedule_rx")
        events = [json.loads(line) for line in (self.output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(next(e for e in events if e["event"] == "schedule_suppressed")["writes_remaining"], 1)

    def test_schedule_timeout_preserves_known_prefix_and_unknown_total(self):
        with self.assertRaises(marvin_probe.serial.SerialTimeoutException):
            self.scheduled_capture(failure="timeout")
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["transmit_status"], "unknown")
        self.assertIsNone(result["application_bytes_written"])
        self.assertEqual(result["known_application_bytes_written"], 3)
        self.assertEqual(result["scheduled_writes_completed"], 1)
        self.assertEqual(self.transport.write.call_count, 2)

    def test_schedule_short_write_is_not_retried(self):
        with self.assertRaises(marvin_probe.serial.SerialTimeoutException):
            self.scheduled_capture(failure="short")
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["transmit_status"], "short_write")
        self.assertEqual(result["application_bytes_written"], 3)
        self.assertEqual(result["scheduled_writes_completed"], 1)
        self.assertEqual(self.transport.write.call_count, 2)

    def test_schedule_interrupt_retains_unknown_write(self):
        with self.assertRaises(KeyboardInterrupt):
            self.scheduled_capture(failure="interrupt")
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["status"], "interrupted")
        self.assertIsNone(result["application_bytes_written"])
        self.assertEqual(result["known_application_bytes_written"], 3)
        self.assertEqual(self.transport.write.call_count, 2)

    def test_schedule_validation_precedes_device_access(self):
        write = marvin_probe.ScheduledWrite
        for schedule in (
            (), [write(0, b"x", "x")] * 257, [write(0, b"", "x")],
            [write(0, b"x" * 33, "x")], [write(-1, b"x", "x")],
            [write(1, b"x", "x")], [write(float("nan"), b"x", "x")],
            [write(0.2, b"x", "x"), write(0.1, b"x", "y")],
            [write(0, b"x" * 32, "x")] * 129, [write(0, b"x", "")],
            [{"offset_seconds": 0, "data": b"x"}],
        ):
            with self.subTest(schedule=schedule), self.assertRaises(ValueError):
                self.capture(probe_schedule=schedule, allow_unknown_command=True)
        for extra in ({}, {"probe": b"\r", "allow_unknown_command": True},
                      {"probe_delay": 0.1, "allow_unknown_command": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.capture(probe_schedule=[write(0.1, b"x", "x")], **extra)
        self.udev.assert_not_called()
        self.factory.assert_not_called()

    def test_invalid_framing_is_rejected_before_device_access(self):
        for fields in ({"bytesize": 6}, {"parity": "M"}, {"stopbits": 1.5}, {"stopbits": True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.capture(**fields)
        self.factory.assert_not_called()

    def test_host_rejected_settings_are_identified_before_any_write(self):
        self.transport.open.side_effect = marvin_probe.serial.SerialException(
            "Could not configure port: (22, 'Invalid argument')"
        )
        with self.assertRaises(marvin_probe.SerialSettingsRejected):
            self.capture()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertTrue(metadata["settings_rejected_before_open"])
        self.assertEqual(metadata["application_bytes_written"], 0)
        self.assertEqual(metadata["error_errno"], 22)
        self.transport.write.assert_not_called()
        self.transport.close.assert_called_once()

    def test_other_open_failures_are_not_treated_as_rejected_settings(self):
        self.transport.open.side_effect = OSError(13, "Permission denied")
        with self.assertRaises(OSError) as error:
            self.capture()
        self.assertNotIsInstance(error.exception, marvin_probe.SerialSettingsRejected)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertFalse(metadata["settings_rejected_before_open"])

    def delayed_capture(self, *, early=None, late_poll=False, write_error=None, expire_in_guard=False):
        clock = [0.0]
        polled = [False]
        supplied = [False]
        write_times = []

        def read(size):
            if self.transport.timeout == 0:
                polled[0] = True
                return b"queued" if late_poll else b""
            clock[0] += self.transport.timeout
            if early is not None and not supplied[0]:
                supplied[0] = True
                return early
            return b""

        def write(data):
            write_times.append(clock[0])
            if write_error:
                raise write_error
            return len(data)

        def guard():
            if expire_in_guard and polled[0]:
                clock[0] += 2

        self.transport.read.side_effect = read
        self.transport.write.side_effect = write
        with patch.object(marvin_probe.time, "monotonic", side_effect=lambda: clock[0]):
            result = self.capture(seconds=6, max_bytes=65536, probe=b"\r",
                                  allow_unknown_command=True, probe_delay=5, guard=guard)
        return result, clock[0], write_times

    def test_delayed_probe_waits_and_writes_once(self):
        result, elapsed, writes = self.delayed_capture()
        self.assertEqual(result["transmit_status"], "written")
        self.assertEqual(len(writes), 1)
        self.assertGreaterEqual(writes[0], 5)
        self.assertGreaterEqual(elapsed, 6)
        self.transport.write.assert_called_once_with(b"\r")
        events = [json.loads(line) for line in (self.output / "events.jsonl").read_text().splitlines()]
        due = next(row["due_monotonic"] for row in events if row["event"] == "probe_scheduled")
        sent = next(row["monotonic_seconds"] for row in events if row["event"] == "write_attempt")
        self.assertGreaterEqual(sent, due)

    def test_pre_probe_bytes_suppress_write_but_do_not_end_capture(self):
        result, elapsed, writes = self.delayed_capture(early=b"banner")
        self.assertEqual(writes, [])
        self.assertGreaterEqual(elapsed, 6)
        self.assertEqual(result["transmit_status"], "suppressed_pre_probe_rx")
        self.assertEqual(result["application_bytes_written"], 0)
        self.assertEqual((self.output / "received.bin").read_bytes(), b"banner")

    def test_bytes_queued_at_send_deadline_also_suppress_write(self):
        result, _, writes = self.delayed_capture(late_poll=True)
        self.assertEqual(writes, [])
        self.assertEqual(result["transmit_status"], "suppressed_pre_probe_rx")
        self.assertEqual((self.output / "received.bin").read_bytes(), b"queued")

    def test_delayed_uncertain_write_is_not_retried(self):
        with self.assertRaises(marvin_probe.serial.SerialTimeoutException):
            self.delayed_capture(write_error=marvin_probe.serial.SerialTimeoutException("unknown"))
        self.transport.write.assert_called_once()
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["transmit_status"], "unknown")
        self.assertIsNone(result["application_bytes_written"])

    def test_delayed_probe_does_not_write_after_guard_exhausts_deadline(self):
        result, _, writes = self.delayed_capture(expire_in_guard=True)
        self.assertEqual(writes, [])
        self.assertEqual(result["transmit_status"], "not_sent_before_deadline")
        self.assertIn("capture_ended_before_write", (self.output / "events.jsonl").read_text())

    def test_bad_delays_are_rejected_before_open(self):
        for delay in (-1, 1, 31, float("nan"), float("inf")):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                self.capture(probe=b"\r", allow_unknown_command=True, probe_delay=delay)
        with self.assertRaises(ValueError):
            self.capture(probe_delay=0.1)
        self.factory.assert_not_called()


class OwnershipTests(unittest.TestCase):
    def test_existing_process_is_rejected(self):
        with patch.object(marvin_probe.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, "1234", "/dev/ttyACM0:")) as run:
            with self.assertRaisesRegex(ValueError, "1234"):
                marvin_probe.check_port_available("/dev/ttyACM0")
            self.assertEqual(run.call_args.args[0], ["fuser", "/dev/ttyACM0"])

    def test_no_owner_is_accepted_but_command_errors_are_not(self):
        for status, stdout, stderr in ((1, "", ""), (1, "", "cannot stat"), (2, "", "")):
            with self.subTest(status=status, stderr=stderr), patch.object(
                marvin_probe.subprocess, "run",
                return_value=subprocess.CompletedProcess([], status, stdout, stderr),
            ):
                if status == 1 and not stderr:
                    marvin_probe.check_port_available("/dev/ttyACM0")
                else:
                    with self.assertRaises(OSError):
                        marvin_probe.check_port_available("/dev/ttyACM0")


if __name__ == "__main__":
    unittest.main()
