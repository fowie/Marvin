"""Read-only replay of legacy S/E capture bytes; JSON output to stdout only.

  python -m tools.marvin_legacy_replay received.bin --chunks chunks.jsonl \
      --evidence recorded --direction received --expect-config-sequence 0

Evidence and direction default to unspecified/unknown, never inferred from
file names or packet content. Optional reply matching checks only integrity and
the exact known GetConfig shape, not authenticated device origin or USB success.
Payloads stay opaque by default. Explicit --telemetry enables only the exact
legacy134/12/2-byte profiles in a caller-declared received stream. Config stays
opaque; no successor telemetry schema is loaded.

Defaults: 16 MiB input, 64 MiB chunk JSONL, 100000 chunk records/events, 4096-byte
payload limit. Limit/schema failures reject the replay, not silently truncate it.
Inputs must be finished regular files, not symlinks (including parent components),
special files or /dev, /proc, /sys interfaces. Capture directories are never
written. Exit0 means framing completed, possibly with errors/partials; exit2 is
an input/schema/limit error, reported as JSON.
"""

import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_legacy_protocol as protocol
from tools.marvin_json import unique_object
from tools.marvin_legacy_telemetry import interpret_packet
from tools.marvin_legacy_stream import DEFAULT_MAX_INPUT_BYTES, DEFAULT_MAX_PAYLOAD_BYTES, LegacyStreamDecoder
from tools.marvin_stream import read_regular_file


DEFAULT_MAX_CHUNKS_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_EVENTS = 100000
DEFAULT_MAX_CHUNKS = 100000
FEED_BYTES = 4096


def _limit(value, name, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}.")


def read_capture_file(path, *, max_bytes):
    """Use the shared bounded reader, including its ancestor-symlink rejection."""
    _limit(max_bytes, "max_bytes")
    return read_regular_file(path, max_bytes=max_bytes)


def _json_constant(value):
    raise ValueError(f"Nonstandard JSON numeric constant: {value}")


def validate_chunks(raw, chunk_data, *, max_chunks=DEFAULT_MAX_CHUNKS):
    """Validate exact byte coverage and host-read times; return timing ranges."""
    _limit(max_chunks, "max_chunks", minimum=1)
    records = []
    expected = 0
    previous_elapsed = 0
    for number, line in enumerate(chunk_data.splitlines(), 1):
        if not line.strip():
            raise ValueError(f"Chunk line {number}: blank record.")
        if len(records) >= max_chunks:
            raise ValueError("Chunk record limit exceeded; no truncated replay produced.")
        try:
            row = json.loads(line, object_pairs_hook=unique_object, parse_constant=_json_constant)
        except ValueError as error:
            raise ValueError(f"Chunk line {number}: invalid JSON: {error}") from error
        if not isinstance(row, dict):
            raise ValueError(f"Chunk line {number}: expected an object.")
        offset, size, encoded = row.get("offset"), row.get("size"), row.get("hex")
        if type(offset) is not int or offset != expected:
            raise ValueError(f"Chunk line {number}: expected contiguous offset {expected}.")
        if type(size) is not int or size <= 0 or offset + size > len(raw):
            raise ValueError(f"Chunk line {number}: invalid or out-of-range size.")
        if not isinstance(encoded, str) or len(encoded) != size * 2:
            raise ValueError(f"Chunk line {number}: hex requires exactly two digits per byte.")
        try:
            data = bytes.fromhex(encoded)
        except ValueError as error:
            raise ValueError(f"Chunk line {number}: invalid hex.") from error
        if data != raw[offset:offset + size]:
            raise ValueError(f"Chunk line {number}: hex disagrees with input bytes.")
        elapsed, at = row.get("elapsed_seconds"), row.get("at")
        if (type(elapsed) not in (int, float)
                or (type(elapsed) is float and not math.isfinite(elapsed))
                or elapsed < previous_elapsed):
            raise ValueError(f"Chunk line {number}: elapsed time must be finite, nonnegative and nondecreasing.")
        if not isinstance(at, str):
            raise ValueError(f"Chunk line {number}: missing ISO timestamp at.")
        try:
            timestamp = datetime.fromisoformat(at)
        except ValueError as error:
            raise ValueError(f"Chunk line {number}: invalid ISO timestamp at.") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"Chunk line {number}: timestamp at requires a timezone.")
        records.append({
            "offset": offset, "end_offset": offset + size, "line": number,
            "at": at, "elapsed_seconds": elapsed,
        })
        previous_elapsed = elapsed
        expected += size
    if expected != len(raw):
        raise ValueError(f"Chunk coverage is {expected} bytes, input has {len(raw)}; replay not emitted.")
    return records


