from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_zero as zero
from tools import marvin_legacy_protocol as protocol
from tools import marvin_tx_policy
from tools.marvin_legacy_client import Received
from tools.marvin_legacy_live import LiveTransport
from tests.test_marvin_legacy_client import Clock, frame


def reply(payload=b"", **options):
    return frame(payload, command=options.pop("command", 0x11),
                 sequence=options.pop("sequence", 1024), **options)


class ZeroTests(unittest.TestCase):
    def test_fixed_plan_literal_consent_and_no_general_command_knobs(self):
        raw = zero.ZERO_TRANSCRIPT[0]
        self.assertEqual(raw.hex(), "5300041100040000000000fdc145")
        packet = protocol.decode_packet(raw)
        self.assertEqual((packet.sequence, packet.command, packet.payload), (1024, 17, bytes(4)))
        with patch.object(zero.os, "open", side_effect=AssertionError("must not open")), \
                patch.object(zero.subprocess, "run", side_effect=AssertionError("must not launch")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(zero.main([]), 0)
            for flag in ("actuators_isolated", "authorize_unvalidated_zero_velocity", "unprivileged_usbmon"):
                for value in (False, None, 0, 1, "true"):
                    options = dict(actuators_isolated=True, authorize_unvalidated_zero_velocity=True,
                                   unprivileged_usbmon=True)
                    options[flag] = value
                    with self.subTest(flag=flag, value=value), self.assertRaises(ValueError):
                        zero.run_zero("unused", expected_physical_port="1-3", **options)
            for knob in ("--sequence", "--value", "--opcode", "--duration", "--retry"):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    zero.main([knob, "1"])

    def test_only_one_exact_zero_at_actual_write_boundary_and_getters_still_reject_it(self):
        ingress = Mock(rx=[], rx_bytes=0, pending=b"", expected_tx=[], outstanding_tx={})
        transport = zero._ZeroTransport("mock", {"tty": "mock"}, Path("."),
                                        ingress, guard=Mock(), plan=zero._Limits())
        transport.fd = 999
        transport._check = Mock()
        transport._read_serial = Mock(return_value=None)
        transport.event = Mock()
        raw = zero.ZERO_TRANSCRIPT[0]
        bad = (bytearray(raw), raw[:-1], raw + raw, protocol.read_raw_data_request(1024),
               raw[:2] + b"\x05" + raw[3:], raw[:7] + b"\x01" + raw[8:])
        with patch.object(zero.os, "write", return_value=14) as writing:
            for data in bad:
                with self.assertRaises(OSError):
                    transport.write(data, deadline=1e12)
            writing.assert_not_called()
            self.assertEqual(transport.write(raw, deadline=1e12), 14)
            with self.assertRaises(OSError):
                transport.write(raw, deadline=1e12)
            writing.assert_called_once_with(999, raw)
        readonly = LiveTransport("mock", {"tty": "mock"}, Path("."), ingress,
                                 guard=Mock(), plan=zero._Limits())
        readonly._check = Mock()
        readonly._submit_once = Mock()
        with self.assertRaises(OSError):
            readonly.write(raw, deadline=1e12)
        readonly._submit_once.assert_not_called()
        with self.assertRaises(ValueError):
            marvin_tx_policy.validate_transmit_stream(raw, profile="legacy")
        with self.assertRaises(ValueError):
            protocol._empty_request(0x11, 1024)

    def evidence(self):
        recorder = Mock()
        observation = zero._ResponseEvidence(recorder)
        observation.submitted_at = 10
        observation.deadline = 13
        return observation

    def test_opaque_reply_shapes_fragmentation_never_become_ack_or_stop(self):
        for payload in (b"", b"opaque!", bytes(134)):
            observation = self.evidence()
            raw = reply(payload)
            observation.feed(Received(raw[:6], 10.1, 10.11), 10.12)
            observation.feed(Received(raw[6:], 10.2, 10.21), 10.22)
            observation.finish(13)
            self.assertEqual(observation.candidates, 1)
            row, = observation.events
            self.assertEqual(row["stream"]["raw_hex"], raw.hex())
            self.assertEqual((row["started_at"], row["ended_at"]), (10.1, 10.21))
            self.assertEqual(row["labels"], ["unverified_shape_and_semantics", "correlated_command_sequence_only"])
            self.assertEqual(row["application_acknowledgment"], "not_established")
            self.assertEqual(row["physical_stop"], "not_established")

    def test_error_opaque_extra_partial_and_ambiguous_evidence_is_retained_and_fails(self):
        for raw, start, now, label in (
            (reply(command=0), 10.1, 10.2, "unexpected_command_or_sequence"),
            (reply(sequence=1023), 10.1, 10.2, "unexpected_command_or_sequence"),
            (reply(status=0x81), 10.1, 10.2, "uninterpreted_non80_status"),
            (reply(), 10, 10.2, "prewrite_or_ambiguous"),
            (reply(), 10.1, 13, "late"),
            (b"noise", 10.1, 10.2, "noise"),
            (reply()[:-1] + b"X", 10.1, 10.2, "error"),
            (reply() + reply(b"extra"), 10.1, 10.2, "additional_frame"),
        ):
            observation = self.evidence()
            with self.subTest(label=label), self.assertRaises(OSError):
                observation.feed(Received(raw, start, 10.15), now)
            self.assertEqual(b"".join(bytes.fromhex(row["stream"]["raw_hex"]) for row in observation.events), raw)
            self.assertTrue(any(label in row["labels"] for row in observation.events))
        observation = self.evidence()
        observation.feed(Received(reply()[:8], 10.1, 10.2), 10.3)
        with self.assertRaises(OSError):
            observation.finish(13)
        self.assertIn("partial", observation.events[-1]["labels"])
        self.assertEqual(observation.events[-1]["stream"]["raw_hex"], reply()[:8].hex())

    def fixture(self, *, data=None, count=14, preflight_error=None, close_error=None):
        clock = Clock()
        transport = Mock(token=b"mock", fd=999, serial_bytes=0, writes=0)
        transport.revalidate.return_value = transport.token
        if preflight_error:
            transport.revalidate.side_effect = preflight_error
        transport.close.side_effect = close_error
        waiting = []
        def write(raw, **options):
            self.assertEqual(raw, zero.ZERO_TRANSCRIPT[0])
            transport.writes += 1
            clock.now += .01
            if data is not None:
                waiting.append(data)
            return count
        transport.write.side_effect = write
        def read(*args, **options):
            raw = waiting.pop()
            clock.now += .01
            transport.serial_bytes += len(raw)
            return Received(raw, clock.now - .001, clock.now)
        transport.read_response.side_effect = read
        def select(*args):
            clock.now += args[3]
            return ([999] if waiting else [], [], [])
        return transport, clock, select

    def test_bounded_observation_success_no_response_and_uncertainty_all_close_once(self):
        for data, count, expected in ((reply(b"unknown"), 14, None), (None, 14, "response_not_observed"),
                                      (None, 3, "Short zero-command write")):
            transport, clock, selecting = self.fixture(data=data, count=count)
            report = {}
            with patch.object(zero.select, "select", side_effect=selecting):
                if expected:
                    with self.assertRaisesRegex(OSError, expected):
                        zero._observe(transport, report, clock=clock)
                else:
                    zero._observe(transport, report, clock=clock)
                    self.assertEqual(report["status"], "observation_complete_unverified")
                    self.assertGreaterEqual(clock.now, report["submitted_at"] + 3)
            transport.close.assert_called_once()
            transport.write.assert_called_once()
            self.assertEqual((report["accepted_tx_bytes"], report["uncertain_tx_bytes"]), (count, 14 - count))
            self.assertEqual(report["physical_stop"], "not_established")
        transport, clock, selecting = self.fixture(preflight_error=OSError("prewrite input"),
                                                   close_error=OSError("close failed"))
        report = {}
        with self.assertRaisesRegex(OSError, "prewrite input"):
            zero._observe(transport, report, clock=clock)
        transport.write.assert_not_called()
        transport.close.assert_called_once()
        self.assertEqual(report["write_status"], "suppressed_before_submission")
        self.assertEqual(report["uncertain_tx_bytes"], 0)
        self.assertEqual(report["cleanup_errors"], ["close failed"])


if __name__ == "__main__":
    unittest.main()
