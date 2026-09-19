"""Fixed disconnected-load left +1000 velocity train for oscilloscope observation.

Offline by default. This is software readiness, not live authorization or a
physical-stop proof. Both motor POWER plugs must remain disconnected.
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
from tools.marvin_legacy_led_mapper import _frame
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet


FIRST_SEQUENCE = 3285
TRAIN_COUNT = 20
CADENCE_SECONDS = 0.050
LEFT_PLUS_1000 = (1000).to_bytes(2, "little", signed=True) + bytes(2)
ALL_ZERO = bytes(4)
INITIAL_ZERO = _frame(FIRST_SEQUENCE, 0x11, ALL_ZERO)
TRAIN = tuple(
    _frame(FIRST_SEQUENCE + 1 + index, 0x11, LEFT_PLUS_1000)
    for index in range(TRAIN_COUNT)
)
CLEANUP_ZERO = _frame(FIRST_SEQUENCE + 1 + TRAIN_COUNT, 0x11, ALL_ZERO)
TRANSCRIPT = (INITIAL_ZERO, *TRAIN, CLEANUP_ZERO)
SERIAL_SECONDS = 10
CLEANUP_SECONDS = 5
RESPONSE_SECONDS = 0.040
SUCCESS = "disconnected_load_left_plus_1000_velocity_train_complete_unverified"


def prepare():
    for index, raw in enumerate(TRANSCRIPT):
        packet = decode_packet(raw)
        expected_payload = ALL_ZERO if index in (0, len(TRANSCRIPT) - 1) else LEFT_PLUS_1000
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                FIRST_SEQUENCE + index, 0x11, 0, expected_payload):
            raise ValueError("Fixed velocity-train transcript disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "name": consent.DISCONNECTED_VELOCITY_TRAIN_SCOPE,
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "transcript_sha256": hashlib.sha256(b"".join(TRANSCRIPT)).hexdigest(),
        "initial_zero_sequence": FIRST_SEQUENCE,
        "train_sequences": [FIRST_SEQUENCE + 1, FIRST_SEQUENCE + TRAIN_COUNT],
        "cleanup_zero_sequence": FIRST_SEQUENCE + TRAIN_COUNT + 1,
        "fixed_left_raw_value": 1000,
        "fixed_right_raw_value": 0,
        "fixed_train_count": TRAIN_COUNT,
        "fixed_cadence_seconds": CADENCE_SECONDS,
        "nominal_train_span_seconds": (TRAIN_COUNT - 1) * CADENCE_SECONDS,
        "maximum_application_bytes": sum(map(len, TRANSCRIPT)),
        "maximum_writes": len(TRANSCRIPT),
        "maximum_serial_rx_bytes": 8192,
        "maximum_expected_response_bytes": len(TRANSCRIPT) * 10,
        "response_policy": (
            "initial_zero_requires_crc_valid_correlated_empty_raw80; "
            "fixed_nonzero_train_accepts_raw80_or_raw82_opaquely"),
        "response_time_origin": "matching_immediate_pre_os_write_monotonic_timestamp",
        "cleanup_policy": (
            "exactly_one_fixed_all_zero_attempt_after_any_nonzero_may_reach_syscall"),
        "automatic_retries": False,
        "automatic_reconnect": False,
        "scope_observation": (
            "trigger_on_left_output_and_compare_absence_or_presence_of_pwm_or_enable_pulses"),
        "software_outcome_is_physical_scope_result": False,
        "source_basis": (
            "PCTestApp timer2_Tick sends command11 repeatedly; Designer fixes timer2 to 50ms. "
            "The fixed timer path also repeats command11. +1000 was previously bounded."),
        "physical_stop": "not_established",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in consent.DISCONNECTED_VELOCITY_TRAIN_FLAGS)],
    }


class _VelocityTrainTransport(LiveTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.may_have_applied = False
        self.cleanup_attempted = False

    def submit(self, index, *, deadline):
        if type(index) is not int or index != self.writes or not 0 <= index < len(TRANSCRIPT) - 1:
            raise OSError("Only the next fixed velocity-train write is permitted.")
        if index:
            self.may_have_applied = True
        return self._submit_once(TRANSCRIPT[index], deadline=deadline)

    def cleanup_once(self, *, deadline):
        if self.cleanup_attempted:
            raise OSError("Cleanup zero has already been attempted; no retry.")
        if not self.may_have_applied:
            raise OSError("Cleanup zero is not required before a nonzero syscall.")
        self.cleanup_attempted = True
        raw = CLEANUP_ZERO
        self._check(deadline)
        self.ingress.expected_tx.append(raw)
        self.writes += 1
        self.last_write_sequence = decode_packet(raw).sequence
        self.last_write_started = time.monotonic()
        count = os.write(self.fd, raw)
        self.last_write = time.monotonic()
        self.event("velocity_train_cleanup_returned", raw_hex=raw.hex(), accepted_bytes=count)
        return count


def _submit(transport, report, index, *, deadline, cleanup=False):
    raw = TRANSCRIPT[index]
    report["uncertain_tx_bytes"] += len(raw)
    count = (transport.cleanup_once(deadline=deadline) if cleanup
             else transport.submit(index, deadline=deadline))
    if type(count) is not int or not 0 <= count <= len(raw):
        raise OSError("Unknown fixed velocity write result; no retry.")
    report["accepted_tx_bytes"] += count
    report["uncertain_tx_bytes"] -= count
    if count != len(raw):
        raise OSError("Partial fixed velocity write; no retry.")
    return count


def _response(transport, report, index, *, deadline, clock=time.monotonic):
    request = decode_packet(TRANSCRIPT[index])
    if (transport.last_write_sequence != request.sequence
            or type(transport.last_write_started) not in (int, float)
            or transport.last_write_started > clock()):
        raise OSError("Response evidence lacks the matching immediate pre-syscall boundary.")
    evidence = zero._ResponseEvidence(
        transport.event, sequence=request.sequence, command=0x11,
        accepted_response_fields=((0x80,) if index == 0 else (0x80, 0x82)),
        validate_packet=lambda packet: [] if not packet.payload else ["unexpected_setter_payload"])
    evidence.submitted_at = transport.last_write_started
    evidence.deadline = min(deadline, evidence.submitted_at + RESPONSE_SECONDS)
    if evidence.deadline != evidence.submitted_at + RESPONSE_SECONDS:
        raise OSError("Insufficient fixed cadence budget for correlated setter response.")
    try:
        zero._observe_response(transport, evidence, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(evidence.events[0]["stream"]["raw_hex"]))
        report["responses"].append({
            "sequence": packet.sequence,
            "raw_response_field": packet.response_field,
            "payload_bytes": len(packet.payload),
            "application_acknowledgment": "not_established",
            "events": evidence.events,
        })
        return packet
    finally:
        evidence.finish(clock())


def _wait_until(transport, target, deadline, *, clock=time.monotonic, sleeper=time.sleep):
    while clock() < target:
        transport.identity(deadline=deadline)
        transport.ingress.pump()
        if transport.ingress.rx:
            raise OSError("Extra USB input between fixed velocity writes; cleanup required.")
        sleeper(min(0.005, max(0, target - clock())))
    lateness = clock() - target
    if lateness > transport.plan.max_lateness:
        raise OSError("Fixed 50 ms velocity cadence lateness exceeded.")
    return lateness


def _attempt_cleanup(transport, report, *, clock=time.monotonic):
    index = len(TRANSCRIPT) - 1
    deadline = clock() + CLEANUP_SECONDS
    _submit(transport, report, index, deadline=deadline, cleanup=True)
    report["cleanup_zero_fully_accepted"] = True
    packet = _response(transport, report, index, deadline=deadline, clock=clock)
    report["cleanup_raw_response_field"] = packet.response_field


def _observe(transport, report, *, clock=time.monotonic, sleeper=time.sleep):
    deadline = clock() + SERIAL_SECONDS
    report.update(
        status="not_started", accepted_tx_bytes=0, uncertain_tx_bytes=0,
        responses=[], cadence_write_lateness_seconds=[],
        nonzero_may_have_applied=False, cleanup_zero_attempted=False,
        cleanup_zero_fully_accepted=False, cleanup_raw_response_field=None,
        operator_scope_observation="pending_external_operator_report",
        application_acknowledgment="not_established", physical_stop="not_established",
    )
    primary = None
    cleanup_error = None
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        _submit(transport, report, 0, deadline=deadline)
        _response(transport, report, 0, deadline=deadline, clock=clock)
        train_start = clock()
        report["train_start_monotonic"] = train_start
        for train_index in range(TRAIN_COUNT):
            index = train_index + 1
            target = train_start + train_index * CADENCE_SECONDS
            lateness = _wait_until(
                transport, target, deadline, clock=clock, sleeper=sleeper)
            report["cadence_write_lateness_seconds"].append(lateness)
            report["nonzero_may_have_applied"] = True
            _submit(transport, report, index, deadline=deadline)
            _response(
                transport, report, index,
                deadline=min(deadline, target + CADENCE_SECONDS),
                clock=clock)
        _wait_until(
            transport, train_start + TRAIN_COUNT * CADENCE_SECONDS,
            deadline, clock=clock, sleeper=sleeper)
        _attempt_cleanup(transport, report, clock=clock)
        report["status"] = SUCCESS
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024],
                      required_operator_action="CUT_HY1803D_POWER_AND_KEEP_MOTOR_PLUGS_DISCONNECTED")
        raise
    finally:
        if transport.may_have_applied and not transport.cleanup_attempted:
            try:
                _attempt_cleanup(transport, report, clock=clock)
            except BaseException as error:
                cleanup_error = error
        report["nonzero_may_have_applied"] = transport.may_have_applied
        report["cleanup_zero_attempted"] = transport.cleanup_attempted
        errors = []
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            errors.append(error)
        report.update(
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            cleanup_error=None if cleanup_error is None else str(cleanup_error)[:1024],
        )
        if cleanup_error is not None:
            errors.insert(0, cleanup_error)
        if errors:
            report["status"] = "failed"
            if primary is None:
                raise errors[0]
            for error in errors:
                primary.add_note(f"Additional cleanup/finalization error: {error}")


def _validate_capture(options):
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if (options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation")
            or consent.classify(
                actuators_isolated=options.get("actuators_isolated", False),
                **declarations) != consent.DISCONNECTED_VELOCITY_TRAIN_SCOPE):
        raise ValueError("Velocity train capture requires its complete literal scope.")


def run_diagnostic(output, *, expected_physical_port, run=False,
                   actuators_isolated=False, **declarations):
    try:
        scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
        if run is not True or scope != consent.DISCONNECTED_VELOCITY_TRAIN_SCOPE:
            raise ValueError("Literal --run and fixed velocity-train scope are required.")
        return zero._run_diagnostic(
            output, expected_physical_port=expected_physical_port, review=prepare(),
            transport_type=_VelocityTrainTransport, observe=_observe,
            limits=zero._Limits(
                first_sequence=FIRST_SEQUENCE, max_requests=len(TRANSCRIPT),
                interval=0, max_lateness=0.020),
            session_options={"actuators_isolated": False, **declarations},
            declarations={**consent.powered_trial_history(declarations),
                          "run_id": str(uuid.uuid4())},
            expected_tx=sum(map(len, TRANSCRIPT)), success_status=SUCCESS,
            report_key="disconnected_load_left_plus_1000_velocity_train_observation",
            authorizations={"unvalidated_left_plus_1000_velocity_train_authorized": True},
            capture_validator=_validate_capture,
            on_failure=consent.notify_powered_trial_fault,
            serial_seconds=SERIAL_SECONDS,
        )
    except BaseException as error:
        consent.notify_powered_trial_fault(error)
        raise


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
        if args.run and args.output is None:
            raise ValueError("--output NEWDIR is required.")
        if args.actuators_isolated or any(declarations.values()):
            _validate_capture({"actuators_isolated": args.actuators_isolated, **declarations})
        result = prepare() if not args.run else run_diagnostic(
            args.output, expected_physical_port=args.expected_physical_port,
            run=True, actuators_isolated=args.actuators_isolated, **declarations)
    except (Exception, KeyboardInterrupt) as error:
        consent.notify_powered_trial_fault(error)
        print(json.dumps({"status": "failed", "error": str(error),
                          "physical_stop": "not_established"}), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
