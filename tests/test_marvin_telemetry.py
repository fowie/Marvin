import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_protocol as protocol
from tools import marvin_telemetry as telemetry


def packet(payload, command, status=0x80):
    body = protocol.HEADER + struct.pack("<HBBH", 0, command, status, len(payload)) + payload
    return protocol.decode_packet(body + struct.pack("<H", protocol.crc16(body)) + protocol.FOOTER)


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.decoder = telemetry.TelemetryDecoder()
        self.catalog = json.loads(telemetry.DEFAULT_CATALOG.read_text())

    def test_packaged_catalog_matches_reviewed_generated_facts(self):
        self.assertEqual(telemetry.DEFAULT_CATALOG,
                         Path(telemetry.__file__).parent.parent / "data/protocol-catalog.json")
        self.assertEqual(self.catalog["classification"], "generated")
        self.assertEqual(hashlib.sha256(telemetry.DEFAULT_CATALOG.read_bytes()).hexdigest(),
                         "0aa5d95b3ddc16d27ffcbdbf48e8d0f0d1a1a5d1e595596220a5b02ec17fd894")

    def test_every_packed_field_and_offset(self):
        codes = {"byte": "B", "ushort": "H", "short": "h", "uint": "I", "int": "i", "ulong": "Q", "float": "f"}
        for name, size in telemetry.REQUIRED_LAYOUTS.items():
            layout = self.catalog["structs"][name]
            data = bytearray(size)
            expected = {}
            offset = 0
            for index, field in enumerate(layout["fields"]):
                with self.subTest(layout=name, field=field["name"]):
                    value = index + 1
                    if field["type"] in ("short", "int"):
                        value = -value
                    elif field["type"] == "float":
                        value += 0.25
                    elif field["type"] == "ulong":
                        value = (1 << 60) + index
                    self.assertEqual(field["offset"], offset)
                    self.assertEqual(struct.calcsize("<" + codes[field["type"]]), field["size"])
                    struct.pack_into("<" + codes[field["type"]], data, field["offset"], value)
                    expected[field["name"]] = value
                    offset += field["size"]
            decoded = self.decoder.decode_payload(name, bytes(data))
            self.assertEqual(offset, size)
            self.assertEqual(decoded["fields"], expected)
            self.assertEqual(decoded["payload_hex"], bytes(data).hex())
            self.assertEqual(decoded["confidence"], "successor-layout-only")
            self.assertEqual(decoded["source_sha256"], layout["source_sha256"])

    def test_config_prefix_and_stateful_unit_info(self):
        identity = struct.pack("<III", 45949, 0x10300, 0x01020304)
        decoded = self.decoder.interpret_packet(packet(identity + bytes(range(96)), 4), direction="received")
        self.assertEqual(decoded["telemetry"]["fields"],
                         {"fwVersion": 45949, "commVersion": 0x10300, "serialNumber": 0x01020304})
        self.assertEqual(decoded["metadata_tail_hex"], bytes(range(96)).hex())
        unit = self.decoder.interpret_packet(packet(identity, 27), direction="received")
        self.assertEqual(unit["telemetry"]["fields"], decoded["telemetry"]["fields"])
        self.assertTrue(any("handshake" in warning for warning in unit["warnings"]))
        self.assertEqual(unit["application_acknowledgment"], "not_established")

    def test_heartbeat_types_are_chosen_only_by_exact_known_command_and_size(self):
        for size, name in ((157, "HeartbeatDataDrive"), (36, "HeartbeatDataHead")):
            result = self.decoder.interpret_packet(packet(bytes(size), 1), direction="received")
            self.assertEqual(result["telemetry"]["layout"], name)
        for command, size in ((1, 156), (1, 35), (250, 157), (4, 12), (27, 108), (29, 128)):
            with self.subTest(command=command, size=size):
                result = self.decoder.interpret_packet(packet(bytes(size), command), direction="received")
                self.assertNotIn("telemetry", result)
                self.assertEqual(result["payload_hex"], bytes(size).hex())
                self.assertTrue(result["warnings"])

    def test_unknown_status_extended_status_and_echo_are_not_decoded_as_telemetry(self):
        for command, status, payload in (
            (4, 0x82, b""), (1, 0xFF, bytes(157)), (4, 0, b""),
            (4, 0, struct.pack("<I", 0x12345678)), (27, 0, bytes(12)),
        ):
            result = self.decoder.interpret_packet(packet(payload, command, status), direction="received")
            self.assertNotIn("telemetry", result)
            self.assertEqual(result["application_acknowledgment"], "not_established")
            if status & 0x80:
                self.assertEqual(result["response_code"], status & 0x7F)
            elif len(payload) >= 4:
                self.assertIn("possible_extended_response_code", result)
                self.assertNotIn("response_code", result)

    def test_outgoing_and_unknown_direction_never_claim_response(self):
        for direction in ("outgoing", "unknown"):
            result = self.decoder.interpret_packet(packet(bytes(108), 4), direction=direction)
            self.assertNotIn("response_code", result)
            self.assertNotIn("telemetry", result)
        with self.assertRaises(ValueError):
            self.decoder.interpret_packet(packet(b"", 4), direction="USB-success")

    def test_nonfinite_floats_have_json_safe_values_and_original_bits(self):
        layout = self.catalog["structs"]["HeartbeatDataDrive"]
        fields = [field for field in layout["fields"] if field["type"] == "float"][:3]
        data = bytearray(157)
        for field, value in zip(fields, (math.nan, math.inf, -math.inf)):
            struct.pack_into("<f", data, field["offset"], value)
        result = self.decoder.decode_payload("HeartbeatDataDrive", bytes(data))
        self.assertEqual([row["value"] for row in result["nonfinite_fields"]], ["nan", "+infinity", "-infinity"])
        self.assertEqual(result["payload_hex"], data.hex())
        json.dumps(result, allow_nan=False)

    def test_bad_payload_sizes_and_unsupported_layout(self):
        for name, size in telemetry.REQUIRED_LAYOUTS.items():
            for wrong in (size - 1, size + 1):
                with self.assertRaises(ValueError):
                    self.decoder.decode_payload(name, bytes(wrong))
        with self.assertRaises(ValueError):
            self.decoder.decode_payload("InventedCalibration", b"")
        with self.assertRaises(TypeError):
            self.decoder.decode_payload("UnitInfo", bytearray(12))

    def test_schema_inconsistencies_are_not_silent_defaults(self):
        changes = (
            lambda c: c.update(byte_order="big-endian"),
            lambda c: c.update(packing=True),
            lambda c: c["structs"]["UnitInfo"].update(size=13),
            lambda c: c["structs"]["UnitInfo"].update(python_struct_format="@III"),
            lambda c: c["structs"]["UnitInfo"]["fields"][1].update(offset=5),
            lambda c: c["structs"]["UnitInfo"]["fields"][1].update(name="fwVersion"),
            lambda c: c["structs"]["UnitInfo"]["fields"][0].update(type="object"),
            lambda c: c["structs"]["UnitInfo"].update(source_sha256="not-a-hash"),
            lambda c: c["structs"].pop("HeartbeatDataDrive"),
        )
        for change in changes:
            catalog = copy.deepcopy(self.catalog)
            change(catalog)
            with patch.object(telemetry, "read_regular_file", return_value=json.dumps(catalog).encode()):
                with self.assertRaises(ValueError):
                    telemetry.TelemetryDecoder()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "huge.json"
            path.write_bytes(b" " * (telemetry.MAX_CATALOG_BYTES + 1))
            with self.assertRaises(ValueError):
                telemetry.TelemetryDecoder(path)

    def test_duplicate_catalogue_keys_reject_identical_and_conflicting_values(self):
        original = json.dumps(self.catalog)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.json"
            for key, value in (("packing", 1), ("size", 12), ("offset", 0)):
                token = f"{json.dumps(key)}: {json.dumps(value)}"
                self.assertIn(token, original)
                for earlier in (value, None):
                    duplicate = f"{json.dumps(key)}: {json.dumps(earlier)}, {token}"
                    payload = original.replace(token, duplicate, 1).encode()
                    with self.subTest(key=key, earlier=earlier):
                        path.write_bytes(payload)
                        with self.assertRaisesRegex(ValueError, f"Duplicate JSON key: {key}"):
                            telemetry.TelemetryDecoder(path)
                        self.assertEqual(path.read_bytes(), payload)

    def test_escaped_and_unused_nested_catalogue_duplicates_are_rejected(self):
        original = json.dumps(self.catalog)
        token = '"offset": 0'
        self.assertIn(token, original)
        cases = (
            original.replace(token, '"offset": 0, "off\\u0073et": 0', 1),
            original[:-1] + ', "extra": {"note": 1, "note": 2}}',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.json"
            for raw in cases:
                with self.subTest(raw_suffix=raw[-80:]):
                    payload = raw.encode()
                    path.write_bytes(payload)
                    with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                        telemetry.TelemetryDecoder(path)
                    self.assertEqual(path.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
