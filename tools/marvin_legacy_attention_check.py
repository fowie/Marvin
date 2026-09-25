"""Fixed legacy attention-LED acceptance sweep with exact baseline cleanup."""

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


BASELINE = bytes.fromhex("000000000000000000000000000000ff0000")
TARGETS = (
    ("left-position-0-red", 0),
    ("left-position-0-blue", 1),
    ("left-position-1-red", 2),
    ("left-position-1-blue", 3),
    ("left-position-2-red", 4),
    ("left-position-2-blue", 5),
    ("right-position-0-red", 6),
    ("right-position-0-blue", 7),
    ("right-position-1-red", 8),
    ("right-position-1-blue", 9),
    ("right-position-2-red", 10),
    ("right-position-2-blue", 11),
    ("front-left-blue", 13),
    ("front-right-red", 14),
)
OBSERVATION_SECONDS = 0.25
SERIAL_SECONDS = 25
CLEANUP_SECONDS = 5
RESPONSE_SECONDS = 0.5
SUCCESS = "attention_led_check_complete_unverified"
TARGET = "attention-led-check"


def _exclusive(index):
    payload = bytearray(18)
    payload[index] = 255
    return bytes(payload)


STEPS = {"baseline": _frame(3700, 0x17)}
for offset, (name, index) in enumerate(TARGETS):
    STEPS[name + "_set"] = _frame(3701 + offset * 2, 0x18, _exclusive(index))
    STEPS[name + "_restore"] = _frame(3702 + offset * 2, 0x18, BASELINE)
STEPS["final_verify"] = _frame(3729, 0x17)
TRANSCRIPT = tuple(STEPS.values())


def prepare():
    return {
        "status": "dry_run",
        "name": consent.ATTENTION_LED_CHECK_SCOPE,
        "profile": "marvin-legacy-se",
        "targets": [
            {"name": name, "index": index, "value": 255}
            for name, index in TARGETS
        ],
        "fixed_observation_seconds_per_target": OBSERVATION_SECONDS,
        "required_baseline_hex": BASELINE.hex(),
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "transcript_sha256": hashlib.sha256(b"".join(TRANSCRIPT)).hexdigest(),
        "maximum_writes": len(TRANSCRIPT),
        "maximum_application_bytes": sum(map(len, TRANSCRIPT)),
        "automatic_retries": False,
        "automatic_reconnect": False,
        "cleanup_policy": (
            "one exact captured-baseline restore attempt after each possible setter "
            "submission; no restore retry"),
        "final_verification": "command-0x17 payload must exactly equal the baseline",
        "raw_response_fields": "0x80 and 0x82 are recorded but remain opaque",
        "physical_effect": "operator observation required; not inferred from protocol",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in consent.ATTENTION_LED_CHECK_FLAGS)],
    }