def replay_capture(received_path, *, chunks_path=None, evidence="unspecified", direction="unknown",
                   expected_config_sequence=None, max_input_bytes=DEFAULT_MAX_INPUT_BYTES,
                   max_chunks_bytes=DEFAULT_MAX_CHUNKS_BYTES, max_chunks=DEFAULT_MAX_CHUNKS,
                   max_events=DEFAULT_MAX_EVENTS, max_payload_bytes=DEFAULT_MAX_PAYLOAD_BYTES,
                   telemetry=False):
    """Return JSON-safe opaque packets and diagnostics from caller-declared evidence."""
    for name, value in (("max_input_bytes", max_input_bytes), ("max_chunks_bytes", max_chunks_bytes)):
        _limit(value, name)
    for name, value in (("max_chunks", max_chunks), ("max_events", max_events)):
        _limit(value, name, minimum=1)
    if evidence not in protocol.EVIDENCE_KINDS or direction not in protocol.DIRECTIONS:
        raise ValueError("Invalid evidence kind or stream direction.")
    if type(telemetry) is not bool:
        raise ValueError("telemetry must be a bool.")
    if expected_config_sequence is not None:
        if type(expected_config_sequence) is not int or not 0 <= expected_config_sequence <= 65535:
            raise ValueError("Expected GetConfig sequence must be an integer fitting uint16.")
        if direction != "received":
            raise ValueError("Reply matching requires an explicit received direction declaration.")
    decoder = LegacyStreamDecoder(max_payload_bytes=max_payload_bytes, max_input_bytes=max_input_bytes)
    raw = read_capture_file(received_path, max_bytes=max_input_bytes)
    chunks = []
    chunk_info = None
    if chunks_path is not None:
        chunk_data = read_capture_file(chunks_path, max_bytes=max_chunks_bytes)
        chunks = validate_chunks(raw, chunk_data, max_chunks=max_chunks)
        chunk_info = {
            "path": str(Path(chunks_path).absolute()), "bytes": len(chunk_data),
            "sha256": hashlib.sha256(chunk_data).hexdigest(), "records": len(chunks),
            "coverage": "exact",
            "timestamps": "Host read completion, not per-byte/device times; wall clocks can move backward.",
        }
    starts = [row["offset"] for row in chunks]
    events = []

    def append_events(decoded):
        for event in decoded:
            if len(events) >= max_events:
                raise ValueError("Event limit exceeded; no truncated replay produced. Original capture is unchanged.")
            result = event.to_dict()
            if telemetry and event.packet is not None:
                result["interpretation"] = interpret_packet(event.packet, direction=direction, evidence=evidence)
            if expected_config_sequence is not None and event.packet is not None:
                try:
                    protocol.validate_get_config_reply(event.packet, expected_config_sequence)
                except ValueError as error:
                    result["get_config_match"] = {"status": "not_match", "reason": str(error)}
                else:
                    result["get_config_match"] = {
                        "status": "integrity_and_shape_match", "sequence": expected_config_sequence,
                        "meaning": "Caller-expected sequence and shape only; not authentication.",
                    }
            if chunks:
                first = chunks[bisect_right(starts, event.offset) - 1]
                last = chunks[bisect_right(starts, event.end_offset - 1) - 1]
                result["chunk_timing"] = {
                    "first_line": first["line"], "last_line": last["line"],
                    "first_at": first["at"], "last_at": last["at"],
                    "first_elapsed_seconds": first["elapsed_seconds"],
                    "last_elapsed_seconds": last["elapsed_seconds"],
                    "meaning": "Host read-completion times of overlapping chunks, not individual byte arrival.",
                }
            events.append(result)

    ranges = [(row["offset"], row["end_offset"]) for row in chunks] or [(0, len(raw))]
    for start, end in ranges:
        for position in range(start, end, FEED_BYTES):
            append_events(decoder.feed(raw[position:min(position + FEED_BYTES, end)]))
    append_events(decoder.finish())
    counts = dict(Counter(event["kind"] for event in events))
    diagnostics = len(events) - counts.get("frame", 0)
    return {
        "schema_version": 1, "protocol": "marvin-legacy-se", "offline_only": True,
        "status": "empty" if not raw else ("decoded_with_diagnostics" if diagnostics else "decoded"),
        "evidence_kind": evidence, "direction": direction,
        "provenance": "Caller-declared evidence kind and direction; not authenticated.",
        "source": {
            "path": str(Path(received_path).absolute()), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "chunks": chunk_info,
        },
        "limits": {
            "max_input_bytes": max_input_bytes, "max_chunks_bytes": max_chunks_bytes,
            "max_chunks": max_chunks, "max_events": max_events,
            "max_payload_bytes": decoder.max_payload_bytes, "max_buffer_bytes": decoder.max_buffer_bytes,
            "recover_at_eof": False,
        },
        "counts": counts, "events": events, "application_acknowledgment": "not_established",
        "limitations": [
            "No received direction, physical origin, USB completion or application ACK is inferred from file contents.",
            ("Only exact legacy134/12/2 profiles are interpreted by explicit opt-in; config and other payloads stay opaque."
             if telemetry else
             "All payloads remain opaque; command IDs may collide with successor IDs and no successor schema is applied."),
            "Configuration version words are returned data, not verified runtime firmware identity.",
            "In-bound lengths retain their whole candidate; corruption may hide/consume later frames. No EOF suffix recovery.",
            "All accepted input bytes have absolute event spans/raw hex; no bytes are silently discarded.",
            "Chunk timing is host read completion, not device time; file reads are not a transactional snapshot.",
            "Use finished trusted local captures. Link/type/identity checks are not a sandbox against concurrent hostile filesystem mutation.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("received", type=Path, help="Existing regular capture file; name does not establish direction")
    parser.add_argument("--chunks", type=Path)
    parser.add_argument("--evidence", choices=protocol.EVIDENCE_KINDS, default="unspecified")
    parser.add_argument("--direction", choices=protocol.DIRECTIONS, default="unknown")
    parser.add_argument("--expect-config-sequence", type=int)
    parser.add_argument("--telemetry", action="store_true", help="Opt in to exact legacy reply profiles; config stays opaque")
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument("--max-chunks-bytes", type=int, default=DEFAULT_MAX_CHUNKS_BYTES)
    parser.add_argument("--max-chunks", type=int, default=DEFAULT_MAX_CHUNKS)
    parser.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS)
    parser.add_argument("--max-payload-bytes", type=int, default=DEFAULT_MAX_PAYLOAD_BYTES)
    args = parser.parse_args(argv)
    try:
        result = replay_capture(
            args.received, chunks_path=args.chunks, evidence=args.evidence, direction=args.direction,
            expected_config_sequence=args.expect_config_sequence, max_input_bytes=args.max_input_bytes,
            max_chunks_bytes=args.max_chunks_bytes, max_chunks=args.max_chunks, max_events=args.max_events,
            max_payload_bytes=args.max_payload_bytes, telemetry=args.telemetry,
        )
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({"status": "input_error", "offline_only": True,
                          "error": str(error), "source_modified_by_replay": False}, allow_nan=False))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
