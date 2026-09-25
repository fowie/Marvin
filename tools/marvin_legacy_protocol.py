"""Offline original-Marvin S/E framing; no transport or successor semantics.

Wire facts: 53 + sequence LE16 + command U8 + response U8 + length LE16,
opaque payload, CRC16 LE (A001 reflected, init0 over header and payload), 45.
The old PCTestApp/SerialPacket.cs describes this framing; the 2026-09-14
GetConfig capture corroborates an empty command4 request and a status80,
108-byte response. Protocol facts only, not copied recovered implementation.

Twelve source-backed empty PCTestApp getter requests can be generated. Five
remain admitted to the persistent client; the other seven remain fixed offline
diagnostic generators and do not broaden its live allowlist. Other valid command/status/size
combinations decode without interpretation. Old00 is not successor ReadRawData3;
command maps must not be mixed. Firmware/communication version
words in configuration are returned data, not verified running-firmware identity.
Caller-declared direction/evidence and even a matching reply shape authenticate
nothing. This module never opens devices, capture files or source files.
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import sys
from types import MappingProxyType
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.marvin_protocol import crc16


HEADER = b"\x53"
FOOTER = b"\x45"
HEADER_BYTES = 7
FRAME_OVERHEAD = 10
WIRE_MAX_PAYLOAD_BYTES = 65535
GET_CONFIG = 4
GET_UNIT_INFO = 0x1B
GET_POWER_STATE = 0x0E
GET_LOG = 0x0C
READ_RAW_DATA = 0x00
GET_RAW_MOTOR_PWM = 0x0A
GET_MOTOR_VELOCITY = 0x10
GET_LED_STATE = 0x17
GET_LED_BLINK = 0x19
GET_SERVO_POSITION = 0x1D
GET_SENSOR_INFO = 0x1F
GET_BATTERY_INFO = 0x28
SET_PROJECTOR_POWER = 0x27
GET_CONFIG_PAYLOAD_BYTES = 108
GETTER_RESPONSE_FIELD = 0x80
GET_CONFIG_RESPONSE_FIELD = GETTER_RESPONSE_FIELD
DIRECTIONS = ("unknown", "received", "outgoing")
EVIDENCE_KINDS = ("unspecified", "recorded", "synthetic")


def _sequence(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("Sequence must be an integer fitting uint16.")
    return value


def _empty_request(command, sequence):
    if type(command) is not int or command not in (
            GET_CONFIG, GET_LOG, GET_UNIT_INFO, GET_POWER_STATE, READ_RAW_DATA,
            GET_RAW_MOTOR_PWM, GET_MOTOR_VELOCITY, GET_LED_STATE, GET_LED_BLINK,
            GET_SERVO_POSITION, GET_SENSOR_INFO, GET_BATTERY_INFO):
        raise ValueError("Only the twelve source-backed legacy getters are allowed.")
    return encode_request(sequence, command)


def encode_request(sequence, command, payload=b""):
    """Encode one offline legacy request; this function has no transport."""
    _sequence(sequence)
    if type(command) is not int or not 0 <= command <= 255:
        raise ValueError("Command must be an integer fitting uint8.")
    if type(payload) is not bytes or len(payload) > WIRE_MAX_PAYLOAD_BYTES:
        raise ValueError("Payload must be immutable bytes fitting uint16.")
    body = HEADER + struct.pack("<HBBH", sequence, command, 0, len(payload)) + payload
    return body + struct.pack("<H", crc16(body)) + FOOTER


def get_config_request(sequence=0):
    """Generate one audited empty legacy GetConfig request, without sending it."""
    return _empty_request(GET_CONFIG, sequence)


def get_unit_info_request(sequence=0):
    """PCTestApp Form1.cs607-611 and a correlated 2026-09-14 reply support empty1B."""
    return _empty_request(GET_UNIT_INFO, sequence)


def get_power_state_request(sequence=0):
    """PCTestApp Form1.cs937-940 and a correlated 2026-09-14 reply support empty0E."""
    return _empty_request(GET_POWER_STATE, sequence)


def get_log_request(sequence=0):
    """PCTestApp getLog_btn and two correlated installed replies support empty0C."""
    return _empty_request(GET_LOG, sequence)


def read_raw_data_request(sequence=0):
    """PCTestApp Form1.cs931-934 and a correlated 2026-09-14 reply support empty00."""
    return _empty_request(READ_RAW_DATA, sequence)


def get_raw_motor_pwm_request(sequence=0):
    """PCTestApp Form1.cs sends an empty legacy 0A; installed reply shape unknown."""
    return _empty_request(GET_RAW_MOTOR_PWM, sequence)


def get_motor_velocity_request(sequence=0):
    """PCTestApp Form1.cs sends an empty legacy 10; installed reply shape unknown."""
    return _empty_request(GET_MOTOR_VELOCITY, sequence)


def get_led_state_request(sequence=0):
    """PCTestApp Form1.cs sends an empty legacy 17; installed reply shape unknown."""
    return _empty_request(GET_LED_STATE, sequence)


def get_led_blink_request(sequence=0):
    """PCTestApp Form1.cs sends an empty legacy 19; installed reply shape unknown."""
    return _empty_request(GET_LED_BLINK, sequence)


def get_servo_position_request(sequence=0):
    """PCTestApp sends empty legacy1D; a correlated installed reply contained 4 bytes."""
    return _empty_request(GET_SERVO_POSITION, sequence)


def get_sensor_info_request(sequence=0):
    """PCTestApp Form1.cs sends an empty legacy 1F; installed reply shape unknown."""
    return _empty_request(GET_SENSOR_INFO, sequence)


def get_battery_info_request(sequence=0):
    """PCTestApp Form1.cs sends an empty legacy 28; installed reply shape unknown."""
    return _empty_request(GET_BATTERY_INFO, sequence)


def projector_power_request(sequence, enabled):
    """Build the source-defined legacy projector power setter without sending it."""
    if type(enabled) is not bool:
        raise ValueError("Projector power must be a literal boolean.")
    return encode_request(sequence, SET_PROJECTOR_POWER, bytes((int(enabled),)))


@dataclass(frozen=True)
class GetterSpec:
    command: int
    payload_bytes: int
    encode: Callable[[int], bytes]


# Reviewed reply shapes; these facts do not broaden the one-shot live policy.
GETTERS = MappingProxyType({
    "get-config": GetterSpec(GET_CONFIG, GET_CONFIG_PAYLOAD_BYTES, get_config_request),
    "get-log": GetterSpec(GET_LOG, 32, get_log_request),
    "get-unit-info": GetterSpec(GET_UNIT_INFO, 12, get_unit_info_request),
    "get-power-state": GetterSpec(GET_POWER_STATE, 2, get_power_state_request),
    "read-raw-data": GetterSpec(READ_RAW_DATA, 134, read_raw_data_request),
})


@dataclass(frozen=True)
class LegacyPacket:
    sequence: int
    command: int
    response_field: int
    payload: bytes
    raw: bytes

    @property
    def declared_payload_bytes(self):
        return int.from_bytes(self.raw[5:7], "little")

    @property
    def crc16_value(self):
        return int.from_bytes(self.raw[-3:-1], "little")

    def to_dict(self):
        return {
            "protocol": "marvin-legacy-se", "sequence": self.sequence,
            "command": self.command, "response_field": self.response_field,
            "declared_payload_bytes": self.declared_payload_bytes,
            "payload_bytes": len(self.payload), "payload_hex": self.payload.hex(),
            "crc16_value": self.crc16_value, "raw_hex": self.raw.hex(),
        }


def decode_packet(data):
    """Validate exactly one legacy frame; preserve every command/status verbatim."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("decode_packet expects bytes or bytearray.")
    if not FRAME_OVERHEAD <= len(data) <= WIRE_MAX_PAYLOAD_BYTES + FRAME_OVERHEAD:
        raise ValueError("Legacy packet size is outside the 10..65545-byte wire range.")
    if data[:1] != HEADER:
        raise ValueError("Invalid legacy header; expected single byte 53.")
    sequence, command, response, length = struct.unpack_from("<HBBH", data, 1)
    if len(data) != length + FRAME_OVERHEAD:
        raise ValueError("Actual legacy packet size does not match declared payload length.")
    if data[-1:] != FOOTER:
        raise ValueError("Invalid legacy footer; expected single byte 45.")
    if crc16(data[:-3]) != struct.unpack_from("<H", data, len(data) - 3)[0]:
        raise ValueError("Legacy packet CRC does not match.")
    raw = bytes(data)
    return LegacyPacket(sequence, command, response, raw[HEADER_BYTES:-3], raw)


