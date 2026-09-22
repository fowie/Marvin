"""Consolidated offline checks for legacy tilt-servo planning and live mapping."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_protocol as protocol
from tools import marvin_legacy_front_servo_mapper as mapper
from tools import marvin_legacy_servo_plan as plan


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
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory, \
                self.assertRaisesRegex(ValueError, "acknowledgment"):
            mapper.run_diagnostic(
                Path(directory) / "evidence",
                expected_physical_port="1-2",
                run=True,
                **dict.fromkeys(mapper.ACKNOWLEDGMENTS, False),
            )

        class Transport:
            token = "token"
            serial_bytes = 0
            steps = mapper.STEPS

            def __init__(self):
                self.attempts = []
                self.writes = 0
                self.last_write_started = 0
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
                if step == "restore":
                    if self.restore_attempted:
                        raise OSError("restore retry")
                    self.restore_attempted = True
                return len(self.steps[step])

        def response(transport, report, step, **_):
            report["protocol_evidence"].append({
                "step": step, "raw_response_field": 0x82 if step == "restore" else 0x80})
            if step == "restore":
                transport.restore_correlated = True
            return Mock(response_field=0x82 if step == "restore" else 0x80)

        def interrupted_response(transport, report, step, **kwargs):
            if step == "set":
                raise KeyboardInterrupt()
            return response(transport, report, step, **kwargs)

        transport = Transport()
        with patch.object(mapper, "_response", side_effect=interrupted_response), \
                redirect_stderr(io.StringIO()), \
                self.assertRaises(KeyboardInterrupt):
            mapper._observe(transport, {}, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline", "set", "restore", "verify"])
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
                    if set_result == "error":
                        raise OSError("uncertain set write")
                    return 1
                return original_submit(step, deadline=deadline)

            faulted.submit = faulty_submit
            with self.subTest(set_result=set_result), \
                    patch.object(mapper, "_response", side_effect=response), \
                    redirect_stderr(io.StringIO()), self.assertRaises(OSError):
                mapper._observe(faulted, {}, clock=lambda: 0)
            self.assertEqual(faulted.attempts.count("restore"), 1)
            self.assertTrue(faulted.restore_attempted)
            faulted.close.assert_called_once()

        live = mapper._Transport.__new__(mapper._Transport)
        live.nonzero_may_have_applied = True
        live.restore_attempted = False
        live.completed = ["baseline", "set"]
        live.writes = 2
        live.fd = 99
        live.ingress = Mock()
        live._check = Mock()
        live.event = Mock(side_effect=OSError("journal fault"))
        with patch.object(mapper.os, "write", return_value=len(mapper.STEPS["restore"])) as write, \
                self.assertRaises(OSError):
            live._restore_once(deadline=1)
        write.assert_called_once_with(99, mapper.STEPS["restore"])
        self.assertTrue(live.restore_attempted)


if __name__ == "__main__":
    unittest.main()
