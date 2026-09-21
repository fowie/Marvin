"""Named bounded drive steps over the proved fixed raw-PWM profiles.

Offline by default. Live use remains an on-blocks operator-authorized
diagnostic, not calibrated motion control.
"""

import argparse
import json
from pathlib import Path
import sys

from tools import marvin_legacy_raw_pwm_pilot as pilot
from tools import marvin_motor_power_off_consent as consent


RAW_PWM = 2000
DURATION_SECONDS = 0.25
DIRECTIONS = {
    "forward": consent.RAW_PWM_DUAL_FORWARD_CONNECTED_SCOPE,
    "backward": consent.RAW_PWM_DUAL_REVERSE_CONNECTED_SCOPE,
    "rotate-left": consent.RAW_PWM_LEFT_REVERSE_RIGHT_FORWARD_SCOPE,
    "rotate-right": consent.RAW_PWM_LEFT_FORWARD_RIGHT_BACKWARD_SCOPE,
}
COMMON_FLAGS = (
    "physical_left_motor_connected_to_robot_right_motor_l_connector",
    "physical_right_motor_connected_to_robot_left_motor_r_connector",
    "motor_left_connected",
    "motor_right_connected",
    "servos_isolated",
    "both_encoder_feedback_connected",
    "robot_secured_on_blocks",
    "operator_at_external_cutoff",
    "unprivileged_usbmon",
)


def _profile(direction, duration, raw_pwm):
    if direction not in DIRECTIONS:
        raise ValueError(f"Direction must be one of: {', '.join(DIRECTIONS)}.")
    if type(raw_pwm) is not int or not 1 <= raw_pwm <= RAW_PWM:
        raise ValueError(f"Raw PWM must be an integer from 1 through {RAW_PWM}.")
    if raw_pwm != RAW_PWM:
        raise ValueError("Only the proved raw-PWM value 2000 is admitted.")
    if type(duration) not in (int, float) or duration != DURATION_SECONDS:
        raise ValueError("Only the proved 0.25-second bounded duration is admitted.")
    return DIRECTIONS[direction]


def prepare(direction, *, duration, raw_pwm):
    scope = _profile(direction, duration, raw_pwm)
    result = pilot.prepare(scope)
    result.update(
        interface="named_bounded_drive_step",
        direction=direction,
        duration_seconds=duration,
        raw_pwm=raw_pwm,
        raw_pwm_units="unvalidated_uint16_wire_value_not_duty_cycle",
        physical_effect="operator_observation_not_protocol_evidence",
        physical_stop="not_established",
        available_directions=list(DIRECTIONS),
        required=[
            "--run",
            "--authorize-unvalidated-drive-step",
            "--expected-physical-port",
            "--output NEWDIR",
            *("--" + name.replace("_", "-") for name in COMMON_FLAGS),
        ],
    )
    return result


def run_step(output, *, direction, duration, raw_pwm, expected_physical_port,
             run=False, authorize_unvalidated_drive_step=False, **declarations):
    scope = _profile(direction, duration, raw_pwm)
    if run is not True or authorize_unvalidated_drive_step is not True:
        raise ValueError("Literal --run and --authorize-unvalidated-drive-step are required.")
    required = set(COMMON_FLAGS)
    if set(declarations) != required or any(declarations[name] is not True for name in required):
        raise ValueError("Live drive steps require every physical safety declaration.")
    profile_flags = dict.fromkeys(consent.POWERED_TRIAL_SCOPES[scope], True)
    return pilot.run_diagnostic(
        output, expected_physical_port=expected_physical_port, run=True,
        **profile_flags)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=DIRECTIONS)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--raw-pwm", type=int, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--authorize-unvalidated-drive-step", action="store_true")
    for name in COMMON_FLAGS:
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    declarations = {name: getattr(args, name) for name in COMMON_FLAGS}
    try:
        if args.run:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_step(
                args.output, direction=args.direction, duration=args.duration,
                raw_pwm=args.raw_pwm,
                expected_physical_port=args.expected_physical_port, run=True,
                authorize_unvalidated_drive_step=args.authorize_unvalidated_drive_step,
                **declarations)
        else:
            if (args.output or args.expected_physical_port
                    or args.authorize_unvalidated_drive_step
                    or any(declarations.values())):
                raise ValueError("Live-only arguments require --run.")
            result = prepare(
                args.direction, duration=args.duration, raw_pwm=args.raw_pwm)
    except (Exception, KeyboardInterrupt) as error:
        if args.run:
            consent.notify_powered_trial_fault(error)
        print(json.dumps({
            "status": "failed",
            "error": str(error),
            "physical_stop": "not_established",
        }), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
