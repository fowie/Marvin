"""Offline Marvin packet encoding/decoding from recovered host/firmware evidence.

No transport access or startup sequence is provided. Only documented config,
unit-info, and sensor-info queries can be generated. The latter two can enable
telemetry together; sensor-info is not a live sensor measurement.
CLI action validation errors produce JSON input_error output and exit 2;
argument syntax/type errors retain argparse's standard diagnostics.
"""

import argparse
from dataclasses import dataclass
import json
import struct


HEADER = b"\xef\xbe"
FOOTER = b"\xad\xde"
GET_CONFIG = 4
GET_UNIT_INFO = 27
GET_SENSOR_INFO = 29
GET_CONFIG_PAYLOAD_BYTES = 108
GET_SENSOR_INFO_PAYLOAD_BYTES = 128


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _identification_request(command: int, sequence: int) -> bytes:
    if command not in (GET_CONFIG, GET_UNIT_INFO, GET_SENSOR_INFO):
        raise ValueError("Only documented config, unit-info, and sensor-info queries are supported.")
    if type(sequence) is not int or not 0 <= sequence <= 0xFFFF:
        raise ValueError("Sequence must be an integer fitting uint16.")
    body = HEADER + struct.pack("<HBBH", sequence, command, 0, 0)
    return body + struct.pack("<H", crc16(body)) + FOOTER


def get_config_request(sequence: int = 0) -> bytes:
    return _identification_request(GET_CONFIG, sequence)


def get_unit_info_request(sequence: int = 1) -> bytes:
    """Build the stateful alternative; this does not authorize its transmission."""
    return _identification_request(GET_UNIT_INFO, sequence)


def get_sensor_info_request(sequence: int = 2) -> bytes:
    """Build the second telemetry-handshake query, not a sensor-reading command."""
    return _identification_request(GET_SENSOR_INFO, sequence)


@dataclass(frozen=True)
class Packet:
    sequence: int
    command: int
    response_field: int
    payload: bytes

    def response_code(self) -> int:
        if self.response_field & 0x80:
            return self.response_field & 0x7F
        if len(self.payload) < 4:
            raise ValueError("Extended response code requires four payload bytes.")
        return struct.unpack_from("<I", self.payload)[0]

    def unit_info(self) -> dict:
        expected_size = {GET_CONFIG: GET_CONFIG_PAYLOAD_BYTES, 27: 12}.get(self.command)
        if expected_size is None or self.response_field != 0x80:
            raise ValueError("UnitInfo requires a successful identification response.")
        if len(self.payload) != expected_size:
            raise ValueError(f"Recovered command {self.command} layout requires {expected_size} payload bytes.")
        fw, comm, serial = struct.unpack_from("<III", self.payload)
        return {"fw_version": fw, "comm_version": comm, "serial_number": serial}


def decode_packet(data: bytes | bytearray | memoryview) -> Packet:
    """Validate one immutable snapshot; returned payload bytes cannot change."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("decode_packet expects bytes, bytearray, or a contiguous byte memoryview.")
    if isinstance(data, memoryview) and (data.ndim != 1 or data.itemsize != 1 or not data.c_contiguous):
        raise TypeError("decode_packet requires a contiguous one-dimensional byte memoryview.")
    if len(data) > 12 + 0xFFFF:
        raise ValueError("Actual packet size does not match its declared payload length.")
    data = bytes(data)
    if len(data) < 12 or data[:2] != HEADER:
        raise ValueError("Packet has a missing or invalid header.")
    sequence, command, response_field, length = struct.unpack_from("<HBBH", data, 2)
    if len(data) != 12 + length:
        raise ValueError("Actual packet size does not match its declared payload length.")
    if data[-2:] != FOOTER:
        raise ValueError("Invalid packet footer.")
    if crc16(data[:-4]) != struct.unpack_from("<H", data, len(data) - 4)[0]:
        raise ValueError("Packet CRC does not match.")
    return Packet(sequence, command, response_field, data[8:-4])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    generate = actions.add_parser("generate")
    generate.add_argument("--command", type=int, choices=(GET_CONFIG, GET_UNIT_INFO, GET_SENSOR_INFO), default=GET_CONFIG)
    generate.add_argument("--sequence", type=int, default=0)
    inspect = actions.add_parser("inspect")
    inspect.add_argument("hex_packet", help="One complete packet, not an unframed byte stream")
    inspect.add_argument("--response", action="store_true",
                         help="Interpret bytes known to have been received from the controller")
    args = parser.parse_args()
    try:
        if args.action == "generate":
            print(_identification_request(args.command, args.sequence).hex(" "))
            return 0
        packet = decode_packet(bytes.fromhex(args.hex_packet))
        result = {
            "sequence": packet.sequence,
            "command": packet.command,
            "response_field": packet.response_field,
            "payload_bytes": len(packet.payload),
            "payload_hex": packet.payload.hex(),
            "direction": "response" if args.response else "unspecified",
        }
        if args.response:
            result["response_code"] = packet.response_code()
            known_layout = (packet.command, len(packet.payload)) in ((GET_CONFIG, 108), (27, 12))
            if packet.response_field == 0x80 and known_layout:
                result["unit_info"] = packet.unit_info()
                result["metadata_tail_hex"] = packet.payload[12:].hex()
            elif packet.command in (GET_CONFIG, 27) and packet.response_field == 0x80:
                result["warning"] = "Unknown metadata layout; raw payload retained without field decoding."
    except (ValueError, TypeError) as error:
        print(json.dumps({"status": "input_error", "offline_only": True, "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
