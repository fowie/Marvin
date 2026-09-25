"""Named, bounded legacy front-camera servo mappings; offline by default.

No arbitrary commands, words, values, timing, retry or reconnect are exposed.
"""

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import select
import sys
import time

from tools import marvin_legacy_zero as zero
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet, encode_request, get_servo_position_request
from tools.marvin_legacy_stream import LegacyStreamDecoder


BASELINE = (2500, 2730)
TARGET = (2490, 2730)
WORD1_TARGET = (2500, 2720)
WORD0_FIVE_DEGREE_TARGET = (2450, 2730)
WORD1_FIVE_DEGREE_TARGET = (2500, 2680)
BASELINE_PAYLOAD = b"".join(value.to_bytes(2, "little") for value in BASELINE)
TARGET_PAYLOAD = b"".join(value.to_bytes(2, "little") for value in TARGET)
WORD1_TARGET_PAYLOAD = b"".join(
    value.to_bytes(2, "little") for value in WORD1_TARGET)
WORD0_FIVE_DEGREE_TARGET_PAYLOAD = b"".join(
    value.to_bytes(2, "little") for value in WORD0_FIVE_DEGREE_TARGET)
WORD1_FIVE_DEGREE_TARGET_PAYLOAD = b"".join(
    value.to_bytes(2, "little") for value in WORD1_FIVE_DEGREE_TARGET)
FIRST_SEQUENCE = 3500
DWELL_SECONDS = 0.250
DIRECTION_HOLD_SECONDS = 0.250
MAX_DIRECTION_HOLD_OVERRUN_SECONDS = 0.010
RESPONSE_SECONDS = 0.500
MAX_DIRECTION_SETTER_TO_RESTORE_SECONDS = (
    RESPONSE_SECONDS + DIRECTION_HOLD_SECONDS
    + MAX_DIRECTION_HOLD_OVERRUN_SECONDS)
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
WORD0_FIVE_DEGREE_STEPS = {
    "baseline": get_servo_position_request(FIRST_SEQUENCE + 8),
    "set": encode_request(
        FIRST_SEQUENCE + 9, 0x1E, WORD0_FIVE_DEGREE_TARGET_PAYLOAD),
    "restore": encode_request(FIRST_SEQUENCE + 10, 0x1E, BASELINE_PAYLOAD),
    "verify": get_servo_position_request(FIRST_SEQUENCE + 11),
}
WORD0_FIVE_DEGREE_TRANSCRIPT = tuple(WORD0_FIVE_DEGREE_STEPS.values())
WORD1_FIVE_DEGREE_STEPS = {
    "baseline": get_servo_position_request(FIRST_SEQUENCE + 12),
    "set": encode_request(
        FIRST_SEQUENCE + 13, 0x1E, WORD1_FIVE_DEGREE_TARGET_PAYLOAD),
    "restore": encode_request(FIRST_SEQUENCE + 14, 0x1E, BASELINE_PAYLOAD),
    "verify": get_servo_position_request(FIRST_SEQUENCE + 15),
}
WORD1_FIVE_DEGREE_TRANSCRIPT = tuple(WORD1_FIVE_DEGREE_STEPS.values())
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
WORD0_FIVE_DEGREE_ACKNOWLEDGMENTS = (
    *COMMON_ACKNOWLEDGMENTS,
    "operator_confirmed_word0_five_degree_mechanical_clearance",
    "exact_profile_baseline_2500_2730_target_2450_2730_word0_five_degree_dwell_0_25_seconds",
    "authorize_single_legacy_1e_front_camera_word0_five_degree_diagnostic_command",
)
WORD1_FIVE_DEGREE_ACKNOWLEDGMENTS = (
    *COMMON_ACKNOWLEDGMENTS,
    "operator_confirmed_word1_five_degree_mechanical_clearance",
    "exact_profile_baseline_2500_2730_target_2500_2680_word1_five_degree_dwell_0_25_seconds",
    "authorize_single_legacy_1e_front_camera_word1_five_degree_diagnostic_command",
)
DIRECTION_ACKNOWLEDGMENTS = (
    *COMMON_ACKNOWLEDGMENTS,
    "operator_confirmed_word0_50_unit_direction_observation_clearance",
    "exact_profile_baseline_2500_2730_target_2450_2730_actual_hold_0_250_seconds",
    "acknowledge_maximum_setter_to_restore_0_760_seconds",
    "authorize_single_legacy_1e_front_camera_word0_direction_diagnostic_command",
)


