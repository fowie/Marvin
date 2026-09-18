"""Fixed reversible legacy LED-state round trip with disconnected loads.

Offline by default. This is software readiness, not live authorization.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from tools import marvin_legacy_zero as zero
from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet


BASELINE = bytes.fromhex("000000000000000000000000ff0000ff0000")
TEST_VECTOR = bytes.fromhex("010000000000000000000000ff0000ff0000")
TRANSCRIPT = tuple(bytes.fromhex(raw) for raw in (
    "53110c1700000075f145",
    "53120c18001200010000000000000000000000ff0000ff00008ab845",
    "53130c17000000741345",
    "53140c18001200000000000000000000000000ff0000ff000010fb45",
    "53150c17000000747545",
))
BASELINE_GETTER, TEST_SETTER, VERIFY_GETTER, RESTORE_SETTER, REVERIFY_GETTER = TRANSCRIPT
STEPS = {
    "baseline": BASELINE_GETTER,
    "test": TEST_SETTER,
    "verify": VERIFY_GETTER,
    "restore": RESTORE_SETTER,
    "reverify": REVERIFY_GETTER,
}
SERIAL_SECONDS = 10
RESPONSE_SECONDS = 0.500
CLEANUP_SECONDS = 5
SUCCESS = "disconnected_load_led_state_round_trip_complete_unverified"


def prepare():
    expected = (
        (3089, 0x17, b""),
        (3090, 0x18, TEST_VECTOR),
        (3091, 0x17, b""),
        (3092, 0x18, BASELINE),
        (3093, 0x17, b""),
    )
    for raw, fields in zip(TRANSCRIPT, expected):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                fields[0], fields[1], 0, fields[2]):
            raise ValueError("Fixed LED-state transcript disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "name": consent.DISCONNECTED_LED_STATE_SCOPE,
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "transcript_sha256": hashlib.sha256(b"".join(TRANSCRIPT)).hexdigest(),
        "baseline_payload_hex": BASELINE.hex(),
        "test_payload_hex": TEST_VECTOR.hex(),
        "fixed_change": {"index": 0, "from": 0, "to": 1},
        "maximum_application_bytes": sum(map(len, TRANSCRIPT)),
        "maximum_writes": len(TRANSCRIPT),
        "max_serial_rx_bytes": 8192,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "restore_policy": (
            "exactly_one_fixed_restore_attempt_after_any_possible_test_setter_submission"),
        "protocol_verification": "separate_from_physical_led_observation",
        "operator_led_observation": "record_before_during_and_after_outside_software",
        "physical_led_effect": "not_established",
        "physical_stop": "not_established",
        "operational_seconds": SERIAL_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "usb_nominal_seconds": 15,
        "usb_hard_seconds": 20,
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in consent.DISCONNECTED_LED_STATE_FLAGS)],
    }


class _LedStateTransport(LiveTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed_steps = []
        self.test_setter_may_have_been_submitted = False
        self.restore_attempted = False
        self.restore_response_clean = False

    def submit(self, step, *, deadline):
        if step not in STEPS:
            raise OSError("Only the fixed LED-state round-trip steps are permitted.")
        required = {
            "baseline": [],
            "test": ["baseline"],
            "verify": ["baseline", "test"],
            "reverify": ["baseline", "test", "verify", "restore"],
        }
        if step == "restore":
            return self._submit_restore_once(deadline=deadline)
        if self.completed_steps != required[step]:
            raise OSError("Fixed LED-state transcript order violated; no retry.")
        if step == "reverify" and not self.restore_response_clean:
            raise OSError("Reverification requires one clean restore response.")
        before = self.writes
        try:
            count = self._submit_once(STEPS[step], deadline=deadline)
        except BaseException:
            if step == "test" and self.writes > before:
                self.test_setter_may_have_been_submitted = True
            raise
        if step == "test" and count:
            self.test_setter_may_have_been_submitted = True
        if count == len(STEPS[step]):
            self.completed_steps.append(step)
        return count

    def _submit_restore_once(self, *, deadline):
        if self.restore_attempted:
            raise OSError("Fixed baseline restore has already been attempted; no retry.")
        if not self.test_setter_may_have_been_submitted:
            raise OSError("Restore is only admitted after possible test-setter submission.")
        self.restore_attempted = True
        self._check(deadline)
        self.ingress.expected_tx.append(RESTORE_SETTER)
        self.writes += 1
        count = os.write(self.fd, RESTORE_SETTER)
        self.last_write = time.monotonic()
        self.event("led_state_restore_returned", raw_hex=RESTORE_SETTER.hex(),
                   sequence=3092, accepted_bytes=count)
        if count == len(RESTORE_SETTER):
            self.completed_steps.append("restore")
        return count


def _submit(transport, report, step, *, deadline):
    raw = STEPS[step]
    report["uncertain_tx_bytes"] += len(raw)
    count = transport.submit(step, deadline=deadline)
    if type(count) is not int or not 0 <= count <= len(raw):
        raise OSError(f"Unknown {step} write result; no retry.")
    report["accepted_tx_bytes"] += count
    report["uncertain_tx_bytes"] -= count
    if count != len(raw):
        raise OSError(f"Partial {step} write; no retry.")


def _observe_one(transport, report, step, *, deadline, expected_payload=None,
                 clock=time.monotonic):
    packet_request = decode_packet(STEPS[step])
    response = zero._ResponseEvidence(
        transport.event, sequence=packet_request.sequence, command=packet_request.command,
        validate_packet=(
            None if expected_payload is None else
            lambda packet: ([] if packet.payload == expected_payload
                            else ["unexpected_led_state_payload"])))
    response.submitted_at = clock()
    response.deadline = min(deadline, response.submitted_at + RESPONSE_SECONDS)
    if response.deadline != response.submitted_at + RESPONSE_SECONDS:
        raise OSError(f"Insufficient remaining budget for {step} response.")
    report["active_response_step"] = step
    try:
        zero._observe_response(transport, response, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(response.events[0]["stream"]["raw_hex"]))
        report.setdefault("responses", []).append({
            "step": step,
            "sequence": packet.sequence,
            "command": packet.command,
            "raw_response_field": packet.response_field,
            "payload_bytes": len(packet.payload),
            "raw_payload_hex": packet.payload.hex(),
            "events": response.events,
        })
        return packet
    finally:
        report["active_response_step"] = None
        response.finish(clock())


def _attempt_restore(transport, report, *, clock=time.monotonic):
    cleanup_deadline = clock() + CLEANUP_SECONDS
    report["restore_attempted"] = True
    _submit(transport, report, "restore", deadline=cleanup_deadline)
    _observe_one(transport, report, "restore", deadline=cleanup_deadline, clock=clock)
    transport.restore_response_clean = True
    report["restore_response_protocol_verified"] = True


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + SERIAL_SECONDS
    report.update(
        status="not_started",
        operational_deadline_monotonic=deadline,
        accepted_tx_bytes=0,
        uncertain_tx_bytes=0,
        responses=[],
        baseline_getter_verified=False,
        test_setter_response_protocol_verified=False,
        test_vector_getter_verified=False,
        restore_attempted=False,
        restore_response_protocol_verified=False,
        baseline_restore_getter_verified=False,
        protocol_store_verification="not_established",
        operator_led_observation="not_recorded_by_software",
        physical_led_effect="not_established",
        physical_stop="not_established",
    )
    primary = None
    cleanup_errors = []
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        _submit(transport, report, "baseline", deadline=deadline)
        _observe_one(transport, report, "baseline", deadline=deadline,
                     expected_payload=BASELINE, clock=clock)
        report["baseline_getter_verified"] = True

        _submit(transport, report, "test", deadline=deadline)
        _observe_one(transport, report, "test", deadline=deadline, clock=clock)
        report["test_setter_response_protocol_verified"] = True

        _submit(transport, report, "verify", deadline=deadline)
        _observe_one(transport, report, "verify", deadline=deadline,
                     expected_payload=TEST_VECTOR, clock=clock)
        report["test_vector_getter_verified"] = True

        _attempt_restore(transport, report, clock=clock)

        _submit(transport, report, "reverify", deadline=deadline)
        _observe_one(transport, report, "reverify", deadline=deadline,
                     expected_payload=BASELINE, clock=clock)
        report.update(
            baseline_restore_getter_verified=True,
            protocol_store_verification="test_vector_and_exact_baseline_restore_verified",
            status=SUCCESS,
        )
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024],
                      required_operator_action="KEEP_MOTOR_POWER_PLUGS_DISCONNECTED")
        raise
    finally:
        if (transport.test_setter_may_have_been_submitted
                and not transport.restore_attempted):
            try:
                _attempt_restore(transport, report, clock=clock)
            except BaseException as error:
                cleanup_errors.append(error)
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            cleanup_errors.append(error)
        report.update(
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            restore_attempted=transport.restore_attempted,
            cleanup_errors=[str(error)[:1024] for error in cleanup_errors],
            application_acknowledgment="not_established",
            physical_stop="not_established",
        )
        if cleanup_errors:
            report["status"] = "failed"
            if primary is None:
                raise cleanup_errors[0]
            for error in cleanup_errors:
                primary.add_note(f"Additional restore/finalization error: {error}")


def _validate_capture(options):
    if options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation"):
        raise ValueError("Disconnected-load LED-state capture forbids other profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) != consent.DISCONNECTED_LED_STATE_SCOPE:
        raise ValueError("Disconnected-load LED-state capture requires its complete literal scope.")


def run_diagnostic(output, *, expected_physical_port, run=False,
                   actuators_isolated=False, **declarations):
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope != consent.DISCONNECTED_LED_STATE_SCOPE:
        raise ValueError("Literal --run and disconnected-load LED-state scope are required.")
    return zero._run_diagnostic(
        output, expected_physical_port=expected_physical_port, review=prepare(),
        transport_type=_LedStateTransport, observe=_observe,
        limits=zero._Limits(first_sequence=3089, max_requests=5, interval=0),
        session_options={"actuators_isolated": False, **declarations},
        declarations={**consent.powered_trial_history(declarations),
                      "run_id": str(uuid.uuid4())},
        expected_tx=sum(map(len, TRANSCRIPT)), success_status=SUCCESS,
        report_key="disconnected_load_led_state_round_trip",
        authorizations={"unvalidated_led_state_round_trip_authorized": True},
        capture_validator=_validate_capture,
        on_failure=consent.notify_powered_trial_fault,
        serial_seconds=SERIAL_SECONDS,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--actuators-isolated", action="store_true")
    consent.add_arguments(parser)
    consent.add_observation_arguments(parser)
    consent.add_powered_trial_arguments(parser)
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    args = consent.parse_observation_arguments(parser, argv)
    declarations = {**consent.arguments(args), **consent.observation_arguments(args),
                    **consent.powered_trial_arguments(args)}
    try:
        if args.actuators_isolated or any(declarations.values()):
            _validate_capture({"actuators_isolated": args.actuators_isolated, **declarations})
        if args.run and args.output is None:
            raise ValueError("--output NEWDIR is required.")
        result = prepare() if not args.run else run_diagnostic(
            args.output, expected_physical_port=args.expected_physical_port,
            run=args.run, actuators_isolated=args.actuators_isolated, **declarations)
    except (Exception, KeyboardInterrupt) as error:
        consent.notify_powered_trial_fault(error)
        print(json.dumps({"status": "failed", "error": str(error),
                          "physical_stop": "not_established"}), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
