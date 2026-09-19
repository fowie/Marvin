"""Fixed OFF-start wheel LED blink pilot with mandatory reverse cleanup.

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


STATE_BASELINE = bytes.fromhex("000000000000000000000000000000ff0000")
BLINK_BASELINE = bytes.fromhex("0000000000000000000000000000002a0000")
STATE_TEST = bytearray(STATE_BASELINE)
STATE_TEST[12] = 0xFF
STATE_TEST = bytes(STATE_TEST)
BLINK_TEST = bytearray(BLINK_BASELINE)
BLINK_TEST[12] = 42
BLINK_TEST = bytes(BLINK_TEST)
STEPS = {
    "state_baseline": _frame(3277, 0x17),
    "blink_baseline": _frame(3278, 0x19),
    "state_set": _frame(3279, 0x18, STATE_TEST),
    "blink_set": _frame(3280, 0x1A, BLINK_TEST),
    "blink_restore": _frame(3281, 0x1A, BLINK_BASELINE),
    "state_restore": _frame(3282, 0x18, STATE_BASELINE),
    "blink_verify": _frame(3283, 0x19),
    "state_verify": _frame(3284, 0x17),
}
TRANSCRIPT = tuple(STEPS.values())
SERIAL_SECONDS = 15
CLEANUP_SECONDS = 5
RESPONSE_SECONDS = 0.500
OBSERVATION_SECONDS = 3
SUCCESS = "wheel_led_blink_pilot_complete_unverified"
TARGET = "wheel-led-blink-off-start"


def prepare():
    expected = (
        (3277, 0x17, b""),
        (3278, 0x19, b""),
        (3279, 0x18, STATE_TEST),
        (3280, 0x1A, BLINK_TEST),
        (3281, 0x1A, BLINK_BASELINE),
        (3282, 0x18, STATE_BASELINE),
        (3283, 0x19, b""),
        (3284, 0x17, b""),
    )
    for raw, fields in zip(TRANSCRIPT, expected):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                fields[0], fields[1], 0, fields[2]):
            raise ValueError("Fixed wheel LED blink transcript disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "name": consent.WHEEL_LED_BLINK_SCOPE,
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "transcript_sha256": hashlib.sha256(b"".join(TRANSCRIPT)).hexdigest(),
        "required_state_baseline_hex": STATE_BASELINE.hex(),
        "required_blink_baseline_hex": BLINK_BASELINE.hex(),
        "state_change": {"index": 12, "from": 0, "to": 255},
        "blink_change": {"index": 12, "from": 0, "to": 42},
        "observation_seconds": OBSERVATION_SECONDS,
        "maximum_application_bytes": sum(map(len, TRANSCRIPT)),
        "maximum_writes": len(TRANSCRIPT),
        "max_serial_rx_bytes": 8192,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "cleanup_order": ["exact_blink_baseline_once", "exact_led_state_baseline_once"],
        "cleanup_policy": (
            "attempt_each_still_required_restore_once_after_any_possible_setter_syscall"),
        "verification_gate": "per_component_only_after_matching_crc_valid_raw80_restore",
        "raw82_meaning": "opaque_may_have_applied",
        "unverified_restoration_policy": (
            "physical_restoration_confirmation_and_power_cycle_acknowledgment_required"),
        "physical_led_effect": "not_established",
        "physical_stop": "not_established",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in consent.WHEEL_LED_BLINK_FLAGS)],
    }


class _BlinkTransport(LiveTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed = []
        self.may_have_applied = {"state": False, "blink": False}
        self.restore_attempted = {"state": False, "blink": False}
        self.restore_raw80 = {"state": False, "blink": False}

    def submit(self, step, *, deadline):
        if step in ("blink_restore", "state_restore"):
            return self._submit_restore_once(step, deadline=deadline)
        required = {
            "state_baseline": [],
            "blink_baseline": ["state_baseline"],
            "state_set": ["state_baseline", "blink_baseline"],
            "blink_set": ["state_baseline", "blink_baseline", "state_set"],
            "blink_verify": None,
            "state_verify": None,
        }
        if step not in required:
            raise OSError("Only the fixed wheel LED blink transcript is permitted.")
        component = step.removesuffix("_verify")
        if step.endswith("_verify"):
            if not self.restore_raw80[component]:
                raise OSError("Getter verification requires a clean raw-80 restore response.")
        elif self.completed != required[step]:
            raise OSError("Fixed wheel LED blink transcript order violated; no retry.")
        if step.endswith("_set"):
            self.may_have_applied[step.removesuffix("_set")] = True
        count = self._submit_once(STEPS[step], deadline=deadline)
        if count == len(STEPS[step]):
            self.completed.append(step)
        return count

    def _submit_restore_once(self, step, *, deadline):
        component = step.removesuffix("_restore")
        if self.restore_attempted[component]:
            raise OSError(f"{component} restore has already been attempted; no retry.")
        if not self.may_have_applied[component]:
            raise OSError(f"{component} restore is not required.")
        if component == "state" and self.may_have_applied["blink"] \
                and not self.restore_attempted["blink"]:
            raise OSError("Blink restore must be attempted before LED-state restore.")
        self.restore_attempted[component] = True
        raw = STEPS[step]
        self._check(deadline)
        self.ingress.expected_tx.append(raw)
        self.writes += 1
        count = os.write(self.fd, raw)
        self.last_write = time.monotonic()
        self.event("wheel_led_blink_restore_returned", component=component,
                   raw_hex=raw.hex(), accepted_bytes=count)
        if count == len(raw):
            self.completed.append(step)
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
                            else ["unexpected_wheel_led_blink_payload"])))
    evidence.submitted_at = clock()
    evidence.deadline = min(deadline, evidence.submitted_at + RESPONSE_SECONDS)
    if evidence.deadline != evidence.submitted_at + RESPONSE_SECONDS:
        raise OSError(f"Insufficient remaining budget for {step} response.")
    try:
        zero._observe_response(transport, evidence, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(evidence.events[0]["stream"]["raw_hex"]))
        report.setdefault("responses", []).append({
            "step": step,
            "sequence": packet.sequence,
            "command": packet.command,
            "raw_response_field": packet.response_field,
            "payload_bytes": len(packet.payload),
            "raw_payload_hex": packet.payload.hex(),
            "events": evidence.events,
        })
        return packet
    finally:
        evidence.finish(clock())


def _attempt_restore(transport, report, component, *, clock=time.monotonic):
    step = component + "_restore"
    deadline = clock() + CLEANUP_SECONDS
    _submit(transport, report, step, deadline=deadline)
    packet = _response(
        transport, report, step, deadline=deadline,
        accepted=(0x80, 0x82), clock=clock)
    report["restore_raw_response_fields"][component] = packet.response_field
    transport.restore_raw80[component] = packet.response_field == 0x80


def _cleanup(transport, report, *, clock=time.monotonic):
    errors = []
    for component in ("blink", "state"):
        if (transport.may_have_applied[component]
                and not transport.restore_attempted[component]):
            try:
                _attempt_restore(transport, report, component, clock=clock)
            except BaseException as error:
                errors.append((component, error))
    for component in ("blink", "state"):
        if transport.restore_raw80[component]:
            try:
                step = component + "_verify"
                _submit(transport, report, step, deadline=clock() + CLEANUP_SECONDS)
                _response(
                    transport, report, step, deadline=clock() + CLEANUP_SECONDS,
                    expected_payload=(
                        BLINK_BASELINE if component == "blink" else STATE_BASELINE),
                    clock=clock)
                report["getter_reverified"][component] = True
            except BaseException as error:
                errors.append((component + "_verify", error))
    return errors


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + SERIAL_SECONDS
    report.update(
        status="not_started", accepted_tx_bytes=0, uncertain_tx_bytes=0,
        responses=[], may_have_applied={"state": False, "blink": False},
        restore_attempted={"state": False, "blink": False},
        restore_raw_response_fields={"state": None, "blink": None},
        getter_reverified={"state": False, "blink": False},
        physical_observation="pending_external_operator_report",
        restoration="not_established", physical_stop="not_established",
    )
    primary = None
    cleanup_errors = []
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        for step, baseline in (
                ("state_baseline", STATE_BASELINE),
                ("blink_baseline", BLINK_BASELINE)):
            _submit(transport, report, step, deadline=deadline)
            _response(transport, report, step, deadline=deadline,
                      expected_payload=baseline, clock=clock)
        for step in ("state_set", "blink_set"):
            _submit(transport, report, step, deadline=deadline)
            packet = _response(
                transport, report, step, deadline=deadline,
                accepted=(0x80, 0x82), clock=clock)
            report[step + "_raw_response_field"] = packet.response_field
        print(
            f"OBSERVE_WHEEL_LEDS_NOW: fixed {OBSERVATION_SECONDS}-second window; "
            "mandatory cleanup follows automatically.", file=sys.stderr, flush=True)
        transport.wait(OBSERVATION_SECONDS)
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        cleanup_errors = _cleanup(transport, report, clock=clock)
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            cleanup_errors.append(("close", error))
        report.update(
            may_have_applied=dict(transport.may_have_applied),
            restore_attempted=dict(transport.restore_attempted),
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            cleanup_errors=[
                {"step": step, "error": str(error)[:1024]}
                for step, error in cleanup_errors
            ],
            application_acknowledgment="not_established",
            restoration=(
                "getter_baselines_reverified"
                if all(report["getter_reverified"].values())
                else "restore_attempts_completed_but_application_unverified"),
            subsequent_live_phase_gate=(
                "none"
                if all(report["getter_reverified"].values())
                else "physical_restoration_confirmation_and_power_cycle_acknowledgment_required"),
            physical_stop="not_established",
        )
        if primary is None and not cleanup_errors:
            report["status"] = SUCCESS
        elif cleanup_errors:
            report["status"] = "failed"
            if primary is None:
                raise cleanup_errors[0][1]
            for step, error in cleanup_errors:
                primary.add_note(f"Additional {step} cleanup error: {error}")


def _validate_capture(options):
    if options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation"):
        raise ValueError("Wheel LED blink pilot forbids other diagnostic profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) != consent.WHEEL_LED_BLINK_SCOPE:
        raise ValueError("Wheel LED blink pilot requires its complete literal scope.")


def run_diagnostic(output, *, expected_physical_port, run=False,
                   actuators_isolated=False, **declarations):
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope != consent.WHEEL_LED_BLINK_SCOPE:
        raise ValueError("Literal --run and wheel LED blink scope are required.")
    output = new_output_path(output)
    root = output.parent
    previous = _load_state(root)
    if previous and previous["status"] not in (
            "restored", "power_cycle_reset_confirmed", "set_aborted_before_baseline"):
        raise ValueError("RESTORE_REQUIRED: finish or power-cycle-confirm the prior LED round.")
    state = {
        "status": "blink_started",
        "target": TARGET,
        "set_evidence": str(output),
        "created_at": time.time(),
    }
    _write_state(root, state)
    try:
        result = zero._run_diagnostic(
            output, expected_physical_port=expected_physical_port, review=prepare(),
            transport_type=_BlinkTransport, observe=_observe,
            limits=zero._Limits(first_sequence=3277, max_requests=8, interval=0),
            session_options={"actuators_isolated": False, **declarations},
            declarations={**consent.powered_trial_history(declarations),
                          "run_id": str(uuid.uuid4())},
            expected_tx=lambda report: report["accepted_tx_bytes"],
            success_status=SUCCESS, report_key="wheel_led_blink_pilot",
            authorizations={"unvalidated_wheel_led_blink_pilot_authorized": True},
            capture_validator=_validate_capture,
            on_failure=consent.notify_powered_trial_fault,
            serial_seconds=SERIAL_SECONDS,
        )
    except BaseException:
        may_have_applied = {"state": False, "blink": False}
        metadata = output / "metadata.json"
        if metadata.is_file():
            may_have_applied.update(
                json.loads(metadata.read_text(encoding="utf-8")).get(
                    "observation", {}).get("may_have_applied", {}))
        state["status"] = (
            "blink_restoration_unverified"
            if any(may_have_applied.values()) else "set_aborted_before_baseline")
        if output.is_dir() and (output / "SHA256SUMS").is_file():
            state["set_manifest_sha256"] = _verify_manifest(output)
        _write_state(root, state)
        raise
    observation = result["observation"]
    state.update(
        status=(
            "restored"
            if observation["restoration"] == "getter_baselines_reverified"
            else "blink_restoration_unverified"),
        restoration=observation["restoration"],
        set_manifest_sha256=_verify_manifest(output),
    )
    _write_state(root, state)
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
            or state.get("status") != "blink_restoration_unverified"):
        raise ValueError("No matching unverified wheel LED blink phase awaits acknowledgment.")
    digest = _verify_manifest(evidence)
    if state.get("set_manifest_sha256") != digest:
        raise ValueError("Blink evidence manifest differs from the active state lock.")
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
                physical_restoration_confirmed=args.physical_restoration_confirmed,
                power_cycle_confirmed=args.power_cycle_confirmed)
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0
        if (args.evidence or args.physical_restoration_confirmed
                or args.power_cycle_confirmed):
            raise ValueError("Acknowledgment arguments require --acknowledge-restoration.")
        if args.actuators_isolated or any(declarations.values()):
            _validate_capture({"actuators_isolated": args.actuators_isolated, **declarations})
        if args.run and args.output is None:
            raise ValueError("--output NEWDIR is required.")
        result = prepare() if not args.run else run_diagnostic(
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
