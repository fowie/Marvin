import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
            self.assertEqual(protocol.main(), 0)
        self.assertEqual(bytes.fromhex(output.getvalue()), protocol.get_sensor_info_request())

    def test_cli_dispatch_input_errors_are_json(self):
        bad_crc = bytearray(protocol.get_config_request())
        bad_crc[-4] ^= 1
        cases = [
            (["inspect", "zz"], "non-hexadecimal"),
            (["inspect", "a"], "hexadecimal"),
            (["inspect", ""], "header"),
            (["inspect", bad_crc.hex()], "CRC"),
            (["inspect", protocol.get_config_request().hex(), "--response"], "four payload bytes"),
        ]
        for command in (4, 27, 29):
            for sequence in (-1, 65536):
                cases.append((["generate", "--command", str(command), "--sequence", str(sequence)], "uint16"))
        for args, message in cases:
            with self.subTest(args=args):
                output, errors = io.StringIO(), io.StringIO()
                with patch("sys.argv", ["marvin_protocol", *args]), redirect_stdout(output), redirect_stderr(errors):
                    status = protocol.main()
                self.assertEqual(status, 2)
                result = json.loads(output.getvalue())
                self.assertEqual(result["status"], "input_error")
                self.assertTrue(result["offline_only"])
                self.assertIn(message, result["error"])
                self.assertEqual(errors.getvalue(), "")

    def test_cli_dispatch_catches_type_error_but_not_unrelated_failures(self):
        args = ["marvin_protocol", "inspect", protocol.get_config_request().hex()]
        output = io.StringIO()
        with patch("sys.argv", args), redirect_stdout(output), \
                patch.object(protocol, "decode_packet", side_effect=TypeError("invalid byte input")):
            self.assertEqual(protocol.main(), 2)
        self.assertEqual(json.loads(output.getvalue()),
                         {"status": "input_error", "offline_only": True, "error": "invalid byte input"})
        with patch("sys.argv", args), patch.object(protocol, "decode_packet", side_effect=RuntimeError("unexpected")):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                protocol.main()

    def test_invalid_sequence_types_keep_argparse_and_api_validation(self):
        for value in ("false", "1.5", "not-an-integer"):
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(value=value), \
                    patch("sys.argv", ["marvin_protocol", "generate", "--sequence", value]), \
                    redirect_stdout(output), redirect_stderr(errors):
                with self.assertRaises(SystemExit) as raised:
                    protocol.main()
                self.assertEqual(raised.exception.code, 2)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("invalid int value", errors.getvalue())
            self.assertNotIn("Traceback", errors.getvalue())
        for encoder in (protocol.get_config_request, protocol.get_unit_info_request, protocol.get_sensor_info_request):
            for value in (True, False, 1.5, "1", None, -1, 65536):
                with self.subTest(encoder=encoder.__name__, value=value), self.assertRaisesRegex(ValueError, "uint16"):
                    encoder(value)

    def test_cli_valid_generation_keeps_default_sequence_and_hex_format(self):
        for command, encoder in ((4, protocol.get_config_request), (27, protocol.get_unit_info_request),
                                 (29, protocol.get_sensor_info_request)):
            for sequence in (None, 0, 65535):
                args = ["marvin_protocol", "generate", "--command", str(command)]
                if sequence is not None:
                    args += ["--sequence", str(sequence)]
                output = io.StringIO()
                with self.subTest(command=command, sequence=sequence), patch("sys.argv", args), redirect_stdout(output):
                    self.assertEqual(protocol.main(), 0)
                self.assertEqual(output.getvalue(), encoder(0 if sequence is None else sequence).hex(" ") + "\n")

    def test_module_and_direct_script_propagate_cli_exit_codes(self):
        for entry in (["-m", "tools.marvin_protocol"], [str(Path(protocol.__file__))]):
            with self.subTest(entry=entry):
                invalid = subprocess.run([sys.executable, "-B", *entry, "inspect", "zz"],
                                         capture_output=True, text=True, check=False)
                self.assertEqual(invalid.returncode, 2)
                self.assertEqual(json.loads(invalid.stdout)["status"], "input_error")
                self.assertEqual(invalid.stderr, "")
                valid = subprocess.run([sys.executable, "-B", *entry, "generate"],
                                       capture_output=True, text=True, check=True)
                self.assertEqual(valid.stdout, protocol.get_config_request().hex(" ") + "\n")
                self.assertEqual(valid.stderr, "")

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
                self.assertEqual(protocol.main(), 0)
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
