"""Offline fixed disconnected-load legacy getter survey tests."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests import test_marvin_session as session_tests
from tests.test_marvin_legacy_client import frame
from tools import marvin_legacy_disconnected_getter_survey as survey
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon


DECLARATIONS = dict.fromkeys(consent.DISCONNECTED_GETTER_SURVEY_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.DISCONNECTED_GETTER_SURVEY_FLAGS]


class DisconnectedGetterSurveyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".getter-survey-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_exact_frames_no_knobs_and_consent_before_hardware(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(survey.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "530b0c0a00000071a745",
            "530c0c10000000770845",
            "530d0c1700000077ad45",
            "530e0c19000000757645",
            "530f0c1f000000742f45",
            "53100c28000000783445",
        ])
        self.assertEqual(plan["transcript_sha256"],
                         "0fac72b34e77fbb432c9a43f786296515cf46fb8dac6ae6b080b5b03f077e24b")
        self.assertEqual((plan["maximum_writes"], plan["maximum_application_bytes"]), (6, 60))
        self.assertEqual(plan["response_rule"]["payload_bytes"], "unknown_preserve_raw")
        self.assertFalse(plan["automatic_retries"])
        self.assertFalse(plan["automatic_reconnect"])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            for knob in ("command", "payload", "sequence", "retry", "count"):
                with self.assertRaises(SystemExit):
                    survey.main(FLAGS + ["--" + knob, "1"])
        with patch.object(session, "preflight", side_effect=AssertionError("no preflight")):
            for name in consent.DISCONNECTED_GETTER_SURVEY_FLAGS:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    survey.run_survey(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(DECLARATIONS | {name: False}))

    def test_coordinator_and_recorder_forward_literal_scope(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.DISCONNECTED_GETTER_SURVEY_SCOPE)
        self.assertEqual(result["load_scope"], "MOTOR_POWER_PLUGS_DISCONNECTED")
        self.assertEqual(result["probe_name"], "DisconnectedLoadLegacyGetterSurvey")
        self.assertFalse(result["unknown_command_authorized"])
        self.assertTrue(result["fixed_legacy_getter_survey_authorized"])
        self.assertEqual(result["immutable_application_transcript_hex"],
                         [raw.hex() for raw in survey.TRANSCRIPT])
        self.assertEqual(result["requested_application_bytes"], 60)
        for flag in FLAGS:
            self.assertIn(flag, command)
        survey._validate_capture(runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        survey._validate_capture(capture.call_args.kwargs)

    def test_unknown_lengths_recorded_and_failure_stops_without_retry(self):
        payloads = tuple(bytes(range(size)) for size in (0, 1, 2, 3, 4, 5))
        transport = Mock(token="token", writes=0, serial_bytes=sum(len(x) + 10 for x in payloads))
        transport.revalidate.return_value = "token"

        def write(raw, *, deadline):
            self.assertEqual(raw, survey.TRANSCRIPT[transport.writes])
            transport.writes += 1
            return len(raw)

        def observe(_, response, **kwargs):
            index = response.sequence - survey.REQUESTS[0][1]
            response.feed(Mock(
                data=frame(payloads[index], command=response.command, status=0x80,
                           sequence=response.sequence),
                started_at=.1, ended_at=.2), .2)
            response.finish(.2)

        transport.write.side_effect = write
        report = {}
        with patch.object(survey.zero, "_observe_response", side_effect=observe):
            survey._observe(transport, report, clock=lambda: 0)
        self.assertEqual(report["status"], survey.SUCCESS)
        self.assertEqual([row["payload_bytes"] for row in report["responses"]],
                         list(range(6)))
        self.assertEqual(transport.write.call_count, 6)
        transport.close.assert_called_once()

        failing = Mock(token="token", writes=0, serial_bytes=10)
        failing.revalidate.return_value = "token"

        def fail_write(raw, *, deadline):
            failing.writes += 1
            return len(raw)

        def fail_on_third(_, response, **kwargs):
            status = 0x82 if response.sequence == survey.REQUESTS[2][1] else 0x80
            response.feed(Mock(
                data=frame(b"", command=response.command, status=status,
                           sequence=response.sequence),
                started_at=.1, ended_at=.2), .2)

        failing.write.side_effect = fail_write
        with patch.object(survey.zero, "_observe_response", side_effect=fail_on_third), \
                self.assertRaises(OSError):
            survey._observe(failing, {}, clock=lambda: 0)
        self.assertEqual(failing.write.call_count, 3)
        self.assertEqual(failing.writes, 3)
        failing.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
