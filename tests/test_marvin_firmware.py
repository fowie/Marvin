import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools import marvin_firmware


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "flash-A.bin"
        self.second = self.root / "flash-B.bin"
        self.registers = self.root / "registers.json"
        self.image = bytearray(b"\xff" * marvin_firmware.FLASH_BYTES)
        struct.pack_into("<II", self.image, 0, 0x20018000, 0x101)
        self.first.write_bytes(self.image)
        self.second.write_bytes(self.image)
        self.registers.write_text(json.dumps({f"FMPRE{i}": "0xffffffff" for i in range(4)}))

    def test_valid_top_of_sram_stack_pointer_and_recorded_read_access(self):
        result = marvin_firmware.audit(self.first, self.second, registers=self.registers)
        self.assertEqual(result["status"], "passed_preliminary_screen")
        self.assertEqual(result["images"][0]["initial_stack_pointer"], "0x20018000")
        self.assertEqual(self.first.read_bytes(), self.image)
        self.assertEqual(self.second.read_bytes(), self.image)

    def test_without_protection_snapshot_never_claims_readable_backup(self):
        result = marvin_firmware.audit(self.first, self.second)
        self.assertEqual(result["status"], "protection_unverified")

    def test_same_file_or_hard_link_is_not_two_acquisitions(self):
        alias = self.root / "alias"
        alias.hardlink_to(self.first)
        for second in (self.first, alias):
            with self.subTest(second=second), self.assertRaisesRegex(ValueError, "distinct"):
                marvin_firmware.audit(self.first, second)

    def test_different_images_require_investigation(self):
        self.image[-1] = 0
        self.second.write_bytes(self.image)
        result = marvin_firmware.audit(self.first, self.second, registers=self.registers)
        self.assertFalse(result["matching_hashes"])
        self.assertEqual(result["status"], "needs_investigation")

    def test_matching_zeros_are_not_a_valid_backup(self):
        self.first.write_bytes(b"\x00" * marvin_firmware.FLASH_BYTES)
        self.second.write_bytes(self.first.read_bytes())
        result = marvin_firmware.audit(self.first, self.second, registers=self.registers)
        self.assertTrue(result["matching_hashes"])
        self.assertEqual(result["status"], "needs_investigation")
        self.assertTrue(any("all-zero" in problem for problem in result["images"][0]["problems"]))

    def test_length_and_vector_checks(self):
        for payload in (b"", b"\xff" * marvin_firmware.FLASH_BYTES, b"123"):
            with self.subTest(size=len(payload)):
                self.first.write_bytes(payload)
                self.assertTrue(marvin_firmware.inspect_image(self.first)["problems"])
        self.first.write_bytes(b"x" * (marvin_firmware.FLASH_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            marvin_firmware.inspect_image(self.first)
        for stack, reset in ((0x08000000, 0x101), (0x20018000, 0x100),
                             (0x20018000, 0x08000101), (0x20000003, 0x101)):
            with self.subTest(stack=stack, reset=reset):
                struct.pack_into("<II", self.image, 0, stack, reset)
                self.first.write_bytes(self.image)
                self.assertTrue(marvin_firmware.inspect_image(self.first)["problems"])

    def test_write_protection_is_not_a_substitute_for_read_protection(self):
        self.registers.write_text(json.dumps({f"FMPPE{i}": "0xffffffff" for i in range(4)}))
        with self.assertRaisesRegex(ValueError, "not a substitute"):
            marvin_firmware.audit(self.first, self.second, registers=self.registers)

    def test_protected_blocks_are_reported_at_2kib_granularity(self):
        values = {f"FMPRE{i}": 0xFFFFFFFF for i in range(4)}
        values["FMPRE2"] &= ~1
        self.registers.write_text(json.dumps(values))
        result = marvin_firmware.audit(self.first, self.second, registers=self.registers)
        self.assertEqual(result["status"], "needs_investigation")
        self.assertEqual(result["read_protection"]["protected_2kib_blocks"], [64])

    def test_invalid_snapshot_values_are_rejected(self):
        for value in (True, -1, 0x100000000, [], "not hex"):
            values = {f"FMPRE{i}": 0xFFFFFFFF for i in range(4)}
            values["FMPRE0"] = value
            self.registers.write_text(json.dumps(values))
            with self.subTest(value=value), self.assertRaises(ValueError):
                marvin_firmware.inspect_read_protection(self.registers)


if __name__ == "__main__":
    unittest.main()
