"""Bounded injection-only ReadRawData collection; CLI defaults to an offline plan.

No live adapter, serial imports, devices, reconnects or enabling commands.
"""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from tools.marvin_legacy_client import LegacyClient, Limits, PROFILE, SETTINGS, SessionError, _integer, _number
from tools.marvin_legacy_recording import (
    MAX_FILE_BYTES, MAX_RECORDS, SCHEMA, TERMINAL_BYTES, Recorder, RecordingLimit,
    evidence_row, inspect_recording, request_row,
)


@dataclass(frozen=True)
class PollPlan:
    interval: float = 1.0
    max_requests: int = 10
    duration: float = 30.0
    request_timeout: float = 0.5
    max_lateness: float = 0.05
    cleanup_timeout: float = 1.0
    max_rx_bytes: int = 65536
    max_events: int = 8192
    max_reads: int = 4096
    read_size: int = 512
    max_output_bytes: int = 4 * 1024 * 1024
    max_records: int = MAX_RECORDS
    first_sequence: int = 0

    def __post_init__(self):
        for name, low, high in (
            ("interval", 0.1, 60), ("duration", 0.001, 600),
            ("request_timeout", 0.001, 120), ("max_lateness", 0, 1),
            ("cleanup_timeout", 0.001, 30),
        ):
            _number(name, getattr(self, name), low, high)
        _integer("max_requests", self.max_requests, 1, 256)
        _integer("max_output_bytes", self.max_output_bytes, TERMINAL_BYTES + 4096, MAX_FILE_BYTES)
        _integer("max_records", self.max_records, 2, MAX_RECORDS)
        _integer("first_sequence", self.first_sequence, 0, 65535)
        _integer("max_rx_bytes", self.max_rx_bytes, 144, 1024 * 1024)
        _integer("max_events", self.max_events, 144, 8192)
        self.client_limits()
        if self.first_sequence + self.max_requests > 65536:
            raise ValueError("Polling would reuse/wrap uint16 sequences.")
        if self.request_timeout > self.interval or self.max_lateness >= self.interval:
            raise ValueError("Timeout must fit interval; scheduling tolerance must be smaller than interval.")
        if (self.max_requests - 1) * self.interval + self.max_requests * self.request_timeout >= self.duration:
            raise ValueError("Duration must exceed the nominal schedule including the last request budget.")
        if min(self.max_rx_bytes, self.max_events) < 144:
            raise ValueError("RX/event budgets must reserve a complete 144-byte reply.")

    def client_limits(self):
        return Limits(self.max_requests, self.max_rx_bytes, self.max_events, self.max_reads, self.read_size)

    def to_dict(self):
        return {
            **asdict(self), "rate_hz": 1 / self.interval, "max_outstanding": 1,
            "profile": PROFILE, "query": "read-raw-data", "declared_settings": dict(SETTINGS),
            "limits_meaning": "Software bounds only, not a physically safe rate or applied serial settings.",
        }


@dataclass(frozen=True)
class Collection:
    client: LegacyClient
    report: dict
    output: Path


class CollectionError(RuntimeError):
    def __init__(self, primary, result):
        super().__init__(str(primary))
        self.code = result.report["failure"]["code"]
        self.primary = primary
        self.result = result


def _diagnostic(error):
    code = (error.code if isinstance(error, SessionError) else
            "recording_limit" if isinstance(error, RecordingLimit) else type(error).__name__)
    return {"code": code[:80], "message": str(error)[:256]}


class _Clock:
    def __init__(self, clock):
        self.clock = clock
        self.last = None

    def __call__(self):
        try:
            now = _number("clock", self.clock(), 0, 1e12)
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            raise SessionError("clock_error", str(error)) from error
        if self.last is not None and now < self.last:
            raise SessionError("clock_regressed", "Monotonic clock moved backwards.")
        self.last = now
        return now


