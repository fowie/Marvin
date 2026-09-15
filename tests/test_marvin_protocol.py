import io
import json
import struct
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools import marvin_protocol as protocol


def table_crc(data):
    table = []
    for value in range(256):
        remainder = 0
        for _ in range(8):
            remainder = (remainder >> 1) ^ 0xA001 if (remainder ^ value) & 1 else remainder >> 1
            value >>= 1
        table.append(remainder)
    result = 0
    for byte in data:
        result = (result >> 8) ^ table[(result ^ byte) & 0xFF]
    return result


def response(payload, command=4, status=0x80):
    body = b"\xef\xbe" + struct.pack("<HBBH", 0, command, status, len(payload)) + payload
    return body + struct.pack("<H", table_crc(body)) + b"\xad\xde"


class ProtocolTests(unittest.TestCase):
    def test_exact_request_and_crc_reference(self):
        self.assertEqual(protocol.get_config_request().hex(), "efbe0000040000001133adde")
        self.assertEqual(protocol.get_config_request(1).hex(), "efbe01000400000010e2adde")
        self.assertEqual(protocol.crc16(b"123456789"), 0xBB3D)
        for data in (b"", bytes(range(256)), bytes(range(255, -1, -1))):
            self.assertEqual(protocol.crc16(data), table_crc(data))
        for seq in (0, 1, 255, 256, 65535):
            packet = protocol.decode_packet(protocol.get_config_request(seq))
            self.assertEqual((packet.sequence, packet.command, packet.payload), (seq, 4, b""))
        for seq in (-1, 65536, 1.5, True):
            with self.assertRaises(ValueError):
                protocol.get_config_request(seq)

    def test_historical_log_crc_includes_header(self):
        # Historical firmware-write packet, used only as an offline decoder fixture.
        data = bytes.fromhex("efbe58040900400018bf00201d010000" + "2f010000" * 14 + "0b05adde")
        packet = protocol.decode_packet(data)
        self.assertEqual((packet.command, len(packet.payload)), (9, 64))
        self.assertEqual(protocol.crc16(data[:-4]), 0x050B)
        self.assertNotEqual(protocol.crc16(data[2:-4]), 0x050B)

    def test_stateful_alternative_has_distinct_opcode_and_sequence(self):
        self.assertEqual(protocol.get_unit_info_request().hex(), "efbe01001b0000001736adde")
        self.assertEqual(protocol.get_unit_info_request(0).hex(), "efbe00001b00000016e7adde")
        packet = protocol.decode_packet(protocol.get_unit_info_request())
        self.assertEqual((packet.sequence, packet.command, packet.payload), (1, 27, b""))
        with self.assertRaises(ValueError):
            protocol.get_unit_info_request(65536)
        for command in (1, 2, 3, 8, 9, 20, 21, 22, 23, 30, 38):
            with self.assertRaises(ValueError):
                protocol._identification_request(command, 0)

    def test_sensor_info_is_a_distinct_fixed_empty_handshake_query(self):
        for seq in (0, 2, 255, 256, 65535):
            with self.subTest(sequence=seq):
                body = b"\xef\xbe" + struct.pack("<HBBH", seq, 29, 0, 0)
                expected = body + struct.pack("<H", table_crc(body)) + b"\xad\xde"
                request = protocol.get_sensor_info_request(seq)
                self.assertEqual(request, expected)
                packet = protocol.decode_packet(request)
                self.assertEqual((packet.sequence, packet.command, packet.response_field, packet.payload),
                                 (seq, 29, 0, b""))
                with self.assertRaises(ValueError):
                    packet.unit_info()
        self.assertEqual(protocol.decode_packet(protocol.get_sensor_info_request()).sequence, 2)
        for seq in (-1, 65536, 1.5, True):
            with self.subTest(sequence=seq), self.assertRaises(ValueError):
                protocol.get_sensor_info_request(seq)

    def test_cli_generates_sensor_info_without_transport(self):
        output = io.StringIO()
        with patch("sys.argv", ["marvin_protocol", "generate", "--command", "29", "--sequence", "2"]), redirect_stdout(output):
            protocol.main()
        self.assertEqual(bytes.fromhex(output.getvalue()), protocol.get_sensor_info_request())

    def test_complete_config_and_shared_unit_info_prefix(self):
        payload = struct.pack("<III", 45949, 0x10300, 0x01020304) + bytes(range(96))
        data = response(payload)
        self.assertEqual(len(data), 120)
        packet = protocol.decode_packet(data)
        self.assertEqual(packet.response_code(), 0)
        self.assertEqual(packet.payload, payload)
        self.assertEqual(packet.unit_info(), {"fw_version": 45949, "comm_version": 66304, "serial_number": 16909060})
        self.assertEqual(packet.unit_info(), protocol.decode_packet(response(payload[:12], command=27)).unit_info())
        for size in range(len(data)):
            with self.assertRaises(ValueError):
                protocol.decode_packet(data[:size])
        for index in range(len(data)):
            changed = bytearray(data)
            changed[index] ^= 1
            with self.assertRaises(ValueError):
                protocol.decode_packet(bytes(changed))
        with self.assertRaises(ValueError):
            protocol.decode_packet(data + b"\x00")

    def test_embedded_delimiters_are_not_frame_boundaries(self):
        payload = bytes.fromhex("addeefbe") * 27
        self.assertEqual(protocol.decode_packet(response(payload)).payload, payload)

    def test_errors_and_unknown_layout_are_not_metadata(self):
        for status, payload, code in ((0x82, b"", 2), (0, struct.pack("<I", 0x10001), 0x10001)):
            packet = protocol.decode_packet(response(payload, status=status))
            self.assertEqual(packet.response_code(), code)
            with self.assertRaises(ValueError):
                packet.unit_info()
        for command, size in ((4, 12), (27, 108), (3, 108)):
            with self.assertRaises(ValueError):
                protocol.decode_packet(response(bytes(size), command=command)).unit_info()
        with self.assertRaises(ValueError):
            protocol.decode_packet(protocol.get_config_request()).response_code()

    def test_cli_preserves_unknown_and_complete_response_payloads(self):
        for size in (12, 108):
            payload = bytes(range(size))
            output = io.StringIO()
            argv = ["marvin_protocol", "inspect", "--response", response(payload).hex()]
            with patch("sys.argv", argv), redirect_stdout(output):
                protocol.main()
            result = json.loads(output.getvalue())
            self.assertEqual(result["payload_hex"], payload.hex())
            self.assertEqual(result["payload_bytes"], size)
            if size == 108:
                self.assertEqual(result["metadata_tail_hex"], payload[12:].hex())
                self.assertIn("unit_info", result)
            else:
                self.assertNotIn("unit_info", result)
                self.assertIn("Unknown metadata layout", result["warning"])


if __name__ == "__main__":
    unittest.main()
