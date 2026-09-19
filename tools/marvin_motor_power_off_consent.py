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
ENCODER_ONLY_FLAGS = (
    "encoder_feedback_observation", "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected",
)
ENCODER_FLAGS = (*ENCODER_ONLY_FLAGS, "unprivileged_usbmon")
POWERED_TRIAL_ONLY_FLAGS = (
    "powered_left_stop_characterization", "motor_left_connected",
    "motor_right_disconnected", "robot_secured_on_blocks",
    "authorize_unvalidated_left_one_and_zero",
)
POWERED_TRIAL_FLAGS = (*POWERED_TRIAL_ONLY_FLAGS, "both_encoder_feedback_connected",
                       "servos_isolated", "operator_at_external_cutoff",
                       "unprivileged_usbmon")
MAPPING_TRIAL_SCOPE = "powered_left_command_right_connected"
MAPPING_TRIAL_ONLY_FLAGS = (
    MAPPING_TRIAL_SCOPE, "motor_left_disconnected", "motor_right_connected",
)
MAPPING_TRIAL_FLAGS = (
    *MAPPING_TRIAL_ONLY_FLAGS, "robot_secured_on_blocks",
    "authorize_unvalidated_left_one_and_zero", "both_encoder_feedback_connected",
    "servos_isolated", "operator_at_external_cutoff", "unprivileged_usbmon",
)
DISCONNECTED_ORDER_SCOPE = "disconnected_load_zero_one_order_diagnostic"
DISCONNECTED_ORDER_ONLY_FLAGS = (
    DISCONNECTED_ORDER_SCOPE, "authorize_unvalidated_zero_one_order_diagnostic",
)
DISCONNECTED_ORDER_FLAGS = (
    *DISCONNECTED_ORDER_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
DISCONNECTED_PLUS_1000_SCOPE = "disconnected_load_zero_plus_1000_order_diagnostic"
DISCONNECTED_PLUS_1000_ONLY_FLAGS = (
    DISCONNECTED_PLUS_1000_SCOPE, "authorize_unvalidated_left_plus_1000_order_diagnostic",
)
DISCONNECTED_PLUS_1000_FLAGS = (
    *DISCONNECTED_PLUS_1000_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
DISCONNECTED_VELOCITY_TRAIN_SCOPE = "disconnected_load_left_plus_1000_velocity_train"
DISCONNECTED_VELOCITY_TRAIN_ONLY_FLAGS = (
    DISCONNECTED_VELOCITY_TRAIN_SCOPE,
    "authorize_unvalidated_left_plus_1000_velocity_train",
)
DISCONNECTED_VELOCITY_TRAIN_FLAGS = (
    *DISCONNECTED_VELOCITY_TRAIN_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE = "disconnected_load_right_plus_1000_velocity_train"
DISCONNECTED_RIGHT_VELOCITY_TRAIN_ONLY_FLAGS = (
    DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE,
    "authorize_unvalidated_right_plus_1000_velocity_train",
)
DISCONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS = (
    *DISCONNECTED_RIGHT_VELOCITY_TRAIN_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
DISCONNECTED_VELOCITY_TRAIN_SCOPES = (
    DISCONNECTED_VELOCITY_TRAIN_SCOPE, DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE,
)
DISCONNECTED_ORDER_SCOPES = (DISCONNECTED_ORDER_SCOPE, DISCONNECTED_PLUS_1000_SCOPE)
DISCONNECTED_MOTOR_SETTER_SCOPES = (
    *DISCONNECTED_ORDER_SCOPES, *DISCONNECTED_VELOCITY_TRAIN_SCOPES,
)
DISCONNECTED_GET_LOG_SCOPE = "disconnected_load_get_log"
DISCONNECTED_GET_LOG_ONLY_FLAGS = (DISCONNECTED_GET_LOG_SCOPE,)
DISCONNECTED_GET_LOG_FLAGS = (
    *DISCONNECTED_GET_LOG_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
DISCONNECTED_GETTER_SURVEY_SCOPE = "disconnected_load_legacy_getter_survey"
DISCONNECTED_GETTER_SURVEY_ONLY_FLAGS = (DISCONNECTED_GETTER_SURVEY_SCOPE,)
DISCONNECTED_GETTER_SURVEY_FLAGS = (
    *DISCONNECTED_GETTER_SURVEY_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
DISCONNECTED_LED_STATE_SCOPE = "disconnected_load_led_state_round_trip"
DISCONNECTED_LED_STATE_ONLY_FLAGS = (
    DISCONNECTED_LED_STATE_SCOPE, "authorize_unvalidated_led_state_round_trip",
)
DISCONNECTED_LED_STATE_FLAGS = (
    *DISCONNECTED_LED_STATE_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
LED_MAPPING_SCOPE = "disconnected_load_led_mapping_phase"
LED_MAPPING_ONLY_FLAGS = (
    LED_MAPPING_SCOPE, "authorize_unvalidated_led_mapping_phase",
)
LED_MAPPING_FLAGS = (
    *LED_MAPPING_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
WHEEL_LED_BLINK_SCOPE = "disconnected_load_wheel_led_blink_pilot"
WHEEL_LED_BLINK_ONLY_FLAGS = (
    WHEEL_LED_BLINK_SCOPE, "authorize_unvalidated_wheel_led_blink_pilot",
)
WHEEL_LED_BLINK_FLAGS = (
    *WHEEL_LED_BLINK_ONLY_FLAGS, "motor_power_plugs_disconnected",
    "servos_isolated", "both_encoder_feedback_connected", "robot_secured_on_blocks",
    "operator_at_external_cutoff", "unprivileged_usbmon",
)
POWERED_TRIAL_SCOPES = {
    "powered_left_stop_characterization": POWERED_TRIAL_FLAGS,
    MAPPING_TRIAL_SCOPE: MAPPING_TRIAL_FLAGS,
    DISCONNECTED_ORDER_SCOPE: DISCONNECTED_ORDER_FLAGS,
    DISCONNECTED_PLUS_1000_SCOPE: DISCONNECTED_PLUS_1000_FLAGS,
    DISCONNECTED_VELOCITY_TRAIN_SCOPE: DISCONNECTED_VELOCITY_TRAIN_FLAGS,
    DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE: DISCONNECTED_RIGHT_VELOCITY_TRAIN_FLAGS,
    DISCONNECTED_GET_LOG_SCOPE: DISCONNECTED_GET_LOG_FLAGS,
    DISCONNECTED_GETTER_SURVEY_SCOPE: DISCONNECTED_GETTER_SURVEY_FLAGS,
    DISCONNECTED_LED_STATE_SCOPE: DISCONNECTED_LED_STATE_FLAGS,
    LED_MAPPING_SCOPE: LED_MAPPING_FLAGS,
    WHEEL_LED_BLINK_SCOPE: WHEEL_LED_BLINK_FLAGS,
}
ALL_FLAGS = tuple(dict.fromkeys((*PREPARATION_FLAGS, *OBSERVATION_ONLY_FLAGS,
                                *ENCODER_ONLY_FLAGS, *POWERED_TRIAL_ONLY_FLAGS,
                                *MAPPING_TRIAL_ONLY_FLAGS, *DISCONNECTED_ORDER_ONLY_FLAGS,
                                *DISCONNECTED_PLUS_1000_ONLY_FLAGS,
                                *DISCONNECTED_VELOCITY_TRAIN_ONLY_FLAGS,
                                *DISCONNECTED_RIGHT_VELOCITY_TRAIN_ONLY_FLAGS,
                                *DISCONNECTED_GET_LOG_ONLY_FLAGS,
                                *DISCONNECTED_GETTER_SURVEY_ONLY_FLAGS,
                                *DISCONNECTED_LED_STATE_ONLY_FLAGS,
                                *LED_MAPPING_ONLY_FLAGS, *WHEEL_LED_BLINK_ONLY_FLAGS)))


def classify(*, actuators_isolated=False, left_motor_powered_observation=False,
             operator_at_external_cutoff=False, encoder_feedback_observation=False,
             motor_power_plugs_disconnected=False, servos_isolated=False,
             both_encoder_feedback_connected=False, powered_left_stop_characterization=False,
             motor_left_connected=False, motor_right_disconnected=False,
             robot_secured_on_blocks=False, authorize_unvalidated_left_one_and_zero=False,
             powered_left_command_right_connected=False,
             motor_left_disconnected=False, motor_right_connected=False,
             disconnected_load_zero_one_order_diagnostic=False,
             authorize_unvalidated_zero_one_order_diagnostic=False,
             disconnected_load_zero_plus_1000_order_diagnostic=False,
             authorize_unvalidated_left_plus_1000_order_diagnostic=False,
             disconnected_load_left_plus_1000_velocity_train=False,
             authorize_unvalidated_left_plus_1000_velocity_train=False,
             disconnected_load_right_plus_1000_velocity_train=False,
             authorize_unvalidated_right_plus_1000_velocity_train=False,
             disconnected_load_get_log=False,
             disconnected_load_legacy_getter_survey=False,
             disconnected_load_led_state_round_trip=False,
             authorize_unvalidated_led_state_round_trip=False,
             disconnected_load_led_mapping_phase=False,
             authorize_unvalidated_led_mapping_phase=False,
             disconnected_load_wheel_led_blink_pilot=False,
             authorize_unvalidated_wheel_led_blink_pilot=False,
             **declarations):
    """Keep validate's historical boolean contract; classify the new scope separately."""
    new = dict(left_motor_powered_observation=left_motor_powered_observation,
               operator_at_external_cutoff=operator_at_external_cutoff)
    encoder = dict(encoder_feedback_observation=encoder_feedback_observation,
                   motor_power_plugs_disconnected=motor_power_plugs_disconnected,
                   servos_isolated=servos_isolated,
                   both_encoder_feedback_connected=both_encoder_feedback_connected)
    trial = dict(powered_left_stop_characterization=powered_left_stop_characterization,
                 motor_left_connected=motor_left_connected,
                 motor_right_disconnected=motor_right_disconnected,
                 robot_secured_on_blocks=robot_secured_on_blocks,
                 authorize_unvalidated_left_one_and_zero=authorize_unvalidated_left_one_and_zero,
                 powered_left_command_right_connected=powered_left_command_right_connected,
                 motor_left_disconnected=motor_left_disconnected,
                 motor_right_connected=motor_right_connected,
                 disconnected_load_zero_one_order_diagnostic=disconnected_load_zero_one_order_diagnostic,
                 authorize_unvalidated_zero_one_order_diagnostic=authorize_unvalidated_zero_one_order_diagnostic,
                 disconnected_load_zero_plus_1000_order_diagnostic=(
                     disconnected_load_zero_plus_1000_order_diagnostic),
                 authorize_unvalidated_left_plus_1000_order_diagnostic=(
                     authorize_unvalidated_left_plus_1000_order_diagnostic),
                 disconnected_load_left_plus_1000_velocity_train=(
                     disconnected_load_left_plus_1000_velocity_train),
                 authorize_unvalidated_left_plus_1000_velocity_train=(
                     authorize_unvalidated_left_plus_1000_velocity_train),
                 disconnected_load_right_plus_1000_velocity_train=(
                     disconnected_load_right_plus_1000_velocity_train),
                 authorize_unvalidated_right_plus_1000_velocity_train=(
                     authorize_unvalidated_right_plus_1000_velocity_train),
                 disconnected_load_get_log=disconnected_load_get_log,
                 disconnected_load_legacy_getter_survey=disconnected_load_legacy_getter_survey,
                 disconnected_load_led_state_round_trip=disconnected_load_led_state_round_trip,
                 authorize_unvalidated_led_state_round_trip=(
                     authorize_unvalidated_led_state_round_trip),
                 disconnected_load_led_mapping_phase=disconnected_load_led_mapping_phase,
                 authorize_unvalidated_led_mapping_phase=(
                     authorize_unvalidated_led_mapping_phase),
                 disconnected_load_wheel_led_blink_pilot=(
                     disconnected_load_wheel_led_blink_pilot),
                 authorize_unvalidated_wheel_led_blink_pilot=(
                     authorize_unvalidated_wheel_led_blink_pilot))
    if any(type(value) is not bool for value in (actuators_isolated, *trial.values(), *new.values(),
                                                *encoder.values(), *declarations.values())):
        raise ValueError("All operator declarations must be literal booleans.")
    if set(declarations) - set(PREPARATION_FLAGS):
        raise ValueError("Unknown operator declaration.")
    if any(trial.values()):
        flags = {**declarations, **new, **encoder, **trial}
        for scope, required in POWERED_TRIAL_SCOPES.items():
            if (not actuators_isolated and all(flags.get(name) is True for name in required)
                    and not any(flags.get(name, False) for name in set(ALL_FLAGS) - set(required))):
                return scope
        raise ValueError("Active diagnostic requires its complete separate literal command/load scope; no mixed scopes.")
    if any(encoder.values()):
        if (actuators_isolated or not all(encoder.values())
                or declarations.get("unprivileged_usbmon") is not True or any(new.values())
                or any(declarations.get(name, False) for name in PREPARATION_FLAGS
                       if name != "unprivileged_usbmon")):
            raise ValueError("Encoder observation requires both motor POWER plugs disconnected, servo "
                             "isolation and both complete encoder harnesses connected; no mixed scopes.")
        return "encoder_feedback_observation"
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


def notify_powered_trial_fault(error):
    try:
        print("CUT_POWER_REQUIRED: OPERATOR CUT HY1803D POWER independently NOW. "
              "Motor output or stop state is UNVERIFIED. "
              "Host cannot remove energy; closing the tty is NOT stopping. "
              f"Fault: {str(error)[:512]}. Do not retain power for logging.",
              file=sys.stderr, flush=True)
    except (OSError, ValueError) as delivery_error:
        if isinstance(error, BaseException):
            error.add_note(f"External-cutoff diagnostic delivery failed: {delivery_error}")


def notify_collection_ended(error):
    print("COLLECTION_ENDED: no movement window remains; if startup failed, no movement window "
          f"opened. Stop manual movement. {str(error)[:256]}. Preliminary evidence only; "
          "not power permission or a power cut.", file=sys.stderr, flush=True)


def powered_faults(operation):
    """Catch even validation/startup/sealing failures outside the inner cleanup scope."""
    @functools.wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except BaseException as error:
            if any(kwargs.get(scope) is True for scope in POWERED_TRIAL_SCOPES):
                notify_powered_trial_fault(error)
            if kwargs.get("left_motor_powered_observation") is True:
                notify_cut_power(error)
            if kwargs.get("encoder_feedback_observation") is True:
                notify_collection_ended(error)
            raise
    return wrapped


def add_observation_arguments(parser):
    for name in (*OBSERVATION_ONLY_FLAGS, *ENCODER_ONLY_FLAGS):
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")


def add_powered_trial_arguments(parser):
    for name in (*POWERED_TRIAL_ONLY_FLAGS, *MAPPING_TRIAL_ONLY_FLAGS,
                 *DISCONNECTED_ORDER_ONLY_FLAGS, *DISCONNECTED_PLUS_1000_ONLY_FLAGS,
                 *DISCONNECTED_VELOCITY_TRAIN_ONLY_FLAGS,
                 *DISCONNECTED_RIGHT_VELOCITY_TRAIN_ONLY_FLAGS,
                 *DISCONNECTED_GET_LOG_ONLY_FLAGS, *DISCONNECTED_GETTER_SURVEY_ONLY_FLAGS,
                 *DISCONNECTED_LED_STATE_ONLY_FLAGS, *LED_MAPPING_ONLY_FLAGS,
                 *WHEEL_LED_BLINK_ONLY_FLAGS):
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")


def powered_trial_arguments(args):
    return {name: getattr(args, name) for name in (
        *POWERED_TRIAL_ONLY_FLAGS, *MAPPING_TRIAL_ONLY_FLAGS, *DISCONNECTED_ORDER_ONLY_FLAGS,
        *DISCONNECTED_PLUS_1000_ONLY_FLAGS, *DISCONNECTED_GET_LOG_ONLY_FLAGS,
        *DISCONNECTED_VELOCITY_TRAIN_ONLY_FLAGS,
        *DISCONNECTED_RIGHT_VELOCITY_TRAIN_ONLY_FLAGS,
        *DISCONNECTED_GETTER_SURVEY_ONLY_FLAGS, *DISCONNECTED_LED_STATE_ONLY_FLAGS,
        *LED_MAPPING_ONLY_FLAGS, *WHEEL_LED_BLINK_ONLY_FLAGS)}


def observation_arguments(args):
    return {name: getattr(args, name) for name in (*OBSERVATION_ONLY_FLAGS, *ENCODER_ONLY_FLAGS)}


def parse_observation_arguments(parser, argv):
    argv = sys.argv[1:] if argv is None else argv
    try:
        return parser.parse_args(argv)
    except SystemExit as error:
        if error.code and any("--" + scope.replace("_", "-") in argv for scope in POWERED_TRIAL_SCOPES):
            notify_powered_trial_fault("Invalid powered-trial CLI arguments; no run started.")
        if error.code and "--left-motor-powered-observation" in argv:
            notify_cut_power("Invalid powered observation CLI arguments; no run started.")
        if error.code and "--encoder-feedback-observation" in argv:
            notify_collection_ended("Invalid encoder observation CLI arguments; no run started.")
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


def encoder_history(declarations):
    return {
        **history(declarations),
        "actuator_power_and_signal_isolation_acknowledged": False,
        "motor_supply_off_acknowledged": False,
        "scope": "encoder_feedback_observation",
        "load_scope": "MOTORLOAD-DISCONNECTED",
        "both_encoder_harnesses": "operator_declared_fully_connected_including_logic_power_and_reference",
        "controller_and_shared_hy_power": "operator_declared_powered",
        "outcome_meaning": "raw_observation_only_not_calibration_or_power_permission",
        "host_can_remove_energy": False,
        "new_boot_basis": "not_claimed",
    }


def powered_trial_history(declarations):
    scope = classify(**declarations)
    if scope not in POWERED_TRIAL_SCOPES:
        raise ValueError("Powered trial history requires a complete powered-trial scope.")
    if scope in (DISCONNECTED_GET_LOG_SCOPE, DISCONNECTED_GETTER_SURVEY_SCOPE):
        return {
            **encoder_history(declarations),
            "scope": scope,
            "load_scope": "MOTOR_POWER_PLUGS_DISCONNECTED",
            "outcome_meaning": (
                "raw_legacy_getter_survey_only_not_application_acknowledgment"
                if scope == DISCONNECTED_GETTER_SURVEY_SCOPE
                else "raw_get_log_observation_only_not_application_acknowledgment"),
            "host_can_remove_energy": False,
            "physical_stop": "not_established",
        }
    if scope == DISCONNECTED_LED_STATE_SCOPE:
        return {
            **encoder_history(declarations),
            "scope": scope,
            "load_scope": "MOTOR_POWER_PLUGS_DISCONNECTED",
            "outcome_meaning": (
                "protocol_and_getter_vector_verification_separate_from_operator_led_observation"),
            "unvalidated_led_state_round_trip_authorized": True,
            "planned_restore_policy": (
                "one_fixed_baseline_restore_attempt_after_any_possible_test_setter_submission"),
            "operator_led_observation": "not_recorded_by_software",
            "host_can_remove_energy": False,
            "physical_stop": "not_established",
        }
    if scope == LED_MAPPING_SCOPE:
        return {
            **encoder_history(declarations),
            "scope": scope,
            "load_scope": "MOTOR_POWER_PLUGS_DISCONNECTED",
            "outcome_meaning": (
                "interactive_led_mapping_protocol_evidence_separate_from_operator_observation"),
            "unvalidated_led_mapping_phase_authorized": True,
            "restore_required_until_verified_or_operator_power_cycle_confirmation": True,
            "operator_led_observation": "external_between_sealed_set_and_restore_phases",
            "host_can_remove_energy": False,
            "physical_stop": "not_established",
        }
    if scope == WHEEL_LED_BLINK_SCOPE:
        return {
            **encoder_history(declarations),
            "scope": scope,
            "load_scope": "MOTOR_POWER_PLUGS_DISCONNECTED",
            "outcome_meaning": "wheel_led_blink_protocol_and_operator_observation",
            "unvalidated_wheel_led_blink_pilot_authorized": True,
            "planned_restore_policy": (
                "reverse_order_exact_blink_then_led_state_once_after_possible_setter_syscall"),
            "restoration_verification": (
                "getter_only_after_raw80_else_physical_confirmation_and_power_cycle_required"),
            "host_can_remove_energy": False,
            "physical_stop": "not_established",
        }
    return {
        **encoder_history(declarations),
        "scope": scope,
        "load_scope": (
            "MOTOR_POWER_PLUGS_DISCONNECTED" if scope in DISCONNECTED_MOTOR_SETTER_SCOPES
            else "MOTOR_L_DISCONNECTED_MOTOR_R_CONNECTED" if scope == MAPPING_TRIAL_SCOPE
            else "MOTOR_L_CONNECTED_MOTOR_R_DISCONNECTED"),
        "outcome_meaning": (
            "fixed_velocity_train_scope_observation_separate_from_protocol_status"
            if scope in DISCONNECTED_VELOCITY_TRAIN_SCOPES
            else "raw_order_status_observation_only_not_protocol_inferred"
            if scope in DISCONNECTED_MOTOR_SETTER_SCOPES
            else "operator_motion_and_stop_observations_required_not_protocol_inferred"),
        **({("unvalidated_zero_one_order_diagnostic_authorized"
             if scope == DISCONNECTED_ORDER_SCOPE
             else "unvalidated_right_plus_1000_velocity_train_authorized"
             if scope == DISCONNECTED_RIGHT_VELOCITY_TRAIN_SCOPE
             else "unvalidated_left_plus_1000_velocity_train_authorized"
             if scope == DISCONNECTED_VELOCITY_TRAIN_SCOPE
             else "unvalidated_left_plus_1000_order_diagnostic_authorized"): True,
            "planned_zero_policy": (
                "one_fixed_cleanup_syscall_after_any_possible_nonzero_train_submission"
                if scope in DISCONNECTED_VELOCITY_TRAIN_SCOPES
                else "one_immediate_cleanup_attempt_after_fully_accepted_nonzero")}
           if scope in DISCONNECTED_MOTOR_SETTER_SCOPES else
           {"unvalidated_left_one_and_zero_authorized": True,
            "planned_zero_policy": "attempt_once_after_fully_accepted_start_on_same_owned_fd"}),
        **({("raw_setter_units"
              if scope == DISCONNECTED_PLUS_1000_SCOPE
              or scope in DISCONNECTED_VELOCITY_TRAIN_SCOPES
              else "raw_one_units"):
            "unvalidated_raw_word_not_physical_speed"}),
        "planned_zero_guaranteed": False,
        "physical_output_duration_bound": "not_established",
        "operator_observed_motion": "not_recorded_by_software",
        "operator_observed_stop_after_zero": "not_recorded_by_software",
        "operator_cutoff_stop_observation": "separate_later_trial_not_part_of_this_run",
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
