"""Two named, bounded legacy front-camera servo mappings; offline by default.

No arbitrary commands, words, values, timing, retry or reconnect are exposed.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from tools import marvin_legacy_zero as zero
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet, encode_request, get_servo_position_request


BASELINE = (2500, 2730)
TARGET = (2490, 2730)
WORD1_TARGET = (2500, 2720)
BASELINE_PAYLOAD = b"".join(value.to_bytes(2, "little") for value in BASELINE)
TARGET_PAYLOAD = b"".join(value.to_bytes(2, "little") for value in TARGET)
WORD1_TARGET_PAYLOAD = b"".join(
    value.to_bytes(2, "little") for value in WORD1_TARGET)
FIRST_SEQUENCE = 3500
DWELL_SECONDS = 0.250
RESPONSE_SECONDS = 0.500
SET_RESPONSE_SECONDS = 0.200
OVERALL_SECONDS = 8
CLEANUP_RESERVE_SECONDS = 2
SUCCESS = "front_camera_servo_mapping_complete_protocol_only"
STEPS = {
    "baseline": get_servo_position_request(FIRST_SEQUENCE),
    "set": encode_request(FIRST_SEQUENCE + 1, 0x1E, TARGET_PAYLOAD),
    "restore": encode_request(FIRST_SEQUENCE + 2, 0x1E, BASELINE_PAYLOAD),
    "verify": get_servo_position_request(FIRST_SEQUENCE + 3),
}
TRANSCRIPT = tuple(STEPS.values())
WORD1_STEPS = {
    "baseline": get_servo_position_request(FIRST_SEQUENCE + 4),
    "set": encode_request(FIRST_SEQUENCE + 5, 0x1E, WORD1_TARGET_PAYLOAD),
    "restore": encode_request(FIRST_SEQUENCE + 6, 0x1E, BASELINE_PAYLOAD),
    "verify": get_servo_position_request(FIRST_SEQUENCE + 7),
}
WORD1_TRANSCRIPT = tuple(WORD1_STEPS.values())
COMMON_ACKNOWLEDGMENTS = (
    "operator_present",
    "robot_secured",
    "independent_actuator_cutoff_ready",
    "drive_and_other_actuators_inactive",
    "front_camera_tilt_only_connected_projector_servo_physically_disconnected",
    "front_camera_servo_is_ax12_plus",
    "unprivileged_usbmon",
)
ACKNOWLEDGMENTS = (
    *COMMON_ACKNOWLEDGMENTS,
    "exact_profile_baseline_2500_2730_target_2490_2730_dwell_0_25_seconds",
    "authorize_single_legacy_1e_front_camera_mapping_command",
)
WORD1_ACKNOWLEDGMENTS = (
    *COMMON_ACKNOWLEDGMENTS,
    "exact_profile_baseline_2500_2730_target_2500_2720_word1_hypothesis_dwell_0_25_seconds",
    "authorize_single_legacy_1e_front_camera_word1_hypothesis_command",
)


def _profile(word1_hypothesis):
    if type(word1_hypothesis) is not bool:
        raise ValueError("Word1 hypothesis selection must be a literal boolean.")
    return ({
        "name": "word1-front-camera-hypothesis",
        "word": 1,
        "target": WORD1_TARGET,
        "steps": WORD1_STEPS,
        "transcript": WORD1_TRANSCRIPT,
        "acknowledgments": WORD1_ACKNOWLEDGMENTS,
        "success": "front_camera_servo_word1_hypothesis_complete_protocol_only",
        "first_sequence": FIRST_SEQUENCE + 4,
    } if word1_hypothesis else {
        "name": "word0-front-camera-observation",
        "word": 0,
        "target": TARGET,
        "steps": STEPS,
        "transcript": TRANSCRIPT,
        "acknowledgments": ACKNOWLEDGMENTS,
        "success": SUCCESS,
        "first_sequence": FIRST_SEQUENCE,
    })


def prepare(*, word1_hypothesis=False):
    profile = _profile(word1_hypothesis)
    expected = (
        (profile["first_sequence"], 0x1D, b""),
        (profile["first_sequence"] + 1, 0x1E,
         WORD1_TARGET_PAYLOAD if word1_hypothesis else TARGET_PAYLOAD),
        (profile["first_sequence"] + 2, 0x1E, BASELINE_PAYLOAD),
        (profile["first_sequence"] + 3, 0x1D, b""),
    )
    for raw, fields in zip(profile["transcript"], expected):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                fields[0], fields[1], 0, fields[2]):
            raise ValueError("Fixed front-servo transcript disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "live_execution_authorized": False,
        "profile": "marvin-legacy-se-front-camera-tilt-only-ax12-plus",
        "fixed_mode": profile["name"],
        "candidate_wire_word": profile["word"],
        "word1_front_camera_assignment": (
            "hypothesis_not_proved" if word1_hypothesis else "not_tested_by_this_profile"),
        "usb_identity": "045e:4444",
        "connected_servo": "front-camera-tilt-only",
        "projector_servo": "physically_disconnected",
        "baseline_words_uint16": list(BASELINE),
        "target_words_uint16": list(profile["target"]),
        f"word{profile['word']}_delta": -10,
        **({"external_word0_observation": {
            "operator_report": "no_visible_movement",
            "protocol_baseline_words_uint16": list(BASELINE),
            "protocol_target_words_uint16": list(TARGET),
            "protocol_restore_and_final_getter_words_uint16": list(BASELINE),
            "setter_raw_response_field_uint8": 0x82,
            "restore_raw_response_field_uint8": 0x82,
            "accepted_tx_only": True,
            "uncertain_tx_bytes": 0,
            "usbmon_dropped": 0,
            "post_run_operator_report": "marvin_off_and_host_usb_disconnected",
            "classification": "external_evidence_not_channel_proof",
        }} if word1_hypothesis else {}),
        "legacy_ui_scale": "0..3000 maps AX-12+ 0..300 degrees; 10 units = 1 degree",
        "observation_seconds": DWELL_SECONDS,
        "setter_response_observation_seconds": SET_RESPONSE_SECONDS,
        "overall_deadline_seconds": OVERALL_SECONDS,
        "immutable_application_transcript_hex": [
            raw.hex() for raw in profile["transcript"]],
        "transcript_sha256": hashlib.sha256(
            b"".join(profile["transcript"])).hexdigest(),
        "maximum_writes": 4,
        "maximum_nonzero_setters": 1,
        "cleanup_attempts_after_setter_may_apply": 1,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "response_policy": (
            "baseline/verify require unique CRC-valid sequence/cmd-correlated raw80 "
            "with exact [2500,2730] payload; setter/restore retain raw80/raw82 as "
            "observed status without treating either as generic success"
        ),
        "restoration_limit": (
            "protocol getter agreement does not prove physical position or restoration"
        ),
        "required": [
            "--run", "--expected-physical-port", "--output NEWDIR",
            *(("--word1-front-camera-hypothesis",) if word1_hypothesis else ()),
            *("--" + name.replace("_", "-")
              for name in profile["acknowledgments"]),
        ],
        "refused": [
            "both_servos_connected", "arbitrary_target_delta_dwell_or_range",
            "standalone_restore_or_stop", "successor_commands",
            "calibration_flash_reset_or_power_state_operations",
        ],
    }


class _Transport(LiveTransport):
    steps = STEPS
    success = SUCCESS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed = []
        self.nonzero_may_have_applied = False
        self.restore_attempted = False
        self.restore_correlated = False

    def submit(self, step, *, deadline):
        if step == "restore":
            return self._restore_once(deadline=deadline)
        if step == "baseline" and self.completed:
            raise OSError("Baseline getter must be first.")
        if step == "set":
            if self.completed != ["baseline"]:
                raise OSError("Setter requires the exact correlated baseline.")
            self.nonzero_may_have_applied = True
        elif step == "verify":
            if not self.restore_correlated:
                raise OSError("Verification getter requires a correlated restore response.")
        elif step != "baseline":
            raise OSError("Only the fixed front-servo mapping transcript is permitted.")
        count = self._submit_once(self.steps[step], deadline=deadline)
        if count == len(self.steps[step]):
            self.completed.append(step)
        return count

    def _restore_once(self, *, deadline):
        if self.restore_attempted:
            raise OSError("Restore has already been attempted; no retry.")
        if not self.nonzero_may_have_applied:
            raise OSError("Restore is only admitted after the setter may have applied.")
        self.restore_attempted = True
        raw = self.steps["restore"]
        self._check(deadline)
        self.ingress.expected_tx.append(raw)
        self.writes += 1
        self.last_write_sequence = decode_packet(raw).sequence
        self.last_write_started = time.monotonic()
        count = os.write(self.fd, raw)
        self.last_write = time.monotonic()
        self.event("front_servo_restore_returned", raw_hex=raw.hex(), accepted_bytes=count)
        if count == len(raw):
            self.completed.append("restore")
        return count


class _Word1Transport(_Transport):
    steps = WORD1_STEPS
    success = "front_camera_servo_word1_hypothesis_complete_protocol_only"


def _submit(transport, report, step, *, deadline):
    raw = transport.steps[step]
    report["uncertain_tx_bytes"] += len(raw)
    count = transport.submit(step, deadline=deadline)
    if type(count) is not int or not 0 <= count <= len(raw):
        raise OSError(f"Unknown {step} write result; no retry.")
    report["accepted_tx_bytes"] += count
    report["uncertain_tx_bytes"] -= count
    if count != len(raw):
        raise OSError(f"Partial {step} write; no retry.")


def _response(transport, report, step, *, deadline, clock=time.monotonic):
    request = decode_packet(transport.steps[step])
    if (transport.last_write_sequence != request.sequence
            or type(transport.last_write_started) not in (int, float)
            or transport.last_write_started > clock()):
        raise OSError("Response lacks the matching immediate pre-write boundary.")
    getter = step in ("baseline", "verify")
    evidence = zero._ResponseEvidence(
        transport.event,
        sequence=request.sequence,
        command=request.command,
        accepted_response_fields=((0x80,) if getter else (0x80, 0x82)),
        validate_packet=lambda packet: (
            [] if packet.payload == (BASELINE_PAYLOAD if getter else b"")
            else ["unexpected_front_servo_payload"]),
    )
    evidence.submitted_at = transport.last_write_started
    response_seconds = SET_RESPONSE_SECONDS if step == "set" else RESPONSE_SECONDS
    evidence.deadline = min(deadline, evidence.submitted_at + response_seconds)
    if evidence.deadline != evidence.submitted_at + response_seconds:
        raise OSError(f"Insufficient overall deadline for {step} response.")
    try:
        zero._observe_response(transport, evidence, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(evidence.events[0]["stream"]["raw_hex"]))
        report["protocol_evidence"].append({
            "step": step,
            "sequence": packet.sequence,
            "command": packet.command,
            "raw_response_field": packet.response_field,
            "raw_payload_hex": packet.payload.hex(),
            "application_acknowledgment": "not_established",
            "events": evidence.events,
        })
        return packet
    finally:
        evidence.finish(clock())


def _observe(transport, report, *, clock=time.monotonic):
    overall_deadline = clock() + OVERALL_SECONDS
    active_deadline = overall_deadline - CLEANUP_RESERVE_SECONDS
    report.update(
        status="not_started",
        accepted_tx_bytes=0,
        uncertain_tx_bytes=0,
        protocol_evidence=[],
        operator_physical_observation={
            "status": "pending_external_operator_report",
            "not_inferred_from_protocol": True,
        },
        nonzero_may_have_applied=False,
        restore_attempted=False,
        restore_correlated=False,
        getter_reverified=False,
        restoration="not_required_before_setter",
        overall_deadline_monotonic=overall_deadline,
        application_acknowledgment="not_established",
    )
    primary = None
    final_errors = []
    try:
        if transport.revalidate(deadline=active_deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        _submit(transport, report, "baseline", deadline=active_deadline)
        _response(transport, report, "baseline", deadline=active_deadline, clock=clock)
        print(
            "OBSERVE_FRONT_CAMERA_TILT_ONLY_NOW: report physical motion separately; "
            "fixed 0.25-second maximum; independent cutoff is primary; restore follows.",
            file=sys.stderr,
            flush=True,
        )
        _submit(transport, report, "set", deadline=active_deadline)
        report["setter_prewrite_monotonic"] = transport.last_write_started
        _response(transport, report, "set", deadline=active_deadline, clock=clock)
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        if transport.nonzero_may_have_applied and not transport.restore_attempted:
            try:
                _submit(transport, report, "restore", deadline=overall_deadline)
                _response(
                    transport, report, "restore", deadline=overall_deadline, clock=clock)
                transport.restore_correlated = True
                setter_started = report.get("setter_prewrite_monotonic")
                if type(setter_started) not in (int, float):
                    raise OSError("Setter timing boundary is uncertain after its write fault.")
                elapsed = transport.last_write_started - setter_started
                report["setter_to_restore_start_seconds"] = elapsed
                if elapsed > DWELL_SECONDS:
                    raise OSError("Restore started after the 0.25-second setter bound.")
            except BaseException as error:
                final_errors.append(("restore", error))
        if transport.restore_correlated:
            try:
                _submit(transport, report, "verify", deadline=overall_deadline)
                _response(
                    transport, report, "verify", deadline=overall_deadline, clock=clock)
                report["getter_reverified"] = True
            except BaseException as error:
                final_errors.append(("verify", error))
        try:
            transport.close(deadline=overall_deadline)
        except BaseException as error:
            final_errors.append(("close", error))
        report.update(
            nonzero_may_have_applied=transport.nonzero_may_have_applied,
            restore_attempted=transport.restore_attempted,
            restore_correlated=transport.restore_correlated,
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            finalization_errors=[
                {"step": step, "error": f"{type(error).__name__}: {error}"[:1024]}
                for step, error in final_errors
            ],
            restoration=(
                "protocol_getter_matches_2500_2730_physical_restoration_unproved"
                if report["getter_reverified"]
                else "restore_attempted_but_restoration_uncertain"
                if transport.restore_attempted
                else "not_required_before_setter"),
        )
        if primary is None and not final_errors:
            report["status"] = transport.success
        elif final_errors:
            report["status"] = "failed"
            if primary is None:
                raise final_errors[0][1]
            for step, error in final_errors:
                primary.add_note(f"Additional front-servo {step} error: {error}")


def run_diagnostic(output, *, expected_physical_port, run=False,
                   word1_hypothesis=False, **acknowledgments):
    profile = _profile(word1_hypothesis)
    required = profile["acknowledgments"]
    if run is not True or set(acknowledgments) != set(required):
        raise ValueError("Literal --run and the complete fixed front-servo scope are required.")
    if any(acknowledgments[name] is not True for name in required):
        raise ValueError("Every separate front-servo acknowledgment must be literal true.")
    return zero._run_diagnostic(
        output,
        expected_physical_port=expected_physical_port,
        review=prepare(word1_hypothesis=word1_hypothesis),
        transport_type=_Word1Transport if word1_hypothesis else _Transport,
        observe=_observe,
        limits=zero._Limits(
            first_sequence=profile["first_sequence"], max_requests=4, interval=0),
        session_options={
            "_front_servo_mapper": True,
            "_front_servo_word1_mapper": word1_hypothesis,
        },
        declarations={"operator_declarations": dict(acknowledgments)},
        expected_tx=lambda report: report["accepted_tx_bytes"],
        success_status=profile["success"],
        report_key="front_servo_mapping",
        authorizations={
            ("single_legacy_1e_front_camera_word1_hypothesis_authorized"
             if word1_hypothesis
             else "single_legacy_1e_front_camera_mapping_authorized"): True},
        serial_seconds=OVERALL_SECONDS,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--word1-front-camera-hypothesis", action="store_true")
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    all_acknowledgments = tuple(dict.fromkeys(
        (*ACKNOWLEDGMENTS, *WORD1_ACKNOWLEDGMENTS)))
    for name in all_acknowledgments:
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")
    args = parser.parse_args(argv)
    profile = _profile(args.word1_front_camera_hypothesis)
    acknowledgments = {
        name: getattr(args, name) for name in profile["acknowledgments"]}
    unused_acknowledgments = set(all_acknowledgments) - set(profile["acknowledgments"])
    try:
        if any(getattr(args, name) for name in unused_acknowledgments):
            raise ValueError("Authorization literals cannot be mixed between fixed profiles.")
        if not args.run:
            if args.output or args.expected_physical_port or any(acknowledgments.values()):
                raise ValueError("Live-only arguments require --run.")
            result = prepare(
                word1_hypothesis=args.word1_front_camera_hypothesis)
        else:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_diagnostic(
                args.output,
                expected_physical_port=args.expected_physical_port,
                run=True,
                word1_hypothesis=args.word1_front_camera_hypothesis,
                **acknowledgments,
            )
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({
            "status": "failed",
            "error": str(error),
            "restoration": "uncertain_if_nonzero_setter_may_have_applied",
        }), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
