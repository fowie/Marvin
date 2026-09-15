import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_protocol as protocol
from tools import marvin_stream as stream
from tools.marvin_stream import StreamDecoder


ROOT = Path(__file__).resolve().parents[1]


def frame(payload=b"", *, command=4, status=0x80, sequence=0):
    body = protocol.HEADER + struct.pack("<HBBH", sequence, command, status, len(payload)) + payload
    return body + struct.pack("<H", protocol.crc16(body)) + protocol.FOOTER


class RegularFileTests(unittest.TestCase):
    def test_regular_parents_allow_absolute_and_relative_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "nested" / "evidence.bin"
            path.parent.mkdir()
            path.write_bytes(b"offline evidence")
            for supplied in (path, Path(os.path.relpath(path))):
                with self.subTest(supplied=supplied):
                    self.assertEqual(stream.read_regular_file(supplied, max_bytes=16), b"offline evidence")
            self.assertEqual(path.read_bytes(), b"offline evidence")

    def test_parent_links_are_rejected_before_child_stat_for_all_path_spellings(self):
        original_lstat = Path.lstat
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            (real / "evidence.bin").write_bytes(b"offline evidence")
            for relative_target in (False, True):
                link = root / f"link-{relative_target}"
                link.symlink_to(real.name if relative_target else real, target_is_directory=True)

                def inspect(component):
                    self.assertFalse(component != link and component.is_relative_to(link))
                    return original_lstat(component)

                for supplied in (link / "evidence.bin", Path(os.path.relpath(link / "evidence.bin"))):
                    with self.subTest(target=relative_target, supplied=supplied), \
                            patch.object(Path, "lstat", autospec=True, side_effect=inspect), \
                            patch.object(stream.os, "open", side_effect=AssertionError("input opened")):
                        with self.assertRaisesRegex(ValueError, "symlink path components"):
                            stream.read_regular_file(supplied, max_bytes=32)

    def test_dotdot_cannot_normalize_away_a_supplied_symlink(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            (real / "evidence.bin").write_bytes(b"offline evidence")
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            supplied = link / ".." / "real" / "evidence.bin"
            with patch.object(stream.os, "open", side_effect=AssertionError("input opened")):
                with self.assertRaisesRegex(ValueError, "symlink path components"):
                    stream.read_regular_file(supplied, max_bytes=32)

    def test_kernel_target_parent_links_are_rejected_without_following_or_opening(self):
        original_lstat = Path.lstat
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for target in ("/dev", "/proc", "/sys"):
                for relative_target in (False, True):
                    link = root / f"{Path(target).name}-{relative_target}"
                    link.symlink_to(
                        os.path.relpath(target, root) if relative_target else target,
                        target_is_directory=True,
                    )

                    def inspect(component):
                        self.assertFalse(component != link and component.is_relative_to(link))
                        return original_lstat(component)

                    with self.subTest(target=target, relative=relative_target), \
                            patch.object(Path, "lstat", autospec=True, side_effect=inspect), \
                            patch.object(Path, "resolve", side_effect=AssertionError("link resolved")), \
                            patch.object(stream.os, "open", side_effect=AssertionError("input opened")):
                        with self.assertRaisesRegex(ValueError, "symlink path components"):
                            stream.read_regular_file(link / "never-read", max_bytes=32)

    def test_dotdot_cannot_escape_the_kernel_input_exclusion_before_component_checks(self):
        for supplied in ("/proc/../never-read", "/sys/../never-read", "/dev/../never-read"):
            with self.subTest(supplied=supplied), \
                    patch.object(Path, "lstat", side_effect=AssertionError("kernel path inspected")), \
                    patch.object(stream.os, "open", side_effect=AssertionError("input opened")):
                with self.assertRaisesRegex(ValueError, "Device and kernel-interface"):
                    stream.read_regular_file(supplied, max_bytes=32)

    def test_all_shared_reader_callers_reject_parent_links_before_open(self):
        from tools import marvin_firmware, marvin_legacy_replay, marvin_replay, marvin_telemetry, marvin_usbmon

        callers = (
            ("legacy input", lambda path: marvin_legacy_replay.read_capture_file(path, max_bytes=32)),
            ("modern input", marvin_replay.replay_capture),
            ("firmware image", marvin_firmware.inspect_image),
            ("register snapshot", marvin_firmware.inspect_read_protection),
            ("catalogue", marvin_telemetry.TelemetryDecoder),
            ("usbmon evidence", marvin_usbmon.analyze_file),
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            (real / "input").write_bytes(b"offline evidence")
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            for name, call in callers:
                with self.subTest(caller=name), patch.object(stream.os, "open") as opening:
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        call(link / "input")
                    opening.assert_not_called()

    def test_reader_explicitly_requests_non_inheritable_descriptor_and_closes_it(self):
        opening = os.open
        observed = []

        def record_open(path, flags):
            descriptor = opening(path, flags)
            observed.append((descriptor, flags, os.get_inheritable(descriptor)))
            return descriptor

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            path.write_bytes(b"offline evidence")
            with patch.object(stream.os, "open", side_effect=record_open):
                self.assertEqual(stream.read_regular_file(path, max_bytes=32), b"offline evidence")
            self.assertEqual(len(observed), 1)
            descriptor, flags, inheritable = observed[0]
            self.assertTrue(flags & os.O_CLOEXEC)
            self.assertFalse(inheritable)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            self.assertEqual(path.read_bytes(), b"offline evidence")


class StreamTests(unittest.TestCase):
    def assert_partition(self, events, data, start=0):
        self.assertEqual(b"".join(event.raw for event in events), data)
        offset = start
        for event in events:
            self.assertEqual(event.offset, offset)
            self.assertGreater(len(event.raw), 0)
            offset = event.end_offset
        self.assertEqual(offset, start + len(data))

    def test_config_at_every_fragment_boundary(self):
        data = frame(struct.pack("<III", 45949, 0x10300, 0x01020304) + bytes(range(96)))
        self.assertEqual(len(data), 120)
        for split in range(len(data) + 1):
            with self.subTest(split=split):
                decoder = StreamDecoder()
                events = decoder.feed(data[:split]) + decoder.feed(data[split:]) + decoder.finish()
                self.assertEqual([event.kind for event in events], ["frame"])
                self.assertEqual(events[0].packet, protocol.decode_packet(data))
                self.assert_partition(events, data)

    def test_bytewise_heartbeat_and_embedded_complete_frame(self):
        nested = frame(b"nested", command=250)
        payload = (nested + protocol.HEADER + protocol.FOOTER).ljust(157, b"\x00")
        data = frame(payload, command=1, sequence=65535)
        self.assertEqual(len(data), 169)
        decoder = StreamDecoder()
        events = []
        for byte in data:
            events.extend(decoder.feed(bytes([byte])))
            self.assertLessEqual(decoder.buffered_bytes, decoder.max_buffer_bytes)
        events += decoder.finish()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].packet.payload, payload)
        self.assert_partition(events, data)

    def test_coalesced_unknown_frames_and_absolute_offsets(self):
        first = frame(b"unfamiliar", command=250, status=0xFF, sequence=54321)
        second = frame(b"\x01", command=4)
        data = b"noise" + first + second
        decoder = StreamDecoder(start_offset=1000)
        events = decoder.feed(data) + decoder.finish()
        packets = [event for event in events if event.kind == "frame"]
        self.assertEqual([event.offset for event in packets], [1005, 1005 + len(first)])
        self.assertEqual(packets[0].packet.response_field, 0xFF)
        self.assertEqual(packets[1].packet.payload, b"\x01")
        self.assert_partition(events, data, start=1000)
        self.assertEqual(packets[0].to_dict()["packet"]["payload_hex"], b"unfamiliar".hex())

    def test_crc_footer_and_short_length_damage_then_valid(self):
        valid = frame(bytes(range(108)))
        for index in (-4, -1, 6):
            with self.subTest(index=index):
                damaged = bytearray(valid)
                damaged[index] ^= 1
                data = bytes(damaged) + valid
                decoder = StreamDecoder()
                events = decoder.feed(data) + decoder.finish()
                self.assertTrue(any(event.code == "invalid_frame" for event in events))
                self.assertEqual([event.raw for event in events if event.kind == "frame"], [valid])
                self.assertTrue(all(event.follows_corruption for event in events if event.kind == "frame"))
                self.assert_partition(events, data)

    def test_complete_invalid_outer_can_recover_nested_frames_only_as_hypotheses(self):
        nested = frame(b"nested", command=250)
        following = frame(b"following", command=251)
        outer = frame(b"prefix" + nested + b"suffix", command=252)
        for damage in (-4, -1):
            damaged = bytearray(outer)
            damaged[damage] ^= 1
            data = bytes(damaged) + following
            for recover_at_eof in (False, True):
                for split in range(len(data) + 1):
                    with self.subTest(damage=damage, recover_at_eof=recover_at_eof, split=split):
                        decoder = StreamDecoder(recover_at_eof=recover_at_eof)
                        events = decoder.feed(data[:split]) + decoder.feed(data[split:]) + decoder.finish()
                        self.assertEqual(events[0].code, "invalid_frame")
                        self.assertEqual(events[0].raw, outer[:1])
                        self.assertEqual(events[0].candidate_preview_hex, bytes(damaged[:64]).hex())
                        self.assertIn("Rejecting one header byte", events[0].message)
                        packets = [event for event in events if event.packet is not None]
                        self.assertEqual([event.raw for event in packets], [nested, following])
                        for event in packets:
                            self.assertTrue(event.follows_corruption)
                            self.assertTrue(event.to_dict()["follows_corruption"])
                            self.assertIn("resynchronization hypothesis", event.message)
                        self.assert_partition(events, data)

    def test_complete_length_damage_still_recovers_a_real_following_frame(self):
        damaged = bytearray(frame(b"damaged", command=250))
        following = frame(b"real following frame", command=251)
        struct.pack_into("<H", damaged, 6, len(damaged) - stream.FRAME_OVERHEAD + len(following))
        data = bytes(damaged) + following
        for recover_at_eof in (False, True):
            for split in range(len(data) + 1):
                with self.subTest(recover_at_eof=recover_at_eof, split=split):
                    decoder = StreamDecoder(recover_at_eof=recover_at_eof)
                    events = decoder.feed(data[:split]) + decoder.feed(data[split:]) + decoder.finish()
                    self.assertEqual(events[0].code, "invalid_frame")
                    packets = [event for event in events if event.packet is not None]
                    self.assertEqual([event.raw for event in packets], [following])
                    self.assertTrue(packets[0].follows_corruption)
                    self.assert_partition(events, data)

    def test_valid_and_incomplete_outer_candidates_keep_embedded_frames_opaque(self):
        nested = frame(b"nested", command=250)
        outer = frame(b"prefix" + nested + b"suffix", command=251)
        decoder = StreamDecoder(recover_at_eof=False)
        self.assertEqual(decoder.feed(outer[:-1]), [])
        events = decoder.feed(outer[-1:]) + decoder.finish()
        self.assertEqual([event.raw for event in events], [outer])
        self.assertFalse(events[0].follows_corruption)
        self.assertFalse(events[0].to_dict()["follows_corruption"])
        decoder = StreamDecoder(recover_at_eof=False)
        self.assertEqual(decoder.feed(outer[:-1]), [])
        events = decoder.finish()
        self.assertEqual([event.code for event in events], ["incomplete_frame"])
        self.assert_partition(events, outer[:-1])

    def test_giant_length_does_not_block_later_valid_frame(self):
        giant = protocol.HEADER + struct.pack("<HBBH", 0, 4, 0x80, 65535)
        valid = frame(b"next", command=201)
        decoder = StreamDecoder()
        events = decoder.feed(giant + valid)
        self.assertTrue(any(event.code == "payload_limit" for event in events))
        self.assertEqual([event.raw for event in events if event.kind == "frame"], [valid])
        self.assertTrue(events[-1].follows_corruption)
        self.assert_partition(events + decoder.finish(), giant + valid)

    def test_in_bound_bad_length_is_conservative_until_eof(self):
        unfinished = protocol.HEADER + struct.pack("<HBBH", 0, 4, 0x80, 800) + b"prefix"
        valid = frame(b"next", command=201)
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(unfinished + valid), [])
        events = decoder.finish()
        self.assertEqual([event.code for event in events], ["ambiguous_eof_resync", "validated_frame"])
        self.assertIn("could be embedded", events[0].message)
        self.assertTrue(events[1].follows_corruption)
        self.assert_partition(events, unfinished + valid)
        decoder = StreamDecoder(recover_at_eof=False)
        events = decoder.feed(unfinished + valid) + decoder.finish()
        self.assertEqual([event.code for event in events], ["incomplete_frame"])
        self.assert_partition(events, unfinished + valid)

    def test_every_config_truncation_is_an_explicit_partial(self):
        data = frame(bytes(range(108)))
        for length in range(1, len(data)):
            with self.subTest(length=length):
                decoder = StreamDecoder()
                events = decoder.feed(data[:length]) + decoder.finish()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].kind, "partial")
                self.assertGreater(events[0].needed_bytes, 0)
                self.assert_partition(events, data[:length])

    def test_split_header_after_noise_and_eof_header_byte(self):
        data = frame(b"")
        decoder = StreamDecoder()
        events = decoder.feed(b"noise" + data[:1])
        self.assertEqual(decoder.buffered_bytes, 1)
        events += decoder.feed(data[1:]) + decoder.finish()
        self.assert_partition(events, b"noise" + data)
        decoder = StreamDecoder()
        events = decoder.feed(b"noise\xef") + decoder.finish()
        self.assertEqual(events[-1].code, "incomplete_header")
        self.assertEqual(events[-1].raw, b"\xef")
        self.assert_partition(events, b"noise\xef")

    def test_large_feed_has_bounded_retention_and_no_byte_loss(self):
        data = b"x" * 100000 + frame(b"payload")
        decoder = StreamDecoder(max_payload_bytes=32)
        events = decoder.feed(data) + decoder.finish()
        self.assertEqual(decoder.buffered_bytes, 0)
        self.assertLessEqual(max(len(event.raw) for event in events), 44)
        self.assertEqual(events[-1].kind, "frame")
        self.assert_partition(events, data)

    def test_configurable_wire_max_retains_large_unknown_frame(self):
        data = frame(b"x" * 65535, command=250)
        decoder = StreamDecoder(max_payload_bytes=65535)
        events = decoder.feed(data) + decoder.finish()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].packet.payload, b"x" * 65535)

    def test_empty_finish_and_invalid_configuration(self):
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(b""), [])
        self.assertEqual(decoder.finish(), [])
        self.assertEqual(decoder.finish(), [])
        with self.assertRaises(ValueError):
            decoder.feed(b"x")
        for options in (
            {"max_payload_bytes": -1}, {"max_payload_bytes": True}, {"max_payload_bytes": 65536},
            {"max_buffer_bytes": 10}, {"max_buffer_bytes": 4107}, {"start_offset": -1},
            {"start_offset": True}, {"recover_at_eof": 1},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                StreamDecoder(**options)
        with self.assertRaises(TypeError):
            StreamDecoder().feed("not bytes")


if __name__ == "__main__":
    unittest.main()