def _profile(
        word1_hypothesis, word0_five_degree, word1_five_degree,
        word0_direction=False):
    if any(type(value) is not bool for value in (
            word1_hypothesis, word0_five_degree, word1_five_degree,
            word0_direction)):
        raise ValueError("Fixed profile selections must be literal booleans.")
    if sum((
            word1_hypothesis, word0_five_degree, word1_five_degree,
            word0_direction)) > 1:
        raise ValueError("Select exactly one fixed front-servo diagnostic mode.")
    if word0_direction:
        return {
            "name": "word0-direction-diagnostic",
            "word": 0,
            "target": WORD0_FIVE_DEGREE_TARGET,
            "target_payload": WORD0_FIVE_DEGREE_TARGET_PAYLOAD,
            "delta": -50,
            "steps": WORD0_FIVE_DEGREE_STEPS,
            "transcript": WORD0_FIVE_DEGREE_TRANSCRIPT,
            "acknowledgments": DIRECTION_ACKNOWLEDGMENTS,
            "success": "front_camera_servo_word0_direction_complete_protocol_only",
            "first_sequence": FIRST_SEQUENCE + 8,
            "mode_flag": "--word0-direction-diagnostic",
        }
    if word1_five_degree:
        return {
            "name": "word1-five-degree-diagnostic",
            "word": 1,
            "target": WORD1_FIVE_DEGREE_TARGET,
            "target_payload": WORD1_FIVE_DEGREE_TARGET_PAYLOAD,
            "delta": -50,
            "steps": WORD1_FIVE_DEGREE_STEPS,
            "transcript": WORD1_FIVE_DEGREE_TRANSCRIPT,
            "acknowledgments": WORD1_FIVE_DEGREE_ACKNOWLEDGMENTS,
            "success": "front_camera_servo_word1_five_degree_complete_protocol_only",
            "first_sequence": FIRST_SEQUENCE + 12,
            "mode_flag": "--word1-five-degree-diagnostic",
        }
    if word0_five_degree:
        return {
            "name": "word0-five-degree-diagnostic",
            "word": 0,
            "target": WORD0_FIVE_DEGREE_TARGET,
            "target_payload": WORD0_FIVE_DEGREE_TARGET_PAYLOAD,
            "delta": -50,
            "steps": WORD0_FIVE_DEGREE_STEPS,
            "transcript": WORD0_FIVE_DEGREE_TRANSCRIPT,
            "acknowledgments": WORD0_FIVE_DEGREE_ACKNOWLEDGMENTS,
            "success": "front_camera_servo_word0_five_degree_complete_protocol_only",
            "first_sequence": FIRST_SEQUENCE + 8,
            "mode_flag": "--word0-five-degree-diagnostic",
        }
    if word1_hypothesis:
        return {
        "name": "word1-front-camera-hypothesis",
        "word": 1,
        "target": WORD1_TARGET,
        "target_payload": WORD1_TARGET_PAYLOAD,
        "delta": -10,
        "steps": WORD1_STEPS,
        "transcript": WORD1_TRANSCRIPT,
        "acknowledgments": WORD1_ACKNOWLEDGMENTS,
        "success": "front_camera_servo_word1_hypothesis_complete_protocol_only",
        "first_sequence": FIRST_SEQUENCE + 4,
        "mode_flag": "--word1-front-camera-hypothesis",
    }
    return {
        "name": "word0-front-camera-observation",
        "word": 0,
        "target": TARGET,
        "target_payload": TARGET_PAYLOAD,
        "delta": -10,
        "steps": STEPS,
        "transcript": TRANSCRIPT,
        "acknowledgments": ACKNOWLEDGMENTS,
        "success": SUCCESS,
        "first_sequence": FIRST_SEQUENCE,
        "mode_flag": None,
    }


