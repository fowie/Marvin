from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_probe as legacy
from tools import marvin_campaign, marvin_probe, marvin_session
from tests.test_marvin_legacy_protocol import frame


BASELINE = {
    "usb": {
        "usb_path": "/fake/sys/1-1.1.3.3", "physical_port": "1-1.1.3.3",
        "idVendor": "045e", "idProduct": "4444", "descriptors_bytes": 71,
        "descriptors_sha256": marvin_campaign.DESCRIPTOR_HASH,
        "busnum": 1, "devnum": 24, "sysfs_device": 26, "sysfs_inode": 72748,
        "extra_identity_field": "must remain pinned",
    },
    "tty": "/dev/test-marvin", "tty_rdev": 123, "udev_path": "/fake/tty",
    "kernel": "fixture", "python": "fixture", "pyserial": "fixture",
}


class LegacyProbeDryRunTests(unittest.TestCase):
    def test_prepare_exact_four_queries_and_no_mutable_shared_review(self):
        for name, command in (
            ("get-config", 4), ("get-unit-info", 0x1B), ("get-power-state", 0x0E), ("read-raw-data", 0),
        ):
            for sequence in (0, 6, 65535):
                review = legacy.prepare(name, sequence)
                self.assertEqual(bytes.fromhex(review["request_hex"]), frame(command=command, sequence=sequence, status=0))
                self.assertEqual(review["maximum_application_bytes"], 10)
                self.assertEqual(review["settings"], {
                    "baudrate": 57600, "bytesize": 8, "parity": "N", "stopbits": 1,
                    "dtr": False, "rts": False, "flow_control": "none",
                })
                self.assertEqual((review["write_offset_seconds"], review["serial_seconds"],
                                  review["usb_nominal_seconds"], review["usb_maximum_seconds"]), (5, 20, 25, 55))
                review["settings"]["baudrate"] = 9600
                self.assertEqual(legacy.prepare(name, sequence)["settings"]["baudrate"], 57600)
        for query in ("0", "reset", "get-sensor-info", "set-power-state", 0, None):
            with self.assertRaises(ValueError):
                legacy.prepare(query)
        for sequence in (True, -1, 65536, 1.0):
            with self.assertRaises(ValueError):
                legacy.prepare("read-raw-data", sequence)

    def test_dry_run_has_no_file_device_or_runtime_module_access(self):
        script = """
import builtins
import io
import json
import sys
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
original_import = builtins.__import__
blocked = {'marvin_campaign','marvin_probe','marvin_session','marvin_usbmon','serial','usb','socket'}
def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split('.')[-1] in blocked or set(fromlist or ()) & blocked:
        raise AssertionError('hardware runtime import: ' + name)
    return original_import(name, globals, locals, fromlist, level)
with patch('builtins.__import__', side_effect=checked_import), \\
     patch('builtins.open', side_effect=AssertionError('file open')), \\
     patch('os.open', side_effect=AssertionError('os open')), \\
     patch('os.geteuid', side_effect=AssertionError('uid probe')), \\
     patch.object(Path, 'open', side_effect=AssertionError('path open')), \\
     patch.object(Path, 'lstat', side_effect=AssertionError('path stat')), \\
     patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir')):
    from tools import marvin_legacy_probe as p
    out=io.StringIO()
    with redirect_stdout(out):
        assert p.main(['read-raw-data','--sequence','6','--output','/not-created/capture']) == 0
    result=json.loads(out.getvalue())
    assert result['status']=='dry_run'
    assert result['command']==0
assert not {'tools.marvin_probe','tools.marvin_session','tools.marvin_usbmon','tools.marvin_campaign'} & set(sys.modules)
"""
        subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(legacy.__file__).parent.parent,
                       capture_output=True, text=True, check=True)

    def test_cli_module_and_script_dry_run_and_fixed_options(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "never-created"
            for prefix in (
                [sys.executable, "-B", "-m", "tools.marvin_legacy_probe"],
                [sys.executable, "-B", str(Path(legacy.__file__))],
            ):
                result = subprocess.run(prefix + ["read-raw-data", "--sequence", "6", "--output", str(output)],
                                        capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout)["status"], "dry_run")
                self.assertEqual(result.stderr, "")
                self.assertFalse(output.exists())
            for options in (
                ["reset"], ["27"], ["read-raw-data", "--baudrate", "9600"],
                ["read-raw-data", "--payload", "00"], ["read-raw-data", "--port", "/dev/fake"],
                ["read-raw-data", "--seconds", "1"], ["read-raw-data", "--dtr"],
            ):
                result = subprocess.run([sys.executable, "-B", "-m", "tools.marvin_legacy_probe", *options],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")


class LegacyProbeRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "capture-attempt"
        self.baseline = deepcopy(BASELINE)
        self.session = SimpleNamespace(
            preflight=Mock(return_value=self.baseline),
            check_identity=Mock(),
            run_session=Mock(side_effect=self.capture),
            write_json=marvin_session.write_json,
            evidence_manifest=Mock(side_effect=marvin_session.evidence_manifest),
        )
        self.probe = SimpleNamespace(
            DEFAULT_PORT="/dev/test-by-id", ScheduledWrite=marvin_probe.ScheduledWrite,
            utc_now=lambda: "2026-09-15T02:00:00+00:00",
        )
        self.campaign = SimpleNamespace(
            DESCRIPTOR_HASH=marvin_campaign.DESCRIPTOR_HASH,
            assess_segment=Mock(return_value={"outcome": "received_data_stop", "serial_received_bytes": 144}),
            partial_segment=Mock(side_effect=marvin_campaign.partial_segment),
        )
        self.runtime = patch.object(legacy, "_load_runtime", return_value=(self.campaign, self.probe, self.session)).start()
        patch.object(legacy.os, "geteuid", return_value=1000).start()
        self.serial_open = patch.object(marvin_probe.serial, "Serial", side_effect=AssertionError("REAL serial access forbidden")).start()
        self.process = patch.object(subprocess, "Popen", side_effect=AssertionError("REAL subprocess/sudo access forbidden")).start()
        self.addCleanup(patch.stopall)

    def capture(self, port, output, **options):
        options["ready_callback"]({"mock_ready": True})
        (output / "serial").mkdir(parents=True)
        (output / "serial/received.bin").write_bytes(b"fixture-response")
        serial = {
            "status": "completed", "transmit_status": "written", "application_bytes_written": 10,
            "scheduled_writes_completed": 1, "bytes_received": len(b"fixture-response"),
        }
        marvin_session.write_json(output / "serial/metadata.json", serial)
        return {"status": "completed", "baseline": deepcopy(self.baseline), "serial": serial,
                "usb": {"status": "completed", "monitor_final_stats": {"queued": 0, "dropped": 0}}}

    def run_probe(self, **options):
        arguments = dict(sequence=6, expected_physical_port="1-1.1.3.3", actuators_isolated=True, sudo_usbmon=True)
        arguments.update(options)
        return legacy.run_probe("read-raw-data", self.output, **arguments)

    def assert_manifest(self):
        manifest = self.output / "SHA256SUMS"
        self.assertTrue(manifest.exists())
        for line in manifest.read_text().splitlines():
            digest, relative = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.output / relative).read_bytes()).hexdigest(), digest)

    def test_exact_single_schedule_full_identity_pin_settings_and_private_evidence(self):
        result = self.run_probe()
        self.session.preflight.assert_called_once_with(self.probe.DEFAULT_PORT)
        self.session.run_session.assert_called_once()
        port, output = self.session.run_session.call_args.args
        options = self.session.run_session.call_args.kwargs
        self.assertEqual((port, output), (self.probe.DEFAULT_PORT, self.output / "capture"))
        schedule = options["probe_schedule"]
        self.assertIsInstance(schedule, tuple)
        self.assertEqual(len(schedule), 1)
        self.assertIsInstance(schedule[0], marvin_probe.ScheduledWrite)
        self.assertEqual((schedule[0].offset_seconds, schedule[0].data, schedule[0].label),
                         (5.0, frame(sequence=6, command=0, status=0), "legacy-read-raw-data"))
        self.assertEqual({key: options[key] for key in ("seconds", "usb_tail_seconds", "usb_close_grace_seconds")}, {
            "seconds": 20, "usb_tail_seconds": 5, "usb_close_grace_seconds": 30,
        })
        for key, value in {"baudrate": 57600, "bytesize": 8, "parity": "N", "stopbits": 1,
                           "dtr": False, "rts": False, "actuators_isolated": True, "sudo_usbmon": True,
                           "allow_unknown_command": True, "allow_telemetry_state_change": True,
                           "usbmon_backend": "binary", "probe_profile": "legacy"}.items():
            self.assertEqual(options[key], value)
        self.assertEqual(options["expected_usb_identity"], self.baseline["usb"])
        self.assertIsNot(options["expected_usb_identity"], self.baseline["usb"])
        self.assertEqual(len(self.session.check_identity.call_args_list), 3)
        for call in self.session.check_identity.call_args_list:
            self.assertEqual(call.args, (self.probe.DEFAULT_PORT, self.baseline))
        self.assertEqual(result["baseline"], self.baseline)
        self.assertEqual(result["status"], "capture_completed")
        self.assertEqual(result["application_acknowledgment"], "not_established")
        self.assertIn("not full USB OUT", result["assessment_limitation"])
        self.assertTrue((self.output / "review.json").exists())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assert_manifest()
        self.serial_open.assert_not_called()
        self.process.assert_not_called()

    def test_all_run_flags_root_and_sequence_rejected_before_runtime_import(self):
        for options in (
            {"actuators_isolated": False}, {"actuators_isolated": 1},
            {"sudo_usbmon": False}, {"sudo_usbmon": 1},
            {"expected_physical_port": None}, {"expected_physical_port": ""},
            {"expected_physical_port": "../1-1.1"}, {"expected_physical_port": "1-0"},
            {"expected_physical_port": "1-1\n"}, {"sequence": True}, {"sequence": 65536},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_probe(**options)
        with patch.object(legacy.os, "geteuid", return_value=0), self.assertRaisesRegex(ValueError, "ordinary user"):
            self.run_probe()
        with self.assertRaises(ValueError):
            legacy.run_probe("get-config", None, expected_physical_port="1-1.1.3.3",
                             actuators_isolated=True, sudo_usbmon=True)
        self.runtime.assert_not_called()
        self.session.preflight.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_preflight_checks_all_identity_anchors_before_output_or_capture(self):
        original = deepcopy(self.baseline)
        changes = (
            ("idVendor", "0000"), ("idProduct", "444a"), ("descriptors_bytes", 70),
            ("descriptors_bytes", 71.0), ("descriptors_sha256", "0" * 64),
            ("physical_port", "1-1.1.3.4"), ("usb_path", "/fake/sys/1-1.1.3.4"),
            ("usb_path", "1-1.1.3.3"), ("busnum", 0), ("devnum", False),
            ("sysfs_inode", None), ("sysfs_device", None),
        )
        for key, value in changes:
            self.baseline = deepcopy(original)
            self.baseline["usb"][key] = value
            self.session.preflight.return_value = self.baseline
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.run_probe()
        for baseline in ({}, {"usb": None}, {**original, "tty_rdev": None}):
            self.session.preflight.return_value = baseline
            with self.assertRaises(ValueError):
                self.run_probe()
        self.session.run_session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_existing_output_links_missing_parent_and_creation_race_never_overwrite(self):
        self.output.mkdir()
        marker = self.output / "old.txt"
        marker.write_bytes(b"old capture")
        with self.assertRaises(FileExistsError):
            self.run_probe()
        self.assertEqual(marker.read_bytes(), b"old capture")
        self.runtime.assert_not_called()
        self.output = self.root / "link"
        self.output.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError):
            self.run_probe()
        parent_link = self.root / "linked-parent"
        parent_link.symlink_to(self.root, target_is_directory=True)
        self.output = parent_link / "fresh"
        with self.assertRaises(ValueError):
            self.run_probe()
        self.output = self.root / "missing-parent" / "fresh"
        with self.assertRaises(FileNotFoundError):
            self.run_probe()
        self.assertFalse(self.output.parent.exists())
        self.output = self.root / "race"
        def race(_port):
            self.output.mkdir()
            (self.output / "winner.txt").write_bytes(b"another capture")
            return self.baseline
        self.session.preflight.side_effect = race
        with self.assertRaises(FileExistsError):
            self.run_probe()
        self.assertEqual((self.output / "winner.txt").read_bytes(), b"another capture")
        self.session.run_session.assert_not_called()
        self.session.evidence_manifest.assert_not_called()

    def test_preflight_error_has_no_new_directory_or_capture(self):
        self.session.preflight.side_effect = OSError("identity unreadable")
        with self.assertRaisesRegex(OSError, "unreadable"):
            self.run_probe()
        self.assertFalse(self.output.exists())
        self.session.run_session.assert_not_called()

    def test_identity_change_at_ready_prevents_mock_serial_stage_and_preserves_evidence(self):
        self.session.check_identity.side_effect = [None, OSError("changed before open")]
        with self.assertRaisesRegex(OSError, "before open"):
            self.run_probe()
        self.assertFalse((self.output / "capture/serial").exists())
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")
        self.session.run_session.assert_called_once()
        self.assert_manifest()

    def test_full_baseline_change_and_incomplete_results_are_failures(self):
        for index, changed in enumerate(("baseline", "serial", "usb", "write")):
            self.output = self.root / f"failure-{index}"
            def altered(port, output, **options):
                result = self.capture(port, output, **options)
                if changed == "baseline":
                    result["baseline"]["tty_rdev"] += 1
                elif changed in ("serial", "usb"):
                    result[changed]["status"] = "failed"
                else:
                    result["serial"].update(transmit_status="unknown", application_bytes_written=None)
                return result
            self.session.run_session.side_effect = altered
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.run_probe()
            self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "failed")
            self.assert_manifest()
        self.campaign.assess_segment.assert_not_called()

    def test_early_rx_suppression_is_capture_only_not_query_or_ack_success(self):
        def suppressed(port, output, **options):
            result = self.capture(port, output, **options)
            result["serial"].update(transmit_status="suppressed_schedule_rx",
                                    application_bytes_written=0, scheduled_writes_completed=0)
            return result
        self.session.run_session.side_effect = suppressed
        result = self.run_probe()
        self.assertEqual(result["status"], "capture_completed")
        self.assertEqual(result["session_result"]["serial"]["application_bytes_written"], 0)
        self.assertEqual(result["source_assessment"]["outcome"], "received_data_stop")
        self.assertEqual(result["application_acknowledgment"], "not_established")
        self.assertNotIn("usb_out_confirmed_bytes", result)

    def test_transport_failure_keeps_partial_files_without_retry(self):
        def failed(port, output, **options):
            self.capture(port, output, **options)
            marvin_session.write_json(output / "serial/metadata.json", {
                "status": "failed", "transmit_status": "unknown", "application_bytes_written": None,
                "known_application_bytes_written": 0, "scheduled_writes_completed": 0, "bytes_received": 0,
            })
            raise marvin_probe.serial.SerialTimeoutException("write outcome unknown")
        self.session.run_session.side_effect = failed
        with self.assertRaisesRegex(OSError, "outcome unknown"):
            self.run_probe()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["partial"]["transmit_status"], "unknown")
        self.assertIsNone(metadata["partial"]["application_bytes_written"])
        self.assertEqual((self.output / "capture/serial/received.bin").read_bytes(), b"fixture-response")
        self.session.run_session.assert_called_once()
        self.assert_manifest()

    def test_assessment_failure_is_not_capture_success_and_partial_error_is_explicit(self):
        self.campaign.assess_segment.side_effect = ValueError("USB pairing incomplete")
        self.campaign.partial_segment.side_effect = ValueError("bad partial metadata")
        with self.assertRaisesRegex(ValueError, "pairing incomplete"):
            self.run_probe()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["error"], "USB pairing incomplete")
        self.assertEqual(metadata["partial_metadata_error"], "bad partial metadata")
        self.assert_manifest()

    def test_manifest_failure_is_reported_and_metadata_not_success_shaped(self):
        self.session.evidence_manifest.side_effect = OSError("manifest write failed")
        with self.assertRaisesRegex(OSError, "manifest write failed"):
            self.run_probe()
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["evidence_sealing_error"], "manifest write failed")
        self.assertTrue((self.output / "capture/serial/received.bin").exists())

    def test_cli_success_failure_and_interrupt_are_mocked_and_nonzero_on_failure(self):
        args = ["read-raw-data", "--sequence", "6", "--output", str(self.output),
                "--expected-physical-port", "1-1.1.3.3", "--actuators-isolated", "--sudo-usbmon", "--run"]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(legacy.main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "capture_completed")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(legacy.main(args), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")
        self.session.run_session.assert_called_once()
        self.output = self.root / "interrupt"
        args[args.index("--output") + 1] = str(self.output)
        self.session.run_session.side_effect = KeyboardInterrupt
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(legacy.main(args), 130)
        self.assertEqual(json.loads(output.getvalue())["status"], "interrupted")
        self.assertEqual(json.loads((self.output / "metadata.json").read_text())["status"], "interrupted")
        self.assert_manifest()
        self.serial_open.assert_not_called()
        self.process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
