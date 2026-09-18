"""Offline fixed disconnected-load LED-state round-trip tests."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests import test_marvin_session as session_tests
from tests.test_marvin_legacy_client import frame
from tools import marvin_legacy_disconnected_led_state as led
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_LED_STATE_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.DISCONNECTED_LED_STATE_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0

    def __init__(self, submit_fault=None):
        self.submit_fault = submit_fault
        self.attempts = []
        self.writes = 0
        self.test_setter_may_have_been_submitted = False
        self.restore_attempted = False
        self.restore_response_clean = False
        self.close = Mock()
        self.event = Mock()

    def revalidate(self, *, deadline):
        return self.token

    def submit(self, step, *, deadline):
        self.attempts.append(step)
        if step == "restore":
            if self.restore_attempted:
                raise OSError("restore retry")
            self.restore_attempted = True
        self.writes += 1
        if step == "test":
            self.test_setter_may_have_been_submitted = True
        if self.submit_fault == (step, "error"):
            raise OSError(step + " uncertain")
        if self.submit_fault == (step, "partial"):
            return 1
        return len(led.STEPS[step])


class DisconnectedLedStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".led-state-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_exact_frames_scope_and_no_knobs(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(led.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "53110c1700000075f145",
            "53120c18001200010000000000000000000000ff0000ff00008ab845",
            "53130c17000000741345",
            "53140c18001200000000000000000000000000ff0000ff000010fb45",
            "53150c17000000747545",
        ])
        self.assertEqual((plan["maximum_writes"], plan["maximum_application_bytes"]), (5, 86))
        self.assertEqual(plan["fixed_change"], {"index": 0, "from": 0, "to": 1})
        self.assertFalse(plan["automatic_retries"])
        self.assertFalse(plan["automatic_reconnect"])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            for knob in ("command", "payload", "sequence", "retry", "count", "value"):
                with self.assertRaises(SystemExit):
                    led.main(FLAGS + ["--" + knob, "1"])
        with patch.object(session, "preflight", side_effect=AssertionError("no preflight")):
            for name in consent.DISCONNECTED_LED_STATE_FLAGS:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    led.run_diagnostic(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(DECLARATIONS | {name: False}))

    def test_coordinator_recorder_and_shared_sealing_lifecycle(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.DISCONNECTED_LED_STATE_SCOPE)
        self.assertEqual(result["load_scope"], "MOTOR_POWER_PLUGS_DISCONNECTED")
        self.assertEqual(result["probe_name"], "DisconnectedLoadLedStateRoundTrip")
        self.assertFalse(result["unknown_command_authorized"])
        self.assertTrue(result["fixed_led_state_round_trip_authorized"])
        self.assertEqual(result["requested_application_bytes"], 86)
        for flag in FLAGS:
            self.assertIn(flag, command)
        led._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        led._validate_capture(capture.call_args.kwargs)

        with patch.object(led.zero, "_run_diagnostic", return_value={"status": "sealed"}) as lifecycle:
            self.assertEqual(led.run_diagnostic(
                self.root / "capture", expected_physical_port="1-3", run=True,
                **DECLARATIONS), {"status": "sealed"})
        kwargs = lifecycle.call_args.kwargs
        self.assertEqual(kwargs["expected_tx"], 86)
        self.assertEqual(kwargs["report_key"], "disconnected_load_led_state_round_trip")
        self.assertIs(kwargs["observe"], led._observe)
        self.assertIs(kwargs["on_failure"], consent.notify_powered_trial_fault)

    def test_baseline_gate_happy_path_and_separate_outcomes(self):
        baseline_fault = _Transport()

        def reject_baseline(_, __, step, **kwargs):
            if step == "baseline":
                raise OSError("baseline mismatch")

        with patch.object(led, "_observe_one", side_effect=reject_baseline), \
                self.assertRaisesRegex(OSError, "baseline mismatch"):
            led._observe(baseline_fault, {}, clock=lambda: 0)
        self.assertEqual(baseline_fault.attempts, ["baseline"])
        self.assertFalse(baseline_fault.restore_attempted)

        transport = _Transport()
        report = {}
        with patch.object(led, "_observe_one"):
            led._observe(transport, report, clock=lambda: 0)
        self.assertEqual(
            transport.attempts, ["baseline", "test", "verify", "restore", "reverify"])
        self.assertEqual(report["status"], led.SUCCESS)
        self.assertTrue(report["baseline_getter_verified"])
        self.assertTrue(report["test_vector_getter_verified"])
        self.assertTrue(report["restore_response_protocol_verified"])
        self.assertTrue(report["baseline_restore_getter_verified"])
        self.assertEqual(
            report["protocol_store_verification"],
            "test_vector_and_exact_baseline_restore_verified")
        self.assertEqual(report["operator_led_observation"], "not_recorded_by_software")
        self.assertEqual(report["physical_led_effect"], "not_established")
        self.assertEqual((report["accepted_tx_bytes"], report["uncertain_tx_bytes"]), (86, 0))

    def test_restore_once_after_every_post_setter_fault_boundary(self):
        response_errors = (
            OSError("timeout"), OSError("non80"), OSError("crc"),
            OSError("correlation"), OSError("extra frame"),
        )
        cases = (
            (("test", "error"), None),
            (("test", "partial"), None),
            (("verify", "error"), None),
            (("verify", "partial"), None),
            *((None, ("test", error)) for error in response_errors),
            *((None, ("verify", error)) for error in response_errors),
            (None, ("verify", KeyboardInterrupt())),
            (None, ("restore", OSError("restore response"))),
        )
        for submit_fault, response_fault in cases:
            transport = _Transport(submit_fault)

            def observe(_, __, step, **kwargs):
                if response_fault and step == response_fault[0]:
                    raise response_fault[1]

            with self.subTest(submit_fault=submit_fault, response_fault=response_fault), \
                    patch.object(led, "_observe_one", side_effect=observe), \
                    self.assertRaises(BaseException):
                led._observe(transport, {}, clock=lambda: 0)
            self.assertEqual(transport.attempts.count("restore"), 1)
            self.assertNotIn("reverify", transport.attempts)
            self.assertLessEqual(transport.writes, 4)
            transport.close.assert_called_once()

        transport = led._LedStateTransport.__new__(led._LedStateTransport)
        transport.test_setter_may_have_been_submitted = True
        transport.restore_attempted = False
        transport.completed_steps = ["baseline", "test"]
        transport.writes = 2
        transport.fd = 99
        transport.ingress = Mock()
        transport.last_write = None
        transport._check = Mock()
        transport.event = Mock(side_effect=OSError("journal fault"))
        with patch.object(led.os, "write", return_value=len(led.RESTORE_SETTER)) as write, \
                self.assertRaisesRegex(OSError, "journal fault"):
            transport._submit_restore_once(deadline=1)
        write.assert_called_once_with(99, led.RESTORE_SETTER)
        self.assertTrue(transport.restore_attempted)

    def test_exact_live_non80_setter_and_restore_path(self):
        self.assertEqual(
            frame(b"", command=0x18, status=0x82, sequence=3090).hex(),
            "53120c18820000d6fe45")
        self.assertEqual(
            frame(b"", command=0x18, status=0x82, sequence=3092).hex(),
            "53140c18820000d69845")
        transport = _Transport()
        transport.serial_bytes = 48
        report = {}

        def respond(_, response, **kwargs):
            baseline = response.sequence == 3089
            response.feed(Mock(
                data=frame(
                    led.BASELINE if baseline else b"",
                    command=response.command,
                    status=0x80 if baseline else 0x82,
                    sequence=response.sequence),
                started_at=.1, ended_at=.2), .2)
            response.finish(.2)

        with patch.object(led.zero, "_observe_response", side_effect=respond), \
                self.assertRaisesRegex(OSError, "uninterpreted_non80_status"):
            led._observe(transport, report, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline", "test", "restore"])
        self.assertEqual((report["accepted_tx_bytes"], report["uncertain_tx_bytes"]), (66, 0))
        self.assertEqual(report["serial_rx_bytes"], 48)
        self.assertTrue(report["restore_attempted"])
        self.assertFalse(report["test_vector_getter_verified"])
        self.assertFalse(report["baseline_restore_getter_verified"])


if __name__ == "__main__":
    unittest.main()
