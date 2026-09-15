import copy
import json
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
        options = {"actuators_isolated": True, "allow_line_state_change": True}
        options.update(kwargs)
        return boot.run_boot_capture("/dev/test", self.output, **options)

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
            self.assertIs(call.kwargs["allow_line_state_change"], True)
            self.assertNotIn("allow_line_state_trial", call.kwargs)
        self.assertTrue((self.output / "before-cycle-ready.json").exists())
        self.assertTrue((self.output / "after-cycle-ready.json").exists())
        self.assertTrue((self.output / "SHA256SUMS").exists())
        self.assertIn("initial_segment_error", result)
        self.assertTrue(result["line_state_change_authorized"])

    def test_line_state_requires_separate_exact_acknowledgment_before_preflight(self):
        for value in (False, "true", "false", 1, 0, None, [True]):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "line.state.change"):
                self.run_boot(allow_line_state_change=value)
        self.preflight.assert_not_called()
        self.segment.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cli_passes_explicit_line_state_acknowledgment(self):
        with patch("sys.argv", ["marvin_boot_capture", "--output", str(self.output),
                               "--actuators-isolated", "--allow-line-state-change"]), \
                patch.object(boot, "run_boot_capture", return_value={"status": "fixture"}) as capture:
            self.assertEqual(boot.main(), 0)
            self.assertTrue(capture.call_args.kwargs["allow_line_state_change"])

    def test_no_reenumeration_does_not_claim_power_cycle(self):
        self.disconnect = False
        result = self.run_boot()
        self.assertEqual(result["status"], "completed_without_reenumeration")
        self.returned.assert_not_called()

    def test_sealing_failure_invalidates_both_completion_paths_and_keeps_original_failure(self):
        cases = (
            (False, None, "completed_without_reenumeration"),
            (True, None, "completed_with_reconnect_gap"),
            (True, OSError("return capture failed"), "failed"),
            (True, KeyboardInterrupt(), "interrupted"),
        )
        for index, (disconnect, original, status) in enumerate(cases):
            with self.subTest(status=status):
                self.output = Path(self.temp.name) / f"seal-{index}"
                self.disconnect = disconnect
                self.return_error = original
                error = OSError("manifest write failed")
                with patch.object(boot.marvin_session, "evidence_manifest", side_effect=error):
                    with self.assertRaises(type(original) if original is not None else OSError) as raised:
                        self.run_boot()
                self.assertIs(raised.exception, original if original is not None else error)
                metadata = json.loads((self.output / "metadata.json").read_text())
                self.assertEqual(metadata["status"], "failed")
                self.assertEqual(metadata["status_before_sealing"], status)
                self.assertEqual(metadata["evidence_sealing_error"], str(error))
                if disconnect:
                    self.assertEqual(metadata["initial_segment_error"], str(self.initial_error))
                if isinstance(original, OSError):
                    self.assertEqual(metadata["error"], str(original))

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
        self.preflight.assert_not_called()
        self.segment.assert_not_called()

    def test_dangling_output_symlink_cannot_redirect_evidence(self):
        root = Path(self.temp.name)
        for relative in (False, True):
            with self.subTest(relative=relative):
                target = root / f"missing-target-{relative}"
                self.output = root / f"output-link-{relative}"
                destination = Path(target.name) if relative else target
                self.output.symlink_to(destination, target_is_directory=True)
                with self.assertRaises(FileExistsError) as raised:
                    self.run_boot()
                self.assertEqual(raised.exception.filename, str(self.output))
                self.assertEqual(self.output.readlink(), destination)
                self.assertFalse(target.exists())
        self.preflight.assert_not_called()
        self.segment.assert_not_called()
        self.changed.assert_not_called()
        self.returned.assert_not_called()

    def test_new_nested_output_parents_remain_supported(self):
        self.output = self.output / "missing" / "nested" / "boot"
        self.disconnect = False
        result = self.run_boot()
        self.assertEqual(result["status"], "completed_without_reenumeration")
        self.assertTrue((self.output / "SHA256SUMS").is_file())
        self.preflight.assert_called_once()
        self.segment.assert_called_once()

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

    def test_late_preflight_results_or_errors_cannot_override_the_timeout(self):
        invalid = copy.deepcopy(RETURNED)
        invalid["usb"]["physical_port"] = "wrong"
        for result in (RETURNED, invalid, ValueError("late identity error"), FileNotFoundError()):
            self.clock.now = 0

            def preflight(port, *, deadline):
                self.assertEqual(deadline, 1)
                self.clock.now = deadline
                if isinstance(result, Exception):
                    raise result
                return result

            with self.subTest(result=result), patch.object(Path, "exists", return_value=True), \
                    patch.object(boot.marvin_session, "preflight", side_effect=preflight), \
                    self.assertRaisesRegex(TimeoutError, "within 1 seconds"):
                boot.wait_for_return("/dev/test", BASELINE, timeout=1)
        self.sleep.assert_not_called()

    def run_real_preflight(self, *, udev_seconds=0.4, fuser_seconds=0.1, cache_seconds=0.1, timeout=1):
        self.commands = []
        node = Mock()
        node.stat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=123)
        properties = ("ID_VENDOR_ID=045e\nID_MODEL_ID=4444\n"
                      "ID_MM_DEVICE_IGNORE=1\nID_MM_PORT_IGNORE=1\n")

        def command(arguments, **options):
            duration = udev_seconds if arguments[0] == "udevadm" else fuser_seconds
            self.commands.append((arguments[0], options["timeout"], self.clock.now))
            self.clock.now += min(duration, options["timeout"])
            if duration > options["timeout"]:
                raise subprocess.TimeoutExpired(arguments, options["timeout"])
            return subprocess.CompletedProcess(
                arguments, 0 if arguments[0] == "udevadm" else 1,
                properties if arguments[0] == "udevadm" else "", "",
            )

        def identity(path):
            self.clock.now += cache_seconds
            return RETURNED["usb"]

        with patch.object(Path, "exists", return_value=True), \
                patch.object(Path, "resolve", return_value=node), \
                patch.object(boot.marvin_session, "usb_path_for_tty", return_value=Path("/fake/usb")), \
                patch.object(boot.marvin_usbmon, "read_identity", side_effect=identity), \
                patch.object(boot.marvin_probe.subprocess, "run", side_effect=command), \
                patch.object(boot.marvin_probe.serial, "Serial", side_effect=AssertionError("serial opened")):
            return boot.wait_for_return("/dev/test", BASELINE, timeout=timeout)

    def test_real_preflight_shares_remaining_budget_across_both_subprocesses(self):
        returned = self.run_real_preflight()
        self.assertEqual(returned["usb"], RETURNED["usb"])
        self.assertEqual(self.commands, [("udevadm", 1, 0), ("fuser", 0.5, 0.5)])
        self.assertAlmostEqual(self.clock.now, 0.6)
        self.sleep.assert_not_called()

    def test_either_preflight_command_timeout_is_terminal_at_the_return_deadline(self):
        for options, expected_calls in (({"udev_seconds": 2}, 1), ({"fuser_seconds": 2}, 2)):
            self.clock.now = 0
            with self.subTest(options=options), self.assertRaises(TimeoutError) as raised:
                self.run_real_preflight(**options)
            self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)
            self.assertEqual(len(self.commands), expected_calls)
            self.assertEqual(self.clock.now, 1)
        self.sleep.assert_not_called()

    def test_expired_cached_identity_read_cannot_start_the_next_preflight_command(self):
        with self.assertRaises(TimeoutError):
            self.run_real_preflight(cache_seconds=1)
        self.assertEqual(len(self.commands), 1)
        self.sleep.assert_not_called()

    def test_command_limit_before_return_deadline_remains_a_preflight_error(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_real_preflight(udev_seconds=6, timeout=90)
        self.assertEqual(self.commands, [("udevadm", 5, 0)])
        self.assertEqual(self.clock.now, 5)
        self.sleep.assert_not_called()

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

    def test_identity_transition_must_finish_strictly_before_deadline(self):
        for offset in (-0.001, 0, 0.001):
            self.clock.now = 0

            def observe_change(baseline):
                self.clock.now = boot.IDENTITY_TRANSITION_SECONDS + offset
                return True

            with self.subTest(offset=offset), patch.object(boot, "identity_changed", side_effect=observe_change):
                self.assertEqual(boot.wait_for_identity_change(BASELINE), offset < 0)
        self.sleep.assert_not_called()

    def test_permission_failure_is_not_device_removal(self):
        with patch.object(Path, "stat", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                boot.identity_changed(BASELINE)
            with self.assertRaises(PermissionError):
                boot.wait_for_identity_change(BASELINE)
            self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
