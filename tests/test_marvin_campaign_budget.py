"""Offline campaign clock models, not a guarantee of real-world completion time."""

from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import marvin_campaign as campaign
from tools import marvin_campaign_plan


ROOT = Path(__file__).resolve().parents[1]
BASELINE = {"usb": {"descriptors_sha256": campaign.DESCRIPTOR_HASH}}
WALL_LIMIT = 14400


class CampaignBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=".campaign-budget-test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.plan = marvin_campaign_plan.make_plan("full")
        self.compiled = [campaign.compile_segment(segment) for segment in self.plan["segments"]]
        self.plan_digest = hashlib.sha256(
            json.dumps(self.plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def execute(self, *, overhead_seconds=0, max_seconds=WALL_LIMIT):
        output = self.root / f"run-{len(list(self.root.iterdir()))}"
        clock = [1000.0]
        started = clock[0]
        calls = []

        def record(port, directory, **options):
            index = len(calls)
            segment = self.plan["segments"][index]
            schedule, seconds = self.compiled[index]
            self.assertEqual(port, "offline-fixture")
            self.assertEqual(directory, output / segment["id"])
            self.assertEqual(options["probe_schedule"], schedule)
            self.assertEqual(options["seconds"], seconds)
            for key in ("baudrate", "bytesize", "parity", "stopbits", "dtr", "rts"):
                self.assertEqual(options[key], segment[key])
            self.assertEqual(options["usb_tail_seconds"], 5)
            self.assertEqual(options["usb_close_grace_seconds"], 30)
            self.assertEqual(options["deadline"], started + max_seconds)
            self.assertEqual(options["probe_profile"], "experimental-successor")
            self.assertEqual(options["expected_usb_identity"], BASELINE["usb"])
            self.assertIs(options["actuators_isolated"], True)
            self.assertIs(options["allow_unknown_command"], True)
            self.assertIs(options["allow_telemetry_state_change"], True)
            self.assertIs(options["allow_line_state_trial"], True)
            calls.append({"id": segment["id"], "started": clock[0] - started})
            # Healthy captures use the nominal USB tail, not maximum close grace.
            # Additional host/capture overhead is elapsed time, never a plan debit.
            clock[0] += seconds + options["usb_tail_seconds"] + overhead_seconds
            return {"confirmed_bytes": sum(len(item.data) for item in schedule)}

        def assess(directory, result, schedule):
            self.assertEqual(result["confirmed_bytes"], sum(len(item.data) for item in schedule))
            return {"outcome": "silent_out_confirmed",
                    "usb_out_confirmed_bytes": result["confirmed_bytes"]}

        with ExitStack() as stack:
            stack.enter_context(patch.object(campaign.os, "geteuid", return_value=1000))
            stack.enter_context(patch.object(campaign.time, "monotonic", side_effect=lambda: clock[0]))
            preflight = stack.enter_context(patch.object(
                campaign.marvin_session, "preflight", return_value=BASELINE))
            identity = stack.enter_context(patch.object(campaign.marvin_session, "check_identity"))
            session = stack.enter_context(patch.object(campaign.marvin_session, "run_session", side_effect=record))
            stack.enter_context(patch.object(campaign, "assess_segment", side_effect=assess))
            # Evidence I/O and sealing are separate concerns; this model charges
            # their possible overhead explicitly rather than using test-host time.
            stack.enter_context(patch.object(campaign.marvin_session, "write_json"))
            stack.enter_context(patch.object(campaign.marvin_session, "seal_evidence"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = campaign.run_campaign(
                self.plan, output, port="offline-fixture", actuators_isolated=True,
                allow_unknown_command=True, allow_telemetry_state_change=True,
                allow_line_state_trials=True, switch_position="RUN", max_seconds=max_seconds,
            )
        preflight.assert_called_once_with("offline-fixture", deadline=started + max_seconds)
        self.assertEqual(identity.call_count, len(calls))
        self.assertEqual(session.call_count, len(calls))
        self.assertEqual([call["id"] for call in calls],
                         [segment["id"] for segment in self.plan["segments"][:len(calls)]])
        self.assertEqual(len({call["id"] for call in calls}), len(calls))
        self.assertTrue(all(segment["status"] == "completed" for segment in result["segments"]))
        self.assertEqual(result["plan_sha256"], self.plan_digest)
        self.assertEqual(hashlib.sha256(
            json.dumps(self.plan, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(), self.plan_digest)
        return result, clock[0] - started, calls

    def test_full_plan_finishes_at_nominal_cost_without_accumulating_per_segment_reserves(self):
        result, elapsed, calls = self.execute()
        self.assertEqual(len(calls), 724)
        self.assertEqual(result["status"], "completed_silent")
        self.assertEqual(result["max_wall_seconds"], WALL_LIMIT)
        self.assertEqual(result["application_bytes_confirmed"], 47651)
        self.assertAlmostEqual(result["planned"]["serial_seconds"], 8017.94, places=6)
        self.assertAlmostEqual(result["planned"]["usb_seconds"], 11637.94, places=6)
        self.assertAlmostEqual(elapsed, 11637.94, places=6)
        self.assertLess(elapsed, WALL_LIMIT)
        incorrectly_accumulated_reserves = math.fsum(
            seconds + 20 + campaign.USB_CLOSE_GRACE_SECONDS for _, seconds in self.compiled)
        self.assertAlmostEqual(incorrectly_accumulated_reserves, 44217.94, places=6)
        self.assertGreater(incorrectly_accumulated_reserves, WALL_LIMIT)

    def test_full_plan_still_fits_with_three_seconds_of_actual_overhead_per_segment(self):
        result, elapsed, calls = self.execute(overhead_seconds=3)
        self.assertEqual(result["status"], "completed_silent")
        self.assertEqual(len(calls), 724)
        self.assertAlmostEqual(elapsed, 11637.94 + 724 * 3, places=6)
        self.assertLess(elapsed, WALL_LIMIT)

    def test_actual_overhead_can_stop_the_full_plan_before_the_next_segment(self):
        result, elapsed, calls = self.execute(overhead_seconds=4)
        self.assertEqual(result["status"], "stopped_wall_limit")
        self.assertGreater(len(calls), 0)
        self.assertLess(len(calls), 724)
        self.assertEqual(result["planned"]["segments"], 724)
        self.assertEqual(len(result["segments"]), len(calls))
        completed_nominal = math.fsum(seconds + 5 for _, seconds in self.compiled[:len(calls)])
        self.assertAlmostEqual(elapsed, completed_nominal + len(calls) * 4, places=6)
        self.assertLess(elapsed, WALL_LIMIT)
        next_seconds = self.compiled[len(calls)][1]
        self.assertGreater(elapsed + next_seconds + 20 + campaign.USB_CLOSE_GRACE_SECONDS, WALL_LIMIT)
        for call, (_, seconds) in zip(calls, self.compiled):
            self.assertLessEqual(call["started"] + seconds + 20 + campaign.USB_CLOSE_GRACE_SECONDS,
                                 WALL_LIMIT)
        self.assertGreater(11637.94 + 724 * 4, WALL_LIMIT)

    def test_reservation_equal_to_remaining_budget_admits_only_that_next_segment(self):
        first_seconds = self.compiled[0][1]
        budget = first_seconds + 20 + campaign.USB_CLOSE_GRACE_SECONDS
        result, elapsed, calls = self.execute(max_seconds=budget)
        self.assertEqual(result["status"], "stopped_wall_limit")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["started"], 0)
        self.assertAlmostEqual(elapsed, first_seconds + 5, places=6)
        self.assertLess(elapsed, budget)


if __name__ == "__main__":
    unittest.main()