def prepare(*, word1_hypothesis=False, word0_five_degree=False,
            word1_five_degree=False, word0_direction=False):
    profile = _profile(
        word1_hypothesis, word0_five_degree, word1_five_degree,
        word0_direction)
    expected = (
        (profile["first_sequence"], 0x1D, b""),
        (profile["first_sequence"] + 1, 0x1E, profile["target_payload"]),
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
            "hypothesis_not_proved"
            if word1_hypothesis else "not_proved_by_negative_observations"),
        "usb_identity": "045e:4444",
        "connected_servo": "front-camera-tilt-only",
        "projector_servo": "physically_disconnected",
        "baseline_words_uint16": list(BASELINE),
        "target_words_uint16": list(profile["target"]),
        f"word{profile['word']}_delta": profile["delta"],
        **({"external_one_degree_observations": {
            "word0": {
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
            },
            **({"word1": {
                "operator_report": "no_visible_movement",
                "operator_ax12_plus_led_report": "illuminated_or_blinked_during_power_on",
                "protocol_baseline_words_uint16": list(BASELINE),
                "protocol_target_words_uint16": list(WORD1_TARGET),
                "protocol_restore_and_final_getter_words_uint16": list(BASELINE),
                "setter_raw_response_field_uint8": 0x82,
                "restore_raw_response_field_uint8": 0x82,
                "accepted_tx_only": True,
                "uncertain_tx_bytes": 0,
                "usbmon_dropped": 0,
                "post_run_operator_report": "marvin_off_and_host_usb_disconnected",
                "classification": "external_evidence_not_channel_proof",
            }} if word0_five_degree or word1_five_degree or word0_direction else {}),
        }} if (word1_hypothesis or word0_five_degree or word1_five_degree
               or word0_direction) else {}),
        **({"external_word0_five_degree_observation": {
            "operator_report": "no_visible_movement_and_audible_servo_engagement",
            "protocol_baseline_words_uint16": list(BASELINE),
            "protocol_target_words_uint16": list(WORD0_FIVE_DEGREE_TARGET),
            "setter_and_restore_host_submission": "established",
            "restore_application_correlation": "not_established",
            "physical_restoration": "unproved",
            "classification": "external_evidence_not_channel_proof",
        }} if word1_five_degree else {}),
        "legacy_ui_scale": (
            "historical UI assumption only: 0..3000 maps AX-12+ 0..300 degrees; "
            "installed word0 observation instead found 50 units approximately 0.5 degrees"
        ),
        **({"five_degree_safety_basis": (
            "historically labeled five-degree profile is a fixed 50-unit decrement; "
            "the operator later observed approximately 0.5 degrees downward, not a "
            "precision calibration and does not establish mechanical safety; separate "
            "clearance remains required"
        )} if word0_five_degree or word1_five_degree or word0_direction else {}),
        **({"operator_observed_direction_calibration": {
            "word0_change": "2500_to_2450",
            "direction": "downward",
            "approximate_displacement_degrees": 0.5,
            "classification": "operator_observed_approximate_not_full_range_or_precision",
        }} if word0_direction else {}),
        "observation_seconds": (
            DIRECTION_HOLD_SECONDS if word0_direction else DWELL_SECONDS),
        "observation_timing_semantics": (
            "actual hold from clean correlated setter response end; restore target "
            "0.250 seconds later with 0.010-second maximum scheduling overrun"
            if word0_direction else
            "maximum setter-start-to-restore-prewrite deadline; not an actual dwell"
        ),
        **({"maximum_setter_to_restore_seconds":
            MAX_DIRECTION_SETTER_TO_RESTORE_SECONDS} if word0_direction else {}),
        "post_restore_response_observation_seconds": RESPONSE_SECONDS,
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
            *((profile["mode_flag"],) if profile["mode_flag"] else ()),
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
        self.setter_write_started = None
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
            self.last_write_started = None
            self.last_write_sequence = None
            try:
                count = self._submit_once(self.steps[step], deadline=deadline)
            except BaseException:
                self.setter_write_started = self.last_write_started
                self.nonzero_may_have_applied = (
                    type(self.setter_write_started) in (int, float))
                raise
            self.setter_write_started = self.last_write_started
            self.nonzero_may_have_applied = True
            if count == len(self.steps[step]):
                self.completed.append(step)
            return count
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
        raw = self.steps["restore"]
        self._owner()
        if self.closed or self.fd is None:
            raise OSError("Owned tty is unavailable for the single restore attempt.")
        self.ingress.expected_tx.append(raw)
        self.writes += 1
        self.last_write_sequence = decode_packet(raw).sequence
        self.last_write_started = time.monotonic()
        self.restore_attempted = True
        self.restore_started_within_bound = self.last_write_started <= deadline
        count = os.write(self.fd, raw)
        self.last_write = time.monotonic()
        self.event(
            "front_servo_restore_returned",
            raw_hex=raw.hex(),
            accepted_bytes=count,
            restore_prewrite_deadline=deadline,
            restore_started_within_bound=self.restore_started_within_bound,
        )
        if count == len(raw):
            self.completed.append("restore")
        return count


