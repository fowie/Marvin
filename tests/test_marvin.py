"""Focused offline tests for the public Marvin facade."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import marvin
from marvin_leds import LEDS, MarvinLEDs
from tools import marvin_legacy_projector_power as projector_power
from tools import marvin_legacy_wheel_led_blink as wheel_blink
from tools import marvin_motor_power_off_consent as consent


class MarvinFacadeTests(unittest.TestCase):
    def test_offline_status_and_drive_profiles(self):
        robot = marvin.Marvin()
        status = robot.status()
        self.assertEqual(status["status"], "offline_ready")
        self.assertFalse(status["hardware_access"])
        self.assertEqual(status["capabilities"]["drive"], list(marvin.DRIVE_DIRECTIONS))
        self.assertEqual(status["capabilities"]["servos"]["camera"]["word"], 0)
        self.assertEqual(status["capabilities"]["servos"]["projector"]["word"], 1)
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

    def test_shared_servo_axes_preserve_the_sibling_word(self):
        self.assertEqual(marvin.CAMERA_AXIS.target_words(2000), (2000, 2730))
        self.assertEqual(marvin.PROJECTOR_AXIS.target_words(2600), (2500, 2600))
        status = marvin.Marvin().projector_status()
        self.assertEqual(status["status"], "unavailable_unverified")
        self.assertIsNone(status["units_per_degree"])
        plan = marvin.Marvin().projector_center()
        self.assertEqual(plan["target_words_uint16"], [2500, 2730])
        self.assertEqual(plan["untouched_sibling_word"], 0)
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "physically disconnected"):
            marvin.Marvin().projector_up(5)
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "routing"):
            marvin.Marvin(
                run=True, expected_physical_port="1-3",
                output="new", safety_confirmed=True).projector_center()

    def test_projector_power_is_fixed_and_offline_only(self):
        on = marvin.Marvin().projector_power("on")
        packets = [
            marvin.marvin_legacy_protocol.decode_packet(bytes.fromhex(raw))
            for raw in on["immutable_application_transcript_hex"]
        ]
        self.assertEqual(
            [(packet.command, packet.payload) for packet in packets],
            [(0x27, b"\x01"), (0x27, b"\x00")])
        self.assertIn("finally", on["cleanup_policy"])
        off = marvin.Marvin().projector_power("off")
        self.assertEqual(off["payload_uint8"], 0)
        self.assertEqual(off["maximum_writes"], 1)
        robot = marvin.Marvin(
            run=True, expected_physical_port="1-3",
            output="new", safety_confirmed=True)
        with patch.object(projector_power, "run_smoke",
                          return_value={"status": "smoke"}) as smoke:
            self.assertEqual(robot.projector_power("on")["status"], "smoke")
        smoke.assert_called_once()
        self.assertTrue(all(
            smoke.call_args.kwargs[name] is True
            for name in projector_power.ACKNOWLEDGMENTS))
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "Standalone live"):
            robot.projector_power("off")
        with self.assertRaises(ValueError):
            marvin.marvin_legacy_protocol.projector_power_request(1, 1)

    def test_projector_smoke_always_attempts_off_after_on_may_apply(self):
        class Clock:
            now = 10.0

            def __call__(self):
                return self.now

        class Transport:
            token = b"identity"
            serial_bytes = 0

            def __init__(self, *, wait_error=None, on_error=None, off_error=None,
                         hold=projector_power.HOLD_SECONDS):
                self.wait_error = wait_error
                self.on_error = on_error
                self.off_error = off_error
                self.hold = hold
                self.on_may_have_applied = False
                self.off_attempted = False
                self.writes = 0
                self.steps = []
                self.identities = 0

            def revalidate(self, *, deadline):
                return self.token

            def identity(self, *, deadline):
                self.identities += 1
                return self.token

            def submit(self, step, *, deadline):
                self.steps.append(step)
                self.writes += 1
                if step == "on":
                    self.on_may_have_applied = True
                    if self.on_error:
                        raise self.on_error
                else:
                    self.off_attempted = True
                    if self.off_error:
                        raise self.off_error
                return len(projector_power.STEPS[step])

            def wait(self, seconds):
                if self.wait_error:
                    raise self.wait_error
                clock.now += self.hold

            def close(self, *, deadline):
                return None

        def run(transport):
            report = {}
            with patch.object(projector_power, "_response", return_value=object()):
                projector_power._observe(transport, report, clock=clock)
            return report

        clock = Clock()
        normal = Transport()
        report = run(normal)
        self.assertEqual(normal.steps, ["on", "off"])
        self.assertEqual(normal.identities, 2)
        self.assertEqual(report["actual_hold_seconds"], 10.0)
        self.assertTrue(report["off_correlated"])

        for error in (KeyboardInterrupt(), OSError("uncertain on")):
            clock = Clock()
            transport = (
                Transport(wait_error=error)
                if isinstance(error, KeyboardInterrupt)
                else Transport(on_error=error)
            )
            with self.subTest(error=type(error).__name__), \
                    self.assertRaises(type(error)):
                run(transport)
            self.assertEqual(transport.steps, ["on", "off"])
            self.assertTrue(transport.off_attempted)

        clock = Clock()
        failed_off = Transport(off_error=OSError("off uncertain"))
        with self.assertRaisesRegex(OSError, "off uncertain"):
            run(failed_off)
        self.assertEqual(failed_off.steps, ["on", "off"])
        self.assertTrue(failed_off.off_attempted)

        clock = Clock()
        overrun = Transport(
            hold=projector_power.HOLD_SECONDS
            + projector_power.MAX_HOLD_OVERRUN_SECONDS + 0.001)
        with self.assertRaisesRegex(OSError, "missed its fixed"):
            run(overrun)
        self.assertEqual(overrun.steps, ["on", "off"])

    def test_led_plans_and_only_proved_live_action(self):
        robot = marvin.Marvin()
        status = robot.leds.status()
        self.assertEqual(status["live_actions"], ["wheel-blink"])
        self.assertEqual(set(status["named_full_intensity_plans"]), set(LEDS))
        plan = robot.leds.full_intensity_plan("left-position-0-red")
        self.assertEqual(
            (plan["target"], plan["fixed_value"], plan["other_payload_indices"]),
            (0, 255, "forced_to_zero"),
        )
        self.assertFalse(plan["live_execution_authorized"])
        with self.assertRaisesRegex(ValueError, "LED must be one of"):
            robot.leds.full_intensity_plan("rainbow")
        with self.assertRaisesRegex(ValueError, "literal booleans"):
            MarvinLEDs(run=1)

        live = marvin.Marvin(
            run=True, expected_physical_port="1-3",
            output="new", safety_confirmed=True)
        with self.assertRaisesRegex(ValueError, "does not own cleanup"):
            live.leds.full_intensity_plan("wheels")
        with patch.object(wheel_blink, "run_diagnostic",
                          return_value={"status": "blink"}) as run:
            self.assertEqual(live.leds.wheel_blink()["status"], "blink")
        self.assertTrue(all(
            run.call_args.kwargs[name] is True
            for name in consent.WHEEL_LED_BLINK_FLAGS
        ))

    def test_live_cli_combined_confirmation_passes_coordinator_gate_in_subprocess(self):
        root = Path(marvin.__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            confirmed = Path(directory) / "confirmed"
            omitted = Path(directory) / "omitted"
            script = f"""
