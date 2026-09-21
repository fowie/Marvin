from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest

from tools import marvin_legacy_protocol as protocol
from tools import marvin_protocol as modern


# Exact serial reply supplied from the sealed 2026-09-14 GetConfig capture.
OBSERVED_REPLY = bytes.fromhex(
    "53000004806c000000020100000201040302010100000000fffffff8ffffff00030000"
    "0100000064000000ac0d000054f2ffff0800000001000000b0040000c4090000c4090000"
    "50010000c8000000aa0a0000aa0a0000000200000800000080f0fa025000000020000000"
    "7805000050000000396745"
)
OBSERVED_UNIT_INFO_REPLY = bytes.fromhex("5301001b800c00000002010000020104030201a3cb45")


def independent_crc(data):
    remainder = 0
    for byte in data:
        remainder ^= int(f"{byte:08b}"[::-1], 2) << 8
        for _ in range(8):
            remainder = ((remainder << 1) ^ (0x8005 if remainder & 0x8000 else 0)) & 0xFFFF
    return int(f"{remainder:016b}"[::-1], 2)


def frame(payload=b"", *, sequence=0, command=4, status=0x80):
    body = b"S" + struct.pack("<HBBH", sequence, command, status, len(payload)) + payload
    return body + struct.pack("<H", independent_crc(body)) + b"E"


