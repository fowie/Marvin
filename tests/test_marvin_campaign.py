import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import marvin_campaign as campaign
from tools import marvin_probe, marvin_protocol, marvin_session


BASELINE = {"usb": {"usb_path": "/fake/1-3.3", "busnum": 1, "devnum": 18,
                    "descriptors_sha256": campaign.DESCRIPTOR_HASH},
            "tty": "/dev/fake-marvin", "tty_rdev": 123}


def plan():
    return {"schema_version": 1, "profile": "fixture", "segments": [
        {"id": name, "baudrate": 115200, "bytesize": 8, "parity": "N", "stopbits": 1,
         "dtr": True, "rts": True, "steps": [
             {"id": "first", "chunks_hex": [marvin_protocol.get_unit_info_request().hex()],
              "interval_seconds": 0, "response_seconds": 0.5,
              "classification": "source-query", "rationale": "offline fixture"},
             {"id": "second", "chunks_hex": [marvin_protocol.get_sensor_info_request().hex()],
              "interval_seconds": 0, "response_seconds": 0.5,
              "classification": "source-query", "rationale": "offline fixture"},
         ]} for name in ("segment-a", "segment-b")]}


def fixture(directory, schedule, *, rx=False, coalesced=True):
    directory = Path(directory)
    (directory / "serial").mkdir(parents=True)
    (directory / "usb").mkdir()
    data = b"reply" if rx else b""
    (directory / "serial/received.bin").write_bytes(data)
    completed = schedule[:1] if rx else schedule
    serial = {
        "status": "completed", "bytes_received": len(data),
        "transmit_status": "suppressed_schedule_rx" if rx else "written",
        "application_bytes_written": sum(len(item.data) for item in completed),
        "scheduled_writes_completed": len(completed),
    }
    (directory / "serial/metadata.json").write_text(json.dumps(serial))
    chunks = [b"".join(item.data for item in completed)] if coalesced else [item.data for item in completed]
    records = []
    def usb_hex(payload):
        return " ".join(payload[index:index + 4].hex() for index in range(0, len(payload), 4))
    for index, payload in enumerate(chunks, 1):
        records.extend((
            f"a{index} {index * 10} S Bo:1:018:3 -115 {len(payload)} = {usb_hex(payload)}",
            f"a{index} {index * 10 + 1} C Bo:1:018:3 0 {len(payload)} >",
        ))
    if rx:
        records.extend(("b1 100 S Bi:1:018:2 -115 64 <",
                        f"b1 101 C Bi:1:018:2 0 {len(data)} = {usb_hex(data)}"))
    (directory / "usb/usbmon.txt").write_text("\n".join(records) + "\n")
    return {"status": "completed", "baseline": BASELINE, "serial": serial,
            "usb": {"status": "completed", "monitor_final_stats": {"queued": 0, "dropped": 0}}}


def rejected_fixture(directory):
    directory = Path(directory)
    (directory / "serial").mkdir(parents=True)
    (directory / "usb").mkdir()
    (directory / "serial/metadata.json").write_text(json.dumps({
        "status": "failed", "application_bytes_written": 0, "bytes_received": 0,
        "settings_rejected_before_open": True, "error": "Host rejected line coding",
        "error_errno": 22,
    }))
    (directory / "serial/events.jsonl").write_text('{"event":"open_attempt"}\n')
    (directory / "usb/metadata.json").write_text(json.dumps({
        "status": "interrupted", "identity": BASELINE["usb"],
        "monitor_final_stats": {"queued": 0, "dropped": 0},
    }))
    (directory / "usb/usbmon.txt").write_text("")


