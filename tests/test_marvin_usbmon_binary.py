import ctypes
import errno
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_usbmon as usbmon
from tools import marvin_usbmon_binary as binary
from tools import marvin_session


def header(*, event="S", transfer=3, endpoint=3, device=10, length=1,
           captured=1, data_flag=0, status=-115, setup_flag=ord("-"), setup=b"\0" * 8,
           bus=1):
    # Populate ABI offsets independently of the production HEADER format.
    value = bytearray(64)
    struct.pack_into("<Q", value, 0, 0x1234)
    struct.pack_into("<BBBBHBB", value, 8, ord(event), transfer, endpoint, device,
                     bus, setup_flag, data_flag)
    struct.pack_into("<qiiII", value, 16, 1788800000, 123456, status, length, captured)
    value[40:48] = setup
    struct.pack_into("<i", value, 48, 10)
    return bytes(value)


class BinaryTests(unittest.TestCase):
    def test_binary_zero_data_flag_normalizes_to_text_equals(self):
        payload = b"abc"
        raw = header(length=3, captured=3, data_flag=0)
        normalized = binary.to_text(raw, payload)
        self.assertIn(b" = 616263", normalized)
        self.assertEqual(usbmon.parse_record(normalized).payload, payload)
        with self.assertRaisesRegex(binary.BinaryError, "data flag"):
            binary.to_text(header(length=3, captured=3, data_flag=ord("=")), payload)

    def test_offline_header_bus_range_is_wider_than_live_character_device_minors(self):
        for bus in (1, 127, 128, 65535):
            raw = header(bus=bus)
            self.assertEqual(binary.address(raw), (bus, 10))
            self.assertEqual(usbmon.parse_record(binary.to_text(raw, b"\r")).busnum, bus)
        with patch.object(binary.os, "open") as opening:
            for bus in (0, 128, 65535):
                with self.assertRaisesRegex(binary.BinaryError, "buses 1..127"):
                    binary.open_monitor(bus)
            opening.assert_not_called()

    def test_getx_uses_len_cap_at_36_not_length_at_32_or_setup_at_40(self):
        # Offsets from docs.kernel.org/usb/usbmon.html, not the production Struct.
        for length, captured in ((64, 0), (64, 7), (100, 100)):
            raw_header = header(endpoint=0x82, length=length, captured=captured,
                                data_flag=ord("<") if captured == 0 else 0,
                                setup=b"\xff" * 8)
            expected = b"a" * min(captured, 32)

            def ioctl(fd, command, request):
                hdr, data, limit = struct.unpack("<QQQ", request)
                self.assertEqual(limit, 32)
                ctypes.memmove(hdr, raw_header, 64)
                ctypes.memmove(data, expected, len(expected))

            with self.subTest(length=length, captured=captured), \
                    patch.object(binary.fcntl, "ioctl", side_effect=ioctl):
                actual_header, payload = binary.read_event(99)
                self.assertEqual(payload, expected)
                parsed = usbmon.parse_record(binary.to_text(actual_header, payload))
                self.assertEqual(parsed.length, length)
                self.assertEqual(parsed.payload, expected)

    def test_getx_copies_header_and_only_bounded_payload_from_pointers(self):
        raw_header = header(length=100, captured=100)

        def ioctl(fd, command, request):
            self.assertEqual(command, 0x4018920A)
            hdr, data, limit = struct.unpack("<QQQ", request)
            self.assertEqual(limit, 32)
            ctypes.memmove(hdr, raw_header, 64)
            ctypes.memmove(data, b"a" * 32, 32)
            return 0

        with patch.object(binary.fcntl, "ioctl", side_effect=ioctl):
            actual_header, payload = binary.read_event(99)
        self.assertEqual(actual_header, raw_header)
        self.assertEqual(payload, b"a" * 32)
        self.assertEqual(binary.address(actual_header), (1, 10))
        normalized = usbmon.parse_record(binary.to_text(actual_header, payload))
        self.assertEqual(normalized.length, 100)
        self.assertEqual(len(normalized.payload), 32)

    def test_control_setup_payload_and_status_normalize_correctly(self):
        setup = bytes.fromhex("2120000000000700")
        raw = header(transfer=2, endpoint=0, length=7, captured=7,
                     setup_flag=0, setup=setup)
        line = binary.to_text(raw, bytes.fromhex("00c20100000008"))
        parsed = usbmon.parse_record(line)
        self.assertEqual(parsed.setup, (0x21, 0x20, 0, 0, 7))
        self.assertEqual(parsed.payload.hex(), "00c20100000008")
        self.assertEqual(parsed.timestamp_us, 1788800000123456)

    def test_cancellation_and_missing_payload_flags_are_not_acknowledgments(self):
        for event, flag, status in (("C", ord(">"), 0), ("E", ord("E"), -32),
                                    ("S", ord("D"), -115), ("C", 0, -2)):
            with self.subTest(event=event, flag=flag):
                raw = header(event=event, transfer=1, endpoint=0x81,
                             captured=0, length=0, data_flag=flag, status=status)
                parsed = usbmon.parse_record(binary.to_text(raw, b""))
                self.assertEqual(parsed.status, status)
                self.assertEqual(parsed.status_details, (10,))

    def test_invalid_target_events_fail_explicitly(self):
        examples = [
            (header(transfer=0), b"\r"),
            (header(event="@"), b"\r"),
            (header(endpoint=0x71), b"\r"),
            (header(data_flag=ord("X")), b"\r"),
            (header(data_flag=ord(">")), b"\r"),
            (header(captured=2), b"\r"),
            (b"too short", b""),
        ]
        for raw, payload in examples:
            with self.subTest(raw=raw), self.assertRaises(binary.BinaryError):
                binary.to_text(raw, payload)

    def test_captured_length_cannot_exceed_transfer_length_even_with_bounded_prefix(self):
        for length, captured in ((0, 1), (31, 32), (32, 33), (64, 100), (2**31 - 1, 2**32 - 1)):
            with self.subTest(length=length, captured=captured):
                raw = header(length=length, captured=captured)
                payload = b"x" * min(captured, binary.PAYLOAD_LIMIT)
                with self.assertRaisesRegex(binary.BinaryError, "Inconsistent"):
                    binary.to_text(raw, payload)
        for length, captured in ((0, 0), (31, 31), (32, 32), (64, 64), (100, 64)):
            with self.subTest(length=length, captured=captured):
                payload = b"x" * min(captured, binary.PAYLOAD_LIMIT)
                record = usbmon.parse_record(binary.to_text(header(length=length, captured=captured), payload))
                self.assertEqual(record.length, length)
                self.assertEqual(record.payload, payload)

    def test_stats_ioctl_decodes_queue_and_drop_counts(self):
        def ioctl(fd, command, buffer, mutate):
            self.assertEqual(command, 0x80089203)
            self.assertTrue(mutate)
            buffer[:] = struct.pack("<II", 3, 4)
        with patch.object(binary.fcntl, "ioctl", side_effect=ioctl):
            self.assertEqual(binary.read_stats(99), {"queued": 3, "dropped": 4})

    def test_permission_failure_identifies_monitor_without_changing_policy(self):
        with patch.object(binary.os, "open", side_effect=PermissionError(errno.EPERM, "denied")):
            with self.assertRaisesRegex(binary.BinaryError, r"/dev/usbmon1.*errno 1"):
                binary.open_monitor(1)
        with patch.object(binary.os, "open") as opened:
            for bus in (0, -1, 128, "../0", True):
                with self.assertRaises(binary.BinaryError):
                    binary.open_monitor(bus)
            opened.assert_not_called()

    def test_unsupported_host_abi_fails_before_open(self):
        cases = (
            ("darwin", "little", 8, "x86_64", "only on Linux"),
            ("freebsd14", "little", 8, "x86_64", "only on Linux"),
            ("win32", "little", 8, "AMD64", "only on Linux"),
            ("linux", "little", 8, "aarch64", "x86-64"),
            ("linux", "big", 8, "x86_64", "x86-64"),
            ("linux", "little", 4, "x86_64", "x86-64"),
        )
        for host, byteorder, pointer_size, machine, message in cases:
            with self.subTest(host=host, byteorder=byteorder,
                              pointer_size=pointer_size, machine=machine), \
                    patch.object(binary.sys, "platform", host), \
                    patch.object(binary.sys, "byteorder", byteorder), \
                    patch.object(binary.struct, "calcsize", return_value=pointer_size), \
                    patch.object(binary.platform, "machine", return_value=machine), \
                    patch.object(binary.os, "open") as opened:
                with self.assertRaisesRegex(binary.BinaryError, message):
                    binary.open_monitor(1)
                opened.assert_not_called()

    def test_linux_little_endian_x86_64_abi_is_supported(self):
        for machine in ("x86_64", "AMD64"):
            with self.subTest(machine=machine), \
                    patch.object(binary.sys, "platform", "linux"), \
                    patch.object(binary.sys, "byteorder", "little"), \
                    patch.object(binary.struct, "calcsize", return_value=8), \
                    patch.object(binary.platform, "machine", return_value=machine):
                self.assertIsNone(binary.check_abi())

    def test_offline_header_normalization_does_not_require_linux_host(self):
        raw = header()
        with patch.object(binary.sys, "platform", "darwin"), \
                patch.object(binary.os, "open") as opened:
            self.assertEqual(binary.address(raw), (1, 10))
            self.assertEqual(usbmon.parse_record(binary.to_text(raw, b"\r")).payload, b"\r")
            opened.assert_not_called()

    def test_debugfs_failure_names_lockdown_and_supported_alternative(self):
        with patch.object(usbmon.os, "open", side_effect=PermissionError(errno.EPERM, "denied")):
            with self.assertRaisesRegex(usbmon.UsbmonError, "lockdown"):
                usbmon._open_usbmon(1)

    def test_coordinator_surfaces_child_error_instead_of_generic_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "usbmon-stderr.log"
            path.write_text(json.dumps({"error": "Cannot open debugfs: kernel lockdown"}))
            self.assertIn("kernel lockdown", marvin_session.recorder_error(root / "usb", "Not ready"))
            path.write_text("sudo: a password is required\n")
            self.assertIn("password", marvin_session.recorder_error(root / "usb", "Not ready"))
            path.write_text("")
            self.assertIn("empty", marvin_session.recorder_error(root / "usb", "Not ready"))


