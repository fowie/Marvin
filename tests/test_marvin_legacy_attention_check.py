"""Offline fixed attention-LED acceptance tests."""

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
from tools import marvin_legacy_attention_check as attention
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.ATTENTION_LED_CHECK_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-")
         for name in consent.ATTENTION_LED_CHECK_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0

    def __init__(self, submit_fault=None, wait_fault=None):
        self.submit_fault = submit_fault
        self.wait_fault = wait_fault
        self.attempts = []
        self.writes = 0
        self.outstanding = None
        self.restore_attempted = set()
        self.close = Mock()
        self.wait = Mock(side_effect=wait_fault)
        self.event = Mock()

    def revalidate(self, *, deadline):
        return self.token

    def submit(self, step, *, deadline):
        self.attempts.append(step)
        if step.endswith("_set"):
            self.outstanding = step.removesuffix("_set")
        elif step.endswith("_restore"):
            target = step.removesuffix("_restore")
            if target in self.restore_attempted:
                raise OSError("restore retry")
            self.restore_attempted.add(target)
        self.writes += 1
        if self.submit_fault == step:
            raise OSError(step + " uncertain")
        return len(attention.STEPS[step])


class AttentionLedCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            dir=Path.cwd(), prefix=".attention-check-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_exact_offline_plan_has_no_knobs_or_hardware(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(attention.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(len(plan["targets"]), 14)
        self.assertEqual(plan["maximum_writes"], 30)
        self.assertEqual(plan["maximum_application_bytes"], 804)
        self.assertEqual(plan["required_baseline_hex"], attention.BASELINE.hex())
        self.assertFalse(plan["automatic_retries"])
        packets = [
            attention.decode_packet(bytes.fromhex(raw))
            for raw in plan["immutable_application_transcript_hex"]
        ]
        self.assertEqual((packets[0].command, packets[-1].command), (0x17, 0x17))
        self.assertTrue(all(packet.command == 0x18 for packet in packets[1:-1]))
        for name, index in attention.TARGETS:
            payload = packets[1 + list(attention.TARGETS).index((name, index)) * 2].payload
            self.assertEqual(payload, attention._exclusive(index))

    def test_identity_and_baseline_gate_before_setter(self):
        changed = _Transport()
        changed.token = "old"
        changed.revalidate = Mock(return_value="new")
        with patch.object(attention, "_response"), self.assertRaisesRegex(
                OSError, "identity differs"):
            attention._observe(changed, {}, clock=lambda: 0)
        self.assertEqual(changed.attempts, [])

        transport = _Transport()

        def respond(_, report, step, **kwargs):
            if step == "baseline":
                raise OSError("unexpected_attention_led_payload")
            return Mock(response_field=0x82)

        with patch.object(attention, "_response", side_effect=respond), \
                self.assertRaisesRegex(OSError, "unexpected_attention"):
            attention._observe(transport, {}, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline"])

    def test_happy_path_restores_each_target_and_finally_verifies(self):
        transport = _Transport()
        report = {}
        with patch.object(
                attention, "_response", return_value=Mock(response_field=0x82)), \
                redirect_stderr(io.StringIO()):
            attention._observe(transport, report, clock=lambda: 0)
        expected = ["baseline"]
        for name, _ in attention.TARGETS:
            expected.extend((name + "_set", name + "_restore"))
        expected.append("final_verify")
        self.assertEqual(transport.attempts, expected)
        self.assertEqual(report["status"], attention.SUCCESS)
        self.assertTrue(report["final_baseline_verified"])
        self.assertEqual(report["accepted_tx_bytes"], 804)
        self.assertEqual(transport.wait.call_count, len(attention.TARGETS))

    def test_every_setter_and_restore_fault_has_one_cleanup_attempt_no_retry(self):
        boundary_steps = [
            name + suffix
            for name, _ in attention.TARGETS
            for suffix in ("_set", "_restore")
        ]
        for fault_step in boundary_steps:
            for fault_kind in ("submit", "response"):
                transport = _Transport(
                    submit_fault=fault_step if fault_kind == "submit" else None)

                def respond(_, report, step, **kwargs):
                    if fault_kind == "response" and step == fault_step:
                        raise OSError(step + " response")
                    return Mock(response_field=0x82)

                with self.subTest(step=fault_step, kind=fault_kind), \
                        patch.object(attention, "_response", side_effect=respond), \
                        redirect_stderr(io.StringIO()), self.assertRaises(OSError):
                    attention._observe(transport, {}, clock=lambda: 0)
                target = fault_step.removesuffix("_set").removesuffix("_restore")
                restores = [
                    step for step in transport.attempts
                    if step == target + "_restore"
                ]
                self.assertEqual(len(restores), 1)
                transport.close.assert_called_once()

    def test_interruption_after_each_setter_restores_once(self):
        for position, (target, _) in enumerate(attention.TARGETS, 1):
            transport = _Transport()
            transport.wait.side_effect = [
                *(None for _ in range(position - 1)), KeyboardInterrupt()
            ]
            with self.subTest(target=target), patch.object(
                    attention, "_response", return_value=Mock(response_field=0x82)), \
                    redirect_stderr(io.StringIO()), self.assertRaises(KeyboardInterrupt):
                attention._observe(transport, {}, clock=lambda: 0)
            self.assertEqual(
                transport.attempts.count(target + "_restore"), 1)

    def test_coordinator_and_usb_recorder_admit_only_fixed_scope(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=attention.SERIAL_SECONDS, baudrate=57600,
            allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096,
            usb_tail_seconds=5, usb_close_grace_seconds=5,
            actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.ATTENTION_LED_CHECK_SCOPE)
        self.assertEqual(result["probe_name"], "DisconnectedLoadAttentionLedCheck")
        self.assertTrue(result["fixed_attention_led_check_authorized"])
        self.assertEqual(result["requested_application_bytes"], 804)
        for flag in FLAGS:
            self.assertIn(flag, command)
        attention._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        attention._validate_capture(capture.call_args.kwargs)

    def test_unverified_restoration_requires_evidence_bound_power_cycle(self):
        evidence = self.root / "attention"
        evidence.mkdir()
        (evidence / "metadata.json").write_text("{}\n", encoding="utf-8")
        session.evidence_manifest(evidence)
        digest = attention._verify_manifest(evidence)
        attention._write_state(self.root, {
            "status": "attention_restoration_unverified",
            "target": attention.TARGET,
            "set_evidence": str(evidence.resolve()),
            "set_manifest_sha256": digest,
        })
        with self.assertRaises(ValueError):
            attention.acknowledge_restoration(
                evidence, physical_restoration_confirmed=True)
        result = attention.acknowledge_restoration(
            evidence, physical_restoration_confirmed=True,
            power_cycle_confirmed=True)
        self.assertEqual(result["status"], "power_cycle_reset_confirmed")
        self.assertFalse(result["hardware_access"])


if __name__ == "__main__":
    unittest.main()
