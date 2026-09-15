import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_boot_capture as boot


BASELINE = {"usb": {
    "usb_path": "/sys/devices/test/1-2", "physical_port": "1-2",
    "busnum": 1, "devnum": 7, "idVendor": "045e", "idProduct": "4444",
    "descriptors_sha256": "same", "descriptors_bytes": 71,
    "sysfs_device": 25, "sysfs_inode": 100,
}}
RETURNED = copy.deepcopy(BASELINE)
RETURNED["usb"].update(devnum=9, sysfs_inode=200)


class BootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "boot"
        self.root = patch.object(boot.os, "geteuid", return_value=1000).start()
        self.preflight = patch.object(
            boot.marvin_session, "preflight", return_value=BASELINE
        ).start()
        self.segment = patch.object(
            boot.marvin_session, "run_session", side_effect=self.run_segment
        ).start()
        self.changed = patch.object(boot, "identity_changed", return_value=True).start()
        self.returned = patch.object(boot, "wait_for_return", return_value=RETURNED).start()
        patch("builtins.print").start()
        self.addCleanup(patch.stopall)
        self.disconnect = True
        self.ready = True
        self.return_error = None

    def run_segment(self, port, output, **options):
        output.mkdir()
        if self.ready:
            options["ready_callback"](options["expected_usb_identity"])
        if output.name == "before-cycle" and self.disconnect:
            raise OSError("device disconnected")
        if output.name == "after-cycle" and self.return_error:
            raise self.return_error
        return {"status": "completed"}

    def run_boot(self, **kwargs):
        return boot.run_boot_capture(
            "/dev/test", self.output, actuators_isolated=True, **kwargs
        )

    def test_one_reconnect_and_no_application_probe(self):
        result = self.run_boot(sudo_usbmon=True)
        self.assertEqual(result["status"], "completed_with_reconnect_gap")
        self.assertEqual(self.segment.call_count, 2)
        self.assertEqual(
            self.segment.call_args.kwargs["expected_usb_identity"], RETURNED["usb"]
        )
        for call in self.segment.call_args_list:
            self.assertNotIn("probe_cr", call.kwargs)
            self.assertTrue(call.kwargs["dtr"])
            self.assertTrue(call.kwargs["rts"])
        self.assertTrue((self.output / "before-cycle-ready.json").exists())
        self.assertTrue((self.output / "after-cycle-ready.json").exists())
        self.assertTrue((self.output / "SHA256SUMS").exists())
        self.assertIn("initial_segment_error", result)

    def test_no_reenumeration_does_not_claim_power_cycle(self):
        self.disconnect = False
        result = self.run_boot()
        self.assertEqual(result["status"], "completed_without_reenumeration")
        self.returned.assert_not_called()

    def test_failure_without_identity_change_is_not_retried(self):
        self.changed.return_value = False
        with self.assertRaises(OSError):
            self.run_boot()
        self.returned.assert_not_called()
        self.assertEqual(self.segment.call_count, 1)

    def test_failure_before_readiness_is_not_retried(self):
        self.ready = False
        with self.assertRaises(OSError):
            self.run_boot()
        self.returned.assert_not_called()

    def test_second_disconnect_is_terminal_and_evidence_is_retained(self):
        self.return_error = OSError("second disconnect")
        with self.assertRaisesRegex(OSError, "second disconnect"):
            self.run_boot()
        self.assertEqual(self.segment.call_count, 2)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertTrue((self.output / "SHA256SUMS").exists())

    def test_return_timeout_is_not_success(self):
        self.returned.side_effect = TimeoutError("no return")
        with self.assertRaises(TimeoutError):
            self.run_boot()
        self.assertEqual(self.segment.call_count, 1)

    def test_requires_isolation_and_nonroot(self):
        with self.assertRaises(ValueError):
            boot.run_boot_capture("/dev/test", self.output)
        self.root.return_value = 0
        with self.assertRaises(ValueError):
            self.run_boot()
        self.preflight.assert_not_called()

    def test_output_cannot_be_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_boot()
        self.segment.assert_not_called()

    def test_accepts_same_physical_device_with_new_address(self):
        boot.validate_return(BASELINE, RETURNED)

    def test_rejects_different_path_identity_or_descriptors(self):
        for key in ("usb_path", "physical_port", "busnum", "idVendor", "idProduct",
                    "descriptors_sha256", "descriptors_bytes"):
            with self.subTest(key=key):
                invalid = copy.deepcopy(RETURNED)
                invalid["usb"][key] = "different"
                with self.assertRaises(ValueError):
                    boot.validate_return(BASELINE, invalid)

    def test_requires_new_enumeration(self):
        with self.assertRaisesRegex(ValueError, "No new USB enumeration"):
            boot.validate_return(BASELINE, BASELINE)

class ReturnTests(unittest.TestCase):
    def test_unexpected_preflight_error_propagates(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", side_effect=ValueError("missing modem exclusions")
        ):
            with self.assertRaisesRegex(ValueError, "modem exclusions"):
                boot.wait_for_return("/dev/test", BASELINE, timeout=1)

    def test_absent_device_times_out(self):
        with patch.object(Path, "exists", return_value=False), patch.object(
            boot.time, "monotonic", side_effect=[0, 0, 2]
        ), patch.object(boot.time, "sleep"):
            with self.assertRaises(TimeoutError):
                boot.wait_for_return("/dev/test", BASELINE, timeout=1)

    def test_accepts_ready_return(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", return_value=RETURNED
        ):
            self.assertEqual(boot.wait_for_return("/dev/test", BASELINE), RETURNED)

    def test_retries_only_disappearance_during_preflight(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", side_effect=[FileNotFoundError(), RETURNED]
        ), patch.object(boot.time, "sleep"):
            self.assertEqual(boot.wait_for_return("/dev/test", BASELINE), RETURNED)

    def test_permission_failure_is_not_device_removal(self):
        with patch.object(Path, "stat", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                boot.identity_changed(BASELINE)


if __name__ == "__main__":
    unittest.main()
