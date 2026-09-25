"""Fixed legacy projector-power smoke test; offline by default."""

import hashlib

from tools.marvin_legacy_protocol import decode_packet, projector_power_request


FIRST_SEQUENCE = 3600
HOLD_SECONDS = 10.0
MAX_HOLD_OVERRUN_SECONDS = 0.01
SERIAL_SECONDS = 15
CLEANUP_SECONDS = 5
RESPONSE_SECONDS = 0.5
STEPS = {
    "on": projector_power_request(FIRST_SEQUENCE, True),
    "off": projector_power_request(FIRST_SEQUENCE + 1, False),
}
ACKNOWLEDGMENTS = (
    "operator_present",
    "robot_stationary_and_projector_output_clear",
    "independent_power_cutoff_ready",
    "drive_and_servo_movement_inactive",
    "projector_connected",
    "host_usb_connected",
    "unprivileged_usbmon",
    "authorize_exact_legacy_27_on_10_seconds_off",
)


def prepare():
    packets = [decode_packet(STEPS[step]) for step in ("on", "off")]
    if [(packet.sequence, packet.command, packet.payload) for packet in packets] != [
            (FIRST_SEQUENCE, 0x27, b"\x01"),
            (FIRST_SEQUENCE + 1, 0x27, b"\x00")]:
        raise ValueError("Fixed projector-power transcript differs from command 0x27.")
    transcript = [STEPS["on"], STEPS["off"]]
    return {
        "status": "dry_run",
        "live_execution_authorized": False,
        "name": "projector_power_on_10_seconds_off",
        "profile": "marvin-legacy-se",
        "command": 0x27,
        "on_payload_uint8": 1,
        "off_payload_uint8": 0,
        "powered_observation_seconds": HOLD_SECONDS,
        "maximum_hold_overrun_seconds": MAX_HOLD_OVERRUN_SECONDS,
        "immutable_application_transcript_hex": [raw.hex() for raw in transcript],
        "transcript_sha256": hashlib.sha256(b"".join(transcript)).hexdigest(),
        "maximum_writes": 2,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "response_policy": (
            "retain one CRC-valid sequence/command-correlated empty response per "
            "write and its raw response field; never treat it as an application ACK"
        ),
        "cleanup_policy": (
            "after ON may reach the syscall, attempt exactly one OFF in finally "
            "after fresh identity validation, including Ctrl-C and failures"
        ),
        "physical_power_state": "not_established",
        "required": [
            "--run", "--expected-physical-port PORT", "--output NEWDIR",
            *("--" + name.replace("_", "-") for name in ACKNOWLEDGMENTS),
        ],
        "refused": [
            "generic_0f_power_mask", "servo_movement", "arbitrary_payload_or_hold",
            "retry_reconnect_or_persistent_on",
        ],
    }


def run_smoke(output, *, expected_physical_port, run=False, **acknowledgments):
    raise ValueError(
        "Live projector power is permanently disabled: command 0x27 came from "
        "a mismatched source map. Only prepare() offline evidence is retained.")
