"""One fixed legacy front-camera GetServoPosition request; offline by default.

No setter, arbitrary sequence/value, retry, reconnect, or follow-up exists.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet, get_servo_position_request
from tools import marvin_legacy_zero as zero


SEQUENCE = 3516
COMMAND = 0x1D
REQUEST = get_servo_position_request(SEQUENCE)
TRANSCRIPT = (REQUEST,)
RESPONSE_SECONDS = 0.5
ACTIVE_SECONDS = 5
CLEANUP_SECONDS = 2
OVERALL_SECONDS = ACTIVE_SECONDS + CLEANUP_SECONDS
SUCCESS = "front_camera_servo_single_getter_complete_protocol_only"
ACKNOWLEDGMENTS = (
    "operator_present",
    "robot_secured",
    "independent_actuator_cutoff_ready",
    "drive_and_other_actuators_inactive",
    "front_camera_ax12_plus_connected_at_j24",
    "projector_servo_physically_disconnected",
    "battery_only_passive_scope_on_verified_green_black_via_spare_cable",
    "host_usb_connected",
    "unprivileged_usbmon",
    "authorize_one_read_only_legacy_1d_getter_only",
    "authorize_immediate_physical_power_off_after_getter",
)


def prepare():
    packet = decode_packet(REQUEST)
    if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
            SEQUENCE, COMMAND, 0, b""):
        raise ValueError("Fixed front-servo getter disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "live_execution_authorized": False,
        "name": "front_camera_ax12_plus_single_get_servo_position",
        "profile": "marvin-legacy-se",
        "usb_identity": "045e:4444",
        "sequence": SEQUENCE,
        "command": COMMAND,
        "request_payload_bytes": 0,
        "immutable_application_transcript_hex": [REQUEST.hex()],
        "transcript_sha256": hashlib.sha256(REQUEST).hexdigest(),
        "maximum_application_bytes": len(REQUEST),
        "maximum_writes": 1,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "follow_up_command": False,
        "response_seconds": RESPONSE_SECONDS,
        "overall_seconds": OVERALL_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "response_policy": (
            "exactly one CRC-valid sequence/cmd-correlated frame with exactly "
            "4 payload bytes; preserve raw response field and two LE16 words; "
            "USB completion and response field are not application ACK"
        ),
        "required": [
            "--run", "--expected-physical-port REVIEWED-PORT", "--output NEWDIR",
            *("--" + name.replace("_", "-") for name in ACKNOWLEDGMENTS),
        ],
        "refused": [
            "legacy_1e_or_any_setter", "arbitrary_sequence_or_value",
            "retry_reconnect_or_follow_up", "successor_commands",
        ],
    }


class _Transport(LiveTransport):
    def write(self, data, *, deadline):
        return self._submit_once(data, deadline=deadline)

    def _submit_once(self, data, *, deadline):
        if type(data) is not bytes or self.writes != 0 or data != REQUEST:
            raise OSError("Only the single fixed empty legacy 0x1D request is permitted.")
        return super()._submit_once(data, deadline=deadline)


def _response_evidence(record):
    return zero._ResponseEvidence(
        record,
        sequence=SEQUENCE,
        command=COMMAND,
        accepted_response_fields=tuple(range(256)),
        validate_packet=lambda packet: (
            [] if len(packet.payload) == 4 else ["unexpected_servo_getter_payload_size"]),
    )


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + OVERALL_SECONDS
    active_deadline = deadline - CLEANUP_SECONDS
    response = _response_evidence(transport.event)
    primary = None
    report.update(
        status="not_started",
        accepted_tx_bytes=0,
        uncertain_tx_bytes=0,
        operational_deadline_monotonic=deadline,
        write_status="not_attempted",
        application_acknowledgment="not_established",
    )
    try:
        if transport.revalidate(deadline=active_deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        report.update(write_status="attempted", uncertain_tx_bytes=len(REQUEST))
        count = transport.write(REQUEST, deadline=active_deadline)
        if type(count) is not int or not 0 <= count <= len(REQUEST):
            raise OSError("Unknown getter write result; no retry.")
        report.update(accepted_tx_bytes=count, uncertain_tx_bytes=len(REQUEST) - count)
        if count != len(REQUEST):
            raise OSError("Partial getter write; no retry or suffix resend.")
        observed_at = clock()
        if (transport.last_write_sequence != SEQUENCE or
                type(transport.last_write_started) not in (int, float) or
                transport.last_write_started > observed_at):
            raise OSError("Getter write boundary evidence is inconsistent.")
        response.submitted_at = transport.last_write_started
        response.deadline = min(active_deadline, response.submitted_at + RESPONSE_SECONDS)
        if response.deadline != response.submitted_at + RESPONSE_SECONDS:
            raise OSError("Insufficient deadline for the complete response window.")
        report.update(
            write_status="fully_accepted_not_acknowledged",
            submitted_at=response.submitted_at,
            response_deadline=response.deadline,
        )
        zero._observe_response(
            transport, response, deadline=response.deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(response.events[0]["stream"]["raw_hex"]))
        report.update(
            status=SUCCESS,
            sequence=packet.sequence,
            command=packet.command,
            raw_response_field_uint8=packet.response_field,
            raw_payload_hex=packet.payload.hex(),
            words_uint16_le=[
                int.from_bytes(packet.payload[0:2], "little"),
                int.from_bytes(packet.payload[2:4], "little"),
            ],
            response_events=response.events,
            response_classification="correlated_shape_only_not_ACK",
        )
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        errors = []
        try:
            response.finish(clock())
        except (OSError, ValueError) as error:
            errors.append(error)
        cleanup_deadline = min(deadline, clock() + CLEANUP_SECONDS)
        report["cleanup_deadline_monotonic"] = cleanup_deadline
        try:
            transport.close(deadline=cleanup_deadline)
        except (OSError, ValueError) as error:
            errors.append(error)
        report.update(
            serial_rx_bytes=transport.serial_bytes,
            application_submission_attempts=transport.writes,
            correlated_candidates=response.candidates,
            response_events=response.events,
            cleanup_errors=[str(error)[:1024] for error in errors],
            application_acknowledgment="not_established",
        )
        if transport.writes == 0:
            report.update(
                write_status="suppressed_before_submission",
                accepted_tx_bytes=0,
                uncertain_tx_bytes=0,
            )
        if errors:
            report["status"] = "failed"
            if primary is None:
                raise errors[0]
            for error in errors:
                primary.add_note(f"Additional getter finalization error: {error}")


def run_getter(output, *, expected_physical_port, run=False, **acknowledgments):
    if run is not True or set(acknowledgments) != set(ACKNOWLEDGMENTS):
        raise ValueError("Literal --run and the complete fixed getter scope are required.")
    if any(acknowledgments[name] is not True for name in ACKNOWLEDGMENTS):
        raise ValueError("Every separate getter acknowledgment must be literal true.")
    return zero._run_diagnostic(
        output,
        expected_physical_port=expected_physical_port,
        review=prepare(),
        transport_type=_Transport,
        observe=_observe,
        limits=zero._Limits(first_sequence=SEQUENCE, max_requests=1, interval=0),
        session_options={"_front_servo_getter": True},
        declarations={"operator_declarations": dict(acknowledgments)},
        expected_tx=len(REQUEST),
        success_status=SUCCESS,
        report_key="front_servo_single_getter",
        authorizations={"single_read_only_legacy_1d_getter_authorized": True},
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
            if any(acknowledgments.values()):
                raise ValueError("Acknowledgments are accepted only with literal --run.")
            result = prepare()
        else:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_getter(
                args.output,
                expected_physical_port=args.expected_physical_port,
                run=True,
                **acknowledgments,
            )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
