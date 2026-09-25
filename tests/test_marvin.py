"""Focused offline tests for the public Marvin facade."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
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
        self.assertTrue(status["capabilities"]["standalone_motor_stop"])
        self.assertFalse(
            status["capabilities"]["servos"]["projector"]["live_power_smoke"])
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

        with patch.object(marvin.legacy_stop, "run_stop",
                          return_value={"status": "stop"}) as stop:
            self.assertEqual(robot.stop()["status"], "stop")
        self.assertTrue(all(
            stop.call_args.kwargs[name] is True
            for name in marvin.motor_consent.RAW_PWM_STOP_FLAGS))

    def test_fixed_offline_plans_and_unproved_controls(self):
        robot = marvin.Marvin()
        self.assertEqual(robot.stop()["fixed_stop_words_uint16"], [0, 0, 0, 0])
        down = robot.camera_down(5)
        self.assertEqual(down["target_words_uint16"], [3000, 2730])
        self.assertFalse(down["live_execution_authorized"])
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "remains blocked"):
            marvin.Marvin(
                run=True, expected_physical_port="1-3",
                output="new", safety_confirmed=True).camera_down(5)
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "unproved linearity"):
            robot.camera_up(1)

    def test_shared_servo_axes_preserve_the_sibling_word(self):
        self.assertEqual(marvin.CAMERA_AXIS.target_words(2000), (2000, 2730))
        self.assertEqual(marvin.PROJECTOR_AXIS.target_words(2600), (2500, 2600))
        status = marvin.Marvin().projector_status()
        self.assertEqual(status["status"], "unavailable_unverified")
        self.assertIsNone(status["units_per_degree"])
        self.assertEqual(
            status["offline_minus_500_plan"]["target_words_uint16"], [2500, 2230])
        self.assertFalse(
            status["offline_minus_500_plan"]["live_execution_authorized"])
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
        self.assertEqual(on["classification"], "disabled_source_collision_experiment")
        self.assertIn("finally", on["cleanup_policy"])
        off = marvin.Marvin().projector_power("off")
        self.assertEqual(off["payload_uint8"], 0)
        self.assertEqual(off["maximum_writes"], 1)
        robot = marvin.Marvin(
            run=True, expected_physical_port="1-3",
            output="new", safety_confirmed=True)
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "mismatched source map"):
            robot.projector_power("on")
        with self.assertRaisesRegex(marvin.UnsupportedOperation, "mismatched source map"):
            robot.projector_power("off")
        with self.assertRaises(ValueError):
            marvin.marvin_legacy_protocol.projector_power_request(1, 1)
        acknowledgments = dict.fromkeys(projector_power.ACKNOWLEDGMENTS, True)
        with patch("os.open", side_effect=AssertionError("open reached")), \
                patch("os.write", side_effect=AssertionError("write reached")), \
                patch.object(
                    marvin.marvin_session, "preflight",
                    side_effect=AssertionError("preflight reached")):
            with self.assertRaisesRegex(ValueError, "permanently disabled"):
                projector_power.run_smoke(
                    "new", expected_physical_port="1-3", run=True,
                    **acknowledgments)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(marvin.main([
                    "projector", "power", "on", "--run",
                    "--expected-physical-port", "1-3",
                    "--output", "new", "--confirm-safe-setup",
                ]), 1)

    def test_teleop_dispatches_and_exit_paths_stop_once(self):
        with tempfile.TemporaryDirectory() as directory:
            robot = marvin.Marvin(
                run=True, expected_physical_port="1-3",
                output=Path(directory) / "teleop", safety_confirmed=True)
            keys = iter(("w", "a", "x", "q"))
            calls = []

            def drive(instance, direction):
                calls.append((direction, instance.output.name))
                return {"status": "drive"}

            def stop(instance):
                calls.append(("stop", instance.output.name))
                return {"status": "stop"}

            with patch.object(marvin.Marvin, "drive", new=drive), \
                    patch.object(marvin.Marvin, "stop", new=stop):
                result = robot.teleop(
                    input_fn=lambda prompt: next(keys), output_stream=io.StringIO())
            self.assertEqual(result["actions"], 3)
            self.assertEqual(calls, [
                ("forward", "run-0001"),
                ("rotate-left", "run-0002"),
                ("stop", "run-0003"),
            ])

        with tempfile.TemporaryDirectory() as directory:
            for ending in ("q", EOFError(), KeyboardInterrupt()):
                with self.subTest(ending=type(ending).__name__):
                    robot = marvin.Marvin(
                        run=True, expected_physical_port="1-3",
                        output=Path(directory) / "exit", safety_confirmed=True)
                    stops = []

                    def exit_stop(instance):
                        stops.append(instance.output.name)
                        return {"status": "stop"}

                    with patch.object(marvin.Marvin, "stop", new=exit_stop):
                        result = robot.teleop(
                            input_fn=(
                                (lambda prompt: ending)
                                if isinstance(ending, str)
                                else lambda prompt: (_ for _ in ()).throw(ending)
                            ),
                            output_stream=io.StringIO())
                    self.assertEqual(result["actions"], 1)
                    self.assertEqual(stops, ["run-0001"])

    def test_teleop_action_failures_and_interrupts_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            robot = marvin.Marvin(
                run=True, expected_physical_port="1-3",
                output=Path(directory) / "teleop", safety_confirmed=True)
            with patch.object(marvin.Marvin, "drive", side_effect=OSError("uncertain")), \
                    patch.object(marvin.Marvin, "stop") as stop, \
                    self.assertRaisesRegex(OSError, "uncertain"):
                robot.teleop(
                    input_fn=lambda prompt: "d", output_stream=io.StringIO())
            stop.assert_not_called()

            attempts = []

            def interrupted_drive(instance, direction):
                attempts.append((direction, instance.output.name))
                raise KeyboardInterrupt

            with patch.object(marvin.Marvin, "drive", new=interrupted_drive), \
                    patch.object(marvin.Marvin, "stop") as stop, \
                    self.assertRaises(KeyboardInterrupt):
                robot.teleop(
                    input_fn=lambda prompt: "w",
                    output_stream=io.StringIO())
            self.assertEqual(attempts, [("forward", "run-0001")])
            stop.assert_not_called()

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

    def test_cli_help_plans_and_failure_surface(self):
        help_text = marvin._parser().format_help()
        self.assertIn("Live projector power is disabled", help_text)
        self.assertIn("fixed offline-only inverse", help_text)
        self.assertIn("teleop", help_text)
        self.assertIn("marvin leds wheel-blink", help_text)

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["drive", "rotate-left"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["direction"], "rotate-left")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["stop"]), 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["fixed_stop_words_uint16"], [0, 0, 0, 0])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["camera", "down", "5"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["target_words_uint16"], [3000, 2730])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["teleop"]), 0)
        teleop = json.loads(stdout.getvalue())
        self.assertIn("run-NNNN", teleop["evidence_subdirectories"])
        self.assertTrue(teleop["live_execution_authorized"])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(marvin.main(["microphone", "status"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["hardware_accessed"], False)
        with patch.object(marvin.marvin_microphone, "main", return_value=0) as microphone:
            self.assertEqual(marvin.main([
                "microphone", "list", "--run", "--hub-path", "1-1.1.2",
                "--device", "hw:CARD=Array,DEV=0",
            ]), 0)
        microphone.assert_called_once_with([
            "list", "--run", "--hub-path", "1-1.1.2",
            "--device", "hw:CARD=Array,DEV=0",
        ])

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
