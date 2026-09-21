"""Offline fixed OFF-start wheel LED blink pilot tests."""

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
from tools import marvin_legacy_wheel_led_blink as blink
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.WHEEL_LED_BLINK_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.WHEEL_LED_BLINK_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0

    def __init__(self, submit_fault=None):
        self.submit_fault = submit_fault
        self.attempts = []
        self.writes = 0
        self.completed = []
        self.may_have_applied = {"state": False, "blink": False}
        self.restore_attempted = {"state": False, "blink": False}
        self.restore_raw80 = {"state": False, "blink": False}
        self.close = Mock()
        self.wait = Mock()
        self.event = Mock()

    def revalidate(self, *, deadline):
        return self.token

    def submit(self, step, *, deadline):
        self.attempts.append(step)
        component = step.split("_", 1)[0]
        if step.endswith("_set"):
            self.may_have_applied[component] = True
        if step.endswith("_restore"):
            if self.restore_attempted[component]:
                raise OSError("restore retry")
            self.restore_attempted[component] = True
        self.writes += 1
        if self.submit_fault == (step, "error"):
            raise OSError(step + " uncertain")
        if self.submit_fault == (step, "partial"):
            return 1
        return len(blink.STEPS[step])


class WheelLedBlinkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            dir=Path.cwd(), prefix=".wheel-blink-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_exact_frames_bounds_scope_and_no_knobs(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(blink.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "53cd0c1700000066ad45",
            "53ce0c19000000647645",
            "53cf0c18001200000000000000000000000000ff0000ff00008b4245",
            "53d00c1a0012000000000000000000000000002a00002a000096f345",
            "53d10c1a0012000000000000000000000000000000002a0000ccac45",
            "53d20c18001200000000000000000000000000000000ff00009a7245",
            "53d30c1900000067fb45",
            "53d40c1700000064a445",
        ])
        self.assertEqual((plan["maximum_writes"], plan["maximum_application_bytes"]),
                         (8, 152))
        self.assertEqual(plan["cleanup_order"],
                         ["exact_blink_baseline_once", "exact_led_state_baseline_once"])
        self.assertFalse(plan["automatic_retries"])
        self.assertFalse(plan["automatic_reconnect"])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            for knob in ("index", "payload", "sequence", "retry", "count", "value"):
                with self.subTest(knob=knob), self.assertRaises(SystemExit):
                    blink.main(FLAGS + ["--" + knob, "1"])
        for name in consent.WHEEL_LED_BLINK_FLAGS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                blink.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS | {name: False}))

    def test_coordinator_recorder_forwarding_and_lifecycle(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=15, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.WHEEL_LED_BLINK_SCOPE)
        self.assertEqual(result["probe_name"], "DisconnectedLoadWheelLedBlinkPilot")
        self.assertTrue(result["fixed_wheel_led_blink_pilot_authorized"])
        self.assertEqual(result["requested_application_bytes"], 152)
        for flag in FLAGS:
            self.assertIn(flag, command)
        blink._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        blink._validate_capture(capture.call_args.kwargs)

        with patch.object(blink.zero, "_run_diagnostic",
                          return_value={"observation": {
                              "restoration": "getter_baselines_reverified",
                          }}), \
                patch.object(blink, "_verify_manifest", return_value="digest") as verify:
            result = blink.run_diagnostic(
                self.root / "capture", expected_physical_port="1-3", run=True,
                **DECLARATIONS)
        self.assertEqual(result["observation"]["restoration"],
                         "getter_baselines_reverified")
        self.assertEqual(blink._load_state(self.root)["status"], "restored")
        verify.assert_called_once()

    def respond(self, statuses):
        payloads = {
            "state_baseline": blink.STATE_BASELINE,
            "blink_baseline": blink.BLINK_BASELINE,
            "blink_verify": blink.BLINK_BASELINE,
            "state_verify": blink.STATE_BASELINE,
        }

        def response(_, evidence, **kwargs):
            step = next(
                name for name, raw in blink.STEPS.items()
                if decode(raw).sequence == evidence.sequence)
            evidence.feed(Mock(
                data=frame(
                    payloads.get(step, b""), command=evidence.command,
                    status=statuses.get(step, 0x80), sequence=evidence.sequence),
                started_at=.1, ended_at=.2), .2)
            evidence.finish(.2)

        return response

    def test_raw82_happy_path_restores_reverse_order_without_getters(self):
        transport = _Transport()
        report = {}
        statuses = {
            "state_set": 0x82, "blink_set": 0x82,
            "blink_restore": 0x82, "state_restore": 0x82,
        }
        with patch.object(blink.zero, "_observe_response",
                          side_effect=self.respond(statuses)), \
                redirect_stderr(io.StringIO()):
            blink._observe(transport, report, clock=lambda: 0)
        self.assertEqual(transport.attempts, [
            "state_baseline", "blink_baseline", "state_set", "blink_set",
            "blink_restore", "state_restore",
        ])
        self.assertEqual(transport.restore_attempted, {"state": True, "blink": True})
        self.assertEqual(report["getter_reverified"], {"state": False, "blink": False})
        self.assertEqual(report["restoration"],
                         "restore_attempts_completed_but_application_unverified")
        self.assertEqual(report["status"], blink.SUCCESS)
        self.assertEqual((report["accepted_tx_bytes"], report["uncertain_tx_bytes"]),
                         (132, 0))
        transport.wait.assert_called_once_with(blink.OBSERVATION_SECONDS)
        transport.close.assert_called_once()

    def test_baseline_mismatch_stops_before_every_setter(self):
        transport = _Transport()

        def mismatch(_, evidence, **kwargs):
            evidence.feed(Mock(
                data=frame(bytes(18), command=evidence.command, status=0x80,
                           sequence=evidence.sequence),
                started_at=.1, ended_at=.2), .2)
            evidence.finish(.2)

        with patch.object(blink.zero, "_observe_response", side_effect=mismatch), \
                self.assertRaises(OSError):
            blink._observe(transport, {}, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["state_baseline"])
        self.assertEqual(transport.restore_attempted, {"state": False, "blink": False})

    def test_raw80_restores_then_reverifies_both_baselines(self):
        transport = _Transport()
        report = {}
        with patch.object(blink.zero, "_observe_response",
                          side_effect=self.respond({})), \
                redirect_stderr(io.StringIO()):
            blink._observe(transport, report, clock=lambda: 0)
        self.assertEqual(transport.attempts[-4:], [
            "blink_restore", "state_restore", "blink_verify", "state_verify"])
        self.assertEqual(report["getter_reverified"], {"state": True, "blink": True})
        self.assertEqual(report["restoration"], "getter_baselines_reverified")
        self.assertEqual(report["accepted_tx_bytes"], 152)

    def test_every_fault_attempts_each_still_required_restore_once(self):
        errors = (OSError("timeout"), OSError("crc"), OSError("correlation"),
                  OSError("extra frame"), KeyboardInterrupt())
        cases = (
            (("state_set", "error"), None, ("state_restore",)),
            (("state_set", "partial"), None, ("state_restore",)),
            (("blink_set", "error"), None, ("blink_restore", "state_restore")),
            (("blink_set", "partial"), None, ("blink_restore", "state_restore")),
            *((None, ("state_set", error), ("state_restore",)) for error in errors),
            *((None, ("blink_set", error), ("blink_restore", "state_restore"))
              for error in errors),
        )
        for submit_fault, response_fault, restores in cases:
            transport = _Transport(submit_fault)

            def response(_, evidence, **kwargs):
                step = next(
                    name for name, raw in blink.STEPS.items()
                    if decode(raw).sequence == evidence.sequence)
                if response_fault and step == response_fault[0]:
                    raise response_fault[1]
                return self.respond({})(_, evidence, **kwargs)

            with self.subTest(submit_fault=submit_fault,
                              response_fault=response_fault), \
                    patch.object(blink.zero, "_observe_response", side_effect=response), \
                    redirect_stderr(io.StringIO()), self.assertRaises(BaseException):
                blink._observe(transport, {}, clock=lambda: 0)
            attempted = tuple(
                step for step in transport.attempts if step.endswith("_restore"))
            self.assertEqual(attempted, restores)
            self.assertEqual(len(attempted), len(set(attempted)))
            transport.close.assert_called_once()

    def test_restore_journal_fault_does_not_suppress_second_restore_and_ack_binds_hash(self):
        transport = blink._BlinkTransport.__new__(blink._BlinkTransport)
        transport.may_have_applied = {"state": True, "blink": True}
        transport.restore_attempted = {"state": False, "blink": False}
        transport.restore_raw80 = {"state": False, "blink": False}
        transport.completed = ["state_baseline", "blink_baseline", "state_set", "blink_set"]
        transport.writes = 4
        transport.fd = 99
        transport.ingress = Mock()
        transport.last_write = None
        transport._check = Mock()
        transport.event = Mock(side_effect=OSError("journal fault"))
        report = {"accepted_tx_bytes": 0, "uncertain_tx_bytes": 0,
                  "responses": [], "restore_raw_response_fields": {
                      "state": None, "blink": None}, "getter_reverified": {
                      "state": False, "blink": False}}
        with patch.object(blink.os, "write",
                          return_value=len(blink.STEPS["blink_restore"])) as write:
            errors = blink._cleanup(transport, report, clock=lambda: 0)
        self.assertEqual([step for step, _ in errors],
                         ["blink", "state"])
        self.assertEqual(write.call_count, 2)
        self.assertEqual(transport.restore_attempted, {"state": True, "blink": True})

        evidence = self.root / "evidence"
        evidence.mkdir()
        (evidence / "metadata.json").write_text("{}\n", encoding="utf-8")
        session.evidence_manifest(evidence)
        digest = blink._verify_manifest(evidence)
        blink._write_state(self.root, {
            "status": "blink_restoration_unverified",
            "target": blink.TARGET,
            "set_evidence": str(evidence.resolve()),
            "set_manifest_sha256": digest,
        })
        with self.assertRaises(ValueError):
            blink.acknowledge_restoration(
                evidence, physical_restoration_confirmed=True)
        result = blink.acknowledge_restoration(
            evidence, physical_restoration_confirmed=True,
            power_cycle_confirmed=True)
        self.assertEqual(result["status"], "power_cycle_reset_confirmed")


def decode(raw):
    from tools.marvin_legacy_protocol import decode_packet
    return decode_packet(raw)


if __name__ == "__main__":
    unittest.main()
