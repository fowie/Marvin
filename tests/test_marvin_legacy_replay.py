from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import marvin_legacy_replay as replay
from tests.test_marvin_legacy_protocol import OBSERVED_REPLY, OBSERVED_UNIT_INFO_REPLY, frame
from tests.test_marvin_legacy_telemetry import OBSERVED_RAW_DATA_REPLY, OBSERVED_POWER_REPLY


def chunks_for(parts):
    rows = []
    offset = 0
    for index, data in enumerate(parts):
        rows.append({
            "offset": offset, "size": len(data), "hex": data.hex(),
            "elapsed_seconds": index * 0.1,
            "at": f"2026-09-14T23:00:{index:02d}+00:00",
        })
        offset += len(data)
    return rows


class LegacyReplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.received = self.root / "received.bin"
        self.chunks = self.root / "chunks.jsonl"

    def write_capture(self, data, rows=None):
        self.received.write_bytes(data)
        if rows is not None:
            self.chunks.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_observed_reply_timestamps_hashes_and_read_only_replay(self):
        parts = [OBSERVED_REPLY[:1], OBSERVED_REPLY[1:7], OBSERVED_REPLY[7:32], OBSERVED_REPLY[32:]]
        rows = chunks_for(parts)
        self.write_capture(OBSERVED_REPLY, rows)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()}
        result = replay.replay_capture(
            self.received, chunks_path=self.chunks, evidence="recorded", direction="received",
            expected_config_sequence=0,
        )
        self.assertEqual(result["counts"], {"frame": 1})
        self.assertEqual(result["status"], "decoded")
        self.assertEqual(result["source"]["bytes"], 118)
        self.assertEqual(result["source"]["sha256"], hashlib.sha256(OBSERVED_REPLY).hexdigest())
        self.assertEqual(result["source"]["chunks"]["coverage"], "exact")
        event = result["events"][0]
        self.assertEqual((event["offset"], event["end_offset"]), (0, 118))
        self.assertEqual((event["chunk_timing"]["first_line"], event["chunk_timing"]["last_line"]), (1, 4))
        self.assertEqual(event["chunk_timing"]["last_at"], rows[-1]["at"])
        self.assertEqual(event["get_config_match"]["status"], "integrity_and_shape_match")
        self.assertEqual(event["packet"]["payload_hex"], OBSERVED_REPLY[7:-3].hex())
        self.assertEqual(result["application_acknowledgment"], "not_established")
        self.assertNotIn("interpretation", event)
        self.assertNotIn("telemetry", event)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()})

    def test_default_declarations_empty_and_outgoing_echo_never_infer_received(self):
        self.write_capture(b"", [])
        result = replay.replay_capture(self.received, chunks_path=self.chunks)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["evidence_kind"], "unspecified")
        self.assertEqual(result["direction"], "unknown")
        self.write_capture(frame(status=0) + OBSERVED_REPLY)
        for direction in ("unknown", "outgoing", "received"):
            result = replay.replay_capture(self.received, direction=direction, evidence="synthetic")
            self.assertEqual(result["direction"], direction)
            self.assertEqual(result["evidence_kind"], "synthetic")
            self.assertEqual(result["application_acknowledgment"], "not_established")
            self.assertTrue(all("get_config_match" not in e for e in result["events"]))
        result = replay.replay_capture(self.received, direction="received", expected_config_sequence=0)
        self.assertEqual([e["get_config_match"]["status"] for e in result["events"]],
                         ["not_match", "integrity_and_shape_match"])

    def test_legacy_telemetry_is_explicit_opt_in_and_preserves_direction_evidence(self):
        data = OBSERVED_RAW_DATA_REPLY + OBSERVED_UNIT_INFO_REPLY + OBSERVED_POWER_REPLY + OBSERVED_REPLY + frame(command=0, status=0)
        self.write_capture(data)
        default = replay.replay_capture(self.received, direction="received", evidence="recorded")
        self.assertEqual(default, replay.replay_capture(self.received, direction="received", evidence="recorded", telemetry=False))
        self.assertTrue(all("interpretation" not in e for e in default["events"]))
        result = replay.replay_capture(self.received, direction="received", evidence="recorded", telemetry=True)
        interpretations = [e["interpretation"] for e in result["events"]]
        self.assertEqual([i["status"] for i in interpretations], ["decoded", "decoded", "decoded", "raw", "raw"])
        self.assertEqual(interpretations[0]["fields"]["tick"]["unsigned"], 0x6821C)
        self.assertEqual(interpretations[1]["fields"]["reportedSerialNumber"]["hex"], "0x01020304")
        self.assertEqual(interpretations[2]["fields"]["powerState"]["hex"], "0x0eff")
        self.assertTrue(all(i["direction"] == "received" and i["evidence_kind"] == "recorded" for i in interpretations))
        self.assertTrue(all(i["application_acknowledgment"] == "not_established" for i in interpretations))
        self.assertEqual(result["application_acknowledgment"], "not_established")
        for direction in ("unknown", "outgoing"):
            result = replay.replay_capture(self.received, telemetry=True, direction=direction, evidence="synthetic")
            self.assertTrue(all(e["interpretation"]["status"] == "raw" for e in result["events"]))
            self.assertTrue(all(e["interpretation"]["evidence_kind"] == "synthetic" for e in result["events"]))
        self.assertEqual(self.received.read_bytes(), data)

    def test_cli_legacy_telemetry_flag_does_not_assume_received(self):
        self.write_capture(OBSERVED_RAW_DATA_REPLY)
        for args, expected in (
            ([], None), (["--telemetry"], "raw"),
            (["--telemetry", "--direction", "received", "--evidence", "synthetic"], "decoded"),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(replay.main([str(self.received), *args]), 0)
            event = json.loads(output.getvalue())["events"][0]
            if expected is None:
                self.assertNotIn("interpretation", event)
            else:
                self.assertEqual(event["interpretation"]["status"], expected)

    def test_opaque_unknown_status_size_id_and_explicit_mismatch_keep_raw(self):
        data = frame(b"SE" + OBSERVED_REPLY, command=255, status=0xFF)
        data += frame(b"short") + frame(bytes(108), status=0x81) + frame(bytes(108), sequence=1)
        self.write_capture(data)
        result = replay.replay_capture(self.received, direction="received", expected_config_sequence=0)
        self.assertEqual(result["counts"], {"frame": 4})
        self.assertEqual(result["events"][0]["packet"]["command"], 255)
        self.assertEqual(result["events"][0]["packet"]["response_field"], 0xFF)
        self.assertTrue(all(e["get_config_match"]["status"] == "not_match" for e in result["events"]))
        self.assertEqual(bytes.fromhex("".join(e["raw_hex"] for e in result["events"])), data)

    def test_noise_damage_partial_partition_and_no_eof_suffix_recovery(self):
        bad = bytearray(OBSERVED_REPLY)
        bad[-3] ^= 1
        data = b"noise" + bytes(bad) + OBSERVED_REPLY + b"S\x00"
        self.write_capture(data)
        result = replay.replay_capture(self.received)
        self.assertEqual(result["status"], "decoded_with_diagnostics")
        self.assertEqual(result["counts"], {"noise": 1, "error": 1, "frame": 1, "partial": 1})
        offset = 0
        for event in result["events"]:
            self.assertEqual(event["offset"], offset)
            offset = event["end_offset"]
        self.assertEqual(bytes.fromhex("".join(e["raw_hex"] for e in result["events"])), data)
        unfinished = b"S\x00\x00\x04\x80\x00\x04" + OBSERVED_REPLY
        self.write_capture(unfinished)
        result = replay.replay_capture(self.received)
        self.assertEqual(result["counts"], {"partial": 1})
        self.assertFalse(result["limits"]["recover_at_eof"])

    def test_chunk_offsets_sizes_hex_coverage_and_timing_inconsistencies(self):
        valid = chunks_for([OBSERVED_REPLY[:10], OBSERVED_REPLY[10:]])
        changes = (
            lambda r: r[0].update(offset=1),
            lambda r: r[1].update(offset=9),
            lambda r: r[1].update(offset=11),
            lambda r: r[0].update(offset=False),
            lambda r: r[0].update(size=0),
            lambda r: r[0].update(size=True),
            lambda r: r[1].update(size=1000),
            lambda r: r[0].update(hex="00" * 10),
            lambda r: r[0].update(hex="zz" * 10),
            lambda r: r[0].update(hex="53 00"),
            lambda r: r[1].update(elapsed_seconds=-0.1),
            lambda r: r[0].update(elapsed_seconds=-1),
            lambda r: r[0].update(elapsed_seconds=True),
            lambda r: r[0].update(elapsed_seconds=float("nan")),
            lambda r: r[0].update(elapsed_seconds=float("inf")),
            lambda r: r[0].update(at="2026-09-14T23:00:00"),
            lambda r: r[0].update(at="bad"),
            lambda r: r[0].pop("at"),
            lambda r: r.pop(),
        )
        for index, change in enumerate(changes):
            rows = copy.deepcopy(valid)
            change(rows)
            self.write_capture(OBSERVED_REPLY, rows)
            with self.subTest(index=index), self.assertRaises(ValueError):
                replay.replay_capture(self.received, chunks_path=self.chunks)
        for content in (b"", b"\n", b"[]\n", b"{}\n", b"{broken}\n", b"\xff\n",
                        b'{"offset":0,"offset":1}\n', b'{"elapsed_seconds":1e999}\n'):
            self.chunks.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                replay.replay_capture(self.received, chunks_path=self.chunks)

    def test_elapsed_time_monotonicity_not_wall_clock_monotonicity(self):
        rows = chunks_for([OBSERVED_REPLY[:10], OBSERVED_REPLY[10:]])
        rows[1]["at"] = "2026-09-13T01:00:00Z"
        self.write_capture(OBSERVED_REPLY, rows)
        self.assertEqual(replay.replay_capture(self.received, chunks_path=self.chunks)["counts"], {"frame": 1})
        rows[1]["elapsed_seconds"] = 10 ** 400
        self.write_capture(OBSERVED_REPLY, rows)
        result = replay.replay_capture(self.received, chunks_path=self.chunks)
        self.assertEqual(result["events"][0]["chunk_timing"]["last_elapsed_seconds"], 10 ** 400)

    def test_input_chunk_event_and_payload_bounds(self):
        data = OBSERVED_REPLY * 2
        self.write_capture(data, chunks_for([OBSERVED_REPLY, OBSERVED_REPLY]))
        for options in (
            {"max_input_bytes": len(data) - 1}, {"max_input_bytes": True}, {"max_input_bytes": -1},
            {"max_chunks_bytes": -1}, {"max_events": 0}, {"max_events": True},
            {"max_events": 1}, {"max_chunks": 0}, {"max_payload_bytes": 65536},
            {"max_payload_bytes": True}, {"chunks_path": self.chunks, "max_chunks": 1},
            {"chunks_path": self.chunks, "max_chunks_bytes": 1},
            {"direction": "response"}, {"evidence": "verified"},
            {"expected_config_sequence": 0}, {"direction": "outgoing", "expected_config_sequence": 0},
            {"direction": "received", "expected_config_sequence": True},
            {"direction": "received", "expected_config_sequence": 65536},
            {"telemetry": 1}, {"telemetry": None},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                replay.replay_capture(self.received, **options)
        result = replay.replay_capture(self.received, max_payload_bytes=0)
        self.assertGreaterEqual(result["counts"]["error"], 1)
        self.assertEqual(self.received.read_bytes(), data)

    def test_symlinks_including_ancestors_directories_and_special_files_rejected(self):
        self.write_capture(b"")
        link = self.root / "link.bin"
        link.symlink_to(self.received)
        ancestor = self.root / "linked"
        ancestor.symlink_to(self.root, target_is_directory=True)
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        for path in (link, ancestor / "received.bin", self.root, fifo):
            with self.subTest(path=path), patch.object(os, "open") as opening, self.assertRaises(ValueError):
                replay.replay_capture(path)
            opening.assert_not_called()
        fake_device = SimpleNamespace(st_mode=stat.S_IFCHR, st_size=0)
        with patch.object(Path, "lstat", return_value=fake_device), patch.object(os, "open") as opening:
            with self.assertRaises(ValueError):
                replay.replay_capture(self.received)
            opening.assert_not_called()
        self.chunks.symlink_to(self.received)
        with self.assertRaises(ValueError):
            replay.replay_capture(self.received, chunks_path=self.chunks)

    def test_kernel_paths_rejected_without_access(self):
        for path in ("/dev/not-capture", "/sys/not-capture", "/proc/not-capture", "/tmp/../dev/not-capture"):
            with self.subTest(path=path), patch.object(Path, "lstat") as inspecting, patch.object(os, "open") as opening:
                with self.assertRaises(ValueError):
                    replay.replay_capture(path)
                inspecting.assert_not_called()
                opening.assert_not_called()

    def test_cli_json_success_error_and_direct_script(self):
        self.write_capture(OBSERVED_REPLY)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(replay.main([str(self.received), "--evidence", "synthetic", "--direction", "received",
                                          "--expect-config-sequence", "0"]), 0)
        self.assertEqual(json.loads(output.getvalue())["events"][0]["get_config_match"]["status"], "integrity_and_shape_match")
        for args in ([str(self.received), "--max-input-bytes", "1"], [str(self.root / "missing.bin")]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(replay.main(args), 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "input_error")
            self.assertFalse(json.loads(output.getvalue())["source_modified_by_replay"])
        for prefix in (
            [sys.executable, "-B", "-m", "tools.marvin_legacy_replay"],
            [sys.executable, "-B", str(Path(replay.__file__))],
        ):
            completed = subprocess.run(prefix + [str(self.received)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(completed.stdout)["direction"], "unknown")
            self.assertEqual(json.loads(completed.stdout)["counts"], {"frame": 1})
            self.assertEqual(completed.stderr, "")

    def test_imports_do_not_open_files_load_successor_telemetry_or_hardware(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
before = set(sys.modules)
with patch('os.open', side_effect=AssertionError('file open')), \\
     patch('builtins.open', side_effect=AssertionError('file open')), \\
     patch.object(Path, 'open', side_effect=AssertionError('path open')):
    from tools import marvin_legacy_protocol, marvin_legacy_stream, marvin_legacy_replay
    assert marvin_legacy_protocol.get_config_request().hex() == '53000004000000623545'
    assert marvin_legacy_stream.LegacyStreamDecoder().finish() == []
new = set(sys.modules) - before
assert not any(name.split('.')[0] in {'serial', 'usb', 'socket', 'requests', 'RPi', 'gpiozero'} for name in new)
assert not {'tools.marvin_probe', 'tools.marvin_session', 'tools.marvin_usbmon',
            'tools.marvin_replay', 'tools.marvin_telemetry'} & new
"""
        subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(replay.__file__).parent.parent,
                       capture_output=True, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
