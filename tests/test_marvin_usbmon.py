import hashlib
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import marvin_usbmon as usbmon


ROOT = Path(__file__).resolve().parents[1]
USB_PATH = "/sys/bus/usb/devices/1-3.3"
CANONICAL = Path("/sys/devices/pci0000:00/usb1/1-3/1-3.3")
DESCRIPTORS = b"\x12\x01\x00\x02\x02\x00\x00\x40\x5e\x04\x44\x44\x00\x01\x01\x02\x03\x01"
IDENTITY = {
    "usb_path": str(CANONICAL), "physical_port": "1-3.3",
    "idVendor": "045e", "idProduct": "4444", "busnum": 1, "devnum": 8,
    "sysfs_device": 12, "sysfs_inode": 345,
    "descriptors_sha256": hashlib.sha256(DESCRIPTORS).hexdigest(),
    "descriptors_bytes": len(DESCRIPTORS),
}
OUT = b"ffff0001 123456 S Bo:1:008:3 -115 1 = 0d\n"
DONE = b"ffff0001 123457 C Bo:1:008:3 0 1 >\n"
KEYBOARD = b"ffff0002 123458 C Ii:1:009:1 0:8 8 = 00000400 00000000\n"
PENDING_IN = b"ffff0003 123459 S Bi:1:008:2 -115 64 <\n"


class ParserTests(unittest.TestCase):
    def test_capture_completeness_rejects_nonzero_or_malformed_gap_counters(self):
        fields = ("unretained_partial_line_bytes", "unprocessed_records",
                  "unprocessed_record_bytes", "unaccounted_retained_bytes")
        usbmon.validate_capture_completeness({})
        usbmon.validate_capture_completeness(dict.fromkeys(fields, 0))
        for field in fields:
            for value in (1, -1, True, False, "0", None, 0.0):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(usbmon.UsbmonError, field):
                    usbmon.validate_capture_completeness({field: value})

    def test_bulk_submission_completion(self):
        submitted = usbmon.parse_record(OUT)
        completed = usbmon.parse_record(DONE)
        self.assertEqual(submitted.payload, b"\r")
        self.assertEqual(submitted.status, -115)
        self.assertEqual(completed.status, 0)
        self.assertEqual(submitted.key, completed.key)
        self.assertEqual(submitted.endpoint_key, "Bo:1:008:3")

    def test_control_setup_and_completion(self):
        raw = b"001a 100 S Co:1:008:0 s 21 20 0000 0000 0007 7 = 00c20100 000008\n"
        record = usbmon.parse_record(raw)
        self.assertEqual(record.setup, (0x21, 0x20, 0, 0, 7))
        self.assertIsNone(record.status)
        self.assertEqual(record.payload, bytes.fromhex("00c20100000008"))
        complete = usbmon.parse_record(b"1a 101 C Co:1:008:0 0 7\n")
        self.assertEqual(record.key, complete.key)
        self.assertEqual(usbmon.parse_record(
            b"a 102 S Ci:1:008:0 s 80 06 0100 0000 0012 18 <\n"
        ).setup, (0x80, 6, 0x100, 0, 18))

    def test_interrupt_status_details_both_directions(self):
        for direction in ("i", "o"):
            with self.subTest(direction=direction):
                record = usbmon.parse_record(
                    f"a 100 C I{direction}:1:008:1 -2:16:-1 0\n".encode()
                )
                self.assertEqual(record.status, -2)
                self.assertEqual(record.status_details, (16, -1))

    def test_payload_32_byte_truncation_is_not_complete_payload(self):
        record = usbmon.parse_record(
            b"a 1 C Bi:1:008:2 0 64 = " + b"00112233 " * 8 + b"\n"
        )
        analyzer = usbmon.Analyzer()
        analyzer.add(record)
        summary = analyzer.summary()
        self.assertEqual(summary["payload"]["captured_bytes"], 32)
        self.assertEqual(summary["payload"]["uncaptured_bytes"], 32)
        self.assertEqual(summary["payload"]["text_32_byte_truncation_records"], 1)

    def test_invalid_records_never_echo_contents(self):
        malformed = (
            b"secret-keyboard",
            b"secret-keyboard 1 C Bi:1:008:2 0 8 = 00000000\n",
            b"a 1 WHAT Bi:1:008:2 0 0\n",
            b"a 1 C Bi:1:008:2 -2:secret-keyboard 0\n",
            b"a 1 C Bi:1:008:2 0 -3\n",
            b"a 1 C Bi:1:008:2 0 1 = ff00\n",
            b"a 1 C Bi:1:008:2 0 1 = xyz\n",
            b"a 1 C Bi:1:008:2 0 1 = f\n",
            b"a 1 C Bi:1:008:2 0 1 =\n",
            b"a 1 C Bi:1:008:2 0 1 ! secret-keyboard\n",
            b"a 1 C Bi:1:008:2 0 1 > secret-keyboard\n",
            b"a 1 C Bi:1:008:2 0 1 = \xff\n",
            b"a 1 C Bi:1:008:99 0 0\n",
            b"a 1 C Bi:1:999:2 0 0\n",
            b"a 1 C Bi:0:008:2 0 0\n",
            b"a 1 C Bi:1:008:2 9999999999 0\n",
            b"a 1 C Bi:1:008:2 0 9999999999\n",
            b"a 1 C Co:1:008:0 s 21 22 0000 0000 0000 0\n",
            b"a 1 S Bo:1:008:3 s 21 22 0000 0000 0000 0\n",
            b"a 1 S Co:1:008:0 s 21 22 00zz 0000 0000 0\n",
            b"a 1 S Co:1:008:0 s 21 22\n",
            b"a 1 C Zi:1:008:2 0:1:1 0 0\n",
            b"a 1 C Bi:1:008:2 0 64 = " + b"00000000 " * 9 + b"\n",
        )
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaises(usbmon.ParseError) as raised:
                usbmon.parse_record(raw)
            self.assertNotIn("secret-keyboard", str(raised.exception))

    def test_pairing_pending_cancel_and_zero_length_not_ack(self):
        analyzer = usbmon.Analyzer()
        for raw in (OUT, DONE, PENDING_IN,
                    b"4 123460 S Ii:1:008:1 -115:16 16 <\n",
                    b"4 123461 C Ii:1:008:1 -2:16 0\n",
                    b"5 123462 S Bi:1:008:2 -115 64 <\n",
                    b"5 123463 C Bi:1:008:2 0 0\n",
                    b"6 123464 C Bi:1:008:2 -108 0\n",
                    b"7 123465 C Bi:1:008:2 -71 0\n"):
            analyzer.add(usbmon.parse_record(raw))
        summary = analyzer.summary()
        self.assertEqual(summary["pairing"]["matched_completions"], 3)
        self.assertEqual(summary["pairing"]["pending_in_retained"], 1)
        self.assertEqual(summary["pairing"]["unmatched_completions"], 2)
        self.assertEqual(summary["submission_status"], {"in_progress": 4, "other_negative": 0})
        self.assertEqual(summary["completion_status"],
                         {"success": 2, "cancellation_or_shutdown": 2, "other_nonzero": 1})
        self.assertEqual(summary["payload"]["zero_length_in_completions"], 4)
        endpoint = summary["endpoints"]["Bo:1:008:3"]
        self.assertEqual(endpoint["submitted_requested_bytes"], 1)
        self.assertEqual(endpoint["completed_reported_bytes"], 1)
        self.assertEqual(endpoint["matched_requested_bytes"], 1)
        self.assertEqual(endpoint["matched_completed_bytes"], 1)
        self.assertEqual(endpoint["matched_completion_statuses"], {"0": 1})
        self.assertEqual(endpoint["statuses"], {"S": {"-115": 1}, "C": {"0": 1}, "E": {}})
        self.assertNotIn("application_ack", summary)
        self.assertTrue(any("not a timeout" in item for item in summary["limitations"]))

    def test_pairing_anomalies_and_bounded_pending(self):
        analyzer = usbmon.Analyzer(max_pending=2)
        for raw in (b"a 1 S Bo:1:008:3 -115 1 = 01\n",
                    b"a 2 S Bo:1:008:3 -115 1 = 02\n",
                    b"a 3 C Bi:1:008:2 0 0\n",
                    b"a 4 C Bo:1:008:3 0 2 >\n",
                    b"b 5 S Bi:1:008:2 -115 64 <\n",
                    b"c 6 S Bi:1:008:2 -115 64 <\n",
                    b"d 7 S Bi:1:008:2 -115 64 <\n",
                    b"b 8 C Bi:1:008:2 -104 0\n",
                    b"c 9 E Bi:1:008:2 -12 0\n",
                    b"e 10 E Bi:1:008:2 -12 0\n"):
            analyzer.add(usbmon.parse_record(raw))
        pairs = analyzer.summary()["pairing"]
        self.assertEqual(pairs["duplicate_submission_ids"], 1)
        self.assertEqual(pairs["endpoint_mismatches"], 1)
        self.assertEqual(pairs["completion_exceeds_requested"], 1)
        self.assertEqual(pairs["evicted_pending_submissions"], 1)
        self.assertEqual(pairs["unmatched_completions"], 2)
        self.assertEqual(pairs["matched_submission_errors"], 1)
        self.assertEqual(pairs["unmatched_submission_errors"], 1)
        self.assertEqual(pairs["pending_in_retained"], 1)

    def test_long_trace_bookkeeping_is_bounded(self):
        analyzer = usbmon.Analyzer(max_pending=3)
        for number in range(1, 1000):
            analyzer.add(usbmon.parse_record(
                f"{number:x} 1 S Bi:{number}:008:2 -115 64 <\n".encode()
            ))
            analyzer.add(usbmon.parse_record(
                f"{number:x} 2 C Ii:1:008:1 -{number} 0\n".encode()
            ))
        self.assertEqual(len(analyzer.pending), 3)
        self.assertLessEqual(len(analyzer.endpoints), 129)
        self.assertLessEqual(len(analyzer.endpoints["Ii:1:008:1"]["statuses"]["C"]), 129)
        self.assertGreater(analyzer.endpoint_overflow_records, 0)

    def test_framer_bounds_and_discard_of_other_device(self):
        framer = usbmon._Framer(64, (1, 8))
        prefix = b"a 1 C Ii:1:009:1 0 100 = "
        self.assertEqual(list(framer.feed(prefix + b"ab" * 100)), [])
        for _ in range(100):
            self.assertEqual(list(framer.feed(b"secret-keyboard" * 10)), [])
            self.assertLessEqual(len(framer.partial), 64)
        self.assertEqual(list(framer.feed(b"\n" + OUT[:10])), [])
        self.assertEqual(list(framer.feed(OUT[10:])), [OUT])
        self.assertEqual(framer.ignored_overlong, 1)
        for prefix in (b"a 1 C Bi:1:008:2 0 100 = ", b"unknown-header "):
            with self.subTest(prefix=prefix), self.assertRaises(usbmon.ParseError):
                list(usbmon._Framer(64, (1, 8)).feed(prefix + b"x" * 100))


