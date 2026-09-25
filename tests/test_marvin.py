"""Focused offline tests for the public Marvin facade."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import marvin


class MarvinFacadeTests(unittest.TestCase):
    def test_offline_status_and_drive_profiles(self):
        robot = marvin.Marvin()
        status = robot.status()
        self.assertEqual(status["status"], "offline_ready")
        self.assertFalse(status["hardware_access"])
        self.assertEqual(status["capabilities"]["drive"], list(marvin.DRIVE_DIRECTIONS))
        expected = {
            "forward": [0, 2000, 0, 2000],
            "backward": [2000, 0, 2000, 0],
            "rotate-left": [2000, 0, 0, 2000],
            "rotate-right": [0, 2000, 2000, 0],
        }
        for direction, words in expected.items():
            self.assertEqual(robot.drive(direction)["fixed_setter_words_uint16"], words)

    def test_live_status_reuses_identity_validation(self):
        baseline = {"tty": "/dev/ttyACM0"}
        robot = marvin.Marvin(run=True, expected_physical_port="1-3")
        with patch.object(marvin.marvin_session, "preflight", return_value=baseline), \
                patch.object(marvin.marvin_legacy_probe, "_validate_baseline") as validate:
            result = robot.status()
        validate.assert_called_once_with(
            baseline, "1-3", marvin.marvin_campaign.DESCRIPTOR_HASH)
        self.assertEqual(result["status"], "connected_ready")

    def test_live_actions_delegate_only_fixed_profiles(self):
        robot = marvin.Marvin(
            run=True, expected_physical_port="1-3",
            output=Path("evidence/new"), safety_confirmed=True)
        with patch.object(marvin.drive_step, "run_step",
                          return_value={"status": "drive"}) as drive:
            self.assertEqual(robot.drive("forward")["status"], "drive")
        call = drive.call_args
        self.assertEqual(call.kwargs["duration"], 0.25)
        self.assertEqual(call.kwargs["raw_pwm"], 2000)
        self.assertTrue(all(call.kwargs[name] is True
                            for name in marvin.drive_step.COMMON_FLAGS))

        with patch.object(marvin.servo, "run_diagnostic",
                          return_value={"status": "camera"}) as camera:
            self.assertEqual(robot.camera_up(5)["status"], "camera")
        self.assertTrue(camera.call_args.kwargs["word0_500_unit"])
        self.assertTrue(all(camera.call_args.kwargs[name] is True
                            for name in marvin.servo.WORD0_500_UNIT_ACKNOWLEDGMENTS))

        with patch.object(marvin.servo_center, "run_restore",
                          return_value={"status": "center"}) as center:
            self.assertEqual(robot.camera_center()["status"], "center")
        self.assertTrue(all(center.call_args.kwargs[name] is True
                            for name in marvin.servo_center.ACKNOWLEDGMENTS))

    def test_unproved_controls_fail_without_transport_access(self):
        robot = marvin.Marvin()
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "not established"):
            robot.stop()
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "inferred inverse"):
            robot.camera_down(5)
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "unproved linearity"):
            robot.camera_up(1)

    def test_cli_help_plans_and_failure_surface(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["drive", "rotate-left"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["direction"], "rotate-left")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(marvin.main(["stop"]), 1)
        failure = json.loads(stderr.getvalue())
        self.assertEqual(failure["status"], "failed")
        self.assertIn("mandatory all-zero cleanup attempt", failure["error"])
        self.assertIn("If a write may have applied", failure["safety"])


if __name__ == "__main__":
    unittest.main()
