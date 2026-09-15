"""Offline, exact-shape transmission allowlists; never infer a firmware profile."""

import re

from tools import marvin_legacy_protocol as legacy
from tools import marvin_protocol as modern


PROFILES = ("modern", "legacy", "experimental-successor")
_MODERN_PAYLOADS = {4: (b"",), 27: (b"",), 29: (b"",)}
_EXPERIMENTAL_PAYLOADS = {
    **_MODERN_PAYLOADS, 3: (b"",), 38: (b"",), 46: (b"\x00", b"\x01"),
}
_LEGACY_COMMANDS = frozenset((legacy.GET_CONFIG, legacy.GET_UNIT_INFO,
                              legacy.GET_POWER_STATE, legacy.READ_RAW_DATA))
_TEXT_BODIES = frozenset((
    b"", b"?", b"help", b"h", b"VER", b"HWVER", b"ADC", b"READ", b"HELP",
    b"ver", b"version", b"VERSION", b"info", b"INFO", b"status", b"STATUS",
))
# These four historical experiments are allowed only as a complete, isolated
# experimental transcript, never as a prefix that could hide another command.
_MALFORMED_EXPERIMENTS = frozenset(bytes.fromhex(value) for value in (
    "efbe0000040000001033adde", "efbe0000040000001133addf",
    "beef0000040000009506adde", "efbe00000400010000a2ccadde",
))


def validate_profile(profile):
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError("Select an explicit modern, legacy or experimental-successor transmit profile.")


def validate_transmit_stream(data, *, profile="modern"):
    """Validate every byte of a bounded transcript; return whether it is stateful."""
    validate_profile(profile)
    if not isinstance(data, bytes) or not 1 <= len(data) <= 4096:
        raise ValueError("A transmit transcript must contain 1 to 4096 bytes.")
    if profile == "modern" and data == b"\r":
        return False
    if profile == "experimental-successor":
        if data in _MALFORMED_EXPERIMENTS:
            return False
        if all(byte < 128 for byte in data):
            position = 0
            while position < len(data):
                line = re.match(rb"([^\r\n]*)(?:\r\n|\r|\n)", data[position:])
                if line is None or line[1] not in _TEXT_BODIES:
                    raise ValueError("Text transmission must use exact named queries and terminators.")
                position += line.end()
            return False
    position = 0
    stateful = False
    while position < len(data):
        remaining = data[position:]
        if profile == "legacy":
            if len(remaining) < legacy.FRAME_OVERHEAD or remaining[:1] != legacy.HEADER:
                raise ValueError("Only complete legacy getter frames are allowed.")
            size = legacy.FRAME_OVERHEAD + int.from_bytes(remaining[5:7], "little")
            packet = legacy.decode_packet(remaining[:size])
            if packet.command not in _LEGACY_COMMANDS or packet.response_field != 0 or packet.payload:
                raise ValueError("Only exact empty legacy getter requests are allowed.")
            stateful |= packet.command == legacy.GET_UNIT_INFO
        else:
            if len(remaining) < 12 or remaining[:2] != modern.HEADER:
                raise ValueError("Only complete modern getter frames are allowed.")
            size = 12 + int.from_bytes(remaining[6:8], "little")
            packet = modern.decode_packet(remaining[:size])
            allowed = _EXPERIMENTAL_PAYLOADS if profile == "experimental-successor" else _MODERN_PAYLOADS
            if packet.response_field != 0 or packet.payload not in allowed.get(packet.command, ()):
                raise ValueError("Only exact audited modern getter request shapes are allowed.")
            stateful |= packet.command in (modern.GET_UNIT_INFO, modern.GET_SENSOR_INFO)
        position += size
    return stateful
