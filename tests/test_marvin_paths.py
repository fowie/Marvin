"""Offline output-destination checks; every runtime boundary is mocked."""

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_boot_capture, marvin_campaign, marvin_legacy_probe
from tools import marvin_paths, marvin_probe, marvin_session, marvin_trials


ROOT = Path(__file__).resolve().parents[1]


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=".output-path-test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_missing_parents_are_explicit_opt_in_and_prevalidation_creates_nothing(self):
        output = self.root / "missing" / "nested" / "capture"
        with self.assertRaises(FileNotFoundError):
            marvin_paths.new_output_path(output)
        for supplied in (output, Path(os.path.relpath(output))):
            self.assertEqual(marvin_paths.new_output_path(supplied, allow_missing_parents=True), output)
        self.assertEqual(list(self.root.iterdir()), [])
        for value in (1, "true", None):
            with self.assertRaisesRegex(ValueError, "boolean"):
                marvin_paths.new_output_path(output, allow_missing_parents=value)
        with self.assertRaises(ValueError):
            marvin_paths.new_output_path(None)

    def test_existing_parent_directories_allow_both_input_spellings_without_writes(self):
        parent = self.root / "existing"
        parent.mkdir()
        output = parent / "new"
        for supplied in (output, Path(os.path.relpath(output))):
            for allow_missing in (False, True):
                self.assertEqual(marvin_paths.new_output_path(
                    supplied, allow_missing_parents=allow_missing), output)
        self.assertEqual(list(parent.iterdir()), [])

    def test_parent_links_are_rejected_before_child_metadata_or_resolution(self):
        original_lstat = Path.lstat
        targets = (self.root / "regular-parent", self.root / "missing-parent",
                   Path("/dev"), Path("/proc"), Path("/sys"))
        targets[0].mkdir()
        for index, target in enumerate(targets):
            for relative_target in (False, True):
                link = self.root / f"parent-{index}-{relative_target}"
                link.symlink_to(os.path.relpath(target, self.root) if relative_target else target,
                                target_is_directory=True)

                def inspect(path):
                    self.assertFalse(path != link and path.is_relative_to(link))
                    return original_lstat(path)

                output = link / "new" / "capture"
                for supplied in (output, Path(os.path.relpath(output)), link / ".." / "capture"):
                    for allow_missing in (False, True):
                        with self.subTest(target=target, relative=relative_target,
                                          supplied=supplied, allow_missing=allow_missing), \
                                patch.object(Path, "lstat", autospec=True, side_effect=inspect), \
                                patch.object(Path, "resolve", side_effect=AssertionError("link resolved")), \
                                patch.object(os, "open", side_effect=AssertionError("output opened")):
                            with self.assertRaisesRegex(ValueError, "symlinks"):
                                marvin_paths.new_output_path(supplied, allow_missing_parents=allow_missing)

    def test_missing_dotdot_prefix_cannot_hide_a_later_existing_link(self):
        link = self.root / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        output = self.root / "missing" / ".." / "linked" / "capture"
        with self.assertRaisesRegex(ValueError, "symlinks"):
            marvin_paths.new_output_path(output, allow_missing_parents=True)
        self.assertFalse((self.root / "missing").exists())
        self.assertFalse((self.root / "capture").exists())

    def test_kernel_components_are_rejected_before_their_metadata_is_inspected(self):
        original_lstat = Path.lstat

        def inspect(path):
            self.assertNotIn(path.parts[1] if len(path.parts) > 1 else "", ("dev", "proc", "sys"))
            return original_lstat(path)

        for supplied in ("/dev/new", "/proc/new", "/sys/new",
                         "/proc/../new", "/missing/../dev/new", "/missing/../proc/../new"):
            with self.subTest(supplied=supplied), \
                    patch.object(Path, "lstat", autospec=True, side_effect=inspect), \
                    patch.object(os, "open", side_effect=AssertionError("output opened")):
                with self.assertRaisesRegex(ValueError, "kernel-interface"):
                    marvin_paths.new_output_path(supplied, allow_missing_parents=True)

    def test_non_directory_parent_is_rejected_without_opening_it(self):
        for kind in ("file", "fifo"):
            parent = self.root / kind
            if kind == "file":
                parent.write_bytes(b"previous data")
            else:
                os.mkfifo(parent)
            with patch.object(os, "open", side_effect=AssertionError("parent opened")):
                for allow_missing in (False, True):
                    with self.assertRaisesRegex(ValueError, "directories"):
                        marvin_paths.new_output_path(parent / "capture", allow_missing_parents=allow_missing)

    def test_helper_import_is_pure_and_does_not_load_transport(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
with patch('os.open', side_effect=AssertionError('open')), \\
     patch.object(Path, 'lstat', side_effect=AssertionError('stat')), \\
     patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir')):
    from tools.marvin_paths import new_output_path
assert not {'serial', 'tools.marvin_probe', 'tools.marvin_session'} & set(sys.modules)
"""
        subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT,
                       capture_output=True, text=True, check=True)


class PreflightReached(RuntimeError):
    pass


class OutputEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=".output-entry-test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(os, "geteuid", return_value=1000))
        self.preflight = stack.enter_context(patch.object(
            marvin_session, "preflight", side_effect=AssertionError("unexpected preflight")))
        self.device = stack.enter_context(patch.object(
            marvin_probe, "check_device", side_effect=AssertionError("unexpected device identity")))
        self.ownership = stack.enter_context(patch.object(
            marvin_probe, "check_port_available", side_effect=AssertionError("unexpected ownership probe")))
        self.identity = stack.enter_context(patch.object(
            marvin_session, "check_identity", side_effect=AssertionError("unexpected identity probe")))
        self.serial = stack.enter_context(patch.object(
            marvin_probe.serial, "Serial", side_effect=AssertionError("unexpected serial")))
        self.process = stack.enter_context(patch.object(
            subprocess, "Popen", side_effect=AssertionError("unexpected recorder")))
        self.runtime = stack.enter_context(patch.object(
            marvin_legacy_probe, "_load_runtime", side_effect=AssertionError("unexpected legacy runtime")))
        plan = {"schema_version": 1, "segments": [{
            "id": "offline", "baudrate": 115200, "bytesize": 8, "parity": "N", "stopbits": 1,
            "dtr": False, "rts": False, "steps": [{
                "id": "query", "chunks_hex": ["0d"], "interval_seconds": 0, "response_seconds": 0.2,
                "classification": "synthetic", "rationale": "offline output guard",
            }],
        }]}
        self.entries = (
            ("session", lambda output: marvin_session.run_session(
                "offline", output, seconds=1, actuators_isolated=True)),
            ("boot", lambda output: marvin_boot_capture.run_boot_capture(
                "offline", output, actuators_isolated=True, allow_line_state_change=True)),
            ("campaign", lambda output: marvin_campaign.run_campaign(
                plan, output, actuators_isolated=True, allow_unknown_command=True,
                allow_telemetry_state_change=True, allow_line_state_trials=True, switch_position="RUN")),
            ("trials", lambda output: marvin_trials.run_trials(
                "offline", output, case_names=["high-high"], actuators_isolated=True,
                allow_unknown_command=True, allow_line_state_trials=True)),
            ("serial", lambda output: marvin_probe.capture(
                "offline", output, seconds=1, baudrate=115200, max_bytes=8, actuators_isolated=True)),
            ("legacy", lambda output: marvin_legacy_probe.run_probe(
                "get-config", output, expected_physical_port="1-2", actuators_isolated=True, sudo_usbmon=True)),
        )

    def assert_no_runtime(self):
        for boundary in (self.preflight, self.device, self.ownership, self.identity,
                         self.serial, self.process, self.runtime):
            boundary.assert_not_called()

    def test_existing_files_directories_and_leaf_links_fail_before_any_runtime(self):
        old_file = self.root / "file"
        old_file.write_bytes(b"previous capture")
        old_dir = self.root / "directory"
        old_dir.mkdir()
        existing_link = self.root / "existing-link"
        existing_link.symlink_to(old_dir, target_is_directory=True)
        dangling = self.root / "dangling"
        dangling.symlink_to("missing", target_is_directory=True)
        for output in (old_file, old_dir, existing_link, dangling):
            for name, call in self.entries:
                with self.subTest(entry=name, output=output), self.assertRaises(FileExistsError) as raised:
                    call(output)
                self.assertEqual(raised.exception.filename, str(output))
        self.assert_no_runtime()
        self.assertEqual(old_file.read_bytes(), b"previous capture")
        self.assertEqual(list(old_dir.iterdir()), [])
        self.assertEqual(dangling.readlink(), Path("missing"))
        self.assertFalse((self.root / "missing").exists())

    def test_ancestor_links_and_non_directories_fail_before_any_runtime(self):
        real = self.root / "real"
        real.mkdir()
        file_parent = self.root / "file"
        file_parent.write_bytes(b"unchanged")
        outputs = [file_parent / "capture"]
        for relative in (False, True):
            link = self.root / f"linked-{relative}"
            link.symlink_to(real.name if relative else real, target_is_directory=True)
            outputs.extend((link / "nested" / "capture", link / ".." / "capture"))
        for output in outputs:
            for name, call in self.entries:
                with self.subTest(entry=name, output=output), self.assertRaises(ValueError):
                    call(output)
        self.assert_no_runtime()
        self.assertEqual(list(real.iterdir()), [])
        self.assertFalse((self.root / "capture").exists())

    def test_kernel_outputs_fail_before_preflight_or_stat(self):
        for output in ("/dev/new", "/proc/new", "/sys/new"):
            for name, call in self.entries:
                with self.subTest(entry=name, output=output), \
                        patch.object(Path, "lstat", side_effect=AssertionError("kernel stat")), \
                        self.assertRaisesRegex(ValueError, "kernel-interface"):
                    call(output)
        self.assert_no_runtime()

    def test_modern_missing_parents_reach_preflight_without_creating_anything(self):
        self.preflight.side_effect = PreflightReached
        self.device.side_effect = PreflightReached
        for name, call in self.entries:
            output = self.root / name / "nested" / "capture"
            with self.subTest(entry=name), self.assertRaises(
                    FileNotFoundError if name == "legacy" else PreflightReached):
                call(output)
            self.assertFalse((self.root / name).exists())
        self.assertEqual(self.preflight.call_count, 4)
        self.device.assert_called_once()
        self.runtime.assert_not_called()
        self.ownership.assert_not_called()
        self.serial.assert_not_called()
        self.process.assert_not_called()

    def test_exclusive_mkdir_still_rejects_a_creation_race_after_prevalidation(self):
        self.ownership.side_effect = None
        for name, call in self.entries[:-1]:
            output = self.root / name

            def create_rival(_port):
                output.mkdir()
                (output / "winner").write_bytes(b"another capture")
                return {"usb": {"descriptors_sha256": marvin_campaign.DESCRIPTOR_HASH}}

            self.preflight.side_effect = create_rival
            self.device.side_effect = create_rival
            with self.subTest(entry=name), self.assertRaises(FileExistsError):
                call(output)
            self.assertEqual((output / "winner").read_bytes(), b"another capture")
            self.assertEqual([path.name for path in output.iterdir()], ["winner"])
        self.serial.assert_not_called()
        self.process.assert_not_called()
        self.runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