class PlanValidationTests(unittest.TestCase):
    def test_plan_compiles_deterministically_without_hardware(self):
        with patch.object(marvin_session, "preflight") as preflight:
            coverage = campaign.validate_plan(plan())
        preflight.assert_not_called()
        self.assertEqual(coverage["segments"], 2)
        self.assertEqual(coverage["steps"], 4)
        self.assertEqual(coverage["writes"], 4)
        self.assertEqual(coverage["application_bytes"], 48)
        self.assertEqual(coverage["serial_seconds"], 8)
        self.assertEqual(coverage["usb_seconds"], 18)
        schedule, seconds = campaign.compile_segment(plan()["segments"][0])
        self.assertEqual([item.offset_seconds for item in schedule], [1, 1.5])
        self.assertEqual(seconds, 4)
        self.assertEqual([item.label for item in schedule], ["first/0", "second/0"])

    def test_fragment_pacing_is_preserved(self):
        segment = plan()["segments"][0]
        segment["steps"][0]["chunks_hex"] = ["ef", "be01001b0000001736adde"]
        segment["steps"][0]["interval_seconds"] = 0.01
        schedule, seconds = campaign.compile_segment(segment)
        self.assertEqual([item.offset_seconds for item in schedule], [1, 1.01, 1.51])
        self.assertEqual(seconds, 4.01)

    def test_limits_and_duplicate_identifiers_are_rejected(self):
        variations = []
        value = plan()
        value["segments"][1]["id"] = value["segments"][0]["id"]
        variations.append(value)
        for key, bad in (("baudrate", 0), ("bytesize", 6), ("parity", "M"), ("stopbits", True),
                         ("dtr", 1), ("id", "../outside"), ("steps", [])):
            value = plan()
            value["segments"][0][key] = bad
            variations.append(value)
        for key, bad in (("response_seconds", float("nan")), ("response_seconds", 4),
                         ("interval_seconds", -1), ("chunks_hex", ["00" * 33]),
                         ("chunks_hex", ["0"]), ("id", "second"), ("rationale", "")):
            value = plan()
            value["segments"][0]["steps"][0][key] = bad
            variations.append(value)
        value = plan()
        value["segments"][0]["steps"] = [
            dict(value["segments"][0]["steps"][0], id=f"query-{i}", response_seconds=3)
            for i in range(40)]
        variations.append(value)
        for value in variations:
            with self.subTest(value=value), self.assertRaises(ValueError):
                campaign.validate_plan(value)

    def test_destructive_opcodes_and_text_are_rejected(self):
        for payload in ("efbe0000080000000000adde", "efbe0000090000000000adde",
                        "efbe00002f0000000000adde", b"erase\r".hex(), b"PING\r".hex(),
                        b"RST\r".hex(), b"\x00".hex()):
            value = plan()
            value["segments"][0]["steps"][0]["chunks_hex"] = [payload]
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                campaign.validate_plan(value)

    def test_cli_without_run_only_prints_the_plan(self):
        fake = SimpleNamespace(make_plan=Mock(return_value=plan()))
        output = io.StringIO()
        with patch.dict("sys.modules", {"tools.marvin_campaign_plan": fake}), \
             patch("sys.argv", ["marvin_campaign"]), \
             patch.object(marvin_session, "preflight") as preflight, redirect_stdout(output):
            self.assertEqual(campaign.main(), 0)
        preflight.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["coverage"]["application_bytes"], 48)


