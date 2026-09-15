import struct
import unittest

from tools.marvin_legacy_stream import LegacyStreamDecoder
from tests.test_marvin_legacy_protocol import OBSERVED_REPLY, OBSERVED_UNIT_INFO_REPLY, frame


class LegacyStreamTests(unittest.TestCase):
    def assert_partition(self, events, data, start=0):
        self.assertEqual(b"".join(event.raw for event in events), data)
        offset = start
        for event in events:
            self.assertEqual(event.offset, offset)
            self.assertGreater(len(event.raw), 0)
            self.assertEqual(event.to_dict()["end_offset"], event.end_offset)
            offset = event.end_offset
        self.assertEqual(offset, start + len(data))

    def test_observed_reply_at_every_fragment_boundary_and_bytewise(self):
        for split in range(len(OBSERVED_REPLY) + 1):
            decoder = LegacyStreamDecoder()
            events = decoder.feed(OBSERVED_REPLY[:split]) + decoder.feed(OBSERVED_REPLY[split:]) + decoder.finish()
            self.assertEqual([event.kind for event in events], ["frame"])
            self.assert_partition(events, OBSERVED_REPLY)
        decoder = LegacyStreamDecoder()
        events = []
        for byte in OBSERVED_REPLY:
            events += decoder.feed(bytes([byte]))
            self.assertLessEqual(decoder.buffered_bytes, decoder.max_buffer_bytes)
        self.assertEqual([e.kind for e in events + decoder.finish()], ["frame"])

    def test_all_truncations_are_explicit_and_finish_is_idempotent(self):
        for length in range(1, len(OBSERVED_REPLY)):
            decoder = LegacyStreamDecoder()
            events = decoder.feed(OBSERVED_REPLY[:length]) + decoder.finish()
            self.assertEqual([event.kind for event in events], ["partial"])
            self.assertGreater(events[0].needed_bytes, 0)
            self.assert_partition(events, OBSERVED_REPLY[:length])
            self.assertEqual(decoder.finish(), [])

    def test_both_observed_reply_fixtures_coalesced_across_reads(self):
        data = OBSERVED_REPLY + OBSERVED_UNIT_INFO_REPLY
        for split in range(len(data) + 1):
            decoder = LegacyStreamDecoder(start_offset=500)
            events = decoder.feed(data[:split]) + decoder.feed(data[split:]) + decoder.finish()
            self.assertEqual([e.kind for e in events], ["frame", "frame"])
            self.assertEqual([e.packet.command for e in events], [4, 0x1B])
            self.assertEqual([e.packet.sequence for e in events], [0, 1])
            self.assert_partition(events, data, start=500)

    def test_nested_complete_packets_and_markers_are_not_frame_boundaries(self):
        payload = b"SSSEEES" + OBSERVED_REPLY + frame(b"nested", command=250) + b"ESES"
        outer = frame(payload, command=255)
        decoder = LegacyStreamDecoder()
        self.assertEqual(decoder.feed(outer[:-3]), [])
        events = decoder.feed(outer[-3:]) + decoder.finish()
        self.assertEqual([e.kind for e in events], ["frame"])
        self.assertEqual(events[0].packet.payload, payload)
        self.assert_partition(events, outer)
        damaged = bytearray(outer)
        damaged[-3] ^= 1
        decoder = LegacyStreamDecoder()
        events = decoder.feed(damaged + OBSERVED_REPLY) + decoder.finish()
        self.assertEqual([e.kind for e in events], ["error", "frame"])
        self.assertEqual(events[0].raw, bytes(damaged))
        self.assertTrue(events[1].follows_corruption)
        self.assert_partition(events, damaged + OBSERVED_REPLY)

    def test_in_bound_damaged_length_never_recovers_nested_eof_suffix(self):
        data = b"S" + struct.pack("<HBBH", 0, 4, 0x80, 800) + b"prefix" + OBSERVED_REPLY
        decoder = LegacyStreamDecoder()
        self.assertEqual(decoder.feed(data), [])
        events = decoder.finish()
        self.assertEqual([e.code for e in events], ["incomplete_frame"])
        self.assert_partition(events, data)

    def test_out_of_bound_length_resynchronizes_explicitly_then_valid_frame(self):
        data = b"S" + struct.pack("<HBBH", 0, 4, 0x80, 65535) + OBSERVED_REPLY
        decoder = LegacyStreamDecoder()
        events = decoder.feed(data) + decoder.finish()
        self.assertEqual(events[0].code, "payload_limit")
        self.assertEqual(events[0].declared_payload_bytes, 65535)
        self.assertEqual([e.raw for e in events if e.kind == "frame"], [OBSERVED_REPLY])
        self.assertTrue(events[-1].follows_corruption)
        self.assert_partition(events, data)

    def test_all_byte_corruption_keeps_every_input_byte(self):
        for position in range(len(OBSERVED_REPLY)):
            with self.subTest(position=position):
                data = bytearray(OBSERVED_REPLY)
                data[position] ^= 1
                decoder = LegacyStreamDecoder()
                events = decoder.feed(data) + decoder.finish()
                self.assertTrue(any(e.kind != "frame" for e in events))
                self.assert_partition(events, data)
        for position in (-3, -2, -1):
            damaged = bytearray(OBSERVED_REPLY)
            damaged[position] ^= 1
            data = bytes(damaged) + OBSERVED_REPLY
            decoder = LegacyStreamDecoder()
            events = decoder.feed(data) + decoder.finish()
            self.assertEqual([e.kind for e in events], ["error", "frame"])
            self.assert_partition(events, data)

    def test_coalesced_unknown_status_and_request_echo_absolute_offsets(self):
        unknown = frame(b"unknown", command=254, status=0x7F, sequence=65535)
        echo = frame(status=0)
        data = b"noise" + OBSERVED_REPLY + unknown + echo + b"S\x00"
        decoder = LegacyStreamDecoder(start_offset=1200)
        events = decoder.feed(data) + decoder.finish()
        self.assertEqual([e.kind for e in events], ["noise", "frame", "frame", "frame", "partial"])
        self.assertEqual(events[2].packet.response_field, 0x7F)
        self.assertEqual(events[2].to_dict()["packet"]["payload_hex"], b"unknown".hex())
        self.assertEqual(events[3].packet.response_field, 0)
        self.assert_partition(events, data, start=1200)

    def test_bounded_retention_total_input_and_wire_maximum(self):
        data = b"x" * 10000 + frame(b"x" * 12, command=250)
        decoder = LegacyStreamDecoder(max_payload_bytes=12, max_input_bytes=len(data))
        events = decoder.feed(data) + decoder.finish()
        self.assertEqual(decoder.buffered_bytes, 0)
        self.assertEqual(decoder.input_bytes, len(data))
        self.assertLessEqual(max(len(e.raw) for e in events), 22)
        self.assert_partition(events, data)
        decoder = LegacyStreamDecoder(max_input_bytes=10)
        first = frame(status=0)
        events = decoder.feed(first[:5])
        with self.assertRaisesRegex(ValueError, "not consumed"):
            decoder.feed(first[5:] + b"x")
        self.assertEqual(decoder.input_bytes, 5)
        events += decoder.feed(first[5:]) + decoder.finish()
        self.assert_partition(events, first)
        data = frame(bytes(65535), command=255)
        decoder = LegacyStreamDecoder(max_payload_bytes=65535)
        events = decoder.feed(data) + decoder.finish()
        self.assertEqual([e.kind for e in events], ["frame"])
        self.assert_partition(events, data)

    def test_invalid_limits_types_and_finished_decoder(self):
        for options in (
            {"max_payload_bytes": -1}, {"max_payload_bytes": True}, {"max_payload_bytes": 65536},
            {"max_buffer_bytes": 10}, {"max_buffer_bytes": 65546}, {"max_buffer_bytes": True},
            {"max_input_bytes": -1}, {"max_input_bytes": True}, {"start_offset": -1}, {"start_offset": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                LegacyStreamDecoder(**options)
        decoder = LegacyStreamDecoder(max_input_bytes=0)
        self.assertEqual(decoder.feed(b""), [])
        self.assertEqual(decoder.finish(), [])
        with self.assertRaises(ValueError):
            decoder.feed(b"")
        with self.assertRaises(TypeError):
            LegacyStreamDecoder().feed("53")


if __name__ == "__main__":
    unittest.main()
