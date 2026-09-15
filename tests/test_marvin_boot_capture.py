import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
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


class FakeClock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


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
        self.clock = FakeClock()
        patch.object(boot.time, "monotonic", side_effect=self.clock.monotonic).start()
        self.sleep = patch.object(boot.time, "sleep", side_effect=self.clock.sleep).start()
        patch("builtins.print").start()
        self.addCleanup(patch.stopall)
        self.disconnect = True
        self.initial_error = OSError("device disconnected")
        self.ready = True
        self.return_error = None

    def run_segment(self, port, output, **options):
        output.mkdir()
        if self.ready:
            options["ready_callback"](options["expected_usb_identity"])
        if output.name == "before-cycle" and self.disconnect:
            raise self.initial_error
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
        self.assertEqual(self.clock.now, boot.IDENTITY_TRANSITION_SECONDS)
        self.assertLessEqual(self.changed.call_count, 21)
        metadata = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["error"], "device disconnected")

    def test_partial_removal_waits_for_directory_transition_before_return(self):
        self.initial_error = boot.marvin_usbmon.IdentityError("Cannot read cached sysfs descriptors.")
        self.changed.side_effect = [False, False, True]
        result = self.run_boot()
        self.assertEqual(result["status"], "completed_with_reconnect_gap")
        self.assertEqual(result["initial_segment_error"], str(self.initial_error))
        self.assertEqual(self.changed.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertAlmostEqual(self.clock.now, 0.2)
        self.returned.assert_called_once_with("/dev/test", BASELINE)
        self.assertEqual(self.segment.call_count, 2)

    def test_failure_before_readiness_is_not_retried(self):
        self.ready = False
        with self.assertRaises(OSError):
            self.run_boot()
        self.returned.assert_not_called()
        self.changed.assert_not_called()
        self.sleep.assert_not_called()

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

    def test_isolation_requires_exact_true_before_preflight_or_output(self):
        for value in ("false", "true", 1, 2, [True], {"yes": True}, object(), None, 0, ""):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "actuators_isolated"):
                boot.run_boot_capture("/dev/test", self.output, actuators_isolated=value)
        self.preflight.assert_not_called()
        self.segment.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_sudo_selector_requires_boolean_before_preflight_or_output(self):
        for value in ("false", "true", 1, 0, [True], object(), None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "sudo_usbmon"):
                self.run_boot(sudo_usbmon=value)
        self.preflight.assert_not_called()
        self.segment.assert_not_called()
        self.assertFalse(self.output.exists())

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
    def setUp(self):
        self.clock = FakeClock()
        patch.object(boot.time, "monotonic", side_effect=self.clock.monotonic).start()
        self.sleep = patch.object(boot.time, "sleep", side_effect=self.clock.sleep).start()
        self.addCleanup(patch.stopall)

    def test_polling_timeouts_are_bounded_before_identity_access(self):
        with patch.object(Path, "exists", side_effect=AssertionError("unexpected exists")) as exists, \
                patch.object(boot, "identity_changed", side_effect=AssertionError("unexpected stat")) as changed:
            for timeout in (0, -1, True, "1", None, float("nan"), float("inf"), 91):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    boot.wait_for_return("/dev/test", BASELINE, timeout=timeout)
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    boot.wait_for_identity_change(BASELINE, timeout=timeout)
            with self.assertRaises(ValueError):
                boot.wait_for_identity_change(BASELINE, timeout=3)
            exists.assert_not_called()
            changed.assert_not_called()
            self.sleep.assert_not_called()

    def test_unexpected_preflight_error_propagates(self):
        for error in (ValueError("missing modem exclusions"), PermissionError("denied"),
                      subprocess.CalledProcessError(1, "udevadm")):
            with self.subTest(error=error), patch.object(Path, "exists", return_value=True), \
                    patch.object(boot.marvin_session, "preflight", side_effect=error) as preflight:
                with self.assertRaises(type(error)) as raised:
                    boot.wait_for_return("/dev/test", BASELINE, timeout=1)
                self.assertIs(raised.exception, error)
                preflight.assert_called_once()
                self.sleep.assert_not_called()

    def test_absent_device_times_out(self):
        with patch.object(Path, "exists", return_value=False), \
                patch.object(boot.marvin_session, "preflight") as preflight:
            with self.assertRaisesRegex(TimeoutError, "within 0.25 seconds"):
                boot.wait_for_return("/dev/test", BASELINE, timeout=0.25)
            preflight.assert_not_called()
            self.assertEqual(self.sleep.call_count, 3)
            self.assertEqual(self.clock.now, 0.25)

    def test_accepts_ready_return(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", return_value=RETURNED
        ):
            self.assertEqual(boot.wait_for_return("/dev/test", BASELINE), RETURNED)

    def test_retries_only_disappearance_during_preflight(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", side_effect=[FileNotFoundError(), RETURNED]
        ):
            self.assertEqual(boot.wait_for_return("/dev/test", BASELINE), RETURNED)
            self.sleep.assert_called_once_with(0.1)

    def test_partial_enumeration_retries_cached_identity_failures(self):
        errors = [
            boot.marvin_usbmon.IdentityError("Cannot read cached USB identity attributes."),
            boot.marvin_usbmon.IdentityError("Cannot read cached sysfs descriptors."),
            boot.marvin_usbmon.IdentityError("Cached descriptors are empty, short, or exceed the size bound."),
        ]
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", side_effect=[*errors, RETURNED]
        ) as preflight:
            self.assertEqual(boot.wait_for_return("/dev/test", BASELINE, timeout=1), RETURNED)
            self.assertEqual(preflight.call_count, 4)
            self.assertEqual(self.sleep.call_count, 3)

    def test_persistent_identity_failure_is_bounded(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", side_effect=boot.marvin_usbmon.IdentityError("partial")
        ) as preflight:
            with self.assertRaisesRegex(TimeoutError, "within 0.25 seconds"):
                boot.wait_for_return("/dev/test", BASELINE, timeout=0.25)
            self.assertEqual(preflight.call_count, 3)
            self.assertEqual(self.sleep.call_count, 3)
            self.assertEqual(self.clock.now, 0.25)

    def test_wrong_return_identity_is_not_retried(self):
        invalid = copy.deepcopy(RETURNED)
        invalid["usb"]["physical_port"] = "1-3"
        with patch.object(Path, "exists", return_value=True), patch.object(
            boot.marvin_session, "preflight", return_value=invalid
        ) as preflight:
            with self.assertRaisesRegex(ValueError, "physical_port"):
                boot.wait_for_return("/dev/test", BASELINE, timeout=1)
            preflight.assert_called_once()
            self.sleep.assert_not_called()

    def test_identity_transition_waits_for_removal_or_changed_inode(self):
        unchanged = SimpleNamespace(st_dev=25, st_ino=100)
        for transition in (FileNotFoundError(), SimpleNamespace(st_dev=25, st_ino=200)):
            with self.subTest(transition=transition), patch.object(
                Path, "stat", side_effect=[unchanged, unchanged, transition]
            ) as inspecting:
                before = self.clock.now
                self.assertTrue(boot.wait_for_identity_change(BASELINE, timeout=1))
                self.assertEqual(inspecting.call_count, 3)
                self.assertAlmostEqual(self.clock.now - before, 0.2)

    def test_identity_transition_stops_at_deadline_without_reenumeration(self):
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_dev=25, st_ino=100)) as inspecting:
            self.assertFalse(boot.wait_for_identity_change(BASELINE, timeout=0.25))
            self.assertEqual(inspecting.call_count, 3)
            self.assertEqual(self.sleep.call_count, 3)
            self.assertEqual(self.clock.now, 0.25)

    def test_permission_failure_is_not_device_removal(self):
        with patch.object(Path, "stat", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                boot.identity_changed(BASELINE)
            with self.assertRaises(PermissionError):
                boot.wait_for_identity_change(BASELINE)
            self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
