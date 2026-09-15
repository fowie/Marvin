from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest

from tools import marvin_legacy_protocol as protocol
from tools import marvin_legacy_telemetry as telemetry
from tests.test_marvin_legacy_protocol import OBSERVED_REPLY, OBSERVED_UNIT_INFO_REPLY, frame


OBSERVED_RAW_DATA_REPLY = bytes.fromhex(
    "530300008086001c82060034028f000f02ff03e9000e0229017a004f0334033e"
    "0283003b01b40000000000b601000061033e0343031302d401f3ff3200e6ffbf"
    "01e0011402eb050000000000000000000000000000000000000000fe0daa0ac4"
    "090000000000000000000000000000000000000000000000ff00000000000000"
    "000000000000000000002a0000d0e145"
)
OBSERVED_POWER_REPLY = bytes.fromhex("5302000e800200ff0ea85645")


def expected_raw_fields():
    fields = {
        "tick": (0, 4),
        "internalTemp": (30, 2), "externalTemp": (32, 2), "externalHumidity": (34, 2),
        "batteryVoltage": (36, 2), "batteryCurrent": (38, 2),
        "power5V": (40, 2), "power12V": (42, 2), "power9V": (44, 2), "power19V": (46, 2),
        "gyroReference": (48, 2), "accelX": (50, 2), "accelY": (52, 2), "accelZ": (54, 2),
        "gyroX": (56, 2), "gyroY": (58, 2), "gyroZ": (60, 2), "compass": (62, 2),
        "motorPositionL": (64, 4), "motorPositionR": (68, 4),
        "motorVelocityL": (72, 2), "motorVelocityR": (74, 2),
        "motorAccelerationL": (76, 2), "motorAccelerationR": (78, 2),
        "motorCurrentL": (80, 2), "motorCurrentR": (82, 2), "flags": (84, 2),
        "projectorPosition": (86, 2), "depthCam": (88, 2),
        "motorPwmLeftForward": (90, 2), "motorPwmLeftReverse": (92, 2),
        "motorPwmRightForward": (94, 2), "motorPwmRightReverse": (96, 2),
    }
    fields.update({f"proximity{i}": (2 + 2 * i, 2) for i in range(1, 9)})
    fields.update({f"cliff{i}": (18 + 2 * i, 2) for i in range(1, 6)})
    fields.update({f"led{i}Brightness": (98 + i, 1) for i in range(18)})
    fields.update({f"led{i}Blink": (116 + i, 1) for i in range(18)})
    return fields


