"""Small public Marvin operator API and command-line interface.

Live actions are intentionally one-shot. Each drive step owns its mandatory
zero cleanup, and camera-up owns its baseline restore, including interruption
and uncertain-write paths.
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from tools import marvin_campaign
from tools import marvin_legacy_drive_step as drive_step
from tools import marvin_legacy_front_servo_baseline_restore as servo_center
from tools import marvin_legacy_front_servo_mapper as servo
from tools import marvin_legacy_probe
from tools import marvin_legacy_protocol
from tools import marvin_legacy_projector_power as projector_power
from tools import marvin_probe
from tools import marvin_session
from tools.marvin_legacy_client import SessionError
import marvin_sensors


DRIVE_DIRECTIONS = tuple(drive_step.DIRECTIONS)
CAMERA_UP_DEGREES = 5


@dataclass(frozen=True)
class _ServoAxis:
    name: str
    word: int
    baseline: int
    live_up_degrees: tuple[int, ...] = ()
    units_per_degree: int | None = None
    routing: str = "unverified"

    def target_words(self, value):
        if type(value) is not int or not 0 <= value <= 65535:
            raise ValueError("Servo target must fit uint16.")
        words = list(SERVO_BASELINE)
        words[self.word] = value
        return tuple(words)

    def status(self):
        return {
            "word": self.word,
            "baseline": self.baseline,
            "routing": self.routing,
            "units_per_degree": self.units_per_degree,
            "live_up_degrees": list(self.live_up_degrees),
        }


SERVO_BASELINE = servo.BASELINE
CAMERA_AXIS = _ServoAxis(
    "camera", 0, SERVO_BASELINE[0], (CAMERA_UP_DEGREES,), 100,
    "directly_observed_installed_camera",
)
PROJECTOR_AXIS = _ServoAxis(
    "projector", 1, SERVO_BASELINE[1],
    routing="historical_expected_word1_not_directly_exercised",
)
PROJECTOR_POWER_SEQUENCE = 3600


class UnsupportedOperation(ValueError):
    """The installed hardware evidence does not support this operation."""


class Marvin:
    """Bounded Marvin controls with no arbitrary protocol access.

    ``run=False`` returns offline plans and never opens hardware. Live calls
    require the reviewed physical USB port, a new evidence directory, and an
    explicit safety confirmation.
    """

    def __init__(self, *, run=False, expected_physical_port=None, output=None,
                 safety_confirmed=False, sensor_transport=None,
                 sensor_ownership_key=None, sensor_expected_identity=None):
        if type(run) is not bool or type(safety_confirmed) is not bool:
            raise ValueError("run and safety_confirmed must be literal booleans.")
        self.run = run
        self.expected_physical_port = expected_physical_port
        self.output = Path(output) if output is not None else None
        self.safety_confirmed = safety_confirmed
        self.sensor_transport = sensor_transport
        self.sensor_ownership_key = sensor_ownership_key
        self.sensor_expected_identity = sensor_expected_identity

    def sensors(self, *, actuators_isolated=False, unprivileged_usbmon=False):
        """Return the offline sensor plan or one injected persistent-session snapshot."""
        if not self.run:
            return marvin_sensors.plan()
        if self.sensor_transport is None:
            if not self.expected_physical_port or self.output is None:
                raise ValueError(
                    "Live sensors require expected_physical_port and a new output directory.")
            return marvin_sensors.run_live_snapshot(
                self.output,
                expected_physical_port=self.expected_physical_port,
                run=True,
                actuators_isolated=actuators_isolated,
                unprivileged_usbmon=unprivileged_usbmon,
            )
        if self.output is not None or actuators_isolated or unprivileged_usbmon:
            raise ValueError(
                "Injected sensor transports cannot accept CLI evidence options; "
                "use the installed live runner to seal evidence.")
        return marvin_sensors.read_snapshot(
            self.sensor_transport,
            ownership_key=self.sensor_ownership_key,
            expected_identity=self.sensor_expected_identity,
        )

    def status(self):
        """Report offline capabilities or read-only live connection readiness."""
        capabilities = {
            "drive": list(DRIVE_DIRECTIONS),
            "drive_step_seconds": drive_step.DURATION_SECONDS,
            "servos": {
                CAMERA_AXIS.name: CAMERA_AXIS.status(),
                PROJECTOR_AXIS.name: {
                    **PROJECTOR_AXIS.status(),
                            "power_plan": True,
                            "live_power_smoke": True,
                },
            },
            "standalone_motor_stop": False,
            "read_only_sensors": True,
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
        return self._servo_move(CAMERA_AXIS, "up", degrees)

    def camera_down(self, degrees):
        return self._servo_move(CAMERA_AXIS, "down", degrees)

    def camera_center(self):
        """Write the locally proved camera baseline while retaining word 1."""
        return self._servo_center(CAMERA_AXIS)

    def projector_status(self):
        """Report historical projector mapping without authorizing a write."""
        return {
            "status": "unavailable_unverified",
            "connected": False,
            **PROJECTOR_AXIS.status(),
            "reason": (
                "The projector is physically disconnected; word-1 live routing "
                "and local units-per-degree have not been directly exercised."
            ),
        }

    def projector_up(self, degrees):
        return self._servo_move(PROJECTOR_AXIS, "up", degrees)

    def projector_down(self, degrees):
        return self._servo_move(PROJECTOR_AXIS, "down", degrees)

    def projector_center(self):
        return self._servo_center(PROJECTOR_AXIS)

    def projector_power(self, state):
        """Return a fixed offline power plan; live execution is not authorized."""
        if state not in ("on", "off"):
            raise ValueError("Projector power state must be 'on' or 'off'.")
        if self.run:
            if state != "on":
                raise UnsupportedOperation(
                    "Standalone live projector OFF is not exposed; the live surface "
                    "is one fixed ON, 10.0-second observation, then OFF smoke test.")
            self._require_live_action()
            return projector_power.run_smoke(
                self.output,
                expected_physical_port=self.expected_physical_port,
                run=True,
                **dict.fromkeys(projector_power.ACKNOWLEDGMENTS, True),
            )
        if state == "on":
            return projector_power.prepare()
        enabled = state == "on"
        requests = [
            marvin_legacy_protocol.projector_power_request(
                PROJECTOR_POWER_SEQUENCE, enabled)
        ]
        if enabled:
            requests.append(marvin_legacy_protocol.projector_power_request(
                PROJECTOR_POWER_SEQUENCE + 1, False))
        return {
            "status": "offline_unverified",
            "operation": f"projector_power_{state}",
            "command": marvin_legacy_protocol.SET_PROJECTOR_POWER,
            "payload_uint8": int(enabled),
            "source_semantics": "1=on, 0=off",
            "reversible_in_source": True,
            "live_execution_authorized": False,
            "immutable_application_transcript_hex": [
                request.hex() for request in requests
            ],
            "maximum_writes": len(requests),
            "automatic_retries": False,
            "cleanup_policy": (
                "If power-on may apply, one fixed power-off attempt runs in finally."
                if enabled else "single fixed power-off request; no retry"
            ),
            "evidence": (
                "recovered firmware source and existing command catalog only; "
                "no installed-hardware execution or response evidence"
            ),
        }

    def _servo_move(self, axis, direction, degrees):
        if axis is PROJECTOR_AXIS:
            raise UnsupportedOperation(
                f"Projector {direction} is not exposed: historical word 1, "
                "direction, routing, and units-per-degree have not been directly "
                "exercised, and the projector is physically disconnected.")
        if direction == "down":
            raise UnsupportedOperation(
                "Camera down is not exposed: increasing word 0 is only an inferred "
                "inverse and has not been exercised with the installed linkage.")
        if type(degrees) is not int or degrees not in axis.live_up_degrees:
            raise UnsupportedOperation(
                "Only camera up 5 is supported: 2500 -> 2000 was directly "
                "observed as approximately five degrees upward. Other angles "
                "would assume unproved linearity.")
        if axis.target_words(axis.baseline - degrees * axis.units_per_degree) != (
                servo.WORD0_500_UNIT_TARGET):
            raise ValueError("Camera axis configuration differs from the proved profile.")
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

    def _servo_center(self, axis):
        target = axis.target_words(axis.baseline)
        if axis is PROJECTOR_AXIS:
            if self.run:
                raise UnsupportedOperation(
                    "Live projector center is not exposed: word-1 routing has not "
                    "been directly exercised and the projector is disconnected.")
            return {
                "status": "offline_unverified",
                "axis": axis.name,
                "target_words_uint16": list(target),
                "untouched_sibling_word": CAMERA_AXIS.word,
                "live_execution_authorized": False,
                "reason": "historical word-1 baseline only; routing unverified",
            }
        if target != tuple(servo_center.WORDS):
            raise ValueError("Camera center configuration differs from the proved profile.")
        if not self.run:
            return servo_center.prepare()
        self._require_live_action()
        return servo_center.run_restore(
            self.output,
            expected_physical_port=self.expected_physical_port,
            run=True,
            **dict.fromkeys(servo_center.ACKNOWLEDGMENTS, True),
        )

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


def _servo_subcommands(parser, *, status=False):
    actions = parser.add_subparsers(dest="servo_command", required=True)
    if status:
        state = actions.add_parser("status", help="show axis evidence status")
        _live_arguments(state, safety=False)
        power = actions.add_parser(
            "power", help="show the offline-only fixed projector power plan")
        power.add_argument("state", choices=("on", "off"))
        _live_arguments(power)
    up = actions.add_parser("up", help="tilt up by a supported angle")
    up.add_argument("degrees", type=int)
    _live_arguments(up)
    down = actions.add_parser("down", help="tilt down by a supported angle")
    down.add_argument("degrees", type=int)
    _live_arguments(down)
    center = actions.add_parser("center", help="center this axis")
    _live_arguments(center)


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m marvin",
        description="Bounded controls for the original Microsoft Marvin robot.",
        epilog="""examples:
  python -m marvin status
  python -m marvin sensors
  python -m marvin drive forward
  python -m marvin camera up 5
  python -m marvin camera center
  python -m marvin projector status
  python -m marvin projector power on
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
    sensors = actions.add_parser(
        "sensors", help="show the exact read-only sensor plan and schema")
    _live_arguments(sensors, safety=False)
    sensors.add_argument("--output", type=Path, metavar="NEWDIR",
                         help="new sealed evidence directory")
    sensors.add_argument(
        "--actuators-isolated", action="store_true",
        help="confirm actuator power and signals are isolated")
    sensors.add_argument(
        "--unprivileged-usbmon", action="store_true",
        help="confirm ordinary-user target-scoped USB recording")
    drive = actions.add_parser("drive", help="run one bounded drive step")
    drive.add_argument("direction", choices=DRIVE_DIRECTIONS)
    _live_arguments(drive)
    stop = actions.add_parser("stop", help="report standalone-stop evidence gap")
    _live_arguments(stop)
    camera = actions.add_parser("camera", help="control front-camera tilt")
    _servo_subcommands(camera)
    projector = actions.add_parser(
        "projector", help="inspect the unverified projector-servo surface")
    _servo_subcommands(projector, status=True)
    return parser


