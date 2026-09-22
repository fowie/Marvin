"""Consolidated offline checks for legacy tilt-servo planning and live mapping."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_marvin_legacy_protocol import frame
from tools import marvin_legacy_protocol as protocol
from tools import marvin_legacy_front_servo_mapper as mapper
from tools import marvin_legacy_servo_plan as plan
from tools.marvin_legacy_client import Received


class LegacyServoPlanTests(unittest.TestCase):
    def test_one_word_transcript_and_all_safety_gates(self):
        result = plan.prepare(
            word=0, baseline_words=(2500, 2730), target=2490,
            minimum=1200, maximum=2500, neutral=2500,
            connected_servo="front-camera-tilt-only",
            profile_source="synthetic-test-profile", first_sequence=3500,
        )
        frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in result["immutable_application_transcript_hex"]
        ]
        self.assertEqual(
            [(frame.sequence, frame.command, frame.payload) for frame in frames],
            [
                (3500, 0x1D, b""),
                (3501, 0x1E, bytes.fromhex("ba09aa0a")),
                (3502, 0x1E, bytes.fromhex("c409aa0a")),
                (3503, 0x1D, b""),
            ],
        )
        self.assertEqual(result["steps"], [
            "baseline_getter", "one_word_setter", "one_cleanup_restore", "verify_getter"])
        self.assertEqual((result["maximum_writes"], result["maximum_application_bytes"]), (4, 48))
        self.assertFalse(result["live_execution_authorized"])
        self.assertFalse(result["automatic_retries"])
        self.assertFalse(result["automatic_reconnect"])
        self.assertEqual(result["cleanup_attempts"], 1)
        self.assertEqual(result["physical_channel_assignment"], "not_established")
        self.assertIn("sealed_serial_and_usb_evidence_with_raw_bytes",
                      result["required_before_future_live_use"])

        base = dict(
            word=0, baseline_words=(2500, 2730), target=2490,
            minimum=1200, maximum=2500, neutral=2500,
            connected_servo="front-camera-tilt-only",
            profile_source="synthetic-test-profile",
        )
        invalid = (
            {"word": 2},
            {"word": True},
            {"baseline_words": [2500, 2730]},
            {"target": 2500},
            {"target": 1199},
            {"neutral": 2499},
            {"minimum": 2501},
            {"connected_servo": "both"},
            {"profile_source": ""},
            {"first_sequence": 65533},
        )
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                plan.prepare(**(base | change))

        getter = protocol.get_servo_position_request(17)
        packet = protocol.decode_packet(getter)
        self.assertEqual((packet.sequence, packet.command, packet.payload), (17, 0x1D, b""))

    def test_fixed_live_mapper_is_offline_by_default_and_cleanup_safe(self):
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(mapper.main([]), 0)
        dry_run = json.loads(stdout.getvalue())
        self.assertEqual(dry_run["baseline_words_uint16"], [2500, 2730])
        self.assertEqual(dry_run["target_words_uint16"], [2490, 2730])
        self.assertEqual(dry_run["observation_seconds"], .25)
        self.assertEqual(dry_run["maximum_nonzero_setters"], 1)
        frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in dry_run["immutable_application_transcript_hex"]
        ]
        self.assertEqual(
            [(frame.sequence, frame.command, frame.payload) for frame in frames],
            [
                (3500, 0x1D, b""),
                (3501, 0x1E, bytes.fromhex("ba09aa0a")),
                (3502, 0x1E, bytes.fromhex("c409aa0a")),
                (3503, 0x1D, b""),
            ],
        )
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(mapper.main(["--word1-front-camera-hypothesis"]), 0)
        word1 = json.loads(stdout.getvalue())
        self.assertEqual(word1["fixed_mode"], "word1-front-camera-hypothesis")
        self.assertEqual(word1["candidate_wire_word"], 1)
        self.assertEqual(word1["target_words_uint16"], [2500, 2720])
        self.assertEqual(word1["word1_front_camera_assignment"], "hypothesis_not_proved")
        self.assertEqual(
            word1["external_one_degree_observations"]["word0"]["classification"],
            "external_evidence_not_channel_proof")
        word1_frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in word1["immutable_application_transcript_hex"]
        ]
        self.assertEqual(
            [(frame.sequence, frame.command, frame.payload) for frame in word1_frames],
            [
                (3504, 0x1D, b""),
                (3505, 0x1E, bytes.fromhex("c409a00a")),
                (3506, 0x1E, bytes.fromhex("c409aa0a")),
                (3507, 0x1D, b""),
            ],
        )
        self.assertIn(
            "--authorize-single-legacy-1e-front-camera-word1-hypothesis-command",
            word1["required"])
        self.assertNotIn(
            "--authorize-single-legacy-1e-front-camera-mapping-command",
            word1["required"])
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(mapper.main(["--word0-five-degree-diagnostic"]), 0)
        five_degree = json.loads(stdout.getvalue())
        self.assertEqual(five_degree["fixed_mode"], "word0-five-degree-diagnostic")
        self.assertEqual(five_degree["candidate_wire_word"], 0)
        self.assertEqual(five_degree["target_words_uint16"], [2450, 2730])
        self.assertEqual(five_degree["word0_delta"], -50)
        self.assertIn("does not establish mechanical safety",
                      five_degree["five_degree_safety_basis"])
        self.assertEqual(
            set(five_degree["external_one_degree_observations"]),
            {"word0", "word1"})
        self.assertTrue(all(
            evidence["classification"] == "external_evidence_not_channel_proof"
            for evidence in five_degree[
                "external_one_degree_observations"].values()))
        five_degree_frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in five_degree["immutable_application_transcript_hex"]
        ]
        self.assertEqual(
            [(frame.sequence, frame.command, frame.payload)
             for frame in five_degree_frames],
            [
                (3508, 0x1D, b""),
                (3509, 0x1E, bytes.fromhex("9209aa0a")),
                (3510, 0x1E, bytes.fromhex("c409aa0a")),
                (3511, 0x1D, b""),
            ],
        )
        self.assertIn(
            "--operator-confirmed-word0-five-degree-mechanical-clearance",
            five_degree["required"])
        self.assertIn(
            "--authorize-single-legacy-1e-front-camera-word0-five-degree-diagnostic-command",
            five_degree["required"])
        evidence_transport = Mock(steps=mapper.WORD0_FIVE_DEGREE_STEPS)
        evidence_report = {
            "setter_prewrite_monotonic": 1.0,
            "restore_prewrite_monotonic": 1.1,
            "protocol_evidence": [],
        }
        evidence = mapper._SetRestoreEvidence(
            evidence_transport, evidence_report, 1.5)
        evidence.feed(Received(
            frame(sequence=3509, command=0x1E, status=0x82)
            + frame(sequence=3510, command=0x1E, status=0x82),
            1.2, 1.3), 1.4)
        evidence.finish(1.4)
        self.assertEqual(evidence.seen, {"set": 1, "restore": 1})
        self.assertEqual(
            [(row["step"], row["raw_response_field"])
             for row in evidence_report["protocol_evidence"]],
            [("set", 0x82), ("restore", 0x82)])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory, \
                self.assertRaisesRegex(ValueError, "acknowledgment"):
            mapper.run_diagnostic(
                Path(directory) / "evidence",
                expected_physical_port="1-2",
                run=True,
                **dict.fromkeys(mapper.ACKNOWLEDGMENTS, False),
            )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory, \
                self.assertRaisesRegex(ValueError, "acknowledgment"):
            mapper.run_diagnostic(
                Path(directory) / "evidence",
                expected_physical_port="1-2",
                run=True,
                word1_hypothesis=True,
                **dict.fromkeys(mapper.WORD1_ACKNOWLEDGMENTS, False),
            )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory, \
                self.assertRaisesRegex(ValueError, "acknowledgment"):
            mapper.run_diagnostic(
                Path(directory) / "evidence",
                expected_physical_port="1-2",
                run=True,
                word0_five_degree=True,
                **dict.fromkeys(
                    mapper.WORD0_FIVE_DEGREE_ACKNOWLEDGMENTS, False),
            )

        class Transport:
            token = "token"
            serial_bytes = 0
            steps = mapper.STEPS

            def __init__(self):
                self.attempts = []
                self.writes = 0
                self.last_write_started = 0
                self.setter_write_started = None
                self.nonzero_may_have_applied = False
                self.restore_attempted = False
                self.restore_correlated = False
                self.ingress = Mock(rx=[])
                self.close = Mock()

            def revalidate(self, *, deadline):
                return self.token

            def identity(self, *, deadline):
                return self.token

            def submit(self, step, *, deadline):
                self.attempts.append(step)
                self.writes += 1
                if step == "set":
                    self.nonzero_may_have_applied = True
                    self.setter_write_started = self.last_write_started
                if step == "restore":
                    if self.restore_attempted:
                        raise OSError("restore retry")
                    self.restore_attempted = True
                    self.restore_started_within_bound = True
                return len(self.steps[step])

        def response(transport, report, step, **_):
            report["protocol_evidence"].append({
                "step": step, "raw_response_field": 0x82 if step == "restore" else 0x80})
            if step == "restore":
                transport.restore_correlated = True
            return Mock(response_field=0x82 if step == "restore" else 0x80)

        def interrupted_response(transport, report, step, **kwargs):
            return response(transport, report, step, **kwargs)

        transport = Transport()
        with patch.object(mapper, "_response", side_effect=interrupted_response), \
                patch.object(
                    mapper, "_set_restore_responses",
                    side_effect=KeyboardInterrupt()), \
                redirect_stderr(io.StringIO()), \
                self.assertRaises(KeyboardInterrupt):
            mapper._observe(transport, {}, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline", "set", "restore"])
        self.assertEqual(transport.attempts.count("restore"), 1)
        self.assertTrue(transport.restore_attempted)
        transport.close.assert_called_once()

        for set_result in ("error", "partial"):
            faulted = Transport()
            original_submit = faulted.submit

            def faulty_submit(step, *, deadline):
                if step == "set":
                    faulted.attempts.append(step)
                    faulted.writes += 1
                    faulted.nonzero_may_have_applied = True
                    faulted.setter_write_started = faulted.last_write_started
                    if set_result == "error":
                        raise OSError("uncertain set write")
                    return 1
                return original_submit(step, deadline=deadline)

            faulted.submit = faulty_submit
            with self.subTest(set_result=set_result), \
                    patch.object(mapper, "_response", side_effect=response), \
                    patch.object(
                        mapper, "_set_restore_responses",
                        return_value={"set": 1, "restore": 1}), \
                    redirect_stderr(io.StringIO()), self.assertRaises(OSError):
                mapper._observe(faulted, {}, clock=lambda: 0)
            self.assertEqual(faulted.attempts.count("restore"), 1)
            self.assertTrue(faulted.restore_attempted)
            faulted.close.assert_called_once()

        live = mapper._Transport.__new__(mapper._Transport)
        live.nonzero_may_have_applied = True
        live.restore_attempted = False
        live.closed = False
        live.completed = ["baseline", "set"]
        live.writes = 2
        live.fd = 99
        live.ingress = Mock()
        live._owner = Mock()
        live._check = Mock(side_effect=OSError("recorder stopped"))
        live.event = Mock(side_effect=OSError("journal fault"))
        with patch.object(mapper.time, "monotonic", return_value=.2), \
                patch.object(mapper.os, "write",
                             return_value=len(mapper.STEPS["restore"])) as write, \
                self.assertRaises(OSError):
            live._restore_once(deadline=.25)
        write.assert_called_once_with(99, mapper.STEPS["restore"])
        self.assertTrue(live.restore_attempted)
        self.assertTrue(live.restore_started_within_bound)
        live._check.assert_not_called()

        late = mapper._Transport.__new__(mapper._Transport)
        late.nonzero_may_have_applied = True
        late.restore_attempted = False
        late.closed = False
        late.completed = ["baseline", "set"]
        late.writes = 2
        late.fd = 99
        late.ingress = Mock()
        late._owner = Mock()
        late.event = Mock()
        with patch.object(mapper.time, "monotonic", side_effect=(.26, .27)), \
                patch.object(mapper.os, "write",
                             return_value=len(mapper.STEPS["restore"])) as write:
            self.assertEqual(
                late._restore_once(deadline=.25),
                len(mapper.STEPS["restore"]))
        write.assert_called_once_with(99, mapper.STEPS["restore"])
        self.assertTrue(late.restore_attempted)
        self.assertFalse(late.restore_started_within_bound)

        late_report = {}
        late_transport = Transport()
        original_submit = late_transport.submit

        def late_restore(step, *, deadline):
            if step == "restore":
                late_transport.attempts.append(step)
                late_transport.writes += 1
                late_transport.restore_attempted = True
                late_transport.last_write_started = .26
                late_transport.restore_started_within_bound = False
                raise OSError("restore evidence failed")
            return original_submit(step, deadline=deadline)

        late_transport.submit = late_restore
        with patch.object(mapper, "_response", side_effect=response), \
                redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(OSError, "restore evidence failed"):
            mapper._observe(late_transport, late_report, clock=lambda: 0)
        self.assertEqual(late_transport.attempts.count("restore"), 1)
        self.assertEqual(late_report["setter_to_restore_start_seconds"], .26)
        self.assertFalse(late_report["restore_started_within_bound"])
        self.assertEqual(
            [error["step"] for error in late_report["finalization_errors"]],
            ["restore", "restore_bound"])

        for reached_prewrite in (False, True):
            setter = mapper._Transport.__new__(mapper._Transport)
            setter.completed = ["baseline"]
            setter.nonzero_may_have_applied = False
            setter.setter_write_started = None
            setter.last_write_started = 10
            setter.last_write_sequence = 3508

            def setter_failure(_raw, *, deadline):
                if reached_prewrite:
                    setter.last_write_started = 11
                raise OSError("setter failure")

            setter._submit_once = setter_failure
            with self.subTest(reached_prewrite=reached_prewrite), \
                    self.assertRaisesRegex(OSError, "setter failure"):
                setter.submit("set", deadline=12)
            self.assertIs(
                setter.nonzero_may_have_applied, reached_prewrite)
            self.assertEqual(
                setter.setter_write_started,
                11 if reached_prewrite else None)


if __name__ == "__main__":
    unittest.main()