class _AttentionTransport(LiveTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.next_step = 0
        self.outstanding = None
        self.restore_attempted = set()

    def submit(self, step, *, deadline):
        names = tuple(STEPS)
        if step not in STEPS or step != names[self.next_step]:
            raise OSError("Fixed attention-LED transcript order violated; no retry.")
        if step.endswith("_set"):
            if self.outstanding is not None:
                raise OSError("The prior attention LED has not been restored.")
            self.outstanding = step.removesuffix("_set")
            self.next_step += 1
        elif step.endswith("_restore"):
            target = step.removesuffix("_restore")
            if self.outstanding != target or target in self.restore_attempted:
                raise OSError("Attention-LED restore is not admitted or was already attempted.")
            self.restore_attempted.add(target)
            self.next_step += 1
        count = self._submit_once(STEPS[step], deadline=deadline)
        if count == len(STEPS[step]) and not (
                step.endswith("_set") or step.endswith("_restore")):
            self.next_step += 1
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


def _response(transport, report, step, *, deadline, expected_payload=None,
              accepted=(0x80,), clock=time.monotonic):
    request = decode_packet(STEPS[step])
    evidence = zero._ResponseEvidence(
        transport.event, sequence=request.sequence, command=request.command,
        accepted_response_fields=accepted,
        validate_packet=(
            None if expected_payload is None else
            lambda packet: ([] if packet.payload == expected_payload
                            else ["unexpected_attention_led_payload"])))
    evidence.submitted_at = clock()
    evidence.deadline = min(deadline, evidence.submitted_at + RESPONSE_SECONDS)
    if evidence.deadline != evidence.submitted_at + RESPONSE_SECONDS:
        raise OSError(f"Insufficient remaining budget for {step} response.")
    try:
        zero._observe_response(transport, evidence, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(evidence.events[0]["stream"]["raw_hex"]))
        report["responses"].append({
            "step": step,
            "sequence": packet.sequence,
            "command": packet.command,
            "raw_response_field": packet.response_field,
            "raw_payload_hex": packet.payload.hex(),
            "events": evidence.events,
        })
        return packet
    finally:
        evidence.finish(clock())


def _restore(transport, report, target, *, clock):
    step = target + "_restore"
    deadline = clock() + CLEANUP_SECONDS
    _submit(transport, report, step, deadline=deadline)
    packet = _response(
        transport, report, step, deadline=deadline,
        accepted=(0x80, 0x82), clock=clock)
    report["restore_raw_response_fields"][target] = packet.response_field
    transport.outstanding = None


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + SERIAL_SECONDS
    report.update(
        status="not_started", accepted_tx_bytes=0, uncertain_tx_bytes=0,
        responses=[], completed_targets=[], restore_raw_response_fields={},
        baseline_verified=False, final_baseline_verified=False,
        application_acknowledgment="not_established",
        physical_observation="required_from_supervising_operator",
    )
    primary = None
    cleanup_errors = []
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        _submit(transport, report, "baseline", deadline=deadline)
        _response(
            transport, report, "baseline", deadline=deadline,
            expected_payload=BASELINE, clock=clock)
        report["baseline_verified"] = True
        for name, _ in TARGETS:
            step = name + "_set"
            _submit(transport, report, step, deadline=deadline)
            packet = _response(
                transport, report, step, deadline=deadline,
                accepted=(0x80, 0x82), clock=clock)
            report.setdefault("setter_raw_response_fields", {})[name] = (
                packet.response_field)
            print(
                f"OBSERVE_ATTENTION_LED_NOW: {name}; fixed "
                f"{OBSERVATION_SECONDS:.2f}-second window; restore follows.",
                file=sys.stderr, flush=True)
            transport.wait(OBSERVATION_SECONDS)
            _restore(transport, report, name, clock=clock)
            report["completed_targets"].append(name)
        _submit(transport, report, "final_verify", deadline=deadline)
        _response(
            transport, report, "final_verify", deadline=deadline,
            expected_payload=BASELINE, clock=clock)
        report["final_baseline_verified"] = True
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        if (transport.outstanding is not None
                and transport.outstanding not in transport.restore_attempted):
            try:
                _restore(transport, report, transport.outstanding, clock=clock)
            except BaseException as error:
                cleanup_errors.append(error)
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            cleanup_errors.append(error)
        report.update(
            restore_attempted=sorted(transport.restore_attempted),
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            cleanup_errors=[str(error)[:1024] for error in cleanup_errors],
            restoration=(
                "final_getter_exact_baseline_verified"
                if report["final_baseline_verified"]
                else "not_established"),
            physical_stop="not_established",
        )
        if primary is None and not cleanup_errors:
            report["status"] = SUCCESS
        elif cleanup_errors:
            report["status"] = "failed"
            if primary is None:
                raise cleanup_errors[0]
            for error in cleanup_errors:
                primary.add_note(f"Additional attention-LED cleanup error: {error}")


def _validate_capture(options):
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) != consent.ATTENTION_LED_CHECK_SCOPE:
        raise ValueError("Attention LED check requires its complete literal scope.")


