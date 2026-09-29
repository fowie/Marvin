from contextlib import redirect_stdout
from collections import deque
from copy import deepcopy
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_live as live
from tools import marvin_legacy_poll as poll
from tools import marvin_usbmon_binary as binary
from tests.test_marvin_legacy_client import Clock, frame
from tests.test_marvin_usbmon_binary import header
import marvin_operator


class Bridge:
    def check(self):
        pass

    def bounds(self, seconds, micros):
        return seconds + micros / 1000000, seconds + (micros + 1) / 1000000


def event(payload=b"", *, stamp=100, urb=1, **options):
    options.setdefault("length", len(payload))
    options.setdefault("captured", len(payload))
    raw = bytearray(header(**options))
    struct.pack_into("<Q", raw, 0, urb)
    struct.pack_into("<qi", raw, 16, int(stamp), round((stamp % 1) * 1000000))
    return binary.evidence_frame(bytes(raw), payload, payload_limit=4096)


def incoming(payload, *, stamp=100.01, urb=2):
    return (event(endpoint=0x82, length=4096, data_flag=ord("<"), urb=urb, stamp=stamp - .001)
            + event(payload, endpoint=0x82, event="C", status=0, stamp=stamp, urb=urb))


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.path = self.output / "binary-events.bin"
        self.path.write_bytes(binary.FILE_MAGIC)
        self.reader = live.UsbIngress(self.path, {"busnum": 1, "devnum": 10}, Bridge())
        self.reader.open()
        self.addCleanup(self.reader.close)

    def append(self, data):
        with self.path.open("ab") as stream:
            stream.write(data)

    def test_default_plan_and_guards_are_inert(self):
        with patch.object(live.os, "open", side_effect=AssertionError("must not open")), \
                patch.object(live.subprocess, "run", side_effect=AssertionError("must not launch")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(live.main([]), 0)
            for flags in ({}, {"actuators_isolated": True}, {"unprivileged_usbmon": True},
                          {"actuators_isolated": 1, "unprivileged_usbmon": True}):
                with self.assertRaises(ValueError):
                    live.run_live(self.output / "new", expected_physical_port="1-3", **flags)
        self.assertEqual(json.loads(output.getvalue())["maximum_application_bytes"], 50)
        for options in ({"max_requests": 6}, {"interval": .9}, {"duration": 31},
                        {"first_sequence": 65534}):
            with self.assertRaises(ValueError):
                live.live_plan(**options)

    def test_full_payload_and_fragmented_publication_match_split_coalesced_tty_reads(self):
        data = bytes(range(144))
        raw = incoming(data[:64], stamp=100.01) + incoming(data[64:], stamp=100.02, urb=3)
        self.append(raw[:23])
        self.reader.pump()
        self.assertIsNone(self.reader.match(data[:1]))
        self.append(raw[23:])
        self.reader.pump()
        first = self.reader.match(data[:10])
        rest = self.reader.match(data[10:])
        self.assertEqual(first.data + rest.data, data)
        self.assertEqual(first.started_at, 100.01)
        self.assertEqual(rest.started_at, 100.01)
        self.assertEqual(rest.ended_at, 100.020001)
        self.reader.finish(0)
        self.assertEqual(self.reader.rx_consumed, 144)

    def test_configured_ingress_correlates_past_8k_and_retains_cleanup(self):
        path = self.output / "continuous-operator.bin"
        path.write_bytes(binary.FILE_MAGIC)
        reader = live.UsbIngress(
            path, {"busnum": 1, "devnum": 10}, Bridge(),
            max_bytes=64 * 1024, max_records=1000,
            max_rx_bytes=16 * 1024)
        reader.open()
        try:
            for index in range(9):
                payload = bytes([index]) * 1024
                with path.open("ab") as stream:
                    stream.write(incoming(
                        payload, stamp=100 + index / 100, urb=10 + index))
                reader.pump()
                self.assertEqual(reader.match(payload).data, payload)
            left, right = socket.socketpair()
            node = f"/proc/self/fd/{left.fileno()}"
            transport = marvin_operator._OperatorTransport(
                "unused", {"tty": node}, self.output, reader,
                guard=lambda: None,
                plan=marvin_operator.zero._Limits(
                    max_rx_bytes=16 * 1024))
            transport.fd = left.fileno()
            transport.opened = True
            info = os.fstat(transport.fd)
            transport.node_stat = (
                info.st_dev, info.st_ino, info.st_rdev)
            transport.journal = io.BytesIO()
            transport.serial_bytes = reader.rx_consumed
            cleanup = marvin_operator._frame(
                65535, marvin_operator.RAW_PWM_COMMAND,
                marvin_operator.pilot.ZERO_PWM)
            with patch.object(
                    marvin_operator.marvin_session, "check_identity"):
                self.assertEqual(
                    transport.submit(
                        cleanup, deadline=1e12, mandatory=True),
                    len(cleanup))
            self.assertEqual(right.recv(len(cleanup)), cleanup)
            with path.open("ab") as stream:
                stream.write(
                    event(cleanup, endpoint=0x03, status=-115, urb=100)
                    + event(
                        endpoint=0x03, event="C", status=0,
                        length=len(cleanup), captured=0, data_flag=ord(">"),
                        urb=100))
            reader.pump()
            reader.finish(len(cleanup))
            self.assertEqual(reader.rx_bytes, 9 * 1024)
            self.assertEqual(reader.rx_consumed, reader.rx_bytes)
            self.assertEqual(reader.completed_tx, len(cleanup))
            self.assertFalse(reader.rx)
            left.close()
            right.close()
        finally:
            reader.close()

    def test_usb_faults_stop_without_discarding_original_evidence(self):
        cases = [
            (incoming(b"abcd"), b"abce", "disagree"),
            (event(b"x" * 32, endpoint=0x82, event="C", status=0,
                   length=144, captured=144), None, "Inconsistent"),
            (incoming(b"a", stamp=101) + incoming(b"b", stamp=100, urb=3), None, "regressed"),
            (event(b"x"), None, "Unexpected"),
            (event(event="E", status=-32, data_flag=ord("E")), None, "submission error"),
        ]
        for index, (data, match, message) in enumerate(cases):
            path = self.output / f"fault-{index}.bin"
            path.write_bytes(binary.FILE_MAGIC + data)
            reader = live.UsbIngress(path, {"busnum": 1, "devnum": 10}, Bridge())
            reader.open()
            try:
                with self.assertRaisesRegex((OSError, binary.BinaryError), message):
                    reader.pump()
                    if match is not None:
                        reader.match(match)
                self.assertEqual(path.read_bytes(), binary.FILE_MAGIC + data)
            finally:
                reader.close()
        self.append(event(endpoint=0x82, length=4096, captured=0, data_flag=ord("<")) +
                    event(b"x" * 32, endpoint=0x82, event="C", status=0, length=144, captured=144))
        with self.assertRaisesRegex(binary.BinaryError, "Inconsistent"):
            self.reader.pump()

    def test_clock_interval_includes_sampling_and_microsecond_truncation_and_rejects_jumps(self):
        clock = live.IngressClock()
        clock.fd = 999
        with patch.object(live.os, "read", side_effect=BlockingIOError), \
                patch.object(live.time, "monotonic_ns", side_effect=[1000, 1100, 2000, 2100]), \
                patch.object(live.time, "time_ns", side_effect=[1001000, 1002000]):
            clock.check()
            start, end = clock.bounds(0, 1002)
        self.assertLessEqual(start, 2000 / 10**9)
        self.assertGreaterEqual(end, 3100 / 10**9)
        with patch.object(live.os, "read", side_effect=OSError(errno.ECANCELED, "clock changed")):
            with self.assertRaises(OSError):
                clock.check()
        with patch.object(live.os, "read", side_effect=BlockingIOError), \
                patch.object(live.time, "monotonic_ns", side_effect=[3000, 3100]), \
                patch.object(live.time, "time_ns", return_value=2003000):
            with self.assertRaisesRegex(OSError, "offset changed"):
                clock.check()

    def test_one_syscall_write_uncertainty_same_owner_close_and_eof(self):
        transport = live.LiveTransport("mock", {"tty": "mock"}, self.output, self.reader,
                                       guard=Mock(), plan=live.live_plan())
        transport.fd = 999
        transport.journal = io.BytesIO()
        transport._check = Mock()
        transport._read_serial = Mock(return_value=None)
        request = live.read_raw_data_request(512)
        with patch.object(live.os, "write", return_value=3) as writing:
            self.assertEqual(transport.write(request, deadline=1e12), 3)
            writing.assert_called_once_with(999, request)
            with self.assertRaises(OSError):
                transport.write(request, deadline=1e12)
            writing.assert_called_once()
        correlated = Mock()
        transport._check.reset_mock()
        transport._check.side_effect = OSError(
            "slow host identity must not run in response window")
        transport._check_response_window = Mock()
        transport._read_serial = Mock(return_value=b"response")
        transport.ingress.match = Mock(return_value=correlated)
        transport.event = Mock()
        self.assertIs(
            transport.read_response(512, deadline=1e12), correlated)
        transport._check.assert_not_called()
        self.assertEqual(transport._check_response_window.call_count, 2)
        transport.owner = (-1, live.current_thread())
        with patch.object(live.os, "close") as closing:
            with self.assertRaisesRegex(OSError, "constructing"):
                transport.close(deadline=1e12)
            closing.assert_not_called()
        transport.owner = (os.getpid(), live.current_thread())
        transport.event = Mock(side_effect=OSError("recorder failed"))
        with patch.object(live.os, "close") as closing:
            with self.assertRaises(OSError):
                transport.close(deadline=1e12)
            closing.assert_called_once_with(999)
        self.assertIsNone(transport.fd)
        transport._read_serial = live.LiveTransport._read_serial.__get__(transport)
        transport.fd = 998
        with patch.object(live.select, "select", return_value=([998], [], [])), \
                patch.object(live.os, "read", return_value=b""):
            with self.assertRaisesRegex(OSError, "EOF/disconnect"):
                transport._read_serial(512)

    def test_production_adapter_collects_then_requires_explicit_fresh_session(self):
        clock = Clock()
        clock.now = 100
        plan = live.live_plan(max_requests=2)
        transport = live.LiveTransport("mock", {"tty": "mock"}, self.output, self.reader,
                                       guard=Mock(), plan=plan)
        transport._check = Mock()
        pending = deque()
        journal = self.output / "adapter.jsonl"

        def start(**kwargs):
            transport.fd = 999
            transport.journal = journal.open("wb")
            transport.opened = True
            return transport.token

        def write(fd, raw):
            self.assertEqual(fd, 999)
            reply = frame(bytes(134), sequence=live.decode_packet(raw).sequence, command=0)
            stamp = clock.now + .01
            self.append(event(raw, stamp=stamp - .005) +
                        event(event="C", data_flag=ord(">"), length=10, status=0, stamp=stamp - .004) +
                        incoming(reply, stamp=stamp))
            pending.append(reply)
            return len(raw)

        def read(size):
            if not pending:
                return None
            clock.now += .02
            data = pending.popleft()
            transport.serial_bytes += len(data)
            transport.event("serial_rx", raw_hex=data.hex())
            return data

        transport.revalidate = Mock(side_effect=start)
        transport._read_serial = Mock(side_effect=read)
        with patch.object(live.time, "monotonic", clock), \
                patch.object(live.time, "sleep", side_effect=lambda seconds: setattr(clock, "now", clock.now + seconds)), \
                patch.object(live.os, "write", side_effect=write), \
                patch.object(live.os, "close") as closing:
            result = poll.collect(transport, self.output / "poll.jsonl", ownership_key=b"mock",
                                  expected_identity=transport.token, plan=plan, evidence_kind="synthetic",
                                  clock=clock, wait=transport.wait)
            self.assertEqual(result.report["status"], "complete")
            self.assertEqual(result.report["accepted_tx_bytes"], 20)
            self.assertEqual(result.report["delivered_candidates"], 2)
            transport.revalidate.assert_called_once()
            closing.assert_called_once_with(999)
            with self.assertRaises(OSError):
                transport.write(live.read_raw_data_request(514), deadline=200)
        self.reader.finish(20)
        self.assertEqual(sum(row["event"] == "close_completed"
                             for row in map(json.loads, journal.read_text().splitlines())), 1)

    def test_extended_binary_getx_budget_retains_full_bytes_and_default_stays_32(self):
        expected = bytes(range(144))

        def ioctl(fd, command, request):
            hdr, data, limit = struct.unpack("<QQQ", request)
            ctypes.memmove(hdr, header(endpoint=0x82, length=144, captured=144), 64)
            ctypes.memmove(data, expected[:limit], min(limit, len(expected)))

        with patch.object(binary.fcntl, "ioctl", side_effect=ioctl):
            self.assertEqual(binary.read_event(999)[1], expected[:32])
            raw, full = binary.read_event(999, payload_limit=4096)
            self.assertEqual(full, expected)
            self.assertEqual(len(binary.evidence_frame(raw, full, payload_limit=4096)), 210)
            for limit in (True, 31, 4097):
                with self.assertRaises(binary.BinaryError):
                    binary.read_event(999, payload_limit=limit)

    def test_raw_open_applies_settings_without_flush_and_failure_closes_once(self):
        from tools import marvin_session
        node = self.output / "mock-tty"
        node.touch()
        baseline = {"tty": str(node)}
        clock = Clock()
        clock.now = 100
        real_open, real_fstat = os.open, os.fstat
        for rejected in (False, True):
            directory = self.output / str(rejected)
            directory.mkdir()
            transport = live.LiveTransport(str(node), baseline, directory, self.reader,
                                           guard=Mock(), plan=live.live_plan())
            transport._read_serial = Mock(return_value=None)
            attrs = [0, 0, 0, 0, 0, 0, [bytes(1)] * 32]

            def apply(fd, mode, settings):
                self.assertEqual(mode, live.termios.TCSANOW)
                attrs[:] = deepcopy(settings)
                if rejected:
                    attrs[4] = live.termios.B9600

            with patch.object(live.time, "monotonic", clock), \
                    patch.object(live.time, "sleep", side_effect=lambda seconds: setattr(clock, "now", clock.now + seconds)), \
                    patch.object(marvin_session, "preflight", return_value=baseline), \
                    patch.object(marvin_session, "check_identity") as identity, \
                    patch.object(live.os, "open", side_effect=lambda path, *a: 999 if path == str(node) else real_open(path, *a)), \
                    patch.object(live.os, "fstat", side_effect=lambda fd: node.stat() if fd == 999 else real_fstat(fd)), \
                    patch.object(live.fcntl, "flock") as flock, \
                    patch.object(live.fcntl, "ioctl", side_effect=lambda fd, op, *a: bytes(4)) as ioctl, \
                    patch.object(live.termios, "tcgetattr", side_effect=lambda fd: deepcopy(attrs)), \
                    patch.object(live.termios, "tcsetattr", side_effect=apply), \
                    patch.object(live.termios, "tcflush") as flushing, \
                    patch.object(live.subprocess, "run", return_value=Mock(
                        returncode=0, stdout=str(os.getpid()), stderr=str(node) + ":")), \
                    patch.object(live.os, "close") as closing:
                try:
                    if rejected:
                        with self.assertRaisesRegex(OSError, "approved non-baud"):
                            transport.revalidate(deadline=200)
                    else:
                        self.assertEqual(transport.revalidate(deadline=200), transport.token)
                        identity.reset_mock()
                        transport._check_response_window(200)
                        identity.assert_not_called()
                        self.assertEqual(attrs[:6], [0, 0, live.termios.CS8 | live.termios.CREAD |
                                                    live.termios.CLOCAL, 0, live.termios.B57600,
                                                    live.termios.B57600])
                        self.assertEqual(attrs[6][live.termios.VMIN], 0)
                    flushing.assert_not_called()
                    flock.assert_called_once()
                    ioctl.assert_any_call(999, live.termios.TIOCEXCL)
                    ioctl.assert_any_call(999, live.termios.TIOCMBIC,
                                          struct.pack("I", live.termios.TIOCM_DTR | live.termios.TIOCM_RTS))
                finally:
                    transport.close(deadline=200)
                    transport.close(deadline=200)
                    closing.assert_called_once_with(999)

    def test_linux_baud_normalization_preserves_all_other_checks_and_raw_readback(self):
        requested = [0, 0, 2224, 0, live.termios.B57600, live.termios.B57600, [b"\0"] * 32]
        actual = deepcopy(requested)
        # Actual Linux/Python 3.14 host-PTY readback, not a robot measurement.
        actual[2] = 268507313
        live._validate_termios(requested, actual)
        self.assertEqual(live._termios_evidence(actual)["fields"][2], 268507313)
        self.assertEqual(live._termios_evidence(actual)["control_chars"][0], {"bytes_hex": "00"})
        for index, value in ((0, live.termios.IXON), (1, live.termios.OPOST),
                             (2, actual[2] | live.termios.PARENB),
                             (2, actual[2] | live.termios.CSTOPB),
                             (2, actual[2] | live.termios.CRTSCTS),
                             (2, actual[2] & ~live.termios.CREAD),
                             (2, actual[2] & ~live.termios.CLOCAL),
                             (3, live.termios.ECHO), (4, live.termios.B9600), (5, live.termios.B9600)):
            changed = deepcopy(actual)
            changed[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(OSError):
                live._validate_termios(requested, changed)
        changed = deepcopy(actual)
        changed[6][live.termios.VMIN] = 1
        with self.assertRaises(OSError):
            live._validate_termios(requested, changed)
        with patch.object(live.termios, "CIBAUD", None):
            with self.assertRaisesRegex(OSError, "unavailable"):
                live._validate_termios(requested, actual)


if __name__ == "__main__":
    unittest.main()
