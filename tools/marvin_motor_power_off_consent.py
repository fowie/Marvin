"""Literal, mutually exclusive operator declarations; never power permission."""

import functools
import sys

PREPARATION_FLAGS = (
    "motor_supply_off", "motor_left_only_connected", "motor_right_and_servos_isolated",
    "authorize_unvalidated_zero_velocity", "unprivileged_usbmon", "new_boot_declared",
)
OBSERVATION_FLAGS = (
    "motor_left_only_connected", "motor_right_and_servos_isolated",
    "left_motor_powered_observation", "operator_at_external_cutoff", "unprivileged_usbmon",
)
OBSERVATION_ONLY_FLAGS = ("left_motor_powered_observation", "operator_at_external_cutoff")


def classify(*, actuators_isolated=False, left_motor_powered_observation=False,
             operator_at_external_cutoff=False, **declarations):
    """Keep validate's historical boolean contract; classify the new scope separately."""
    new = dict(left_motor_powered_observation=left_motor_powered_observation,
               operator_at_external_cutoff=operator_at_external_cutoff)
    if any(type(value) is not bool for value in (actuators_isolated, *new.values(),
                                                *declarations.values())):
        raise ValueError("All operator declarations must be literal booleans.")
    if set(declarations) - set(PREPARATION_FLAGS):
        raise ValueError("Unknown operator declaration.")
    if any(new.values()):
        flags = {**{name: declarations.get(name, False) for name in PREPARATION_FLAGS}, **new}
        if (actuators_isolated or not all(flags[name] for name in OBSERVATION_FLAGS)
                or any(flags[name] for name in set(PREPARATION_FLAGS) - set(OBSERVATION_FLAGS))):
            raise ValueError("Powered observation requires all current separate declarations; full isolation, "
                             "motor-supply-OFF and preparation/zero declarations are forbidden.")
        return "left_motor_powered_observation"
    return "preparation" if validate(actuators_isolated=actuators_isolated, **declarations) else "isolated"


def notify_cut_power(error):
    print("OPERATOR: CUT EXTERNAL POWER NOW - powered observation failed/unknown: "
          f"{str(error)[:256]}. Host cannot remove energy; closing the tty does not stop a motor.",
          file=sys.stderr, flush=True)


def powered_faults(operation):
    """Catch even validation/startup/sealing failures outside the inner cleanup scope."""
    @functools.wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except BaseException as error:
            if kwargs.get("left_motor_powered_observation") is True:
                notify_cut_power(error)
            raise
    return wrapped


def add_observation_arguments(parser):
    for name in OBSERVATION_ONLY_FLAGS:
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")


def observation_arguments(args):
    return {name: getattr(args, name) for name in OBSERVATION_ONLY_FLAGS}


def parse_observation_arguments(parser, argv):
    argv = sys.argv[1:] if argv is None else argv
    try:
        return parser.parse_args(argv)
    except SystemExit as error:
        if error.code and "--left-motor-powered-observation" in argv:
            notify_cut_power("Invalid powered observation CLI arguments; no run started.")
        raise


def observation_history(declarations):
    return {
        **history(declarations),
        "actuator_power_and_signal_isolation_acknowledged": False,
        "motor_supply_off_acknowledged": False,
        "scope": "left_motor_powered_observation",
        "outcome_meaning": "observation_only_not_stop_or_commissioning",
        "host_can_remove_energy": False,
        "physical_movement_detection": "operator_only_continuous_watch_including_boot_and_open",
        "new_boot_basis": "not_claimed",
    }


def validate(*, actuators_isolated=False, **declarations):
    if set(declarations) - set(PREPARATION_FLAGS):
        raise ValueError("Unknown motor-power-OFF declaration.")
    flags = {name: declarations.get(name, False) for name in PREPARATION_FLAGS}
    if any(type(value) is not bool for value in (actuators_isolated, *flags.values())):
        raise ValueError("Isolation and preparation declarations must be literal booleans.")
    preparation = any(flags.values())
    if preparation:
        if actuators_isolated or not all(flags.values()):
            raise ValueError("Motor-power-OFF preparation requires all separate declarations and forbids full isolation.")
    elif not actuators_isolated:
        raise ValueError("Explicit --actuators-isolated isolation confirmation is required.")
    return preparation


def add_arguments(parser):
    for name in PREPARATION_FLAGS:
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")


def arguments(args):
    return {name: getattr(args, name) for name in PREPARATION_FLAGS}


def history(declarations):
    return {
        "historical_operator_declarations": dict(declarations),
        "declarations_are_current_permission": False,
        "motor_supply_on_permission": "not_granted",
        "physical_stop": "not_established",
        "application_acknowledgment": "not_established",
        "new_boot_basis": "operator_declaration_not_enumeration_proof",
    }
