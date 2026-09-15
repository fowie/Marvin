import struct
import unittest

from tools import marvin_legacy_protocol as legacy
from tools import marvin_protocol as modern
from tools import marvin_tx_policy as policy


def request(command, payload=b"", *, legacy_frame=False, response=0):
    header, footer = (b"S", b"E") if legacy_frame else (b"\xef\xbe", b"\xad\xde")
    body = header + struct.pack("<HBBH", 65535, command, response, len(payload)) + payload
    return body + struct.pack("<H", modern.crc16(body)) + footer


class TransmitPolicyTests(unittest.TestCase):
    def test_every_opcode_is_profile_specific_and_exact_payloads_are_required(self):
        for profile, commands in (
            ("modern", {4, 27, 29}),
            ("legacy", {0, 4, 14, 27}),
            ("experimental-successor", {3, 4, 27, 29, 38, 46}),
        ):
            for command in range(256):
                payload = b"\x00" if profile == "experimental-successor" and command == 46 else b""
                data = request(command, payload, legacy_frame=profile == "legacy")
                with self.subTest(profile=profile, command=command):
                    if command in commands:
                        stateful = policy.validate_transmit_stream(data, profile=profile)
                        self.assertEqual(stateful, command in ({27} if profile == "legacy" else {27, 29}))
                    else:
                        with self.assertRaises(ValueError):
                            policy.validate_transmit_stream(data, profile=profile)

    def test_crc_status_lengths_payloads_and_trailing_garbage_are_checked(self):
        for profile in policy.PROFILES:
            data = request(4, legacy_frame=profile == "legacy")
            for bad in (data[:-1], data + b"\x80", b"\x80" + data,
                        data[:-3] + bytes([data[-3] ^ 1]) + data[-2:],
                        request(4, b"\0", legacy_frame=profile == "legacy"),
                        request(4, legacy_frame=profile == "legacy", response=0x80)):
                with self.subTest(profile=profile, bad=bad), self.assertRaises(ValueError):
                    policy.validate_transmit_stream(bad, profile=profile)
            self.assertFalse(policy.validate_transmit_stream(data * 2, profile=profile))

    def test_calibration_selector_is_exactly_one_audited_byte(self):
        for selector in range(256):
            data = request(46, bytes([selector]))
            if selector in (0, 1):
                self.assertFalse(policy.validate_transmit_stream(data, profile="experimental-successor"))
            else:
                with self.assertRaises(ValueError):
                    policy.validate_transmit_stream(data, profile="experimental-successor")
        for payload in (b"", b"\0\0", b"\1\0", bytes(8)):
            with self.assertRaises(ValueError):
                policy.validate_transmit_stream(request(46, payload), profile="experimental-successor")

    def test_profiles_never_autodetect_or_mix_protocols(self):
        legacy_request = legacy.get_config_request()
        modern_request = modern.get_config_request()
        for profile, data in (("modern", legacy_request), ("experimental-successor", legacy_request),
                              ("legacy", modern_request), ("legacy", b"\r")):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                policy.validate_transmit_stream(data, profile=profile)
        for profile in policy.PROFILES:
            with self.assertRaises(ValueError):
                policy.validate_transmit_stream(legacy_request + modern_request, profile=profile)
        for profile in (None, [], "", "auto"):
            with self.assertRaises(ValueError):
                policy.validate_transmit_stream(modern_request, profile=profile)

    def test_text_requires_fixed_vocabulary_and_complete_terminators(self):
        for good in (b"\r", b"help\r\nVER\n", b"?\r", b"h\n"):
            self.assertFalse(policy.validate_transmit_stream(good, profile="experimental-successor"))
        for bad in (b"help", b"help\rERASE\r", b"\0help\r", b"\rhelp", b"\vhelp\r",
                    modern.get_config_request() + b"help\r", b"\x80"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                policy.validate_transmit_stream(bad, profile="experimental-successor")
        with self.assertRaises(ValueError):
            policy.validate_transmit_stream(b"help\r")


if __name__ == "__main__":
    unittest.main()
