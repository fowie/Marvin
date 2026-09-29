import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import marvin_campaign as campaign
from tools import marvin_campaign_plan, marvin_probe, marvin_protocol, marvin_session


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
    def test_bounded_numbers_keep_finite_integer_and_float_endpoints(self):
        for minimum, maximum in ((0, 0.1), (0.2, 3), (1, 14400)):
            for value in (minimum, maximum, float(minimum), float(maximum)):
                with self.subTest(minimum=minimum, maximum=maximum, value=value):
                    campaign.bounded_number(value, minimum, maximum, "Window")
            for value in (10**500, -(10**500), minimum - 1, maximum + 1,
                          float("nan"), float("inf"), float("-inf"), True, False, None, "1"):
                with self.subTest(minimum=minimum, maximum=maximum, value=value), \
                        self.assertRaisesRegex(ValueError, "Window must be finite"):
                    campaign.bounded_number(value, minimum, maximum, "Window")

    def test_oversized_json_step_intervals_are_normal_validation_errors(self):
        for field in ("interval_seconds", "response_seconds"):
            for value in (10**500, -(10**500)):
                invalid = plan()
                invalid["segments"][0]["steps"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    campaign.validate_plan(json.loads(json.dumps(invalid)))

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

    def test_all_generated_profiles_still_validate_including_fixed_experiments(self):
        for profile in ("quick", "full"):
            generated = marvin_campaign_plan.make_plan(profile)
            coverage = campaign.validate_plan(generated)
            self.assertEqual(coverage["application_bytes"], generated["summary"]["tx_bytes"])

    def test_unframed_bytes_split_headers_and_unterminated_text_are_rejected(self):
        for chunks in (["80"], ["beef0000080000000000adde"], ["ef", "be0000080000000000adde"],
                       ["efbe0000040000001133adde80"], ["68656c70"], ["0b68656c700d"]):
            value = plan()
            value["segments"][0]["steps"][0]["chunks_hex"] = chunks
            with self.subTest(chunks=chunks), self.assertRaises(ValueError):
                campaign.validate_plan(value)
        value = plan()
        value["segments"][0]["steps"][0]["chunks_hex"] = ["ef"]
        value["segments"][0]["steps"][1]["chunks_hex"] = ["be0000080000000000adde"]
        with self.assertRaises(ValueError):
            campaign.validate_plan(value)

    def test_fixed_malformed_cases_cannot_prefix_more_bytes_or_steps(self):
        segment = marvin_campaign_plan.make_plan("quick")["segments"][-1]
        campaign.compile_segment(segment)
        segment["steps"].append(plan()["segments"][0]["steps"][0])
        with self.assertRaises(ValueError):
            campaign.compile_segment(segment)

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
    def test_completed_metadata_with_evidence_gaps_never_advances_even_on_rx(self):
        schedule, _ = campaign.compile_segment(plan()["segments"][0])
        for field in ("unretained_partial_line_bytes", "unprocessed_records",
                      "unprocessed_record_bytes", "unaccounted_retained_bytes"):
            for rx in (False, True):
                with self.subTest(field=field, rx=rx), tempfile.TemporaryDirectory() as directory:
                    result = fixture(directory, schedule, rx=rx)
                    result["usb"][field] = 1
                    with patch.object(campaign.marvin_usbmon, "read_analyzed_records") as analyze:
                        with self.assertRaisesRegex(ValueError, "Incomplete USB capture"):
                            campaign.assess_segment(directory, result, schedule)
                    analyze.assert_not_called()

    def test_rejected_settings_with_incomplete_interrupted_usb_are_not_skipped(self):
        for field in ("unretained_partial_line_bytes", "unprocessed_records",
                      "unprocessed_record_bytes", "unaccounted_retained_bytes"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                rejected_fixture(directory)
                path = Path(directory) / "usb/metadata.json"
                metadata = json.loads(path.read_text())
                metadata[field] = 1
                path.write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, "Incomplete USB capture"):
                    campaign.rejected_settings_evidence(directory, BASELINE)

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
        self.assertIs(options["allow_line_state_trial"], True)
        return fixture(output, options["probe_schedule"], rx=self.rx)

    def run_campaign(self, **overrides):
        options = {"actuators_isolated": True, "allow_unknown_command": True,
                   "allow_telemetry_state_change": True, "allow_line_state_trials": True,
                   "switch_position": "RUN"}
        options.update(overrides)
        return campaign.run_campaign(plan(), self.output, **options)

    def test_oversized_wall_limits_are_rejected_before_preflight(self):
        for value in (10**500, -(10**500)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "wall-clock limit"):
                self.run_campaign(max_seconds=value)
        self.preflight.assert_not_called()
        self.session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cli_reports_oversized_plan_numbers_without_a_traceback(self):
        invalid = plan()
        invalid["segments"][0]["steps"][0]["response_seconds"] = 10**500
        argv = ["marvin_campaign", "--run", "--output", str(self.output),
                "--actuators-isolated", "--allow-unknown-command",
                "--allow-telemetry-state-change", "--allow-line-state-trials",
                "--switch-position", "RUN"]
        stderr = io.StringIO()
        with patch.object(campaign.sys, "argv", argv), \
                patch.object(marvin_campaign_plan, "make_plan",
                             return_value=json.loads(json.dumps(invalid))), \
                redirect_stderr(stderr):
            self.assertEqual(campaign.main(), 1)
        self.assertIn("Campaign stopped: Response window", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.preflight.assert_not_called()
        self.session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_matched_empty_in_error_stops_before_the_next_segment(self):
        def record_with_error(port, output, **options):
            result = self.record(port, output, **options)
            with (output / "usb/usbmon.txt").open("a") as stream:
                stream.write("ee 201 S Bi:1:018:2 -115 64 <\nee 202 C Bi:1:018:2 -71 0\n")
            return result

        self.session.side_effect = record_with_error
        with self.assertRaisesRegex(ValueError, "USB transfer-status errors"):
            self.run_campaign()
        self.session.assert_called_once()
        self.assertFalse((self.output / "segment-b").exists())
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["segments"][0]["status"], "failed")
        self.assertEqual(metadata["segments"][0]["partial"]["application_bytes_written"], 24)
        self.assertTrue((self.output / "SHA256SUMS").is_file())

    def test_dangling_output_symlink_cannot_redirect_evidence(self):
        root = Path(self.temp.name)
        for relative in (False, True):
            with self.subTest(relative=relative):
                target = root / f"missing-target-{relative}"
                self.output = root / f"output-link-{relative}"
                destination = Path(target.name) if relative else target
                self.output.symlink_to(destination, target_is_directory=True)
                with self.assertRaises(FileExistsError) as raised:
                    self.run_campaign()
                self.assertEqual(raised.exception.filename, str(self.output))
                self.assertEqual(self.output.readlink(), destination)
                self.assertFalse(target.exists())
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.session.assert_not_called()

    def test_new_nested_output_parents_remain_supported(self):
        self.output = self.output / "missing" / "nested" / "campaign"
        result = self.run_campaign()
        self.assertEqual(result["status"], "completed_silent")
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.preflight.assert_called_once()
        self.assertEqual(self.session.call_count, 2)

    def test_silent_campaign_records_all_segments_and_hashes(self):
        result = self.run_campaign()
        self.assertEqual(result["status"], "completed_silent")
        self.assertEqual(result["application_bytes_confirmed"], 48)
        self.assertEqual(result["usb_close_grace_seconds"], 30)
        self.assertEqual(result["planned"]["usb_seconds"], 18)
        self.assertIs(result["line_state_trials_authorized"], True)
        recorded = json.loads((self.output / "metadata.json").read_text())
        self.assertIs(recorded["line_state_trials_authorized"], True)
        self.assertEqual(self.session.call_count, 2)
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            digest, path = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.output / path).read_bytes()).hexdigest(), digest)
        encoded = json.dumps(plan(), sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(result["plan_sha256"], hashlib.sha256(encoded).hexdigest())

    def test_non_boolean_authorizations_stop_before_preflight(self):
        for name in ("actuators_isolated", "allow_unknown_command",
                     "allow_telemetry_state_change", "allow_line_state_trials", "sudo_usbmon"):
            for value in (1, 0, "false", "true", None, [], [True]):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, "boolean"):
                    self.run_campaign(**{name: value})
        self.preflight.assert_not_called()
        self.session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_sealing_failure_invalidates_all_completion_states_and_preserves_campaign_failure(self):
        for index, (rx, rejected, failure, before) in enumerate((
            (False, False, False, "completed_silent"),
            (True, False, False, "stopped_on_rx"),
            (False, True, False, "completed_with_unsupported_settings"),
            (False, False, True, "failed"),
        )):
            with self.subTest(before=before):
                self.output = Path(self.temp.name) / f"seal-{index}"
                self.rx, self.settings_rejected, self.failure = rx, rejected, failure
                with patch.object(marvin_session, "evidence_manifest", side_effect=OSError("manifest failed")), \
                        self.assertRaises(OSError) as raised:
                    self.run_campaign()
                self.assertEqual(str(raised.exception), "uncertain write" if failure else "manifest failed")
                metadata = json.loads((self.output / "metadata.json").read_text())
                self.assertEqual(metadata["status"], "failed")
                self.assertEqual(metadata["status_before_sealing"], before)
                self.assertEqual(metadata["evidence_sealing_error"], "manifest failed")
                if failure:
                    self.assertEqual(metadata["error"], "uncertain write")
                    self.assertEqual(metadata["segments"][0]["partial"]["transmit_status"], "unknown")

    def test_missing_line_authorization_stops_before_preflight_or_output(self):
        for lines in ((True, True), (True, False), (False, False), (False, True)):
            value = plan()
            for segment in value["segments"]:
                segment["dtr"], segment["rts"] = lines
            with self.subTest(lines=lines), self.assertRaisesRegex(ValueError, "line-state"):
                campaign.run_campaign(
                    value, self.output, actuators_isolated=True, allow_unknown_command=True,
                    allow_telemetry_state_change=True, switch_position="RUN",
                )
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_authorized_line_matrix_is_forwarded_without_changing_plan(self):
        value = plan()
        value["segments"] = [
            dict(value["segments"][0], id=f"lines-{index}", dtr=dtr, rts=rts)
            for index, (dtr, rts) in enumerate(
                ((True, True), (True, False), (False, False), (False, True)))
        ]
        original = copy.deepcopy(value)
        result = campaign.run_campaign(
            value, self.output, actuators_isolated=True, allow_unknown_command=True,
            allow_telemetry_state_change=True, allow_line_state_trials=True,
            switch_position="RUN",
        )
        self.assertEqual(result["status"], "completed_silent")
        self.assertEqual(value, original)
        self.assertEqual(json.loads((self.output / "plan.json").read_text()), original)
        for call, segment in zip(self.session.call_args_list, original["segments"], strict=True):
            self.assertIs(call.kwargs["allow_line_state_trial"], True)
            for key in ("baudrate", "dtr", "rts", "bytesize", "parity", "stopbits"):
                self.assertEqual(call.kwargs[key], segment[key])
            schedule, seconds = campaign.compile_segment(segment)
            self.assertEqual(call.kwargs["probe_schedule"], schedule)
            self.assertEqual(call.kwargs["seconds"], seconds)
            self.assertEqual(call.kwargs["probe_profile"], "experimental-successor")

    def test_cli_requires_and_forwards_separate_line_state_authorization(self):
        argv = [
            "marvin_campaign", "--run", "--output", str(self.output),
            "--actuators-isolated", "--allow-unknown-command",
            "--allow-telemetry-state-change", "--switch-position", "RUN",
        ]
        with patch.object(marvin_campaign_plan, "make_plan", return_value=plan()):
            with patch("sys.argv", argv), redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(campaign.main(), 1)
            self.assertIn("line-state", errors.getvalue())
            self.preflight.assert_not_called()
            self.identity.assert_not_called()
            self.session.assert_not_called()
            self.assertFalse(self.output.exists())
            with patch("sys.argv", [*argv, "--allow-line-state-trials"]), redirect_stdout(io.StringIO()):
                self.assertEqual(campaign.main(), 0)
        self.assertEqual(self.session.call_count, 2)
        recorded = json.loads((self.output / "metadata.json").read_text())
        self.assertIs(recorded["line_state_trials_authorized"], True)

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
                       {"allow_telemetry_state_change": False}, {"allow_line_state_trials": False},
                       {"switch_position": "PRG"}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.run_campaign(**option)
        with patch.object(campaign.os, "geteuid", return_value=0), self.assertRaises(ValueError):
            self.run_campaign()
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_fingerprint_never_opens_a_port(self):
        self.preflight.return_value["usb"]["descriptors_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.run_campaign()
        self.session.assert_not_called()

    def test_wall_limit_reserves_close_grace_without_changing_plan_timings(self):
        with patch.object(campaign.time, "monotonic", return_value=0):
            result = self.run_campaign(max_seconds=40)
        self.assertEqual(result["status"], "stopped_wall_limit")
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["planned"]["usb_seconds"], 18)
        self.session.assert_not_called()

    def test_existing_evidence_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_campaign()
        self.preflight.assert_not_called()
        self.session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
