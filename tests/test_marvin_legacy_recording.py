import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.marvin_legacy_poll import CollectionError, PollPlan, collect
from tools.marvin_legacy_recording import (
    MAX_FILE_BYTES, MAX_RECORDS, TERMINAL_BYTES, encode_row, inspect_recording,
)
from tests.test_marvin_legacy_client import Clock, FakeTransport
from tests.test_marvin_legacy_protocol import frame


class RecordingReplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.output = self.directory / "synthetic.jsonl"
        self.clock = Clock()
        self.transport = FakeTransport(self.clock)

    def record(self, *, count=2):
        def wait(seconds):
            self.clock.now += seconds

        return collect(
            self.transport, self.output, ownership_key=b"recording-tests",
            expected_identity=self.transport.token, plan=PollPlan(max_requests=count),
            evidence_kind="synthetic", clock=self.clock, wait=wait,
        )

    def rows(self):
        return [json.loads(line) for line in self.output.read_bytes().splitlines()]

    def altered(self, rows, *, reseal=True):
        if reseal and rows[-1]["type"] == "terminal":
            prefix = b"".join(encode_row(row) for row in rows[:-1])
            rows[-1].update(prefix_bytes=len(prefix), prefix_records=len(rows) - 1,
                            prefix_sha256=hashlib.sha256(prefix).hexdigest())
        path = self.directory / "altered.jsonl"
        path.write_bytes(b"".join(encode_row(row) for row in rows))
        return path

    def test_exact_seal_scope_and_byte_accounting(self):
        result = self.record()
        raw = self.output.read_bytes()
        rows = self.rows()
        prefix = b"".join(raw.splitlines(keepends=True)[:-1])
        self.assertEqual(rows[-1]["prefix_bytes"], len(prefix))
        self.assertEqual(rows[-1]["prefix_sha256"], hashlib.sha256(prefix).hexdigest())
        self.assertEqual(rows[-1]["prefix_records"], len(rows) - 1)
        self.assertEqual(result.report["recording_bytes"], len(raw))
        self.assertLessEqual(len(raw), PollPlan().max_output_bytes)
        self.assertLessEqual(len(raw) - len(prefix), TERMINAL_BYTES)
        replay = inspect_recording(self.output)
        self.assertEqual(replay["status"], "sealed_collection_claim_complete")
        self.assertEqual(replay["seal"], "prefix_hash_verified_not_authenticated")
        self.assertEqual(replay["report"]["observed"], {"requests": 2, "events": 2})
        self.assertEqual(len(replay["samples"][0]["interpretation"]["fields"]), 82)
        self.assertEqual(replay["samples"][1]["identical_payload_run"], 1)
        self.assertEqual(replay["samples"][1]["tick_progression"], "unchanged")
        self.assertEqual(replay["header"]["evidence_kind"], "synthetic")

    def test_terminal_reserve_covers_worst_case_escaped_diagnostics(self):
        self.record()
        report = self.rows()[-1]["report"]
        # Non-BMP characters require two JSON surrogate escapes (12 ASCII bytes).
        report.update(
            failure={"code": "\U0001f600" * 80, "message": "\U0001f600" * 256},
            secondary_errors=[{"code": "\U0001f600" * 80, "message": "\U0001f600" * 256}] * 16,
            client_failure={"code": "operation_aborted", "message": "\U0001f600" * 1024},
        )
        terminal = {
            "type": "terminal", "prefix_bytes": MAX_FILE_BYTES,
            "prefix_records": MAX_RECORDS, "prefix_sha256": "f" * 64, "report": report,
        }
        self.assertLessEqual(len(encode_row(terminal)), TERMINAL_BYTES)

    def test_successful_file_never_attests_own_finalization(self):
        self.record()
        replay = inspect_recording(self.output)
        self.assertIn("cannot attest", replay["report"]["recording_finalization"])
        self.assertNotIn("recording_sealed", replay["report"])
        self.assertEqual(replay["application_acknowledgment"], "not_established")

    def test_failed_but_sealed_collection_is_not_success(self):
        self.transport.count = 3
        with self.assertRaises(CollectionError):
            self.record()
        replay = inspect_recording(self.output)
        self.assertEqual(replay["status"], "sealed_failed_collection")
        self.assertEqual(replay["report"]["failure"]["code"], "short_write")
        self.assertEqual(replay["samples"], [])
        self.assertEqual(replay["requests"][0]["accepted_bytes"], 3)
        self.assertEqual(replay["requests"][0]["uncertain_bytes"], 7)

    def test_incomplete_flag_cannot_waive_complete_request_accounting(self):
        self.transport.count = 3
        with self.assertRaises(CollectionError):
            self.record(count=1)
        rows = self.rows()
        rows[-1]["report"].update(evidence_complete=False, delivered_candidates=1)
        rows[-1]["report"]["gap"]["uncollected_snapshots"] = 0
        with self.assertRaises(ValueError):
            inspect_recording(self.altered(rows))

    def test_non_frame_candidate_is_never_promoted_to_a_sample(self):
        self.record()
        for kind in ("error", "noise", "partial"):
            rows = self.rows()
            event = next(row for row in rows if row["type"] == "evidence")
            event["stream"]["kind"] = kind
            del event["stream"]["packet"]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))

    def test_retained_but_not_delivered_candidate_is_not_a_sample(self):
        def identity():
            if self.transport.calls[-2][0] == "read":
                self.transport.token = b"changed-generation"

        self.transport.on_identity = identity
        with self.assertRaises(CollectionError):
            self.record()
        replay = inspect_recording(self.output)
        self.assertEqual(replay["requests"][0]["status"], "failed")
        self.assertEqual(replay["events"][0]["labels"], ["matched_candidate"])
        self.assertEqual(replay["samples"], [])

    def test_missing_truncated_duplicate_terminal_and_trailing_content(self):
        self.record()
        raw = self.output.read_bytes()
        prefix = b"".join(raw.splitlines(keepends=True)[:-1])
        for bad in (prefix, raw[:-1], raw[:-10], raw + raw.splitlines(keepends=True)[-1],
                    raw + b"{}\n", raw + b"\n", raw + b"trailing"):
            with self.subTest(tail=bad[-30:]):
                path = self.directory / "partial.jsonl"
                path.write_bytes(bad)
                with self.assertRaises(ValueError):
                    inspect_recording(path)
        path.write_bytes(prefix + b'{"type":"term')
        replay = inspect_recording(path, allow_incomplete=True)
        self.assertEqual(replay["status"], "incomplete_recording")
        self.assertEqual(bytes.fromhex(replay["partial_final_line_hex"]), b'{"type":"term')
        self.assertIsNone(replay["report"])
        self.assertEqual(len(replay["samples"]), 2)

    def test_hash_length_and_record_count_mismatch(self):
        self.record()
        for key, value in (("prefix_bytes", 1), ("prefix_records", 1),
                           ("prefix_sha256", "0" * 64)):
            rows = self.rows()
            rows[-1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows, reseal=False))

    def test_tampered_raw_or_candidate_declarations_are_not_trusted(self):
        self.record()
        changes = (
            ("raw_hex", "00"), ("offset", 1), ("end_offset", 1),
            ("raw_bytes", True), ("follows_corruption", True),
        )
        for key, value in changes:
            rows = self.rows()
            event = next(row for row in rows if row["type"] == "evidence")
            event["stream"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))
        for key, value in (("labels", ["unverified"]), ("confidence", "physical_ack"),
                           ("application_acknowledgment", "established"), ("started_at", 0),
                           ("evidence_kind", "recorded"), ("ended_at", float("1e100"))):
            rows = self.rows()
            event = next(row for row in rows if row["type"] == "evidence")
            event[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))

    def test_bad_request_shape_status_and_correlation(self):
        self.record()
        for key, value in (("raw_hex", "00" * 10), ("status", "acknowledged"),
                           ("query", "get-config"), ("accepted_bytes", True),
                           ("uncertain_bytes", 1), ("reply_event", None),
                           ("submitted_at", None), ("sequence", 65536),
                           ("deadline", False), ("input_boundary", 999)):
            rows = self.rows()
            request = next(row for row in rows if row["type"] == "request")
            request[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))

    def test_packet_derived_fields_must_equal_raw(self):
        self.record()
        rows = self.rows()
        event = next(row for row in rows if row["type"] == "evidence")
        event["stream"]["packet"]["sequence"] = 9
        with self.assertRaises(ValueError):
            inspect_recording(self.altered(rows))

    def test_boolean_or_contradictory_completeness_claims_rejected(self):
        self.record()
        for key, value in (("persisted", {"requests": 1, "events": 2}),
                           ("observed", {"requests": 2, "events": 3}),
                           ("client_state", "invalid"), ("failure", {"code": "deadline"}),
                           ("evidence_complete", False), ("rejected_input_bytes", 1),
                           ("client_failure", {"code": "deadline"})):
            rows = self.rows()
            rows[-1]["report"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))
        for key, value in (("requested_snapshots", 1), ("delivered_candidates", 0),
                           ("accepted_tx_bytes", 0), ("uncertain_tx_bytes", 1),
                           ("observed", {"requests": True, "events": 2}),
                           ("persisted", {"requests": 2, "events": True}),
                           ("gap", {"reason": "made-up"})):
            rows = self.rows()
            rows[-1]["report"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))

    def test_no_missing_or_duplicate_evidence_positions(self):
        self.record()
        rows = self.rows()
        second = [row for row in rows if row["type"] == "evidence"][1]
        second["index"] = 0
        with self.assertRaises(ValueError):
            inspect_recording(self.altered(rows))

    def test_header_plan_and_context_are_validated(self):
        self.record()
        for key, value in (("max_requests", True), ("interval", float("1e100")),
                           ("max_events", 999999), ("rate_hz", 100),
                           ("max_output_bytes", TERMINAL_BYTES)):
            rows = self.rows()
            rows[0]["plan"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))
        for key, value in (("utc", "not-a-time"), ("monotonic_before", True),
                           ("monotonic_after", 0), ("phase", "physical_ack")):
            rows = self.rows()
            context = next(row for row in rows if row["type"] == "context")
            context[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect_recording(self.altered(rows))

    def test_duplicate_keys_nonstandard_numbers_and_invalid_json(self):
        self.record()
        raw = self.output.read_bytes()
        for line in (b'{"type":"header","type":"header"}\n',
                     b'{"type":"header","plan":{"max_requests":1,"max_requests":1}}\n',
                     b'{"type":"header","value":NaN}\n', b"null\n", b"\xff\n"):
            path = self.directory / "bad.jsonl"
            path.write_bytes(line + raw)
            with self.assertRaises((ValueError, UnicodeError)):
                inspect_recording(path)

    def test_bound_file_records_rows_and_options_before_read(self):
        self.record()
        with self.assertRaises(ValueError):
            inspect_recording(self.output, max_bytes=self.output.stat().st_size - 1)
        with self.assertRaises(ValueError):
            inspect_recording(self.output, max_records=1)
        nonexistent = self.directory / "missing"
        for key, values in (("max_bytes", (False, 0, MAX_FILE_BYTES + 1, 10 ** 1000)),
                            ("max_records", (True, 0, MAX_RECORDS + 1, float("inf"))),
                            ("allow_incomplete", (1, "yes"))):
            for value in values:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    inspect_recording(nonexistent, **{key: value})
        path = self.directory / "large-line.jsonl"
        path.write_bytes(b" " * (TERMINAL_BYTES + 1) + b"\n")
        with self.assertRaises(ValueError):
            inspect_recording(path)
        path.write_bytes(b"\n" * (MAX_RECORDS + 1))
        with self.assertRaises(ValueError):
            inspect_recording(path)

    def test_local_input_link_special_and_kernel_paths_rejected(self):
        self.record()
        link = self.directory / "link.jsonl"
        link.symlink_to(self.output)
        parent = self.directory / "parent"
        parent.symlink_to(self.directory, target_is_directory=True)
        for path in (link, parent / self.output.name, self.directory, "/dev/null", "/proc/self/status"):
            with self.subTest(path=str(path)), self.assertRaises(ValueError):
                inspect_recording(path)
        self.assertEqual(inspect_recording(self.output)["status"], "sealed_collection_claim_complete")

    def test_raw_field_changes_and_tick_ambiguity_keep_source_profile(self):
        ticks = (0xFFFFFFFE, 1, 0, 0)

        def reply(packet):
            payload = bytearray(134)
            payload[:4] = ticks[packet.sequence].to_bytes(4, "little")
            payload[36:38] = (438).to_bytes(2, "little")
            self.transport.enqueue(frame(bytes(payload), command=0, sequence=packet.sequence))

        self.transport.on_write = reply
        self.record(count=4)
        samples = inspect_recording(self.output)["samples"]
        self.assertEqual([sample["tick_progression"] for sample in samples],
                         ["first", "possible_wrap_or_regression", "regression_or_large_gap", "unchanged"])
        self.assertEqual(samples[1]["changed_fields"], ["tick"])
        self.assertEqual(samples[-1]["identical_payload_run"], 1)
        for sample in samples:
            decoded = sample["interpretation"]
            self.assertEqual(decoded["profile"], "pctestapp-raw-data-134")
            self.assertEqual(decoded["fields"]["batteryVoltage"]["unsigned"], 438)
            self.assertEqual(decoded["application_acknowledgment"], "not_established")


if __name__ == "__main__":
    unittest.main()