def run_diagnostic(output, *, expected_physical_port, run=False,
                   actuators_isolated=False, **declarations):
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope != consent.ATTENTION_LED_CHECK_SCOPE:
        raise ValueError("Literal --run and attention LED check scope are required.")
    output = new_output_path(output)
    root = output.parent
    previous = _load_state(root)
    if previous and previous["status"] not in (
            "restored", "power_cycle_reset_confirmed", "set_aborted_before_baseline"):
        raise ValueError("RESTORE_REQUIRED: resolve the prior LED operation first.")
    _write_state(root, {
        "status": "attention_check_started", "target": TARGET,
        "set_evidence": str(output), "created_at": time.time(),
    })
    try:
        result = zero._run_diagnostic(
            output, expected_physical_port=expected_physical_port, review=prepare(),
            transport_type=_AttentionTransport, observe=_observe,
            limits=zero._Limits(first_sequence=3700, max_requests=len(TRANSCRIPT),
                                interval=0),
            session_options={"actuators_isolated": False, **declarations},
            declarations={**consent.powered_trial_history(declarations),
                          "run_id": str(uuid.uuid4())},
            expected_tx=sum(map(len, TRANSCRIPT)),
            success_status=SUCCESS, report_key="attention_led_check",
            authorizations={"unvalidated_attention_led_check_authorized": True},
            capture_validator=_validate_capture,
            on_failure=consent.notify_powered_trial_fault,
            serial_seconds=SERIAL_SECONDS,
        )
    except BaseException:
        metadata = output / "metadata.json"
        observation = (
            json.loads(metadata.read_text(encoding="utf-8")).get("observation", {})
            if metadata.is_file() else {})
        state = {
            "status": (
                "restored" if observation.get("final_baseline_verified")
                else "attention_restoration_unverified"
                if observation.get("restore_attempted")
                else "set_aborted_before_baseline"),
            "target": TARGET, "set_evidence": str(output),
        }
        if output.is_dir() and (output / "SHA256SUMS").is_file():
            state["set_manifest_sha256"] = _verify_manifest(output)
        _write_state(root, state)
        raise
    digest = _verify_manifest(output)
    _write_state(root, {
        "status": "restored", "target": TARGET, "set_evidence": str(output),
        "set_manifest_sha256": digest,
        "restoration": result["observation"]["restoration"],
    })
    return result


def acknowledge_restoration(evidence, *, physical_restoration_confirmed=False,
                            power_cycle_confirmed=False):
    if physical_restoration_confirmed is not True or power_cycle_confirmed is not True:
        raise ValueError("Literal physical-restoration and power-cycle confirmations are required.")
    evidence = _evidence_path(evidence)
    root = evidence.parent
    state = _load_state(root)
    if (not state or state.get("target") != TARGET
            or state.get("set_evidence") != str(evidence)
            or state.get("status") != "attention_restoration_unverified"):
        raise ValueError("No matching attention check awaits restoration acknowledgment.")
    digest = _verify_manifest(evidence)
    if state.get("set_manifest_sha256") != digest:
        raise ValueError("Attention-check evidence manifest differs from the state lock.")
    state.update(
        status="power_cycle_reset_confirmed",
        confirmed_at=time.time(),
        physical_restoration_confirmed=True,
        power_cycle_confirmed=True,
        restoration="operator_confirmation_plus_power_cycle_not_software_verification",
    )
    _write_state(root, state)
    return {
        "status": "power_cycle_reset_confirmed",
        "target": TARGET,
        "hardware_access": False,
        "restoration": "operator_confirmation_plus_power_cycle_not_software_verification",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--acknowledge-restoration", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--physical-restoration-confirmed", action="store_true")
    parser.add_argument("--power-cycle-confirmed", action="store_true")
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
                    or any(declarations.values())):
                raise ValueError("Restoration acknowledgment is offline bookkeeping only.")
            result = acknowledge_restoration(
                args.evidence,
                physical_restoration_confirmed=args.physical_restoration_confirmed,
                power_cycle_confirmed=args.power_cycle_confirmed)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if (args.evidence or args.physical_restoration_confirmed
                or args.power_cycle_confirmed):
            raise ValueError("Acknowledgment arguments require --acknowledge-restoration.")
        if args.run and args.output is None:
            raise ValueError("--output NEWDIR is required.")
        result = prepare() if not args.run else run_diagnostic(
            args.output, expected_physical_port=args.expected_physical_port,
            run=True, **declarations)
    except (Exception, KeyboardInterrupt) as error:
        if not args.acknowledge_restoration:
            consent.notify_powered_trial_fault(error)
        print(json.dumps({"status": "failed", "error": str(error),
                          "physical_stop": "not_established"}),
              file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