class LegacyProtocolTests(unittest.TestCase):
    def test_exact_observed_request_reply_and_raw_fields(self):
        self.assertEqual(independent_crc(b"123456789"), 0xBB3D)
        self.assertEqual(protocol.get_config_request().hex(), "53000004000000623545")
        self.assertEqual(len(OBSERVED_REPLY), 118)
        packet = protocol.decode_packet(OBSERVED_REPLY)
        self.assertIsInstance(packet, protocol.LegacyPacket)
        self.assertNotIsInstance(packet, modern.Packet)
        self.assertEqual((packet.sequence, packet.command, packet.response_field), (0, 4, 0x80))
        self.assertEqual(packet.declared_payload_bytes, 108)
        self.assertEqual(packet.crc16_value, 0x6739)
        self.assertEqual(independent_crc(OBSERVED_REPLY[:-3]), 0x6739)
        self.assertEqual(packet.payload, OBSERVED_REPLY[7:-3])
        self.assertEqual(packet.raw, OBSERVED_REPLY)
        self.assertIs(protocol.validate_get_config_reply(packet), packet)
        self.assertFalse(hasattr(packet, "unit_info"))
        self.assertFalse(hasattr(packet, "response_code"))
        self.assertEqual(packet.to_dict()["payload_hex"], OBSERVED_REPLY[7:-3].hex())
        self.assertEqual(packet.to_dict()["raw_hex"], OBSERVED_REPLY.hex())

    def test_sequences_and_getconfig_generation_remain_compatible(self):
        for sequence in (0, 1, 255, 256, 32768, 65535):
            with self.subTest(sequence=sequence):
                request = protocol.get_config_request(sequence)
                self.assertEqual(request, frame(sequence=sequence, status=0))
                parsed = protocol.decode_packet(request)
                self.assertEqual((parsed.sequence, parsed.command, parsed.response_field, parsed.payload),
                                 (sequence, 4, 0, b""))
                reply = protocol.decode_packet(frame(bytes(108), sequence=sequence))
                self.assertIs(protocol.validate_get_config_reply(reply, sequence), reply)
        for value in (-1, 65536, True, False, 1.0, "0", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.get_config_request(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.validate_get_config_reply(protocol.decode_packet(OBSERVED_REPLY), value)
        self.assertNotIn("get-sensor-info", protocol.GETTERS)

    def test_source_backed_empty_query_encoders_and_no_other_opcodes(self):
        encoders = {
            4: protocol.get_config_request, 0x1B: protocol.get_unit_info_request,
            0x0E: protocol.get_power_state_request, 0x0C: protocol.get_log_request,
            0: protocol.read_raw_data_request,
            0x0A: protocol.get_raw_motor_pwm_request,
            0x10: protocol.get_motor_velocity_request,
            0x17: protocol.get_led_state_request,
            0x19: protocol.get_led_blink_request,
            0x1D: protocol.get_servo_position_request,
            0x1F: protocol.get_sensor_info_request,
            0x28: protocol.get_battery_info_request,
        }
        observed = (
            (4, 0, "53000004000000623545"),
            (0x1B, 1, "5301001b000000643045"),
            (0x0E, 2, "5302000e000000600f45"),
            (0, 3, "53030000000000633645"),
            (0x0C, 3076, "53040c0c00000071d045"),
        )
        for command, sequence, expected in observed:
            self.assertEqual(encoders[command](sequence).hex(), expected)
        for command, encoder in encoders.items():
            for sequence in (0, 1, 255, 256, 32768, 65535):
                self.assertEqual(encoder(sequence), frame(command=command, sequence=sequence, status=0))
                self.assertEqual(len(encoder(sequence)), 10)
            self.assertEqual(encoder(), encoder(0))
            for sequence in (-1, 65536, True, False, 1.0, "0", None):
                with self.subTest(command=command, sequence=sequence), self.assertRaises(ValueError):
                    encoder(sequence)
        for command in range(256):
            if command not in encoders:
                with self.subTest(command=command), self.assertRaises(ValueError):
                    protocol._empty_request(command, 0)
        for command in (False, 0.0, "0"):
            with self.assertRaises(ValueError):
                protocol._empty_request(command, 0)

    def test_named_cli_queries_only_and_default_is_unchanged(self):
        for name, command in (
            ("get-config", 4), ("get-unit-info", 0x1B), ("get-power-state", 0x0E), ("read-raw-data", 0),
            ("get-log", 0x0C), ("get-servo-position", 0x1D),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(protocol.main(["generate", "--command", name, "--sequence", "65535"]), 0)
            self.assertEqual(bytes.fromhex(output.getvalue()), frame(command=command, sequence=65535, status=0))
        for bad in ("0", "27", "reset", "set-power-state", "get-sensor-info"):
            result = subprocess.run(
                [sys.executable, "-B", "-m", "tools.marvin_legacy_protocol", "generate", "--command", bad],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_second_observed_reply_stays_opaque_and_is_not_getconfig(self):
        packet = protocol.decode_packet(OBSERVED_UNIT_INFO_REPLY)
        self.assertEqual(len(packet.raw), 22)
        self.assertEqual((packet.sequence, packet.command, packet.response_field), (1, 0x1B, 0x80))
        self.assertEqual(packet.crc16_value, 0xCBA3)
        self.assertEqual(independent_crc(packet.raw[:-3]), 0xCBA3)
        self.assertEqual(packet.payload, protocol.decode_packet(OBSERVED_REPLY).payload[:12])
        self.assertFalse(hasattr(packet, "unit_info"))
        with self.assertRaisesRegex(ValueError, "command4"):
            protocol.validate_get_config_reply(packet, sequence=1)

    def test_strict_single_frame_length_footer_crc_and_each_bit_corruption(self):
        for position in range(len(OBSERVED_REPLY)):
            for bit in range(8):
                damaged = bytearray(OBSERVED_REPLY)
                damaged[position] ^= 1 << bit
                with self.subTest(position=position, bit=bit), self.assertRaises(ValueError):
                    protocol.decode_packet(damaged)
        for length in range(len(OBSERVED_REPLY)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                protocol.decode_packet(OBSERVED_REPLY[:length])
        for data in (OBSERVED_REPLY + b"\x00", OBSERVED_REPLY * 2, modern.get_config_request(), b"S" * 65546):
            with self.assertRaises(ValueError):
                protocol.decode_packet(data)
        with self.assertRaises(TypeError):
            protocol.decode_packet(OBSERVED_REPLY.hex())

    def test_unknown_commands_statuses_sizes_and_nested_markers_remain_opaque(self):
        payload = b"SESE" + OBSERVED_REPLY + b"ES"
        for command, status in ((0, 0), (3, 0x80), (27, 0x80), (29, 1), (250, 0xFF)):
            data = frame(payload, sequence=65535, command=command, status=status)
            packet = protocol.decode_packet(data)
            self.assertEqual((packet.command, packet.response_field, packet.payload), (command, status, payload))
        largest = frame(bytes(65535), command=255)
        self.assertEqual(protocol.decode_packet(largest).payload, bytes(65535))
        mutable = bytearray(OBSERVED_REPLY)
        packet = protocol.decode_packet(mutable)
        mutable[-1] = 0
        self.assertEqual(packet.raw, OBSERVED_REPLY)

    def test_reply_validation_rejects_echo_status_size_sequence_command_and_forged_fields(self):
        for data in (
            protocol.get_config_request(), frame(bytes(108), sequence=1),
            *(frame(bytes(108), status=status) for status in (0, 1, 0x7F, 0x81, 0xFF)),
            *(frame(bytes(size)) for size in (0, 4, 12, 36, 107, 109, 157)),
            *(frame(bytes(108), command=command) for command in (0, 3, 27, 29, 38, 46, 255)),
        ):
            with self.subTest(data=data[:7].hex()), self.assertRaises(ValueError):
                protocol.validate_get_config_reply(protocol.decode_packet(data))
        valid = protocol.decode_packet(OBSERVED_REPLY)
        with self.assertRaises(ValueError):
            protocol.validate_get_config_reply(replace(valid, sequence=1), 1)
        with self.assertRaises(ValueError):
            protocol.validate_get_config_reply(replace(valid, payload=bytes(108)))
        with self.assertRaises(TypeError):
            protocol.validate_get_config_reply(modern.decode_packet(modern.get_config_request()))

    def test_general_getter_validator_exact_shapes_and_strict_field_types(self):
        for query, spec in protocol.GETTERS.items():
            valid = protocol.decode_packet(frame(bytes(spec.payload_bytes), command=spec.command, sequence=1))
            self.assertIs(protocol.validate_getter_reply(valid, query, 1), valid)
            for bad in (
                replace(valid, sequence=True), replace(valid, command=float(spec.command)),
                replace(valid, response_field=128.0), replace(valid, raw=bytearray(valid.raw)),
                replace(valid, payload=bytearray(valid.payload)), replace(valid, raw=spec.encode(1)),
                protocol.decode_packet(spec.encode(1)),
                *(protocol.decode_packet(frame(bytes(size), command=spec.command, sequence=1))
                  for size in (spec.payload_bytes - 1, spec.payload_bytes + 1)),
            ):
                with self.subTest(query=query), self.assertRaises(ValueError):
                    protocol.validate_getter_reply(bad, query, 1)
        for query in ("get-sensor-info", "reset", 4, True, None):
            with self.assertRaises(ValueError):
                protocol.validate_getter_reply(valid, query, 1)

    def test_cli_generate_inspect_declarations_and_errors(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(protocol.main(["generate", "--sequence", "65535"]), 0)
        self.assertEqual(bytes.fromhex(output.getvalue()), frame(sequence=65535, status=0))
        for args in (
            ["inspect", OBSERVED_REPLY.hex()],
            ["inspect", OBSERVED_REPLY.hex(), "--direction", "outgoing", "--evidence", "synthetic"],
            ["inspect", OBSERVED_REPLY.hex(), "--direction", "received", "--evidence", "recorded",
             "--expect-config-sequence", "0"],
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(protocol.main(args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["packet"]["raw_hex"], OBSERVED_REPLY.hex())
            self.assertEqual(result["application_acknowledgment"], "not_established")
            if "--direction" not in args:
                self.assertEqual(result["direction"], "unknown")
                self.assertEqual(result["evidence_kind"], "unspecified")
        for args in (
            ["generate", "--sequence", "-1"], ["inspect", "gg"], ["inspect", "53"],
            ["inspect", OBSERVED_REPLY.hex(), "--expect-config-sequence", "0"],
            ["inspect", protocol.get_config_request().hex(), "--direction", "received", "--expect-config-sequence", "0"],
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(protocol.main(args), 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "input_error")
        for command in (
            [sys.executable, "-B", "-m", "tools.marvin_legacy_protocol", "generate"],
            [sys.executable, "-B", str(Path(protocol.__file__)), "generate"],
        ):
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(bytes.fromhex(result.stdout), protocol.get_config_request())
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
