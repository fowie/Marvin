"""Consolidated offline checks for the legacy tilt-servo planner."""

import unittest

from tools import marvin_legacy_protocol as protocol
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


if __name__ == "__main__":
    unittest.main()
