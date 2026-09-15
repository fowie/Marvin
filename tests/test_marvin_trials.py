import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_protocol, marvin_trials


IDENTITY = {"busnum": 1, "devnum": 12, "usb_path": "/fake/1-3.3"}
BASELINE = {"usb": IDENTITY, "tty": "/dev/fake", "tty_rdev": 1}


def case_fixture(output, *, receive=b"", wrong_out=False):
    (output / "usb").mkdir(parents=True)
    (output / "serial").mkdir()
    (output / "serial" / "received.bin").write_bytes(receive)
    packet = marvin_protocol.get_config_request()
    payload = packet[:-1] + b"\x00" if wrong_out else packet
    words = " ".join(payload[i:i + 4].hex() for i in range(0, len(payload), 4))
    trace = f"aa 100 S Bo:1:012:3 -115 12 = {words}\naa 101 C Bo:1:012:3 0 12 >\n"
    if receive:
        words = " ".join(receive[i:i + 4].hex() for i in range(0, len(receive), 4))
        trace += f"bb 102 S Bi:1:012:2 -115 128 <\nbb 103 C Bi:1:012:2 0 {len(receive)} = {words}\n"
    (output / "usb" / "usbmon.txt").write_text(trace)
    result = {
        "status": "completed", "baseline": BASELINE, "requested_probe_hex": packet.hex(),
        "serial": {"status": "completed", "bytes_received": len(receive),
                   "application_bytes_written": 12, "transmit_status": "written"},
        "usb": {"status": "completed", "monitor_final_stats": {"queued": 0, "dropped": 0}},
    }
    (output / "metadata.json").write_text(json.dumps(result))
    return result


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "case"

    def test_matching_out_is_not_called_an_application_ack(self):
        result = case_fixture(self.output)
        assessment = marvin_trials.assess_case(self.output, result)
        self.assertEqual(assessment["outcome"], "silent_out_confirmed")
        self.assertEqual(assessment["usb_out_bytes"], 12)

    def test_completed_metadata_with_evidence_gaps_never_advances_even_on_rx(self):
        for field in ("unretained_partial_line_bytes", "unprocessed_records",
                      "unprocessed_record_bytes", "unaccounted_retained_bytes"):
            for receive in (b"", b"reply"):
                with self.subTest(field=field, receive=receive):
                    self.output = Path(self.temp.name) / f"{field}-{len(receive)}"
                    result = case_fixture(self.output, receive=receive)
                    result["usb"][field] = 1
                    with patch.object(marvin_trials.marvin_usbmon, "analyze_file") as analyze:
                        with self.assertRaisesRegex(ValueError, "Incomplete USB capture"):
                            marvin_trials.assess_case(self.output, result)
                    analyze.assert_not_called()

    def test_any_rx_stops_even_if_serial_suppressed_the_write(self):
        result = case_fixture(self.output, receive=b"banner")
        result["serial"]["transmit_status"] = "suppressed_pre_probe_rx"
        result["serial"]["application_bytes_written"] = 0
        trace = self.output / "usb" / "usbmon.txt"
        trace.write_text("\n".join(trace.read_text().splitlines()[2:]) + "\n")
        assessment = marvin_trials.assess_case(self.output, result)
        self.assertEqual(assessment["outcome"], "received_data_stop")

    def test_usb_only_input_is_not_ignored(self):
        result = case_fixture(self.output, receive=b"early")
        (self.output / "serial" / "received.bin").write_bytes(b"")
        result["serial"]["bytes_received"] = 0
        self.assertEqual(marvin_trials.assess_case(self.output, result)["outcome"], "received_data_stop")

    def test_bad_out_or_loss_prevents_continuation(self):
        result = case_fixture(self.output, wrong_out=True)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            marvin_trials.assess_case(self.output, result)
        result["usb"]["monitor_final_stats"]["dropped"] = 1
        with self.assertRaisesRegex(ValueError, "loss"):
            marvin_trials.assess_case(self.output, result)

    def test_unmatched_completion_or_byte_count_is_rejected(self):
        result = case_fixture(self.output)
        trace = self.output / "usb" / "usbmon.txt"
        original = trace.read_text()
        trace.write_text(original + "cc 104 C Bo:1:012:3 0 12 >\n")
        with self.assertRaisesRegex(ValueError, "pairing"):
            marvin_trials.assess_case(self.output, result)
        trace.write_text(original)
        result["serial"]["bytes_received"] = 1
        with self.assertRaisesRegex(ValueError, "byte count"):
            marvin_trials.assess_case(self.output, result)


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "trials"
        self.preflight = patch.object(marvin_trials.marvin_session, "preflight", return_value=BASELINE).start()
        self.identity = patch.object(marvin_trials.marvin_session, "check_identity").start()
        patch.object(marvin_trials.os, "geteuid", return_value=1000).start()
        self.calls = []
        self.receive_on = None
        self.fail_on = None
        self.run_session = patch.object(marvin_trials.marvin_session, "run_session", side_effect=self.session).start()
        self.addCleanup(patch.stopall)

    def session(self, port, output, **options):
        self.calls.append(options)
        if len(self.calls) == self.fail_on:
            raise OSError("write outcome unknown")
        return case_fixture(output, receive=b"x" if len(self.calls) == self.receive_on else b"")

    def run_trials(self, **options):
        kwargs = {"actuators_isolated": True, "allow_unknown_command": True, "allow_line_state_trials": True}
        kwargs.update(options)
        with contextlib.redirect_stdout(io.StringIO()):
            return marvin_trials.run_trials("/dev/fake", self.output, **kwargs)

    def test_four_fixed_cases_are_bounded_and_pin_identity(self):
        result = self.run_trials()
        self.assertEqual(result["status"], "completed_silent")
        self.assertEqual(result["initial_power_state"], "existing-unverified")
        self.assertEqual(len(self.calls), 4)
        self.assertEqual([(c["dtr"], c["rts"]) for c in self.calls],
                         [(True, True), (True, False), (False, False), (False, True)])
        for call in self.calls:
            self.assertEqual(call["probe_delay"], 5)
            self.assertEqual(call["seconds"], 15)
            self.assertEqual(call["expected_usb_identity"], IDENTITY)
            self.assertTrue(call["probe_get_config"])
            self.assertTrue(call["allow_line_state_trial"])
        self.assertTrue((self.output / "SHA256SUMS").is_file())

    def test_rx_stops_remaining_cases(self):
        self.receive_on = 1
        result = self.run_trials()
        self.assertEqual(result["status"], "stopped_on_rx")
        self.assertEqual(len(self.calls), 1)

    def test_uncertain_write_is_not_retried_or_followed_by_another_case(self):
        self.fail_on = 2
        with self.assertRaisesRegex(OSError, "unknown"):
            self.run_trials()
        self.assertEqual(len(self.calls), 2)
        result = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertTrue((self.output / "SHA256SUMS").exists())

    def test_missing_authorization_or_invalid_power_state_never_opens(self):
        for options in ({"actuators_isolated": False}, {"allow_unknown_command": False},
                        {"allow_line_state_trials": False}, {"power_state": "assumed-cold"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_trials(**options)
        self.preflight.assert_not_called()

    def test_authorizations_require_exact_true_before_preflight_or_output(self):
        for flag in ("actuators_isolated", "allow_unknown_command", "allow_line_state_trials"):
            for value in ("false", "true", 1, 2, [True], {"yes": True}, object(), None, 0, ""):
                with self.subTest(flag=flag, value=value), self.assertRaisesRegex(ValueError, flag):
                    self.run_trials(**{flag: value})
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.run_session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_sudo_selector_requires_boolean_before_preflight_or_output(self):
        for value in ("false", "true", 1, 0, [True], object(), None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "sudo_usbmon"):
                self.run_trials(sudo_usbmon=value)
        self.preflight.assert_not_called()
        self.identity.assert_not_called()
        self.run_session.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_default_cli_is_offline_and_selection_cannot_duplicate(self):
        with patch("sys.argv", ["marvin_trials"]), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(marvin_trials.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["maximum_application_bytes"], 48)
        self.preflight.assert_not_called()
        for names in ([], ["high-high", "high-high"], ["unknown"]):
            with self.assertRaises(ValueError):
                marvin_trials.make_plan(names)
        self.assertEqual(len(marvin_trials.make_plan(["high-high"])["cases"]), 1)


if __name__ == "__main__":
    unittest.main()