class _Word1Transport(_Transport):
    steps = WORD1_STEPS
    success = "front_camera_servo_word1_hypothesis_complete_protocol_only"


class _Word0FiveDegreeTransport(_Transport):
    steps = WORD0_FIVE_DEGREE_STEPS
    success = "front_camera_servo_word0_five_degree_complete_protocol_only"


class _Word1FiveDegreeTransport(_Transport):
    steps = WORD1_FIVE_DEGREE_STEPS
    success = "front_camera_servo_word1_five_degree_complete_protocol_only"


class _Word0DirectionTransport(_Transport):
    steps = WORD0_FIVE_DEGREE_STEPS
    success = "front_camera_servo_word0_direction_complete_protocol_only"
    direction_hold = True


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
    evidence.deadline = min(deadline, evidence.submitted_at + RESPONSE_SECONDS)
    if evidence.deadline != evidence.submitted_at + RESPONSE_SECONDS:
        raise OSError(f"Insufficient overall deadline for {step} response.")
    try:
        zero._observe_response(
            transport, evidence, deadline=evidence.deadline, clock=clock)
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


class _SetRestoreEvidence:
    def __init__(self, transport, report, deadline):
        self.transport = transport
        self.report = report
        self.deadline = deadline
        self.decoder = LegacyStreamDecoder(max_input_bytes=8192)
        self.spans = deque()
        self.last_bounds = None
        self.events = []
        self.seen = {"set": 0, "restore": 0}
        self.clean_ended = {}
        self.expected = {
            (decode_packet(transport.steps[step]).sequence, 0x1E): step
            for step in self.seen
        }

    def feed(self, received, now):
        start = self.decoder.input_bytes
        self.spans.append((
            start, start + len(received.data),
            received.started_at, received.ended_at))
        timing_ok = (
            received.started_at <= received.ended_at <= now
            and (self.last_bounds is None
                 or (received.started_at >= self.last_bounds[0]
                     and received.ended_at >= self.last_bounds[1])))
        self.last_bounds = (received.started_at, received.ended_at)
        self._record(self.decoder.feed(received.data), now, timing_ok)

    def finish(self, now):
        self._record(self.decoder.finish(), now, True)

    def _record(self, events, now, timing_ok):
        faults = []
        for event in events:
            spans = [
                span for span in self.spans
                if span[0] < event.end_offset and span[1] > event.offset]
            started = min((span[2] for span in spans), default=None)
            ended = max((span[3] for span in spans), default=None)
            labels = ["unverified_shape_and_semantics"]
            step = None
            if not timing_ok:
                labels.append("invalid_ingress_bounds")
            if event.kind != "frame":
                labels.append(event.kind)
            else:
                packet = event.packet
                step = self.expected.get((packet.sequence, packet.command))
                if step is None:
                    labels.append("unexpected_command_or_sequence")
                else:
                    submitted = self.report.get(
                        "setter_prewrite_monotonic"
                        if step == "set" else "restore_prewrite_monotonic")
                    if (started is None or submitted is None
                            or started <= submitted):
                        labels.append("prewrite_or_ambiguous")
                    if ended is None or ended >= self.deadline or now >= self.deadline:
                        labels.append("late")
                    if packet.response_field not in (0x80, 0x82):
                        labels.append("uninterpreted_non80_status")
                    if packet.payload:
                        labels.append("unexpected_front_servo_payload")
                    if self.seen[step]:
                        labels.append("additional_frame")
                    if event.follows_corruption:
                        labels.append("ambiguous_boundary")
                    if len(labels) == 1:
                        labels.append("correlated_command_sequence_only")
                        self.seen[step] += 1
                        self.clean_ended[step] = ended
            clean = labels == [
                "unverified_shape_and_semantics",
                "correlated_command_sequence_only",
            ]
            row = {
                "stream": event.to_dict(),
                "started_at": started,
                "ended_at": ended,
                "labels": labels,
                "application_acknowledgment": "not_established",
            }
            self.events.append(row)
            self.transport.event("response_evidence", **row)
            if clean:
                packet = event.packet
                self.report["protocol_evidence"].append({
                    "step": step,
                    "sequence": packet.sequence,
                    "command": packet.command,
                    "raw_response_field": packet.response_field,
                    "raw_payload_hex": packet.payload.hex(),
                    "application_acknowledgment": "not_established",
                    "events": [row],
                })
            else:
                faults.append(labels)
            while self.spans and self.spans[0][1] <= event.end_offset:
                self.spans.popleft()
        if faults:
            raise OSError(
                f"Unclean set/restore response evidence: {faults[0]}")