import contextlib
import io
from unittest.mock import patch
import marvin
from tools import marvin_legacy_zero as zero
from tools import marvin_session

baseline = {{
    "usb": {{
        "usb_path": "/fake/1-1.1.3.3",
        "physical_port": "1-1.1.3.3",
        "idVendor": "045e",
        "idProduct": "4444",
        "busnum": 1,
        "devnum": 2,
        "sysfs_device": 3,
        "sysfs_inode": 4,
        "descriptors_sha256": marvin.marvin_campaign.DESCRIPTOR_HASH,
        "descriptors_bytes": 71,
    }},
    "tty": "/dev/fake-marvin",
    "tty_rdev": 5,
}}

def arm(clock):
    clock.offset = (0, 0)

stderr = io.StringIO()
with patch.object(marvin_session, "preflight", side_effect=[
        baseline, OSError("POST_GATE_IDENTITY_DEVICE_BOUNDARY"),
    ]), patch.object(zero.IngressClock, "start", arm), \\
        patch.object(zero.IngressClock, "close"), \\
        patch.object(marvin_session.os, "geteuid", return_value=1000), \\
        contextlib.redirect_stderr(stderr):
    result = marvin.main([
        "projector", "power", "on", "--run",
        "--expected-physical-port", "1-1.1.3.3",
        "--output", {str(confirmed)!r}, "--confirm-safe-setup",
    ])
assert result == 1, result
assert "POST_GATE_IDENTITY_DEVICE_BOUNDARY" in stderr.getvalue(), stderr.getvalue()
assert "actuators-isolated" not in stderr.getvalue(), stderr.getvalue()

stderr = io.StringIO()
with patch.object(marvin_session, "preflight",
                  side_effect=AssertionError("DEVICE_BOUNDARY_REACHED")), \\
        contextlib.redirect_stderr(stderr):
    result = marvin.main([
        "projector", "power", "on", "--run",
        "--expected-physical-port", "1-1.1.3.3",
        "--output", {str(omitted)!r},
    ])
assert result == 1, result
assert "safety_confirmed=True" in stderr.getvalue(), stderr.getvalue()
"""
            completed = subprocess.run(
                [sys.executable, "-B", "-c", script],
                cwd=root, capture_output=True, text=True)
            self.assertEqual(
                completed.returncode, 0,
                completed.stdout + completed.stderr)
            self.assertFalse(omitted.exists())

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

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["projector", "status"]), 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["status"], "unavailable_unverified")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["projector", "power", "on"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["command"], 0x27)

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                marvin.main(["leds", "plan", "right-position-2-blue"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["target"], 11)


if __name__ == "__main__":
    unittest.main()
