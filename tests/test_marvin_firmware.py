from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import marvin_firmware
from tools import marvin_stream


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
        snapshot = self.registers.read_bytes()
        result = marvin_firmware.audit(self.first, self.second, registers=self.registers)
        self.assertEqual(result["status"], "passed_preliminary_screen")
        self.assertEqual(result["images"][0]["initial_stack_pointer"], "0x20018000")
        self.assertEqual(self.first.read_bytes(), self.image)
        self.assertEqual(self.second.read_bytes(), self.image)
        self.assertEqual(self.registers.read_bytes(), snapshot)

    def test_stack_pointer_bounds_allow_empty_stack_at_one_past_sram_end(self):
        for stack, accepted in (
            (0x20000000, False), (0x20000004, True),
            (0x20017FFC, True), (0x20017FFF, False),
            (0x20018000, True), (0x20018004, False),
        ):
            with self.subTest(stack=hex(stack)):
                image = bytearray(self.image)
                struct.pack_into("<I", image, 0, stack)
                self.first.write_bytes(image)
                result = marvin_firmware.inspect_image(self.first)
                self.assertEqual(bool(result["problems"]), not accepted)
                if not accepted:
                    self.assertIn("Initial stack pointer", result["problems"][0])
                self.assertEqual(self.first.read_bytes(), image)

    def test_special_files_are_rejected_before_open(self):
        for inspect in (marvin_firmware.inspect_image, marvin_firmware.inspect_read_protection):
            for mode in (stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK):
                with self.subTest(inspect=inspect.__name__, mode=mode), \
                        patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_size=0)), \
                        patch.object(Path, "open", side_effect=AssertionError("unsafe Path.open")) as path_open, \
                        patch.object(marvin_stream.os, "open", side_effect=AssertionError("unsafe os.open")) as opening:
                    with self.assertRaisesRegex(ValueError, "regular file"):
                        inspect(self.first)
                    path_open.assert_not_called()
                    opening.assert_not_called()

    def test_symlinks_and_directories_are_rejected_before_open(self):
        link = self.root / "link"
        link.symlink_to(self.first)
        for inspect in (marvin_firmware.inspect_image, marvin_firmware.inspect_read_protection):
            for path in (link, self.root):
                with self.subTest(inspect=inspect.__name__, path=path), \
                        patch.object(Path, "open", side_effect=AssertionError("unsafe Path.open")) as path_open, \
                        patch.object(marvin_stream.os, "open", side_effect=AssertionError("unsafe os.open")) as opening:
                    with self.assertRaisesRegex(ValueError, "regular file"):
                        inspect(path)
                    path_open.assert_not_called()
                    opening.assert_not_called()

    def test_device_and_kernel_paths_are_rejected_before_stat_or_open(self):
        for inspect in (marvin_firmware.inspect_image, marvin_firmware.inspect_read_protection):
            for path in ("/dev/not-an-input", "/proc/not-an-input", "/sys/not-an-input",
                         "/unused/../dev/not-an-input"):
                with self.subTest(inspect=inspect.__name__, path=path), \
                        patch.object(Path, "lstat", side_effect=AssertionError("unsafe lstat")) as inspecting, \
                        patch.object(Path, "open", side_effect=AssertionError("unsafe Path.open")) as path_open, \
                        patch.object(marvin_stream.os, "open", side_effect=AssertionError("unsafe os.open")) as opening:
                    with self.assertRaisesRegex(ValueError, "Device and kernel-interface"):
                        inspect(path)
                    inspecting.assert_not_called()
                    path_open.assert_not_called()
                    opening.assert_not_called()

    def test_resolved_kernel_paths_are_rejected_before_open(self):
        for inspect in (marvin_firmware.inspect_image, marvin_firmware.inspect_read_protection):
            for path in ("/dev/not-an-input", "/proc/not-an-input", "/sys/not-an-input"):
                with self.subTest(inspect=inspect.__name__, path=path), \
                        patch.object(Path, "resolve", return_value=Path(path)), \
                        patch.object(Path, "open", side_effect=AssertionError("unsafe Path.open")) as path_open, \
                        patch.object(marvin_stream.os, "open", side_effect=AssertionError("unsafe os.open")) as opening:
                    with self.assertRaisesRegex(ValueError, "resolves to a device or kernel-interface"):
                        inspect(self.registers)
                    path_open.assert_not_called()
                    opening.assert_not_called()

    def test_image_and_register_size_limits_reject_before_open_without_truncating(self):
        for inspect, path, limit in (
            (marvin_firmware.inspect_image, self.first, 262144),
            (marvin_firmware.inspect_read_protection, self.registers, 65536),
        ):
            payload = path.read_bytes().ljust(limit, b" ")
            path.write_bytes(payload)
            with self.subTest(inspect=inspect.__name__, size=limit):
                inspect(path)
                self.assertEqual(path.read_bytes(), payload)
            oversized = payload + b" "
            path.write_bytes(oversized)
            with self.subTest(inspect=inspect.__name__, size=limit + 1), \
                    patch.object(Path, "open", side_effect=AssertionError("oversized Path.open")) as path_open, \
                    patch.object(marvin_stream.os, "open", side_effect=AssertionError("oversized os.open")) as opening:
                with self.assertRaisesRegex(ValueError, f"exceeds the {limit}-byte limit"):
                    inspect(path)
                path_open.assert_not_called()
                opening.assert_not_called()
            self.assertEqual(path.read_bytes(), oversized)

    def test_missing_inputs_surface_file_errors(self):
        for inspect in (marvin_firmware.inspect_image, marvin_firmware.inspect_read_protection):
            with self.subTest(inspect=inspect.__name__), self.assertRaises(FileNotFoundError):
                inspect(self.root / "missing")

    def test_cli_reports_regular_file_rejection_and_keeps_direct_script_support(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["marvin_firmware", str(self.first), str(self.second),
                                      "--registers", str(self.root)]), redirect_stderr(output):
            status = marvin_firmware.main()
        self.assertEqual(status, 1)
        self.assertIn("Firmware screening failed: Input must be a regular file", output.getvalue())
        completed = subprocess.run(
            [sys.executable, "-B", str(Path(marvin_firmware.__file__)), str(self.first),
             str(self.second), "--registers", str(self.registers)],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(completed.stdout)["status"], "passed_preliminary_screen")
        self.assertEqual(completed.stderr, "")

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

    def test_duplicate_snapshot_registers_reject_api_and_cli_without_mutation(self):
        values = {f"FMPRE{i}": "0xffffffff" for i in range(4)}
        report = self.root / "report.json"
        for key in values:
            for earlier in (0, values[key]):
                payload = "{" + json.dumps(key) + ":" + json.dumps(earlier) + "," + json.dumps(values)[1:]
                self.registers.write_text(payload)
                before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                          for path in self.root.iterdir()}
                with self.subTest(key=key, earlier=earlier):
                    with self.assertRaisesRegex(ValueError, f"Duplicate JSON key: {key}"):
                        marvin_firmware.inspect_read_protection(self.registers)
                    output, errors = io.StringIO(), io.StringIO()
                    argv = ["marvin_firmware", str(self.first), str(self.second),
                            "--registers", str(self.registers), "--output", str(report)]
                    with patch.object(sys, "argv", argv), redirect_stdout(output), redirect_stderr(errors):
                        status = marvin_firmware.main()
                    self.assertEqual(status, 1)
                    self.assertEqual(output.getvalue(), "")
                    self.assertIn(f"Firmware screening failed: Duplicate JSON key: {key}", errors.getvalue())
                    self.assertFalse(report.exists())
                    self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                                              for path in self.root.iterdir()})

    def test_duplicate_unknown_nested_and_escaped_keys_are_rejected(self):
        valid = json.dumps({f"FMPRE{i}": "0xffffffff" for i in range(4)})
        for payload, key in (
            (valid[:-1] + ',"note":1,"note":1}', "note"),
            (valid[:-1] + ',"metadata":{"source":"first","source":"second"}}', "source"),
            (r'{"FMPRE\u0030":0,' + valid[1:], "FMPRE0"),
        ):
            self.registers.write_text(payload)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, f"Duplicate JSON key: {key}"):
                marvin_firmware.inspect_read_protection(self.registers)
            self.assertEqual(self.registers.read_text(), payload)


if __name__ == "__main__":
    unittest.main()