class AssessmentTests(unittest.TestCase):
    def test_rejected_settings_require_zero_io_and_intact_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            rejected_fixture(temp)
            result = campaign.rejected_settings_evidence(temp, BASELINE)
            self.assertEqual(result["outcome"], "setting_rejected_before_application_io")
            events = Path(temp) / "serial/events.jsonl"
            events.write_text('{"event":"open_attempt"}\n{"event":"write_attempt"}\n')
            with self.assertRaisesRegex(ValueError, "attempted"):
                campaign.rejected_settings_evidence(temp, BASELINE)

    def test_out_stream_allows_usb_split_or_coalesced_writes(self):
        schedule, _ = campaign.compile_segment(plan()["segments"][0])
        for coalesced in (True, False):
            with tempfile.TemporaryDirectory() as temp:
                result = fixture(temp, schedule, coalesced=coalesced)
                assessment = campaign.assess_segment(temp, result, schedule)
                self.assertEqual(assessment["outcome"], "silent_out_confirmed")
                self.assertEqual(assessment["usb_out_confirmed_bytes"], 24)

    def test_reused_urb_identifiers_are_paired_chronologically(self):
        schedule, _ = campaign.compile_segment(plan()["segments"][0])
        with tempfile.TemporaryDirectory() as temp:
            result = fixture(temp, schedule, coalesced=False)
            trace = Path(temp) / "usb/usbmon.txt"
            trace.write_text(trace.read_text().replace("a2 ", "a1 "))
            assessment = campaign.assess_segment(temp, result, schedule)
            self.assertEqual(assessment["usb_out_confirmed_bytes"], 24)

    def test_any_rx_stops_even_with_a_suppressed_partial_schedule(self):
        schedule, _ = campaign.compile_segment(plan()["segments"][0])
        with tempfile.TemporaryDirectory() as temp:
            result = fixture(temp, schedule, rx=True)
            assessment = campaign.assess_segment(temp, result, schedule)
            self.assertEqual(assessment["outcome"], "received_data_stop")
            self.assertEqual(assessment["scheduled_writes_completed"], 1)
            self.assertEqual(assessment["usb_in_reported_bytes"], 5)

    def test_loss_unknown_write_and_incomplete_schedule_never_advance(self):
        schedule, _ = campaign.compile_segment(plan()["segments"][0])
        for problem in ("loss", "queued", "unknown", "missing", "bytes"):
            with tempfile.TemporaryDirectory() as temp:
                result = fixture(temp, schedule)
                if problem == "loss":
                    result["usb"]["monitor_final_stats"]["dropped"] = 1
                elif problem == "queued":
                    result["usb"]["monitor_final_stats"]["queued"] = 1
                elif problem == "unknown":
                    result["serial"]["transmit_status"] = "unknown"
                elif problem == "missing":
                    result["serial"]["scheduled_writes_completed"] = 1
                else:
                    result["serial"]["application_bytes_written"] = 25
                with self.subTest(problem=problem), self.assertRaises(ValueError):
                    campaign.assess_segment(temp, result, schedule)

    def test_cooperative_completion_does_not_relax_complete_usb_pairing(self):
        schedule, _ = campaign.compile_segment(plan()["segments"][0])
        with tempfile.TemporaryDirectory() as temp:
            result = fixture(temp, schedule)
            result["usb"]["stop_reason"] = "coordinator_stop"
            trace = Path(temp) / "usb/usbmon.txt"
            trace.write_text(trace.read_text() + "b1 100 S Bi:1:018:2 -115 64 <\n")
            with self.assertRaisesRegex(ValueError, "Incomplete USB pairing"):
                campaign.assess_segment(temp, result, schedule)

    def test_bad_completion_and_foreign_usb_identity_are_fatal(self):
        schedule, _ = campaign.compile_segment(plan()["segments"][0])
        for old, new in (("0 24 >", "-71 24 >"), ("1:018:3", "1:019:3"),
                         ("1736adde", "1737adde")):
            with tempfile.TemporaryDirectory() as temp:
                result = fixture(temp, schedule)
                trace = Path(temp) / "usb/usbmon.txt"
                trace.write_text(trace.read_text().replace(old, new))
                with self.subTest(old=old), self.assertRaises(ValueError):
                    campaign.assess_segment(temp, result, schedule)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "campaign"
        self.rx = False
        self.failure = False
        self.settings_rejected = False
        patch.object(campaign.os, "geteuid", return_value=1000).start()
        self.preflight = patch.object(marvin_session, "preflight", return_value=copy.deepcopy(BASELINE)).start()
        self.identity = patch.object(marvin_session, "check_identity").start()
        self.session = patch.object(marvin_session, "run_session", side_effect=self.record).start()
        self.addCleanup(patch.stopall)

    def record(self, port, output, **options):
        if self.settings_rejected:
            rejected_fixture(output)
            raise marvin_probe.SerialSettingsRejected(22, "Host rejected line coding")
        if self.failure:
            (output / "serial").mkdir(parents=True)
            (output / "serial/metadata.json").write_text(json.dumps({
                "status": "failed", "transmit_status": "unknown",
                "application_bytes_written": None, "known_application_bytes_written": 12,
                "scheduled_writes_completed": 1, "bytes_received": 0}))
            raise marvin_probe.serial.SerialTimeoutException("uncertain write")
        self.assertEqual(options["expected_usb_identity"], BASELINE["usb"])
        self.assertEqual(options["usb_tail_seconds"], 5)
        self.assertEqual(options["usb_close_grace_seconds"], 30)
        return fixture(output, options["probe_schedule"], rx=self.rx)

    def run_campaign(self, **overrides):
        options = {"actuators_isolated": True, "allow_unknown_command": True,
                   "allow_telemetry_state_change": True, "switch_position": "RUN"}
        options.update(overrides)
        return campaign.run_campaign(plan(), self.output, **options)

    def test_silent_campaign_records_all_segments_and_hashes(self):
        result = self.run_campaign()
        self.assertEqual(result["status"], "completed_silent")
        self.assertEqual(result["application_bytes_confirmed"], 48)
        self.assertEqual(result["usb_close_grace_seconds"], 30)
        self.assertEqual(result["planned"]["usb_seconds"], 18)
        self.assertEqual(self.session.call_count, 2)
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            digest, path = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.output / path).read_bytes()).hexdigest(), digest)
        encoded = json.dumps(plan(), sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(result["plan_sha256"], hashlib.sha256(encoded).hexdigest())

    def test_rx_stops_before_the_second_segment(self):
        self.rx = True
        result = self.run_campaign()
        self.assertEqual(result["status"], "stopped_on_rx")
        self.session.assert_called_once()
        self.assertEqual(len(result["segments"]), 1)

    def test_unsupported_settings_are_reported_not_retried_or_claimed_tested(self):
        self.settings_rejected = True
        result = self.run_campaign()
        self.assertEqual(result["status"], "completed_with_unsupported_settings")
        self.assertEqual(self.session.call_count, 2)
        self.assertEqual(result["application_bytes_confirmed"], 0)
        self.assertTrue(all(entry["status"] == "unsupported_settings" for entry in result["segments"]))

    def test_unknown_write_retains_partial_ledger_and_does_not_retry(self):
        self.failure = True
        with self.assertRaisesRegex(OSError, "uncertain"):
            self.run_campaign()
        self.session.assert_called_once()
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["segments"][0]["partial"]["application_bytes_written"])
        self.assertEqual(result["segments"][0]["partial"]["known_application_bytes_written"], 12)
        self.assertTrue((self.output / "SHA256SUMS").exists())

    def test_identity_change_stops_before_another_open(self):
        self.identity.side_effect = [None, OSError("identity changed")]
        with self.assertRaisesRegex(OSError, "identity changed"):
            self.run_campaign()
        self.session.assert_called_once()
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["segments"][0]["status"], "completed")

    def test_authorization_and_root_are_rejected_before_preflight(self):
        for option in ({"actuators_isolated": False}, {"allow_unknown_command": False},
                       {"allow_telemetry_state_change": False}, {"switch_position": "PRG"}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.run_campaign(**option)
        with patch.object(campaign.os, "geteuid", return_value=0), self.assertRaises(ValueError):
            self.run_campaign()
        self.preflight.assert_not_called()

    def test_changed_fingerprint_never_opens_a_port(self):
        self.preflight.return_value["usb"]["descriptors_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.run_campaign()
        self.session.assert_not_called()

    def test_wall_limit_is_partial_coverage_not_success(self):
        with patch.object(campaign.time, "monotonic", return_value=0):
            result = self.run_campaign(max_seconds=1)
        self.assertEqual(result["status"], "stopped_wall_limit")
        self.assertEqual(result["segments"], [])
        self.session.assert_not_called()

    def test_wall_limit_reserves_close_grace_without_changing_plan_timings(self):
        with patch.object(campaign.time, "monotonic", return_value=0):
            result = self.run_campaign(max_seconds=40)
        self.assertEqual(result["status"], "stopped_wall_limit")
        self.assertEqual(result["planned"]["usb_seconds"], 18)
        self.session.assert_not_called()

    def test_existing_evidence_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_campaign()
        self.session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
