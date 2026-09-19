"""Fixed experimental raw-PWM word-0 pilots with mandatory zero cleanup.

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
from tools.marvin_legacy_led_mapper import (
    _evidence_path, _frame, _load_state, _verify_manifest, _write_state)
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet
from tools.marvin_paths import new_output_path


ZERO_PWM = bytes(8)
WORD0_ONE = (1).to_bytes(2, "little") + bytes(6)
WORD0_1000 = (1000).to_bytes(2, "little") + bytes(6)
WORD0_2000 = (2000).to_bytes(2, "little") + bytes(6)


def _steps(first_sequence, payload):
    return {
        "baseline": _frame(first_sequence, 0x0A),
        "set": _frame(first_sequence + 1, 0x0B, payload),
        "cleanup": _frame(first_sequence + 2, 0x0B, ZERO_PWM),
        "verify": _frame(first_sequence + 3, 0x0A),
    }


STEPS = _steps(3329, WORD0_ONE)
TRANSCRIPT = tuple(STEPS.values())
STEPS_1000 = _steps(3333, WORD0_1000)
TRANSCRIPT_1000 = tuple(STEPS_1000.values())
STEPS_2000 = _steps(3337, WORD0_2000)
TRANSCRIPT_2000 = tuple(STEPS_2000.values())
STEPS_LEFT_CONNECTED = _steps(3341, WORD0_1000)
TRANSCRIPT_LEFT_CONNECTED = tuple(STEPS_LEFT_CONNECTED.values())
SERIAL_SECONDS = 10
CLEANUP_SECONDS = 5
RESPONSE_SECONDS = 0.500
OBSERVATION_SECONDS = 3
SUCCESS = "raw_pwm_word0_one_pilot_complete_unverified"
SUCCESS_1000 = "raw_pwm_word0_1000_pilot_complete_unverified"
SUCCESS_2000 = "raw_pwm_word0_2000_pilot_complete_unverified"
SUCCESS_LEFT_CONNECTED = "raw_pwm_left_motor_connected_proof_complete_unverified"
PROFILES = {
    consent.RAW_PWM_PILOT_SCOPE: {
        "steps": STEPS, "transcript": TRANSCRIPT, "first_sequence": 3329,
        "value": 1, "success": SUCCESS, "target": "raw-pwm-word0-one-pilot",
        "authorization": "unvalidated_raw_pwm_word0_one_pilot_authorized",
        "report_key": "raw_pwm_word0_one_pilot", "observation_seconds": 3,
    },
    consent.RAW_PWM_1000_PILOT_SCOPE: {
        "steps": STEPS_1000, "transcript": TRANSCRIPT_1000, "first_sequence": 3333,
        "value": 1000, "success": SUCCESS_1000, "target": "raw-pwm-word0-1000-pilot",
        "authorization": "unvalidated_raw_pwm_word0_1000_pilot_authorized",
        "report_key": "raw_pwm_word0_1000_pilot", "observation_seconds": 3,
    },
    consent.RAW_PWM_2000_PILOT_SCOPE: {
        "steps": STEPS_2000, "transcript": TRANSCRIPT_2000, "first_sequence": 3337,
        "value": 2000, "success": SUCCESS_2000, "target": "raw-pwm-word0-2000-pilot",
        "authorization": "unvalidated_raw_pwm_word0_2000_pilot_authorized",
        "report_key": "raw_pwm_word0_2000_pilot", "observation_seconds": 3,
    },
    consent.RAW_PWM_LEFT_CONNECTED_SCOPE: {
        "steps": STEPS_LEFT_CONNECTED, "transcript": TRANSCRIPT_LEFT_CONNECTED,
        "first_sequence": 3341, "value": 1000,
        "success": SUCCESS_LEFT_CONNECTED,
        "target": "raw-pwm-word0-1000-left-motor-connected-proof",
        "authorization": "unvalidated_raw_pwm_left_motor_connected_proof_authorized",
        "report_key": "raw_pwm_left_motor_connected_proof",
        "observation_seconds": 0.250,
    },
}


def transcript_for_scope(scope):
    try:
        return PROFILES[scope]["transcript"]
    except KeyError:
        raise ValueError("Unknown fixed raw-PWM pilot scope.") from None


def prepare(scope=consent.RAW_PWM_PILOT_SCOPE):
    profile = PROFILES[scope]
    transcript = profile["transcript"]
    first_sequence = profile["first_sequence"]
    value = profile["value"]
    expected = (
        (first_sequence, 0x0A, b""),
        (first_sequence + 1, 0x0B, value.to_bytes(2, "little") + bytes(6)),
        (first_sequence + 2, 0x0B, ZERO_PWM),
        (first_sequence + 3, 0x0A, b""),
    )
    for raw, fields in zip(transcript, expected):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                fields[0], fields[1], 0, fields[2]):
            raise ValueError("Fixed raw-PWM transcript disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "name": scope,
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in transcript],
        "transcript_sha256": hashlib.sha256(b"".join(transcript)).hexdigest(),
        "required_baseline_payload_hex": ZERO_PWM.hex(),
        "fixed_setter_words_uint16": [value, 0, 0, 0],
        "fixed_cleanup_words_uint16": [0, 0, 0, 0],
        "observation_seconds": profile["observation_seconds"],
        "operator_selected_physical_plug_label": "Motor L",
        "word_to_physical_plug_mapping": "not_established",
        "physical_load": (
            "left_motor_connected_to_robot_right_side_connector_printed_Motor_L"
            if scope == consent.RAW_PWM_LEFT_CONNECTED_SCOPE
            else "both_motor_power_plugs_disconnected"),
        "raw_value_units_or_effective_minimum": "not_established",
        "null_scope_result": "inconclusive_effective_pwm_minimum_and_channel_mapping_unknown",
        "waveform_result": "evidence_of_lower_level_path_not_channel_topology",
        "maximum_application_bytes": sum(map(len, transcript)),
        "minimum_application_bytes_after_setter": sum(map(len, transcript[:3])),
        "maximum_writes": len(transcript),
        "maximum_serial_rx_bytes": 8192,
        "maximum_expected_response_bytes": 56,
        "response_policy": (
            "getter_requires_unique_crc_valid_correlated_raw80_exact_eight_zero_bytes; "
            "setter_and_cleanup_accept_unique_crc_valid_correlated_empty_raw80_or_raw82_opaquely"),
        "response_time_origin": "matching_immediate_pre_os_write_monotonic_timestamp",
        "cleanup_policy": (
            "exactly_one_fixed_all_zero_attempt after any nonzero may reach syscall"),
        "conditional_verification": "cmd0a only after clean empty raw80 cleanup response",
        "unverified_restoration_policy": (
            "physical_output_baseline_confirmation_and_power_cycle_acknowledgment_required"),
        "automatic_retries": False,
        "automatic_reconnect": False,
        "fixed_cadence": None,
        "maximum_setter_to_cleanup_start_seconds": (
            RESPONSE_SECONDS + profile["observation_seconds"]),
        "source_limitations": (
            "PCTestApp admits four UInt16 words but supplies no nonzero example, "
            "units, effective minimum, channel binding, or explicit repeat interval"),
        "physical_stop": "not_established",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in consent.POWERED_TRIAL_SCOPES[scope])],
    }


class _RawPwmTransport(LiveTransport):
    steps = STEPS
    success = SUCCESS
    observation_seconds = OBSERVATION_SECONDS
    motor_connected = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed = []
        self.may_have_applied = False
        self.cleanup_attempted = False
        self.cleanup_raw80 = False

    def submit(self, step, *, deadline):
        if step == "cleanup":
            return self._cleanup_once(deadline=deadline)
        if step == "baseline":
            if self.completed:
                raise OSError("Raw-PWM baseline must be first.")
        elif step == "set":
            if self.completed != ["baseline"]:
                raise OSError("Raw-PWM setter requires the exact zero baseline.")
            self.may_have_applied = True
        elif step == "verify":
            if not self.cleanup_raw80:
                raise OSError("Raw-PWM getter verification requires a clean raw-80 cleanup.")
        else:
            raise OSError("Only the fixed raw-PWM pilot transcript is permitted.")
        count = self._submit_once(self.steps[step], deadline=deadline)
        if count == len(self.steps[step]):
            self.completed.append(step)
        return count

    def _cleanup_once(self, *, deadline):
        if self.cleanup_attempted:
            raise OSError("Raw-PWM cleanup has already been attempted; no retry.")
        if not self.may_have_applied:
            raise OSError("Raw-PWM cleanup is not required before the setter.")
        self.cleanup_attempted = True
        raw = self.steps["cleanup"]
        self._check(deadline)
        self.ingress.expected_tx.append(raw)
        self.writes += 1
        self.last_write_sequence = decode_packet(raw).sequence
        self.last_write_started = time.monotonic()
        count = os.write(self.fd, raw)
        self.last_write = time.monotonic()
        self.event("raw_pwm_cleanup_returned", raw_hex=raw.hex(), accepted_bytes=count)
        if count == len(raw):
            self.completed.append("cleanup")
        return count


class _RawPwm1000Transport(_RawPwmTransport):
    steps = STEPS_1000
    success = SUCCESS_1000


class _RawPwm2000Transport(_RawPwmTransport):
    steps = STEPS_2000
    success = SUCCESS_2000


class _RawPwmLeftConnectedTransport(_RawPwmTransport):
    steps = STEPS_LEFT_CONNECTED
    success = SUCCESS_LEFT_CONNECTED
    observation_seconds = 0.250
    motor_connected = True


def _submit(transport, report, step, *, deadline):
    raw = transport.steps[step]
    report["uncertain_tx_bytes"] += len(raw)
    count = transport.submit(step, deadline=deadline)
    if type(count) is not int or not 0 <= count <= len(raw):
        raise OSError(f"Unknown raw-PWM {step} write result; no retry.")
    report["accepted_tx_bytes"] += count
    report["uncertain_tx_bytes"] -= count
    if count != len(raw):
        raise OSError(f"Partial raw-PWM {step} write; no retry.")


def _response(transport, report, step, *, deadline, clock=time.monotonic):
    request = decode_packet(transport.steps[step])
    if (transport.last_write_sequence != request.sequence
            or type(transport.last_write_started) not in (int, float)
            or transport.last_write_started > clock()):
        raise OSError("Response evidence lacks the matching immediate pre-syscall boundary.")
    expected_payload = ZERO_PWM if step in ("baseline", "verify") else b""
    evidence = zero._ResponseEvidence(
        transport.event, sequence=request.sequence, command=request.command,
        accepted_response_fields=((0x80,) if step in ("baseline", "verify")
                                  else (0x80, 0x82)),
        validate_packet=lambda packet: (
            [] if packet.payload == expected_payload else ["unexpected_raw_pwm_payload"]))
    evidence.submitted_at = transport.last_write_started
    evidence.deadline = min(deadline, evidence.submitted_at + RESPONSE_SECONDS)
    if evidence.deadline != evidence.submitted_at + RESPONSE_SECONDS:
        raise OSError(f"Insufficient remaining budget for raw-PWM {step} response.")
    try:
        zero._observe_response(transport, evidence, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(evidence.events[0]["stream"]["raw_hex"]))
        report["responses"].append({
            "step": step,
            "sequence": packet.sequence,
            "command": packet.command,
            "raw_response_field": packet.response_field,
            "payload_bytes": len(packet.payload),
            "raw_payload_hex": packet.payload.hex(),
            "application_acknowledgment": "not_established",
            "events": evidence.events,
        })
        return packet
    finally:
        evidence.finish(clock())


def _attempt_cleanup(transport, report, *, clock=time.monotonic):
    deadline = clock() + CLEANUP_SECONDS
    _submit(transport, report, "cleanup", deadline=deadline)
    packet = _response(transport, report, "cleanup", deadline=deadline, clock=clock)
    report["cleanup_raw_response_field"] = packet.response_field
    transport.cleanup_raw80 = packet.response_field == 0x80


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + SERIAL_SECONDS
    report.update(
        status="not_started", accepted_tx_bytes=0, uncertain_tx_bytes=0,
        responses=[], nonzero_may_have_applied=False, cleanup_attempted=False,
        cleanup_raw_response_field=None, getter_reverified=False,
        operator_scope_observation="pending_external_operator_report",
        application_acknowledgment="not_established", physical_stop="not_established",
    )
    primary = None
    cleanup_errors = []
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        _submit(transport, report, "baseline", deadline=deadline)
        _response(transport, report, "baseline", deadline=deadline, clock=clock)
        report["nonzero_may_have_applied"] = True
        _submit(transport, report, "set", deadline=deadline)
        packet = _response(transport, report, "set", deadline=deadline, clock=clock)
        report["setter_raw_response_field"] = packet.response_field
        print(
            ("OBSERVE_LEFT_MOTOR_NOW: report no_motion/motion_direction_uncertain/"
             "motion_direction_observed; external cutoff is primary"
             if transport.motor_connected else
             "OBSERVE_MOTOR_L_SCOPE_NOW: word-to-plug mapping is unproved")
            + f"; fixed {transport.observation_seconds}-second window; "
            "mandatory zero cleanup follows.",
            file=sys.stderr, flush=True)
        transport.wait(transport.observation_seconds)
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        if transport.may_have_applied and not transport.cleanup_attempted:
            try:
                _attempt_cleanup(transport, report, clock=clock)
            except BaseException as error:
                cleanup_errors.append(("cleanup", error))
        if transport.cleanup_raw80:
            try:
                verify_deadline = clock() + CLEANUP_SECONDS
                _submit(transport, report, "verify", deadline=verify_deadline)
                _response(transport, report, "verify",
                          deadline=verify_deadline, clock=clock)
                report["getter_reverified"] = True
            except BaseException as error:
                cleanup_errors.append(("verify", error))
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            cleanup_errors.append(("close", error))
        report.update(
            nonzero_may_have_applied=transport.may_have_applied,
            cleanup_attempted=transport.cleanup_attempted,
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            cleanup_errors=[
                {"step": step, "error": str(error)[:1024]}
                for step, error in cleanup_errors
            ],
            restoration=(
                "getter_zero_baseline_reverified"
                if report["getter_reverified"]
                else "zero_cleanup_attempted_but_application_unverified"),
            subsequent_live_phase_gate=(
                "none"
                if report["getter_reverified"]
                else "physical_output_baseline_confirmation_and_power_cycle_acknowledgment_required"),
            physical_stop="not_established",
        )
        if primary is None and not cleanup_errors:
            report["status"] = transport.success
        elif cleanup_errors:
            report["status"] = "failed"
            if primary is None:
                raise cleanup_errors[0][1]
            for step, error in cleanup_errors:
                primary.add_note(f"Additional raw-PWM {step} error: {error}")


def _validate_capture(options):
    if options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation"):
        raise ValueError("Raw-PWM pilot forbids other diagnostic profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) not in PROFILES:
        raise ValueError("Raw-PWM pilot requires its complete literal scope.")


def run_diagnostic(output, *, expected_physical_port, run=False,
                   actuators_isolated=False, **declarations):
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope not in PROFILES:
        raise ValueError("Literal --run and one fixed raw-PWM word-0 scope are required.")
    profile = PROFILES[scope]
    transport_type = {
        consent.RAW_PWM_PILOT_SCOPE: _RawPwmTransport,
        consent.RAW_PWM_1000_PILOT_SCOPE: _RawPwm1000Transport,
        consent.RAW_PWM_2000_PILOT_SCOPE: _RawPwm2000Transport,
        consent.RAW_PWM_LEFT_CONNECTED_SCOPE: _RawPwmLeftConnectedTransport,
    }[scope]
    output = new_output_path(output)
    root = output.parent
    previous = _load_state(root)
    if previous and previous["status"] not in (
            "restored", "power_cycle_reset_confirmed", "set_aborted_before_baseline"):
        raise ValueError("RESTORATION_ACK_REQUIRED before another live diagnostic.")
    state = {
        "status": "raw_pwm_started",
        "target": profile["target"],
        "set_evidence": str(output),
        "created_at": time.time(),
    }
    _write_state(root, state)
    try:
        result = zero._run_diagnostic(
            output, expected_physical_port=expected_physical_port, review=prepare(scope),
            transport_type=transport_type, observe=_observe,
            limits=zero._Limits(
                first_sequence=profile["first_sequence"], max_requests=4, interval=0),
            session_options={"actuators_isolated": False, **declarations},
            declarations={**consent.powered_trial_history(declarations),
                          "run_id": str(uuid.uuid4())},
            expected_tx=lambda report: report["accepted_tx_bytes"],
            success_status=profile["success"], report_key=profile["report_key"],
            authorizations={profile["authorization"]: True},
            capture_validator=_validate_capture,
            on_failure=consent.notify_powered_trial_fault,
            serial_seconds=SERIAL_SECONDS,
        )
    except BaseException:
        state["status"] = "raw_pwm_restoration_unverified"
        if output.is_dir() and (output / "SHA256SUMS").is_file():
            state["set_manifest_sha256"] = _verify_manifest(output)
        _write_state(root, state)
        raise
    observation = result["observation"]
    state.update(
        status=(
            "restored"
            if observation["restoration"] == "getter_zero_baseline_reverified"
            else "raw_pwm_restoration_unverified"),
        restoration=observation["restoration"],
        set_manifest_sha256=_verify_manifest(output),
    )
    _write_state(root, state)
    return result


def acknowledge_restoration(evidence, *, physical_output_baseline_confirmed=False,
                            power_cycle_confirmed=False):
    if physical_output_baseline_confirmed is not True or power_cycle_confirmed is not True:
        raise ValueError("Literal physical-output-baseline and power-cycle confirmations are required.")
    evidence = _evidence_path(evidence)
    root = evidence.parent
    state = _load_state(root)
    targets = {profile["target"] for profile in PROFILES.values()}
    if (not state or state.get("target") not in targets
            or state.get("set_evidence") != str(evidence)
            or state.get("status") != "raw_pwm_restoration_unverified"):
        raise ValueError("No matching unverified raw-PWM pilot awaits acknowledgment.")
    digest = _verify_manifest(evidence)
    if state.get("set_manifest_sha256") != digest:
        raise ValueError("Raw-PWM evidence manifest differs from the active state lock.")
    state.update(
        status="power_cycle_reset_confirmed",
        confirmed_at=time.time(),
        physical_output_baseline_confirmed=True,
        power_cycle_confirmed=True,
        restoration="operator_scope_baseline_plus_power_cycle_not_software_verification",
    )
    _write_state(root, state)
    return {
        "status": "power_cycle_reset_confirmed",
        "target": state["target"],
        "hardware_access": False,
        "restoration": "operator_scope_baseline_plus_power_cycle_not_software_verification",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--acknowledge-restoration", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--physical-output-baseline-confirmed", action="store_true")
    parser.add_argument("--power-cycle-confirmed", action="store_true")
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
        if args.acknowledge_restoration:
            if (args.run or args.output or args.expected_physical_port
                    or args.actuators_isolated or any(declarations.values())):
                raise ValueError("Restoration acknowledgment is offline bookkeeping only.")
            result = acknowledge_restoration(
                args.evidence,
                physical_output_baseline_confirmed=args.physical_output_baseline_confirmed,
                power_cycle_confirmed=args.power_cycle_confirmed)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if (args.evidence or args.physical_output_baseline_confirmed
                or args.power_cycle_confirmed):
            raise ValueError("Acknowledgment arguments require --acknowledge-restoration.")
        scope = consent.RAW_PWM_PILOT_SCOPE
        if args.actuators_isolated or any(declarations.values()):
            _validate_capture({"actuators_isolated": args.actuators_isolated, **declarations})
            scope = consent.classify(
                actuators_isolated=args.actuators_isolated, **declarations)
        if args.run and args.output is None:
            raise ValueError("--output NEWDIR is required.")
        result = prepare(scope) if not args.run else run_diagnostic(
            args.output, expected_physical_port=args.expected_physical_port,
            run=True, actuators_isolated=args.actuators_isolated, **declarations)
    except (Exception, KeyboardInterrupt) as error:
        if not args.acknowledge_restoration:
            consent.notify_powered_trial_fault(error)
        print(json.dumps({"status": "failed", "error": str(error),
                          "physical_stop": "not_established"}), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
