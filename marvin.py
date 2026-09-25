"""Small public Marvin operator API and command-line interface.

Live actions are intentionally one-shot. Each drive step owns its mandatory
zero cleanup, and camera-up owns its baseline restore, including interruption
and uncertain-write paths.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from tools import marvin_campaign
from tools import marvin_legacy_drive_step as drive_step
from tools import marvin_legacy_front_servo_baseline_restore as servo_center
from tools import marvin_legacy_front_servo_mapper as servo
from tools import marvin_legacy_probe
from tools import marvin_probe
from tools import marvin_session


DRIVE_DIRECTIONS = tuple(drive_step.DIRECTIONS)
CAMERA_UP_DEGREES = 5


class UnsupportedOperation(ValueError):
    """The installed hardware evidence does not support this operation."""


class Marvin:
    """Bounded Marvin controls with no arbitrary protocol access.

    ``run=False`` returns offline plans and never opens hardware. Live calls
    require the reviewed physical USB port, a new evidence directory, and an
    explicit safety confirmation.
    """

    def __init__(self, *, run=False, expected_physical_port=None, output=None,
                 safety_confirmed=False):
        if type(run) is not bool or type(safety_confirmed) is not bool:
            raise ValueError("run and safety_confirmed must be literal booleans.")
        self.run = run
        self.expected_physical_port = expected_physical_port
        self.output = Path(output) if output is not None else None
        self.safety_confirmed = safety_confirmed

    def status(self):
        """Report offline capabilities or read-only live connection readiness."""
        capabilities = {
            "drive": list(DRIVE_DIRECTIONS),
            "drive_step_seconds": drive_step.DURATION_SECONDS,
            "camera_up_degrees": [CAMERA_UP_DEGREES],
            "camera_center": True,
            "camera_down": False,
            "standalone_motor_stop": False,
        }
        if not self.run:
            return {
                "status": "offline_ready",
                "connected": False,
                "hardware_access": False,
                "capabilities": capabilities,
            }
        if not self.expected_physical_port:
            raise ValueError("Live status requires expected_physical_port.")
        baseline = marvin_session.preflight(marvin_probe.DEFAULT_PORT)
        marvin_legacy_probe._validate_baseline(
            baseline, self.expected_physical_port, marvin_campaign.DESCRIPTOR_HASH)
        return {
            "status": "connected_ready",
            "connected": True,
            "hardware_access": "read_only_host_identity",
            "usb_identity": "045e:4444",
            "physical_port": self.expected_physical_port,
            "tty": baseline["tty"],
            "capabilities": capabilities,
        }

    def drive(self, direction):
        """Run one proved 0.25-second drive step, always followed by zero cleanup."""
        if direction not in DRIVE_DIRECTIONS:
            raise ValueError(f"Direction must be one of: {', '.join(DRIVE_DIRECTIONS)}.")
        if not self.run:
            return drive_step.prepare(
                direction, duration=drive_step.DURATION_SECONDS,
                raw_pwm=drive_step.RAW_PWM)
        self._require_live_action()
        return drive_step.run_step(
            self.output,
            direction=direction,
            duration=drive_step.DURATION_SECONDS,
            raw_pwm=drive_step.RAW_PWM,
            expected_physical_port=self.expected_physical_port,
            run=True,
            authorize_unvalidated_drive_step=True,
            **dict.fromkeys(drive_step.COMMON_FLAGS, True),
        )

    def camera_up(self, degrees):
        """Run the directly observed five-degree camera-up profile and restore."""
        if type(degrees) is not int or degrees != CAMERA_UP_DEGREES:
            raise UnsupportedOperation(
                "Only camera up 5 is supported: 2500 -> 2000 was directly "
                "observed as approximately five degrees upward. Other angles "
                "would assume unproved linearity.")
        if not self.run:
            return servo.prepare(word0_500_unit=True)
        self._require_live_action()
        return servo.run_diagnostic(
            self.output,
            expected_physical_port=self.expected_physical_port,
            run=True,
            word0_500_unit=True,
            **dict.fromkeys(servo.WORD0_500_UNIT_ACKNOWLEDGMENTS, True),
        )

    def camera_center(self):
        """Write the locally proved [2500, 2730] camera baseline once."""
        if not self.run:
            return servo_center.prepare()
        self._require_live_action()
        return servo_center.run_restore(
            self.output,
            expected_physical_port=self.expected_physical_port,
            run=True,
            **dict.fromkeys(servo_center.ACKNOWLEDGMENTS, True),
        )

    def camera_down(self, degrees):
        raise UnsupportedOperation(
            "Camera down is not exposed: increasing word 0 is only an inferred "
            "inverse and has not been exercised with the installed linkage.")

    def stop(self):
        raise UnsupportedOperation(
            "Standalone motor stop is not exposed: physical stop semantics are "
            "not established. Every drive step makes one mandatory all-zero "
            "cleanup attempt and surfaces cleanup uncertainty.")

    def _require_live_action(self):
        if not self.expected_physical_port or self.output is None:
            raise ValueError(
                "Live actions require expected_physical_port and a new output directory.")
        if not self.safety_confirmed:
            raise ValueError(
                "Live actions require safety_confirmed=True after confirming the "
                "reviewed wiring, on-blocks setup, external cutoff, encoder and "
                "servo isolation requirements.")


def _live_arguments(parser, *, safety=True):
    parser.add_argument("--run", action="store_true", help="access the reviewed device")
    parser.add_argument(
        "--expected-physical-port", metavar="PORT",
        help="reviewed USB topology such as 1-3")
    if safety:
        parser.add_argument("--output", type=Path, metavar="NEWDIR",
                            help="new evidence directory")
        parser.add_argument(
            "--confirm-safe-setup", action="store_true",
            help="confirm reviewed wiring, robot on blocks, external cutoff ready, "
                 "encoders connected, required servo isolation, and ordinary-user usbmon")


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m marvin",
        description="Bounded controls for the original Microsoft Marvin robot.",
        epilog="""examples:
  python -m marvin status
  python -m marvin drive forward
  python -m marvin camera up 5
  python -m marvin camera center
  python -m marvin drive rotate-left --run --expected-physical-port 1-3 \\
      --output evidence/left-001 --confirm-safe-setup

