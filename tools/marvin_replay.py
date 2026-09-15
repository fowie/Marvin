"""Replay existing local serial capture files offline; JSON goes to stdout only.

Examples:
  python -m tools.marvin_replay capture/received.bin --evidence recorded
  python -m tools.marvin_replay received.bin --chunks chunks.jsonl --evidence synthetic

Defaults bound raw input to 16 MiB, chunk JSONL to 64 MiB, chunks/events to
100000 each, and declared payloads to 4096 bytes. Larger wire payloads can be
retained with --max-payload-bytes up to 65535. Limits reject inputs explicitly;
they never silently truncate a capture. Empty completed receive files are valid.

Optional chunks must exactly cover received.bin in order with matching hex,
positive sizes, finite nondecreasing elapsed_seconds and timezone-aware ISO at
timestamps. Duplicate JSON object keys are rejected, even with identical values.
Wall-clock adjustments are allowed. Chunk times are host read times,
not byte arrival or device timestamps. No USB completion or application ACK is
inferred from these bytes, even when the caller declares them received/recorded.

Exit 0 means replay completed (possibly with noise/errors/partial events);
exit 2 means an input/schema/limit error, reported as JSON. Nothing writes to a
capture directory. Recovered programs and hardware libraries are never loaded.
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

from tools.marvin_stream import DEFAULT_MAX_PAYLOAD_BYTES, StreamDecoder, read_regular_file
from tools.marvin_telemetry import DEFAULT_CATALOG, DIRECTIONS, TelemetryDecoder


DEFAULT_MAX_INPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_CHUNKS_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_EVENTS = 100000
DEFAULT_MAX_CHUNKS = 100000
FEED_BYTES = 4096
EVIDENCE_KINDS = ("unspecified", "recorded", "synthetic")


def _integer_limit(value, name, *, allow_zero=False):
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError(f"{name} must be a {'nonnegative' if allow_zero else 'positive'} integer.")


def _reject_constant(value):
    raise ValueError(f"Nonstandard JSON numeric constant: {value}")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_chunks(raw, chunk_data, *, max_chunks):
    records = []
    expected_offset = 0
    previous_elapsed = 0
    for line_number, line in enumerate(chunk_data.splitlines(), 1):
        if not line.strip():
            raise ValueError(f"Chunk line {line_number}: blank JSONL record.")
        if len(records) >= max_chunks:
            raise ValueError(f"Chunk record limit {max_chunks} exceeded; no truncated replay produced.")
        try:
            row = json.loads(line, object_pairs_hook=_json_object, parse_constant=_reject_constant)
        except ValueError as error:
            raise ValueError(f"Chunk line {line_number}: invalid JSON: {error}") from error
        if not isinstance(row, dict):
            raise ValueError(f"Chunk line {line_number}: expected an object.")
        offset, size, data_hex = row.get("offset"), row.get("size"), row.get("hex")
        if type(offset) is not int or offset != expected_offset:
            raise ValueError(f"Chunk line {line_number}: expected contiguous offset {expected_offset}.")
        if type(size) is not int or size <= 0 or offset + size > len(raw):
            raise ValueError(f"Chunk line {line_number}: invalid or out-of-range size.")
        if not isinstance(data_hex, str) or len(data_hex) != 2 * size:
            raise ValueError(f"Chunk line {line_number}: hex must have exactly two digits per byte.")
        try:
            data = bytes.fromhex(data_hex)
        except ValueError as error:
            raise ValueError(f"Chunk line {line_number}: invalid hex.") from error
        if data != raw[offset:offset + size]:
            raise ValueError(f"Chunk line {line_number}: data differs from received.bin at offset {offset}.")
        elapsed, at = row.get("elapsed_seconds"), row.get("at")
        if (type(elapsed) not in (int, float)
                or (type(elapsed) is float and not math.isfinite(elapsed))
                or elapsed < previous_elapsed):
            raise ValueError(f"Chunk line {line_number}: elapsed_seconds must be finite, nonnegative and nondecreasing.")
        if not isinstance(at, str):
            raise ValueError(f"Chunk line {line_number}: missing ISO timestamp at.")
        try:
            timestamp = datetime.fromisoformat(at)
        except ValueError as error:
            raise ValueError(f"Chunk line {line_number}: invalid ISO timestamp at.") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"Chunk line {line_number}: timestamp at must include a timezone.")
        records.append({
            "offset": offset, "end_offset": offset + size, "at": at,
            "elapsed_seconds": elapsed, "line": line_number,
        })
        expected_offset += size
        previous_elapsed = elapsed
    if expected_offset != len(raw):
        raise ValueError(f"Chunk records cover {expected_offset} bytes but received.bin has {len(raw)}; replay not emitted.")
    return records


def replay_capture(received_path, *, chunks_path=None, evidence="unspecified",
                   direction="received", max_input_bytes=DEFAULT_MAX_INPUT_BYTES,
                   max_chunks_bytes=DEFAULT_MAX_CHUNKS_BYTES, max_chunks=DEFAULT_MAX_CHUNKS,
                   max_events=DEFAULT_MAX_EVENTS, max_payload_bytes=DEFAULT_MAX_PAYLOAD_BYTES,
                   recover_at_eof=True, telemetry=True, catalog_path=DEFAULT_CATALOG):
    """Read a finished capture and return JSON-safe frame/diagnostic/partial events.

