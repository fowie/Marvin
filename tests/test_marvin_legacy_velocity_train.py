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
from tests.test_marvin_legacy_client import frame
from tools import marvin_legacy_velocity_train as train
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_VELOCITY_TRAIN_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-")
         for name in consent.DISCONNECTED_VELOCITY_TRAIN_FLAGS]
RIGHT_DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS, True)
RIGHT_FLAGS = ["--" + name.replace("_", "-")
               for name in consent.DISCONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS]
CONNECTED_DECLARATIONS = dict.fromkeys(
    consent.CONNECTED_LEFT_VELOCITY_TRAIN_FLAGS, True)
CONNECTED_FLAGS = ["--" + name.replace("_", "-")
                   for name in consent.CONNECTED_LEFT_VELOCITY_TRAIN_FLAGS]
CONNECTED_RIGHT_DECLARATIONS = dict.fromkeys(
    consent.CONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS, True)
CONNECTED_RIGHT_FLAGS = ["--" + name.replace("_", "-")
                         for name in consent.CONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0
    plan = Mock(max_lateness=.02)
    transcript = train.TRANSCRIPT
    success = train.SUCCESS
    train_count = train.TRAIN_COUNT
    cleanup_immediately_after_train = False
    motor_connected = False

    def __init__(self, submit_fault=None):
        self.submit_fault = submit_fault
        self.attempts = []
        self.writes = 0
        self.may_have_applied = False
        self.cleanup_attempted = False
        self.cleanup_started = 100.24
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
        self.last_write_started = self.cleanup_started
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
        status = 0x80 if index in (0, len(transport.transcript) - 1) else 0x82
        report["responses"].append({
            "sequence": train.decode_packet(transport.transcript[index]).sequence,
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
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(train.main(RIGHT_FLAGS), 0)
        right_plan = json.loads(stdout.getvalue())
        self.assertEqual(
            (right_plan["name"], right_plan["commanded_field"],
             right_plan["operator_selected_physical_plug_label"]),
            (consent.DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE,
             "PCTestApp rightVel second signed int16 word", "Motor R"))
        self.assertEqual(right_plan["immutable_application_transcript_hex"][:4], [
            "53eb0c1100040000000000efb245",
            "53ec0c110004000000e803506945",
            "53ed0c110004000000e80301ac45",
            "53ee0c110004000000e803f1a345",
        ])
        self.assertEqual(right_plan["immutable_application_transcript_hex"][-2:], [
            "53ff0c110004000000e803a1f345",
            "53000d1100040000000000979145",
        ])
        self.assertEqual(
            right_plan["transcript_sha256"],
            "07d2dec07a7db89cc859ac16f4aeb16390361076385aafa41af1fb563c43ec5d")
        self.assertTrue(all(
            train.decode_packet(raw).payload == train.RIGHT_PLUS_1000
            for raw in train.RIGHT_TRAIN))
        self.assertEqual(
            train.transcript_for_scope(consent.DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE),
            train.RIGHT_TRANSCRIPT)
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(train.main(CONNECTED_FLAGS), 0)
        connected_plan = json.loads(stdout.getvalue())
        self.assertEqual(connected_plan["immutable_application_transcript_hex"], [
            "532d0d1100040000000000070145",
            "532e0d11000400e803000032ae45",
            "532f0d11000400e8030000636b45",
            "53300d11000400e803000052ce45",
            "53310d11000400e8030000030b45",
            "53320d11000400e8030000f30445",
            "53330d1100040000000000676145",
        ])
        self.assertEqual(
            connected_plan["transcript_sha256"],
            "62f25f66bda910a2f9764db7809b844e0602ccad10bd0b8c7ff2e464367baa1d")
        self.assertEqual(
            (connected_plan["fixed_train_count"],
             connected_plan["nominal_train_span_seconds"],
             connected_plan["maximum_planned_first_nonzero_to_cleanup_prewrite_seconds"],
             connected_plan["maximum_application_bytes"],
             connected_plan["maximum_expected_response_bytes"]),
            (5, .2, .25, 98, 70))
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(train.main(CONNECTED_RIGHT_FLAGS), 0)
        connected_right_plan = json.loads(stdout.getvalue())
        self.assertEqual(connected_right_plan["immutable_application_transcript_hex"], [
            "53340d1100040000000000d6bb45",
            "53350d110004000000e803897f45",
            "53360d110004000000e803797045",
            "53370d110004000000e80328b545",
            "53380d110004000000e803188545",
            "53390d110004000000e803494045",
            "533a0d1100040000000000b74e45",
        ])
        self.assertEqual(
            connected_right_plan["transcript_sha256"],
            "563fb24134a3935d7037a6bfcfe40ba2506e2885ade85b3c578dab6b516f2639")
        self.assertEqual(
            connected_right_plan["scope_observation"],
            "observe_connected_physical_right_wheel_motion_direction_and_stop")
        for knob in ("value", "duration", "sequence", "retry", "count", "cadence"):
            with self.subTest(knob=knob), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                train.main(FLAGS + ["--" + knob, "1"])
        for name in consent.DISCONNECTED_VELOCITY_TRAIN_FLAGS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                train.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS | {name: False}))
        for name in consent.DISCONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS:
            with self.subTest(right_name=name), self.assertRaises(ValueError):
                train.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(RIGHT_DECLARATIONS | {name: False}))
        for name in consent.CONNECTED_LEFT_VELOCITY_TRAIN_FLAGS:
            with self.subTest(connected_name=name), self.assertRaises(ValueError):
                train.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(CONNECTED_DECLARATIONS | {name: False}))
        for name in consent.CONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS:
            with self.subTest(connected_right_name=name), self.assertRaises(ValueError):
                train.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(CONNECTED_RIGHT_DECLARATIONS | {name: False}))

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

        connected_right_harness = session_tests.SessionTests()
        connected_right_harness.setUp()
        self.addCleanup(connected_right_harness.doCleanups)
        connected_right_runner = Mock(side_effect=connected_right_harness.capture)
        connected_right_result = connected_right_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=connected_right_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **CONNECTED_RIGHT_DECLARATIONS)
        connected_right_command = connected_right_harness.popen.call_args.args[0]
        self.assertEqual(
            connected_right_result["scope"],
            consent.CONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE)
        self.assertEqual(
            connected_right_result["probe_name"], "ConnectedRightPlus1000VelocityTrain")
        self.assertTrue(
            connected_right_result[
                "fixed_connected_right_plus_1000_velocity_train_authorized"])
        for flag in CONNECTED_RIGHT_FLAGS:
            self.assertIn(flag, connected_right_command)
        train._validate_capture(connected_right_runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(connected_right_command[2:]), 0)
        train._validate_capture(capture.call_args.kwargs)

        right_harness = session_tests.SessionTests()
        right_harness.setUp()
        self.addCleanup(right_harness.doCleanups)
        right_runner = Mock(side_effect=right_harness.capture)
        right_result = right_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=right_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **RIGHT_DECLARATIONS)
        right_command = right_harness.popen.call_args.args[0]
        self.assertEqual(
            right_result["scope"], consent.DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE)
        self.assertEqual(
            right_result["probe_name"], "DisconnectedLoadRightPlus1000VelocityTrain")
        self.assertTrue(right_result["fixed_right_plus_1000_velocity_train_authorized"])
        self.assertEqual(
            right_result["immutable_application_transcript_hex"],
            [raw.hex() for raw in train.RIGHT_TRANSCRIPT])
        for flag in RIGHT_FLAGS:
            self.assertIn(flag, right_command)
        train._validate_capture(right_runner.call_args.kwargs)

        connected_harness = session_tests.SessionTests()
        connected_harness.setUp()
        self.addCleanup(connected_harness.doCleanups)
        connected_runner = Mock(side_effect=connected_harness.capture)
        connected_result = connected_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=connected_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **CONNECTED_DECLARATIONS)
        connected_command = connected_harness.popen.call_args.args[0]
        self.assertEqual(
            connected_result["scope"], consent.CONNECTED_LEFT_VELOCITY_TRAIN_SCOPE)
        self.assertEqual(
            connected_result["probe_name"], "ConnectedLeftPlus1000VelocityTrain")
        self.assertTrue(
            connected_result["fixed_connected_left_plus_1000_velocity_train_authorized"])
        for flag in CONNECTED_FLAGS:
            self.assertIn(flag, connected_command)
        train._validate_capture(connected_runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(connected_command[2:]), 0)
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

        right_transport = _Transport()
        right_transport.transcript = train.RIGHT_TRANSCRIPT
        right_transport.success = train.RIGHT_SUCCESS
        right_report = {}
        with patch.object(train, "_response", side_effect=self.response), \
                patch.object(train, "_wait_until", return_value=0):
            train._observe(right_transport, right_report, clock=lambda: 100)
        self.assertEqual(right_report["status"], train.RIGHT_SUCCESS)
        self.assertEqual(
            [row["sequence"] for row in right_report["responses"]],
            list(range(3307, 3329)))
        self.assertEqual(right_transport.attempts, [*range(21), "cleanup"])

        connected_transport = _Transport()
        connected_transport.transcript = train.CONNECTED_LEFT_TRANSCRIPT
        connected_transport.success = train.CONNECTED_LEFT_SUCCESS
        connected_transport.train_count = train.CONNECTED_TRAIN_COUNT
        connected_transport.cleanup_immediately_after_train = True
        connected_transport.motor_connected = True
        connected_report = {}
        targets = []
        with patch.object(train, "_response", side_effect=self.response), \
                patch.object(
                    train, "_wait_until",
                    side_effect=lambda _, target, __, **___: targets.append(target) or 0), \
                redirect_stderr(io.StringIO()) as stderr:
            train._observe(connected_transport, connected_report, clock=lambda: 100)
        self.assertIn("OBSERVE_LEFT_MOTOR_NOW", stderr.getvalue())
        self.assertEqual(connected_transport.attempts, [*range(6), "cleanup"])
        self.assertEqual(targets, [100 + index * .05 for index in range(5)])
        self.assertAlmostEqual(
            connected_report["cleanup_prewrite_elapsed_seconds"], .24)
        self.assertEqual(connected_report["status"], train.CONNECTED_LEFT_SUCCESS)

        connected_right_transport = _Transport()
        connected_right_transport.transcript = train.CONNECTED_RIGHT_TRANSCRIPT
        connected_right_transport.success = train.CONNECTED_RIGHT_SUCCESS
        connected_right_transport.train_count = train.CONNECTED_TRAIN_COUNT
        connected_right_transport.cleanup_immediately_after_train = True
        connected_right_transport.motor_connected = True
        connected_right_transport.physical_motor_label = "RIGHT"
        with patch.object(train, "_response", side_effect=self.response), \
                patch.object(train, "_wait_until", return_value=0), \
                redirect_stderr(io.StringIO()) as stderr:
            train._observe(connected_right_transport, {}, clock=lambda: 100)
        self.assertIn("OBSERVE_RIGHT_MOTOR_NOW", stderr.getvalue())
        self.assertNotIn("OBSERVE_LEFT_MOTOR_NOW", stderr.getvalue())

        late_transport = _Transport()
        late_transport.transcript = train.CONNECTED_LEFT_TRANSCRIPT
        late_transport.success = train.CONNECTED_LEFT_SUCCESS
        late_transport.train_count = train.CONNECTED_TRAIN_COUNT
        late_transport.cleanup_immediately_after_train = True
        late_transport.motor_connected = True
        late_transport.cleanup_started = 100.251
        with patch.object(train, "_response", side_effect=self.response), \
                patch.object(train, "_wait_until", return_value=0), \
                redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(OSError, "exceeded 250 ms"):
            train._observe(late_transport, {}, clock=lambda: 100)
        self.assertEqual(late_transport.attempts.count("cleanup"), 1)

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

        live_start = 281342.3600659589
        transport = Mock(
            last_write_sequence=3286, last_write_started=281342.359916316,
            transcript=train.TRANSCRIPT)
        transport.event = Mock()
        report = {"responses": []}

        def observed(_, evidence, **kwargs):
            evidence.feed(Mock(
                data=frame(b"", command=0x11, status=0x82, sequence=3286),
                started_at=live_start, ended_at=live_start + .000002), live_start + .005)
            evidence.finish(live_start + .005)

        with patch.object(train.zero, "_observe_response", side_effect=observed):
            packet = train._response(
                transport, report, 1, deadline=live_start + 1,
                clock=lambda: live_start + .005)
        self.assertEqual(packet.response_field, 0x82)
        self.assertEqual(
            report["responses"][0]["events"][0]["labels"],
            ["unverified_shape_and_semantics", "correlated_command_sequence_only"])

        transport.last_write_started = live_start
        with patch.object(train.zero, "_observe_response", side_effect=observed), \
                self.assertRaisesRegex(OSError, "prewrite_or_ambiguous"):
            train._response(
                transport, {"responses": []}, 1, deadline=live_start + 1,
                clock=lambda: live_start + .005)

        transport.last_write_sequence = 3285
        transport.last_write_started = live_start - .001

        def initial_raw82(_, evidence, **kwargs):
            evidence.feed(Mock(
                data=frame(b"", command=0x11, status=0x82, sequence=3285),
                started_at=live_start, ended_at=live_start + .000002), live_start + .005)

        with patch.object(train.zero, "_observe_response", side_effect=initial_raw82), \
                self.assertRaisesRegex(OSError, "uninterpreted_non80_status"):
            train._response(
                transport, {"responses": []}, 0, deadline=live_start + 1,
                clock=lambda: live_start + .005)

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
