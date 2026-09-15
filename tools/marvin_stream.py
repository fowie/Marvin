"""Bounded offline framing for recorded Marvin byte streams.

Default payload limit: 4096 bytes; configurable through the wire maximum 65535.
The retained buffer is at most max_payload_bytes + 12 unless explicitly enlarged.
Input chunks need not align with packets. Unknown commands and payload sizes
within the configured bound remain valid frames.

While an in-bound candidate is incomplete, feeding never searches its payload
for another frame: an embedded valid frame can itself be payload. A damaged
length can therefore hold later bytes until the declared size arrives or EOF.
For a complete CRC-valid candidate, embedded frames likewise remain payload. At EOF,
recover_at_eof=True selects a later complete CRC-valid frame if possible, reporting
the abandoned prefix as ambiguous partial evidence. Set it false to retain the
entire incomplete candidate instead. Out-of-bound lengths and complete invalid
frames are rejected one byte at a time, preserving modern length-damage recovery.
This can recover a nested payload frame from a rejected outer candidate; it is
not legacy's whole-invalid-candidate/no-nested-recovery policy.

follows_corruption is sticky after a rejected candidate or ambiguous EOF recovery.
Later CRC-valid frames retain that boundary warning: integrity alone does not
distinguish a nested payload from a real next frame or prove physical corruption.

Event spans partition all input bytes after finish(); raw bytes are never silently
discarded. This module does not determine direction, provenance or acknowledgment.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import struct

from tools import marvin_protocol as protocol


DEFAULT_MAX_PAYLOAD_BYTES = 4096
WIRE_MAX_PAYLOAD_BYTES = 65535
FRAME_OVERHEAD = 12


def read_regular_file(path, *, max_bytes):
    """Read a bounded local regular file; reject all link components and special files.