Evidence kind and stream direction are caller declarations, not authentication.
Inconsistent optional chunk metadata rejects the replay rather than inventing
timing. Replay raw received.bin without --chunks to examine such evidence.
    """
    for name, value in (("max_input_bytes", max_input_bytes), ("max_chunks_bytes", max_chunks_bytes)):
        _integer_limit(value, name, allow_zero=True)
    for name, value in (("max_chunks", max_chunks), ("max_events", max_events)):
        _integer_limit(value, name)
    if evidence not in EVIDENCE_KINDS or direction not in DIRECTIONS:
        raise ValueError("Invalid evidence kind or stream direction.")
    if type(telemetry) is not bool:
        raise ValueError("telemetry must be a bool.")
    decoder = StreamDecoder(max_payload_bytes=max_payload_bytes, recover_at_eof=recover_at_eof)
    raw = read_regular_file(received_path, max_bytes=max_input_bytes)
    chunks = []
    chunk_info = None
    if chunks_path is not None:
        chunk_data = read_regular_file(chunks_path, max_bytes=max_chunks_bytes)
        chunks = _validate_chunks(raw, chunk_data, max_chunks=max_chunks)
        chunk_info = {
            "path": str(Path(chunks_path).absolute()), "bytes": len(chunk_data),
            "sha256": hashlib.sha256(chunk_data).hexdigest(), "records": len(chunks),
            "coverage": "exact", "timestamps": "host read completion, not per-byte/device time",
        }
    starts = [row["offset"] for row in chunks]
    interpreter = TelemetryDecoder(catalog_path) if telemetry else None
    events = []

    def append_events(decoded):
        for event in decoded:
            if len(events) >= max_events:
                raise ValueError(f"Event limit {max_events} exceeded; no truncated replay produced. "
                                 "Original capture is unchanged; use an explicitly larger limit.")
            result = event.to_dict()
            if event.packet is not None and interpreter is not None:
                result["interpretation"] = interpreter.interpret_packet(event.packet, direction=direction)
            if chunks:
                first = bisect_right(starts, event.offset) - 1
                last = bisect_right(starts, event.end_offset - 1) - 1
                result["chunk_timing"] = {
                    "first_line": chunks[first]["line"], "last_line": chunks[last]["line"],
                    "first_at": chunks[first]["at"], "last_at": chunks[last]["at"],
                    "first_elapsed_seconds": chunks[first]["elapsed_seconds"],
                    "last_elapsed_seconds": chunks[last]["elapsed_seconds"],
                    "meaning": "Host read-completion timestamps of chunks overlapping this byte span.",
                }
            events.append(result)

    # Bounded feeds also bound each temporary list returned by the stream decoder.
    ranges = [(row["offset"], row["end_offset"]) for row in chunks] or [(0, len(raw))]
    for start, end in ranges:
        for position in range(start, end, FEED_BYTES):
            append_events(decoder.feed(raw[position:min(position + FEED_BYTES, end)]))
    append_events(decoder.finish())
    counts = dict(Counter(event["kind"] for event in events))
    diagnostic_count = len(events) - counts.get("frame", 0)
    return {
        "schema_version": 1,
        "status": "empty" if not raw else ("decoded_with_diagnostics" if diagnostic_count else "decoded"),
        "offline_only": True, "evidence_kind": evidence, "direction": direction,
        "provenance": "Caller-declared evidence kind and direction; not authenticated.",
        "source": {
            "path": str(Path(received_path).absolute()), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "chunks": chunk_info,
        },
        "limits": {
            "max_input_bytes": max_input_bytes, "max_chunks_bytes": max_chunks_bytes,
            "max_chunks": max_chunks, "max_events": max_events,
            "max_payload_bytes": decoder.max_payload_bytes, "max_buffer_bytes": decoder.max_buffer_bytes,
            "recover_at_eof": recover_at_eof,
        },
        "counts": counts, "events": events,
        "application_acknowledgment": "not_established",
        "limitations": [
            "A received byte stream may contain noise, echoes or injected/synthetic data.",
            "No USB OUT completion, USB IN transfer, or matched-request application acknowledgment is inferred.",
            "No physical reply or installed-controller compatibility is established by a successor layout match.",
            "Damaged in-bound lengths wait for their declared end or EOF; EOF suffix recovery has an explicit ambiguous boundary.",
            "All skipped bytes have absolute spans and raw hex; the source path/hash identifies the complete byte snapshot read without modifying it.",
            "The files must be finished captures; separate-file read timing is not a transactional snapshot.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("received", type=Path, help="Existing serial received.bin; regular local file only")
    parser.add_argument("--chunks", type=Path, help="Optional exact-cover chunks.jsonl")
    parser.add_argument("--evidence", choices=EVIDENCE_KINDS, default="unspecified")
    parser.add_argument("--direction", choices=DIRECTIONS, default="received")
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument("--max-chunks-bytes", type=int, default=DEFAULT_MAX_CHUNKS_BYTES)
    parser.add_argument("--max-chunks", type=int, default=DEFAULT_MAX_CHUNKS)
    parser.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS)
    parser.add_argument("--max-payload-bytes", type=int, default=DEFAULT_MAX_PAYLOAD_BYTES)
    parser.add_argument("--retain-incomplete", action="store_true", help="Disable ambiguous EOF suffix recovery")
    parser.add_argument("--no-telemetry", action="store_true", help="Only frame bytes; do not load a telemetry catalogue")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)
    try:
        result = replay_capture(
            args.received, chunks_path=args.chunks, evidence=args.evidence, direction=args.direction,
            max_input_bytes=args.max_input_bytes, max_chunks_bytes=args.max_chunks_bytes,
            max_chunks=args.max_chunks, max_events=args.max_events, max_payload_bytes=args.max_payload_bytes,
            recover_at_eof=not args.retain_incomplete, telemetry=not args.no_telemetry, catalog_path=args.catalog,
        )
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({"status": "input_error", "offline_only": True,
                          "error": str(error), "source_modified_by_replay": False}, allow_nan=False))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
