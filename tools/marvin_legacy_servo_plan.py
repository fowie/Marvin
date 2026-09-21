"""Build an offline-only legacy two-word servo mapping transcript.

This module never opens hardware. It deliberately calls the legacy words
``word0`` and ``word1`` because their physical actuator ordering is unproved.
"""

import argparse
import hashlib
import json

from tools import marvin_legacy_protocol as protocol


SET_SERVO_POSITION = 0x1E
CONNECTED_SERVOS = ("projector-tilt-only", "front-camera-tilt-only")
UI_HINT = "PCTestApp text says 0..3000, but its parser accepts the full uint16 range."


def _uint16(name, value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError(f"{name} must be an integer fitting uint16.")
    return value


def prepare(*, word, baseline_words, target, minimum, maximum, neutral,
            connected_servo, profile_source, first_sequence=3500):
    if type(word) is not int or word not in (0, 1):
        raise ValueError("Word must be 0 or 1; physical channel ordering is not established.")
    if (type(baseline_words) is not tuple or len(baseline_words) != 2):
        raise ValueError("Baseline must be an exact two-word tuple.")
    baseline = tuple(
        _uint16(f"baseline word {index}", value)
        for index, value in enumerate(baseline_words)
    )
    target = _uint16("Target", target)
    minimum = _uint16("Minimum", minimum)
    maximum = _uint16("Maximum", maximum)
    neutral = _uint16("Neutral", neutral)
    if minimum > maximum:
        raise ValueError("Minimum must not exceed maximum.")
    if not minimum <= target <= maximum or not minimum <= neutral <= maximum:
        raise ValueError("Target and neutral must fit the exact declared profile.")
    if baseline[word] != neutral:
        raise ValueError("The selected baseline word must equal the declared neutral.")
    if target == neutral:
        raise ValueError("Target must differ from neutral for a mapping observation.")
    if connected_servo not in CONNECTED_SERVOS:
        raise ValueError("Declare exactly one connected physical servo.")
    if type(profile_source) is not str or not profile_source.strip():
        raise ValueError("An exact reviewed profile source citation is required.")
    if type(first_sequence) is not int or not 0 <= first_sequence <= 65532:
        raise ValueError("First sequence must leave room for four uint16 sequence values.")

    trial = list(baseline)
    trial[word] = target
    baseline_payload = b"".join(value.to_bytes(2, "little") for value in baseline)
    trial_payload = b"".join(value.to_bytes(2, "little") for value in trial)
    transcript = (
        protocol.get_servo_position_request(first_sequence),
        protocol.encode_request(first_sequence + 1, SET_SERVO_POSITION, trial_payload),
        protocol.encode_request(first_sequence + 2, SET_SERVO_POSITION, baseline_payload),
        protocol.get_servo_position_request(first_sequence + 3),
    )
    return {
        "status": "offline_plan_only",
        "live_execution_authorized": False,
        "profile": "marvin-legacy-se",
        "connected_servo_declaration": connected_servo,
        "candidate_wire_word": word,
        "physical_channel_assignment": "not_established",
        "baseline_words_uint16": list(baseline),
        "trial_words_uint16": trial,
        "declared_profile": {
            "minimum": minimum,
            "maximum": maximum,
            "neutral": neutral,
            "source": profile_source,
            "caller_declared_not_authenticated": True,
        },
        "immutable_application_transcript_hex": [frame.hex() for frame in transcript],
        "transcript_sha256": hashlib.sha256(b"".join(transcript)).hexdigest(),
        "steps": ["baseline_getter", "one_word_setter", "one_cleanup_restore", "verify_getter"],
        "maximum_writes": 4,
        "maximum_application_bytes": sum(map(len, transcript)),
        "automatic_retries": False,
        "automatic_reconnect": False,
        "cleanup_attempts": 1,
        "fixed_cadence": None,
        "required_before_future_live_use": [
            "separate_explicit_operator_authorization",
            "exact_installed_legacy_identity_and_physical_port",
            "only_the_declared_servo_connected_and_other_servo_isolated",
            "robot_secured_and_operator_at_external_cutoff",
            "reviewed_exact_bounds_neutral_and_restore_profile",
            "fresh_two_word_baseline_correlated_to_getter",
            "one_cleanup_restore_attempt_on_every_post_setter_exit",
            "no_retry_no_reconnect_no_resume",
            "sealed_serial_and_usb_evidence_with_raw_bytes",
        ],
        "limitations": [
            UI_HINT,
            "The getter word order and setter-to-actuator order are not proved.",
            "Reported values are not angles, pulse widths, calibrated limits or safe mechanics.",
            "A matching reply or restored getter value does not prove physical restoration.",
            "Successor SetServoRadians, joint IDs, ranges and startup behavior do not apply.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--word", type=int, required=True, choices=(0, 1))
    parser.add_argument("--baseline", type=int, nargs=2, required=True, metavar=("WORD0", "WORD1"))
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--minimum", type=int, required=True)
    parser.add_argument("--maximum", type=int, required=True)
    parser.add_argument("--neutral", type=int, required=True)
    parser.add_argument("--connected-servo", choices=CONNECTED_SERVOS, required=True)
    parser.add_argument("--profile-source", required=True)
    parser.add_argument("--first-sequence", type=int, default=3500)
    args = parser.parse_args(argv)
    try:
        result = prepare(
            word=args.word, baseline_words=tuple(args.baseline), target=args.target,
            minimum=args.minimum, maximum=args.maximum, neutral=args.neutral,
            connected_servo=args.connected_servo, profile_source=args.profile_source,
            first_sequence=args.first_sequence,
        )
    except ValueError as error:
        print(json.dumps({"status": "input_error", "offline_only": True, "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
