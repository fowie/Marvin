"""Bounded append-only legacy polling evidence. No transport or device access."""

from dataclasses import asdict, fields as dataclass_fields
from datetime import datetime, timezone
import hashlib
import json
import os

from tools.marvin_json import unique_object
from tools.marvin_legacy_client import PROFILE, _integer
from tools import marvin_legacy_protocol as protocol
from tools.marvin_legacy_telemetry import interpret_packet
from tools.marvin_paths import new_output_path
from tools.marvin_stream import read_regular_file


SCHEMA = "marvin-legacy-poll-v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 16384
TERMINAL_BYTES = 131072
LABELS = frozenset((
    "unverified", "unsolicited", "pre_request", "stale", "late", "duplicate",
    "request_echo", "error_status", "unexpected_command", "unexpected_payload",
    "malformed", "noise", "partial", "ambiguous_boundary", "matched_candidate",
))
CONFIDENCES = (
    "raw_observation", "framing_integrity_only",
    "integrity_and_shape_match_not_authenticated",
)


def encode_row(row):
    return (json.dumps(row, ensure_ascii=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("ascii")


def request_row(index, request):
    row = asdict(request)
    row["raw_hex"] = row.pop("raw").hex()
    return {"type": "request", "index": index, **row}


def evidence_row(index, item):
    return {
        "type": "evidence", "index": index, "stream": item.stream.to_dict(),
        "started_at": item.started_at, "ended_at": item.ended_at,
        "labels": list(item.labels), "profile": item.profile,
        "confidence": item.confidence, "evidence_kind": item.evidence_kind,
        "application_acknowledgment": item.application_acknowledgment,
    }


class RecordingLimit(ValueError):
    pass


class Recorder:
    """One exclusive private file; cap includes the reserved terminal seal.

    A partial/uncertain OS write poisons the writer; it never appends a
    success-shaped seal after that. Already-written bytes are never removed.
    """

    def __init__(self, output, *, max_bytes, max_records=MAX_RECORDS):
        _integer("max_bytes", max_bytes, TERMINAL_BYTES + 4096, MAX_FILE_BYTES)
        _integer("max_records", max_records, 2, MAX_RECORDS)
        self.path = new_output_path(output)
        self.max_bytes, self.max_records = max_bytes, max_records
        self.bytes_written = self.records = 0
        self.digest = hashlib.sha256()
        self.broken = False
        self.sealed = False
        self.stream = None

    def open(self):
        # Repeat snapshot checks immediately before exclusive creation.
        path = new_output_path(self.path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.stream = os.fdopen(descriptor, "wb", buffering=0)

    def _write(self, data):
        completed = False
        try:
            count = self.stream.write(data)
            if type(count) is not int or count != len(data):
                if type(count) is int and 0 <= count <= len(data):
                    self.bytes_written += count
                raise OSError("Short/uncertain evidence write; recording is incomplete.")
            self.bytes_written += count
            completed = True
        finally:
            if not completed:
                self.broken = True

    def append(self, row):
        if self.broken or self.sealed or self.stream is None:
            raise OSError("Recording is not writable; no resume.")
        data = encode_row(row)
        if (self.bytes_written + len(data) > self.max_bytes - TERMINAL_BYTES
                or self.records >= self.max_records - 1):
            raise RecordingLimit("Serialized evidence byte/record budget exhausted; no row was truncated.")
        self._write(data)
        self.digest.update(data)
        self.records += 1

    def finish(self, report):
        if self.broken or self.sealed or self.stream is None:
            raise OSError("Cannot seal a broken, sealed or unopened recording.")
        data = encode_row({
            "type": "terminal", "prefix_bytes": self.bytes_written,
            "prefix_records": self.records, "prefix_sha256": self.digest.hexdigest(),
            "report": report,
        })
        if len(data) > TERMINAL_BYTES or self.bytes_written + len(data) > self.max_bytes:
            raise RecordingLimit("Terminal diagnostics exceed the reserved seal budget.")
        self._write(data)
        self.records += 1
        os.fsync(self.stream.fileno())
        self.sealed = True

    def close(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            stream.close()


def _constant(value):
    raise ValueError(f"Nonstandard JSON constant: {value}")


def _time(value, *, optional=False):
    if value is None and optional:
        return
    if type(value) not in (int, float) or not 0 <= value <= 1e12:
        raise ValueError("Expected a finite nonnegative host monotonic time.")


def _hex(value, maximum):
    if type(value) is not str or len(value) > maximum * 2:
        raise ValueError("Invalid/bounded raw hex.")
    raw = bytes.fromhex(value)
    if value != raw.hex():
        raise ValueError("Raw hex must contain exactly two lowercase digits per byte.")
    return raw


def _utc(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError("Expected a bounded UTC timestamp.")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise ValueError("Expected a host UTC timestamp.")


def _validate_request(row, index):
    if row.get("index") != index or type(row.get("index")) is not int:
        raise ValueError("Request positions must be contiguous.")
    _integer("sequence", row.get("sequence"), 0, 65535)
    if row.get("query") != "read-raw-data":
        raise ValueError("Polling records allow only ReadRawData.")
    if _hex(row.get("raw_hex"), 10) != protocol.GETTERS["read-raw-data"].encode(row["sequence"]):
        raise ValueError("Request bytes disagree with command/sequence.")
    for key in ("accepted_bytes", "uncertain_bytes"):
        _integer(key, row.get(key), 0, 10)
    if row["accepted_bytes"] + row["uncertain_bytes"] > 10:
        raise ValueError("Invalid TX accounting.")
    _time(row.get("deadline"))
    _time(row.get("submitted_at"), optional=True)
    _integer("input_boundary", row.get("input_boundary"), 0, 16 * 1024 * 1024)
    if row.get("status") not in ("prepared", "write_attempted", "awaiting_reply", "matched", "failed"):
        raise ValueError("Unknown request status.")
    if row.get("reply_event") is not None:
        _integer("reply_event", row["reply_event"], 0, 8191)
        if row["submitted_at"] is None:
            raise ValueError("Candidate reference requires a completed submission timestamp.")
    if row["status"] == "matched" and (
            row["accepted_bytes"] != 10 or row["uncertain_bytes"] != 0
            or row["submitted_at"] is None or row["reply_event"] is None):
        raise ValueError("Delivered request has inconsistent TX/result accounting.")


def _validate_evidence(row, index, offset, evidence_kind):
    if type(row.get("index")) is not int or row["index"] != index:
        raise ValueError("Evidence positions must be contiguous.")
    if (row.get("profile") != PROFILE or row.get("evidence_kind") != evidence_kind
            or row.get("application_acknowledgment") != "not_established"
            or row.get("confidence") not in CONFIDENCES):
        raise ValueError("Invalid evidence declarations/confidence.")
    labels = row.get("labels")
    if (type(labels) is not list or len(labels) > len(LABELS)
            or any(type(label) is not str or label not in LABELS for label in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("Invalid client evidence labels.")
    _time(row.get("started_at"), optional=True)
    _time(row.get("ended_at"), optional=True)
    stream = row.get("stream")
    if not isinstance(stream, dict):
        raise ValueError("Missing stream evidence.")
    raw = _hex(stream.get("raw_hex"), 4106)
    if not raw or any(type(stream.get(key)) is not int for key in ("offset", "end_offset", "raw_bytes")):
        raise ValueError("Invalid raw span.")
    if (stream["offset"], stream["end_offset"], stream["raw_bytes"]) != (offset, offset + len(raw), len(raw)):
        raise ValueError("RX spans must partition accepted bytes without gaps/duplicates.")
    if stream.get("kind") not in ("frame", "error", "partial", "noise"):
        raise ValueError("Unknown stream event kind.")
    if (type(stream.get("follows_corruption")) is not bool
            or any(type(stream.get(key)) is not str or len(stream[key]) > 1024
                   for key in ("code", "message"))):
        raise ValueError("Invalid stream diagnostics.")
    if stream["kind"] == "frame":
        packet = protocol.decode_packet(raw)
        if packet.to_dict() != stream.get("packet"):
            raise ValueError("Packet fields disagree with raw bytes.")
    elif "packet" in stream:
        raise ValueError("Non-frame event cannot assert a packet.")
    if "matched_candidate" in labels and stream["kind"] != "frame":
        raise ValueError("A non-frame event cannot be a matched candidate.")
    return offset + len(raw)


def _failure(value, *, message_limit=256):
    if (type(value) is not dict or set(value) != {"code", "message"}
            or type(value["code"]) is not str or not 1 <= len(value["code"]) <= 80
            or type(value["message"]) is not str or len(value["message"]) > message_limit):
        raise ValueError("Invalid bounded failure diagnostic.")


def _validate_report(report, requests, events, samples, contexts, plan):
    if report.get("status") not in ("complete", "failed"):
        raise ValueError("Invalid terminal collection status.")
    for key in ("observed", "persisted"):
        counts = report.get(key)
        if type(counts) is not dict or set(counts) != {"requests", "events"}:
            raise ValueError("Invalid terminal evidence counters.")
        _integer(key + ".requests", counts["requests"], 0, plan.max_requests)
        _integer(key + ".events", counts["events"], 0, plan.max_events)
    if report["persisted"] != {"requests": len(requests), "events": len(events)}:
        raise ValueError("Terminal persisted counts disagree with records.")
    if any(report["observed"][key] < report["persisted"][key] for key in ("requests", "events")):
        raise ValueError("Observed counters cannot be less than persisted counters.")
    if type(report.get("evidence_complete")) is not bool:
        raise ValueError("Invalid evidence completeness declaration.")
    if report["evidence_complete"] and report["observed"] != report["persisted"]:
        raise ValueError("Terminal evidence completeness contradicts counts.")
    _integer("requested_snapshots", report.get("requested_snapshots"), 1, 256)
    _integer("delivered_candidates", report.get("delivered_candidates"), 0, report["observed"]["requests"])
    _integer("accepted_tx_bytes", report.get("accepted_tx_bytes"), 0, 10 * report["observed"]["requests"])
    _integer("uncertain_tx_bytes", report.get("uncertain_tx_bytes"), 0, 10 * report["observed"]["requests"])
    _integer("rejected_input_bytes", report.get("rejected_input_bytes"), 0, (1 << 63) - 1)
    _integer("secondary_errors_omitted", report.get("secondary_errors_omitted"), 0, 65536)
    if (report["requested_snapshots"] != plan.max_requests
            or report["accepted_tx_bytes"] + report["uncertain_tx_bytes"] > 10 * report["observed"]["requests"]
            or report.get("application_acknowledgment") != "not_established"
            or report.get("client_state") not in ("closed", "invalid")):
        raise ValueError("Contradictory terminal session accounting.")
    missing_requests = report["observed"]["requests"] - len(requests)
    for key, actual, per_missing in (
        ("delivered_candidates", sum(row["status"] == "matched" for row in requests), 1),
        ("accepted_tx_bytes", sum(row["accepted_bytes"] for row in requests), 10),
        ("uncertain_tx_bytes", sum(row["uncertain_bytes"] for row in requests), 10),
    ):
        if not actual <= report[key] <= actual + per_missing * missing_requests:
            raise ValueError("Terminal counters disagree with retained requests.")
    missing_tx = (report["accepted_tx_bytes"] + report["uncertain_tx_bytes"]
                  - sum(row["accepted_bytes"] + row["uncertain_bytes"] for row in requests))
    if missing_tx > 10 * missing_requests:
        raise ValueError("Unrecorded TX counters exceed missing requests.")
    secondary = report.get("secondary_errors")
    if type(secondary) is not list or len(secondary) > 16:
        raise ValueError("Invalid secondary diagnostics.")
    for failure in secondary:
        _failure(failure)
    if report.get("client_failure") is not None:
        _failure(report["client_failure"], message_limit=1024)
    if report["status"] == "failed":
        _failure(report.get("failure"))
        gap = report.get("gap")
        if (type(gap) is not dict or gap.get("reason") != report["failure"]["code"]
                or gap.get("resume_permitted") is not False
                or type(gap.get("uncollected_snapshots")) is not int
                or gap["uncollected_snapshots"] != plan.max_requests - report["delivered_candidates"]):
            raise ValueError("Invalid failed collection gap.")
    elif (not report["evidence_complete"] or report["client_state"] != "closed"
          or report.get("failure") is not None or secondary or report["secondary_errors_omitted"]
          or report["rejected_input_bytes"] or report.get("client_failure") is not None
          or report.get("gap") is not None
          or len(samples) != len(requests) or len(samples) != plan.max_requests
          or len(events) != len(samples)
          or [row["phase"] for row in contexts] != ["request"] * len(requests) + ["final"]
          or any(event["labels"] != ["matched_candidate"] for event in events)):
        raise ValueError("Contradictory completion report.")


def inspect_recording(path, *, max_bytes=MAX_FILE_BYTES, max_records=MAX_RECORDS, allow_incomplete=False):
    """Inspect bounded, finished local input; declarations/hashes are not trust.

    Missing terminal/newline rejects the input unless incomplete inspection is
    explicitly enabled. Malformed complete rows or a bad seal always reject it.
    """
    _integer("max_bytes", max_bytes, 1, MAX_FILE_BYTES)
    _integer("max_records", max_records, 1, MAX_RECORDS)
    if type(allow_incomplete) is not bool:
        raise ValueError("allow_incomplete must be an explicit boolean.")
    raw = read_regular_file(path, max_bytes=max_bytes)
    if raw.count(b"\n") + int(not raw.endswith(b"\n")) > max_records:
        raise ValueError("Offline record budget exceeded.")
    lines = raw.split(b"\n")
    partial = lines.pop()
    if len(partial) > TERMINAL_BYTES:
        raise ValueError("Partial final record exceeds row budget.")
    partial_bytes = len(partial)
    requests, events, contexts = [], [], []
    header, terminal = None, None
    digest = hashlib.sha256()
    offset = prefix_bytes = 0
    for index, line in enumerate(lines):
        line += b"\n"
        if len(line) > TERMINAL_BYTES:
            raise ValueError("Offline row byte budget exceeded.")
        row = json.loads(line, object_pairs_hook=unique_object, parse_constant=_constant)
        if type(row) is not dict or terminal is not None:
            raise ValueError("Invalid row or data after terminal.")
        kind = row.get("type")
        if index == 0:
            if (kind != "header" or row.get("schema") != SCHEMA or row.get("profile") != PROFILE
                    or row.get("evidence_kind") not in protocol.EVIDENCE_KINDS
                    or row.get("application_acknowledgment") != "not_established"):
                raise ValueError("Invalid polling header.")
            header = row
            # Runtime import avoids duplicating the collection plan validator.
            from tools.marvin_legacy_poll import PollPlan
            options = row.get("plan")
            if not isinstance(options, dict):
                raise ValueError("Missing validated polling plan.")
            plan = PollPlan(**{field.name: options.get(field.name) for field in dataclass_fields(PollPlan)})
            if options != plan.to_dict():
                raise ValueError("Recorded plan fields disagree.")
            if len(raw) > plan.max_output_bytes:
                raise ValueError("Recording exceeds its declared byte bound.")
            _utc(row.get("utc"))
            _time(row.get("monotonic"))
        elif kind == "request":
            if len(requests) >= 256:
                raise ValueError("Offline request budget exceeded.")
            _validate_request(row, len(requests))
            if not requests and row["sequence"] != plan.first_sequence:
                raise ValueError("First request disagrees with planned sequence.")
            if requests and row["sequence"] != requests[-1]["sequence"] + 1:
                raise ValueError("Request sequences must be contiguous, without reuse/wrap.")
            requests.append(row)
        elif kind == "evidence":
            if len(events) >= 8192:
                raise ValueError("Offline event budget exceeded.")
            offset = _validate_evidence(row, len(events), offset, header["evidence_kind"])
            events.append(row)
        elif kind == "context":
            if len(contexts) >= 258:
                raise ValueError("Offline timing context budget exceeded.")
            if row.get("phase") not in ("request", "final"):
                raise ValueError("Invalid timing context phase.")
            _utc(row.get("utc"))
            _time(row.get("monotonic_before"))
            _time(row.get("monotonic_after"))
            previous_time = contexts[-1]["monotonic_after"] if contexts else header["monotonic"]
            if not previous_time <= row["monotonic_before"] <= row["monotonic_after"]:
                raise ValueError("Invalid/regressed monotonic context.")
            contexts.append(row)
        elif kind == "terminal":
            if (type(row.get("prefix_bytes")) is not int or row["prefix_bytes"] != prefix_bytes
                    or type(row.get("prefix_records")) is not int or row["prefix_records"] != index
                    or row.get("prefix_sha256") != digest.hexdigest() or partial_bytes):
                raise ValueError("Terminal hash/byte/record seal mismatch.")
            if type(row.get("report")) is not dict:
                raise ValueError("Missing terminal report.")
            terminal = row
            continue
        else:
            raise ValueError("Unknown polling record type.")
        prefix_bytes += len(line)
        digest.update(line)

    if header is None:
        raise ValueError("Missing complete polling header.")
    if terminal is None and not allow_incomplete:
        raise ValueError("Missing/truncated terminal seal; explicitly opt in to incomplete inspection.")
    if (len(lines) + bool(partial) > plan.max_records or len(requests) > plan.max_requests
            or len(events) > plan.max_events or offset > plan.max_rx_bytes):
        raise ValueError("Recording exceeds its declared resource bounds.")
    samples = []
    previous = None
    repeats = 0
    for request in requests:
        event_index = request["reply_event"]
        if event_index is None:
            continue
        if event_index >= len(events):
            observed = terminal["report"].get("observed") if terminal else None
            if terminal and (
                    terminal["report"].get("evidence_complete") is True
                    or (isinstance(observed, dict) and observed.get("events") == len(events))):
                raise ValueError("Request references a candidate absent from complete event records.")
            continue
        event = events[event_index]
        if (event["labels"] != ["matched_candidate"]
                or event["confidence"] != CONFIDENCES[-1]
                or event["stream"]["kind"] != "frame"
                or event["stream"]["follows_corruption"]
                or event["stream"]["offset"] < request["input_boundary"]
                or event["started_at"] is None or event["ended_at"] is None
                or not request["submitted_at"] < event["started_at"] <= event["ended_at"] < request["deadline"]):
            raise ValueError("Delivered request references a non-candidate.")
        packet = protocol.decode_packet(bytes.fromhex(event["stream"]["raw_hex"]))
        protocol.validate_getter_reply(packet, "read-raw-data", request["sequence"])
        if request["status"] != "matched":
            continue
        interpretation = interpret_packet(packet, direction="received", evidence=header["evidence_kind"])
        fields = interpretation["fields"]
        changed = ([] if previous is None else
                   [key for key in fields if fields[key]["raw_hex"] != previous[key]["raw_hex"]])
        repeats = repeats + 1 if previous is not None and not changed else 0
        tick = fields["tick"]["unsigned"]
        if previous is None:
            progression, delta = "first", None
        else:
            prior_tick = previous["tick"]["unsigned"]
            delta = (tick - prior_tick) % (1 << 32)
            progression = ("unchanged" if delta == 0 else
                           "regression_or_large_gap" if delta >= (1 << 31) else
                           "possible_wrap_or_regression" if tick < prior_tick else
                           "forward_raw_counter")
        samples.append({
            "request_index": request["index"], "event_index": event_index,
            "interpretation": interpretation, "changed_fields": changed,
            "identical_payload_run": repeats, "tick_progression": progression,
            "tick_modulo_delta": delta,
            "staleness": ("first_snapshot" if previous is None else
                          "unchanged_raw_payload_not_proof_of_stale_device" if not changed else
                          "raw_payload_changed"),
            "gap_before": bool(samples and request["index"] != samples[-1]["request_index"] + 1),
        })
        previous = fields
    report = terminal["report"] if terminal else None
    if report is not None:
        _validate_report(report, requests, events, samples, contexts, plan)
    return {
        "schema": SCHEMA, "offline_only": True,
        "status": ("incomplete_recording" if terminal is None else
                   "sealed_failed_collection" if report["status"] == "failed" else
                   "sealed_collection_claim_complete"),
        "seal": "prefix_hash_verified_not_authenticated" if terminal else "missing",
        "source_bytes": len(raw), "partial_final_line_bytes": partial_bytes,
        "partial_final_line_hex": partial.hex(),
        "header": header, "report": report, "requests": requests,
        "events": events, "contexts": contexts, "samples": samples,
        "application_acknowledgment": "not_established",
        "limitations": [
            "Stored status, evidence kind, labels, identity and timestamps are unauthenticated declarations.",
            "A hash seal checks file consistency, not durable storage, physical origin or application ACK.",
            "The checksum excludes the terminal record itself; its status/counts are checked claims, not hash-covered proof.",
            "Only status=matched is a delivered correlation candidate; other retained matches are not delivered.",
            "Tick deltas/wrap hypotheses do not establish elapsed device time, watchdog or control-loop timing.",
            "Unchanged raw payloads do not prove staleness, attachment, health, calibration, safe limits or stop.",
            "Battery raw438 is not volts; servo reports are not angles; config27 may be default reports.",
            "Inputs are bounded regular-file snapshots, not protection against hostile concurrent filesystem mutation.",
        ],
    }
