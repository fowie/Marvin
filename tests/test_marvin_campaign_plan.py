import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_campaign_plan as campaign


ROOT = Path(__file__).resolve().parent.parent
ALLOWED_PAYLOADS = {3: {b""}, 4: {b""}, 27: {b""}, 29: {b""}, 38: {b""}, 46: {b"\x00", b"\x01"}}
SAFE_TEXT = {"?", "help", "h", "VER", "HWVER", "ADC", "READ", "HELP",
             "ver", "version", "VERSION", "info", "INFO", "status", "STATUS"}
# These pin the published audit citations, not a fresh verification of private files.
ARCHIVE_SOURCE_HASHES = {
    "command-enum": "1dee57075a29dda30a3cde4d2ea0a5606ffa316deea5472cf49f9469676afa72",
    "host-serial": "ba30d0db6e5d66fd5f316ac258de06bf74a3caeb07cf3493c355a13f32a4c430",
    "host-packetizer": "ce1b548c128c0912d71cdc84d7409dd2e6f76ffd40de4c654f1c50020304576b",
    "host-flash-query": "d32241211dbbbb0aa8b3143e958e078dc82253351330e71e69645435dcd53ee4",
    "host-manufacturing": "536d972d1ceb3fc96aaa60bd9c0942e8683d8215d9a265c1135b50c6c9b45626",
    "host-startup": "5a0415b09ef0f0dbca552f6c08df4090519c078035fe51ac45211a1e80c69a04",
    "calibration-arg": "e1ef9793e3d8c805f04e345b7264d35e8feecaee80f8fc725d00ba8370603e5f",
}


def independent_crc(data):
    """MSB-first polynomial0x8005 with reflected input/output, not production loop."""
    remainder = 0
    for byte in data:
        reflected = int(f"{byte:08b}"[::-1], 2)
        remainder ^= reflected << 8
        for _ in range(8):
            remainder = ((remainder << 1) ^ (0x8005 if remainder & 0x8000 else 0)) & 0xFFFF
    return int(f"{remainder:016b}"[::-1], 2)


def step_bytes(step):
    return bytes.fromhex("".join(step["chunks_hex"]))


def frames(data):
    position = 0
    packets = []
    while position < len(data):
        if data[position:position + 2] != b"\xef\xbe" or len(data) - position < 12:
            raise ValueError("Missing complete header.")
        sequence, command, response, length = struct.unpack_from("<HBBH", data, position + 2)
        end = position + 12 + length
        raw = data[position:end]
        if len(raw) != length + 12 or raw[-2:] != b"\xad\xde":
            raise ValueError("Length/footer mismatch.")
        if int.from_bytes(raw[-4:-2], "little") != independent_crc(raw[:-4]):
            raise ValueError("CRC mismatch.")
        packets.append({"sequence": sequence, "command": command, "response": response,
                        "payload": raw[8:-4], "raw": raw})
        position = end
    return packets


