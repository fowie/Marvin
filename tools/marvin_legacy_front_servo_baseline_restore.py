"""One fixed legacy baseline SetServoPosition request; offline by default."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

from tools import marvin_legacy_front_servo_getter as one_shot
from tools import marvin_legacy_zero as zero
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet, encode_request


SEQUENCE = 3517
COMMAND = 0x1E
WORDS = (2500, 2730)
REQUEST = encode_request(SEQUENCE, COMMAND, struct.pack("<HH", *WORDS))
TRANSCRIPT = (REQUEST,)
SUCCESS = "servo_baseline_single_restore_complete_protocol_only"
ACKNOWLEDGMENTS = (
    "operator_present",
    "robot_secured",
    "independent_actuator_cutoff_ready",
    "drive_and_other_actuators_inactive",
    "front_camera_ax12_plus_connected_at_j24",
    "projector_servo_physically_disconnected",
    "battery_only_passive_scope_on_verified_green_black_via_spare_cable",
    "operator_confirmed_baseline_restore_clearance",
    "host_usb_connected",
    "unprivileged_usbmon",
    "authorize_exactly_one_legacy_1e_baseline_2500_2730_restore_setter",
    "authorize_immediate_physical_power_off_after_baseline_restore",
)


def prepare():
    packet = decode_packet(REQUEST)
    if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
            SEQUENCE, COMMAND, 0, struct.pack("<HH", *WORDS)):
        raise ValueError("Fixed baseline restore disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "live_execution_authorized": False,
        "name": "single_legacy_servo_baseline_restore",
        "profile": "marvin-legacy-se",
        "usb_identity": "045e:4444",
        "sequence": SEQUENCE,
        "command": COMMAND,
        "baseline_words_uint16_le": list(WORDS),
        "immutable_application_transcript_hex": [REQUEST.hex()],
        "transcript_sha256": hashlib.sha256(REQUEST).hexdigest(),
        "maximum_application_bytes": len(REQUEST),
        "maximum_writes": 1,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "follow_up_command": False,
        "response_seconds": one_shot.RESPONSE_SECONDS,
        "overall_seconds": one_shot.OVERALL_SECONDS,
        "cleanup_seconds": one_shot.CLEANUP_SECONDS,
        "response_policy": (
            "exactly one CRC-valid sequence/cmd-correlated frame with empty payload; "
            "preserve raw response field; USB completion and response field are not ACK"
        ),
        "physical_restoration": "unproved",
        "observed_ax_interpretation": (
            "Passive J24 capture observed ID2 Goal Position 833 for this fixed "
            "baseline; actuator acceptance, execution and physical position remain unproved"
        ),
        "required": [
            "--run", "--expected-physical-port REVIEWED-PORT", "--output NEWDIR",
            *("--" + name.replace("_", "-") for name in ACKNOWLEDGMENTS),
        ],
        "refused": [
            "preliminary_getter", "arbitrary_sequence_value_or_dwell",
            "delta_or_follow_up_setter", "retry_reconnect_or_follow_up",
        ],
    }


class _Transport(LiveTransport):
    def write(self, data, *, deadline):
        return self._submit_once(data, deadline=deadline)

    def _submit_once(self, data, *, deadline):
        if type(data) is not bytes or self.writes != 0 or data != REQUEST:
            raise OSError("Only the single fixed [2500,2730] restore setter is permitted.")
        return super()._submit_once(data, deadline=deadline)


def _response_evidence(record):
    return zero._ResponseEvidence(
        record,
        sequence=SEQUENCE,
        command=COMMAND,
        accepted_response_fields=tuple(range(256)),
        validate_packet=lambda packet: (
            [] if packet.payload == b"" else ["unexpected_baseline_restore_payload"]),
    )


def _observe(transport, report, *, clock=one_shot.time.monotonic):
    return one_shot._observe_fixed(
        transport, report,
        request=REQUEST,
        sequence=SEQUENCE,
        response=_response_evidence(transport.event),
        success=SUCCESS,
        result_fields=lambda _packet: {},
        noun="baseline restore",
        clock=clock,
    )


def run_restore(output, *, expected_physical_port, run=False, **acknowledgments):
    if run is not True or set(acknowledgments) != set(ACKNOWLEDGMENTS):
        raise ValueError("Literal --run and the complete fixed restore scope are required.")
    if any(acknowledgments[name] is not True for name in ACKNOWLEDGMENTS):
        raise ValueError("Every separate restore acknowledgment must be literal true.")
    return zero._run_diagnostic(
        output,
        expected_physical_port=expected_physical_port,
        review=prepare(),
        transport_type=_Transport,
        observe=_observe,
        limits=zero._Limits(first_sequence=SEQUENCE, max_requests=1, interval=0),
        session_options={"_front_servo_baseline_restore": True},
        declarations={"operator_declarations": dict(acknowledgments)},
        expected_tx=len(REQUEST),
        success_status=SUCCESS,
        report_key="servo_baseline_single_restore",
        authorizations={"single_legacy_1e_baseline_restore_authorized": True},
        serial_seconds=one_shot.OVERALL_SECONDS,
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
            result = run_restore(
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