def _set_restore_responses(
        transport, report, *, deadline, evidence=None, clock=time.monotonic):
    if evidence is None:
        evidence = _SetRestoreEvidence(transport, report, deadline)
    evidence.deadline = deadline
    for _ in range(4096):
        if clock() >= deadline:
            break
        transport.ingress.pump()
        remaining = deadline - clock()
        if remaining <= 0:
            break
        readable, _, _ = select.select(
            [transport.fd], [], [], min(0.005, remaining))
        if readable:
            evidence.feed(
                transport.read_response(512, deadline=deadline), clock())
    else:
        raise OSError("Set/restore response observation iteration budget exhausted.")
    evidence.finish(clock())
    return evidence.seen


def _direction_hold(
        transport, report, evidence, *, deadline, clock=time.monotonic):
    response_deadline = min(
        deadline, transport.setter_write_started + RESPONSE_SECONDS)
    evidence.deadline = response_deadline
    for _ in range(4096):
        if evidence.seen["set"] == 1:
            break
        if clock() >= response_deadline:
            raise OSError("No clean correlated setter response before direction hold.")
        transport.ingress.pump()
        remaining = response_deadline - clock()
        if remaining <= 0:
            continue
        readable, _, _ = select.select(
            [transport.fd], [], [], min(0.005, remaining))
        if readable:
            evidence.feed(
                transport.read_response(512, deadline=response_deadline), clock())
    else:
        raise OSError("Direction setter response iteration budget exhausted.")
    hold_start = evidence.clean_ended["set"]
    hold_target = hold_start + DIRECTION_HOLD_SECONDS
    hold_maximum = hold_target + MAX_DIRECTION_HOLD_OVERRUN_SECONDS
    if hold_maximum > deadline:
        raise OSError("Direction hold cannot fit before the cleanup deadline.")
    report.update(
        requested_hold_seconds=DIRECTION_HOLD_SECONDS,
        hold_start_boundary="clean_correlated_setter_response_end",
        hold_start_monotonic=hold_start,
        hold_target_monotonic=hold_target,
        hold_maximum_restore_start_monotonic=hold_maximum,
    )
    transport.identity(deadline=hold_maximum)
    report["restore_identity_validated_after_setter"] = True
    for _ in range(4096):
        if clock() >= hold_target:
            break
        transport.ingress.pump()
        remaining = hold_target - clock()
        if remaining <= 0:
            break
        readable, _, _ = select.select(
            [transport.fd], [], [], min(0.005, remaining))
        if readable:
            evidence.feed(
                transport.read_response(512, deadline=hold_target), clock())
    else:
        raise OSError("Direction hold iteration budget exhausted.")
    if clock() > hold_maximum:
        raise OSError("Direction hold exceeded its maximum scheduling overrun.")


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
    direction_evidence = None
    try:
        if transport.revalidate(deadline=active_deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        _submit(transport, report, "baseline", deadline=active_deadline)
        _response(transport, report, "baseline", deadline=active_deadline, clock=clock)
        print((
            "OBSERVE_FRONT_CAMERA_TILT_ONLY_NOW: report direction separately; "
            "0.250-second post-response hold, maximum 0.760 seconds from setter "
            "start to restore start; independent cutoff is primary."
            if getattr(transport, "direction_hold", False) else
            "OBSERVE_FRONT_CAMERA_TILT_ONLY_NOW: report physical motion separately; "
            "0.250-second maximum restore deadline, not a dwell; independent "
            "cutoff is primary; restore follows."
        ),
            file=sys.stderr,
            flush=True,
        )
        if getattr(transport, "direction_hold", False):
            transport.identity(deadline=active_deadline)
        _submit(transport, report, "set", deadline=active_deadline)
        report["setter_prewrite_monotonic"] = transport.setter_write_started
        if getattr(transport, "direction_hold", False):
            direction_evidence = _SetRestoreEvidence(
                transport, report, active_deadline)
            _direction_hold(
                transport, report, direction_evidence,
                deadline=active_deadline, clock=clock)
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        if transport.nonzero_may_have_applied and not transport.restore_attempted:
            try:
                setter_started = transport.setter_write_started
                if type(setter_started) not in (int, float):
                    raise OSError("Setter prewrite boundary is unavailable for restore.")
                report["setter_prewrite_monotonic"] = setter_started
                restore_deadline = (
                    report["hold_maximum_restore_start_monotonic"]
                    if direction_evidence is not None
                    and "hold_maximum_restore_start_monotonic" in report
                    else setter_started + DWELL_SECONDS)
                report["restore_prewrite_deadline_monotonic"] = restore_deadline
                if (getattr(transport, "direction_hold", False)
                        and not report.get(
                            "restore_identity_validated_after_setter", False)):
                    try:
                        transport.identity(
                            deadline=min(active_deadline, restore_deadline))
                        report["restore_identity_validated_after_setter"] = True
                    except BaseException as error:
                        report["restore_identity_validation_error"] = (
                            f"{type(error).__name__}: {error}"[:1024])
                        final_errors.append(("restore_identity", error))
                _submit(transport, report, "restore", deadline=restore_deadline)
                report["restore_prewrite_monotonic"] = transport.last_write_started
                if getattr(transport, "direction_hold", False):
                    hold_start = report.get("hold_start_monotonic")
                    if type(hold_start) in (int, float):
                        report["actual_hold_seconds"] = (
                            transport.last_write_started - hold_start)
                        report["hold_within_maximum"] = (
                            transport.last_write_started <= restore_deadline)
                response_deadline = min(
                    overall_deadline,
                    transport.last_write_started + RESPONSE_SECONDS)
                seen = _set_restore_responses(
                    transport, report, deadline=response_deadline,
                    evidence=direction_evidence, clock=clock)
                transport.restore_correlated = seen["restore"] == 1
                if seen["set"] != 1:
                    final_errors.append((
                        "set_response",
                        OSError("No single clean correlated setter response."),
                    ))
                if not transport.restore_correlated:
                    raise OSError("No single clean correlated restore response.")
            except BaseException as error:
                final_errors.append(("restore", error))
            finally:
                if transport.restore_attempted:
                    report["setter_to_restore_start_seconds"] = (
                        transport.last_write_started - setter_started)
                    report["restore_started_within_bound"] = (
                        transport.restore_started_within_bound)
                    if not transport.restore_started_within_bound:
                        final_errors.append((
                            "restore_bound",
                            OSError(
                                "Restore started after its fixed profile deadline "
                                f"{restore_deadline:.9f}."),
                        ))
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
                   word1_hypothesis=False, word0_five_degree=False,
                   word1_five_degree=False, word0_direction=False,
                   **acknowledgments):
    profile = _profile(
        word1_hypothesis, word0_five_degree, word1_five_degree,
        word0_direction)
    required = profile["acknowledgments"]
    if run is not True or set(acknowledgments) != set(required):
        raise ValueError("Literal --run and the complete fixed front-servo scope are required.")
    if any(acknowledgments[name] is not True for name in required):
        raise ValueError("Every separate front-servo acknowledgment must be literal true.")
    return zero._run_diagnostic(
        output,
        expected_physical_port=expected_physical_port,
        review=prepare(
            word1_hypothesis=word1_hypothesis,
            word0_five_degree=word0_five_degree,
            word1_five_degree=word1_five_degree,
            word0_direction=word0_direction),
        transport_type=(
            _Word0DirectionTransport if word0_direction
            else _Word1FiveDegreeTransport if word1_five_degree
            else _Word0FiveDegreeTransport if word0_five_degree
            else _Word1Transport if word1_hypothesis else _Transport),
        observe=_observe,
        limits=zero._Limits(
            first_sequence=profile["first_sequence"], max_requests=4, interval=0),
        session_options={
            "_front_servo_mapper": True,
            "_front_servo_word1_mapper": word1_hypothesis,
            "_front_servo_word0_five_degree_mapper": word0_five_degree,
            "_front_servo_word1_five_degree_mapper": word1_five_degree,
            "_front_servo_word0_direction_mapper": word0_direction,
        },
        declarations={"operator_declarations": dict(acknowledgments)},
        expected_tx=lambda report: report["accepted_tx_bytes"],
        success_status=profile["success"],
        report_key="front_servo_mapping",
        authorizations={
            ("single_legacy_1e_front_camera_word1_five_degree_authorized"
             if word1_five_degree
             else "single_legacy_1e_front_camera_word0_direction_authorized"
             if word0_direction
             else "single_legacy_1e_front_camera_word0_five_degree_authorized"
             if word0_five_degree
             else "single_legacy_1e_front_camera_word1_hypothesis_authorized"
             if word1_hypothesis
             else "single_legacy_1e_front_camera_mapping_authorized"): True},
        serial_seconds=OVERALL_SECONDS,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--word1-front-camera-hypothesis", action="store_true")
    modes.add_argument("--word0-five-degree-diagnostic", action="store_true")
    modes.add_argument("--word1-five-degree-diagnostic", action="store_true")
    modes.add_argument("--word0-direction-diagnostic", action="store_true")
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    all_acknowledgments = tuple(dict.fromkeys(
        (*ACKNOWLEDGMENTS, *WORD1_ACKNOWLEDGMENTS,
         *WORD0_FIVE_DEGREE_ACKNOWLEDGMENTS,
         *WORD1_FIVE_DEGREE_ACKNOWLEDGMENTS,
         *DIRECTION_ACKNOWLEDGMENTS)))
    for name in all_acknowledgments:
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")
    args = parser.parse_args(argv)
    profile = _profile(
        args.word1_front_camera_hypothesis, args.word0_five_degree_diagnostic,
        args.word1_five_degree_diagnostic, args.word0_direction_diagnostic)
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
                word1_hypothesis=args.word1_front_camera_hypothesis,
                word0_five_degree=args.word0_five_degree_diagnostic,
                word1_five_degree=args.word1_five_degree_diagnostic,
                word0_direction=args.word0_direction_diagnostic)
        else:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_diagnostic(
                args.output,
                expected_physical_port=args.expected_physical_port,
                run=True,
                word1_hypothesis=args.word1_front_camera_hypothesis,
                word0_five_degree=args.word0_five_degree_diagnostic,
                word1_five_degree=args.word1_five_degree_diagnostic,
                word0_direction=args.word0_direction_diagnostic,
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
