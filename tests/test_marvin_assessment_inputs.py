"""Offline input-boundary regressions for all three trace assessment paths."""

from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_marvin_campaign as campaign_fixtures
from tests import test_marvin_trials as trial_fixtures
from tools import marvin_campaign, marvin_stream, marvin_trials, marvin_usbmon


class AssessmentInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self, kind):
        output = self.root / kind
        if kind == "campaign":
            schedule, _ = marvin_campaign.compile_segment(campaign_fixtures.plan()["segments"][0])
            result = campaign_fixtures.fixture(output, schedule)
            assess = lambda: marvin_campaign.assess_segment(output, result, schedule)
        elif kind == "rejected":
            campaign_fixtures.rejected_fixture(output)
            assess = lambda: marvin_campaign.rejected_settings_evidence(output, campaign_fixtures.BASELINE)
        else:
            result = trial_fixtures.case_fixture(output)
            assess = lambda: marvin_trials.assess_case(output, result)
        return output / "usb/usbmon.txt", assess

    def test_records_and_summary_share_one_bounded_read_and_one_parse_per_record(self):
        for kind in ("campaign", "rejected", "trial"):
            with self.subTest(kind=kind):
                trace, assess = self.fixture(kind)
                original = trace.read_bytes()
                with patch.object(marvin_usbmon, "read_regular_file", wraps=marvin_usbmon.read_regular_file) as read, \
                        patch.object(marvin_usbmon, "parse_record", wraps=marvin_usbmon.parse_record) as parse, \
                        patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded trace read")):
                    result = assess()
                read.assert_called_once_with(trace, max_bytes=marvin_usbmon.DEFAULT_MAX_BYTES)
                self.assertEqual(parse.call_count, len(original.splitlines()))
                self.assertEqual(result["outcome"], (
                    "setting_rejected_before_application_io" if kind == "rejected" else "silent_out_confirmed"))
                self.assertEqual(trace.read_bytes(), original)

    def test_matched_zero_length_input_errors_cannot_be_called_silent(self):
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            original = trace.read_text()
            devnum = 12 if kind == "trial" else 18
            for transfer, endpoint in (("Bi", 2), ("Ii", 1)):
                for submitted, completed in ((-115, -71), (-115, 1), (-32, 0), (-32, -104)):
                    pair = (
                        f"ee 201 S {transfer}:1:{devnum:03d}:{endpoint} {submitted} 0 <\n"
                        f"ee 202 C {transfer}:1:{devnum:03d}:{endpoint} {completed} 0\n"
                    )
                    trace.write_text(original + pair)
                    with self.subTest(kind=kind, transfer=transfer, submitted=submitted, completed=completed), \
                            self.assertRaisesRegex(marvin_usbmon.UsbmonError, "USB transfer-status errors"):
                        assess()
                    self.assertEqual(trace.read_text(), original + pair)

    def test_matched_submission_error_events_prevent_all_three_continuation_paths(self):
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            devnum = 12 if kind == "trial" else 18
            with trace.open("a") as stream:
                stream.write(
                    f"ee 201 S Bi:1:{devnum:03d}:2 -115 0 <\n"
                    f"ee 202 E Bi:1:{devnum:03d}:2 -32 0\n"
                )
            with self.subTest(kind=kind), self.assertRaisesRegex(
                    marvin_usbmon.UsbmonError, "USB transfer-status errors"):
                assess()

    def test_success_and_known_zero_length_in_cancellations_keep_existing_outcomes(self):
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            original = trace.read_text()
            devnum = 12 if kind == "trial" else 18
            for transfer, endpoint in (("Bi", 2), ("Ii", 1)):
                for completed in (0, -2, -104, -108):
                    trace.write_text(
                        original + f"ee 201 S {transfer}:1:{devnum:03d}:{endpoint} -115 0 <\n"
                        f"ee 202 C {transfer}:1:{devnum:03d}:{endpoint} {completed} 0\n"
                    )
                    with self.subTest(kind=kind, transfer=transfer, completed=completed):
                        self.assertEqual(assess()["outcome"], (
                            "setting_rejected_before_application_io"
                            if kind == "rejected" else "silent_out_confirmed"))

    def test_leaf_and_parent_symlinks_are_rejected_before_opening_the_trace(self):
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            original = trace.read_bytes()
            target = trace.with_name("original.txt")
            trace.rename(target)
            trace.symlink_to(target.name)
            for link_kind in ("leaf", "parent"):
                with self.subTest(kind=kind, link=link_kind), \
                        patch.object(marvin_stream.os, "open", side_effect=AssertionError("linked trace opened")), \
                        self.assertRaisesRegex(marvin_usbmon.AnalysisError, "without symlinks"):
                    assess()
                if link_kind == "leaf":
                    trace.unlink()
                    target.rename(trace)
                    actual = trace.parent.with_name("actual-usb")
                    trace.parent.rename(actual)
                    trace.parent.symlink_to(actual.name, target_is_directory=True)
            self.assertEqual(trace.read_bytes(), original)

    def test_oversized_trace_is_rejected_without_opening_or_parsing(self):
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            with trace.open("ab") as stream:
                stream.truncate(marvin_usbmon.DEFAULT_MAX_BYTES + 1)
            with self.subTest(kind=kind), \
                    patch.object(marvin_stream.os, "open", side_effect=AssertionError("oversized trace opened")), \
                    patch.object(marvin_usbmon, "parse_record") as parse, \
                    self.assertRaisesRegex(marvin_usbmon.AnalysisError, "byte limit"):
                assess()
            parse.assert_not_called()

    def test_special_file_trace_is_rejected_before_open(self):
        lstat = Path.lstat
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)

            def inspect(path):
                return SimpleNamespace(st_mode=stat.S_IFIFO) if path == trace else lstat(path)

            with self.subTest(kind=kind), patch.object(Path, "lstat", autospec=True, side_effect=inspect), \
                    patch.object(marvin_stream.os, "open", side_effect=AssertionError("special file opened")), \
                    self.assertRaises(marvin_usbmon.AnalysisError):
                assess()

    def test_identity_change_and_in_read_mutation_are_rejected_before_parsing(self):
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            opened = trace.stat()
            replacement = SimpleNamespace(st_mode=opened.st_mode, st_dev=opened.st_dev,
                                          st_ino=opened.st_ino + 1)
            changed = SimpleNamespace(st_size=opened.st_size + 1, st_mtime_ns=opened.st_mtime_ns,
                                      st_ctime_ns=opened.st_ctime_ns)
            for observations in ([replacement], [opened, changed]):
                with self.subTest(kind=kind, observations=len(observations)), \
                        patch.object(marvin_stream.os, "fstat", side_effect=observations), \
                        patch.object(marvin_usbmon, "parse_record") as parse, \
                        self.assertRaises(marvin_usbmon.AnalysisError):
                    assess()
                parse.assert_not_called()

    def test_growth_after_open_remains_byte_bounded(self):
        fstat = marvin_stream.os.fstat
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            opened = trace.stat()
            observations = []

            def inspect(descriptor):
                observations.append(descriptor)
                if len(observations) == 1:
                    with trace.open("ab") as stream:
                        stream.truncate(marvin_usbmon.DEFAULT_MAX_BYTES + 1)
                    return opened
                return fstat(descriptor)

            with self.subTest(kind=kind), patch.object(marvin_stream.os, "fstat", side_effect=inspect), \
                    patch.object(marvin_usbmon, "parse_record") as parse, \
                    self.assertRaisesRegex(marvin_usbmon.AnalysisError, "byte limit"):
                assess()
            parse.assert_not_called()
            self.assertEqual(len(observations), 2)

    def test_record_limit_still_applies_before_assessment(self):
        for kind in ("campaign", "rejected", "trial"):
            trace, assess = self.fixture(kind)
            devnum = 12 if kind == "trial" else 18
            pair = f"a 1 S Bi:1:{devnum:03d}:2 -115 0 <\na 2 C Bi:1:{devnum:03d}:2 0 0\n"
            trace.write_text(pair * (marvin_usbmon.DEFAULT_MAX_RECORDS // 2 + 1))
            self.assertLess(trace.stat().st_size, marvin_usbmon.DEFAULT_MAX_BYTES)
            with self.subTest(kind=kind), self.assertRaisesRegex(marvin_usbmon.AnalysisError, "record limit"):
                assess()


if __name__ == "__main__":
    unittest.main()
