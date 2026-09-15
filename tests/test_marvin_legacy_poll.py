from contextlib import redirect_stdout
from dataclasses import fields
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_legacy_client as client
from tools import marvin_legacy_poll as poll
from tools import marvin_legacy_protocol as protocol
from tools import marvin_legacy_recording as recording
from tests.test_marvin_legacy_client import Clock, FakeTransport, frame
from tests.test_marvin_legacy_telemetry import expected_raw_fields


UTC = datetime(2026, 9, 15, 12, 30, tzinfo=timezone.utc)


def raw_reply(payload=None, *, sequence=0, **options):
    return frame(bytes(134) if payload is None else payload,
                 command=0, sequence=sequence, **options)


class PollTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".poll-tests-", dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.serial = 0
        self.fresh()

    def fresh(self):
        self.serial += 1
        self.output = self.directory / f"synthetic-{self.serial}.jsonl"
        self.clock = Clock()
        self.transport = FakeTransport(self.clock)
        self.waits = []

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.now += seconds

    def collect(self, *, plan=None, **options):
        arguments = {
            "ownership_key": b"synthetic-poll-tests",
            "expected_identity": b"synthetic-connection-generation-1",
            "plan": poll.PollPlan(max_requests=3, duration=10, request_timeout=0.2) if plan is None else plan,
            "evidence_kind": "synthetic",
            "clock": self.clock, "wait": self.wait, "wall_clock": lambda: UTC,
        }
        arguments.update(options)
        return poll.collect(self.transport, self.output, **arguments)

    def failure(self, code=None, *, writes=1, **options):
        with self.assertRaises(poll.CollectionError) as raised:
            self.collect(**options)
        error = raised.exception
        report = error.result.report
        if code is not None:
            self.assertEqual(error.code, code)
            self.assertEqual(report["failure"]["code"], code)
        self.assertIs(error.__cause__, error.primary)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(self.transport.writes), writes)
        self.assertLessEqual(sum(name == "revalidate" for name, _ in self.transport.calls), 1)
        self.assertLessEqual(sum(name == "close" for name, _ in self.transport.calls), 1)
        self.assertFalse(report["gap"]["resume_permitted"])
        self.assertEqual(report["gap"]["uncollected_snapshots"],
                         report["requested_snapshots"] - report["delivered_candidates"])
        with self.assertRaises(client.SessionError):
            error.result.client.start()
        with self.assertRaises(client.SessionError):
            error.result.client.request("read-raw-data", timeout=0.2)
        self.assertEqual(len(self.transport.writes), writes)
        return error

    def assert_raw(self, result, raw):
        events = result.client.evidence
        self.assertEqual(b"".join(item.stream.raw for item in events), raw)
        offset = 0
        for index, item in enumerate(events):
            self.assertEqual(item.stream.offset, offset)
            offset = item.stream.end_offset
            self.assertEqual(offset, sum(len(event.stream.raw) for event in events[:index + 1]))
            self.assertEqual(item.profile, "marvin-legacy-se")
            self.assertEqual(item.evidence_kind, "synthetic")
            self.assertEqual(item.application_acknowledgment, "not_established")
        self.assertEqual(offset, len(raw))

    def replay(self):
        return recording.inspect_recording(self.output)

    def assert_persisted_events(self, result):
        replay = self.replay()
        self.assertEqual(len(replay["events"]), len(result.client.evidence))
        for row, event in zip(replay["events"], result.client.evidence):
            self.assertEqual(row["stream"]["raw_hex"], event.stream.raw.hex())
            self.assertEqual(row["stream"]["offset"], event.stream.offset)
            self.assertEqual(row["stream"]["end_offset"], event.stream.end_offset)
            self.assertEqual(row["labels"], list(event.labels))
            for name in ("started_at", "ended_at", "profile", "confidence",
                         "evidence_kind", "application_acknowledgment"):
                self.assertEqual(row[name], getattr(event, name))
        return replay