class CampaignPlanTests(unittest.TestCase):
    def test_deterministic_json_independent_results_and_profiles(self):
        for profile in ("quick", "full"):
            first, second = campaign.make_plan(profile), campaign.make_plan(profile)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
            self.assertEqual(first["schema_version"], 1)
            self.assertEqual(first["profile"], profile)
            self.assertTrue(first["exclusions"])
            self.assertTrue(first["limitations"])
            first["segments"][0]["steps"][0]["chunks_hex"][0] = "00"
            first["evidence"]["host-serial"]["citation"] = "changed"
            first["command_catalogue"][0]["reason"] = "changed"
            self.assertEqual(campaign.make_plan(profile), second)
        self.assertEqual(campaign.make_plan(), campaign.make_plan("full"))
        for profile in ("all", "FULL", "", None, 1, True, []):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                campaign.make_plan(profile)

    def test_shape_unique_kebab_ids_and_all_finite_bounds(self):
        segment_keys = {"id", "baudrate", "bytesize", "parity", "stopbits", "dtr", "rts", "steps"}
        step_keys = {"id", "chunks_hex", "interval_seconds", "response_seconds", "classification", "rationale"}
        for profile in ("quick", "full"):
            plan = campaign.make_plan(profile)
            ids = set()
            total_seconds = 0
            total_writes = total_bytes = total_steps = 0
            for segment in plan["segments"]:
                self.assertEqual(set(segment), segment_keys)
                self.assertRegex(segment["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                self.assertNotIn(segment["id"], ids)
                ids.add(segment["id"])
                self.assertIs(type(segment["baudrate"]), int)
                self.assertGreater(segment["baudrate"], 0)
                self.assertIn(segment["bytesize"], (7, 8))
                self.assertIn(segment["parity"], ("N", "E", "O"))
                self.assertIn(segment["stopbits"], (1, 2))
                self.assertIs(type(segment["dtr"]), bool)
                self.assertIs(type(segment["rts"]), bool)
                step_ids = set()
                seconds, writes, size = 3.0, 0, 0
                self.assertTrue(segment["steps"])
                for step in segment["steps"]:
                    self.assertEqual(set(step), step_keys)
                    self.assertRegex(step["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                    self.assertNotIn(step["id"], step_ids)
                    step_ids.add(step["id"])
                    self.assertIs(type(step["interval_seconds"]), float)
                    self.assertIs(type(step["response_seconds"]), float)
                    self.assertGreaterEqual(step["interval_seconds"], 0)
                    self.assertLessEqual(step["interval_seconds"], 0.1)
                    self.assertGreaterEqual(step["response_seconds"], 0.2)
                    self.assertLessEqual(step["response_seconds"], 3)
                    self.assertTrue(step["classification"])
                    self.assertTrue(step["rationale"])
                    self.assertTrue(step["chunks_hex"])
                    for chunk in step["chunks_hex"]:
                        self.assertRegex(chunk, r"^(?:[0-9a-f]{2}){1,32}$")
                    self.assertLessEqual(len(step_bytes(step)), 32)
                    self.assertLessEqual(len(step_bytes(step)), 256)
                    writes += len(step["chunks_hex"])
                    size += len(step_bytes(step))
                    seconds += step["response_seconds"] + (len(step["chunks_hex"]) - 1) * step["interval_seconds"]
                self.assertLessEqual(seconds, 85)
                self.assertLessEqual(writes, 256)
                self.assertLessEqual(size, 4096)
                # Even a hypothetical UART wire-time allowance fits this segment.
                char_bits = 1 + segment["bytesize"] + (segment["parity"] != "N") + segment["stopbits"]
                self.assertLessEqual(seconds + size * char_bits / segment["baudrate"], 85)
                summary = next(row for row in plan["summary"]["per_segment"] if row["id"] == segment["id"])
                self.assertEqual(summary["writes"], writes)
                self.assertEqual(summary["tx_bytes"], size)
                self.assertAlmostEqual(summary["scheduled_seconds"], seconds)
                total_seconds += seconds
                total_writes += writes
                total_bytes += size
                total_steps += len(step_ids)
            self.assertLessEqual(len(ids), 750 if profile == "full" else 400)
            self.assertEqual(plan["bounds"]["campaign_segments"], 750 if profile == "full" else 400)
            self.assertLessEqual(total_writes, 100000)
            self.assertLessEqual(total_bytes, 1048576)
            self.assertEqual(plan["summary"]["segments"], len(ids))
            self.assertEqual(plan["summary"]["steps"], total_steps)
            self.assertEqual(plan["summary"]["writes"], total_writes)
            self.assertEqual(plan["summary"]["tx_bytes"], total_bytes)
            self.assertAlmostEqual(plan["summary"]["scheduled_seconds"], total_seconds)
            if profile == "full":
                self.assertEqual(len(ids), 724)
                self.assertGreaterEqual(total_seconds, 30 * 60)
                self.assertAlmostEqual(total_seconds, 8017.94)
                self.assertEqual(plan["summary"]["usb_tail_seconds_per_segment"], 5.0)
                with_usb_tails = total_seconds + len(ids) * 5
                self.assertAlmostEqual(plan["summary"]["estimated_seconds_with_usb_tail"], with_usb_tails)
                self.assertLessEqual(with_usb_tails, 14400)
                self.assertEqual(plan["bounds"]["campaign_seconds"], 14400)

    def test_first_segment_is_actual_persistent_27_then_29_with_other_exact_queries(self):
        for profile in ("quick", "full"):
            plan = campaign.make_plan(profile)
            first = plan["segments"][0]
            self.assertEqual((first["baudrate"], first["bytesize"], first["parity"], first["stopbits"],
                              first["dtr"], first["rts"]), (115200, 8, "N", 1, True, True))
            packets = [frames(step_bytes(step))[0] for step in first["steps"]]
            self.assertEqual([(packet["command"], packet["sequence"]) for packet in packets[:2]], [(27, 0), (29, 1)])
            self.assertEqual(packets[0]["raw"].hex(), "efbe00001b00000016e7adde")
            self.assertEqual(packets[2]["command"], 4)
            self.assertEqual(set(packet["command"] for packet in packets), set(ALLOWED_PAYLOADS))
            self.assertTrue(plan["execution_contract"]["persistent_connection_per_segment"])
            self.assertEqual(plan["execution_contract"]["prelisten_seconds"], 1.0)
            self.assertEqual(plan["execution_contract"]["final_tail_seconds"], 2.0)
            self.assertFalse(plan["execution_contract"]["retry_uncertain_write"])
            self.assertTrue(plan["execution_contract"]["pause_on_any_rx"])
            self.assertEqual(packets[-1]["raw"], packets[-2]["raw"])
            self.assertEqual((packets[-1]["command"], packets[-1]["sequence"]), (4, 0))

    def test_all_valid_binary_crc_sequences_and_allowlisted_payloads(self):
        self.assertEqual(independent_crc(b"123456789"), 0xBB3D)
        all_sequences, observed = set(), set()
        for profile in ("quick", "full"):
            for segment in campaign.make_plan(profile)["segments"]:
                if not segment["id"].startswith("binary-"):
                    continue
                self.assertIn(segment["bytesize"], (7, 8))
                for step in segment["steps"]:
                    packets = frames(step_bytes(step))
                    self.assertIn(len(packets), (1, 2))
                    for packet in packets:
                        self.assertEqual(packet["response"], 0)
                        self.assertIn(packet["command"], ALLOWED_PAYLOADS)
                        self.assertIn(packet["payload"], ALLOWED_PAYLOADS[packet["command"]])
                        all_sequences.add(packet["sequence"])
                        observed.add((packet["command"], packet["payload"]))
                    stateful = any(packet["command"] in (27, 29) for packet in packets)
                    self.assertEqual(step["classification"], "stateful-query" if stateful else "source-query")
        self.assertTrue({0, 1, 65535} <= all_sequences)
        self.assertEqual(observed, {(command, payload) for command, payloads in ALLOWED_PAYLOADS.items() for payload in payloads})

    def test_byte_and_chunk_pacing_and_two_frame_coalescing(self):
        segments = campaign.make_plan()["segments"]
        for milliseconds in (1, 10, 50, 100):
            segment = next(s for s in segments if s["id"] == f"binary-pacing-byte-{milliseconds}ms")
            self.assertTrue(all(len(chunk) == 2 for step in segment["steps"] for chunk in step["chunks_hex"]))
            self.assertTrue(all(step["interval_seconds"] == milliseconds / 1000 for step in segment["steps"]))
        for name, cut in (("marker-10ms", 2), ("header-100ms", 8), ("trailer-10ms", None)):
            segment = next(s for s in segments if s["id"] == f"binary-pacing-{name}")
            for step in segment["steps"]:
                self.assertEqual(len(step["chunks_hex"]), 2)
                expected_cut = len(step_bytes(step)) - 4 if cut is None else cut
                self.assertEqual(len(bytes.fromhex(step["chunks_hex"][0])), expected_cut)
                self.assertEqual(len(frames(step_bytes(step))), 1)
        coalesced = next(s for s in segments if s["id"] == "binary-coalesced-pairs")
        for step in coalesced["steps"]:
            self.assertEqual(len(step["chunks_hex"]), 1)
            self.assertIn(len(step_bytes(step)), (24, 26))
            self.assertEqual(len(frames(step_bytes(step))), 2)

    def test_all_defined_command_ids_classified_and_unassigned_gaps_excluded(self):
        plan = campaign.make_plan()
        catalog = json.loads((ROOT / "data/protocol-catalog.json").read_text())
        declared = {entry["value"]: entry["name"] for entry in catalog["command_enum"]}
        self.assertEqual(len(declared), 58)
        self.assertEqual(len(declared), len(catalog["command_enum"]))
        self.assertTrue(plan["evidence"]["command-enum"]["path"].endswith(catalog["command_enum_source"]))
        entries = plan["command_catalogue"]
        self.assertEqual({entry["command"]: entry["name"] for entry in entries}, declared)
        self.assertEqual(len({entry["command"] for entry in entries}), len(entries))
        self.assertEqual({entry["command"] for entry in entries if entry["included"]}, set(ALLOWED_PAYLOADS))
        self.assertEqual(plan["unassigned_opcodes_excluded"], sorted(set(range(64)) - set(declared)))
        for entry in entries:
            self.assertIs(type(entry["included"]), bool)
            self.assertTrue(entry["reason"])
            self.assertTrue(entry["classification"])
            self.assertTrue(entry["evidence"])
            self.assertTrue(all(key in plan["evidence"] for key in entry["evidence"]))
        for name in ("InitReflash", "ReflashBlock", "ResetIoBoard", "ResetPC", "HostHeartbeat",
                     "SetDriveVelocities", "RecalibrateServo", "CommitCalibrationParams", "SetPowerState"):
            self.assertFalse(next(entry for entry in entries if entry["name"] == name)["included"])

    def test_archived_hash_citations_and_optional_getter_audit_are_preserved(self):
        plan = campaign.make_plan()
        for key, source in plan["evidence"].items():
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(source["citation"])
            self.assertTrue(source["path"].startswith(("private-archive/", "local-evidence/")))
            self.assertNotIn("session-state", source["path"])
            if key in ARCHIVE_SOURCE_HASHES:
                self.assertEqual(source["sha256"], ARCHIVE_SOURCE_HASHES[key])
        for command in (3, 38, 46):
            entry = next(entry for entry in plan["command_catalogue"] if entry["command"] == command)
            self.assertIn("getter-audit", entry["evidence"])
            self.assertIn("io-firmware", entry["evidence"])
        cal = next(entry for entry in plan["command_catalogue"] if entry["command"] == 46)
        self.assertEqual(cal["request_payloads_hex"], ["00", "01"])
        self.assertIn("Pack=1, one byte flashCal", plan["evidence"]["calibration-arg"]["citation"])
        self.assertIn("0x884a", plan["evidence"]["io-firmware"]["citation"])
        self.assertIn("0x7f16", plan["evidence"]["io-firmware"]["citation"])

    def test_calibration_query_has_exactly_one_selector_byte_and_no_address(self):
        for selector in range(256):
            payload = bytes([selector])
            if selector in (0, 1):
                encoded = campaign._request(46, 0, payload)
                self.assertEqual(len(encoded), 13)
                self.assertEqual(encoded[6:8], b"\x01\x00")
                self.assertEqual(frames(encoded)[0]["payload"], payload)
            else:
                with self.subTest(selector=selector), self.assertRaises(ValueError):
                    campaign._request(46, 0, payload)
        for payload in (b"", b"\x00\x00", b"\x01\x00", b"\x00" * 8):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                campaign._request(46, 0, payload)

    def test_full_settings_and_explicit_seven_bit_cdc_hypotheses(self):
        plan = campaign.make_plan()
        segments = plan["segments"]
        self.assertEqual({s["baudrate"] for s in segments}, {4800, 9600, 19200, 38400, 57600, 115200, 230400})
        self.assertEqual({(s["bytesize"], s["parity"], s["stopbits"]) for s in segments},
                         {(bits, parity, stops) for bits in (7, 8) for parity in ("N", "E", "O") for stops in (1, 2)})
        self.assertEqual({(s["dtr"], s["rts"]) for s in segments},
                         {(dtr, rts) for dtr in (False, True) for rts in (False, True)})
        for segment in segments:
            if segment["bytesize"] == 7:
                if segment["id"].startswith("binary-"):
                    self.assertTrue(segment["id"].startswith("binary-grid-"))
                    for step in segment["steps"]:
                        self.assertIn("CDC-line-coding hypothesis", step["rationale"])
                        self.assertIn("not valid7-bit UART binary encoding", step["rationale"])
                        self.assertIn("never strip", step["rationale"])
                        self.assertEqual(step_bytes(step)[:2], b"\xef\xbe")
                else:
                    self.assertTrue(segment["id"].startswith("text-"))
                    self.assertTrue(all(byte < 128 for step in segment["steps"] for byte in step_bytes(step)))
        self.assertEqual(plan["execution_contract"]["flow_control"], "none")

    def test_exact_cartesian_grid_completeness_and_seed_sets(self):
        plan = campaign.make_plan()
        expected = {
            (baud, bits, parity, stops, dtr, rts)
            for baud in (4800, 9600, 19200, 38400, 57600, 115200, 230400)
            for bits in (7, 8) for parity in ("N", "E", "O") for stops in (1, 2)
            for dtr in (False, True) for rts in (False, True)
        }
        self.assertEqual(len(expected), 336)
        binary_seeds = [(27, b""), (29, b""), (4, b""), (38, b""), (3, b""), (46, b"\x00"), (46, b"\x01")]
        ascii_seeds = [b"?\r", b"help\r", b"VER\r", b"version\r", b"status\r", b"?\n", b"?\r\n"]
        for prefix in ("binary-grid-", "text-grid-"):
            segments = [s for s in plan["segments"] if s["id"].startswith(prefix)]
            self.assertEqual(len(segments), 336)
            self.assertEqual({
                (s["baudrate"], s["bytesize"], s["parity"], s["stopbits"], s["dtr"], s["rts"])
                for s in segments
            }, expected)
            for segment in segments:
                self.assertEqual(len(segment["steps"]), 7)
                for step in segment["steps"]:
                    self.assertEqual(step["response_seconds"], 0.75)
                    self.assertEqual(step["interval_seconds"], 0.0)
                    self.assertEqual(len(step["chunks_hex"]), 1)
                if prefix == "binary-grid-":
                    parsed = [frames(step_bytes(step)) for step in segment["steps"]]
                    self.assertTrue(all(len(packets) == 1 for packets in parsed))
                    self.assertEqual([(packets[0]["command"], packets[0]["payload"]) for packets in parsed], binary_seeds)
                    self.assertEqual([packets[0]["sequence"] for packets in parsed], list(range(7)))
                else:
                    self.assertEqual([step_bytes(step) for step in segment["steps"]], ascii_seeds)
        grid = plan["coverage"]["cartesian_grid"]
        self.assertEqual(grid["settings_combinations"], 336)
        self.assertEqual(grid["binary_segments"], 336)
        self.assertEqual(grid["ascii_segments"], 336)
        self.assertEqual(grid["binary_7bit_cdc_hypothesis_segments"], 168)
        self.assertEqual(grid["binary_steps_per_setting"], 7)
        self.assertEqual(grid["ascii_steps_per_setting"], 7)
        self.assertEqual(grid["response_seconds"], 0.75)
        self.assertEqual({s[0] for s in expected}, set(grid["baudrates"]))
        self.assertEqual({f"{s[1]}{s[2]}{s[3]}" for s in expected}, set(grid["serial_formats"]))
        self.assertEqual({s[4:] for s in expected}, {tuple(s) for s in grid["control_line_states"]})
        self.assertEqual(grid["binary_seeds"], [
            {"command": command, "payload_hex": payload.hex(), "sequence": index}
            for index, (command, payload) in enumerate(binary_seeds)
        ])
        self.assertEqual(grid["ascii_seeds_hex"], [seed.hex() for seed in ascii_seeds])
        self.assertEqual(plan["summary"]["steps"], 5617)
        self.assertEqual(plan["summary"]["writes"], 6553)
        self.assertEqual(plan["summary"]["tx_bytes"], 47651)
        self.assertAlmostEqual(plan["summary"]["estimated_seconds_with_usb_tail"], 11637.94)

    def test_execution_segments_remain_identical_after_citation_sanitization(self):
        quick = campaign.make_plan("quick")
        self.assertEqual(len(quick["segments"]), 5)
        self.assertNotIn("cartesian_grid", quick["coverage"])
        for profile, digest in (
            ("quick", "067fe0a93917f31bcf9b4b981d4b8b8b8b00d72a810f7ca58f625a77644ea181"),
            ("full", "41fb4132088b5b041b5823ff448fc9599a7d17aaa85f7314120273c677d46933"),
        ):
            plan = campaign.make_plan(profile)
            encoded = json.dumps(plan["segments"], sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), digest)
            self.assertTrue(any("not recommended" in note for note in plan["limitations"]))
            self.assertTrue(any("private archive" in note for note in plan["limitations"]))

    def test_cartesian_campaign_caps_reject_oversized_and_overlong_plans(self):
        template = campaign.make_plan()["segments"][0]
        too_many = [dict(template, id=f"limit-{index}", steps=template["steps"][:1]) for index in range(751)]
        with self.assertRaisesRegex(ValueError, "global bounds"):
            campaign._summarize_and_check(too_many, full_grid=True)
        too_long = [dict(template, id=f"limit-{index}") for index in range(300)]
        with self.assertRaisesRegex(ValueError, "duration bound"):
            campaign._summarize_and_check(too_long, full_grid=True)

    def test_exact_text_vocabulary_terminators_and_unsafe_verbs_absent(self):
        plan = campaign.make_plan()
        terms = set()
        vocabulary = set()
        banned = {"PING", "IN", "OUT", "HIGH", "LOW", "GO", "GOSPD", "TRVL", "TURN", "ACC",
                  "STOP", "RST", "VERB", "SGP", "SPNG", "BLINK", "BLNK", "LED", "ECHO",
                  "RESET", "FLASH", "ERASE", "UNLOCK", "BOOT", "REBOOT", "WRITE"}
        for segment in plan["segments"]:
            if not segment["id"].startswith("text-"):
                continue
            for step in segment["steps"]:
                data = step_bytes(step)
                suffix = next((ending for ending in (b"\r\n", b"\r", b"\n") if data.endswith(ending)), None)
                self.assertIsNotNone(suffix)
                terms.add(suffix)
                body = data[:-len(suffix)].decode("ascii")
                self.assertNotIn(body.upper(), banned)
                if body:
                    self.assertIn(body, SAFE_TEXT)
                    vocabulary.add(body)
                self.assertEqual(step["classification"], "text-hypothesis")
                self.assertIn("historical-text", step["rationale"])
        self.assertEqual(terms, {b"\r", b"\n", b"\r\n"})
        self.assertEqual(vocabulary, SAFE_TEXT)

    def test_valid_binary_then_text_then_malformed_order(self):
        for profile in ("quick", "full"):
            segments = campaign.make_plan(profile)["segments"]
            phases = [{"binary": 0, "text": 1, "malformed": 2}[s["id"].split("-")[0]] for s in segments]
            self.assertEqual(phases, sorted(phases))
            self.assertEqual(set(phases), {0, 1, 2})
            for segment in segments:
                if segment["id"].startswith("malformed-"):
                    self.assertEqual(len(segment["steps"]), 1)
                    self.assertEqual(segment["steps"][0]["classification"], "malformed-hypothesis")

    def test_only_defensible_exact_getconfig_mutations_in_terminal_phase(self):
        plan = campaign.make_plan()
        mutations = {s["id"]: step_bytes(s["steps"][0]) for s in plan["segments"] if s["id"].startswith("malformed-")}
        baseline = bytes.fromhex("efbe0000040000001133adde")
        self.assertEqual(set(mutations), {"malformed-crc-bit", "malformed-footer-bit",
                                         "malformed-header-order", "malformed-extra-payload"})
        for name, data in mutations.items():
            self.assertEqual(data[4], 4)
            self.assertEqual(data[5], 0)
            if name == "malformed-extra-payload":
                parsed = frames(data)
                self.assertEqual(parsed[0]["payload"], b"\x00")
                self.assertNotIn(parsed[0]["payload"], ALLOWED_PAYLOADS[4])
            else:
                with self.assertRaises(ValueError):
                    frames(data)
        bad_crc = mutations["malformed-crc-bit"]
        self.assertEqual([i for i in range(12) if baseline[i] != bad_crc[i]], [8])
        self.assertEqual(baseline[8] ^ bad_crc[8], 1)
        bad_footer = mutations["malformed-footer-bit"]
        self.assertEqual([i for i in range(12) if baseline[i] != bad_footer[i]], [11])
        bad_header = mutations["malformed-header-order"]
        self.assertEqual(bad_header[:2], b"\xbe\xef")
        self.assertEqual(bad_header[2:8], baseline[2:8])
        self.assertEqual(int.from_bytes(bad_header[-4:-2], "little"), independent_crc(bad_header[:-4]))

    def test_no_unsafe_request_shapes_via_internal_encoder(self):
        for command in range(256):
            if command not in ALLOWED_PAYLOADS:
                with self.subTest(command=command), self.assertRaises(ValueError):
                    campaign._request(command, 0)
        for command, payload in ((4, b"\x00"), (3, b"\x00" * 8), (46, b""), (46, b"\x02"),
                                 (46, b"\x00\x00"), (27, b"\x00")):
            with self.subTest(command=command, payload=payload), self.assertRaises(ValueError):
                campaign._request(command, 0, payload)
        for sequence in (-1, 65536, True, 1.5):
            with self.assertRaises(ValueError):
                campaign._request(4, sequence)

    def test_generator_has_no_runtime_reads_hardware_or_transport_imports(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected file read")), \
                patch.object(Path, "open", side_effect=AssertionError("unexpected Path.open")):
            campaign.make_plan()
            campaign.make_plan("quick")
        tree = ast.parse(Path(campaign.__file__).read_text())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module)
        self.assertEqual(modules, {"argparse", "copy", "json", "pathlib", "struct", "sys", "tools.marvin_protocol"})
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
before = set(sys.modules)
with patch('builtins.open', side_effect=AssertionError('file I/O')), \\
     patch.object(Path, 'open', side_effect=AssertionError('path I/O')):
    from tools.marvin_campaign_plan import make_plan
    assert make_plan()['schema_version'] == 1
new = set(sys.modules) - before
assert not {'tools.marvin_probe', 'tools.marvin_session', 'tools.marvin_usbmon', 'serial', 'usb', 'socket'} & new
"""
        subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True, text=True, check=True)

    def test_cli_stdout_is_same_json_plan(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(campaign.main(["--profile", "quick"]), 0)
        self.assertEqual(json.loads(output.getvalue()), campaign.make_plan("quick"))
        result = subprocess.run([sys.executable, "-B", "-m", "tools.marvin_campaign_plan", "--profile", "quick"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), campaign.make_plan("quick"))
        self.assertEqual(result.stderr, "")

    def test_standalone_cli_is_independent_of_cwd_and_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            for cwd in (ROOT, Path(directory)):
                for profile in ("quick", "full"):
                    with self.subTest(cwd=cwd, profile=profile):
                        result = subprocess.run(
                            [sys.executable, "-I", "-B", str(ROOT / "tools/marvin_campaign_plan.py"),
                             "--profile", profile],
                            cwd=cwd, capture_output=True, text=True, check=True,
                        )
                        self.assertEqual(json.loads(result.stdout), campaign.make_plan(profile))
                        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
