"""Offline two-phase legacy LED mapper tests."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests import test_marvin_session as session_tests
from tests.test_marvin_legacy_client import frame
from tools import marvin_legacy_led_mapper as mapper
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.LED_MAPPING_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.LED_MAPPING_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0

    def __init__(self, transcript, write_fault=None):
        self.transcript = transcript
        self.write_fault = write_fault
        self.writes = 0
        self.event = Mock()
        self.close = Mock()

    def revalidate(self, *, deadline):
        return self.token

    def write(self, raw, *, deadline):
        index = self.writes
        self.assert_request(index, raw)
        self.writes += 1
        if self.write_fault == (index, "error"):
            raise OSError("uncertain write")
        if self.write_fault == (index, "partial"):
            return 1
        return len(raw)

    def assert_request(self, index, raw):
        if raw != self.transcript[index]:
            raise AssertionError("unexpected request")


class LegacyLedMapperTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".led-mapper-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def respond(self, statuses, baseline):
        calls = 0

        def response(_, evidence, **kwargs):
            nonlocal calls
            status = statuses[calls]
            payload = baseline if evidence.command == 0x17 else b""
            calls += 1
            evidence.feed(Mock(
                data=frame(payload, command=evidence.command, status=status,
                           sequence=evidence.sequence),
                started_at=.1, ended_at=.2), .2)
            evidence.finish(.2)

        return response

    def test_exact_index_frames_bounds_and_no_payload_knobs(self):
        self.assertEqual([raw.hex() for raw in mapper.transcript_for("set", 0)], [
            "53800c17000000697045",
            "53810c18001200ff0000000000000000000000000000000000b1db45",
        ])
        self.assertEqual([raw.hex() for raw in mapper.transcript_for("set", 17)], [
            "53c40c17000000663445",
            "53c50c180012000000000000000000000000000000000000ffb1bd45",
        ])
        baseline = bytes(range(18))
        self.assertEqual([raw.hex() for raw in mapper.transcript_for("restore", 0, baseline)], [
            "53820c18001200000102030405060708090a0b0c0d0e0f1011de6245",
            "53830c17000000694345",
        ])
        for index in (-1, 18, True, 1.0):
            with self.subTest(index=index), self.assertRaises(ValueError):
                mapper.transcript_for("set", index)
        plan = mapper.prepare_set(0)
        self.assertEqual((plan["fixed_value"], plan["maximum_writes"],
                          plan["maximum_application_bytes"]), (255, 2, 38))
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(mapper.main(["--phase", "set", "--index", "0", *FLAGS]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["fixed_value"], 255)
        for knob in ("payload", "value", "sequence", "retry", "count"):
            with self.subTest(knob=knob), self.assertRaises(SystemExit):
                mapper.main(["--phase", "set", "--index", "0", *FLAGS, "--" + knob, "1"])

    def test_fixed_left_attention_group_preserves_baseline_and_uses_next_sequences(self):
        baseline = bytes.fromhex("000000000000000000000000ff0000ff0000")
        expected = bytes.fromhex("00ffffffffff000000000000ff0000ff0000")
        self.assertEqual(mapper.test_vector(mapper.LEFT_ATTENTION_PHOTO, baseline), expected)
        self.assertEqual([raw.hex() for raw in mapper.transcript_for(
                "set", mapper.LEFT_ATTENTION_PHOTO, baseline)], [
            "53c80c1700000066f845",
            "53c90c1800120000ffffffffff000000000000ff0000ff000024cc45",
        ])
        self.assertEqual([raw.hex() for raw in mapper.transcript_for(
                "restore", mapper.LEFT_ATTENTION_PHOTO, baseline)], [
            "53ca0c18001200000000000000000000000000ff0000ff0000a40245",
            "53cb0c1700000066cb45",
        ])
        plan = mapper.prepare_set(mapper.LEFT_ATTENTION_PHOTO)
        self.assertEqual(plan["immutable_application_transcript_hex"],
                         ["53c80c1700000066f845"])
        self.assertEqual(
            plan["runtime_derived_setter_policy"],
            "captured_baseline_with_only_indices_1_2_3_4_5_forced_to_ff")
        self.assertEqual((plan["maximum_writes"], plan["maximum_application_bytes"]), (2, 38))

        transport = _Transport(mapper.transcript_for("set", mapper.LEFT_ATTENTION_PHOTO))
        report = {}
        with patch.object(
                mapper.zero, "_observe_response",
                side_effect=self.respond((0x80, 0x82), baseline)):
            mapper._set_observer(
                transport.transcript, mapper.LEFT_ATTENTION_PHOTO, lambda _: None)(
                transport, report, clock=lambda: 0)
        self.assertEqual(transport.writes, 2)
        self.assertEqual(report["derived_setter_payload_hex"], expected.hex())
        self.assertEqual(report["preserved_baseline_indices"], [0, *range(6, 18)])
        self.assertEqual(report["operator_action"], "RESTORE_REQUIRED")

        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=Mock(side_effect=harness.capture), binary_payload_limit=4096,
            usb_tail_seconds=5, usb_close_grace_seconds=5, actuators_isolated=False,
            _led_mapping_phase="set",
            _led_mapping_index=mapper.LEFT_ATTENTION_PHOTO,
            _led_mapping_baseline=None, **DECLARATIONS)
        self.assertEqual(result["requested_application_bytes"], 38)
        self.assertEqual(
            result["runtime_derived_setter_policy"],
            "captured_baseline_with_only_indices_1_2_3_4_5_forced_to_ff")
        self.assertEqual(result["immutable_application_transcript_hex"],
                         ["53c80c1700000066f845"])

    def test_fixed_wheel_led_blink_pilot_gates_state_and_restores_exact_baseline(self):
        state = bytes.fromhex("000000000000000000000000ff0000ff0000")
        baseline = bytes.fromhex("0000000000000000000000000000002a0000")
        changed = bytes.fromhex("0000000000000000000000002a00002a0000")
        target = mapper.WHEEL_LED_BLINK_PILOT
        self.assertEqual(mapper.test_vector(target, baseline), changed)
        self.assertEqual([raw.hex() for raw in mapper.transcript_for("set", target)], [
            "53cc0c17000000677c45",
            "53cd0c19000000644545",
        ])
        self.assertEqual(
            [raw.hex() for raw in mapper.transcript_for("set", target, baseline)], [
                "53cc0c17000000677c45",
                "53cd0c19000000644545",
                "53ce0c1a0012000000000000000000000000002a00002a0000773345",
            ])
        self.assertEqual(
            [raw.hex() for raw in mapper.transcript_for("restore", target, baseline)], [
                "53cf0c1a0012000000000000000000000000000000002a00002d6c45",
                "53d00c1900000067c845",
            ])
        plan = mapper.prepare_set(target)
        self.assertEqual(
            (plan["fixed_value"], plan["maximum_writes"],
             plan["maximum_application_bytes"], plan["max_serial_rx_bytes"]),
            (42, 3, 48, 8192))
        self.assertEqual(
            plan["runtime_derived_setter_policy"],
            "captured_blink_baseline_with_only_index_12_changed_to_2a")

        transport = _Transport(mapper.transcript_for("set", target))
        payloads = iter((state, baseline, b""))

        def response(_, evidence, **kwargs):
            payload = next(payloads)
            status = 0x82 if evidence.command == 0x1A else 0x80
            evidence.feed(Mock(
                data=frame(payload, command=evidence.command, status=status,
                           sequence=evidence.sequence),
                started_at=.1, ended_at=.2), .2)
            evidence.finish(.2)

        report = {}
        with patch.object(mapper.zero, "_observe_response", side_effect=response):
            mapper._set_observer(
                transport.transcript, target, lambda value: self.assertEqual(value, baseline))(
                transport, report, clock=lambda: 0)
        self.assertEqual(transport.writes, 3)
        self.assertEqual(report["led_state_baseline_payload_hex"], state.hex())
        self.assertEqual(report["derived_setter_payload_hex"], changed.hex())
        self.assertEqual(report["preserved_baseline_indices"],
                         [index for index in range(18) if index != 12])
        self.assertEqual(report["setter_raw_response_field"], 0x82)
        self.assertEqual(report["operator_action"], "RESTORE_REQUIRED")

        restore = mapper.transcript_for("restore", target, baseline)
        restored = _Transport(restore)
        restore_payloads = iter((b"", baseline))

        def restore_response(_, evidence, **kwargs):
            payload = next(restore_payloads)
            evidence.feed(Mock(
                data=frame(payload, command=evidence.command, status=0x80,
                           sequence=evidence.sequence),
                started_at=.1, ended_at=.2), .2)
            evidence.finish(.2)

        restore_report = {}
        with patch.object(mapper.zero, "_observe_response", side_effect=restore_response):
            mapper._restore_observer(restore, target, baseline, "changed")(
                restored, restore_report, clock=lambda: 0)
        self.assertEqual(restored.writes, 2)
        self.assertTrue(restore_report["baseline_reverified"])

        rejected_restore = _Transport(restore)
        with patch.object(
                mapper.zero, "_observe_response",
                side_effect=self.respond((0x82,), baseline)):
            mapper._restore_observer(restore, target, baseline, "uncertain")(
                rejected_restore, {}, clock=lambda: 0)
        self.assertEqual(rejected_restore.writes, 1)

        partial = _Transport(mapper.transcript_for("set", target), (2, "partial"))
        payloads = iter((state, baseline))
        with patch.object(mapper.zero, "_observe_response", side_effect=response), \
                self.assertRaisesRegex(OSError, "Partial"):
            mapper._set_observer(partial.transcript, target, lambda _: None)(
                partial, {}, clock=lambda: 0)
        self.assertEqual(partial.writes, 3)

        for state_payload, blink_payload, writes in (
                (bytes(18), baseline, 1),
                (state, changed, 2)):
            failing = _Transport(mapper.transcript_for("set", target))
            payloads = iter((state_payload, blink_payload))

            def rejected(_, evidence, **kwargs):
                payload = next(payloads)
                evidence.feed(Mock(
                    data=frame(payload, command=evidence.command, status=0x80,
                               sequence=evidence.sequence),
                    started_at=.1, ended_at=.2), .2)
                evidence.finish(.2)

            with self.subTest(writes=writes), patch.object(
                    mapper.zero, "_observe_response", side_effect=rejected), \
                    self.assertRaises(OSError):
                mapper._set_observer(failing.transcript, target, lambda _: None)(
                    failing, {}, clock=lambda: 0)
            self.assertEqual(failing.writes, writes)

        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=Mock(side_effect=harness.capture), binary_payload_limit=4096,
            usb_tail_seconds=5, usb_close_grace_seconds=5, actuators_isolated=False,
            _led_mapping_phase="set", _led_mapping_index=target,
            _led_mapping_baseline=None, **DECLARATIONS)
        self.assertEqual(result["requested_application_bytes"], 48)
        self.assertEqual(
            result["runtime_derived_setter_policy"],
            "captured_blink_baseline_with_only_index_12_changed_to_2a")
        self.assertEqual(result["probe_name"], "DisconnectedLoadWheelLedBlinkPilot")
        self.assertTrue(result["fixed_wheel_led_blink_pilot_authorized"])
        self.assertEqual(result["immutable_application_transcript_hex"], [
            "53cc0c17000000677c45",
            "53cd0c19000000644545",
        ])

    def test_set_raw82_exits_restore_required_and_faults_never_retry(self):
        baseline = bytes.fromhex("000000000000000000000000ff0000ff0000")
        transcript = mapper.transcript_for("set", 0)
        transport = _Transport(transcript)
        report = {}
        captured = []
        with patch.object(
                mapper.zero, "_observe_response",
                side_effect=self.respond((0x80, 0x82), baseline)):
            mapper._set_observer(transcript, 0, captured.append)(
                transport, report, clock=lambda: 0)
        self.assertEqual(captured, [baseline])
        self.assertEqual(report["status"], mapper.SET_COMPLETE)
        self.assertEqual(report["setter_raw_response_field"], 0x82)
        self.assertEqual(report["operator_action"], "RESTORE_REQUIRED")
        self.assertTrue(report["restore_required"])
        self.assertEqual(transport.writes, 2)

        for fault in ((1, "partial"), (1, "error")):
            failing = _Transport(transcript, fault)
            with self.subTest(fault=fault), patch.object(
                    mapper.zero, "_observe_response",
                    side_effect=self.respond((0x80,), baseline)), self.assertRaises(OSError):
                mapper._set_observer(transcript, 0, lambda _: None)(
                    failing, {}, clock=lambda: 0)
            self.assertEqual(failing.writes, 2)
            failing.close.assert_called_once()

        for error in (OSError("timeout"), OSError("crc"), OSError("correlation")):
            failing = _Transport(transcript)
            calls = 0

            def fail_setter(_, evidence, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise error
                self.respond((0x80,), baseline)(_, evidence, **kwargs)

            with self.subTest(error=error), patch.object(
                    mapper.zero, "_observe_response", side_effect=fail_setter), \
                    self.assertRaisesRegex(OSError, str(error)):
                mapper._set_observer(transcript, 0, lambda _: None)(
                    failing, {}, clock=lambda: 0)
            self.assertEqual(failing.writes, 2)

    def test_restore_raw82_stops_and_raw80_reverifies(self):
        baseline = bytes.fromhex("000000000000000000000000ff0000ff0000")
        transcript = mapper.transcript_for("restore", 0, baseline)
        rejected = _Transport(transcript)
        report = {}
        with patch.object(
                mapper.zero, "_observe_response",
                side_effect=self.respond((0x82,), baseline)):
            mapper._restore_observer(transcript, 0, baseline, "no_change")(
                rejected, report, clock=lambda: 0)
        self.assertEqual(rejected.writes, 1)
        self.assertEqual(report["status"], mapper.RESTORE_REJECTED)
        self.assertFalse(report["baseline_reverified"])
        self.assertEqual(report["operator_led_observation"], "no_change")

        restored = _Transport(transcript)
        report = {}
        with patch.object(
                mapper.zero, "_observe_response",
                side_effect=self.respond((0x80, 0x80), baseline)):
            mapper._restore_observer(transcript, 0, baseline, "changed")(
                restored, report, clock=lambda: 0)
        self.assertEqual(restored.writes, 2)
        self.assertEqual(report["status"], mapper.RESTORED)
        self.assertTrue(report["baseline_reverified"])
        self.assertEqual(report["restoration"], "getter_baseline_reverified")

    def test_manifest_binding_round_gate_ack_and_scope_forwarding(self):
        set_output = self.root / "set-index-0"
        baseline = bytes.fromhex("000000000000000000000000ff0000ff0000")

        def sealed_set(output, **kwargs):
            output.mkdir()
            observation = {
                "status": mapper.SET_COMPLETE,
                "baseline_payload_hex": baseline.hex(),
                "setter_raw_response_field": 0x82,
                "restore_required": True,
            }
            metadata = {
                "status": "led_mapping_set_phase_complete_unverified",
                "review": mapper.prepare_set(0),
                "observation": observation,
            }
            session.seal_evidence(output, metadata)
            return metadata

        with patch.object(mapper, "_run_live", side_effect=sealed_set):
            mapper.run_set(
                set_output, target=0, expected_physical_port="1-3", run=True,
                **DECLARATIONS)
        state = mapper._load_state(self.root)
        self.assertEqual(state["status"], "restore_required")
        self.assertEqual(mapper._bound_set_state(set_output)[3], baseline)
        extra = set_output / "unsealed"
        extra.write_text("tamper", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exact evidence file set"):
            mapper._bound_set_state(set_output)
        extra.unlink()
        with patch.object(mapper, "_run_live", side_effect=AssertionError("blocked")), \
                self.assertRaisesRegex(ValueError, "RESTORE_REQUIRED"):
            mapper.run_set(
                self.root / "set-index-1", target=1,
                expected_physical_port="1-3", run=True, **DECLARATIONS)

        restore_output = self.root / "restore-index-0"

        def sealed_rejected_restore(output, **kwargs):
            output.mkdir()
            observation = {
                "status": mapper.RESTORE_REJECTED,
                "restore_attempted": True,
                "restoration": "not_established",
            }
            metadata = {
                "status": "led_mapping_restore_phase_complete_unverified",
                "review": mapper.prepare_restore(0, baseline),
                "observation": observation,
            }
            session.seal_evidence(output, metadata)
            return metadata

        with patch.object(mapper, "_run_live", side_effect=sealed_rejected_restore):
            mapper.run_restore(
                set_output, restore_output, expected_physical_port="1-3", run=True,
                operator_observation="no_change", **DECLARATIONS)
        self.assertEqual(mapper._load_state(self.root)["status"], "restore_rejected")
        with patch.object(mapper, "_run_live", side_effect=AssertionError("no retry")), \
                self.assertRaisesRegex(ValueError, "not awaiting restoration"):
            mapper.run_restore(
                set_output, self.root / "restore-retry",
                expected_physical_port="1-3", run=True,
                operator_observation="no_change", **DECLARATIONS)
        acknowledged = mapper.acknowledge_power_cycle(
            set_output, operator_observation="uncertain", confirmed=True)
        self.assertEqual(acknowledged["status"], "power_cycle_reset_confirmed")

        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            _led_mapping_phase="set", _led_mapping_index=0,
            _led_mapping_baseline=None, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.LED_MAPPING_SCOPE)
        self.assertEqual(result["probe_name"], "DisconnectedLoadLedMappingPhase")
        self.assertFalse(result["unknown_command_authorized"])
        self.assertTrue(result["fixed_led_mapping_phase_authorized"])
        self.assertEqual(result["requested_application_bytes"], 38)
        for flag in FLAGS:
            self.assertIn(flag, command)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        mapper._validate_capture(capture.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
