"""Bounded, offline, length-aware framing for original-Marvin S/E streams.

Defaults: payload <=4096 bytes, retained buffer <=payload limit+10, total accepted
input <=16 MiB. Payload limits can reach the uint16 wire maximum; this is not a
claim of controller support. Returned event storage scales with accepted input.

Once an in-bound header is available, its entire declared frame is retained
until complete or EOF, even if payload contains S/E or complete valid frames.
A complete CRC/footer failure consumes that entire candidate as one error span:
no nested-payload recovery is attempted. An out-of-bound length rejects the S
byte and scans onward with an explicit ambiguity warning. Corrupt lengths can
hide or consume later genuine frames. Recovered boundaries are hypotheses, not
proof of sender intent. EOF never searches inside an incomplete candidate.

Events partition all accepted input after finish(), including noise and partial
tails. A feed exceeding the total-input limit is rejected before consuming any
of that feed. No direction, command semantics or acknowledgment is inferred.
"""

from dataclasses import dataclass
import struct

from tools import marvin_legacy_protocol as protocol


DEFAULT_MAX_PAYLOAD_BYTES = 4096
DEFAULT_MAX_INPUT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class LegacyStreamEvent:
    kind: str
    offset: int
    raw: bytes
    code: str
    message: str
    packet: protocol.LegacyPacket | None = None
    declared_payload_bytes: int | None = None
    needed_bytes: int | None = None
    candidate_preview_hex: str | None = None
    follows_corruption: bool = False

    @property
    def end_offset(self):
        return self.offset + len(self.raw)

    def to_dict(self):
        result = {
            "kind": self.kind, "code": self.code, "message": self.message,
            "offset": self.offset, "end_offset": self.end_offset,
            "raw_bytes": len(self.raw), "raw_hex": self.raw.hex(),
            "follows_corruption": self.follows_corruption,
        }
        for key in ("declared_payload_bytes", "needed_bytes", "candidate_preview_hex"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        if self.packet is not None:
            result["packet"] = self.packet.to_dict()
        return result


class LegacyStreamDecoder:
    def __init__(self, *, max_payload_bytes=DEFAULT_MAX_PAYLOAD_BYTES,
                 max_buffer_bytes=None, max_input_bytes=DEFAULT_MAX_INPUT_BYTES, start_offset=0):
        if type(max_payload_bytes) is not int or not 0 <= max_payload_bytes <= protocol.WIRE_MAX_PAYLOAD_BYTES:
            raise ValueError("max_payload_bytes must be an integer from 0 through 65535.")
        minimum = max_payload_bytes + protocol.FRAME_OVERHEAD
        if max_buffer_bytes is None:
            max_buffer_bytes = minimum
        if (type(max_buffer_bytes) is not int
                or not minimum <= max_buffer_bytes <= protocol.WIRE_MAX_PAYLOAD_BYTES + protocol.FRAME_OVERHEAD):
            raise ValueError("max_buffer_bytes must hold payload limit+10 and cannot exceed 65545.")
        for name, value in (("max_input_bytes", max_input_bytes), ("start_offset", start_offset)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer.")
        self.max_payload_bytes = max_payload_bytes
        self.max_buffer_bytes = max_buffer_bytes
        self.max_input_bytes = max_input_bytes
        self._offset = start_offset
        self._input_bytes = 0
        self._buffer = bytearray()
        self._finished = False
        self._corruption_seen = False

    @property
    def offset(self):
        return self._offset

    @property
    def buffered_bytes(self):
        return len(self._buffer)

    @property
    def input_bytes(self):
        return self._input_bytes

    def _take(self, size, kind, code, message, **details):
        event = LegacyStreamEvent(
            kind, self._offset, bytes(self._buffer[:size]), code, message,
            follows_corruption=self._corruption_seen, **details,
        )
        del self._buffer[:size]
        self._offset += size
        return event

    def _drain(self, *, final=False):
        events = []
        while self._buffer:
            if self._buffer[0] != protocol.HEADER[0]:
                next_header = self._buffer.find(protocol.HEADER)
                size = len(self._buffer) if next_header < 0 else next_header
                events.append(self._take(
                    size, "noise", "unframed_bytes",
                    "Bytes before the next S candidate; raw span retained, no direction inferred.",
                ))
                continue
            if len(self._buffer) < protocol.HEADER_BYTES:
                if final:
                    events.append(self._take(
                        len(self._buffer), "partial", "incomplete_header",
                        "EOF inside a possible seven-byte legacy header.",
                        needed_bytes=protocol.HEADER_BYTES - len(self._buffer),
                    ))
                break
            length = struct.unpack_from("<H", self._buffer, 5)[0]
            if length > self.max_payload_bytes:
                events.append(self._take(
                    1, "error", "payload_limit",
                    "Declared length exceeds the configured limit, not necessarily the wire format. "
                    "Rejecting only this S byte; later candidates may lie inside its alleged payload.",
                    declared_payload_bytes=length,
                    candidate_preview_hex=bytes(self._buffer[:protocol.HEADER_BYTES]).hex(),
                ))
                self._corruption_seen = True
                continue
            size = length + protocol.FRAME_OVERHEAD
            if len(self._buffer) < size:
                if final:
                    events.append(self._take(
                        len(self._buffer), "partial", "incomplete_frame",
                        "EOF before the declared end; embedded S/E or complete frames remain opaque. "
                        "No speculative suffix recovery.",
                        declared_payload_bytes=length, needed_bytes=size - len(self._buffer),
                    ))
                break
            candidate = bytes(self._buffer[:size])
            try:
                packet = protocol.decode_packet(candidate)
            except ValueError as error:
                events.append(self._take(
                    size, "error", "invalid_frame",
                    f"{error} Entire declared candidate retained; no nested-payload recovery. "
                    "A damaged length may have consumed later frame bytes.",
                    declared_payload_bytes=length,
                ))
                self._corruption_seen = True
                continue
            events.append(self._take(
                size, "frame", "validated_frame",
                "Legacy header, length, footer and CRC match; origin/direction/ACK are not established. "
                "After corruption, this boundary remains a resynchronization hypothesis.",
                packet=packet, declared_payload_bytes=length,
            ))
        return events

    def feed(self, data):
        if self._finished:
            raise ValueError("Cannot feed a finished legacy decoder.")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("feed expects bytes or bytearray.")
        if self._input_bytes + len(data) > self.max_input_bytes:
            raise ValueError("Legacy stream input limit exceeded; this feed was not consumed.")
        self._input_bytes += len(data)
        events = []
        position = 0
        while position < len(data):
            count = min(len(data) - position, self.max_buffer_bytes - len(self._buffer))
            if count <= 0:
                raise RuntimeError("Legacy decoder made no progress within its buffer bound.")
            self._buffer.extend(data[position:position + count])
            position += count
            events.extend(self._drain())
        return events

    def finish(self):
        if self._finished:
            return []
        self._finished = True
        return self._drain(final=True)