Commands are offline plans unless --run is present. Live drive steps last 0.25
seconds and always attempt zero cleanup. Camera up supports only the directly
observed 5-degree profile and always attempts baseline restore.""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="command", required=True)
    status = actions.add_parser("status", help="show capabilities or live readiness")
    _live_arguments(status, safety=False)
    drive = actions.add_parser("drive", help="run one bounded drive step")
    drive.add_argument("direction", choices=DRIVE_DIRECTIONS)
    _live_arguments(drive)
    stop = actions.add_parser("stop", help="report standalone-stop evidence gap")
    _live_arguments(stop)
    camera = actions.add_parser("camera", help="control front-camera tilt")
    camera_actions = camera.add_subparsers(dest="camera_command", required=True)
    up = camera_actions.add_parser("up", help="tilt up by the proved five degrees")
    up.add_argument("degrees", type=int)
    _live_arguments(up)
    down = camera_actions.add_parser("down", help="report camera-down evidence gap")
    down.add_argument("degrees", type=int)
    _live_arguments(down)
    center = camera_actions.add_parser("center", help="write baseline [2500,2730]")
    _live_arguments(center)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    marvin = Marvin(
        run=args.run,
        expected_physical_port=args.expected_physical_port,
        output=getattr(args, "output", None),
        safety_confirmed=getattr(args, "confirm_safe_setup", False),
    )
    try:
        if args.command == "status":
            result = marvin.status()
        elif args.command == "drive":
            result = marvin.drive(args.direction)
        elif args.command == "stop":
            result = marvin.stop()
        elif args.camera_command == "up":
            result = marvin.camera_up(args.degrees)
        elif args.camera_command == "down":
            result = marvin.camera_down(args.degrees)
        else:
            result = marvin.camera_center()
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        details = {
            "status": "failed",
            "error": str(error) or type(error).__name__,
            "safety": (
                "No retry. If a write may have applied, the underlying bounded "
                "action attempted cleanup before surfacing this failure."
            ),
        }
        notes = getattr(error, "__notes__", ())
        if notes:
            details["additional_errors"] = list(notes)
        print(json.dumps(details, sort_keys=True), file=sys.stderr, flush=True)
        return 130 if isinstance(error, KeyboardInterrupt) else 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