def validate_getter_reply(packet, query, sequence=0):
    """Validate a reviewed getter's raw consistency, correlation and exact shape."""
    if not isinstance(query, str) or query not in GETTERS:
        raise ValueError("Select one of the five reviewed legacy getter names.")
    spec = GETTERS[query]
    _sequence(sequence)
    if not isinstance(packet, LegacyPacket):
        raise TypeError("Expected a LegacyPacket from decode_packet.")
    if (type(packet.raw) is not bytes or type(packet.payload) is not bytes
            or any(type(value) is not int for value in (packet.sequence, packet.command, packet.response_field))):
        raise ValueError("Legacy packet requires immutable bytes and integer wire fields.")
    if decode_packet(packet.raw) != packet:
        raise ValueError("Legacy packet fields do not match its raw frame.")
    if packet.sequence != sequence:
        raise ValueError(f"{query} reply sequence does not match the request.")
    if packet.command != spec.command:
        raise ValueError(f"Expected legacy {query} command{spec.command}.")
    if packet.response_field != GETTER_RESPONSE_FIELD:
        raise ValueError(f"{query} reply requires response field80; request echoes are not replies.")
    if len(packet.payload) != spec.payload_bytes:
        raise ValueError(f"Legacy {query} reply requires the reviewed {spec.payload_bytes}-byte payload layout.")
    return packet


