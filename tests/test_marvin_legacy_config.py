import io
import json
import struct
import unittest
from contextlib import redirect_stdout
from dataclasses import replace

from tools import marvin_legacy_config as config
from tools import marvin_legacy_protocol as protocol
from tests.test_marvin_legacy_protocol import OBSERVED_REPLY, OBSERVED_UNIT_INFO_REPLY, frame


class LegacyConfigTests(unittest.TestCase):
    def test_real_reply_maps_every_word_and_preserves_bits(self):
        packet = protocol.decode_packet(OBSERVED_REPLY)
        result = config.interpret_packet(packet, direction="received", evidence="recorded")
        self.assertEqual(result["status"], "decoded")
        fields = result["fields"]
        self.assertEqual(len(fields), 27)
        self.assertEqual([f["offset"] for f in fields.values()], list(range(0, 108, 4)))
        self.assertEqual(b"".join(bytes.fromhex(f["raw_hex"]) for f in fields.values()), packet.payload)
        for index, field in enumerate(fields.values()):
            self.assertEqual(field["unsigned"], struct.unpack_from("<I", packet.payload, index * 4)[0])
            self.assertEqual(field["signed"], struct.unpack_from("<i", packet.payload, index * 4)[0])
        self.assertEqual([fields[k]["source_value"] for k in ("kp", "ki", "kd")], [-256, -8, 768])
        self.assertEqual(fields["sysClockFreq"]["source_value"], 50_000_000)
        self.assertEqual(fields["unitInfo.serialNumber"]["hex"], "0x01020304")
        self.assertEqual(
            [field["source_value"] for field in fields.values()],
            [16908288, 16908288, 16909060, 1, -256, -8, 768, 1, 100,
             3500, -3500, 8, 1, 1200, 2500, 2500, 336, 200, 2730, 2730,
             512, 8, 50000000, 80, 32, 1400, 80],
        )
        changed = {k for k, f in fields.items() if f["differs_from_newer_drive_default"] is True}
        self.assertEqual(changed, {"unitInfo.commVersion", "kp", "ki", "kd",
                                  "maxPwmDelta", "servoCamDefault", "servoCamMax"})
        self.assertEqual(result["application_acknowledgment"], "not_established")

    def test_direction_echo_unknown_status_size_and_command_stay_raw(self):
        packet = protocol.decode_packet(OBSERVED_REPLY)
        for direction in ("unknown", "outgoing"):
            result = config.interpret_packet(packet, direction=direction)
            self.assertEqual(result["status"], "raw")
            self.assertNotIn("fields", result)
        packets = (protocol.get_config_request(), OBSERVED_UNIT_INFO_REPLY,
                   frame(bytes(108), status=0x81), frame(bytes(104)), frame(bytes(112)))
        for raw in packets:
            result = config.interpret_packet(protocol.decode_packet(raw), direction="received")
            self.assertEqual(result["status"], "raw")
            self.assertEqual(result["packet"]["raw_hex"], raw.hex())
            self.assertNotIn("fields", result)

    def test_forged_fields_and_corruption_do_not_become_config(self):
        packet = protocol.decode_packet(OBSERVED_REPLY)
        for bad in (replace(packet, payload=bytes(108)),
                    replace(packet, raw=packet.raw[:-2] + b"\x00\x45"),
                    replace(packet, sequence=7)):
            result = config.interpret_packet(bad, direction="received")
            self.assertEqual(result["status"], "raw")
            self.assertNotIn("fields", result)

    def test_all_word_extremes_retain_signed_and_unsigned_views(self):
        payload = struct.pack("<III", 0, 0x7FFFFFFF, 0xFFFFFFFF) * 9
        result = config.interpret_packet(protocol.decode_packet(frame(payload)), direction="received")
        for i, field in enumerate(result["fields"].values()):
            self.assertEqual(field["unsigned"], (0, 2147483647, 4294967295)[i % 3])
            self.assertEqual(field["signed"], (0, 2147483647, -1)[i % 3])

    def test_invalid_api_declarations_raise(self):
        packet = protocol.decode_packet(OBSERVED_REPLY)
        with self.assertRaises(TypeError):
            config.interpret_packet(OBSERVED_REPLY)
        for kwargs in ({"direction": "response"}, {"evidence": "verified"}):
            with self.assertRaises(ValueError):
                config.interpret_packet(packet, **kwargs)

    def test_cli_requires_explicit_direction_and_handles_bad_input(self):
        for args, status, code in (
            ([OBSERVED_REPLY.hex()], "raw", 0),
            ([OBSERVED_REPLY.hex(), "--direction", "received", "--evidence", "recorded"], "decoded", 0),
            (["00"], "input_error", 2),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(config.main(args), code)
            self.assertEqual(json.loads(output.getvalue())["status"], status)


if __name__ == "__main__":
    unittest.main()
