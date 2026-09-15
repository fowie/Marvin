"""Authored synthetic evidence only; actual CLI runs never access hardware."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_commissioning as commissioning


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "commissioning"
AS_OF = "2026-01-01T12:00:00Z"


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        self.package = commissioning.load_document(FIXTURES / "synthetic-evidence.json")
        self.configuration = commissioning.load_document(FIXTURES / "synthetic-configuration.json")

    def evaluate(self, package=None, *, as_of=AS_OF, configuration=None):
        result = commissioning.validate_evidence(
            self.package if package is None else package, as_of=as_of,
            expected_configuration=self.configuration if configuration is None else configuration)
        for key in ("physical_safety_established", "physical_signoff_established", "authorization_granted"):
            self.assertIs(result[key], False)
        self.assertIs(result["offline_only"], True)
        return result

    def test_complete_template_provenance_and_no_mutation_or_io(self):
        original = deepcopy(self.package)
        with patch("builtins.open", side_effect=AssertionError("I/O")), \
                patch("os.open", side_effect=AssertionError("I/O")):
            result = self.evaluate()
            template = commissioning.blank_template()
            incomplete = self.evaluate(template, configuration=template["configuration"])
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["structurally_valid"] and result["content_complete"] and result["evidence_complete"])
        self.assertEqual(result["freshness"], "current")
        self.assertEqual(result["evaluation_time_source"], "supplied_as_of")
        self.assertEqual(result["package"], original)
        result["package"]["entries"][0]["observation"] = "changed output"
        self.assertEqual(self.package, original)
        self.assertEqual(incomplete["status"], "incomplete")
        self.assertTrue(incomplete["structurally_valid"])
        self.assertEqual(incomplete["freshness"], "not_evaluated")
        self.assertEqual(len(incomplete["checks"]), len(commissioning.CHECKLIST))
        template["entries"][0]["configuration"]["revision"] = "changed"
        self.assertIsNone(commissioning.blank_template()["entries"][0]["configuration"]["revision"])
        self.assertIsNone(template["configuration"]["identity"]["revision"])
        schema = commissioning.evidence_schema()
        self.assertEqual(json.loads(json.dumps(schema, allow_nan=False)), schema)
        ids = {row["properties"]["id"]["const"] for row in schema["properties"]["entries"]["items"]["oneOf"]}
        self.assertEqual(ids, set(commissioning.CHECKLIST))
        self.assertEqual(self.evaluate(as_of=None)["evaluation_time_source"], "system_utc")

    def test_every_prerequisite_unknown_failure_conflict_and_attribution_blocks(self):
        for index, entry in enumerate(self.package["entries"]):
            changes = [
                (key, None) for key in
                ("operator", "reviewer", "observed_at", "reviewed_at", "method", "observation")
            ] + [
                ("evidence_references", []), ("confidence", "unknown"),
                ("blockers", ["unresolved synthetic blocker"]),
            ] + [
                ("state", state) for state in commissioning.STATES if state != "reviewed"
            ] + [
                ("outcome", outcome) for outcome in commissioning.OUTCOMES if outcome != "satisfied"
            ]
            for field, value in changes:
                package = deepcopy(self.package)
                package["entries"][index][field] = value
                with self.subTest(check=entry["id"], field=field, value=value):
                    result = self.evaluate(package)
                    self.assertEqual(result["status"], "incomplete")
                    self.assertFalse(result["evidence_complete"])
                    self.assertTrue(result["issues"])
            for assertion in entry["assertions"]:
                for value in (None, False):
                    package = deepcopy(self.package)
                    package["entries"][index]["assertions"][assertion] = value
                    with self.subTest(check=entry["id"], assertion=assertion, value=value):
                        result = self.evaluate(package)
                        self.assertEqual(result["status"], "incomplete")
                        self.assertIn("unconfirmed_assertion", {issue["code"] for issue in result["issues"]})
            package = deepcopy(self.package)
            del package["entries"][index]
            self.assertEqual(self.evaluate(package)["checks"][index]["status"], "missing")
        for field, value in (("package_id", None), ("blockers", ["conflict"]), ("validity_policy", None)):
            package = deepcopy(self.package)
            package[field] = value
            self.assertEqual(self.evaluate(package)["status"], "incomplete")

    def test_configuration_scope_mismatches_and_missing_map(self):
        for field in commissioning.IDENTITY_FIELDS:
            for scope in ("expected", "package", "policy", "entry"):
                package, expected = deepcopy(self.package), deepcopy(self.configuration)
                target = {
                    "expected": expected["identity"],
                    "package": package["configuration"]["identity"],
                    "policy": package["validity_policy"]["configuration"],
                    "entry": package["entries"][0]["configuration"],
                }[scope]
                target[field] = "different"
                with self.subTest(field=field, scope=scope):
                    result = self.evaluate(package, configuration=expected)
                    self.assertEqual(result["status"], "incomplete")
                    self.assertIn("configuration_mismatch", {issue["code"] for issue in result["issues"]})
        for value in ([], [{"channel": "different", "connector": "different", "function": "different"}]):
            expected = deepcopy(self.configuration)
            expected["wiring_map"] = value
            self.assertEqual(self.evaluate(configuration=expected)["status"], "incomplete")
        self.package["configuration"]["wiring_map"] = []
        self.configuration["wiring_map"] = []
        self.assertEqual(self.evaluate()["status"], "incomplete")

    def test_reviewed_policy_and_exact_freshness_boundaries(self):
        self.assertEqual(self.evaluate()["status"], "complete")  # Exactly 7200 seconds old.
        result = self.evaluate(as_of="2026-01-01T12:00:00.000001Z")
        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["content_complete"])
        self.assertFalse(result["evidence_complete"])
        self.assertEqual(self.evaluate(as_of="2026-01-01T13:00:00+01:00")["status"], "complete")
        package = deepcopy(self.package)
        package["validity_policy"]["max_age_seconds"] = dict.fromkeys(commissioning.CHECKLIST, 100000)
        result = self.evaluate(package, as_of="2026-01-02T09:00:00Z")
        self.assertEqual(result["status"], "stale")
        self.assertIn("stale_policy", {issue["code"] for issue in result["issues"]})
        package["blockers"] = ["Still blocked even when stale"]
        result = self.evaluate(package, as_of="2026-01-02T09:00:00Z")
        self.assertEqual((result["status"], result["freshness"]), ("incomplete", "stale"))
        for field in ("policy_id", "reviewer", "reviewed_at", "expires_at", "evidence_reference", "rationale"):
            package = deepcopy(self.package)
            package["validity_policy"][field] = None
            with self.subTest(policy=field):
                result = self.evaluate(package)
                self.assertEqual((result["status"], result["freshness"]), ("incomplete", "not_evaluated"))
        for field, value in (
            ("reviewed_at", "2026-01-01T12:00:01Z"),  # Future policy.
            ("reviewed_at", "2026-01-01T11:00:01Z"),  # Entry reviewed under old policy.
            ("expires_at", "2026-01-01T09:00:00Z"),  # Empty validity interval.
        ):
            package = deepcopy(self.package)
            package["validity_policy"][field] = value
            self.assertEqual(self.evaluate(package)["status"], "incomplete")
        for field, value in (("observed_at", "2026-01-01T12:00:01Z"),
                             ("reviewed_at", "2026-01-01T12:00:01Z"),
                             ("reviewed_at", "2026-01-01T09:59:59Z")):
            package = deepcopy(self.package)
            package["entries"][0][field] = value
            self.assertEqual(self.evaluate(package)["status"], "incomplete")
        key = next(iter(commissioning.CHECKLIST))
        for age, status in ((None, "incomplete"), (0.5, "stale"), (7200.0, "complete"),
                            (commissioning.MAX_AGE_SECONDS, "complete")):
            package = deepcopy(self.package)
            package["validity_policy"]["max_age_seconds"][key] = age
            self.assertEqual(self.evaluate(package)["status"], status)

    def test_malformed_types_duplicates_unknown_fields_timestamps_and_bounds(self):
        mutations = [
            lambda p: p.update(schema_version=True),
            lambda p: p.update(schema_version=1.0),
            lambda p: p.update(schema_version=2),
            lambda p: p.update(unexpected="field"),
            lambda p: p.pop("entries"),
            lambda p: p.update(entries={}),
            lambda p: p["entries"].__setitem__(1, deepcopy(p["entries"][0])),
            lambda p: p["entries"][0].update(id="unknown-check"),
            lambda p: p["entries"][0].update(unexpected=True),
            lambda p: p["entries"][0]["configuration"].update(unexpected=True),
            lambda p: p["entries"][0]["assertions"].update(unexpected=True),
            lambda p: p["entries"][0].update(evidence_references=["same", "same"]),
            lambda p: p["entries"][0].update(evidence_references=[None]),
            lambda p: p["entries"][0].update(operator=" "),
            lambda p: p["entries"][0].update(operator="x" * (commissioning.MAX_TEXT + 1)),
            lambda p: p["entries"][0].update(blockers=["x"] * (commissioning.MAX_ITEMS + 1)),
            lambda p: p["configuration"]["wiring_map"].append(deepcopy(p["configuration"]["wiring_map"][0])),
            lambda p: p["validity_policy"].update(unexpected=True),
            lambda p: p.update(package_id="\ud800"),
        ]
        key = next(iter(commissioning.CHECKLIST))
        for age in (True, False, 0, -1, "7200", float("nan"), float("inf"), float("-inf"),
                    commissioning.MAX_AGE_SECONDS + 1, 10**400):
            mutations.append(lambda p, age=age: p["validity_policy"]["max_age_seconds"].update({key: age}))
        for value in (1, 0, "true", [], {}):
            mutations.append(lambda p, value=value: p["entries"][0]["assertions"].update(
                unique_device_identified=value))
        invalid_times = (
            "2026-01-01T12:00:00", "2026-02-30T12:00:00Z", "2026-01-01",
            "2026-01-01T12:00:00-00:00", "2026-01-01T12:00:00.1234567Z",
            "0001-01-01T00:00:00+01:00", "2026-01-01T12:00:00+24:00",
            "2026-01-01T12:00:00+01:60", "2026-01-01T12:00:00-00:99", True, 1,
        )
        for value in invalid_times:
            mutations.append(lambda p, value=value: p["entries"][0].update(observed_at=value))
            self.assertEqual(self.evaluate(as_of=value)["status"], "malformed")
        for index, mutate in enumerate(mutations):
            package = deepcopy(self.package)
            mutate(package)
            with self.subTest(mutation=index):
                result = self.evaluate(package)
                self.assertEqual(result["status"], "malformed")
                self.assertFalse(result["structurally_valid"])
                self.assertNotIn("package", result)
        for value in (None, [], "string", True):
            result = commissioning.validate_evidence(value, expected_configuration=self.configuration, as_of=AS_OF)
            self.assertEqual(result["status"], "malformed")
        cyclic = {}
        cyclic["cycle"] = cyclic
        self.assertEqual(self.evaluate(cyclic)["status"], "malformed")
        maximum = deepcopy(self.package)
        maximum["entries"][0]["operator"] = "x" * commissioning.MAX_TEXT
        self.assertEqual(self.evaluate(maximum)["status"], "complete")
        maximum["blockers"] = [str(index) + "x" * 4000 for index in range(64)]
        self.assertEqual(self.evaluate(maximum)["status"], "malformed")  # Aggregate byte limit.

    def test_safe_reader_json_boundary_and_no_unsafe_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            regular = root / "evidence.json"
            original = b'{"schema_version":1}'
            regular.write_bytes(original)
            self.assertEqual(commissioning.load_document(regular), {"schema_version": 1})
            for raw in (b'{"a":1,"a":1}', b'{"nested":{"a":1,"a":2}}',
                        b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
                        b"\xff", b"{}", b"[", b'{"x":"\\ud800"}'):
                regular.write_bytes(raw)
                if raw == b"{}":
                    self.assertEqual(commissioning.load_document(regular), {})
                else:
                    with self.subTest(raw=raw), self.assertRaises((ValueError, UnicodeError)):
                        commissioning.load_document(regular)
                self.assertEqual(regular.read_bytes(), raw)
            regular.write_bytes(original)
            leaf = root / "leaf"
            leaf.symlink_to(regular)
            parent = root / "parent"
            parent.symlink_to(root, target_is_directory=True)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            oversized = root / "oversized"
            with oversized.open("wb") as stream:
                stream.truncate(commissioning.MAX_INPUT_BYTES + 1)
            for path in (leaf, parent / "evidence.json", parent / ".." / "evidence.json",
                         fifo, root, oversized, "/dev/commissioning-never-open",
                         "/proc/commissioning-never-open", "/sys/commissioning-never-open"):
                with self.subTest(path=path), patch("os.open", side_effect=AssertionError("unsafe open")), \
                        self.assertRaises((ValueError, OSError)):
                    commissioning.load_document(path)
            self.assertEqual(regular.read_bytes(), original)
            regular.write_bytes(b" " * (commissioning.MAX_INPUT_BYTES - 2) + b"{}")
            self.assertEqual(commissioning.load_document(regular), {})

    def test_real_stdout_cli_statuses_schema_template_and_immutable_inputs(self):
        def cli(*args):
            process = subprocess.run(
                [sys.executable, "-B", "-m", "tools.marvin_commissioning", *args],
                cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(process.stderr, "")
            return process.returncode, json.loads(process.stdout)

        for command, expected in (("template", commissioning.blank_template()),
                                  ("schema", commissioning.evidence_schema())):
            code, result = cli(command)
            self.assertEqual((code, result), (0, expected))
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "package.json"
            config = FIXTURES / "synthetic-configuration.json"
            cases = [(deepcopy(self.package), AS_OF, 0, "complete"),
                     (commissioning.blank_template(), AS_OF, 1, "incomplete"),
                     (deepcopy(self.package), "2026-01-01T12:00:00.000001Z", 3, "stale"),
                     ({}, AS_OF, 2, "malformed")]
            mismatch = deepcopy(self.package)
            mismatch["entries"][0]["configuration"]["physical_port"] = "different"
            cases.append((mismatch, AS_OF, 1, "incomplete"))
            for package, clock, expected_code, status in cases:
                path.write_text(json.dumps(package), encoding="utf-8")
                before = path.read_bytes()
                code, result = cli("validate", str(path), "--configuration", str(config), "--as-of", clock)
                self.assertEqual((code, result["status"]), (expected_code, status))
                self.assertFalse(result["authorization_granted"] or result["physical_safety_established"])
                self.assertEqual(result["evaluation_time_source"], "supplied_as_of")
                self.assertEqual(path.read_bytes(), before)
            for contents in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}'):
                path.write_text(contents, encoding="utf-8")
                code, result = cli("validate", str(path), "--configuration", str(config), "--as-of", AS_OF)
                self.assertEqual((code, result["status"]), (2, "malformed"))
            path.write_text(json.dumps(self.package), encoding="utf-8")
            code, result = cli("validate", str(path), "--configuration", str(config), "--as-of", "naive")
            self.assertEqual((code, result["status"]), (2, "malformed"))
            link = Path(directory) / "link"
            link.symlink_to(path)
            for bad_evidence, bad_config in ((link, config), (path, link), (path, path.with_name("missing"))):
                code, result = cli("validate", str(bad_evidence), "--configuration", str(bad_config))
                self.assertEqual((code, result["status"]), (2, "malformed"))

    def test_fresh_import_has_no_transport_network_or_hardware_side_effects(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
with patch('os.open', side_effect=AssertionError('open')), \\
     patch.object(Path, 'lstat', side_effect=AssertionError('stat')), \\
     patch('socket.socket', side_effect=AssertionError('network')):
    from tools import marvin_commissioning
    marvin_commissioning.blank_template()
    marvin_commissioning.evidence_schema()
assert not {'serial', 'tools.marvin_probe', 'tools.marvin_session',
            'tools.marvin_legacy_client', 'tools.marvin_legacy_probe'} & set(sys.modules)
"""
        subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT,
                       capture_output=True, text=True, timeout=15, check=True)


if __name__ == "__main__":
    unittest.main()
