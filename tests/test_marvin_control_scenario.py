from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_control_scenario as scenario


ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "data" / "synthetic-control-scenario.json"


class ControlScenarioTests(unittest.TestCase):
    def example(self):
        return json.loads(EXAMPLE.read_bytes())

    def cli(self, path, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "tools.marvin_control_scenario", str(path), *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )

    def test_real_cli_deterministic_output_new_destination_and_immutable_input(self):
        before = EXAMPLE.read_bytes()
        first, second = self.cli(EXAMPLE), self.cli(EXAMPLE)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual(report, {
            **scenario.run_scenario(self.example()),
            "input": {
                "bytes": len(before), "sha256": hashlib.sha256(before).hexdigest(),
                "meaning": "Input integrity identifier only, not authentication.",
            },
        })
        self.assertEqual(report["state"]["mode"], "disarmed")
        self.assertEqual(report["physical_stop"], "not_established")
        self.assertEqual(report["physical_authorization"], "not_granted")
        self.assertIn("BLOCKED", report["manual_11"])
        self.assertFalse(report["resume_permitted"])
        self.assertEqual(report["records"][4]["event"]["write"]["raw"]["raw_hex"],
                         b"SYNTHETIC".hex())
        self.assertEqual(report["records"][5]["before"], report["records"][4]["after"])
        self.assertEqual(report["records"][5]["before"]["pending"]["status"], "submitted")
        self.assertIsNone(report["records"][2]["before"]["armed_at"])
        self.assertEqual(report["records"][2]["after"]["armed_at"], 0)
        self.assertEqual(report["records"][6]["before"]["mode"], "armed")
        self.assertEqual(report["records"][6]["before"]["armed_at"], 0)
        self.assertIsNone(report["records"][6]["after"]["armed_at"])
        self.assertIsNotNone(report["records"][6]["before"]["token"])
        self.assertIsNone(report["records"][6]["after"]["token"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            result = self.cli(EXAMPLE, "--output", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(output.read_text(), first.stdout)
            refused = self.cli(EXAMPLE, "--output", output)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(output.read_text(), first.stdout)
        self.assertEqual(EXAMPLE.read_bytes(), before)

    def test_real_cli_fault_crash_partial_write_and_denied_authorization(self):
        changes = []
        doc = self.example()
        doc["events"][5]["reply"]["request_status"] = "failed"
        changes.append((doc, "fault", "reply_not_delivered"))
        doc = self.example()
        doc["events"] = doc["events"][:5] + [{"kind": "host_crash", "at": 0.7}]
        changes.append((doc, "crashed", "host_crash"))
        doc = self.example()
        doc["events"][4]["write"].update(accepted_bytes=4, uncertain_bytes=5)
        changes.append((doc, "fault", "partial_write"))
        doc = self.example()
        doc["reviews"][0]["reviewed"] = False
        changes.append((doc, "fault", "unreviewed_evidence"))
        doc = self.example()
        doc["events"] = doc["events"][:3] + [
            {"kind": "tick", "at": 2},
            {"kind": "reset", "at": 2},
            {"kind": "connect", "at": 2, "scope": "expected"},
            {"kind": "arm", "at": 2, "scope": "expected", "token": "first"},
        ]
        changes.append((doc, "fault", "ownership"))
        doc = self.example()
        doc["policy"]["max_events"] = 5
        doc["events"] = doc["events"][:3] + [
            {"kind": "host_exit", "at": 0.1}, {"kind": "restart", "at": 0.2},
        ]
        changes.append((doc, "exited", None))
        for restart in (False, True):
            for pending in (False, True):
                doc = self.example()
                doc["events"] = doc["events"][:5 if pending else 3] + [
                    {"kind": "host_exit", "at": 0.7},
                ]
                if restart:
                    doc["events"].append({"kind": "restart", "at": 0.8})
                changes.append((doc, "new" if restart else "exited",
                                "uncertain_delivery" if pending and not restart else None))
        for initial, mode, code in (
            ("transport_lost", "fault", "transport_lost"),
            ("host_crash", "crashed", "host_crash"),
            ("host_exit", "exited", None),
        ):
            doc = self.example()
            doc["events"] = doc["events"][:3] + [
                {"kind": initial, "at": 0.1},
                {"kind": "host_crash", "at": 0.2},
                {"kind": "host_exit", "at": 0.3},
            ]
            changes.append((doc, mode, code))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            for doc, mode, code in changes:
                with self.subTest(code=code):
                    original = json.dumps(doc).encode()
                    path.write_bytes(original)
                    result = self.cli(path)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertTrue(report["complete"])
                    self.assertEqual(report["state"]["mode"], mode)
                    self.assertEqual(report["state"]["fault"], code)
                    self.assertEqual(report["physical_stop"], "not_established")
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(len(report["records"]), len(doc["events"]))
                    if code == "host_crash":
                        self.assertEqual(report["state"]["cleanup"], "cannot_execute_after_host_crash")

    def test_real_cli_strict_schema_numeric_and_bounded_file_failures(self):
        malformed = [
            b'{"schema_version":1,"schema_version":1}',
            b'{"value":NaN}', b'{"value":Infinity}', b"\xff",
            b"[" * 2000 + b"]" * 2000,
        ]
        base = self.example()
        for transform in (
            lambda d: d.update(evidence_kind="recorded"),
            lambda d: d.update(schema_version=True),
            lambda d: d["events"][0].update(opcode=17),
            lambda d: d["events"][2].update(token="unknown"),
            lambda d: d["events"].append({**d["events"][1]}),
            lambda d: d["events"][4]["write"].update(raw_hex="xx"),
            lambda d: d["events"][4]["write"].update(raw_hex="aa " * 3),
            lambda d: d["events"][4]["write"].update(raw_hex="aa" * 4097),
            lambda d: d["policy"].update(deadman_timeout=True),
            lambda d: d["policy"].update(reply_timeout=1e300),
            lambda d: d["events"].extend([{"kind": "tick", "at": 0}] * 33),
            lambda d: d["reviews"].append(deepcopy(d["reviews"][0])),
        ):
            doc = deepcopy(base)
            transform(doc)
            malformed.append(json.dumps(doc).encode())
        malformed.append(b" " * (scenario.MAX_INPUT_BYTES + 1))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            output = Path(directory) / "output.json"
            for raw in malformed:
                with self.subTest(prefix=raw[:80]):
                    path.write_bytes(raw)
                    result = self.cli(path, "--output", output)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(json.loads(result.stderr)["complete"])
                    self.assertFalse(output.exists())
                    self.assertEqual(path.read_bytes(), raw)
            valid = self.example()
            valid["events"][3]["intent"]["duration"] = True
            path.write_text(json.dumps(valid))
            result = self.cli(path)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["state"]["fault"], "invalid_number")

    def test_real_cli_rejects_special_paths_aliases_and_existing_input_output(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            path = parent / "input.json"
            path.write_bytes(EXAMPLE.read_bytes())
            link = parent / "link.json"
            link.symlink_to(path)
            directory_link = parent / "alias"
            directory_link.symlink_to(parent, target_is_directory=True)
            fifo = parent / "fifo"
            os.mkfifo(fifo)
            for source in (link, directory_link / "input.json", fifo, parent):
                with self.subTest(source=source):
                    result = self.cli(source)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertFalse(json.loads(result.stderr)["complete"])
            before = path.read_bytes()
            for output in (path, link, directory_link / "output.json", parent / "missing" / "out"):
                result = self.cli(path, "--output", output)
                self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((parent / "output.json").exists())

    def test_every_review_is_schema_checked_even_when_unused(self):
        base = self.example()
        invalid = []
        for field, values in (
            ("kind", ("unknown", True)),
            ("reference", ("",)),
            ("reviewer", (" ",)),
            ("policy_reference", ("",)),
            ("measured", (1, "true", None)),
            ("reviewed", (0, "false", None)),
            ("source", ("recorded", None)),
            ("valid_from", (True, -1, float("nan"), 20, 21)),
            ("expires_at", (False, float("inf"), 1e13, 0, -1)),
            ("scope", (None, {}, {**base["scope"], "extra": "unknown"})),
        ):
            for value in values:
                invalid.append({**base["reviews"][0], field: value})
        for field in base["scope"]:
            for value in ("", 1, None):
                invalid.append({**base["reviews"][0], "scope": {**base["scope"], field: value}})
        with tempfile.TemporaryDirectory() as directory:
            path, output = Path(directory) / "scenario.json", Path(directory) / "report.json"
            for used in (False, True):
                for index, review in enumerate(invalid):
                    with self.subTest(used=used, case=index):
                        doc = self.example()
                        doc["reviews"][0] = review
                        if not used:
                            doc["events"] = [doc["events"][0], {"kind": "tick", "at": 0.1}]
                        with patch.object(scenario.model, "Model") as constructing:
                            with self.assertRaises(ValueError):
                                scenario.run_scenario(doc)
                            constructing.assert_not_called()
                        path.write_text(json.dumps(doc))
                        result = self.cli(path, "--output", output)
                        self.assertEqual(result.returncode, 2, result.stdout)
                        self.assertEqual(result.stdout, "")
                        error = json.loads(result.stderr)
                        self.assertFalse(error["complete"])
                        self.assertEqual(error["result"], "input_or_output_error")
                        self.assertNotIn("records", error)
                        self.assertFalse(output.exists())

            for changes, code in (
                ({"measured": False, "reviewed": False}, "unreviewed_evidence"),
                ({"valid_from": 1, "expires_at": 2}, "stale_evidence"),
                ({"valid_from": 0, "expires_at": 0.001}, "stale_evidence"),
                ({"policy_reference": "SYNTHETIC-other"}, "policy_mismatch"),
                ({"scope": {**base["scope"], "identity": "SYNTHETIC-other"}}, "identity_changed"),
                ({"scope": {**base["scope"], "profile": "SYNTHETIC-other"}}, "profile_mismatch"),
                ({"scope": {**base["scope"], "velocity_unit": "SYNTHETIC-other"}}, "unknown_units"),
            ):
                for used in (False, True):
                    with self.subTest(used=used, changes=changes):
                        doc = self.example()
                        doc["reviews"][0].update(changes)
                        doc["events"] = doc["events"][:2 if used else 1]
                        for event in doc["events"]:
                            event["at"] = 0.1
                        doc["events"].append({"kind": "tick", "at": 0.1})
                        scenario.parse_scenario(doc)
                        path.write_text(json.dumps(doc))
                        result = self.cli(path)
                        self.assertEqual(result.returncode, int(used), result.stderr)
                        report = json.loads(result.stdout)
                        self.assertTrue(report["complete"])
                        self.assertEqual(report["state"]["fault"], code if used else None)
                        self.assertEqual(report["physical_stop"], "not_established")
                        self.assertEqual(report["physical_authorization"], "not_granted")


if __name__ == "__main__":
    unittest.main()