def main(argv=None, *, sensor_transport=None, sensor_ownership_key=None,
         sensor_expected_identity=None):
    args = _parser().parse_args(argv)
    marvin = Marvin(
        run=args.run,
        expected_physical_port=getattr(args, "expected_physical_port", None),
        output=getattr(args, "output", None),
        safety_confirmed=getattr(args, "confirm_safe_setup", False),
        sensor_transport=sensor_transport,
        sensor_ownership_key=sensor_ownership_key,
        sensor_expected_identity=sensor_expected_identity,
    )
    try:
        if args.command == "status":
            result = marvin.status()
        elif args.command == "sensors":
            result = marvin.sensors(
                actuators_isolated=args.actuators_isolated,
                unprivileged_usbmon=args.unprivileged_usbmon,
            )
        elif args.command == "drive":
            result = marvin.drive(args.direction)
        elif args.command == "stop":
            result = marvin.stop()
        elif args.command == "camera" and args.servo_command == "up":
            result = marvin.camera_up(args.degrees)
        elif args.command == "camera" and args.servo_command == "down":
            result = marvin.camera_down(args.degrees)
        elif args.command == "camera":
            result = marvin.camera_center()
        elif args.servo_command == "status":
            result = marvin.projector_status()
        elif args.servo_command == "power":
            result = marvin.projector_power(args.state)
        elif args.servo_command == "up":
            result = marvin.projector_up(args.degrees)
        elif args.servo_command == "down":
            result = marvin.projector_down(args.degrees)
        else:
            result = marvin.projector_center()
    except (OSError, ValueError, SessionError,
            subprocess.SubprocessError, KeyboardInterrupt) as error:
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