class PollCollectionTests(PollTestCase):
    def test_multiple_snapshots_share_one_client_and_preserve_exact_getter_evidence(self):
        result = self.collect(plan=poll.PollPlan(max_requests=3, first_sequence=37))
        self.assertIsInstance(result, poll.Collection)
        self.assertEqual(result.output, self.output)
        self.assertEqual(result.client.state, "closed")
        self.assertEqual(result.report["status"], "complete")
        self.assertEqual(result.report["observed"], {"requests": 3, "events": 3})
        self.assertEqual(result.report["persisted"], result.report["observed"])
        self.assertTrue(result.report["evidence_complete"])
        self.assertEqual(result.report["delivered_candidates"], 3)
        self.assertEqual((result.report["accepted_tx_bytes"], result.report["uncertain_tx_bytes"]), (30, 0))
        self.assertEqual(self.transport.writes,
                         [protocol.GETTERS["read-raw-data"].encode(index) for index in range(37, 40)])
        self.assertEqual(sum(name == "revalidate" for name, _ in self.transport.calls), 1)
        self.assertEqual(sum(name == "close" for name, _ in self.transport.calls), 1)
        self.assertEqual(len(self.waits), 2)
        self.assert_raw(result, b"".join(raw_reply(sequence=index) for index in range(37, 40)))
        replay = self.assert_persisted_events(result)
        self.assertEqual(replay["status"], "sealed_collection_claim_complete")
        self.assertEqual(replay["source_bytes"], self.output.stat().st_size)
        self.assertEqual(result.report["recording_bytes"], replay["source_bytes"])
        for index, request in enumerate(result.client.requests):
            self.assertEqual(request.status, "matched")
            self.assertEqual(request.reply_event, index)
            self.assertEqual(result.client.evidence[index].labels, ("matched_candidate",))
            self.assertEqual(result.client.evidence[index].confidence,
                             "integrity_and_shape_match_not_authenticated")

    def test_full_interval_follows_request_and_persistence_not_previous_start(self):
        completion_times = []
        starts = []
        clock = self.clock

        class SlowRecorder(recording.Recorder):
            def append(self, row):
                super().append(row)
                if row["type"] == "evidence":
                    clock.now += 0.2
                    completion_times.append(clock.now)

        def reply(packet):
            starts.append(clock.now - 0.01)
            self.transport.enqueue(raw_reply(sequence=packet.sequence), at=clock.now + 0.15)

        self.transport.on_write = reply
        self.collect(recorder_factory=SlowRecorder)
        self.assertEqual(len(starts), 3)
        for previous_finish, next_start in zip(completion_times, starts[1:]):
            self.assertAlmostEqual(next_start - previous_finish, 1.0)
        for seconds in self.waits:
            self.assertAlmostEqual(seconds, 1.0)

    def test_persistence_overrun_stops_without_catchup_even_with_lateness_tolerance(self):
        for elapsed in (1.0, 1.5):
            with self.subTest(elapsed=elapsed):
                self.fresh()
                clock = self.clock

                class SlowRecorder(recording.Recorder):
                    def append(self, row):
                        super().append(row)
                        if row["type"] == "evidence":
                            clock.now += elapsed

                error = self.failure("schedule_overrun", recorder_factory=SlowRecorder)
                self.assertEqual(self.waits, [])
                self.assert_raw(error.result, raw_reply())
                self.assertEqual(self.replay()["status"], "sealed_failed_collection")

    def test_early_frozen_regressed_late_and_invalid_wait_stop_after_one_call(self):
        for behavior, code in (
            ("early", "wait_incomplete"), ("frozen", "wait_incomplete"),
            ("regressed", "clock_regressed"), ("late", "schedule_overrun"),
            ("return_value", "wait_contract"), ("exception", "OSError"),
            ("duration", "duration"),
        ):
            with self.subTest(behavior=behavior):
                self.fresh()
                calls = []

                def wait(seconds):
                    calls.append(seconds)
                    if behavior == "exception":
                        raise OSError("injected wait failure")
                    if behavior == "return_value":
                        return False
                    self.clock.now += {
                        "early": seconds / 2, "frozen": 0, "regressed": -1,
                        "late": seconds + 0.1, "duration": 20,
                    }[behavior]

                error = self.failure(code, wait=wait)
                self.assertEqual(len(calls), 1)
                self.assert_raw(error.result, raw_reply())

    def test_context_persistence_cannot_hide_missed_slot(self):
        contexts = 0
        clock = self.clock

        class DelayedContextRecorder(recording.Recorder):
            def append(self, row):
                nonlocal contexts
                super().append(row)
                if row["type"] == "context" and row["phase"] == "request":
                    contexts += 1
                    if contexts == 2:
                        clock.now += 0.1

        self.failure("schedule_overrun", recorder_factory=DelayedContextRecorder)
        self.assertEqual(len(self.waits), 1)

    def test_next_slot_at_or_after_duration_is_rejected_before_waiting(self):
        for duration in (1.75, 1.6):
            with self.subTest(duration=duration):
                self.fresh()
                clock = self.clock
                self.transport.on_write = lambda packet: self.transport.enqueue(raw_reply(), at=10.125)

                class SlowRecorder(recording.Recorder):
                    def append(self, row):
                        super().append(row)
                        if row["type"] == "evidence":
                            clock.now += 0.625

                plan = poll.PollPlan(max_requests=2, request_timeout=0.25, duration=duration)
                error = self.failure("duration", plan=plan, recorder_factory=SlowRecorder)
                self.assertEqual(self.waits, [])
                self.assertEqual(clock.now, 10.75)
                self.assert_raw(error.result, raw_reply())
                self.assertEqual(self.replay()["status"], "sealed_failed_collection")

    def test_every_frame_fragment_boundary_and_single_byte_reads(self):
        raw = raw_reply(bytes(range(134)))
        for split in range(1, len(raw)):
            with self.subTest(split=split):
                self.fresh()
                self.transport.on_write = lambda packet: (
                    self.transport.enqueue(raw[:split]), self.transport.enqueue(raw[split:]))
                result = self.collect(plan=poll.PollPlan(max_requests=1))
                self.assert_raw(result, raw)
                self.assertEqual(len(result.client.evidence), 1)
        self.fresh()
        result = self.collect(plan=poll.PollPlan(max_requests=1, read_size=1))
        self.assert_raw(result, raw_reply())
        self.assertEqual(sum(name == "read" for name, _ in self.transport.calls), 144)

    def test_coalesced_unknown_error_duplicate_echo_and_future_after_candidate_stop_polling(self):
        extras = (
            (frame(b"opaque", command=250, sequence=9), "unsolicited"),
            (raw_reply(status=0x81), "error_status"),
            (raw_reply(), "duplicate"),
            (protocol.GETTERS["read-raw-data"].encode(0), "request_echo"),
            (raw_reply(sequence=1), "unsolicited"),
        )
        for extra, label in extras:
            with self.subTest(label=label, extra=extra.hex()):
                self.fresh()
                raw = raw_reply() + extra
                self.transport.on_write = lambda packet: self.transport.enqueue(raw)
                error = self.failure("unexpected_evidence")
                self.assert_raw(error.result, raw)
                self.assertIn(label, error.result.client.evidence[-1].labels)
                self.assertEqual(error.result.report["delivered_candidates"], 1)
                replay = self.assert_persisted_events(error.result)
                self.assertEqual(replay["status"], "sealed_failed_collection")
                self.assertEqual(len(replay["samples"]), 1)

    def test_corrupt_crc_footer_length_noise_and_truncation_are_preserved_not_delivered(self):
        good = raw_reply()
        crc, footer, length = bytearray(good), bytearray(good), bytearray(good)
        crc[-3] ^= 1
        footer[-1] = 0
        length[5:7] = b"\xff\xff"
        for raw, label in (
            (bytes(crc) + good, "malformed"), (bytes(footer) + good, "malformed"),
            (bytes(length) + good, "malformed"), (b"noise" + good, "noise"),
            (good[:-1], "partial"), (good[:6], "partial"),
        ):
            with self.subTest(label=label, prefix=raw[:8].hex()):
                self.fresh()
                self.transport.on_write = lambda packet: self.transport.enqueue(raw)
                error = self.failure("deadline")
                self.assert_raw(error.result, raw)
                self.assertTrue(any(label in item.labels for item in error.result.client.evidence))
                self.assertFalse(any("matched_candidate" in item.labels for item in error.result.client.evidence))
                self.assertEqual(error.result.report["delivered_candidates"], 0)
                self.assertEqual(self.assert_persisted_events(error.result)["samples"], [])

    def test_partial_tail_after_final_candidate_is_finalized_and_recorded_as_failure(self):
        raw = raw_reply() + b"S\x00"
        self.transport.on_write = lambda packet: self.transport.enqueue(raw)
        error = self.failure("unexpected_evidence", plan=poll.PollPlan(max_requests=1))
        self.assert_raw(error.result, raw)
        self.assertIn("partial", error.result.client.evidence[-1].labels)
        self.assertEqual(error.result.report["observed"], {"requests": 1, "events": 2})
        self.assert_persisted_events(error.result)

    def test_stale_reply_from_previous_request_is_retained_but_never_reused(self):
        raw = raw_reply()

        def reply(packet):
            self.transport.enqueue(raw)
            if packet.sequence == 1:
                self.transport.enqueue(raw_reply(sequence=1))

        self.transport.on_write = reply
        error = self.failure("unexpected_evidence", writes=2)
        self.assert_raw(error.result, raw + raw + raw_reply(sequence=1))
        self.assertTrue({"stale", "duplicate"} <= set(error.result.client.evidence[1].labels))
        self.assertEqual(error.result.client.requests[1].reply_event, 2)
        self.assertEqual(len(self.assert_persisted_events(error.result)["samples"]), 2)

    def test_pre_request_and_late_ingestion_cannot_be_delivered(self):
        for at, label in ((10.0, "pre_request"), (10.01, "pre_request"), (10.2, "late")):
            with self.subTest(at=at):
                self.fresh()
                self.transport.on_write = lambda packet: self.transport.enqueue(raw_reply(), at=at)
                error = self.failure("deadline")
                self.assert_raw(error.result, raw_reply())
                self.assertIn(label, error.result.client.evidence[0].labels)
                self.assertEqual(self.assert_persisted_events(error.result)["samples"], [])

    def test_invalid_ingestion_times_retain_unverified_raw_evidence(self):
        for start, end in (
            (True, 10.02), (float("nan"), 10.02), (10.02, float("inf")),
            (10**1000, 10.02), (10.03, 10.02), (11, 11),
        ):
            with self.subTest(start_type=type(start).__name__, end=end):
                self.fresh()
                self.transport.on_read = lambda size, deadline: client.Received(raw_reply(), start, end)
                error = self.failure("adapter_timing")
                self.assert_raw(error.result, raw_reply())
                event = error.result.client.evidence[0]
                self.assertEqual(event.labels, ("unverified",))
                self.assertEqual(event.confidence, "raw_observation")
                self.assertEqual(error.result.report["delivered_candidates"], 0)
                self.assertEqual(self.assert_persisted_events(error.result)["samples"], [])

    def test_disconnect_after_partial_input_keeps_raw_and_never_reconnects(self):
        raw = b"S\x00\x00"
        self.transport.on_write = lambda packet: self.transport.enqueue(raw)

        def read(size, deadline):
            if self.transport.incoming:
                chunk = self.transport.incoming.pop()
                self.clock.now = chunk.ended_at
                return chunk
            raise OSError("synthetic transport disconnected")

        self.transport.on_read = read
        error = self.failure("transport_error")
        self.assert_raw(error.result, raw)
        self.assertIn("partial", error.result.client.evidence[0].labels)
        self.assert_persisted_events(error.result)

    def test_identity_loss_at_start_write_and_after_candidate_never_reconnects(self):
        for stage in ("start", "write", "read", "lost"):
            with self.subTest(stage=stage):
                self.fresh()
                if stage == "start":
                    self.transport.token = b"another-generation"
                elif stage == "write":
                    self.transport.on_write = lambda packet: setattr(
                        self.transport, "token", b"another-generation")
                else:
                    def identity():
                        if self.transport.calls[-2][0] == "read":
                            if stage == "lost":
                                raise OSError("identity unavailable")
                            self.transport.token = b"another-generation"
                    self.transport.on_identity = identity
                error = self.failure("transport_error" if stage == "lost" else "identity_changed",
                                     writes=0 if stage == "start" else 1)
                self.assertEqual(error.result.report["delivered_candidates"], 0)
                if stage in ("read", "lost"):
                    self.assert_raw(error.result, raw_reply())
                    self.assertEqual(error.result.client.evidence[0].labels, ("matched_candidate",))
                    self.assertEqual(error.result.client.requests[0].status, "failed")
                    self.assertEqual(self.assert_persisted_events(error.result)["samples"], [])

    def test_short_uncertain_and_exception_writes_keep_exact_accounting_without_retry(self):
        for count in (0, 1, 9, None, True, 1.0, -1, 11, 10**1000):
            with self.subTest(count_type=type(count).__name__, valid=count in (0, 1, 9)):
                self.fresh()
                self.transport.count = count
                valid = type(count) is int and 0 <= count < 10
                error = self.failure("short_write" if valid else "uncertain_write")
                accepted = count if valid else 0
                self.assertEqual(error.result.report["accepted_tx_bytes"], accepted)
                self.assertEqual(error.result.report["uncertain_tx_bytes"], 10 - accepted)
                self.assertEqual(error.result.client.evidence, ())
                self.assertFalse(any(name == "read" for name, _ in self.transport.calls))
                self.assertEqual(self.replay()["samples"], [])
        self.fresh()

        def fail_write(packet):
            raise OSError("write disconnected")

        self.transport.on_write = fail_write
        error = self.failure("transport_error")
        self.assertEqual((error.result.report["accepted_tx_bytes"],
                          error.result.report["uncertain_tx_bytes"]), (0, 10))

    def test_slow_write_and_slow_read_are_not_successful_requests(self):
        for stage in ("write", "read"):
            with self.subTest(stage=stage):
                self.fresh()
                if stage == "write":
                    self.transport.on_write = lambda packet: setattr(self.clock, "now", 10.2)
                else:
                    def late_read(size, deadline):
                        self.clock.now = deadline
                        return client.Received(raw_reply(), 10.02, 10.02)
                    self.transport.on_read = late_read
                error = self.failure("deadline")
                self.assertEqual(error.result.report["delivered_candidates"], 0)
                if stage == "read":
                    self.assert_raw(error.result, raw_reply())
                    self.assertIn("late", error.result.client.evidence[0].labels)
                else:
                    self.assertFalse(any(name == "read" for name, _ in self.transport.calls))

    def test_frozen_empty_reads_exhaust_call_budget_without_spinning_forever(self):
        self.transport.on_write = None
        self.transport.empty_advances = False
        self.failure("read_limit", plan=poll.PollPlan(max_requests=3, max_reads=3))
        self.assertEqual(sum(name == "read" for name, _ in self.transport.calls), 3)
        self.assertEqual(self.clock.now, 10.01)

    def test_rx_and_event_reservations_prevent_next_write(self):
        for limits in ({"max_rx_bytes": 144}, {"max_events": 144}):
            with self.subTest(limits=limits):
                self.fresh()
                error = self.failure("evidence_limit", plan=poll.PollPlan(max_requests=2, **limits))
                self.assert_raw(error.result, raw_reply())
                self.assertEqual(error.result.report["observed"], {"requests": 1, "events": 1})
                self.assertEqual(len(self.replay()["samples"]), 1)

    def test_overread_bytes_are_counted_as_rejected_not_misrepresented_as_retained(self):
        self.transport.on_read = lambda size, deadline: client.Received(
            b"x" * (size + 1), self.clock.now, self.clock.now)
        error = self.failure("adapter_overread")
        self.assertEqual(error.result.report["rejected_input_bytes"], 513)
        self.assertEqual(error.result.client.evidence, ())
        self.assertEqual(self.replay()["events"], [])

    def test_cleanup_failure_and_late_close_keep_primary_cause_and_close_once(self):
        for short_write in (False, True):
            for late in (False, True):
                with self.subTest(short_write=short_write, late=late):
                    self.fresh()
                    if short_write:
                        self.transport.count = 3

                    def close():
                        if late:
                            self.clock.now += 2
                        else:
                            raise OSError("synthetic close failed")

                    self.transport.on_close = close
                    error = self.failure("short_write" if short_write else "cleanup_failed",
                                         writes=1 if short_write else 3)
                    diagnostics = error.result.report["secondary_errors"]
                    self.assertIn("close_deadline" if late else "close_error",
                                  [item["code"] for item in diagnostics])
                    self.assertEqual(sum(name == "close" for name, _ in self.transport.calls), 1)
                    self.assertEqual(self.replay()["status"], "sealed_failed_collection")

    def test_invalid_clock_before_io_and_during_wait_is_fail_closed(self):
        for value in (True, None, -1, float("nan"), float("inf"), 10**1000):
            for during_wait in (False, True):
                with self.subTest(value_type=type(value).__name__, during_wait=during_wait):
                    self.fresh()
                    if during_wait:
                        def wait(seconds):
                            self.clock.now = value
                        error = self.failure("clock_error", wait=wait)
                        self.assert_raw(error.result, raw_reply())
                    else:
                        self.clock.now = value
                        self.failure("clock_error", writes=0)
                        self.assertFalse(self.output.exists())
                        self.assertEqual(self.transport.calls, [])

    def test_clock_failure_after_read_keeps_accepted_bytes_unverified_and_undelivered(self):
        for value, code in ((9, "clock_regressed"), (float("nan"), "clock_error")):
            with self.subTest(value=value):
                self.fresh()

                def read(size, deadline):
                    self.clock.now = value
                    return client.Received(raw_reply(), 10.02, 10.02)

                self.transport.on_read = read
                error = self.failure(code)
                self.assert_raw(error.result, raw_reply())
                self.assertEqual(error.result.client.evidence[0].labels, ("unverified",))
                self.assertEqual(error.result.report["delivered_candidates"], 0)
                self.assertEqual(sum(name == "close" for name, _ in self.transport.calls), 1)
                self.assertEqual(self.assert_persisted_events(error.result)["samples"], [])

    def test_utc_requires_aware_datetime_before_output_or_transport(self):
        for value in (None, "2026-09-15T12:30:00Z", datetime(2026, 9, 15)):
            with self.subTest(value=value):
                self.fresh()
                self.failure("ValueError", writes=0, wall_clock=lambda: value)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.transport.calls, [])

    def test_utc_normalizes_offset_but_regressing_wall_time_does_not_drive_schedule(self):
        calls = []

        def wall_clock():
            value = UTC.astimezone(timezone(timedelta(hours=-7))) - timedelta(days=len(calls))
            calls.append(value)
            return value

        self.collect(wall_clock=wall_clock)
        replay = self.replay()
        anchors = [replay["header"]["utc"]] + [row["utc"] for row in replay["contexts"]]
        self.assertEqual(anchors, [value.astimezone(timezone.utc).isoformat(timespec="microseconds")
                                   for value in calls])
        self.assertTrue(all(value.endswith("+00:00") for value in anchors))
        self.assertTrue(all(left > right for left, right in zip(anchors, anchors[1:])))
        self.assertEqual(len(self.waits), 2)

    def test_changing_identical_wrap_and_regressing_ticks_keep_raw_82_field_views(self):
        payloads = []
        for tick, battery in ((0xFFFFFFFE, 438), (0xFFFFFFFE, 438),
                              (1, 439), (0, 439), (2, 439)):
            payload = bytearray(134)
            payload[:4] = tick.to_bytes(4, "little")
            payload[36:38] = battery.to_bytes(2, "little")
            payload[50:52] = b"\x00\x80"
            payloads.append(bytes(payload))
        self.transport.on_write = lambda packet: self.transport.enqueue(
            raw_reply(payloads[packet.sequence], sequence=packet.sequence))
        result = self.collect(plan=poll.PollPlan(max_requests=len(payloads)))
        replay = self.replay()
        self.assertEqual([item["tick_progression"] for item in replay["samples"]],
                         ["first", "unchanged", "possible_wrap_or_regression",
                          "regression_or_large_gap", "forward_raw_counter"])
        self.assertEqual([item["tick_modulo_delta"] for item in replay["samples"]],
                         [None, 0, 3, 0xFFFFFFFF, 2])
        self.assertEqual([item["identical_payload_run"] for item in replay["samples"]], [0, 1, 0, 0, 0])
        self.assertEqual(replay["samples"][1]["staleness"],
                         "unchanged_raw_payload_not_proof_of_stale_device")
        self.assertEqual(set(replay["samples"][2]["changed_fields"]), {"tick", "batteryVoltage"})
        for sample, payload in zip(replay["samples"], payloads):
            interpretation = sample["interpretation"]
            self.assertEqual(interpretation["profile"], "pctestapp-raw-data-134")
            self.assertEqual(interpretation["evidence_kind"], "synthetic")
            self.assertEqual(interpretation["application_acknowledgment"], "not_established")
            self.assertEqual(len(interpretation["fields"]), 82)
            for name, (offset, size) in expected_raw_fields().items():
                field = interpretation["fields"][name]
                self.assertEqual(field["raw_hex"], payload[offset:offset + size].hex())
                self.assertEqual(field["unsigned"], int.from_bytes(payload[offset:offset + size], "little"))
                self.assertNotIn("units", field)
                self.assertNotIn("scaled", field)
            self.assertEqual(interpretation["fields"]["accelX"]["signed"], -32768)
        self.assert_raw(result, b"".join(raw_reply(payload, sequence=index)
                                       for index, payload in enumerate(payloads)))


