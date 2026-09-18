"""Offline fixed disconnected-load GetLog tests."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_disconnected_get_log as get_log
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon
from tests.test_marvin_legacy_client import frame
from tests import test_marvin_session as session_tests


DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_GET_LOG_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.DISCONNECTED_GET_LOG_FLAGS]


class DisconnectedGetLogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".get-log-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_exact_frame_sha_no_knobs_and_consent_before_hardware(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(get_log.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"],
                         ["53040c0c00000071d045"])
        self.assertEqual(plan["request_sha256"],
                         "e617b3bc6fd672b38f4c8f963b4c03d7726ac953f48eaee1209cf26b57e41182")
        self.assertEqual(hashlib.sha256(get_log.REQUEST).hexdigest(), plan["request_sha256"])
        self.assertEqual(plan["response_shape"],
                         {"command": 12, "response_field": 128, "payload_bytes": 32})
        self.assertEqual((plan["maximum_writes"], plan["maximum_application_bytes"]), (1, 10))
        self.assertFalse(plan["automatic_retries"])
        self.assertFalse(plan["automatic_reconnect"])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            for knob in ("command", "payload", "sequence", "retry", "count"):
                with self.assertRaises(SystemExit):
                    get_log.main(FLAGS + ["--" + knob, "1"])
        with patch.object(session, "preflight", side_effect=AssertionError("no preflight")), \
                redirect_stderr(io.StringIO()):
            for name in consent.DISCONNECTED_GET_LOG_FLAGS:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    get_log.run_get_log(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(DECLARATIONS | {name: False}))
            with self.assertRaises(ValueError):
                get_log.run_get_log(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS | {"disconnected_load_zero_one_order_diagnostic": True}))

    def test_coordinator_and_recorder_forward_read_only_scope(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.DISCONNECTED_GET_LOG_SCOPE)
        self.assertEqual(result["load_scope"], "MOTOR_POWER_PLUGS_DISCONNECTED")
        self.assertEqual(result["probe_name"], "DisconnectedLoadGetLog")
        self.assertFalse(result["unknown_command_authorized"])
        self.assertTrue(result["fixed_get_log_authorized"])
        self.assertEqual(result["immutable_application_transcript_hex"], [get_log.REQUEST.hex()])
        self.assertEqual(result["requested_application_bytes"], 10)
        for flag in FLAGS:
            self.assertIn(flag, command)
        get_log._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        get_log._validate_capture(capture.call_args.kwargs)

    def test_single_write_exact_response_and_failure_never_retries(self):
        for status, payload_size, succeeds in ((0x80, 32, True), (0x82, 32, False), (0x80, 31, False)):
            transport = Mock(token="token", writes=0, serial_bytes=payload_size + 10)
            transport.revalidate.return_value = "token"

            def write(raw, *, deadline):
                transport.writes += 1
                self.assertEqual(raw, get_log.REQUEST)
                return len(raw)

            def observe(_, response, **kwargs):
                response.feed(Mock(
                    data=frame(bytes(payload_size), command=0x0C, status=status,
                               sequence=get_log.SEQUENCE),
                    started_at=.1, ended_at=.2), .2)
                response.finish(.2)
                if response.candidates != 1:
                    raise OSError("response_not_observed")

            transport.write.side_effect = write
            report = {}
            context = (self.subTest(status=status, payload_size=payload_size),
                       patch.object(get_log.zero, "_observe_response", side_effect=observe),
                       redirect_stderr(io.StringIO()))
            with context[0], context[1], context[2]:
                if succeeds:
                    get_log._observe(transport, report, clock=lambda: 0)
                    self.assertEqual(report["status"], get_log.SUCCESS)
                    self.assertEqual(len(report["raw_payload_hex"]), 64)
                else:
                    with self.assertRaises(OSError):
                        get_log._observe(transport, report, clock=lambda: 0)
            self.assertEqual(transport.write.call_count, 1)
            self.assertEqual(transport.writes, 1)
            transport.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
