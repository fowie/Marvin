"""Offline fixed disconnected-load velocity-train tests."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests import test_marvin_session as session_tests
from tools import marvin_legacy_velocity_train as train
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_VELOCITY_TRAIN_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-")
         for name in consent.DISCONNECTED_VELOCITY_TRAIN_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0
    plan = Mock(max_lateness=.02)

    def __init__(self, submit_fault=None):
        self.submit_fault = submit_fault
        self.attempts = []
        self.writes = 0
        self.may_have_applied = False
        self.cleanup_attempted = False
        self.close = Mock()
        self.ingress = Mock(rx=[])

    def revalidate(self, *, deadline):
        return self.token

    def submit(self, index, *, deadline):
        self.attempts.append(index)
        if index:
            self.may_have_applied = True
        self.writes += 1
        if self.submit_fault == (index, "error"):
            raise OSError("uncertain")
        if self.submit_fault == (index, "partial"):
            return 1
        return len(train.TRANSCRIPT[index])

    def cleanup_once(self, *, deadline):
        if self.cleanup_attempted:
            raise OSError("cleanup retry")
        self.cleanup_attempted = True
        self.attempts.append("cleanup")
        self.writes += 1
        if self.submit_fault == ("cleanup", "error"):
            raise OSError("cleanup uncertain")
        return len(train.CLEANUP_ZERO)


class VelocityTrainTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            dir=Path.cwd(), prefix=".velocity-train-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    @staticmethod
    def response(transport, report, index, **_):
        status = 0x80 if index in (0, len(train.TRANSCRIPT) - 1) else 0x82
        report["responses"].append({
            "sequence": train.FIRST_SEQUENCE + index,
            "raw_response_field": status,
        })
        return Mock(response_field=status)

    def test_exact_transcript_scope_forwarding_cadence_cleanup_and_faults(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(train.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual((plan["maximum_writes"], plan["maximum_application_bytes"],
                          plan["maximum_expected_response_bytes"]), (22, 308, 220))
        self.assertEqual(plan["fixed_cadence_seconds"], .05)
        self.assertEqual(plan["fixed_train_count"], 20)
        self.assertEqual(plan["immutable_application_transcript_hex"][0],
                         "53d50c11000400000000008eb845")
        self.assertEqual(plan["immutable_application_transcript_hex"][1:4], [
            "53d60c11000400e8030000bb1745",
            "53d70c11000400e8030000ead245",
            "53d80c11000400e8030000dae245",
        ])
        self.assertEqual(plan["immutable_application_transcript_hex"][-2:], [
            "53e90c11000400e80300008bd845",
            "53ea0c1100040000000000be7745",
        ])
        self.assertEqual(
            [train.decode_packet(raw).sequence for raw in train.TRANSCRIPT],
            list(range(3285, 3307)))
        self.assertTrue(all(
            train.decode_packet(raw).payload == train.LEFT_PLUS_1000
            for raw in train.TRAIN))
        for knob in ("value", "duration", "sequence", "retry", "count", "cadence"):
            with self.subTest(knob=knob), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                train.main(FLAGS + ["--" + knob, "1"])
        for name in consent.DISCONNECTED_VELOCITY_TRAIN_FLAGS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                train.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS | {name: False}))

        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.DISCONNECTED_VELOCITY_TRAIN_SCOPE)
        self.assertEqual(result["probe_name"],
                         "DisconnectedLoadLeftPlus1000VelocityTrain")
        self.assertEqual(result["requested_application_bytes"], 308)
        self.assertTrue(result["fixed_left_plus_1000_velocity_train_authorized"])
        self.assertEqual(result["immutable_application_transcript_hex"],
                         [raw.hex() for raw in train.TRANSCRIPT])
        for flag in FLAGS:
            self.assertIn(flag, command)
        train._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        train._validate_capture(capture.call_args.kwargs)

        transport = _Transport()
        targets = []

        def wait(_, target, __, **___):
            targets.append(target)
            return 0

        report = {}
        with patch.object(train, "_response", side_effect=self.response), \
                patch.object(train, "_wait_until", side_effect=wait):
            train._observe(transport, report, clock=lambda: 100)
        self.assertEqual(transport.attempts, [*range(21), "cleanup"])
        self.assertEqual(targets, [100 + index * .05 for index in range(21)])
        self.assertEqual(
            [row["raw_response_field"] for row in report["responses"]],
            [0x80, *([0x82] * 20), 0x80])
        self.assertEqual(report["status"], train.SUCCESS)
        self.assertEqual((report["accepted_tx_bytes"], report["uncertain_tx_bytes"]),
                         (308, 0))
        self.assertTrue(report["cleanup_zero_fully_accepted"])
        transport.close.assert_called_once()

        fault_cases = (
            ((1, "error"), None),
            ((1, "partial"), None),
            ((10, "error"), None),
            (None, (1, OSError("timeout"))),
            (None, (8, OSError("crc"))),
            (None, (9, OSError("framing"))),
            (None, (10, OSError("correlation"))),
            (None, (11, OSError("extra frame"))),
            (None, (12, OSError("evidence journal"))),
            (None, (20, KeyboardInterrupt())),
        )
        for submit_fault, response_fault in fault_cases:
            transport = _Transport(submit_fault)

            def response(inner_transport, inner_report, index, **kwargs):
                if response_fault and index == response_fault[0]:
                    raise response_fault[1]
                return self.response(inner_transport, inner_report, index, **kwargs)

            with self.subTest(submit_fault=submit_fault, response_fault=response_fault), \
                    patch.object(train, "_response", side_effect=response), \
                    patch.object(train, "_wait_until", return_value=0), \
                    redirect_stderr(io.StringIO()), self.assertRaises(BaseException):
                train._observe(transport, {}, clock=lambda: 100)
            self.assertEqual(transport.attempts.count("cleanup"), 1)
            self.assertTrue(transport.cleanup_attempted)
            transport.close.assert_called_once()

        transport = train._VelocityTrainTransport.__new__(train._VelocityTrainTransport)
        transport.may_have_applied = True
        transport.cleanup_attempted = False
        transport.writes = 21
        transport.fd = 99
        transport.ingress = Mock(expected_tx=[])
        transport._check = Mock()
        calls = []
        transport.event = lambda *args, **kwargs: calls.append("journal")
        with patch.object(train.os, "write",
                          side_effect=lambda fd, raw: calls.append("syscall") or len(raw)):
            self.assertEqual(
                transport.cleanup_once(deadline=1), len(train.CLEANUP_ZERO))
        self.assertEqual(calls, ["syscall", "journal"])
        with self.assertRaisesRegex(OSError, "already"):
            transport.cleanup_once(deadline=1)

        transport = train._VelocityTrainTransport.__new__(train._VelocityTrainTransport)
        transport.may_have_applied = True
        transport.cleanup_attempted = False
        transport.writes = 2
        transport.fd = 99
        transport.ingress = Mock(expected_tx=[])
        transport._check = Mock()
        calls = []
        transport.event = Mock(side_effect=OSError("cleanup journal"))
        with patch.object(train.os, "write",
                          side_effect=lambda fd, raw: calls.append("syscall") or len(raw)), \
                self.assertRaisesRegex(OSError, "cleanup journal"):
            transport.cleanup_once(deadline=1)
        self.assertEqual(calls, ["syscall"])
        self.assertTrue(transport.cleanup_attempted)


if __name__ == "__main__":
    unittest.main()