def validate_get_config_reply(packet, sequence=0):
    """Match command4/status80/108 bytes, not authentication or a hardware ACK."""
    return validate_getter_reply(packet, "get-config", sequence)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    encoders = {
        "get-config": get_config_request, "get-log": get_log_request,
        "get-unit-info": get_unit_info_request,
        "get-power-state": get_power_state_request, "read-raw-data": read_raw_data_request,
        "get-servo-position": get_servo_position_request,
    }
    generate = actions.add_parser("generate", help="Print one audited offline getter request as hex only")
    generate.add_argument("--command", choices=tuple(encoders), default="get-config")
    generate.add_argument("--sequence", type=int, default=0)
    inspect = actions.add_parser("inspect", help="Inspect one complete legacy frame")
    inspect.add_argument("hex_packet")
    inspect.add_argument("--direction", choices=DIRECTIONS, default="unknown")
    inspect.add_argument("--evidence", choices=EVIDENCE_KINDS, default="unspecified")
    inspect.add_argument("--expect-config-sequence", type=int)
    args = parser.parse_args(argv)
    try:
        if args.action == "generate":
            print(encoders[args.command](args.sequence).hex(" "))
            return 0
        packet = decode_packet(bytes.fromhex(args.hex_packet))
        result = {
            "schema_version": 1, "status": "decoded", "offline_only": True,
            "direction": args.direction, "evidence_kind": args.evidence,
            "provenance": "Caller-declared direction and evidence kind; not authenticated.",
            "packet": packet.to_dict(), "application_acknowledgment": "not_established",
            "payload_interpretation": "opaque; no successor schema or runtime identity inferred",
        }
        if args.expect_config_sequence is not None:
            if args.direction != "received":
                raise ValueError("Reply matching requires an explicit received direction declaration.")
            validate_get_config_reply(packet, args.expect_config_sequence)
            result["get_config_match"] = {
                "sequence": args.expect_config_sequence,
                "status": "integrity_and_shape_match",
                "meaning": "Caller-expected sequence and reply shape only; not authentication.",
            }
    except (ValueError, TypeError) as error:
        print(json.dumps({"status": "input_error", "offline_only": True, "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