class LocalFilesTests(unittest.TestCase):
    def setUp(self):
        # Scratch files stay in the project, never the system temporary directory.
        self.directory = tempfile.TemporaryDirectory(prefix=".usbmon-test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)


class OfflineTests(LocalFilesTests):
    def test_complete_file_byte_and_record_limits_are_enforced_by_api_and_cli(self):
        path = self.root / "evidence.txt"
        data = OUT + DONE
        path.write_bytes(data)
        with patch.object(usbmon, "read_regular_file", wraps=usbmon.read_regular_file) as reader:
            result = usbmon.analyze_file(path, max_bytes=len(data), max_records=2)
        reader.assert_called_once_with(path, max_bytes=len(data))
        self.assertEqual(result["records"], 2)
        with patch.object(usbmon.os, "open", side_effect=AssertionError("oversized input opened")):
            with self.assertRaises(usbmon.AnalysisError) as raised:
                usbmon.analyze_file(path, max_bytes=len(data) - 1)
        self.assertEqual(raised.exception.summary["records"], 0)
        with self.assertRaises(usbmon.AnalysisError) as raised:
            usbmon.analyze_file(path, max_records=1)
        self.assertEqual(raised.exception.summary["records"], 1)
        self.assertEqual(raised.exception.summary["error_line"], 2)
        for limits, code, records in (
            (["--max-bytes", str(len(data)), "--max-records", "2"], 0, 2),
            (["--max-bytes", str(len(data) - 1)], 1, 0),
            (["--max-records", "1"], 1, 1),
        ):
            with self.subTest(limits=limits), \
                    patch.object(usbmon.sys, "stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(usbmon.main(["--analyze", str(path), *limits]), code)
                self.assertEqual(json.loads(stdout.getvalue())["records"], records)

    def test_default_total_byte_and_record_limits_are_not_unbounded(self):
        path = self.root / "evidence.txt"
        path.write_bytes(b"x" * (usbmon.DEFAULT_MAX_BYTES + 1))
        with patch.object(usbmon.os, "open", side_effect=AssertionError("oversized input opened")):
            with self.assertRaises(usbmon.AnalysisError):
                usbmon.analyze_file(path)
        path.write_bytes(DONE * (usbmon.DEFAULT_MAX_RECORDS + 1))
        with self.assertRaises(usbmon.AnalysisError) as raised:
            usbmon.analyze_file(path)
        self.assertEqual(raised.exception.summary["records"], usbmon.DEFAULT_MAX_RECORDS)

    def test_invalid_analysis_limits_fail_before_reading(self):
        for option, values in (
            ("max_bytes", (0, -1, 64 * usbmon.DEFAULT_MAX_BYTES + 1, True, 1.0, None)),
            ("max_records", (0, -1, 1000001, True, 1.0, None)),
        ):
            for value in values:
                with self.subTest(option=option, value=value), \
                        patch.object(usbmon, "read_regular_file") as reader:
                    with self.assertRaises(usbmon.UsbmonError):
                        usbmon.analyze_file(self.root / "absent", **{option: value})
                    reader.assert_not_called()

    def test_kernel_and_device_paths_are_rejected_without_stat_or_open(self):
        for path in ("/proc/self/mem", "/sys/fake-usb/descriptors", "/dev/fake-usb",
                     "/proc/self/../self/status"):
            with self.subTest(path=path), \
                    patch.object(Path, "lstat", side_effect=AssertionError("forbidden stat")), \
                    patch.object(usbmon.os, "open", side_effect=AssertionError("forbidden open")):
                with self.assertRaises(usbmon.AnalysisError):
                    usbmon.analyze_file(path)
                with patch.object(usbmon.sys, "stdout", new_callable=io.StringIO) as stdout:
                    self.assertEqual(usbmon.main(["--analyze", path]), 1)
                self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")

    def test_symlinks_and_special_files_are_rejected_without_opening(self):
        regular = self.root / "regular"
        regular.write_bytes(OUT)
        link = self.root / "link"
        link.symlink_to(regular)
        broken = self.root / "broken"
        broken.symlink_to(self.root / "absent")
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (link, broken, fifo, self.root):
            with self.subTest(path=path), \
                    patch.object(usbmon.os, "open", side_effect=AssertionError("input opened")):
                with self.assertRaises(usbmon.AnalysisError):
                    usbmon.analyze_file(path)
                with patch.object(usbmon.sys, "stdout", new_callable=io.StringIO):
                    self.assertEqual(usbmon.main(["--analyze", str(path)]), 1)

    def test_resolved_kernel_paths_are_rejected_without_opening(self):
        path = self.root / "evidence.txt"
        path.write_bytes(OUT)
        for resolved in ("/proc/self/status", "/sys/fake/attribute", "/dev/fake-device"):
            with self.subTest(resolved=resolved), \
                    patch.object(Path, "resolve", return_value=Path(resolved)), \
                    patch.object(usbmon.os, "open", side_effect=AssertionError("resolved input opened")):
                with self.assertRaises(usbmon.AnalysisError):
                    usbmon.analyze_file(path)

    def test_changed_input_is_rejected_by_shared_snapshot_reader(self):
        path = self.root / "evidence.txt"
        path.write_bytes(OUT)
        opened = path.stat()
        changed = SimpleNamespace(
            st_size=opened.st_size + 1, st_mtime_ns=opened.st_mtime_ns,
            st_ctime_ns=opened.st_ctime_ns,
        )
        with patch.object(usbmon.os, "fstat", side_effect=[opened, changed]):
            with self.assertRaises(usbmon.AnalysisError) as raised:
                usbmon.analyze_file(path)
        self.assertEqual(raised.exception.summary["records"], 0)
        self.assertEqual(raised.exception.summary["error_line"], 0)

    def test_replaced_input_is_rejected_by_shared_snapshot_reader(self):
        path = self.root / "evidence.txt"
        path.write_bytes(OUT)
        before = path.stat()
        replacement = SimpleNamespace(
            st_mode=before.st_mode, st_dev=before.st_dev, st_ino=before.st_ino + 1,
        )
        with patch.object(usbmon.os, "fstat", return_value=replacement):
            with self.assertRaises(usbmon.AnalysisError) as raised:
                usbmon.analyze_file(path)
        self.assertEqual(raised.exception.summary["records"], 0)

    def test_offline_api_and_cli_do_not_check_usb_or_privileges(self):
        path = self.root / "evidence.txt"
        path.write_bytes(OUT + DONE + PENDING_IN)
        before = set(self.root.iterdir())
        with patch.object(usbmon, "read_identity", side_effect=AssertionError("hardware")), \
                patch.object(usbmon, "_open_usbmon", side_effect=AssertionError("hardware")), \
                patch.object(usbmon, "validate_privilege_drop", side_effect=AssertionError("privileges")), \
                patch.object(usbmon.sys, "stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(usbmon.analyze_file(path)["records"], 3)
            self.assertEqual(usbmon.main(["--analyze", str(path)]), 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "completed")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_offline_failed_summary_retains_counters_and_no_raw_input(self):
        path = self.root / "evidence.txt"
        path.write_bytes(OUT + b"a 1 C Bi:1:008:2 0 8 = secret-keyboard\n")
        with self.assertRaises(usbmon.AnalysisError) as raised:
            usbmon.analyze_file(path)
        summary = raised.exception.summary
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["error_line"], 2)
        self.assertNotIn("secret-keyboard", json.dumps(summary))
        with patch.object(usbmon.sys, "stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(usbmon.main(["--analyze", str(path)]), 1)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")

    def test_offline_line_and_pending_bounds(self):
        path = self.root / "evidence.txt"
        for data in (b"a" * 100000, OUT.rstrip(b"\n"), b"a" * 100000 + b"\n"):
            path.write_bytes(data)
            with self.assertRaises(usbmon.AnalysisError):
                usbmon.analyze_file(path, max_line_bytes=64)
        for pending in (0, -1, 65537, True):
            with self.subTest(pending=pending), self.assertRaises(usbmon.UsbmonError):
                usbmon.analyze_file(path, max_pending=pending)

    def test_empty_trace_is_not_a_device_success_claim(self):
        path = self.root / "evidence.txt"
        path.write_bytes(b"")
        result = usbmon.analyze_file(path)
        self.assertEqual(result["records"], 0)
        self.assertEqual(result["completion_status"]["success"], 0)

    def test_missing_or_nonregular_offline_file_is_explicit_failure(self):
        for path in (self.root / "missing", self.root):
            with self.subTest(path=path), self.assertRaises(usbmon.AnalysisError):
                usbmon.analyze_file(path)

    def test_atomic_private_json_does_not_clobber(self):
        path = self.root / "ready.json"
        usbmon._write_json(path, {"first": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            usbmon._write_json(path, {"first": False})
        self.assertEqual(json.loads(path.read_text()), {"first": True})
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_json_destination_is_invisible_until_atomic_publication(self):
        path = self.root / "ready.json"
        real_link = os.link

        def publish(source, destination):
            self.assertFalse(destination.exists())
            self.assertEqual(json.loads(source.read_text()), {"pid": 123})
            real_link(source, destination)

        with patch.object(usbmon.os, "link", side_effect=publish):
            usbmon._write_json(path, {"pid": 123})
        self.assertEqual(json.loads(path.read_text()), {"pid": 123})

    def test_short_writes_are_retried_and_no_progress_is_an_error(self):
        stream = Mock()
        stream.write.side_effect = [2, 1]
        usbmon._write_all(stream, b"abc")
        self.assertEqual(bytes(stream.write.call_args_list[1].args[0]), b"c")
        stream.write.side_effect = [0]
        with self.assertRaisesRegex(usbmon.UsbmonError, "no progress"):
            usbmon._write_all(stream, b"abc")


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.attributes = {"idVendor": "045e", "idProduct": "4444", "busnum": "1", "devnum": "8"}
        self.directory_stat = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_dev=12, st_ino=345)
        self.resolve = patch.object(Path, "resolve", return_value=CANONICAL).start()
        self.stat = patch.object(Path, "stat", return_value=self.directory_stat).start()
        self.read = patch.object(usbmon, "_read_attribute",
                                 side_effect=lambda path: self.attributes[path.name]).start()
        self.open_cached = patch.object(
            Path, "open", side_effect=lambda *args, **kwargs: io.BytesIO(DESCRIPTORS),
        ).start()
        self.addCleanup(patch.stopall)

    def test_fresh_cached_identity_canonicalizes_and_includes_inode(self):
        self.assertEqual(usbmon.read_identity(USB_PATH), IDENTITY)
        self.assertEqual(self.read.call_count, 8)
        self.open_cached.assert_called_once_with("rb")

    def test_repeated_identity_snapshots_are_json_equal(self):
        first = usbmon.read_identity(USB_PATH)
        second = usbmon.read_identity(USB_PATH)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first)), second)
        self.assertEqual(self.open_cached.call_count, 2)
        self.assertEqual(first["descriptors_sha256"], hashlib.sha256(DESCRIPTORS).hexdigest())

    def test_cached_descriptor_change_is_detected_by_guard_equality(self):
        first = usbmon.read_identity(USB_PATH)
        changed = DESCRIPTORS[:-1] + b"\x02"
        self.open_cached.side_effect = lambda *args, **kwargs: io.BytesIO(changed)
        second = usbmon.read_identity(USB_PATH)
        self.assertNotEqual(first, second)
        self.assertEqual(first["usb_path"], second["usb_path"])
        self.assertEqual(first["devnum"], second["devnum"])
        self.assertEqual(second["descriptors_sha256"], hashlib.sha256(changed).hexdigest())
        with self.assertRaisesRegex(usbmon.IdentityError, "identity/address changed"):
            usbmon._check_identity(USB_PATH, first)

    def test_unreadable_cached_descriptors_fail_identity_validation(self):
        self.open_cached.side_effect = PermissionError()
        with self.assertRaisesRegex(usbmon.IdentityError, "cached sysfs descriptors"):
            usbmon.read_identity(USB_PATH)

    def test_wrong_device_bus_or_non_numeric_addresses_rejected(self):
        for key, value in (("idVendor", "0451"), ("idProduct", "2046"),
                           ("busnum", "2"), ("busnum", "0"), ("busnum", "../1"),
                           ("devnum", "128"), ("devnum", "0"), ("devnum", "+8"),
                           ("devnum", "1\n8")):
            before = dict(self.attributes)
            self.attributes[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(usbmon.IdentityError):
                usbmon.read_identity(USB_PATH)
            self.attributes = before

    def test_nonphysical_or_non_sysfs_path_rejected(self):
        for path in ("/home/device/1-3.3", "/sys/devices/usb1",
                     "/sys/devices/usb1/1-3.3:1.0"):
            self.resolve.return_value = Path(path)
            with self.subTest(path=path), self.assertRaises(usbmon.IdentityError):
                usbmon.read_identity(USB_PATH)

    def test_removal_and_mid_snapshot_replacement_rejected(self):
        self.stat.side_effect = [self.directory_stat, FileNotFoundError()]
        with self.assertRaisesRegex(usbmon.IdentityError, "inaccessible"):
            usbmon.read_identity(USB_PATH)
        self.stat.side_effect = [
            self.directory_stat, SimpleNamespace(st_mode=stat.S_IFDIR, st_dev=12, st_ino=346)
        ]
        with self.assertRaisesRegex(usbmon.IdentityError, "directory changed"):
            usbmon.read_identity(USB_PATH)
        self.stat.side_effect = None
        self.resolve.side_effect = [CANONICAL, CANONICAL.with_name("1-3.4")]
        with self.assertRaisesRegex(usbmon.IdentityError, "physical path changed"):
            usbmon.read_identity(USB_PATH)

    def test_attributes_changing_between_reads_are_rejected(self):
        self.read.side_effect = ["045e", "4444", "1", "8", "045e", "4444", "1", "9"]
        with self.assertRaisesRegex(usbmon.IdentityError, "changed"):
            usbmon.read_identity(USB_PATH)

    def test_cached_attribute_reads_are_bounded(self):
        # Access the real helper without reading any real sysfs attribute.
        patch.stopall()
        for content in (b"x" * 129, b"\xff\n"):
            with self.subTest(content=content), \
                    patch.object(Path, "open", return_value=io.BytesIO(content)), \
                    self.assertRaises(usbmon.IdentityError):
                usbmon._read_attribute(Path("fake-attribute"))

    def test_cached_descriptors_only_and_header_validation(self):
        with patch.object(Path, "open", return_value=io.BytesIO(DESCRIPTORS)) as opened:
            self.assertEqual(usbmon._cached_descriptors(IDENTITY), DESCRIPTORS)
            opened.assert_called_once_with("rb")
        for content in (b"", b"\0" * 18, DESCRIPTORS + b"\0" * usbmon.DEFAULT_MAX_BYTES):
            with self.subTest(length=len(content)), \
                    patch.object(Path, "open", return_value=io.BytesIO(content)), \
                    self.assertRaises(usbmon.IdentityError):
                usbmon._cached_descriptors(IDENTITY)


class PrivilegeTests(unittest.TestCase):
    def setUp(self):
        self.environment = {"SUDO_UID": "1000", "SUDO_GID": "1000",
                            "SUDO_USER": "owner", "SUDO_COMMAND": "python tools/marvin_usbmon.py"}
        self.account = SimpleNamespace(pw_name="owner", pw_gid=1000)
        self.lookup = patch.object(usbmon.pwd, "getpwuid", return_value=self.account).start()
        self.addCleanup(patch.stopall)

    def test_ordinary_user_needs_no_drop(self):
        self.assertIsNone(usbmon.validate_privilege_drop(False, euid=1000, environ={}))
        self.lookup.assert_not_called()
        with self.assertRaises(usbmon.UsbmonError):
            usbmon.validate_privilege_drop(True, euid=1000, environ=self.environment)

    def test_root_requires_flag_and_complete_sudo_environment(self):
        with self.assertRaisesRegex(usbmon.UsbmonError, "requires"):
            usbmon.validate_privilege_drop(False, euid=0, environ=self.environment)
        for key in self.environment:
            environment = dict(self.environment)
            del environment[key]
            with self.subTest(key=key), self.assertRaises(usbmon.UsbmonError):
                usbmon.validate_privilege_drop(True, euid=0, environ=environment)

    def test_uid_gid_must_be_numeric_nonzero_and_account_must_match(self):
        for key in ("SUDO_UID", "SUDO_GID"):
            for value in ("", "0", "-1", "+1000", "1000 ", "1e3", "4294967295", "1" * 100):
                environment = dict(self.environment, **{key: value})
                with self.subTest(key=key, value=value), self.assertRaises(usbmon.UsbmonError):
                    usbmon.validate_privilege_drop(True, euid=0, environ=environment)
        self.account.pw_name = "somebody-else"
        with self.assertRaises(usbmon.UsbmonError):
            usbmon.validate_privilege_drop(True, euid=0, environ=self.environment)
        self.lookup.side_effect = KeyError()
        with self.assertRaises(usbmon.UsbmonError):
            usbmon.validate_privilege_drop(True, euid=0, environ=self.environment)

    def test_valid_sudo_identity_and_mocked_drop_order(self):
        self.assertEqual(usbmon.validate_privilege_drop(
            True, euid=0, environ=self.environment), (1000, 1000))
        calls = []
        with patch.object(usbmon.os, "setgroups", side_effect=lambda groups: calls.append(("groups", groups))), \
                patch.object(usbmon.os, "setgid", side_effect=lambda gid: calls.append(("gid", gid))), \
                patch.object(usbmon.os, "setuid", side_effect=lambda uid: calls.append(("uid", uid))), \
                patch.object(usbmon.os, "getuid", return_value=1000), \
                patch.object(usbmon.os, "geteuid", return_value=1000), \
                patch.object(usbmon.os, "getgid", return_value=1000), \
                patch.object(usbmon.os, "getegid", return_value=1000), \
                patch.object(usbmon.os, "getgroups", return_value=[]):
            usbmon._drop_privileges((1000, 1000))
        self.assertEqual(calls, [("groups", []), ("gid", 1000), ("uid", 1000)])

    def test_drop_errors_are_explicit_without_real_privilege_changes(self):
        with patch.object(usbmon.os, "setgroups", side_effect=PermissionError), \
                patch.object(usbmon.os, "setgid") as gid, patch.object(usbmon.os, "setuid") as uid:
            with self.assertRaisesRegex(usbmon.UsbmonError, "drop failed"):
                usbmon._drop_privileges((1000, 1000))
            gid.assert_not_called()
            uid.assert_not_called()

    def test_only_bus_specific_fixed_debugfs_path_is_opened(self):
        with patch.object(usbmon.os, "open", return_value=123) as opened:
            self.assertEqual(usbmon._open_usbmon(7), 123)
            self.assertEqual(opened.call_args.args[0], Path("/sys/kernel/debug/usb/usbmon/7u"))
            self.assertTrue(opened.call_args.args[1] & os.O_NONBLOCK)
            for bus in ("0u", "../0u", 0, -1, 65536):
                with self.assertRaises(usbmon.UsbmonError):
                    usbmon._open_usbmon(bus)
            self.assertEqual(opened.call_count, 1)


class CaptureTests(LocalFilesTests):
    def setUp(self):
        super().setUp()
        self.output = self.root / "capture"
        self.now = 0.0
        self.chunks = []
        self.handlers = {}
        self.after_read = None
        self.closed = []
        self.trace_fd = 123456
        self.select_count = 0
        self.signal_after_select = None
        self.after_select = None
        patch.object(usbmon.os, "geteuid", return_value=1000).start()
        self.identity = patch.object(usbmon, "read_identity", return_value=IDENTITY).start()
        self.descriptors = patch.object(usbmon, "_cached_descriptors", return_value=DESCRIPTORS).start()
        self.open_trace = patch.object(usbmon, "_open_usbmon", return_value=self.trace_fd).start()
        self.drop = patch.object(usbmon, "_drop_privileges").start()
        patch.object(usbmon.time, "monotonic", side_effect=lambda: self.now).start()
        patch.object(usbmon.select, "select", side_effect=self.select).start()
        patch.object(usbmon.os, "read", side_effect=self.read).start()
        real_close = os.close
        patch.object(usbmon.os, "close", side_effect=lambda fd: (
            self.closed.append(fd) if fd == self.trace_fd else real_close(fd)
        )).start()
        patch.object(usbmon.signal, "signal", side_effect=self.handle).start()
        self.addCleanup(patch.stopall)

    def handle(self, signum, handler):
        previous = self.handlers.get(signum, signal.SIG_DFL)
        self.handlers[signum] = handler
        return previous

    def select(self, readable, writable, exceptional, timeout):
        self.select_count += 1
        self.assertEqual(readable, [self.trace_fd])
        self.assertLessEqual(timeout, usbmon.IDENTITY_INTERVAL + 1e-9)
        self.assertGreaterEqual(timeout, 0)
        self.assertTrue((self.output / "ready.json").is_file())
        self.now += min(timeout, 0.001) if self.chunks else timeout
        if self.signal_after_select is not None:
            self.handlers[self.signal_after_select](self.signal_after_select, None)
            self.signal_after_select = None
        if self.after_select is not None:
            action, self.after_select = self.after_select, None
            action()
        return ([self.trace_fd] if self.chunks else []), [], []

    def read(self, fd, size):
        self.assertEqual(fd, self.trace_fd)
        self.assertEqual(size, usbmon.READ_SIZE)
        data = self.chunks.pop(0)
        if isinstance(data, Exception):
            raise data
        if self.after_read is not None:
            self.after_read()
        return data

    def capture(self, **kwargs):
        options = {"seconds": 0.5, "actuators_isolated": True}
        options.update(kwargs)
        return usbmon.capture(USB_PATH, self.output, **options)

    def metadata(self):
        return json.loads((self.output / "metadata.json").read_text())

    @contextmanager
    def binary_monitor(self, final_stats, *, initial_stats=None, event_reader=None):
        initial = {"queued": 0, "dropped": 0} if initial_stats is None else initial_stats
        with patch.object(usbmon.binary, "open_monitor", return_value=self.trace_fd) as opening, \
                patch.object(usbmon.binary, "read_stats", side_effect=[initial, final_stats]) as stats, \
                patch.object(usbmon.binary, "read_event", side_effect=(
                    event_reader if event_reader is not None else AssertionError("unexpected binary read")
                )), \
                patch.object(usbmon.binary, "address", return_value=(1, 8)), \
                patch.object(usbmon.binary, "to_text", return_value=OUT), \
                patch.object(usbmon.binary, "evidence_frame", return_value=b"mock-frame"):
            yield
        opening.assert_called_once_with(1)
        self.assertEqual(stats.call_count, 2)
        self.open_trace.assert_not_called()
        self.assertEqual(self.closed[-1], self.trace_fd)

    def test_direct_binary_capture_requires_complete_final_stats_at_both_stop_boundaries(self):
        for reason in ("duration", "coordinator_stop"):
            for index, stats in enumerate((
                {"queued": 1, "dropped": 0}, {"queued": 0, "dropped": 1},
                {"queued": False, "dropped": 0}, None,
            )):
                with self.subTest(reason=reason, stats=stats):
                    self.output = self.root / f"final-stats-{reason}-{index}"
                    self.now = 0
                    self.after_select = lambda: self.request_stop(reason)
                    with self.binary_monitor(stats):
                        with self.assertRaises(usbmon.CaptureError) as raised:
                            self.capture(backend="binary", coordinator_stop=True)
                    metadata = raised.exception.metadata
                    self.assertEqual(metadata["status"], "failed")
                    self.assertEqual(metadata["stop_reason"], "capture_error")
                    self.assertEqual(metadata["monitor_final_stats"], stats)
                    self.assertIn("final queued/dropped", metadata["monitor_final_stats_error"])
                    self.assertEqual(self.metadata()["status"], "failed")
                    self.assertEqual(json.loads((self.output / "summary.json").read_text())["status"], "failed")
                    self.assertEqual((self.output / "binary-events.bin").read_bytes(), usbmon.binary.FILE_MAGIC)
                    self.assertEqual((self.output / "usbmon.txt").read_bytes(), b"")

    def test_direct_binary_zero_final_stats_complete_but_initial_drops_still_fail(self):
        for reason in ("duration", "coordinator_stop"):
            self.output = self.root / f"complete-{reason}"
            self.now = 0
            self.after_select = lambda: self.request_stop(reason)
            with self.binary_monitor({"queued": 0, "dropped": 0}):
                self.assertEqual(self.capture(backend="binary", coordinator_stop=True)["status"], "completed")
        self.output = self.root / "initial-loss"
        self.now = 0
        with self.binary_monitor({"queued": 0, "dropped": 0}, initial_stats={"queued": 0, "dropped": 1}):
            with self.assertRaisesRegex(usbmon.CaptureError, "dropped events"):
                self.capture(backend="binary")

    def test_final_statistics_error_does_not_replace_an_earlier_binary_read_failure(self):
        self.chunks = [b"ready"]
        original = usbmon.binary.BinaryError("original binary read error")
        with self.binary_monitor(OSError(5, "stats failure"), event_reader=original):
            with self.assertRaises(usbmon.CaptureError) as raised:
                self.capture(backend="binary")
        self.assertIs(raised.exception.__cause__, original)
        self.assertEqual(self.metadata()["error"], str(original))
        self.assertIn("errno 5", self.metadata()["monitor_final_stats_error"])

    def test_binary_queued_tail_retains_existing_signal_and_limit_non_success_results(self):
        for stop in ("signal", "limit"):
            self.output = self.root / f"non-success-{stop}"
            self.now = 0
            self.chunks = [] if stop == "signal" else [b"ready"]
            self.signal_after_select = signal.SIGINT if stop == "signal" else None

            def read_event(fd):
                self.chunks.pop()
                return b"mock-header", b"\r"

            with self.binary_monitor({"queued": 1, "dropped": 0}, event_reader=read_event):
                result = self.capture(backend="binary", max_records=1)
            self.assertEqual(result["status"], "interrupted" if stop == "signal" else "limit_reached")
            self.assertEqual(result["monitor_final_stats"]["queued"], 1)

    def test_direct_binary_cli_reports_an_unread_tail_as_failure(self):
        with self.binary_monitor({"queued": 1, "dropped": 0}), \
                patch.object(usbmon.sys, "stdout", new_callable=io.StringIO) as stdout, \
                patch.object(usbmon.sys, "stderr", new_callable=io.StringIO) as stderr:
            code = usbmon.main([
                "--usb-path", USB_PATH, "--output", str(self.output),
                "--seconds", "0.5", "--actuators-isolated", "--backend", "binary",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["status"], "failed")

    def test_filters_keyboard_payload_and_matches_only_target(self):
        other_bus = b"ffff0002 123458 C Ii:2:008:1 0:8 8 = 00000400 00000000\n"
        self.chunks = [KEYBOARD + other_bus + OUT[:12], OUT[12:] + DONE + PENDING_IN]
        result = self.capture()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stop_reason"], "duration")
        self.assertEqual(result["ignored_records"], 2)
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT + DONE + PENDING_IN)
        self.assertEqual(result["retained_bytes"], len(OUT + DONE + PENDING_IN))
        for path in self.output.iterdir():
            self.assertNotIn(b"00000400", path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output / "descriptors.bin").read_bytes(), DESCRIPTORS)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["pairing"]["matched_completions"], 1)
        self.assertEqual(summary["pairing"]["pending_in_retained"], 1)
        self.assertEqual(self.closed, [self.trace_fd])

    def test_readiness_published_after_open_drop_and_identity_check_before_loop(self):
        order = []
        self.identity.side_effect = lambda path: order.append("identity") or IDENTITY
        self.open_trace.side_effect = lambda bus: order.append("open") or self.trace_fd
        self.drop.side_effect = lambda ids: order.append("drop")
        real_write = usbmon._write_json

        def write_json(path, value, **kwargs):
            if path.name == "ready.json":
                self.assertEqual(order[-1], "identity")
                self.assertLess(order.index("open"), order.index("drop"))
                self.assertTrue((self.output / "usbmon.txt").exists())
                self.assertEqual(self.select_count, 0)
                self.assertFalse(path.exists())
            return real_write(path, value, **kwargs)

        with patch.object(usbmon, "_write_json", side_effect=write_json):
            self.capture()
        ready = json.loads((self.output / "ready.json").read_text())
        self.assertEqual(ready["pid"], os.getpid())
        for field in ("busnum", "devnum", "usb_path"):
            self.assertEqual(ready[field], IDENTITY[field])
        self.assertIn("utc", ready)
        self.assertEqual(ready["monotonic"], 0.0)

    def test_quiet_trace_duration_is_monotonic_and_checks_identity(self):
        result = self.capture()
        self.assertEqual(result["retained_records"], 0)
        self.assertAlmostEqual(result["elapsed_seconds"], 0.5)
        self.assertGreaterEqual(self.identity.call_count, 6)
        self.assertEqual(self.handlers[signal.SIGINT], signal.SIG_DFL)
        self.assertEqual(self.handlers[signal.SIGTERM], signal.SIG_DFL)

    def request_stop(self, reason):
        if reason == "duration":
            self.now = 0.5
        else:
            (self.output / usbmon.COORDINATOR_STOP_FILE).write_bytes(b"")

    def test_partial_target_or_unclassified_line_cannot_complete_at_either_stop(self):
        for reason in ("duration", "coordinator_stop"):
            for partial in (PENDING_IN.rstrip(b"\n"), b"unknown-private-prefix"):
                with self.subTest(reason=reason, partial=partial):
                    self.output = self.root / f"partial-{len(list(self.root.iterdir()))}"
                    self.now = 0
                    self.chunks = [OUT + DONE, partial]
                    self.after_read = lambda: self.request_stop(reason) if not self.chunks else None
                    with self.assertRaisesRegex(usbmon.CaptureError, "Incomplete USB capture") as raised:
                        self.capture(coordinator_stop=True)
                    result = raised.exception.metadata
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(result["unretained_partial_line_bytes"], len(partial))
                    self.assertEqual(result["unprocessed_records"], 0)
                    self.assertEqual(result["retained_records"], 2)
                    self.assertEqual(result["retained_bytes"], len(OUT + DONE))
                    self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT + DONE)
                    summary = json.loads((self.output / "summary.json").read_text())
                    self.assertEqual(summary["status"], "failed")
                    self.assertEqual(summary["records"], 2)
                    for path in self.output.iterdir():
                        self.assertNotIn(partial, path.read_bytes())
                    self.assertEqual(self.closed[-1], self.trace_fd)

    def test_unrelated_partial_line_is_counted_but_not_saved_or_treated_as_target_loss(self):
        self.chunks = [OUT + DONE + KEYBOARD.rstrip(b"\n")]
        result = self.capture()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["unretained_partial_line_bytes"], 0)
        self.assertEqual(result["ignored_partial_line_bytes"], len(KEYBOARD) - 1)
        usbmon.validate_capture_completeness(result)
        for path in self.output.iterdir():
            self.assertNotIn(b"00000400", path.read_bytes())

    def test_already_read_target_records_cannot_disappear_at_stop_boundaries(self):
        incoming = b"ffff0003 123460 C Bi:1:008:2 0 1 = 41\n"
        target_records = (OUT, DONE, PENDING_IN, incoming)
        original_add = usbmon.Analyzer.add
        for reason in ("duration", "coordinator_stop"):
            for retained in (0, 1, 2):
                with self.subTest(reason=reason, retained=retained):
                    self.output = self.root / f"batch-{reason}-{retained}"
                    self.now = 0
                    self.chunks = [KEYBOARD + b"".join(target_records)]
                    self.after_read = (lambda: self.request_stop(reason)) if retained == 0 else None

                    def add_then_stop(analyzer, record):
                        original_add(analyzer, record)
                        if analyzer.records == retained:
                            self.request_stop(reason)

                    with patch.object(usbmon.Analyzer, "add", new=add_then_stop), \
                            self.assertRaisesRegex(usbmon.CaptureError, "Incomplete USB capture") as raised:
                        self.capture(coordinator_stop=True)
                    result = raised.exception.metadata
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(result["retained_records"], retained)
                    self.assertEqual(result["retained_bytes"], len(b"".join(target_records[:retained])))
                    self.assertEqual(result["unprocessed_records"], len(target_records) - retained)
                    self.assertEqual(result["unprocessed_record_bytes"], len(b"".join(target_records[retained:])))
                    self.assertEqual(result["unretained_partial_line_bytes"], 0)
                    self.assertEqual(result["ignored_records"], 1)
                    self.assertEqual(result["unaccounted_retained_bytes"], 0)
                    self.assertEqual((self.output / "usbmon.txt").read_bytes(), b"".join(target_records[:retained]))
                    summary = json.loads((self.output / "summary.json").read_text())
                    self.assertEqual(summary["status"], "failed")
                    self.assertEqual(summary["records"], retained)
                    self.assertEqual(self.chunks, [])
                    for path in self.output.iterdir():
                        self.assertNotIn(b"00000400", path.read_bytes())

    def test_stop_during_prewrite_identity_check_accounts_for_current_record_and_tail(self):
        for reason in ("duration", "coordinator_stop"):
            self.output = self.root / f"prewrite-{reason}"
            self.now = 0
            partial = b"ff 123 C Bi:1:008:2"
            self.chunks = [OUT + DONE + partial]
            with patch.object(usbmon, "parse_record", wraps=usbmon.parse_record) as parse:
                self.identity.side_effect = lambda path: (
                    self.request_stop(reason) if parse.call_count else None
                ) or IDENTITY
                with self.assertRaises(usbmon.CaptureError) as raised:
                    self.capture(coordinator_stop=True)
            self.identity.side_effect = None
            result = raised.exception.metadata
            self.assertEqual(result["retained_bytes"], 0)
            self.assertEqual(result["retained_records"], 0)
            self.assertEqual(result["unprocessed_records"], 2)
            self.assertEqual(result["unprocessed_record_bytes"], len(OUT + DONE))
            self.assertEqual(result["unretained_partial_line_bytes"], len(partial))

    def test_only_unrelated_records_remaining_after_boundary_do_not_make_a_target_gap(self):
        self.chunks = [OUT + DONE + KEYBOARD]
        original_add = usbmon.Analyzer.add

        def stop_after_pair(analyzer, record):
            original_add(analyzer, record)
            if analyzer.records == 2:
                self.now = 0.5

        with patch.object(usbmon.Analyzer, "add", new=stop_after_pair):
            result = self.capture()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["retained_records"], 2)
        self.assertEqual(result["ignored_records"], 1)
        self.assertEqual(result["unprocessed_records"], 0)
        self.assertEqual(result["unprocessed_record_bytes"], 0)

    def test_already_dequeued_binary_target_event_is_not_silently_lost_at_boundary(self):
        for reason in ("duration", "coordinator_stop"):
            with self.subTest(reason=reason):
                self.output = self.root / f"binary-{reason}"
                self.now = 0
                self.chunks = [b"ready"]

                def read_event(fd):
                    self.assertEqual(fd, self.trace_fd)
                    self.chunks.pop()
                    self.request_stop(reason)
                    return b"mock-header", b"\r"

                with patch.object(usbmon.binary, "open_monitor", return_value=self.trace_fd), \
                        patch.object(usbmon.binary, "read_stats", return_value={"queued": 0, "dropped": 0}), \
                        patch.object(usbmon.binary, "read_event", side_effect=read_event), \
                        patch.object(usbmon.binary, "address", return_value=(1, 8)), \
                        patch.object(usbmon.binary, "to_text", return_value=OUT), \
                        patch.object(usbmon.binary, "evidence_frame", return_value=b"mock-frame"):
                    with self.assertRaises(usbmon.CaptureError) as raised:
                        self.capture(backend="binary", coordinator_stop=True)
                result = raised.exception.metadata
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["retained_records"], 0)
                self.assertEqual(result["retained_bytes"], 0)
                self.assertEqual(result["unprocessed_records"], 1)
                self.assertEqual(result["unprocessed_record_bytes"], len(OUT))
                self.assertEqual(result["retained_binary_bytes"], len(usbmon.binary.FILE_MAGIC))
                self.assertEqual((self.output / "binary-events.bin").read_bytes(), usbmon.binary.FILE_MAGIC)

    def test_partial_and_unprocessed_evidence_keep_signal_and_limit_non_success_status(self):
        partial = PENDING_IN.rstrip(b"\n")
        for stop in ("signal", "limit"):
            self.output = self.root / stop
            self.now = 0
            self.chunks = [OUT + DONE + partial]
            self.after_read = (
                lambda: self.handlers[signal.SIGINT](signal.SIGINT, None)
            ) if stop == "signal" else None
            result = self.capture(max_records=1)
            self.assertEqual(result["status"], "interrupted" if stop == "signal" else "limit_reached")
            self.assertEqual(result["unretained_partial_line_bytes"], len(partial))
            self.assertEqual(result["unprocessed_records"], 2 if stop == "signal" else 1)
            with self.assertRaises(usbmon.UsbmonError):
                usbmon.validate_capture_completeness(result)

    def test_cli_partial_capture_is_failure_not_a_successful_empty_trace(self):
        self.chunks = [PENDING_IN.rstrip(b"\n")]
        with patch.object(usbmon.sys, "stderr", new_callable=io.StringIO) as stderr:
            code = usbmon.main([
                "--usb-path", USB_PATH, "--output", str(self.output),
                "--seconds", "0.5", "--actuators-isolated",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stderr.getvalue())["status"], "failed")

    def test_coordinator_stop_is_explicit_and_advertised_without_extending_duration(self):
        self.after_select = lambda: (self.output / usbmon.COORDINATOR_STOP_FILE).write_bytes(b"")
        result = self.capture(coordinator_stop=True)
        ready = json.loads((self.output / "ready.json").read_text())
        for value in (ready, result):
            self.assertTrue(value["coordinator_stop"])
            self.assertEqual(value["coordinator_stop_file"], usbmon.COORDINATOR_STOP_FILE)
            self.assertEqual(value["seconds"], 0.5)
            self.assertEqual(value["deadline_monotonic"], 0.5)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stop_reason"], "coordinator_stop")
        self.assertEqual(result["stopped_monotonic"], 0.2)
        self.assertLess(result["elapsed_seconds"], 0.5)
        self.assertIsNone(result["signal"])

    def test_stop_entry_is_not_even_inspected_without_opt_in(self):
        self.after_select = lambda: (self.output / usbmon.COORDINATOR_STOP_FILE).symlink_to(self.root / "missing")
        with patch.object(usbmon, "_coordinator_stop_requested", side_effect=AssertionError("must not inspect")):
            result = self.capture()
        self.assertEqual(result["stop_reason"], "duration")
        self.assertFalse(result["coordinator_stop"])
        self.assertIsNone(result["coordinator_stop_file"])

    def test_stop_request_type_checks_never_open_or_follow_the_entry(self):
        target = self.root / "private"
        target.write_bytes(b"private payload")
        empty = self.root / "empty"
        empty.write_bytes(b"")
        request = self.root / usbmon.COORDINATOR_STOP_FILE
        with patch.object(Path, "open", side_effect=AssertionError("request must not be opened")):
            self.assertFalse(usbmon._coordinator_stop_requested(self.root))
            for create in (
                lambda: request.symlink_to(target),
                lambda: request.symlink_to(self.root / "missing"),
                lambda: request.mkdir(),
                lambda: os.mkfifo(request),
                lambda: os.link(empty, request),
            ):
                create()
                with self.assertRaisesRegex(usbmon.UsbmonError, "regular file"):
                    usbmon._coordinator_stop_requested(self.root)
                if request.is_dir():
                    request.rmdir()
                else:
                    request.unlink()
        request.write_bytes(b"private payload")
        with patch.object(Path, "open", side_effect=AssertionError("request must not be read")):
            with self.assertRaises(usbmon.UsbmonError):
                usbmon._coordinator_stop_requested(self.root)

    def test_malformed_stop_request_fails_capture_without_echoing_contents(self):
        self.after_select = lambda: (self.output / usbmon.COORDINATOR_STOP_FILE).write_bytes(b"private payload")
        with self.assertRaises(usbmon.CaptureError) as raised:
            self.capture(coordinator_stop=True)
        self.assertEqual(raised.exception.metadata["status"], "failed")
        self.assertEqual(raised.exception.metadata["stop_reason"], "capture_error")
        self.assertNotIn("private payload", str(raised.exception))

    def test_stop_file_cannot_override_hard_deadline_or_either_signal(self):
        for signum in (None, signal.SIGINT, signal.SIGTERM):
            self.output = self.root / f"stop-{signum}"
            self.now = 0
            self.signal_after_select = signum
            self.after_select = lambda: (self.output / usbmon.COORDINATOR_STOP_FILE).write_bytes(b"")
            result = self.capture(seconds=0.2, coordinator_stop=True)
            self.assertEqual(result["stop_reason"], "signal" if signum else "duration")
            self.assertEqual(result["status"], "interrupted" if signum else "completed")
            self.assertEqual(result["signal"], signum)

    def test_coordinator_opt_in_preserves_record_and_byte_limits(self):
        for options in ({"max_records": 1}, {"max_bytes": len(OUT)}):
            self.output = self.root / f"limit-{len(list(self.root.iterdir()))}"
            self.chunks = [OUT + DONE]
            result = self.capture(coordinator_stop=True, **options)
            self.assertEqual(result["status"], "limit_reached")
            self.assertEqual(result["retained_records"], 1)

    def test_cli_coordinator_stop_is_capture_only_and_keeps_signal_exit(self):
        argv = ["--usb-path", USB_PATH, "--output", str(self.output),
                "--seconds", "0.5", "--actuators-isolated", "--coordinator-stop"]
        self.after_select = lambda: (self.output / usbmon.COORDINATOR_STOP_FILE).write_bytes(b"")
        with patch.object(usbmon.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(usbmon.main(argv), 0)
        self.assertEqual(self.metadata()["stop_reason"], "coordinator_stop")
        self.output = self.root / "signalled"
        self.signal_after_select = signal.SIGTERM
        argv[argv.index("--output") + 1] = str(self.output)
        with patch.object(usbmon.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(usbmon.main(argv), 130)
        with patch.object(usbmon.sys, "stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            usbmon.main(["--analyze", str(self.root / "anything"), "--coordinator-stop"])

    def test_identity_change_before_readiness_fails_without_ready(self):
        self.identity.side_effect = [IDENTITY, IDENTITY, dict(IDENTITY, devnum=9), IDENTITY]
        with self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertFalse((self.output / "ready.json").exists())
        self.assertEqual(self.metadata()["status"], "failed")
        self.assertEqual(self.closed, [self.trace_fd])

    def test_identity_change_periodically_on_quiet_bus_fails(self):
        self.identity.side_effect = lambda path: IDENTITY if self.now < 0.2 else dict(IDENTITY, devnum=9)
        with self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertLess(self.now, 0.5)
        self.assertEqual(self.metadata()["stop_reason"], "identity_unavailable_or_changed")

    def test_identity_unavailable_during_record_recheck_retains_partial(self):
        self.chunks = [OUT, DONE]
        self.after_read = lambda: (
            setattr(self.identity, "side_effect", usbmon.IdentityError("Identity unavailable."))
            if not self.chunks else None
        )
        with self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)
        self.assertEqual(self.metadata()["retained_records"], 1)
        self.assertEqual(self.metadata()["status"], "failed")

    def test_identity_checked_after_records_not_only_periodically(self):
        self.chunks = [OUT + DONE]
        self.identity.side_effect = [IDENTITY, IDENTITY, IDENTITY, IDENTITY,
                                     dict(IDENTITY, sysfs_inode=999), IDENTITY]
        with self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)
        self.assertLess(self.now, usbmon.IDENTITY_INTERVAL)

    def test_eof_is_failure_with_partial_filtered_evidence(self):
        self.chunks = [KEYBOARD + OUT, b""]
        with self.assertRaisesRegex(usbmon.CaptureError, "EOF"):
            self.capture()
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)
        self.assertEqual(self.metadata()["status"], "failed")
        self.assertIsNotNone(self.metadata()["finished_at"])
        self.assertEqual(self.closed, [self.trace_fd])

    def test_read_error_is_not_swallowed(self):
        self.chunks = [OUT, OSError(5, "input/output error")]
        with self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)
        self.assertIn("errno 5", self.metadata()["error"])

    def test_failed_partial_disk_write_reports_actual_retained_byte_count(self):
        self.chunks = [OUT + DONE]
        original_open = usbmon._private_file

        class PartialWrite:
            def __init__(self, stream):
                self.stream = stream
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def write(self, data):
                self.calls += 1
                if self.calls == 1:
                    return self.stream.write(data[:10])
                raise OSError(28, "no space")

        def private_file(path):
            stream = original_open(path)
            return PartialWrite(stream) if path.name == "usbmon.txt" else stream

        with patch.object(usbmon, "_private_file", side_effect=private_file), \
                self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT[:10])
        metadata = self.metadata()
        self.assertEqual(metadata["retained_bytes"], 10)
        self.assertEqual(metadata["retained_records"], 0)
        self.assertEqual(metadata["unaccounted_retained_bytes"], 10)

    def test_unexpected_failure_retains_metadata_and_is_not_success_shaped(self):
        self.chunks = [OUT]
        with patch.object(usbmon, "parse_record", side_effect=RuntimeError("private input")), \
                self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertEqual(self.metadata()["status"], "failed")
        self.assertIn("RuntimeError", self.metadata()["error"])
        self.assertNotIn("private input", self.metadata()["error"])

    def test_nonblocking_retry_does_not_fail(self):
        self.chunks = [BlockingIOError(), InterruptedError(), OUT]
        self.assertEqual(self.capture()["retained_records"], 1)

    def test_potential_target_parse_failure_does_not_leak_other_payload(self):
        self.chunks = [
            KEYBOARD + OUT,
            b"a 1 C Bi:1:008:2 0 8 = secret-keyboard\n",
        ]
        with self.assertRaises(usbmon.CaptureError):
            self.capture()
        for path in self.output.iterdir():
            self.assertNotIn(b"secret-keyboard", path.read_bytes())
            self.assertNotIn(b"00000400", path.read_bytes())
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)

    def test_malformed_other_payload_is_discarded_without_parsing(self):
        self.chunks = [b"a 1 C Ii:1:009:1 0 8 = secret-keyboard\n" + OUT]
        result = self.capture()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["ignored_records"], 1)
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)

    def test_overlong_target_fails_but_other_is_bounded_and_ignored(self):
        self.chunks = [b"a 1 C Ii:1:009:1 0 100 = " + b"z" * 500,
                       b"z" * 500, b"\n" + OUT]
        result = self.capture(max_line_bytes=64)
        self.assertEqual(result["ignored_overlong_records"], 1)
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)
        self.output = self.root / "second"
        self.chunks = [OUT + b"a 1 C Bi:1:008:2 0 100 = " + b"z" * 500]
        with self.assertRaises(usbmon.CaptureError):
            self.capture(max_line_bytes=64)
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)

    def test_retained_byte_cap_never_saves_partial_record(self):
        self.chunks = [OUT + DONE]
        result = self.capture(max_bytes=len(OUT) + 1)
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["stop_reason"], "max_bytes")
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)
        self.assertEqual(result["retained_bytes"], len(OUT))

    def test_exact_byte_and_record_caps(self):
        for options in ({"max_bytes": len(OUT)}, {"max_records": 1}, {"max_bytes": 1}):
            self.output = self.root / str(len(list(self.root.iterdir())))
            self.chunks = [OUT + DONE]
            result = self.capture(**options)
            self.assertEqual(result["status"], "limit_reached")
            self.assertLessEqual(result["retained_records"], 1)
            self.assertLessEqual(result["retained_bytes"], options.get("max_bytes", 1048576))

    def test_busy_unrelated_source_still_respects_duration(self):
        self.chunks = [KEYBOARD] * 1000
        result = self.capture(seconds=0.02)
        self.assertEqual(result["status"], "completed")
        self.assertLessEqual(result["elapsed_seconds"], 0.021)
        self.assertEqual(result["retained_bytes"], 0)

    def test_identity_check_consuming_remaining_time_does_not_read_more(self):
        def identity(path):
            if self.now >= 0.2:
                self.now += 0.5
            return IDENTITY

        self.identity.side_effect = identity
        result = self.capture()
        self.assertEqual(result["stop_reason"], "duration")
        self.assertEqual(self.select_count, 1)

    def test_sigint_and_sigterm_preserve_evidence_and_return_interrupted(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.output = self.root / str(signum)
            self.chunks = [OUT]
            original_select = self.select

            def select_then_signal(*args):
                if not self.chunks:
                    self.signal_after_select = signum
                return original_select(*args)

            with patch.object(usbmon.select, "select", side_effect=select_then_signal):
                result = self.capture()
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["signal"], signum)
            self.assertEqual((self.output / "usbmon.txt").read_bytes(), OUT)

    def test_cli_interrupt_and_failure_exit_status(self):
        self.signal_after_select = signal.SIGINT
        arguments = ["--usb-path", USB_PATH, "--output", str(self.output),
                     "--seconds", "0.5", "--actuators-isolated"]
        with patch.object(usbmon.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(usbmon.main(arguments), 130)
        with patch.object(usbmon.sys, "stderr", new_callable=io.StringIO):
            self.assertEqual(usbmon.main(arguments), 1)

    def test_cli_limit_has_distinct_nonzero_exit(self):
        self.chunks = [OUT + DONE]
        with patch.object(usbmon.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(usbmon.main([
                "--usb-path", USB_PATH, "--output", str(self.output),
                "--seconds", "0.5", "--actuators-isolated", "--max-records", "1",
            ]), 2)

    def test_interrupt_before_ready_preserves_evidence_without_publishing_ready(self):
        self.drop.side_effect = lambda ids: self.handlers[signal.SIGINT](signal.SIGINT, None)
        result = self.capture()
        self.assertEqual(result["status"], "interrupted")
        self.assertFalse((self.output / "ready.json").exists())
        self.assertTrue((self.output / "summary.json").exists())
        self.assertEqual(self.select_count, 0)

    def test_readiness_write_failure_is_explicit_and_never_enters_loop(self):
        original_write = usbmon._write_json

        def write_json(path, value, **kwargs):
            if path.name == "ready.json":
                raise PermissionError()
            return original_write(path, value, **kwargs)

        with patch.object(usbmon, "_write_json", side_effect=write_json), \
                self.assertRaises(usbmon.CaptureError):
            self.capture()
        self.assertFalse((self.output / "ready.json").exists())
        self.assertEqual(self.metadata()["status"], "failed")
        self.assertEqual(self.select_count, 0)

    def test_missing_isolation_or_bad_bounds_fail_before_identity_or_open(self):
        invalid = [{"actuators_isolated": value} for value in (False, "false", 1)]
        invalid += [{"seconds": value} for value in (0, -1, 121, float("inf"), float("nan"), True)]
        invalid += [{key: value} for key, value in (
            ("max_bytes", 0), ("max_bytes", 64 * 1048576 + 1),
            ("max_records", 0), ("max_records", 1000001),
            ("max_line_bytes", 63), ("max_line_bytes", 16385), ("max_pending", 0),
            ("coordinator_stop", 1), ("coordinator_stop", "true"),
        )]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(usbmon.UsbmonError):
                self.capture(**options)
        self.identity.assert_not_called()
        self.open_trace.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_no_clobber_existing_directory_or_broken_symlink(self):
        self.output.mkdir()
        (self.output / "usbmon.txt").write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), b"original")
        self.output = self.root / "symlink"
        self.output.symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError):
            self.capture()
        self.open_trace.assert_not_called()
        self.identity.assert_not_called()

    def test_output_ancestors_are_validated_before_identity_open_or_privilege_drop(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "linked"
        link.symlink_to(target.name, target_is_directory=True)
        dangling = self.root / "dangling-parent"
        dangling.symlink_to("missing")
        file_parent = self.root / "file-parent"
        file_parent.write_bytes(b"unchanged")
        paths = (
            link / "capture", link / ".." / "capture", dangling / "capture", file_parent / "capture",
        )
        self.identity.side_effect = AssertionError("identity accessed for invalid output")
        for path in paths:
            relative = Path(os.path.relpath(path.anchor)) / path.relative_to(path.anchor)
            for output in (path, relative):
                for backend in ("text", "binary"):
                    self.output = output
                    with self.subTest(output=output, backend=backend), \
                            patch.object(Path, "resolve", side_effect=AssertionError("link resolved")), \
                            patch.object(usbmon.binary, "open_monitor") as binary_open, \
                            self.assertRaisesRegex(usbmon.UsbmonError, "parents"):
                        self.capture(backend=backend)
                    binary_open.assert_not_called()
        self.identity.assert_not_called()
        self.open_trace.assert_not_called()
        self.drop.assert_not_called()
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(file_parent.read_bytes(), b"unchanged")
        self.assertFalse((self.root / "missing").exists())
        self.assertFalse((self.root / "capture").exists())

    def test_missing_usb_output_parents_fail_before_runtime_without_creating_them(self):
        self.output = self.root / "missing" / "nested" / "capture"
        for backend in ("text", "binary"):
            with self.subTest(backend=backend), self.assertRaises(FileNotFoundError):
                self.capture(backend=backend)
        self.identity.assert_not_called()
        self.open_trace.assert_not_called()
        self.drop.assert_not_called()
        self.assertFalse((self.root / "missing").exists())

    def test_kernel_output_paths_fail_before_any_metadata_lookup(self):
        for output in ("/dev/new-capture", "/proc/new-capture", "/sys/new-capture"):
            self.output = Path(output)
            with self.subTest(output=output), \
                    patch.object(Path, "lstat", side_effect=AssertionError("kernel metadata accessed")), \
                    patch.object(os.path, "lexists", side_effect=AssertionError("kernel metadata accessed")), \
                    self.assertRaisesRegex(usbmon.UsbmonError, "kernel-interface"):
                self.capture()
        self.identity.assert_not_called()
        self.open_trace.assert_not_called()
        self.drop.assert_not_called()

    def test_cli_reports_unsafe_usb_output_parent_without_starting_capture(self):
        link = self.root / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        self.output = link / "capture"
        with patch.object(usbmon.sys, "stderr", new_callable=io.StringIO) as errors:
            status = usbmon.main([
                "--usb-path", USB_PATH, "--output", str(self.output),
                "--seconds", "0.5", "--actuators-isolated",
            ])
        self.assertEqual(status, 1)
        self.assertIn("symlinks", json.loads(errors.getvalue())["error"])
        self.identity.assert_not_called()
        self.open_trace.assert_not_called()
        self.drop.assert_not_called()
        self.assertFalse((self.root / "capture").exists())

    def test_root_validation_happens_before_identity_or_any_opens(self):
        with patch.object(usbmon.os, "geteuid", return_value=0), \
                patch.object(usbmon.os, "open", side_effect=AssertionError("must not open")):
            with self.assertRaises(usbmon.UsbmonError):
                self.capture()
        self.identity.assert_not_called()
        self.open_trace.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_bad_sudo_environment_fails_before_identity_or_any_opens(self):
        with patch.object(usbmon.os, "geteuid", return_value=0), \
                patch.dict(usbmon.os.environ, {"SUDO_UID": "0", "SUDO_GID": "1000"}, clear=True), \
                patch.object(usbmon.os, "open", side_effect=AssertionError("must not open")), \
                self.assertRaises(usbmon.UsbmonError):
            self.capture(drop_to_invoking_user=True)
        self.identity.assert_not_called()
        self.open_trace.assert_not_called()

    def test_successful_mocked_drop_precedes_all_output_creation(self):
        def dropped(ids):
            self.open_trace.assert_called_once_with(1)
            self.assertFalse(self.output.exists())
            self.assertEqual(ids, (1000, 1000))

        self.drop.side_effect = dropped
        with patch.object(usbmon, "validate_privilege_drop", return_value=(1000, 1000)):
            self.assertTrue(self.capture(drop_to_invoking_user=True)["privileges_dropped"])

    def test_directory_creation_race_does_not_clobber_and_closes_trace(self):
        def race(ids):
            self.output.mkdir()
            (self.output / "usbmon.txt").write_bytes(b"previous evidence")

        self.drop.side_effect = race
        with self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual((self.output / "usbmon.txt").read_bytes(), b"previous evidence")
        self.assertEqual(self.closed, [self.trace_fd])

    def test_drop_failure_closes_trace_and_creates_no_output(self):
        self.drop.side_effect = usbmon.UsbmonError("Privilege drop failed.")
        with patch.object(usbmon, "validate_privilege_drop", return_value=(1000, 1000)), \
                self.assertRaisesRegex(usbmon.UsbmonError, "drop failed"):
            self.capture(drop_to_invoking_user=True)
        self.drop.assert_called_once_with((1000, 1000))
        self.assertEqual(self.closed, [self.trace_fd])
        self.assertFalse(self.output.exists())

    def test_open_failure_does_not_create_output_or_drop(self):
        self.open_trace.side_effect = PermissionError("debugfs unavailable")
        with self.assertRaises(PermissionError):
            self.capture()
        self.assertFalse(self.output.exists())
        self.drop.assert_not_called()

    def test_descriptors_must_match_identity_snapshot_before_opening_trace(self):
        self.descriptors.return_value = DESCRIPTORS[:-1] + b"\x02"
        with self.assertRaisesRegex(usbmon.IdentityError, "descriptors changed"):
            self.capture()
        self.open_trace.assert_not_called()
        self.drop.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cli_failure_is_flushed_without_unrelated_data(self):
        self.open_trace.side_effect = PermissionError(13, "unrelated keyboard contents")
        with patch.object(usbmon.sys, "stderr") as stderr:
            self.assertEqual(usbmon.main([
                "--usb-path", USB_PATH, "--output", str(self.output),
                "--seconds", "0.5", "--actuators-isolated",
            ]), 1)
        stderr.flush.assert_called_once()
        output = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertEqual(json.loads(output)["status"], "failed")
        self.assertIn("errno 13", output)
        self.assertNotIn("unrelated keyboard contents", output)


if __name__ == "__main__":
    unittest.main()
