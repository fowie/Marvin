"""Offline fixed raw-PWM word-0/value-1 pilot tests."""

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
from tools import marvin_legacy_raw_pwm_pilot as pilot
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.RAW_PWM_PILOT_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.RAW_PWM_PILOT_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0

    def __init__(self, submit_fault=None):
        self.submit_fault = submit_fault
        self.attempts = []
        self.writes = 0
        self.completed = []
        self.may_have_applied = False
        self.cleanup_attempted = False
        self.cleanup_raw80 = False
        self.close = Mock()
        self.wait = Mock()

    def revalidate(self, *, deadline):
        return self.token

    def submit(self, step, *, deadline):
        self.attempts.append(step)
        if step == "set":
            self.may_have_applied = True
        if step == "cleanup":
            if self.cleanup_attempted:
                raise OSError("cleanup retry")
            self.cleanup_attempted = True
        self.writes += 1
        if self.submit_fault == (step, "error"):
            raise OSError(step + " uncertain")
        if self.submit_fault == (step, "partial"):
            return 1
        return len(pilot.STEPS[step])


class RawPwmPilotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            dir=Path.cwd(), prefix=".raw-pwm-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    @staticmethod
    def response(statuses=None, fault=None):
        statuses = statuses or {}

        def respond(transport, report, step, **_):
            if fault and step == fault[0]:
                raise fault[1]
            status = statuses.get(step, 0x80)
            report["responses"].append({
                "step": step,
                "sequence": pilot.decode_packet(pilot.STEPS[step]).sequence,
                "raw_response_field": status,
            })
            return Mock(response_field=status)

        return respond

    def test_fixed_pilot_integration_cleanup_gates_and_lock(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "53010d0a0000004ccd45",
            "53020d0b0008000100000000000000a64f45",
            "53030d0b0008000000000000000000674245",
            "53040d0a0000004c9845",
        ])
        self.assertEqual(
            plan["transcript_sha256"],
            "6880718e5a54cf8a8225d3d2afc3cd4cc975f6c0bb6552a8de4161bf4a6b0df8")
        self.assertEqual(
            (plan["maximum_writes"], plan["maximum_application_bytes"],
             plan["minimum_application_bytes_after_setter"],
             plan["maximum_expected_response_bytes"]),
            (4, 56, 46, 56))
        self.assertEqual(pilot.decode_packet(pilot.STEPS["set"]).payload,
                         b"\x01\x00" + bytes(6))
        self.assertFalse(plan["automatic_retries"])
        self.assertFalse(plan["automatic_reconnect"])
        self.assertIsNone(plan["fixed_cadence"])
        for knob in ("index", "value", "payload", "sequence", "retry", "count", "cadence"):
            with self.subTest(knob=knob), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                pilot.main(FLAGS + ["--" + knob, "1"])
        for name in consent.RAW_PWM_PILOT_FLAGS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
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
        self.assertEqual(result["scope"], consent.RAW_PWM_PILOT_SCOPE)
        self.assertEqual(result["probe_name"], "DisconnectedLoadRawPwmWord0OnePilot")
        self.assertTrue(result["fixed_raw_pwm_word0_one_pilot_authorized"])
        self.assertEqual(result["requested_application_bytes"], 56)
        self.assertEqual(result["immutable_application_transcript_hex"],
                         [raw.hex() for raw in pilot.TRANSCRIPT])
        for flag in FLAGS:
            self.assertIn(flag, command)
        pilot._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        pilot._validate_capture(capture.call_args.kwargs)

        transport = _Transport()
        report = {}
        with patch.object(pilot, "_response",
                          side_effect=self.response({"cleanup": 0x82})), \
                redirect_stderr(io.StringIO()):
            pilot._observe(transport, report, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline", "set", "cleanup"])
        self.assertEqual(report["restoration"],
                         "zero_cleanup_attempted_but_application_unverified")
        self.assertEqual(report["subsequent_live_phase_gate"],
                         "physical_output_baseline_confirmation_and_power_cycle_acknowledgment_required")
        self.assertEqual(report["status"], pilot.SUCCESS)
        self.assertEqual((report["accepted_tx_bytes"], report["uncertain_tx_bytes"]),
                         (46, 0))
        transport.wait.assert_called_once_with(3)
        transport.close.assert_called_once()

        transport = _Transport()
        report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(transport, report, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline", "set", "cleanup", "verify"])
        self.assertTrue(report["getter_reverified"])
        self.assertEqual(report["restoration"], "getter_zero_baseline_reverified")
        self.assertEqual(report["accepted_tx_bytes"], 56)

        faults = (
            (("set", "error"), None),
            (("set", "partial"), None),
            (None, ("set", OSError("timeout"))),
            (None, ("set", OSError("crc"))),
            (None, ("set", OSError("correlation"))),
            (None, ("set", OSError("extra frame"))),
            (None, ("set", KeyboardInterrupt())),
        )
        for submit_fault, response_fault in faults:
            transport = _Transport(submit_fault)
            with self.subTest(submit_fault=submit_fault,
                              response_fault=response_fault), \
                    patch.object(
                        pilot, "_response",
                        side_effect=self.response(fault=response_fault)), \
                    redirect_stderr(io.StringIO()), self.assertRaises(BaseException):
                pilot._observe(transport, {}, clock=lambda: 0)
            self.assertEqual(transport.attempts.count("cleanup"), 1)
            self.assertTrue(transport.cleanup_attempted)
            transport.close.assert_called_once()

        transport = _Transport()
        transport.wait.side_effect = KeyboardInterrupt()
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            pilot._observe(transport, {}, clock=lambda: 0)
        self.assertEqual(transport.attempts.count("cleanup"), 1)
        self.assertTrue(transport.cleanup_attempted)

        live = pilot._RawPwmTransport.__new__(pilot._RawPwmTransport)
        live.may_have_applied = True
        live.cleanup_attempted = False
        live.cleanup_raw80 = False
        live.completed = ["baseline", "set"]
        live.writes = 2
        live.fd = 99
        live.ingress = Mock()
        live._check = Mock()
        live.event = Mock(side_effect=OSError("journal fault"))
        with patch.object(pilot.os, "write",
                          return_value=len(pilot.STEPS["cleanup"])) as write, \
                self.assertRaises(OSError):
            live._cleanup_once(deadline=1)
        write.assert_called_once_with(99, pilot.STEPS["cleanup"])
        self.assertTrue(live.cleanup_attempted)

        evidence = self.root / "evidence"
        evidence.mkdir()
        (evidence / "metadata.json").write_text("{}\n", encoding="utf-8")
        session.evidence_manifest(evidence)
        digest = pilot._verify_manifest(evidence)
        pilot._write_state(self.root, {
            "status": "raw_pwm_restoration_unverified",
            "target": pilot.TARGET,
            "set_evidence": str(evidence.resolve()),
            "set_manifest_sha256": digest,
        })
        with self.assertRaises(ValueError):
            pilot.acknowledge_restoration(
                evidence, physical_output_baseline_confirmed=True)
        result = pilot.acknowledge_restoration(
            evidence, physical_output_baseline_confirmed=True,
            power_cycle_confirmed=True)
        self.assertEqual(result["status"], "power_cycle_reset_confirmed")

        boundary = Mock(
            last_write_sequence=3330, last_write_started=10,
            event=Mock())
        boundary_report = {"responses": []}

        def observed(_, evidence, **__):
            evidence.feed(Mock(
                data=frame(b"", command=0x0B, status=0x82, sequence=3330),
                started_at=10.001, ended_at=10.002), 10.003)
            evidence.finish(10.003)

        with patch.object(pilot.zero, "_observe_response", side_effect=observed):
            packet = pilot._response(
                boundary, boundary_report, "set", deadline=11, clock=lambda: 10.004)
        self.assertEqual(packet.response_field, 0x82)

        def invalid_baseline(_, evidence, **__):
            evidence.feed(Mock(
                data=frame(bytes.fromhex("0100000000000000"), command=0x0A,
                           status=0x80, sequence=3329),
                started_at=10.001, ended_at=10.002), 10.003)
            evidence.finish(10.003)

        boundary.last_write_sequence = 3329
        boundary_report = {"responses": []}
        with patch.object(pilot.zero, "_observe_response",
                          side_effect=invalid_baseline), self.assertRaises(OSError):
            pilot._response(
                boundary, boundary_report, "baseline",
                deadline=11, clock=lambda: 10.004)


if __name__ == "__main__":
    unittest.main()
