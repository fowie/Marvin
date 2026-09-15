import ast
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import marvin_protocol as protocol
from tools import marvin_replay as replay
from tools import marvin_stream as stream


def frame(payload=b"", *, command=4, status=0x80, sequence=0):
    body = protocol.HEADER + struct.pack("<HBBH", sequence, command, status, len(payload)) + payload
    return body + struct.pack("<H", protocol.crc16(body)) + protocol.FOOTER


def chunks_for(parts):
    rows = []
    offset = 0
    for index, data in enumerate(parts):
        rows.append({
            "offset": offset, "size": len(data), "hex": data.hex(),
            "elapsed_seconds": (index + 1) * 0.1,
            "at": f"2026-09-10T23:00:{index % 60:02d}+00:00",
        })
        offset += len(data)
    return rows


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.raw_path = self.root / "received.bin"
        self.chunk_path = self.root / "chunks.jsonl"

    def write_capture(self, data, rows=None):
        self.raw_path.write_bytes(data)
        if rows is not None:
            self.chunk_path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_fragmented_config_and_heartbeat_with_chunk_timestamps(self):
        config = frame(struct.pack("<III", 45949, 0x10300, 123) + bytes(range(96)))
        heartbeat = frame(bytes(157), command=1, sequence=65535)
        data = config + heartbeat
        parts = [data[:1], data[1:7], data[7:63], data[63:155], data[155:]]
        rows = chunks_for(parts)
        self.write_capture(data, rows)
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        result = replay.replay_capture(self.raw_path, chunks_path=self.chunk_path, evidence="synthetic")
        self.assertEqual(result["evidence_kind"], "synthetic")
        self.assertEqual(result["status"], "decoded")
        self.assertEqual(result["counts"], {"frame": 2})
        first, second = result["events"]
        self.assertEqual((first["offset"], first["end_offset"]), (0, 120))
        self.assertEqual((second["offset"], second["end_offset"]), (120, 289))
        self.assertEqual((first["chunk_timing"]["first_line"], first["chunk_timing"]["last_line"]), (1, 4))
        self.assertEqual((second["chunk_timing"]["first_line"], second["chunk_timing"]["last_line"]), (4, 5))
        self.assertEqual(second["chunk_timing"]["last_at"], rows[-1]["at"])
        self.assertEqual(first["interpretation"]["telemetry"]["fields"]["fwVersion"], 45949)
        self.assertEqual(second["interpretation"]["telemetry"]["layout"], "HeartbeatDataDrive")
        self.assertEqual(result["source"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_empty_recorded_capture_is_not_a_reply(self):
        self.write_capture(b"", [])
        result = replay.replay_capture(self.raw_path, chunks_path=self.chunk_path, evidence="recorded")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["source"]["bytes"], 0)
        self.assertEqual(result["source"]["chunks"]["records"], 0)
        self.assertEqual(result["application_acknowledgment"], "not_established")
        self.assertIn("Caller-declared", result["provenance"])
        self.assertEqual(replay.replay_capture(self.raw_path)["evidence_kind"], "unspecified")

    def test_echo_and_outgoing_bytes_cannot_become_application_ack(self):
        self.write_capture(protocol.get_config_request() + protocol.get_unit_info_request())
        for direction in ("received", "outgoing", "unknown"):
            result = replay.replay_capture(self.raw_path, evidence="recorded", direction=direction)
            self.assertEqual(result["application_acknowledgment"], "not_established")
            for event in result["events"]:
                self.assertNotIn("telemetry", event["interpretation"])
                self.assertEqual(event["interpretation"]["application_acknowledgment"], "not_established")

    def test_noise_crc_failure_unknown_status_and_tail_keep_raw_input(self):
        damaged = bytearray(frame(b"damaged"))
        damaged[-4] ^= 1
        unknown = frame(b"unknown", command=250, status=0xFF)
        data = b"noise" + bytes(damaged) + unknown + b"\xef\xbe\x00"
        self.write_capture(data)
        result = replay.replay_capture(self.raw_path)
        self.assertEqual(result["status"], "decoded_with_diagnostics")
        self.assertEqual(result["counts"]["frame"], 1)
        self.assertTrue(any(event["kind"] == "error" for event in result["events"]))
        self.assertEqual(result["events"][-1]["kind"], "partial")
        self.assertEqual(bytes.fromhex("".join(event["raw_hex"] for event in result["events"])), data)
        validated = next(event for event in result["events"] if event["kind"] == "frame")
        self.assertEqual(validated["interpretation"]["response_code"], 127)
        self.assertNotIn("telemetry", validated["interpretation"])

    def test_inconsistent_chunk_offsets_sizes_hex_and_timestamps_are_rejected(self):
        data = frame(bytes(108))
        valid = chunks_for([data[:10], data[10:]])
        changes = (
            lambda rows: rows[0].update(offset=1),
            lambda rows: rows[1].update(offset=9),
            lambda rows: rows[1].update(offset=11),
            lambda rows: rows[0].update(offset=False),
            lambda rows: rows[0].update(size=0),
            lambda rows: rows[0].update(size=True),
            lambda rows: rows[1].update(size=1000),
            lambda rows: rows[0].update(hex="00" * 10),
            lambda rows: rows[0].update(hex="zz" * 10),
            lambda rows: rows[0].update(hex="ef be"),
            lambda rows: rows[1].update(elapsed_seconds=0.0),
            lambda rows: rows[0].update(elapsed_seconds=-1),
            lambda rows: rows[0].update(elapsed_seconds=True),
            lambda rows: rows[0].update(elapsed_seconds=float("nan")),
            lambda rows: rows[0].update(at="2026-09-10T23:00:00"),
            lambda rows: rows[0].update(at="not-a-date"),
            lambda rows: rows[0].pop("at"),
            lambda rows: rows.pop(),
        )
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                rows = copy.deepcopy(valid)
                change(rows)
                self.write_capture(data, rows)
                with self.assertRaises(ValueError):
                    replay.replay_capture(self.raw_path, chunks_path=self.chunk_path)
        for content in ("", "\n", "[]\n", "{broken}\n", "{}\n"):
            self.write_capture(data)
            self.chunk_path.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                replay.replay_capture(self.raw_path, chunks_path=self.chunk_path)

    def test_wall_clock_adjustment_does_not_invalidate_elapsed_order(self):
        data = frame(b"hello")
        rows = chunks_for([data[:5], data[5:]])
        rows[1]["at"] = "2026-09-09T01:00:00+00:00"
        self.write_capture(data, rows)
        result = replay.replay_capture(self.raw_path, chunks_path=self.chunk_path)
        self.assertEqual(result["events"][0]["chunk_timing"]["last_at"], rows[1]["at"])

    def test_duplicate_chunk_keys_are_rejected_without_modifying_sources(self):
        data = frame(b"hello")
        rows = chunks_for([data[:5], data[5:]])
        self.write_capture(data)
        for key, conflicting in (("offset", -1), ("size", 0), ("hex", "")):
            for earlier in (conflicting, rows[1][key]):
                content = (
                    json.dumps(rows[0]) + "\n"
                    + "{" + json.dumps(key) + ": " + json.dumps(earlier) + ", "
                    + json.dumps(rows[1])[1:] + "\n"
                )
                self.chunk_path.write_text(content)
                with self.subTest(key=key, earlier=earlier):
                    with self.assertRaisesRegex(ValueError, f"Chunk line 2: invalid JSON: Duplicate JSON key: {key}"):
                        replay.replay_capture(self.raw_path, chunks_path=self.chunk_path)
                    output = io.StringIO()
                    with redirect_stdout(output):
                        status = replay.main([str(self.raw_path), "--chunks", str(self.chunk_path)])
                    self.assertEqual(status, 2)
                    result = json.loads(output.getvalue())
                    self.assertEqual(result["status"], "input_error")
                    self.assertIn(f"Duplicate JSON key: {key}", result["error"])
                    self.assertFalse(result["source_modified_by_replay"])
                    self.assertEqual(self.raw_path.read_bytes(), data)
                    self.assertEqual(self.chunk_path.read_text(), content)

    def test_large_finite_integer_elapsed_is_not_cast_to_float(self):
        data = frame()
        rows = chunks_for([data])
        rows[0]["elapsed_seconds"] = 10 ** 400
        self.write_capture(data, rows)
        result = replay.replay_capture(self.raw_path, chunks_path=self.chunk_path)
        self.assertEqual(result["events"][0]["chunk_timing"]["first_elapsed_seconds"], 10 ** 400)

    def test_explicit_bounds_reject_instead_of_truncating(self):
        data = frame() * 2
        self.write_capture(data, chunks_for([data[:12], data[12:]]))
        for options in (
            {"max_input_bytes": len(data) - 1},
            {"chunks_path": self.chunk_path, "max_chunks_bytes": 1},
            {"chunks_path": self.chunk_path, "max_chunks": 1},
            {"max_events": 1}, {"max_input_bytes": -1}, {"max_input_bytes": True},
            {"max_events": 0}, {"max_payload_bytes": 65536},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                replay.replay_capture(self.raw_path, **options)
        self.assertEqual(self.raw_path.read_bytes(), data)

    def test_regular_input_guard_rejects_links_directories_and_special_files_before_open(self):
        self.write_capture(b"")
        link = self.root / "link.bin"
        link.symlink_to(self.raw_path)
        for path in (link, self.root):
            with self.assertRaises(ValueError):
                stream.read_regular_file(path, max_bytes=1024)
        fake_device = SimpleNamespace(st_mode=stat.S_IFCHR, st_size=0)
        with patch.object(Path, "lstat", return_value=fake_device), patch.object(stream.os, "open") as opening:
            with self.assertRaises(ValueError):
                stream.read_regular_file(self.raw_path, max_bytes=1024)
            opening.assert_not_called()

    def test_device_and_kernel_paths_are_rejected_without_open_or_stat(self):
        for path in ("/dev/not-a-capture", "/sys/not-a-capture", "/proc/not-a-capture",
                     "/tmp/../dev/not-a-capture"):
            with self.subTest(path=path), patch.object(Path, "lstat") as inspecting, \
                    patch.object(stream.os, "open") as opening:
                with self.assertRaises(ValueError):
                    stream.read_regular_file(path, max_bytes=1024)
                opening.assert_not_called()
                inspecting.assert_not_called()

    def test_cli_json_success_error_no_telemetry_and_direct_script(self):
        self.write_capture(frame(bytes(108)))
        output = io.StringIO()
        with redirect_stdout(output):
            status = replay.main([str(self.raw_path), "--evidence", "synthetic", "--no-telemetry",
                                  "--catalog", str(self.root / "absent.json")])
        self.assertEqual(status, 0)
        self.assertNotIn("interpretation", json.loads(output.getvalue())["events"][0])
        output = io.StringIO()
        with redirect_stdout(output):
            status = replay.main([str(self.raw_path), "--max-input-bytes", "1"])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "input_error")
        completed = subprocess.run(
            [sys.executable, "-B", str(Path(replay.__file__)), str(self.raw_path), "--evidence", "synthetic"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(completed.stdout)["events"][0]["kind"], "frame")
        self.assertEqual(completed.stderr, "")

    def test_module_imports_are_offline_and_do_not_load_schema(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
before = set(sys.modules)
with patch('os.open', side_effect=AssertionError('unexpected open')), \\
     patch.object(Path, 'open', side_effect=AssertionError('unexpected Path.open')):
    from tools import marvin_stream, marvin_telemetry, marvin_replay
new = set(sys.modules) - before
assert not any(name.split('.')[0] in {'serial', 'usb', 'socket', 'requests'} for name in new)
assert not {'tools.marvin_probe', 'tools.marvin_session', 'tools.marvin_usbmon'} & new
"""
        subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(replay.__file__).parent.parent,
                       capture_output=True, text=True, check=True)
        for name in ("marvin_stream.py", "marvin_telemetry.py", "marvin_replay.py"):
            tree = ast.parse((Path(replay.__file__).parent / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                    if node.module == "tools":
                        imports += [alias.name for alias in node.names]
                else:
                    continue
                self.assertFalse(any(value.split(".")[0] in
                                     {"serial", "usb", "socket", "requests", "marvin_probe", "marvin_session"}
                                     for value in imports))


if __name__ == "__main__":
    unittest.main()
