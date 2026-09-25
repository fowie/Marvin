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
from tools import marvin_legacy_front_servo_baseline_restore as baseline_restore
from tools import marvin_legacy_front_servo_getter as getter
from tools import marvin_legacy_front_servo_mapper as mapper
from tools import marvin_legacy_servo_plan as plan
from tools.marvin_legacy_client import Received


class LegacyServoPlanTests(unittest.TestCase):
    def test_single_baseline_restore_is_fixed_and_preserves_raw_response(self):
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(baseline_restore.main([]), 0)
        dry_run = json.loads(stdout.getvalue())
        packet = protocol.decode_packet(bytes.fromhex(
            dry_run["immutable_application_transcript_hex"][0]))
        self.assertEqual(
            (packet.sequence, packet.command, packet.payload),
            (3517, 0x1E, bytes.fromhex("c409aa0a")))
        self.assertEqual(dry_run["maximum_writes"], 1)
        self.assertFalse(dry_run["automatic_retries"])
        for option in ("sequence", "value", "dwell", "delta", "getter"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                baseline_restore.main(["--" + option, "1"])

        blocked = object.__new__(baseline_restore._Transport)
        blocked.writes = 1
        with self.assertRaises(OSError):
            blocked._submit_once(baseline_restore.REQUEST, deadline=1e12)
        blocked.writes = 0
        arbitrary = protocol.encode_request(
            baseline_restore.SEQUENCE, 0x1E, bytes.fromhex("ba09aa0a"))
        with self.assertRaises(OSError):
            blocked._submit_once(arbitrary, deadline=1e12)

        evidence = baseline_restore._response_evidence(Mock())
        evidence.submitted_at, evidence.deadline = 10, 11
        evidence.feed(Received(frame(
            sequence=baseline_restore.SEQUENCE, command=0x1E,
            status=0x82, payload=b""), 10.1, 10.2), 10.3)
        self.assertEqual(evidence.candidates, 1)
        wrong_payload = baseline_restore._response_evidence(Mock())
        wrong_payload.submitted_at, wrong_payload.deadline = 10, 11
        with self.assertRaisesRegex(OSError, "unexpected_baseline_restore_payload"):
            wrong_payload.feed(Received(frame(
                sequence=baseline_restore.SEQUENCE, command=0x1E,
                status=0x82, payload=b"\x00"), 10.1, 10.2), 10.3)

        transport = Mock(
            token=b"token", serial_bytes=10, writes=1,
            last_write_started=10.0,
            last_write_sequence=baseline_restore.SEQUENCE,
            event=Mock())
        transport.revalidate.return_value = transport.token
        transport.write.return_value = len(baseline_restore.REQUEST)
        transport.close.return_value = None
        raw = frame(
            sequence=baseline_restore.SEQUENCE, command=0x1E,
            status=0x82, payload=b"")

        def observe_response(_, evidence, **__):
            evidence.candidates = 1
            evidence.events = [{"stream": {"raw_hex": raw.hex()}}]

        report = {}
        clock = Mock(side_effect=[9.0, 10.0, 10.1, 10.2, 10.3])
        with patch.object(
                baseline_restore.zero, "_observe_response",
                side_effect=observe_response):
            baseline_restore._observe(transport, report, clock=clock)
        self.assertEqual(report["status"], baseline_restore.SUCCESS)
        self.assertEqual(report["raw_response_field_uint8"], 0x82)
        self.assertEqual(report["raw_payload_hex"], "")
        self.assertEqual(report["application_submission_attempts"], 1)
        self.assertEqual(report["application_acknowledgment"], "not_established")
        transport.write.assert_called_once_with(
            baseline_restore.REQUEST, deadline=14.0)

        tail_response = Mock(deadline=.5, candidates=1)
        tail_transport = Mock(fd=99, ingress=Mock())
        tail_transport.identity.side_effect = OSError(
            "identity validation exceeded response deadline")
        tail_clock = Mock(side_effect=(
            .34, .41, .41, .41, .45, .45, .50, .50))
        with patch.object(baseline_restore.zero.select, "select",
                          return_value=([99], [], [])), \
                patch.object(
                    tail_transport, "read_response",
                    return_value=b"tail") as tail_read:
            baseline_restore.zero._observe_response(
                tail_transport, tail_response, deadline=.5, clock=tail_clock)
        tail_transport.identity.assert_not_called()
        self.assertEqual(tail_transport.ingress.pump.call_count, 2)
        self.assertEqual(tail_read.call_count, 2)
        tail_response.feed.assert_called_with(b"tail", .45)
        tail_response.finish.assert_called_once_with(.5)

    def test_single_getter_is_fixed_read_only_and_preserves_response(self):
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(getter.main([]), 0)
        dry_run = json.loads(stdout.getvalue())
        packet = protocol.decode_packet(bytes.fromhex(
            dry_run["immutable_application_transcript_hex"][0]))
        self.assertEqual(
            (packet.sequence, packet.command, packet.payload),
            (getter.SEQUENCE, 0x1D, b""))
        self.assertEqual(dry_run["maximum_writes"], 1)
        self.assertEqual(dry_run["request_payload_bytes"], 0)
        self.assertFalse(dry_run["automatic_retries"])
        self.assertFalse(dry_run["automatic_reconnect"])
        for option in ("sequence", "value", "setter", "retry"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                getter.main(["--" + option, "1"])
        blocked_transport = object.__new__(getter._Transport)
        blocked_transport.writes = 1
        with self.assertRaises(OSError):
            blocked_transport._submit_once(getter.REQUEST, deadline=1e12)

        evidence = getter._response_evidence(Mock())
        evidence.submitted_at, evidence.deadline = 10, 11
        evidence.feed(Received(frame(
            sequence=getter.SEQUENCE, command=0x1D, status=0x82,
            payload=bytes.fromhex("c409aa0a")), 10.1, 10.2), 10.3)
        self.assertEqual(evidence.candidates, 1)
        wrong_size = getter._response_evidence(Mock())
        wrong_size.submitted_at, wrong_size.deadline = 10, 11
        with self.assertRaisesRegex(OSError, "unexpected_servo_getter_payload_size"):
            wrong_size.feed(Received(frame(
                sequence=getter.SEQUENCE, command=0x1D, status=0x00,
                payload=b"\x00\x00"), 10.1, 10.2), 10.3)

        transport = Mock(
            token=b"token", serial_bytes=14, writes=1,
            last_write_started=10.0, last_write_sequence=getter.SEQUENCE,
            event=Mock())
        transport.revalidate.return_value = transport.token
        transport.write.return_value = len(getter.REQUEST)
        transport.close.return_value = None
        raw = frame(
            sequence=getter.SEQUENCE, command=0x1D, status=0x82,
            payload=bytes.fromhex("c409aa0a"))

        def observe_response(_, evidence, **__):
            evidence.candidates = 1
            evidence.events = [{"stream": {"raw_hex": raw.hex()}}]

        report = {}
        clock = Mock(side_effect=[9.0, 10.0, 10.1, 10.2])
        with patch.object(getter.zero, "_observe_response", side_effect=observe_response):
            getter._observe(transport, report, clock=clock)
        self.assertEqual(report["status"], getter.SUCCESS)
        self.assertEqual(report["raw_response_field_uint8"], 0x82)
        self.assertEqual(report["raw_payload_hex"], "c409aa0a")
        self.assertEqual(report["words_uint16_le"], [2500, 2730])
        self.assertEqual(report["application_submission_attempts"], 1)
        self.assertEqual(report["application_acknowledgment"], "not_established")

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
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(
                mapper.main(["--word1-five-degree-diagnostic"]), 0)
        word1_five_degree = json.loads(stdout.getvalue())
        self.assertEqual(
            (word1_five_degree["fixed_mode"],
             word1_five_degree["candidate_wire_word"],
             word1_five_degree["target_words_uint16"],
             word1_five_degree["word1_delta"]),
            ("word1-five-degree-diagnostic", 1, [2500, 2680], -50))
        self.assertEqual(
            word1_five_degree["external_word0_five_degree_observation"],
            {
                "operator_report": "no_visible_movement_and_audible_servo_engagement",
                "protocol_baseline_words_uint16": [2500, 2730],
                "protocol_target_words_uint16": [2450, 2730],
                "setter_and_restore_host_submission": "established",
                "restore_application_correlation": "not_established",
                "physical_restoration": "unproved",
                "classification": "external_evidence_not_channel_proof",
            })
        word1_five_degree_frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in word1_five_degree["immutable_application_transcript_hex"]
        ]
        self.assertEqual(
            [(frame.sequence, frame.command, frame.payload)
             for frame in word1_five_degree_frames],
            [
                (3512, 0x1D, b""),
                (3513, 0x1E, bytes.fromhex("c409780a")),
                (3514, 0x1E, bytes.fromhex("c409aa0a")),
                (3515, 0x1D, b""),
            ])
        self.assertIn(
            "--operator-confirmed-word1-five-degree-mechanical-clearance",
            word1_five_degree["required"])
        self.assertIn(
            "--authorize-single-legacy-1e-front-camera-word1-five-degree-diagnostic-command",
            word1_five_degree["required"])
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(
                mapper.main(["--word0-direction-diagnostic"]), 0)
        direction = json.loads(stdout.getvalue())
        self.assertEqual(
            (direction["fixed_mode"], direction["target_words_uint16"],
             direction["observation_seconds"]),
            ("word0-direction-diagnostic", [2450, 2730], .25))
        self.assertIn(
            "actual hold from clean correlated setter response end",
            direction["observation_timing_semantics"])
        self.assertEqual(direction["maximum_setter_to_restore_seconds"], .76)
        self.assertEqual(direction["required"], [
            "--run", "--expected-physical-port", "--output NEW-SESSION-DIR",
            "--word0-direction-diagnostic",
            "--authorize-unchanged-setup-session-after-fresh-safety-confirmation",
        ])
        self.assertIn("No scope or probe is required",
                      direction["combined_live_confirmation"])
        self.assertEqual(
            direction["operator_observed_direction_calibration"][
                "camera_tilt_direction"],
            "not_directly_observed_for_2500_to_2450")
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(
                mapper.main(["--word0-100-unit-direction-diagnostic"]), 0)
        hundred = json.loads(stdout.getvalue())
        self.assertEqual(
            (hundred["fixed_mode"], hundred["target_words_uint16"],
             hundred["expected_ax_goal_position"],
             hundred["expected_ax_goal_delta_from_baseline_833"]),
            ("word0-100-unit-direction-characterization", [2400, 2730],
             800, -33))
        self.assertEqual(
            hundred["operator_camera_displacement_hypothesis_degrees"], 1.0)
        self.assertIn("roughly 1-degree hypothesis",
                      hundred["decrement_safety_basis"])
        hundred_frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in hundred["immutable_application_transcript_hex"]]
        self.assertEqual(
            [(packet.sequence, packet.command, packet.payload)
             for packet in hundred_frames],
            [(3518, 0x1D, b""),
             (3519, 0x1E, bytes.fromhex("6009aa0a")),
             (3520, 0x1E, bytes.fromhex("c409aa0a")),
             (3521, 0x1D, b"")])
        self.assertEqual(hundred["required"], [
            "--run", "--expected-physical-port", "--output NEW-SESSION-DIR",
            "--word0-100-unit-direction-diagnostic",
            "--authorize-unchanged-setup-session-after-fresh-safety-confirmation",
        ])
        self.assertEqual(
            hundred["operator_observed_direction_calibration"][
                "servo_output_direction"],
            "clockwise_in_observed_test_frame")
        self.assertEqual(
            hundred["operator_observed_direction_calibration"][
                "camera_tilt_direction"],
            "upward_for_2500_to_2400_by_composed_post_run_operator_observations")
        self.assertTrue(mapper._Word0100UnitTransport.direction_hold)
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(
                mapper.main(["--word0-500-unit-installed-camera-diagnostic"]), 0)
        five_hundred = json.loads(stdout.getvalue())
        self.assertEqual(
            (five_hundred["fixed_mode"],
             five_hundred["target_words_uint16"],
             five_hundred["expected_ax_goal_position"],
             five_hundred["expected_ax_goal_delta_from_baseline_833"],
             five_hundred["operator_camera_displacement_hypothesis_degrees"]),
            ("word0-500-unit-installed-camera-characterization",
             [2000, 2730], 666, -167, 5.0))
        five_hundred_frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in five_hundred["immutable_application_transcript_hex"]]
        self.assertEqual(
            [(packet.sequence, packet.command, packet.payload)
             for packet in five_hundred_frames],
            [(3522, 0x1D, b""),
             (3523, 0x1E, bytes.fromhex("d007aa0a")),
             (3524, 0x1E, bytes.fromhex("c409aa0a")),
             (3525, 0x1D, b"")])
        self.assertEqual(five_hundred["required"], [
            "--run", "--expected-physical-port", "--output NEW-SESSION-DIR",
            "--word0-500-unit-installed-camera-diagnostic",
            "--authorize-unchanged-setup-session-after-fresh-safety-confirmation",
        ])
        self.assertTrue(mapper._Word0500UnitTransport.direction_hold)
        with patch.object(os, "open", side_effect=AssertionError("no hardware")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(
                mapper.main([
                    "--word0-plus-500-unit-installed-camera-diagnostic"]), 0)
        inverse = json.loads(stdout.getvalue())
        self.assertEqual(
            (inverse["fixed_mode"], inverse["target_words_uint16"],
             inverse["expected_ax_goal_position"],
             inverse["expected_ax_goal_delta_from_baseline_833"],
             inverse["operator_camera_displacement_hypothesis_degrees"]),
            ("word0-plus-500-unit-installed-camera-characterization",
             [3000, 2730], 1000, 167, 5.0))
        inverse_frames = [
            protocol.decode_packet(bytes.fromhex(raw))
            for raw in inverse["immutable_application_transcript_hex"]]
        self.assertEqual(
            [(packet.sequence, packet.command, packet.payload)
             for packet in inverse_frames],
            [(3526, 0x1D, b""),
             (3527, 0x1E, bytes.fromhex("b80baa0a")),
             (3528, 0x1E, bytes.fromhex("c409aa0a")),
             (3529, 0x1D, b"")])
        self.assertEqual(inverse["expected_ax_goal_range"], [0, 1023])
        self.assertTrue(inverse["expected_ax_goal_within_range"])
        self.assertTrue(inverse["sibling_word1_preserved"])
        self.assertEqual(
            inverse["operator_observed_direction_calibration"][
                "camera_tilt_direction"],
            "downward_hypothesis_from_directly_proven_inverse_relation")
        self.assertEqual(inverse["required"], [
            "--run", "--expected-physical-port", "--output NEW-SESSION-DIR",
            "--word0-plus-500-unit-installed-camera-diagnostic",
            "--authorize-unchanged-setup-session-after-fresh-safety-confirmation",
        ])
        self.assertTrue(mapper._Word0Plus500UnitTransport.direction_hold)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory) / "session"
            with patch.object(
                    mapper, "run_diagnostic",
                    return_value={"status": "complete"}) as triggered, \
                    redirect_stderr(io.StringIO()):
                session_result = mapper.run_authorized_session(
                    root, expected_physical_port="1-2",
                    word0_100_unit=True,
                    input_fn=Mock(side_effect=["RUN", "RUN", "END"]),
                    authorize_unchanged_setup_session_after_fresh_safety_confirmation=True,
                )
            self.assertEqual(session_result["successful_runs"], 2)
            self.assertEqual(triggered.call_count, 2)
            self.assertEqual(
                [call.args[0].name for call in triggered.call_args_list],
                ["run-0001", "run-0002"])
            self.assertTrue(all(
                call.kwargs["word0_100_unit"] is True
                and call.kwargs["run"] is True
                for call in triggered.call_args_list))
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory, \
                patch.object(
                    mapper, "run_diagnostic",
                    side_effect=OSError("identity changed")) as triggered, \
                self.assertRaisesRegex(OSError, "identity changed"):
            mapper.run_authorized_session(
                Path(directory) / "session",
                expected_physical_port="1-2",
                word0_direction=True,
                input_fn=Mock(side_effect=["RUN", "RUN"]),
                authorize_unchanged_setup_session_after_fresh_safety_confirmation=True,
            )
        triggered.assert_called_once()
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
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory, \
                self.assertRaisesRegex(ValueError, "acknowledgment"):
            mapper.run_diagnostic(
                Path(directory) / "evidence",
                expected_physical_port="1-2",
                run=True,
                word1_five_degree=True,
                **dict.fromkeys(
                    mapper.WORD1_FIVE_DEGREE_ACKNOWLEDGMENTS, False),
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

        hold_transport = Mock(
            fd=99, ingress=Mock(), setter_write_started=.9)
        hold_evidence = Mock(
            seen={"set": 1}, clean_ended={"set": 1.0})
        hold_report = {}
        hold_clock = Mock(side_effect=(1.1, 1.2, 1.25, 1.25))
        with patch.object(mapper.select, "select", return_value=([], [], [])):
            mapper._direction_hold(
                hold_transport, hold_report, hold_evidence,
                deadline=2.0, clock=hold_clock)
        hold_transport.identity.assert_called_once_with(deadline=1.26)
        self.assertEqual(hold_report["requested_hold_seconds"], .25)
        overrun_clock = Mock(side_effect=(1.1, 1.2, 1.27, 1.27))
        with patch.object(mapper.select, "select", return_value=([], [], [])), \
                self.assertRaisesRegex(OSError, "scheduling overrun"):
            mapper._direction_hold(
                Mock(fd=99, ingress=Mock(), setter_write_started=.9),
                {}, hold_evidence,
                deadline=2.0, clock=overrun_clock)

        held = Transport()
        held.steps = mapper.WORD0_FIVE_DEGREE_STEPS
        held.success = "direction"
        held.direction_hold = True
        held.event = Mock()
        held_deadlines = {}
        submit = held.submit

        def timed_submit(step, *, deadline):
            held_deadlines[step] = deadline
            result = submit(step, deadline=deadline)
            if step == "restore":
                held.last_write_started = 1.255
            return result

        def completed_hold(_transport, report, _evidence, **_):
            report.update(
                requested_hold_seconds=.25,
                hold_start_monotonic=1.0,
                hold_target_monotonic=1.25,
                hold_maximum_restore_start_monotonic=1.26,
                restore_identity_validated_after_setter=True,
            )

        held.submit = timed_submit
        held_report = {}
        with patch.object(mapper, "_response", side_effect=response), \
                patch.object(mapper, "_direction_hold", side_effect=completed_hold), \
                patch.object(
                    mapper, "_set_restore_responses",
                    return_value={"set": 1, "restore": 1}), \
                redirect_stderr(io.StringIO()):
            mapper._observe(held, held_report, clock=lambda: 0)
        self.assertEqual(held_deadlines["restore"], 1.26)
        self.assertAlmostEqual(held_report["actual_hold_seconds"], .255)
        self.assertTrue(held_report["hold_within_maximum"])

        for hold_error in (KeyboardInterrupt(), OSError("hold overrun")):
            held = Transport()
            held.steps = mapper.WORD0_FIVE_DEGREE_STEPS
            held.success = "direction"
            held.direction_hold = True
            held.event = Mock()
            with patch.object(mapper, "_response", side_effect=response), \
                    patch.object(
                        mapper, "_direction_hold", side_effect=hold_error), \
                    patch.object(
                        mapper, "_set_restore_responses",
                        return_value={"set": 1, "restore": 1}), \
                    redirect_stderr(io.StringIO()), \
                    self.assertRaises(type(hold_error)):
                mapper._observe(held, {}, clock=lambda: 0)
            self.assertEqual(held.attempts[:3], ["baseline", "set", "restore"])
            self.assertEqual(held.attempts.count("restore"), 1)

        identity_blocked = Transport()
        identity_blocked.steps = mapper.WORD0_FIVE_DEGREE_STEPS
        identity_blocked.direction_hold = True
        identity_blocked.identity = Mock(
            side_effect=OSError("identity changed before setter"))
        with patch.object(mapper, "_response", side_effect=response), \
                redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(OSError, "identity changed before setter"):
            mapper._observe(identity_blocked, {}, clock=lambda: 0)
        self.assertEqual(identity_blocked.attempts, ["baseline"])
        self.assertFalse(identity_blocked.nonzero_may_have_applied)
        self.assertFalse(identity_blocked.restore_attempted)

        identity_changed = Transport()
        submit = identity_changed.submit

        def fail_setter_identity(step, *, deadline):
            if step == "set":
                raise OSError("identity changed before setter")
            return submit(step, deadline=deadline)

        identity_changed.submit = fail_setter_identity
        with patch.object(mapper, "_response", side_effect=response), \
                redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(OSError, "identity changed before setter"):
            mapper._observe(identity_changed, {}, clock=lambda: 0)
        self.assertEqual(identity_changed.attempts, ["baseline"])
        self.assertEqual(identity_changed.writes, 1)
        self.assertFalse(identity_changed.nonzero_may_have_applied)
        self.assertFalse(identity_changed.restore_attempted)

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

        tail_transport = Mock(
            fd=99, ingress=Mock(), serial_bytes=0, steps=mapper.STEPS)
        tail_transport.read_response.return_value = None
        tail_clock = Mock(side_effect=(.34, .34, .41, .41, .50, .50, .50))
        with patch.object(mapper.select, "select", return_value=([], [], [])):
            self.assertEqual(
                mapper._set_restore_responses(
                    tail_transport, {
                        "protocol_evidence": [],
                        "setter_prewrite_monotonic": 0,
                        "restore_prewrite_monotonic": 0,
                    },
                    deadline=.5, clock=tail_clock),
                {"set": 0, "restore": 0},
            )
        tail_transport.identity.assert_not_called()

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