class PollRecordingFaultTests(PollTestCase):
    def test_append_and_serialization_failures_keep_unpersisted_raw_in_memory(self):
        for kind in ("request", "evidence"):
            for serialize in (False, True):
                with self.subTest(kind=kind, serialize=serialize):
                    self.fresh()
                    injected = OSError("injected append failure")

                    class FaultRecorder(recording.Recorder):
                        def append(self, row):
                            if row["type"] == kind:
                                if serialize:
                                    return super().append({**row, "unserializable": object()})
                                raise injected
                            return super().append(row)

                    error = self.failure("TypeError" if serialize else "OSError",
                                         recorder_factory=FaultRecorder)
                    if not serialize:
                        self.assertIs(error.primary, injected)
                    self.assert_raw(error.result, raw_reply())
                    self.assertFalse(error.result.report["evidence_complete"])
                    self.assertEqual(error.result.report["observed"], {"requests": 1, "events": 1})
                    self.assertEqual(error.result.report["persisted"]["events"], 0)
                    self.assertEqual(self.replay()["status"], "sealed_failed_collection")

    def test_request_failure_remains_primary_when_persistence_and_close_also_fail(self):
        class FaultRecorder(recording.Recorder):
            def append(self, row):
                if row["type"] == "request":
                    raise OSError("secondary append failure")
                super().append(row)

            def close(self):
                super().close()
                raise OSError("secondary recorder close failure")

        self.transport.count = 3
        self.transport.on_close = lambda: (_ for _ in ()).throw(OSError("secondary transport close failure"))
        error = self.failure("short_write", recorder_factory=FaultRecorder)
        messages = [item["message"] for item in error.result.report["secondary_errors"]]
        for expected in ("secondary append failure", "secondary recorder close failure",
                         "secondary transport close failure"):
            self.assertIn(expected, messages)
        self.assertEqual(error.result.report["accepted_tx_bytes"], 3)
        self.assertEqual(error.result.report["uncertain_tx_bytes"], 7)

    def test_read_failure_remains_primary_when_partial_raw_cannot_be_persisted(self):
        raw = b"S\x00\x00"
        self.transport.on_write = lambda packet: self.transport.enqueue(raw)

        def read(size, deadline):
            if self.transport.incoming:
                chunk = self.transport.incoming.pop()
                self.clock.now = chunk.ended_at
                return chunk
            raise OSError("primary disconnected read")

        class FaultRecorder(recording.Recorder):
            def append(self, row):
                if row["type"] == "evidence":
                    raise OSError("secondary evidence write failure")
                super().append(row)

        self.transport.on_read = read
        error = self.failure("transport_error", recorder_factory=FaultRecorder)
        self.assertIn("primary disconnected read", str(error.primary))
        self.assertIn("secondary evidence write failure",
                      [item["message"] for item in error.result.report["secondary_errors"]])
        self.assert_raw(error.result, raw)
        self.assertFalse(error.result.report["evidence_complete"])
        self.assertEqual(error.result.report["persisted"], {"requests": 1, "events": 0})
        self.assertEqual(error.result.report["observed"], {"requests": 1, "events": 1})
        self.assertEqual(self.replay()["status"], "sealed_failed_collection")

    def test_recording_diagnostics_are_bounded_without_discarding_primary_exception(self):
        injected = OSError("\U0001f600" * 100_000)

        class FaultRecorder(recording.Recorder):
            def append(self, row):
                if row["type"] == "evidence":
                    raise injected
                super().append(row)

        error = self.failure("OSError", recorder_factory=FaultRecorder)
        self.assertIs(error.primary, injected)
        self.assertEqual(error.result.report["failure"]["message"], "\U0001f600" * 256)
        self.assert_raw(error.result, raw_reply())
        self.assertLessEqual(self.output.stat().st_size, poll.PollPlan().max_output_bytes)
        self.assertEqual(self.replay()["status"], "sealed_failed_collection")

    def test_short_and_uncertain_output_writes_poison_recorder_without_success_seal(self):
        for count in (0, 7, None, True, -1):
            with self.subTest(count=count):
                self.fresh()
                instances = []

                class ShortStream:
                    def __init__(self, stream):
                        self.stream = stream

                    def write(self, data):
                        if type(count) is int and 0 <= count <= len(data):
                            self.stream.write(data[:count])
                        return count

                    def close(self):
                        self.stream.close()

                    def fileno(self):
                        return self.stream.fileno()

                class ShortRecorder(recording.Recorder):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        instances.append(self)

                    def append(self, row):
                        if row["type"] == "evidence":
                            self.stream = ShortStream(self.stream)
                        super().append(row)

                error = self.failure("OSError", recorder_factory=ShortRecorder)
                self.assertIn("Short/uncertain", str(error.primary))
                self.assert_raw(error.result, raw_reply())
                self.assertTrue(instances[0].broken)
                self.assertFalse(error.result.report["recording_sealed"])
                self.assertFalse(error.result.report["evidence_complete"])
                self.assertEqual(error.result.report["recording_bytes"], self.output.stat().st_size)
                with self.assertRaises(ValueError):
                    self.replay()
                replay = recording.inspect_recording(self.output, allow_incomplete=True)
                self.assertEqual(replay["status"], "incomplete_recording")
                self.assertEqual(replay["partial_final_line_bytes"], count if type(count) is int and count >= 0 else 0)
                self.assertEqual(replay["events"], [])

    def test_finish_failure_preserves_raw_and_explicit_incomplete_inspection(self):
        injected = OSError("injected terminal failure")

        class FinishRecorder(recording.Recorder):
            def finish(self, report):
                raise injected

        error = self.failure("OSError", writes=3, recorder_factory=FinishRecorder)
        self.assertIs(error.primary, injected)
        self.assertTrue(error.result.report["evidence_complete"])
        self.assertFalse(error.result.report["recording_sealed"])
        self.assert_raw(error.result, b"".join(raw_reply(sequence=index) for index in range(3)))
        with self.assertRaises(ValueError):
            self.replay()
        replay = recording.inspect_recording(self.output, allow_incomplete=True)
        self.assertEqual(replay["status"], "incomplete_recording")
        self.assertEqual(len(replay["samples"]), 3)

    def test_fsync_failure_does_not_erase_evidence_or_attest_durable_success(self):
        with patch.object(recording.os, "fsync", side_effect=OSError("injected fsync failure")) as fsync:
            error = self.failure("OSError", writes=3)
        fsync.assert_called_once()
        self.assertFalse(error.result.report["recording_sealed"])
        self.assertTrue(error.result.report["evidence_complete"])
        self.assert_raw(error.result, b"".join(raw_reply(sequence=index) for index in range(3)))
        replay = self.replay()
        self.assertEqual(replay["status"], "sealed_collection_claim_complete")
        self.assertIn("cannot attest", replay["report"]["recording_finalization"])
        self.assertNotIn("recording_sealed", replay["report"])

    def test_direct_recorder_cannot_resume_after_fsync_failure_or_interruption(self):
        for fault in (OSError("injected fsync failure"), KeyboardInterrupt()):
            with self.subTest(fault=type(fault).__name__):
                self.fresh()
                recorder = recording.Recorder(self.output, max_bytes=poll.PollPlan().max_output_bytes)
                recorder.open()
                try:
                    recorder.append({"type": "header"})
                    with patch.object(recording.os, "fsync", side_effect=fault) as fsync:
                        with self.assertRaises(type(fault)):
                            recorder.finish({"status": "failed"})
                    fsync.assert_called_once()
                    before = self.output.read_bytes()
                    self.assertTrue(recorder.broken)
                    self.assertFalse(recorder.sealed)
                    with self.assertRaises(OSError):
                        recorder.append({"type": "after-terminal"})
                    with self.assertRaises(OSError):
                        recorder.finish({"status": "failed"})
                    self.assertEqual(self.output.read_bytes(), before)
                    self.assertEqual(recorder.bytes_written, len(before))
                    self.assertEqual(recorder.records, 2)
                finally:
                    recorder.close()

    def test_recorder_close_error_and_late_close_are_reported_after_preserving_seal(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.fresh()
                clock = self.clock
                closes = []

                class CloseRecorder(recording.Recorder):
                    def close(self):
                        closes.append(True)
                        super().close()
                        if late:
                            clock.now += 2
                        else:
                            raise OSError("recorder close failed")

                error = self.failure("finalization_deadline" if late else "OSError",
                                     writes=3, recorder_factory=CloseRecorder)
                self.assertEqual(closes, [True])
                self.assertTrue(error.result.report["recording_sealed"])
                self.assertEqual(error.result.report["recording_bytes"], self.output.stat().st_size)
                self.assertEqual(self.replay()["status"], "sealed_collection_claim_complete")

    def test_finalization_budget_is_shared_between_transport_close_and_final_context(self):
        clock = self.clock
        self.transport.on_close = lambda: setattr(clock, "now", clock.now + 0.6)

        class BudgetRecorder(recording.Recorder):
            def append(self, row):
                super().append(row)
                if row["type"] == "context" and row["phase"] == "final":
                    clock.now += 0.6

        error = self.failure("finalization_deadline", writes=3, recorder_factory=BudgetRecorder)
        self.assertEqual(error.result.client.cleanup_errors, ())
        self.assertTrue(error.result.report["evidence_complete"])
        self.assertTrue(error.result.report["recording_sealed"])
        self.assertEqual(self.replay()["status"], "sealed_failed_collection")

    def test_finalization_budget_covers_tail_persistence_without_replacing_primary_fault(self):
        clock = self.clock
        raw = raw_reply() + b"S\x00"
        self.transport.on_write = lambda packet: self.transport.enqueue(raw)
        self.transport.on_close = lambda: setattr(clock, "now", clock.now + 0.6)

        class BudgetRecorder(recording.Recorder):
            def append(self, row):
                super().append(row)
                if row["type"] == "evidence" and row["stream"]["kind"] == "partial":
                    clock.now += 0.6

        error = self.failure("unexpected_evidence", plan=poll.PollPlan(max_requests=1),
                             recorder_factory=BudgetRecorder)
        self.assert_raw(error.result, raw)
        self.assertEqual(error.result.client.cleanup_errors, ())
        self.assertIn("finalization_deadline",
                      [item["code"] for item in error.result.report["secondary_errors"]])
        self.assertTrue(error.result.report["evidence_complete"])
        self.assertEqual(self.assert_persisted_events(error.result)["status"], "sealed_failed_collection")

    def test_seal_and_file_close_share_one_postcall_deadline(self):
        clock = self.clock
        calls = []

        class BudgetRecorder(recording.Recorder):
            def finish(self, report):
                calls.append("finish")
                super().finish(report)
                clock.now += 0.6

            def close(self):
                calls.append("close")
                super().close()
                clock.now += 0.6

        error = self.failure("finalization_deadline", writes=3, recorder_factory=BudgetRecorder)
        self.assertEqual(calls, ["finish", "close"])
        self.assertTrue(error.result.report["recording_sealed"])
        self.assertTrue(error.result.report["evidence_complete"])
        self.assertEqual(error.result.report["recording_bytes"], self.output.stat().st_size)
        replay = self.replay()
        self.assertEqual(replay["status"], "sealed_collection_claim_complete")
        self.assertIn("cannot attest", replay["report"]["recording_finalization"])

    def test_cleanup_grace_can_extend_past_collection_duration_without_being_reset(self):
        started = self.clock.now
        self.transport.on_close = lambda: setattr(self.clock, "now", self.clock.now + 0.5)
        plan = poll.PollPlan(max_requests=1, request_timeout=0.05, duration=0.1, cleanup_timeout=1)
        result = self.collect(plan=plan)
        self.assertGreater(self.clock.now, started + plan.duration)
        self.assertLess(self.clock.now, started + 0.02 + plan.cleanup_timeout)
        self.assertEqual(result.report["status"], "complete")
        self.assertEqual(sum(name == "close" for name, _ in self.transport.calls), 1)
        self.assertEqual(self.replay()["status"], "sealed_collection_claim_complete")

    def test_open_and_header_failure_never_start_transport(self):
        for stage in ("open", "header"):
            with self.subTest(stage=stage):
                self.fresh()
                closes = []

                class StartupRecorder(recording.Recorder):
                    def open(self):
                        if stage == "open":
                            raise OSError("injected open failure")
                        super().open()

                    def append(self, row):
                        if row["type"] == "header":
                            raise OSError("injected header failure")
                        super().append(row)

                    def close(self):
                        closes.append(True)
                        super().close()

                self.failure("OSError", writes=0, recorder_factory=StartupRecorder)
                self.assertEqual(self.transport.calls, [])
                self.assertEqual(closes, [True])
                self.assertEqual(self.output.exists(), stage == "header")

    def test_record_and_byte_exhaustion_stop_without_truncating_rows_or_dropping_memory(self):
        for options, writes in (
            ({"max_records": 4}, 1),
            ({"max_output_bytes": recording.TERMINAL_BYTES + 4096}, 2),
        ):
            with self.subTest(options=options):
                self.fresh()
                error = self.failure("recording_limit", writes=writes,
                                     plan=poll.PollPlan(max_requests=3, **options))
                self.assert_raw(error.result, b"".join(raw_reply(sequence=index) for index in range(writes)))
                self.assertFalse(error.result.report["evidence_complete"])
                raw = self.output.read_bytes()
                self.assertTrue(raw.endswith(b"\n"))
                rows = [json.loads(line) for line in raw.splitlines()]
                self.assertEqual(rows[-1]["type"], "terminal")
                self.assertLessEqual(len(raw), options.get("max_output_bytes", recording.MAX_FILE_BYTES))
                self.assertLessEqual(len(rows), options.get("max_records", recording.MAX_RECORDS))
                self.assertEqual(self.replay()["status"], "sealed_failed_collection")


class PollValidationAndCliTests(PollTestCase):
    def test_every_numeric_plan_field_rejects_bool_nonfinite_huge_and_wrong_types(self):
        for field in fields(poll.PollPlan):
            for value in (True, False, None, "1", float("nan"), float("inf"),
                          -float("inf"), 10**1000, -1):
                with self.subTest(field=field.name, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        poll.PollPlan(**{field.name: value})

    def test_plan_caps_sequence_exhaustion_and_nominal_request_plus_interval_budget(self):
        invalid = (
            {"max_requests": 257}, {"max_events": 8193}, {"max_rx_bytes": 1024 * 1024 + 1},
            {"max_rx_bytes": 143}, {"max_events": 143},
            {"max_output_bytes": recording.MAX_FILE_BYTES + 1},
            {"max_output_bytes": recording.TERMINAL_BYTES + 4095},
            {"max_records": recording.MAX_RECORDS + 1}, {"max_records": 1},
            {"first_sequence": 65535, "max_requests": 2},
            {"interval": 0.1, "request_timeout": 0.2},
            {"interval": 0.1, "request_timeout": 0.1, "max_lateness": 0.1},
            {"max_requests": 3, "interval": 1, "request_timeout": 0.5, "duration": 3.5},
        )
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                poll.PollPlan(**options)
        plan = poll.PollPlan(max_requests=256, duration=600, max_events=8192,
                             max_rx_bytes=1024 * 1024, max_output_bytes=recording.MAX_FILE_BYTES)
        self.assertEqual(plan.to_dict()["max_outstanding"], 1)
        self.assertEqual(plan.to_dict()["query"], "read-raw-data")
        self.assertEqual(plan.to_dict()["declared_settings"], dict(client.SETTINGS))
        self.assertEqual(poll.PollPlan(first_sequence=65535, max_requests=1).first_sequence, 65535)
        self.assertEqual(poll.PollPlan(max_requests=3, interval=1, request_timeout=0.5,
                                       duration=3.501).duration, 3.501)

    def test_invalid_collect_arguments_are_rejected_before_output_or_transport(self):
        for options in (
            {"plan": {}}, {"clock": 1}, {"wait": False}, {"wall_clock": None},
            {"recorder_factory": None}, {"evidence_kind": "authenticated"},
            {"ownership_key": b""}, {"expected_identity": "not-bytes"},
        ):
            with self.subTest(options=options):
                with self.assertRaises((ValueError, TypeError)):
                    self.collect(**options)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.transport.calls, [])

    def test_private_output_no_clobber_and_exclusive_create_race(self):
        self.collect(plan=poll.PollPlan(max_requests=1))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        before = self.output.read_bytes()
        self.transport = FakeTransport(self.clock)
        with self.assertRaises(FileExistsError):
            self.collect()
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])
        self.fresh()
        with patch.object(recording.os, "open", side_effect=FileExistsError("exclusive-create race")) as opened:
            self.failure("FileExistsError", writes=0)
        opened.assert_called_once()
        self.assertTrue(opened.call_args.args[1] & os.O_EXCL)
        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
        self.assertEqual(self.transport.calls, [])

    def test_output_rejects_missing_link_and_special_ancestors_before_open(self):
        regular = self.directory / "regular"
        regular.write_bytes(b"untouched")
        link = self.directory / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        fifo = self.directory / "fifo"
        os.mkfifo(fifo)
        dangling = self.directory / "dangling"
        dangling.symlink_to(self.directory / "absent")
        for path in (
            self.directory / "missing" / "output", regular / "output", fifo / "output",
            link / "output", link / ".." / "output", dangling, fifo, regular,
        ):
            with self.subTest(path=str(path)):
                self.output = path
                with patch.object(recording.os, "open") as opened:
                    with self.assertRaises((ValueError, OSError)):
                        self.collect()
                opened.assert_not_called()
                self.assertEqual(self.transport.calls, [])
        self.assertEqual(regular.read_bytes(), b"untouched")

    def test_device_kernel_paths_rejected_before_any_stat_or_open(self):
        for output in ("/dev/ttyUSB0", "/dev/null", "/proc/self/fd/1", "/sys/test",
                       "/dev/../poll.jsonl", "/proc/../poll.jsonl"):
            with self.subTest(output=output):
                self.output = Path(output)
                with patch.object(Path, "lstat") as lstat, patch.object(recording.os, "open") as opened:
                    with self.assertRaises(ValueError):
                        self.collect()
                lstat.assert_not_called()
                opened.assert_not_called()
                self.assertEqual(self.transport.calls, [])

    def test_default_cli_is_an_offline_plan_without_transport_or_output(self):
        stdout = io.StringIO()
        with patch.object(poll, "collect") as collect, patch.object(recording.os, "open") as opened:
            with redirect_stdout(stdout):
                self.assertEqual(poll.main([]), 0)
        collect.assert_not_called()
        opened.assert_not_called()
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "offline_plan")
        self.assertTrue(result["offline_only"])
        self.assertFalse(result["transport_accessed"])
        self.assertFalse(result["output_created"])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_cli_requires_explicit_synthetic_output_and_replay_for_incomplete(self):
        for options in (
            ["--output", str(self.output)], ["--synthetic"],
            ["--allow-incomplete"], ["--synthetic", "--allow-incomplete", "--output", str(self.output)],
            ["--max-requests", "257"], ["--interval", "nan"],
        ):
            with self.subTest(options=options):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    self.assertEqual(poll.main(options), 2)
                self.assertEqual(json.loads(stdout.getvalue())["status"], "input_error")
                self.assertFalse(self.output.exists())

    def test_synthetic_cli_record_and_offline_replay_end_to_end(self):
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"

        def invoke(*arguments):
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "tools.marvin_legacy_poll", *arguments],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(completed.stderr, "")
            return json.loads(completed.stdout)

        result = invoke("--synthetic", "--output", str(self.output), "--max-requests", "3")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["accepted_tx_bytes"], 30)
        self.assertEqual(result["uncertain_tx_bytes"], 0)
        self.assertEqual(result["observed"], {"requests": 3, "events": 3})
        self.assertEqual(result["recording_bytes"], self.output.stat().st_size)
        self.assertTrue(result["recording_sealed"])
        replay = invoke("--replay", str(self.output))
        self.assertEqual(replay["status"], "sealed_collection_claim_complete")
        self.assertTrue(replay["offline_only"])
        self.assertEqual(replay["source_bytes"], result["recording_bytes"])
        self.assertEqual(replay["header"]["evidence_kind"], "synthetic")
        self.assertEqual(len(replay["samples"]), 3)
        self.assertEqual(replay["application_acknowledgment"], "not_established")
        self.assertEqual([bytes.fromhex(row["raw_hex"]) for row in replay["requests"]],
                         [protocol.GETTERS["read-raw-data"].encode(index) for index in range(3)])


if __name__ == "__main__":
    unittest.main()
