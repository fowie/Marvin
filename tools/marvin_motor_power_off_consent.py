"""Literal, mutually exclusive operator declarations; never power permission."""

PREPARATION_FLAGS = (
    "motor_supply_off", "motor_left_only_connected", "motor_right_and_servos_isolated",
    "authorize_unvalidated_zero_velocity", "unprivileged_usbmon", "new_boot_declared",
)


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