The caller-selected limit is enforced before and during the read. No device,
pipe, URL, stdin, or capture-directory write path is supported.
    """
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer.")
    supplied = Path(path)
    absolute = supplied if supplied.is_absolute() else Path.cwd() / supplied
    path = Path(os.path.abspath(absolute))
    if any(len(candidate.parts) > 1 and candidate.parts[1] in {"dev", "proc", "sys"}
           for candidate in (absolute, path)):
        raise ValueError(f"Device and kernel-interface paths are not offline capture inputs: {path}")
    # Walk the supplied spelling before normalizing away ".."; lstat of the
    # leaf alone would follow parent links, including links into kernel paths.
    for component in (*reversed(absolute.parents), absolute):
        if stat.S_ISLNK(component.lstat().st_mode):
            raise ValueError(f"Input must be a regular file with no symlink path components: {component}")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Input must be a regular file, not a symlink or device: {path}")
    if before.st_size > max_bytes:
        raise ValueError(f"Input exceeds the {max_bytes}-byte limit: {path}")
    resolved = path.resolve(strict=True)
    if len(resolved.parts) > 1 and resolved.parts[1] in {"dev", "proc", "sys"}:
        raise ValueError(f"Input resolves to a device or kernel-interface path: {path}")
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"Input identity/type changed while opening: {path}")
        data = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    if len(data) > max_bytes:
        raise ValueError(f"Input exceeds the {max_bytes}-byte limit: {path}")
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
        opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
    ) or len(data) != opened.st_size:
        raise ValueError(f"Input changed during reading; use a finished capture: {path}")
    return data


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    offset: int
    raw: bytes
    code: str
    message: str
    packet: protocol.Packet | None = None
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
            result["packet"] = {
                "sequence": self.packet.sequence, "command": self.packet.command,
                "response_field": self.packet.response_field,
                "payload_bytes": len(self.packet.payload), "payload_hex": self.packet.payload.hex(),
            }
        return result


class StreamDecoder:
    def __init__(self, *, max_payload_bytes=DEFAULT_MAX_PAYLOAD_BYTES,
                 max_buffer_bytes=None, start_offset=0, recover_at_eof=True):
        if type(max_payload_bytes) is not int or not 0 <= max_payload_bytes <= WIRE_MAX_PAYLOAD_BYTES:
            raise ValueError("max_payload_bytes must be an integer from 0 through 65535.")
        if max_buffer_bytes is None:
            max_buffer_bytes = max_payload_bytes + FRAME_OVERHEAD
        if type(max_buffer_bytes) is not int or max_buffer_bytes < max_payload_bytes + FRAME_OVERHEAD:
            raise ValueError("max_buffer_bytes must hold max_payload_bytes + 12.")
        if type(start_offset) is not int or start_offset < 0:
            raise ValueError("start_offset must be a nonnegative integer.")
        if type(recover_at_eof) is not bool:
            raise ValueError("recover_at_eof must be a bool.")
        self.max_payload_bytes = max_payload_bytes
        self.max_buffer_bytes = max_buffer_bytes
        self.recover_at_eof = recover_at_eof
        self._offset = start_offset
        self._buffer = bytearray()
        self._finished = False
        self._corruption_seen = False

    @property
    def offset(self):
        """Absolute offset of the first buffered byte (or next input byte)."""
        return self._offset

    @property
    def buffered_bytes(self):
        return len(self._buffer)

    def _take(self, length, kind, code, message, **details):
        event = StreamEvent(
            kind, self._offset, bytes(self._buffer[:length]), code, message,
            follows_corruption=self._corruption_seen, **details,
        )
        del self._buffer[:length]
        self._offset += length
        return event

    def _later_valid_frame(self):
        position = self._buffer.find(protocol.HEADER, 1)
        while position >= 0 and len(self._buffer) - position >= FRAME_OVERHEAD:
            length = struct.unpack_from("<H", self._buffer, position + 6)[0]
            end = position + FRAME_OVERHEAD + length
            if length <= self.max_payload_bytes and end <= len(self._buffer):
                try:
                    protocol.decode_packet(bytes(self._buffer[position:end]))
                except ValueError:
                    pass
                else:
                    return position
            position = self._buffer.find(protocol.HEADER, position + 1)
        return None

    def _drain(self, *, final=False):
        events = []
        while self._buffer:
            if not self._buffer.startswith(protocol.HEADER):
                position = self._buffer.find(protocol.HEADER)
                if position < 0:
                    position = len(self._buffer) - (self._buffer[-1] == protocol.HEADER[0])
                if position:
                    events.append(self._take(
                        position, "noise", "unframed_bytes",
                        "Skipped bytes with no complete header; raw input retained in this span.",
                    ))
                    continue
            if len(self._buffer) < 8:
                if final:
                    events.append(self._take(
                        len(self._buffer), "partial", "incomplete_header",
                        "EOF inside a possible eight-byte header; no complete frame established.",
                        needed_bytes=8 - len(self._buffer),
                    ))
                break
            length = struct.unpack_from("<H", self._buffer, 6)[0]
            if length > self.max_payload_bytes:
                events.append(self._take(
                    1, "error", "payload_limit",
                    f"Declared payload {length} exceeds configured limit {self.max_payload_bytes}; "
                    "rejecting this candidate, not proving its length is impossible on the wire.",
                    declared_payload_bytes=length, candidate_preview_hex=bytes(self._buffer[:8]).hex(),
                ))
                self._corruption_seen = True
                continue
            frame_bytes = length + FRAME_OVERHEAD
            if len(self._buffer) < frame_bytes:
                if not final:
                    break
                later = self._later_valid_frame() if self.recover_at_eof else None
                if later is not None:
                    events.append(self._take(
                        later, "partial", "ambiguous_eof_resync",
                        "EOF before declared frame end; selected a later CRC-valid frame. "
                        "It could be embedded in the truncated payload; boundary is not certain.",
                        declared_payload_bytes=length, needed_bytes=frame_bytes - len(self._buffer),
                    ))
                    self._corruption_seen = True
                    continue
                events.append(self._take(
                    len(self._buffer), "partial", "incomplete_frame",
                    "EOF before declared frame end; raw incomplete candidate retained.",
                    declared_payload_bytes=length, needed_bytes=frame_bytes - len(self._buffer),
                ))
                break
            candidate = bytes(self._buffer[:frame_bytes])
            try:
                packet = protocol.decode_packet(candidate)
            except ValueError as error:
                events.append(self._take(
                    1, "error", "invalid_frame",
                    f"{error} Rejecting one header byte to permit length-damage recovery; "
                    "later headers may instead lie inside this rejected candidate's payload.",
                    declared_payload_bytes=length, candidate_preview_hex=candidate[:64].hex(),
                ))
                self._corruption_seen = True
                continue
            events.append(self._take(
                frame_bytes, "frame", "validated_frame",
                "Header, declared length, footer and CRC match; direction and origin are not established."
                + (" After a rejected candidate or EOF recovery, this boundary remains a "
                   "resynchronization hypothesis." if self._corruption_seen else ""),
                packet=packet, declared_payload_bytes=length,
            ))
        return events

    def feed(self, data):
        if self._finished:
            raise ValueError("Cannot feed a finished decoder.")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("feed expects bytes or bytearray.")
        events = []
        position = 0
        while position < len(data):
            count = min(len(data) - position, self.max_buffer_bytes - len(self._buffer))
            if count <= 0:
                raise RuntimeError("Decoder buffer made no progress within its configured bound.")
            self._buffer.extend(data[position:position + count])
            position += count
            events.extend(self._drain())
        return events

    def finish(self):
        """Report the incomplete tail and optionally recover an ambiguous EOF suffix."""
        if self._finished:
            return []
        self._finished = True
        return self._drain(final=True)
