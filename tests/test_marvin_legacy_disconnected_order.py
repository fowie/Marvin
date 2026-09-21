"""Offline fixed disconnected-load order diagnostic tests."""

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_disconnected_order as order
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon
from tests.test_marvin_legacy_client import frame
from tests import test_marvin_session as session_tests


DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_ORDER_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.DISCONNECTED_ORDER_FLAGS]
PLUS_1000_DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_PLUS_1000_FLAGS, True)
PLUS_1000_FLAGS = [
    "--" + name.replace("_", "-") for name in consent.DISCONNECTED_PLUS_1000_FLAGS]


class DisconnectedOrderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".order-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def transport(self, writes, results=None):
        ingress = Mock(pending=b"", rx=[], rx_bytes=0, expected_tx=deque(), outstanding_tx={})
        transport = order._OrderTransport(
            "mock", {"tty": "mock"}, self.root, ingress, guard=Mock(),
            plan=order.zero._Limits(first_sequence=3072, max_requests=4, interval=0))
        transport.fd = 99
        transport.node_stat = (1, 2, 3)
        transport.journal = Mock()
        transport.journal.fileno.return_value = 88
        transport._check = Mock()
        transport._same_owned_writable_fd = Mock()
        transport._read_serial = Mock(return_value=None)
        transport.event = Mock()
        results = iter(results or ())

        def write(fd, raw):
            writes.append(raw)
            result = next(results, len(raw))
            if isinstance(result, BaseException):
                raise result
            return result

        return transport, write

    def test_exact_crc_order_and_no_knobs(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(order.main([]), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "53000c11000400000000009a0145",
            "53010c1100040001000000ca3845",
            "53020c11000400000000003bcb45",
            "53030c00000000733745",
        ])
        self.assertEqual([len(raw) for raw in order.TRANSCRIPT], [14, 14, 14, 10])
        self.assertEqual(plan["maximum_application_bytes"], 52)
        self.assertFalse(plan["automatic_retries"])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(order.main(FLAGS), 0)
            for knob in ("value", "duration", "sequence", "retry", "count"):
                with self.assertRaises(SystemExit):
                    order.main(FLAGS + ["--" + knob, "1"])

    def test_plus_1000_exact_profile_consent_and_forwarding(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(order.main(PLUS_1000_FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["name"], consent.DISCONNECTED_PLUS_1000_SCOPE)
        self.assertEqual(plan["fixed_left_raw_value"], 1000)
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "53000c11000400000000009a0145",
            "53010c11000400e80300000e6445",
            "53020c11000400000000003bcb45",
            "53030c00000000733745",
        ])
        self.assertEqual(order.transcript_for_scope(consent.DISCONNECTED_PLUS_1000_SCOPE),
                         order.PLUS_1000_TRANSCRIPT)

        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **PLUS_1000_DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.DISCONNECTED_PLUS_1000_SCOPE)
        self.assertEqual(result["probe_name"], "DisconnectedLoadZeroPlus1000OrderDiagnostic")
        self.assertTrue(result["unvalidated_left_plus_1000_order_diagnostic_authorized"])
        self.assertEqual(result["immutable_application_transcript_hex"],
                         [raw.hex() for raw in order.PLUS_1000_TRANSCRIPT])
        for flag in PLUS_1000_FLAGS:
            self.assertIn(flag, command)
        order._validate_capture(
            runner.call_args.kwargs, consent.DISCONNECTED_PLUS_1000_SCOPE)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        order._validate_capture(
            capture.call_args.kwargs, consent.DISCONNECTED_PLUS_1000_SCOPE)
        with redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
            order.run_diagnostic(
                self.root / "unused", expected_physical_port="1-3", run=True,
                **(PLUS_1000_DECLARATIONS
                   | {"authorize_unvalidated_left_plus_1000_order_diagnostic": False}))

    def test_scope_rejects_missing_mixed_or_nonboolean_consent_before_hardware(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no preflight")), \
                redirect_stderr(io.StringIO()):
            for name in consent.DISCONNECTED_ORDER_FLAGS:
                for value in (False, 0, 1, None, "true"):
                    with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                        order.run_diagnostic(
                            self.root / "unused", expected_physical_port="1-3", run=True,
                            **(DECLARATIONS | {name: value}))
            for name in ("encoder_feedback_observation", "powered_left_stop_characterization",
                         "motor_left_connected", "actuators_isolated"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    order.run_diagnostic(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(DECLARATIONS | {name: True}))

    def test_coordinator_and_recorder_forward_scope_and_load(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.DISCONNECTED_ORDER_SCOPE)
        self.assertEqual(result["load_scope"], "MOTOR_POWER_PLUGS_DISCONNECTED")
        self.assertTrue(result["unvalidated_zero_one_order_diagnostic_authorized"])
        self.assertNotIn("unvalidated_left_one_and_zero_authorized", result)
        self.assertEqual(result["immutable_application_transcript_hex"],
                         [raw.hex() for raw in order.TRANSCRIPT])
        self.assertEqual(result["requested_application_bytes"], 52)
        for flag in FLAGS:
            self.assertIn(flag, command)
        order._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        order._validate_capture(capture.call_args.kwargs)

    def test_immediate_order_cleanup_once_and_no_retry(self):
        writes = []
        transport, write = self.transport(writes)
        with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(transport.run_order(deadline=100), [14, 14, 14])
        self.assertEqual(writes, list(order.TRANSCRIPT[:3]))

        for results in ((14, 14, 2), (14, 14, OSError("cleanup fault"))):
            writes = []
            transport, write = self.transport(writes, results)
            with self.subTest(results=results), patch.object(os, "write", side_effect=write), \
                    patch.object(os, "fsync"), redirect_stderr(io.StringIO()), \
                    self.assertRaises(OSError):
                transport.run_order(deadline=100)
            self.assertEqual(writes, list(order.TRANSCRIPT[:3]))
            self.assertEqual(writes.count(order.CLEANUP_ZERO), 1)

        writes = []
        transport, write = self.transport(writes, (14, 2))
        with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                redirect_stderr(io.StringIO()), self.assertRaises(OSError):
            transport.run_order(deadline=100)
        self.assertEqual(writes, [order.INITIAL_ZERO, order.LEFT_ONE])

        writes = []
        transport, write = self.transport(writes)
        transport.transcript = order.PLUS_1000_TRANSCRIPT
        with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                redirect_stderr(io.StringIO()):
            transport.run_order(deadline=100)
        self.assertEqual(writes, list(order.PLUS_1000_TRANSCRIPT[:3]))

    def test_raw_setter_statuses_gate_getter_without_decoding_82(self):
        packets = b"".join((
            frame(b"", command=0x11, status=0x80, sequence=3072),
            frame(b"", command=0x11, status=0x82, sequence=3073),
            frame(b"", command=0x11, status=0x80, sequence=3074),
        ))
        transport = Mock()
        transport.fd = 99
        transport.ingress = Mock()
        transport.read.return_value = Mock(data=packets, started_at=1.0, ended_at=1.1)
        report = {}
        ticks = iter((0.0, 0.0, 0.0, 0.6))
        with patch.object(order.select, "select", return_value=([99], [], [])):
            order._collect_setter_responses(
                transport, report, deadline=1.0, clock=lambda: next(ticks, 0.6))
        self.assertEqual(
            [report["setter_responses"][sequence]["response_field_hex"]
             for sequence in (3072, 3073, 3074)],
            ["80", "82", "80"])

        transport = Mock(writes=3, accepted_tx_bytes=42, uncertain_tx_bytes=0,
                         serial_bytes=0, token="token", transcript=order.TRANSCRIPT)
        transport.revalidate.return_value = "token"
        with patch.object(order, "_collect_setter_responses",
                          side_effect=OSError("missing setter response")), \
                redirect_stderr(io.StringIO()), self.assertRaisesRegex(
                    OSError, "missing setter response"):
            order._observe(transport, {})
        transport.submit_getter.assert_not_called()
        transport.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