class LegacyTelemetryTests(unittest.TestCase):
    def decode(self, data, **options):
        return telemetry.interpret_packet(protocol.decode_packet(data), direction="received", **options)

    def assert_raw(self, result, reason):
        self.assertEqual(result["status"], "raw")
        self.assertEqual(result["reason"], reason)
        self.assertNotIn("fields", result)
        self.assertNotIn("profile", result)
        self.assertEqual(result["application_acknowledgment"], "not_established")

    def test_exact_observed_134_byte_reply_and_all_field_spans(self):
        self.assertEqual(len(OBSERVED_RAW_DATA_REPLY), 144)
        self.assertEqual(hashlib.sha256(OBSERVED_RAW_DATA_REPLY).hexdigest(),
                         "c5c7118576aa781fef938976246b7834c708f7b205482c052939cab1e363fcc4")
        packet = protocol.decode_packet(OBSERVED_RAW_DATA_REPLY)
        self.assertEqual((packet.sequence, packet.command, packet.response_field, packet.crc16_value), (3, 0, 0x80, 0xE1D0))
        result = self.decode(OBSERVED_RAW_DATA_REPLY, evidence="recorded")
        self.assertEqual(result["status"], "decoded")
        self.assertEqual(result["profile"], "pctestapp-raw-data-134")
        self.assertEqual(result["packet"]["raw_hex"], OBSERVED_RAW_DATA_REPLY.hex())
        self.assertEqual(result["fields"]["tick"]["unsigned"], 0x0006821C)
        self.assertEqual(result["fields"]["flags"]["hex"], "0x0dfe")
        self.assertEqual(result["fields"]["projectorPosition"]["unsigned"], 0x0AAA)
        self.assertEqual(result["fields"]["depthCam"]["unsigned"], 0x09C4)
        self.assertIn("guard>=133", result["source"]["citation"])
        self.assertEqual(result["application_acknowledgment"], "not_established")
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        fields = result["fields"]
        self.assertEqual(len(fields), 82)
        self.assertEqual(set(fields), set(expected_raw_fields()))
        spans = []
        for name, (offset, size) in expected_raw_fields().items():
            self.assertEqual((fields[name]["offset"], fields[name]["size"]), (offset, size))
            self.assertEqual(fields[name]["raw_hex"], packet.payload[offset:offset + size].hex())
            self.assertTrue(fields[name]["source_label"])
            spans.extend(range(offset, offset + size))
        self.assertEqual(sorted(spans), list(range(134)))

    def test_every_field_offset_unsigned_signed_and_bit_preservation(self):
        layouts = expected_raw_fields()
        payloads = [bytes(range(134)), bytes([255]) * 134, bytes(134)]
        boundaries = bytearray(134)
        for offset, size in layouts.values():
            boundaries[offset:offset + size] = (1 << (size * 8 - 1)).to_bytes(size, "little")
        payloads.append(bytes(boundaries))
        for payload in payloads:
            result = self.decode(frame(payload, command=0), evidence="synthetic")
            self.assertEqual(result["evidence_kind"], "synthetic")
            for name, (offset, size) in layouts.items():
                field = result["fields"][name]
                unsigned = struct.unpack_from({1: "<B", 2: "<H", 4: "<I"}[size], payload, offset)[0]
                self.assertEqual(field["unsigned"], unsigned)
                self.assertEqual(int(field["hex"], 16), unsigned)
                self.assertEqual(unsigned.to_bytes(size, "little").hex(), field["raw_hex"])
                self.assertEqual(field["bits"], size * 8)
                if size > 1:
                    signed = struct.unpack_from({2: "<h", 4: "<i"}[size], payload, offset)[0]
                    self.assertEqual(field["signed"], signed)
                    self.assertEqual(signed & ((1 << (size * 8)) - 1), unsigned)
                else:
                    self.assertNotIn("signed", field)
                self.assertNotIn("scaled", field)
                self.assertNotIn("units", field)
        result = self.decode(frame(bytes(range(134)), command=0))
        self.assertEqual(result["fields"]["led17Blink"]["unsigned"], 133)

    def test_unit_info_is_three_reported_words_with_raw_and_hex_not_identity(self):
        result = self.decode(OBSERVED_UNIT_INFO_REPLY, evidence="recorded")
        self.assertEqual(result["profile"], "legacy-unit-info-12")
        fields = result["fields"]
        self.assertEqual(set(fields), {"reportedFwVersion", "reportedCommVersion", "reportedSerialNumber"})
        self.assertEqual(fields["reportedFwVersion"]["hex"], "0x01020000")
        self.assertEqual(fields["reportedCommVersion"]["hex"], "0x01020000")
        self.assertEqual(fields["reportedSerialNumber"]["hex"], "0x01020304")
        self.assertEqual([fields[name]["offset"] for name in fields], [0, 4, 8])
        self.assertIn("not attested", result["source"]["meaning"])
        extremes = self.decode(frame(b"\xff" * 12, command=0x1B))
        for value in extremes["fields"].values():
            self.assertEqual(value["unsigned"], 0xFFFFFFFF)
            self.assertEqual(value["signed"], -1)

    def test_power_state_is_raw_mask_only(self):
        self.assertEqual(hashlib.sha256(OBSERVED_POWER_REPLY).hexdigest(),
                         "184c1382d523aeee8e63598d15bb4aa04428a27a4e81d9ff165700a31c423233")
        result = self.decode(OBSERVED_POWER_REPLY, evidence="recorded")
        self.assertEqual(result["profile"], "legacy-power-state-2")
        self.assertEqual(set(result["fields"]), {"powerState"})
        self.assertEqual(result["fields"]["powerState"]["hex"], "0x0eff")
        self.assertEqual(result["fields"]["powerState"]["raw_hex"], "ff0e")
        self.assertNotIn("flags", result)
        extreme = self.decode(frame(b"\x00\x80", command=0x0E))
        self.assertEqual(extreme["fields"]["powerState"]["unsigned"], 32768)
        self.assertEqual(extreme["fields"]["powerState"]["signed"], -32768)

    def test_default_unknown_outgoing_echo_and_all_non80_statuses_remain_raw(self):
        packet = protocol.decode_packet(OBSERVED_RAW_DATA_REPLY)
        self.assert_raw(telemetry.interpret_packet(packet), "direction_not_received")
        self.assert_raw(telemetry.interpret_packet(packet, direction="outgoing"), "direction_not_received")
        for command, size in ((0, 134), (0x1B, 12), (0x0E, 2)):
            for status in range(256):
                if status != 0x80:
                    result = self.decode(frame(bytes(size), command=command, status=status))
                    self.assert_raw(result, "response_not_80_or_request_echo")
            self.assert_raw(self.decode(frame(command=command, status=0)), "response_not_80_or_request_echo")

    def test_unknown_sizes_commands_successor_shapes_and_config_stay_raw(self):
        for command, sizes in (
            (0, (0, 12, 36, 108, 133, 135, 157)),
            (0x1B, (0, 11, 13, 108)), (0x0E, (0, 1, 3, 134)),
        ):
            for size in sizes:
                data = frame(bytes(size), command=command)
                result = self.decode(data)
                self.assert_raw(result, "unknown_payload_size")
                self.assertEqual(result["packet"]["raw_hex"], data.hex())
        for command, size in ((1, 134), (3, 157), (3, 36), (29, 128), (250, 134)):
            self.assert_raw(self.decode(frame(bytes(size), command=command)), "unknown_command")
        self.assert_raw(self.decode(OBSERVED_REPLY), "config_left_opaque")

    def test_integrity_checked_even_for_received_dataclass_instances(self):
        packet = protocol.decode_packet(OBSERVED_RAW_DATA_REPLY)
        for index in (0, 3, 5, 7, -3, -1):
            raw = bytearray(packet.raw)
            raw[index] ^= 1
            forged = replace(packet, raw=bytes(raw))
            result = telemetry.interpret_packet(forged, direction="received")
            self.assert_raw(result, "invalid_frame_integrity")
            self.assertEqual(result["packet"]["raw_hex"], raw.hex())
        for forged in (replace(packet, command=0x1B), replace(packet, payload=bytes(134)), replace(packet, sequence=4)):
            self.assert_raw(telemetry.interpret_packet(forged, direction="received"), "packet_fields_disagree_with_raw")
        for value in (packet.raw, None):
            with self.assertRaises(TypeError):
                telemetry.interpret_packet(value, direction="received")
        with self.assertRaises(TypeError):
            telemetry.interpret_packet(replace(packet, raw=bytearray(packet.raw)), direction="received")
        for options in ({"direction": "response"}, {"evidence": "verified"}):
            with self.assertRaises(ValueError):
                telemetry.interpret_packet(packet, **options)

    def test_import_and_interpretation_are_offline_and_independent_results(self):
        packet = protocol.decode_packet(OBSERVED_RAW_DATA_REPLY)
        first = telemetry.interpret_packet(packet, direction="received")
        second = telemetry.interpret_packet(packet, direction="received")
        first["fields"]["flags"]["unsigned"] = 0
        first["limitations"].clear()
        self.assertEqual(telemetry.interpret_packet(packet, direction="received"), second)
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
before = set(sys.modules)
with patch('os.open', side_effect=AssertionError('open')), \\
     patch('builtins.open', side_effect=AssertionError('open')), \\
     patch.object(Path, 'open', side_effect=AssertionError('path open')):
    from tools import marvin_legacy_protocol as p, marvin_legacy_telemetry as t
    assert t.interpret_packet(p.decode_packet(p.read_raw_data_request()), direction='received')['status'] == 'raw'
new = set(sys.modules) - before
assert not {'tools.marvin_telemetry','tools.marvin_probe','tools.marvin_session','tools.marvin_usbmon'} & new
assert not any(n.split('.')[0] in {'serial','usb','socket','requests','RPi','gpiozero'} for n in new)
"""
        subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(telemetry.__file__).parent.parent,
                       capture_output=True, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