def _utc(wall_clock):
    value = wall_clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("wall_clock must return a timezone-aware datetime.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def collect(transport, output, *, ownership_key, expected_identity, plan=PollPlan(),
            evidence_kind="unspecified", clock=time.monotonic, wait=time.sleep,
            wall_clock=lambda: datetime.now(timezone.utc), recorder_factory=Recorder,
            on_evidence=None, on_failure=None, on_sample_persisted=None, on_collection_ended=None):
    """Own one client on the calling thread; raise CollectionError on faults.

    All operational failures retain the primary cause and an in-memory client in
    error.result, including raw evidence unavailable after a persistence failure.
    Injected transport, clocks, wait and recorder must be bounded/cooperative.
    on_sample_persisted receives all current evidence, the operation deadline and
    checked completion time, only after successful sample journal appends.
    on_collection_ended runs before close/finalization, not as a success verdict.
    """
    if not isinstance(plan, PollPlan):
        raise ValueError("plan must be a validated PollPlan.")
    for callback in (on_evidence, on_failure, on_sample_persisted, on_collection_ended):
        if callback is not None and not callable(callback):
            raise ValueError("Observation callbacks must be callable.")
    for name, callback in (("clock", clock), ("wait", wait), ("wall_clock", wall_clock),
                           ("recorder_factory", recorder_factory)):
        if not callable(callback):
            raise ValueError(f"{name} must be callable.")
    timer = _Clock(clock)
    client = LegacyClient(
        transport, ownership_key=ownership_key, expected_identity=expected_identity,
        session_timeout=plan.duration, cleanup_timeout=plan.cleanup_timeout,
        limits=plan.client_limits(), first_sequence=plan.first_sequence,
        clock=timer, evidence_kind=evidence_kind, on_failure=on_failure,
        on_evidence=(lambda: inspect_new()) if on_evidence is not None else None,
    )
    # Both constructors are inert; all argument/path validation precedes I/O.
    recorder = recorder_factory(output, max_bytes=plan.max_output_bytes, max_records=plan.max_records)
    persisted = {"requests": 0, "events": 0}
    observed = {"requests": 0, "events": 0}
    report = {
        "status": "failed", "failure": None, "secondary_errors": [],
        "secondary_errors_omitted": 0,
        "observed": observed, "persisted": persisted, "evidence_complete": False,
        "client_state": "new", "accepted_tx_bytes": 0, "uncertain_tx_bytes": 0,
        "rejected_input_bytes": 0, "requested_snapshots": plan.max_requests,
        "delivered_candidates": 0, "gap": None,
        "application_acknowledgment": "not_established",
        "recording_finalization": "Terminal cannot attest its own subsequent fsync/close outcome.",
    }
    primary = None
    persistence_failed = False
    inspected = 0
    completed = False

    def remember(error):
        nonlocal primary
        if primary is None:
            primary = error
            report["failure"] = _diagnostic(error)
            if on_failure is not None:
                try:
                    on_failure(error)
                except Exception as notification_error:
                    remember(notification_error)
        elif len(report["secondary_errors"]) < 16:
            report["secondary_errors"].append(_diagnostic(error))
        else:
            report["secondary_errors_omitted"] += 1
            primary.add_note("Additional cleanup diagnostics exceeded terminal capacity.")

    def persist():
        nonlocal persistence_failed
        requests, events = client.requests, client.evidence
        observed.update(requests=len(requests), events=len(events))
        if persistence_failed:
            return
        try:
            while persisted["requests"] < len(requests):
                index = persisted["requests"]
                recorder.append(request_row(index, requests[index]))
                persisted["requests"] += 1
            while persisted["events"] < len(events):
                index = persisted["events"]
                recorder.append(evidence_row(index, events[index]))
                persisted["events"] += 1
        except (OSError, ValueError, TypeError, RuntimeError):
            persistence_failed = True
            raise

    def inspect_new():
        nonlocal inspected
        events = client.evidence
        bad = [index for index in range(inspected, len(events))
               if events[index].labels != ("matched_candidate",)]
        new = events[inspected:]
        inspected = len(events)
        if on_evidence is not None:
            on_evidence(new)
        if bad:
            raise SessionError("unexpected_evidence",
                               f"Non-clean client evidence at event {bad[0]}; all batch evidence retained.")

    def context(phase):
        # These are snapshot-time anchors, not TX/RX per-byte UTC timestamps.
        before = timer()
        utc = _utc(wall_clock)
        after = timer()
        recorder.append({"type": "context", "phase": phase, "utc": utc,
                         "monotonic_before": before, "monotonic_after": after})
        return after

    def check(deadline, code="schedule_overrun"):
        now = timer()
        if now >= deadline:
            raise SessionError(code, "Collection/scheduling deadline exceeded; no catch-up or resume.")
        return now

    try:
        # Validate clocks before output creation or transport use.
        context_start = timer()
        utc_start = _utc(wall_clock)
        recorder.open()
        recorder.append({
            "type": "header", "schema": SCHEMA, "profile": PROFILE,
            "evidence_kind": evidence_kind, "plan": plan.to_dict(),
            "utc": utc_start, "monotonic": context_start,
            "application_acknowledgment": "not_established",
            "timing": "UTC/monotonic host snapshot anchors, not per-byte/device time; wall time can regress.",
            "capture_scope": "All client-accepted RX only; no drain, rejected overread bytes or upstream queues.",
        })
        started = timer()
        deadline = started + plan.duration
        client.start()
        target = None
        for index in range(plan.max_requests):
            if target is not None:
                now = check(deadline, "duration")
                if target >= deadline:
                    raise SessionError("duration", "Next slot reaches/exceeds the collection deadline; no wait.")
                if now > target + plan.max_lateness:
                    raise SessionError("schedule_overrun", "Processing missed the next slot; no catch-up.")
                if now < target:
                    outcome = wait(target - now)
                    if outcome is not None:
                        raise SessionError("wait_contract", "wait() must return exactly None.")
                    now = check(deadline, "duration")
                    if now < target:
                        raise SessionError("wait_incomplete", "Wait returned early/frozen; no spin or retry.")
                if now > target + plan.max_lateness:
                    raise SessionError("schedule_overrun", "Wait exceeded scheduling tolerance; no catch-up.")
            context("request")
            begin = timer()
            if target is not None and begin > target + plan.max_lateness:
                raise SessionError("schedule_overrun", "Recording missed the next slot; no catch-up.")
            check(deadline, "duration")
            target = begin + plan.interval
            try:
                client.request("read-raw-data", timeout=plan.request_timeout)
                inspect_new()
            finally:
                # Never let persistence replace a request/identity failure.
                active_error = sys.exc_info()[1]
                if active_error is not None and on_failure is not None:
                    remember(active_error)
                try:
                    persist()
                except (OSError, ValueError, TypeError, RuntimeError) as error:
                    if active_error is None:
                        raise
                    remember(active_error)
                    remember(error)
            check(deadline, "duration")
            finished = check(target)
            if on_sample_persisted is not None:
                on_sample_persisted(client.evidence, deadline, finished)
            target = finished + plan.interval
        completed = True
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        if primary is not error:
            remember(error)
    finally:
        interrupted = sys.exc_info()[1]
        if interrupted is not None and primary is None:
            remember(interrupted)
        if on_collection_ended is not None:
            try:
                on_collection_ended(primary)
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                remember(error)
        finalization_deadline = (timer.last or 0) + plan.cleanup_timeout
        try:
            finalization_deadline = timer() + plan.cleanup_timeout
        except SessionError as error:
            remember(error)
        try:
            client.close()
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            remember(error)
        try:
            inspect_new()
        except SessionError as error:
            remember(error)
        try:
            persist()
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            remember(error)
        for error in client.cleanup_errors:
            remember(SessionError(error.code, error.message))
        report.update(
            client_state=client.state, accepted_tx_bytes=client.accepted_bytes,
            uncertain_tx_bytes=client.uncertain_bytes, rejected_input_bytes=client.rejected_input_bytes,
            evidence_complete=not persistence_failed and persisted == observed,
            delivered_candidates=sum(item.status == "matched" for item in client.requests),
            client_failure=asdict(client.failure) if client.failure else None,
        )
        if primary is None and not (completed and client.state == "closed"):
            remember(SessionError("incomplete", "Collection did not complete and close successfully."))
        if recorder.stream is not None:
            try:
                context("final")
                check(finalization_deadline, "finalization_deadline")
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                remember(error)
            report["status"] = "complete" if primary is None else "failed"
            if primary is not None:
                report["gap"] = {
                    "reason": report["failure"]["code"],
                    "uncollected_snapshots": plan.max_requests - report["delivered_candidates"],
                    "resume_permitted": False,
                }
            try:
                recorder.finish(report)
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                remember(error)
        try:
            recorder.close()
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            remember(error)
        try:
            check(finalization_deadline, "finalization_deadline")
        except SessionError as error:
            remember(error)
        report["status"] = "complete" if primary is None else "failed"
        report["recording_sealed"] = recorder.sealed
        report["recording_bytes"] = recorder.bytes_written
        if primary is not None:
            report["gap"] = {
                "reason": report["failure"]["code"],
                "uncollected_snapshots": plan.max_requests - report["delivered_candidates"],
                "resume_permitted": False,
            }
    result = Collection(client, report, recorder.path)
    if primary is not None:
        raise CollectionError(primary, result) from primary
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--synthetic", action="store_true", help="In-memory synthetic record; never observations")
    mode.add_argument("--replay", help="Inspect a finished local recording offline")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="Inspect an unsealed/partial recording explicitly as incomplete")
    parser.add_argument("--output", help="New private JSONL file (synthetic mode only)")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-requests", type=int, default=10)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--request-timeout", type=float, default=0.5)
    parser.add_argument("--max-lateness", type=float, default=0.05)
    parser.add_argument("--max-output-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--max-input-bytes", type=int, default=MAX_FILE_BYTES)
    parser.add_argument("--max-records", type=int, default=MAX_RECORDS)
    args = parser.parse_args(argv)
    try:
        if args.allow_incomplete and not args.replay:
            raise ValueError("--allow-incomplete requires --replay.")
        if args.output and not args.synthetic:
            raise ValueError("--output requires explicit --synthetic; default plan creates nothing.")
        plan = PollPlan(
            interval=args.interval, max_requests=args.max_requests, duration=args.duration,
            request_timeout=args.request_timeout, max_lateness=args.max_lateness,
            max_output_bytes=args.max_output_bytes, max_records=args.max_records,
        )
        if args.replay:
            result = inspect_recording(args.replay, max_bytes=args.max_input_bytes, max_records=args.max_records,
                                       allow_incomplete=args.allow_incomplete)
        elif args.synthetic:
            from tools.marvin_legacy_client_example import SyntheticTransport
            transport = SyntheticTransport()

            def synthetic_wait(seconds):
                transport.now += seconds

            result = collect(
                transport, args.output, ownership_key=b"synthetic-poll",
                expected_identity=b"synthetic-example-generation-1", plan=plan,
                clock=transport.clock, wait=synthetic_wait, evidence_kind="synthetic",
            ).report
        else:
            result = {"status": "offline_plan", "offline_only": True, "plan": plan.to_dict(),
                      "transport_accessed": False, "output_created": False}
    except CollectionError as error:
        print(json.dumps(error.result.report, indent=2, allow_nan=False))
        return 2
    except (OSError, ValueError, TypeError, RecursionError) as error:
        print(json.dumps({"status": "input_error", "offline_only": True, "error": str(error)[:1024]}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["status"] not in ("incomplete_recording", "sealed_failed_collection", "failed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