class BinaryCaptureTests(unittest.TestCase):
    def test_target_filtered_binary_evidence_and_normalized_text(self):
        descriptors = bytes.fromhex("12011001020000405e044444000101020301")
        import hashlib
        identity = {"usb_path": "/fake/sys/1-3.3", "busnum": 1, "devnum": 10,
                    "descriptors_bytes": len(descriptors),
                    "descriptors_sha256": hashlib.sha256(descriptors).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            for dropped, coordinated in ((0, False), (2, False), (0, True), (2, True)):
                with self.subTest(dropped=dropped, coordinated=coordinated):
                    output = Path(directory) / f"capture-{dropped}-{coordinated}"
                    records = [
                        (header(device=3, length=4, captured=4), b"\xde\xad\xbe\xef"),
                        (header(), b"\r"),
                        (header(event="C", captured=0, data_flag=ord(">"), status=0), b""),
                    ]
                    now = [0.0]
                    read_fd, write_fd = os.pipe()

                    def select(fds, writable, exceptional, timeout):
                        now[0] += 0.01 if records or coordinated else timeout
                        if coordinated and not records:
                            (output / usbmon.COORDINATOR_STOP_FILE).write_bytes(b"")
                        return (fds if records else [], [], [])

                    try:
                        with patch.object(usbmon, "read_identity", return_value=identity), \
                             patch.object(usbmon, "_cached_descriptors", return_value=descriptors), \
                             patch.object(binary, "open_monitor", return_value=read_fd), \
                             patch.object(binary, "read_event", side_effect=lambda fd: records.pop(0)), \
                             patch.object(binary, "read_stats", side_effect=[
                                 {"queued": 0, "dropped": 0}, {"queued": 0, "dropped": dropped}]), \
                             patch.object(usbmon.select, "select", side_effect=select), \
                             patch.object(usbmon.time, "monotonic", side_effect=lambda: now[0]):
                            if dropped:
                                with self.assertRaisesRegex(usbmon.CaptureError, "dropped events"):
                                    usbmon.capture("/fake/sys/1-3.3", output, seconds=0.1,
                                                   actuators_isolated=True, backend="binary",
                                                   coordinator_stop=coordinated)
                            else:
                                result = usbmon.capture("/fake/sys/1-3.3", output, seconds=0.1,
                                                        actuators_isolated=True, backend="binary",
                                                        coordinator_stop=coordinated)
                                self.assertEqual(result["status"], "completed")
                                self.assertEqual(result["stop_reason"], "coordinator_stop" if coordinated else "duration")
                        raw = (output / "binary-events.bin").read_bytes()
                        self.assertTrue(raw.startswith(binary.FILE_MAGIC))
                        self.assertNotIn(b"\xde\xad\xbe\xef", raw)
                        self.assertNotIn("deadbeef", (output / "usbmon.txt").read_text())
                        self.assertEqual(len(raw), len(binary.FILE_MAGIC) + 66 * 2 + 1)
                        summary = json.loads((output / "summary.json").read_text())
                        self.assertEqual(summary["pairing"]["matched_completions"], 1)
                    finally:
                        os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
