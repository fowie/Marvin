"""Bounded legacy front-camera servo mapping; offline by default.

The only live profile is word0 2500 -> 2490 -> 2500 while word1 remains
2730. It never exposes arbitrary commands, values, timing, retry or reconnect.
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
BASELINE_PAYLOAD = b"".join(value.to_bytes(2, "little") for value in BASELINE)
TARGET_PAYLOAD = b"".join(value.to_bytes(2, "little") for value in TARGET)
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
ACKNOWLEDGMENTS = (
    "operator_present",
    "robot_secured",
    "independent_actuator_cutoff_ready",
    "drive_and_other_actuators_inactive",
    "front_camera_tilt_only_connected_projector_servo_physically_disconnected",
    "front_camera_servo_is_ax12_plus",
    "exact_profile_baseline_2500_2730_target_2490_2730_dwell_0_25_seconds",
    "authorize_single_legacy_1e_front_camera_mapping_command",
    "unprivileged_usbmon",
)


def prepare():
    expected = (
        (FIRST_SEQUENCE, 0x1D, b""),
        (FIRST_SEQUENCE + 1, 0x1E, TARGET_PAYLOAD),
        (FIRST_SEQUENCE + 2, 0x1E, BASELINE_PAYLOAD),
        (FIRST_SEQUENCE + 3, 0x1D, b""),
    )
    for raw, fields in zip(TRANSCRIPT, expected):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                fields[0], fields[1], 0, fields[2]):
            raise ValueError("Fixed front-servo transcript disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "live_execution_authorized": False,
        "profile": "marvin-legacy-se-front-camera-tilt-only-ax12-plus",
        "usb_identity": "045e:4444",
        "connected_servo": "front-camera-tilt-only",
        "projector_servo": "physically_disconnected",
        "baseline_words_uint16": list(BASELINE),
        "target_words_uint16": list(TARGET),
        "word0_delta": -10,
        "legacy_ui_scale": "0..3000 maps AX-12+ 0..300 degrees; 10 units = 1 degree",
        "observation_seconds": DWELL_SECONDS,
        "setter_response_observation_seconds": SET_RESPONSE_SECONDS,
        "overall_deadline_seconds": OVERALL_SECONDS,
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "transcript_sha256": hashlib.sha256(b"".join(TRANSCRIPT)).hexdigest(),
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
            *("--" + name.replace("_", "-") for name in ACKNOWLEDGMENTS),
        ],
        "refused": [
            "both_servos_connected", "arbitrary_target_delta_dwell_or_range",
            "standalone_restore_or_stop", "successor_commands",
            "calibration_flash_reset_or_power_state_operations",
        ],
    }


class _Transport(LiveTransport):
    steps = STEPS

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
            report["status"] = SUCCESS
        elif final_errors:
            report["status"] = "failed"
            if primary is None:
                raise final_errors[0][1]
            for step, error in final_errors:
                primary.add_note(f"Additional front-servo {step} error: {error}")


def run_diagnostic(output, *, expected_physical_port, run=False, **acknowledgments):
    if run is not True or set(acknowledgments) != set(ACKNOWLEDGMENTS):
        raise ValueError("Literal --run and the complete fixed front-servo scope are required.")
    if any(acknowledgments[name] is not True for name in ACKNOWLEDGMENTS):
        raise ValueError("Every separate front-servo acknowledgment must be literal true.")
    return zero._run_diagnostic(
        output,
        expected_physical_port=expected_physical_port,
        review=prepare(),
        transport_type=_Transport,
        observe=_observe,
        limits=zero._Limits(
            first_sequence=FIRST_SEQUENCE, max_requests=4, interval=0),
        session_options={"_front_servo_mapper": True},
        declarations={"operator_declarations": dict(acknowledgments)},
        expected_tx=lambda report: report["accepted_tx_bytes"],
        success_status=SUCCESS,
        report_key="front_servo_mapping",
        authorizations={"single_legacy_1e_front_camera_mapping_authorized": True},
        serial_seconds=OVERALL_SECONDS,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    for name in ACKNOWLEDGMENTS:
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")
    args = parser.parse_args(argv)
    acknowledgments = {name: getattr(args, name) for name in ACKNOWLEDGMENTS}
    try:
        if not args.run:
            if args.output or args.expected_physical_port or any(acknowledgments.values()):
                raise ValueError("Live-only arguments require --run.")
            result = prepare()
        else:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_diagnostic(
                args.output,
                expected_physical_port=args.expected_physical_port,
                run=True,
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
