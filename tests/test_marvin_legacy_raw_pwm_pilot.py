"""Offline fixed raw-PWM word-0/value-1 pilot tests."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch

from tests import test_marvin_session as session_tests
from tests.test_marvin_legacy_client import frame
from tools import marvin_legacy_raw_pwm_pilot as pilot
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon
from tools.marvin_legacy_client import Received


DECLARATIONS = dict.fromkeys(consent.RAW_PWM_PILOT_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.RAW_PWM_PILOT_FLAGS]
DECLARATIONS_1000 = dict.fromkeys(consent.RAW_PWM_1000_PILOT_FLAGS, True)
FLAGS_1000 = ["--" + name.replace("_", "-")
              for name in consent.RAW_PWM_1000_PILOT_FLAGS]
DECLARATIONS_2000 = dict.fromkeys(consent.RAW_PWM_2000_PILOT_FLAGS, True)
FLAGS_2000 = ["--" + name.replace("_", "-")
              for name in consent.RAW_PWM_2000_PILOT_FLAGS]
DECLARATIONS_WORD1_2000 = dict.fromkeys(
    consent.RAW_PWM_WORD1_2000_PILOT_FLAGS, True)
FLAGS_WORD1_2000 = ["--" + name.replace("_", "-")
                    for name in consent.RAW_PWM_WORD1_2000_PILOT_FLAGS]
DECLARATIONS_WORD2_2000 = dict.fromkeys(
    consent.RAW_PWM_WORD2_2000_PILOT_FLAGS, True)
FLAGS_WORD2_2000 = ["--" + name.replace("_", "-")
                    for name in consent.RAW_PWM_WORD2_2000_PILOT_FLAGS]
DECLARATIONS_WORD3_2000 = dict.fromkeys(
    consent.RAW_PWM_WORD3_2000_PILOT_FLAGS, True)
FLAGS_WORD3_2000 = ["--" + name.replace("_", "-")
                    for name in consent.RAW_PWM_WORD3_2000_PILOT_FLAGS]
DECLARATIONS_WORD2_2000_RIGHT_CONNECTED = dict.fromkeys(
    consent.RAW_PWM_WORD2_2000_RIGHT_CONNECTED_FLAGS, True)
FLAGS_WORD2_2000_RIGHT_CONNECTED = [
    "--" + name.replace("_", "-")
    for name in consent.RAW_PWM_WORD2_2000_RIGHT_CONNECTED_FLAGS]
DECLARATIONS_WORD3_2000_RIGHT_CONNECTED = dict.fromkeys(
    consent.RAW_PWM_WORD3_2000_RIGHT_CONNECTED_FLAGS, True)
FLAGS_WORD3_2000_RIGHT_CONNECTED = [
    "--" + name.replace("_", "-")
    for name in consent.RAW_PWM_WORD3_2000_RIGHT_CONNECTED_FLAGS]
DECLARATIONS_DUAL_FORWARD_CONNECTED = dict.fromkeys(
    consent.RAW_PWM_DUAL_FORWARD_CONNECTED_FLAGS, True)
FLAGS_DUAL_FORWARD_CONNECTED = [
    "--" + name.replace("_", "-")
    for name in consent.RAW_PWM_DUAL_FORWARD_CONNECTED_FLAGS]
DECLARATIONS_DUAL_REVERSE_CONNECTED = dict.fromkeys(
    consent.RAW_PWM_DUAL_REVERSE_CONNECTED_FLAGS, True)
DECLARATIONS_LEFT_REVERSE_RIGHT_FORWARD = dict.fromkeys(
    consent.RAW_PWM_LEFT_REVERSE_RIGHT_FORWARD_FLAGS, True)
DECLARATIONS_LEFT_FORWARD_RIGHT_BACKWARD = dict.fromkeys(
    consent.RAW_PWM_LEFT_FORWARD_RIGHT_BACKWARD_FLAGS, True)
DECLARATIONS_DUAL_FORWARD_ONE_SECOND = dict.fromkeys(
    consent.RAW_PWM_DUAL_FORWARD_ONE_SECOND_FLAGS, True)
DECLARATIONS_BOTH_CONNECTED_LEFT_FORWARD = dict.fromkeys(
    consent.RAW_PWM_BOTH_CONNECTED_LEFT_FORWARD_FLAGS, True)
DECLARATIONS_BOTH_CONNECTED_RIGHT_FORWARD = dict.fromkeys(
    consent.RAW_PWM_BOTH_CONNECTED_RIGHT_FORWARD_FLAGS, True)
DECLARATIONS_CONNECTED = dict.fromkeys(consent.RAW_PWM_LEFT_CONNECTED_FLAGS, True)
FLAGS_CONNECTED = ["--" + name.replace("_", "-")
                   for name in consent.RAW_PWM_LEFT_CONNECTED_FLAGS]
DECLARATIONS_2000_CONNECTED = dict.fromkeys(
    consent.RAW_PWM_2000_LEFT_CONNECTED_FLAGS, True)
FLAGS_2000_CONNECTED = ["--" + name.replace("_", "-")
                        for name in consent.RAW_PWM_2000_LEFT_CONNECTED_FLAGS]
DECLARATIONS_WORD1_2000_CONNECTED = dict.fromkeys(
    consent.RAW_PWM_WORD1_2000_LEFT_CONNECTED_FLAGS, True)
FLAGS_WORD1_2000_CONNECTED = ["--" + name.replace("_", "-")
                              for name in consent.RAW_PWM_WORD1_2000_LEFT_CONNECTED_FLAGS]


class _Transport:
    token = "token"
    serial_bytes = 0
    steps = pilot.STEPS
    success = pilot.SUCCESS
    observation_seconds = 3
    motor_connected = False

    def __init__(self, submit_fault=None):
        self.submit_fault = submit_fault
        self.attempts = []
        self.writes = 0
        self.completed = []
        self.may_have_applied = False
        self.cleanup_attempted = False
        self.cleanup_raw80 = False
        self.last_write_started = 0
        self.close = Mock()
        self.wait = Mock()

    def revalidate(self, *, deadline):
        return self.token

    def submit(self, step, *, deadline):
        self.attempts.append(step)
        if step == "set":
            self.may_have_applied = True
        if step == "cleanup":
            if self.cleanup_attempted:
                raise OSError("cleanup retry")
            self.cleanup_attempted = True
        self.writes += 1
        if self.submit_fault == (step, "error"):
            raise OSError(step + " uncertain")
        if self.submit_fault == (step, "partial"):
            return 1
        return len(pilot.STEPS[step])


class RawPwmPilotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            dir=Path.cwd(), prefix=".raw-pwm-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    @staticmethod
    def response(statuses=None, fault=None):
        statuses = statuses or {}

        def respond(transport, report, step, **_):
            if fault and step == fault[0]:
                raise fault[1]
            status = statuses.get(step, 0x80)
            report["responses"].append({
                "step": step,
                "sequence": pilot.decode_packet(transport.steps[step]).sequence,
                "raw_response_field": status,
            })
            return Mock(response_field=status)

        return respond

    def test_fixed_pilot_integration_cleanup_gates_and_lock(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "53010d0a0000004ccd45",
            "53020d0b0008000100000000000000a64f45",
            "53030d0b0008000000000000000000674245",
            "53040d0a0000004c9845",
        ])
        self.assertEqual(
            plan["transcript_sha256"],
            "6880718e5a54cf8a8225d3d2afc3cd4cc975f6c0bb6552a8de4161bf4a6b0df8")
        self.assertEqual(
            (plan["maximum_writes"], plan["maximum_application_bytes"],
             plan["minimum_application_bytes_after_setter"],
             plan["maximum_expected_response_bytes"]),
            (4, 56, 46, 56))
        self.assertEqual(pilot.decode_packet(pilot.STEPS["set"]).payload,
                         b"\x01\x00" + bytes(6))
        self.assertFalse(plan["automatic_retries"])
        self.assertFalse(plan["automatic_reconnect"])
        self.assertIsNone(plan["fixed_cadence"])
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS_1000), 0)
        plan_1000 = json.loads(stdout.getvalue())
        self.assertEqual(plan_1000["immutable_application_transcript_hex"], [
            "53050d0a0000004d4945",
            "53060d0b000800e8030000000000005ea945",
            "53070d0b0008000000000000000000628645",
            "53080d0a0000004c5445",
        ])
        self.assertEqual(
            plan_1000["transcript_sha256"],
            "f9fb5e2ae27dbbeb0c8c57b3d9bb1663ca2d34fa16f2c2ae7966e505310e012b")
        self.assertEqual(plan_1000["fixed_setter_words_uint16"], [1000, 0, 0, 0])
        self.assertEqual(
            pilot.transcript_for_scope(consent.RAW_PWM_1000_PILOT_SCOPE),
            pilot.TRANSCRIPT_1000)
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS_2000), 0)
        plan_2000 = json.loads(stdout.getvalue())
        self.assertEqual(plan_2000["immutable_application_transcript_hex"], [
            "53090d0a0000004d8545",
            "530a0d0b000800d00700000000000015d745",
            "530b0d0b00080000000000000000006e8a45",
            "530c0d0a0000004dd045",
        ])
        self.assertEqual(
            plan_2000["transcript_sha256"],
            "9a3a6fa3110df4769b62391cd289cebbd06bf801b04154117f4a989c1a78d51b")
        self.assertEqual(plan_2000["fixed_setter_words_uint16"], [2000, 0, 0, 0])
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS_CONNECTED), 0)
        connected_plan = json.loads(stdout.getvalue())
        self.assertEqual(connected_plan["immutable_application_transcript_hex"], [
            "530d0d0a0000004c0145",
            "530e0d0b000800e803000000000000576145",
            "530f0d0b00080000000000000000006b4e45",
            "53100d0a0000004f8c45",
        ])
        self.assertEqual(
            connected_plan["transcript_sha256"],
            "35c73a4732f7ee75e95f27c291018d7a038172a80c3a9bc9827aabd2d0cac575")
        self.assertEqual(connected_plan["observation_seconds"], .25)
        self.assertEqual(connected_plan["maximum_setter_to_cleanup_start_seconds"], .75)
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS_2000_CONNECTED), 0)
        connected_2000_plan = json.loads(stdout.getvalue())
        self.assertEqual(connected_2000_plan["immutable_application_transcript_hex"], [
            "53110d0a0000004e5d45",
            "53120d0b000800d0070000000000000dcf45",
            "53130d0b0008000000000000000000769245",
            "53140d0a0000004e0845",
        ])
        self.assertEqual(
            connected_2000_plan["transcript_sha256"],
            "00c4193c533cdcf24d94b280bd866255ceb6d94a9e3fb4e01eb59899749853a8")
        self.assertEqual(connected_2000_plan["fixed_setter_words_uint16"], [2000, 0, 0, 0])
        self.assertEqual(
            connected_2000_plan["status"],
            "retired_after_live_nonzero_post_cleanup_getter")
        self.assertFalse(connected_2000_plan["live_execution_authorized"])
        with redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(ValueError, "retired after reverse motion"):
            pilot.run_diagnostic(
                self.root / "retired", expected_physical_port="1-3", run=True,
                **DECLARATIONS_2000_CONNECTED)
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS_WORD1_2000), 0)
        word1_plan = json.loads(stdout.getvalue())
        self.assertEqual(word1_plan["immutable_application_transcript_hex"], [
            "53150d0a0000004fd945",
            "53160d0b0008000000d00700000000d5c745",
            "53170d0b0008000000000000000000735645",
            "53180d0a0000004ec445",
        ])
        self.assertEqual(
            word1_plan["transcript_sha256"],
            "41b8589d900c4e071e8c5510a70411d43561bf2059314537b63b7b7d20dc5d27")
        self.assertEqual(word1_plan["fixed_setter_words_uint16"], [0, 2000, 0, 0])
        self.assertEqual(word1_plan["observation_seconds"], 3)
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS_WORD1_2000_CONNECTED), 0)
        connected_word1_plan = json.loads(stdout.getvalue())
        self.assertEqual(connected_word1_plan["immutable_application_transcript_hex"], [
            "53190d0a0000004f1545",
            "531a0d0b0008000000d00700000000d9cb45",
            "531b0d0b00080000000000000000007f5a45",
            "531c0d0a0000004f4045",
        ])
        self.assertEqual(
            connected_word1_plan["transcript_sha256"],
            "2bdece99255756fed374341c9d8520eb7e0231c398aa5eb8f1e5da8dc8d58b95")
        self.assertEqual(
            connected_word1_plan["fixed_setter_words_uint16"], [0, 2000, 0, 0])
        self.assertEqual(connected_word1_plan["observation_seconds"], .25)
        for flags, frames, digest, words in (
            (FLAGS_WORD2_2000, [
                "531d0d0a0000004e9145",
                "531e0d0b00080000000000d0070000f35e45",
                "531f0d0b00080000000000000000007a9e45",
                "53200d0a0000004a7c45",
             ], "64bf02c70c5b94a5e2845e73468d72a3b441fa4cd0002f38012707a2b2bc8fca",
             [0, 0, 2000, 0]),
            (FLAGS_WORD3_2000, [
                "53210d0a0000004bad45",
                "53220d0b000800000000000000d0075a6145",
                "53230d0b000800000000000000000046a245",
                "53240d0a0000004bf845",
             ], "6ecfb0fac2ed75ae890517d913ba82b1e5a460d52826bf50661d0ec01304700d",
             [0, 0, 0, 2000]),
        ):
            with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                    patch.object(os, "open", side_effect=AssertionError("no open")), \
                    redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(pilot.main(flags), 0)
            motor_r_plan = json.loads(stdout.getvalue())
            self.assertEqual(motor_r_plan["immutable_application_transcript_hex"], frames)
            self.assertEqual(motor_r_plan["transcript_sha256"], digest)
            self.assertEqual(motor_r_plan["fixed_setter_words_uint16"], words)
            self.assertEqual(motor_r_plan["operator_selected_physical_plug_label"], "Motor R")
        for flags, frames, digest, words in (
            (FLAGS_WORD2_2000_RIGHT_CONNECTED, [
                "53250d0a0000004a2945",
                "53260d0b00080000000000d0070000caa645",
                "53270d0b0008000000000000000000436645",
                "53280d0a0000004b3445",
             ], "4bb4f48a37e8a08db303b7df19990108504ca4e5800d71c13ddd9fd189a2f064",
             [0, 0, 2000, 0]),
            (FLAGS_WORD3_2000_RIGHT_CONNECTED, [
                "53290d0a0000004ae545",
                "532a0d0b000800000000000000d00753a945",
                "532b0d0b00080000000000000000004f6a45",
                "532c0d0a0000004ab045",
             ], "45853e630a4d4f1fb292b77ffe68c0c0bef1258bfb757de80840f9210c106ccd",
             [0, 0, 0, 2000]),
        ):
            with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                    patch.object(os, "open", side_effect=AssertionError("no open")), \
                    redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(pilot.main(flags), 0)
            connected_right_plan = json.loads(stdout.getvalue())
            self.assertEqual(
                connected_right_plan["immutable_application_transcript_hex"], frames)
            self.assertEqual(connected_right_plan["transcript_sha256"], digest)
            self.assertEqual(connected_right_plan["fixed_setter_words_uint16"], words)
            self.assertEqual(connected_right_plan["observation_seconds"], .25)
            self.assertEqual(
                connected_right_plan["physical_load"],
                "right_motor_connected_to_robot_left_side_connector_printed_Motor_R")
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(FLAGS_DUAL_FORWARD_CONNECTED), 0)
        dual_plan = json.loads(stdout.getvalue())
        self.assertEqual(dual_plan["immutable_application_transcript_hex"], [
            "533b0d0a000000499745",
            "533c0d0b0008000000d0070000d007e22f45",
            "533d0d0b000800000000000000000058bc45",
            "533e0d0a00000049c245",
        ])
        self.assertEqual(
            dual_plan["transcript_sha256"],
            "efc78618ca4c6f208886a61628f2b38e475250c45abbb5d08dea12f5624b24d0")
        self.assertEqual(dual_plan["fixed_setter_words_uint16"], [0, 2000, 0, 2000])
        self.assertEqual(
            dual_plan["operator_selected_physical_plug_label"], "Motor L and Motor R")
        reverse_flags = [
            "--" + name.replace("_", "-")
            for name in consent.RAW_PWM_DUAL_REVERSE_CONNECTED_FLAGS]
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(reverse_flags), 0)
        reverse_plan = json.loads(stdout.getvalue())
        self.assertEqual(reverse_plan["immutable_application_transcript_hex"], [
            "53470d0a000000426b45",
            "53480d0b000800d0070000d0070000de9445",
            "53490d0b00080000000000000000002cc845",
            "534a0d0a000000437645",
        ])
        self.assertEqual(
            reverse_plan["transcript_sha256"],
            "01062cff9b64d2334d9b7fb023db002723444922eb4f291d7ddf5dcc569de9bd")
        self.assertEqual(reverse_plan["fixed_setter_words_uint16"], [2000, 0, 2000, 0])
        for declarations, frames, digest, words in (
            (DECLARATIONS_LEFT_REVERSE_RIGHT_FORWARD, [
                "534b0d0a00000042a745",
                "534c0d0b000800d00700000000d0074e5345",
                "534d0d0b0008000000000000000000290c45",
                "534e0d0a00000042f245",
             ], "e1b7c5fbe491c37b8c2b78dd352d6d96885049c61f9d3e3f46e242d139b475f2",
             [2000, 0, 0, 2000]),
            (DECLARATIONS_LEFT_FORWARD_RIGHT_BACKWARD, [
                "534f0d0a000000432345",
                "53500d0b0008000000d007d00700001b4045",
                "53510d0b000800000000000000000034d045",
                "53520d0a00000040ae45",
             ], "1673977cfce05b88411e568bd3fd1a5cf029a419c24f74b7b5da6622a2b3ea36",
             [0, 2000, 2000, 0]),
        ):
            flags = ["--" + name.replace("_", "-") for name in declarations]
            with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                    patch.object(os, "open", side_effect=AssertionError("no open")), \
                    redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(pilot.main(flags), 0)
            direction_plan = json.loads(stdout.getvalue())
            self.assertEqual(direction_plan["immutable_application_transcript_hex"], frames)
            self.assertEqual(direction_plan["transcript_sha256"], digest)
            self.assertEqual(direction_plan["fixed_setter_words_uint16"], words)
        sustained_flags = [
            "--" + name.replace("_", "-")
            for name in consent.RAW_PWM_DUAL_FORWARD_ONE_SECOND_FLAGS]
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(pilot.main(sustained_flags), 0)
        sustained_plan = json.loads(stdout.getvalue())
        self.assertEqual(sustained_plan["immutable_application_transcript_hex"], [
            "53530d0a000000417f45",
            "53540d0b0008000000d0070000d0078b8745",
            "53550d0b0008000000000000000000311445",
            "53560d0a000000412a45",
        ])
        self.assertEqual(
            sustained_plan["transcript_sha256"],
            "e40ce7c77ede5ea1c3d9784d419928d12e6838ef94b9c01f87fdac4fb69305a7")
        self.assertEqual(sustained_plan["observation_seconds"], 1.0)
        self.assertEqual(sustained_plan["maximum_setter_to_cleanup_start_seconds"], 1.5)
        self.assertFalse(sustained_plan["ground_drive_authorized"])
        self.assertEqual(sustained_plan["operating_surface"], "on_blocks_only")
        for declarations, frames, digest, words in (
            (DECLARATIONS_BOTH_CONNECTED_LEFT_FORWARD, [
                "533f0d0a000000481345",
                "53400d0b0008000000d00700000000839145",
                "53410d0b0008000000000000000000250045",
                "53420d0a000000423e45",
             ], "039eb52792de9be997c1ec92ae5d236d5a9c6f39ad7b39f24584c748bac0a846",
             [0, 2000, 0, 0]),
            (DECLARATIONS_BOTH_CONNECTED_RIGHT_FORWARD, [
                "53430d0a00000043ef45",
                "53440d0b000800000000000000d0073c0745",
                "53450d0b000800000000000000000020c445",
                "53460d0a00000043ba45",
             ], "1bd1d7eaf79f367f049a4852e0901df44265d41a3515fdf474e67cd32cdd8501",
             [0, 0, 0, 2000]),
        ):
            flags = ["--" + name.replace("_", "-") for name in declarations]
            with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                    patch.object(os, "open", side_effect=AssertionError("no open")), \
                    redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(pilot.main(flags), 0)
            single_plan = json.loads(stdout.getvalue())
            self.assertEqual(single_plan["immutable_application_transcript_hex"], frames)
            self.assertEqual(single_plan["transcript_sha256"], digest)
            self.assertEqual(single_plan["fixed_setter_words_uint16"], words)
        for knob in ("index", "value", "payload", "sequence", "retry", "count", "cadence"):
            with self.subTest(knob=knob), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                pilot.main(FLAGS + ["--" + knob, "1"])
        for name in consent.RAW_PWM_PILOT_FLAGS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS | {name: False}))
        for name in consent.RAW_PWM_1000_PILOT_FLAGS:
            with self.subTest(value_1000_name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS_1000 | {name: False}))
        for name in consent.RAW_PWM_2000_PILOT_FLAGS:
            with self.subTest(value_2000_name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS_2000 | {name: False}))
        for name in consent.RAW_PWM_WORD1_2000_PILOT_FLAGS:
            with self.subTest(word1_2000_name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS_WORD1_2000 | {name: False}))
        for declarations in (DECLARATIONS_WORD2_2000, DECLARATIONS_WORD3_2000):
            for name in declarations:
                with self.subTest(motor_r_name=name), self.assertRaises(ValueError):
                    pilot.run_diagnostic(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(declarations | {name: False}))
        for declarations in (
                DECLARATIONS_WORD2_2000_RIGHT_CONNECTED,
                DECLARATIONS_WORD3_2000_RIGHT_CONNECTED):
            for name in declarations:
                with self.subTest(connected_right_name=name), self.assertRaises(ValueError):
                    pilot.run_diagnostic(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(declarations | {name: False}))
        for declarations in (
                DECLARATIONS_DUAL_FORWARD_CONNECTED,
                DECLARATIONS_DUAL_REVERSE_CONNECTED,
                DECLARATIONS_LEFT_REVERSE_RIGHT_FORWARD,
                DECLARATIONS_LEFT_FORWARD_RIGHT_BACKWARD,
                DECLARATIONS_DUAL_FORWARD_ONE_SECOND):
            for name in declarations:
                with self.subTest(dual_name=name), self.assertRaises(ValueError):
                    pilot.run_diagnostic(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(declarations | {name: False}))
        for declarations in (
                DECLARATIONS_BOTH_CONNECTED_LEFT_FORWARD,
                DECLARATIONS_BOTH_CONNECTED_RIGHT_FORWARD):
            for name in declarations:
                with self.subTest(single_name=name), self.assertRaises(ValueError):
                    pilot.run_diagnostic(
                        self.root / "unused", expected_physical_port="1-3", run=True,
                        **(declarations | {name: False}))
        for name in consent.RAW_PWM_LEFT_CONNECTED_FLAGS:
            with self.subTest(connected_name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS_CONNECTED | {name: False}))
        for name in consent.RAW_PWM_2000_LEFT_CONNECTED_FLAGS:
            with self.subTest(connected_2000_name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS_2000_CONNECTED | {name: False}))
        for name in consent.RAW_PWM_WORD1_2000_LEFT_CONNECTED_FLAGS:
            with self.subTest(connected_word1_name=name), self.assertRaises(ValueError):
                pilot.run_diagnostic(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    **(DECLARATIONS_WORD1_2000_CONNECTED | {name: False}))

        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.RAW_PWM_PILOT_SCOPE)
        self.assertEqual(result["probe_name"], "DisconnectedLoadRawPwmWord0OnePilot")
        self.assertTrue(result["fixed_raw_pwm_word0_one_pilot_authorized"])
        self.assertEqual(result["requested_application_bytes"], 56)
        self.assertEqual(result["immutable_application_transcript_hex"],
                         [raw.hex() for raw in pilot.TRANSCRIPT])
        for flag in FLAGS:
            self.assertIn(flag, command)
        pilot._validate_capture(runner.call_args.kwargs)
        for declarations, flags, scope, probe, authorization in (
            (DECLARATIONS_WORD2_2000_RIGHT_CONNECTED,
             FLAGS_WORD2_2000_RIGHT_CONNECTED,
             consent.RAW_PWM_WORD2_2000_RIGHT_CONNECTED_SCOPE,
             "RawPwmWord2Value2000RightMotorConnectedProof",
             "fixed_raw_pwm_word2_2000_right_motor_connected_proof_authorized"),
            (DECLARATIONS_WORD3_2000_RIGHT_CONNECTED,
             FLAGS_WORD3_2000_RIGHT_CONNECTED,
             consent.RAW_PWM_WORD3_2000_RIGHT_CONNECTED_SCOPE,
             "RawPwmWord3Value2000RightMotorConnectedProof",
             "fixed_raw_pwm_word3_2000_right_motor_connected_proof_authorized"),
        ):
            harness = session_tests.SessionTests()
            harness.setUp()
            self.addCleanup(harness.doCleanups)
            runner = Mock(side_effect=harness.capture)
            result = harness.run_capture(
                seconds=10, baudrate=57600, allow_unknown_command=True,
                probe_profile="legacy", capture_runner=runner,
                binary_payload_limit=4096, usb_tail_seconds=5,
                usb_close_grace_seconds=5, actuators_isolated=False,
                **declarations)
            command = harness.popen.call_args.args[0]
            self.assertEqual(result["scope"], scope)
            self.assertEqual(result["probe_name"], probe)
            self.assertTrue(result[authorization])
            for flag in flags:
                self.assertIn(flag, command)
            pilot._validate_capture(runner.call_args.kwargs)
            with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(usbmon.main(command[2:]), 0)
            pilot._validate_capture(capture.call_args.kwargs)
            sustained_harness = session_tests.SessionTests()
            sustained_harness.setUp()
            self.addCleanup(sustained_harness.doCleanups)
            sustained_runner = Mock(side_effect=sustained_harness.capture)
            sustained_result = sustained_harness.run_capture(
                seconds=10, baudrate=57600, allow_unknown_command=True,
                probe_profile="legacy", capture_runner=sustained_runner,
                binary_payload_limit=4096, usb_tail_seconds=5,
                usb_close_grace_seconds=5, actuators_isolated=False,
                **DECLARATIONS_DUAL_FORWARD_ONE_SECOND)
            sustained_command = sustained_harness.popen.call_args.args[0]
            self.assertEqual(
                sustained_result["scope"], consent.RAW_PWM_DUAL_FORWARD_ONE_SECOND_SCOPE)
            self.assertEqual(
                sustained_result["probe_name"],
                "RawPwmDualMotorForwardValue2000OneSecondOnBlocksProof")
            self.assertTrue(
                sustained_result[
                    "fixed_raw_pwm_dual_motor_forward_2000_one_second_on_blocks_proof_authorized"])
            pilot._validate_capture(sustained_runner.call_args.kwargs)
            with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(usbmon.main(sustained_command[2:]), 0)
            pilot._validate_capture(capture.call_args.kwargs)
            for declarations, scope, probe, authorization in (
                (DECLARATIONS_LEFT_REVERSE_RIGHT_FORWARD,
                 consent.RAW_PWM_LEFT_REVERSE_RIGHT_FORWARD_SCOPE,
                 "RawPwmLeftReverseRightForwardValue2000ConnectedProof",
                 "fixed_raw_pwm_left_reverse_right_forward_2000_connected_proof_authorized"),
                (DECLARATIONS_LEFT_FORWARD_RIGHT_BACKWARD,
                 consent.RAW_PWM_LEFT_FORWARD_RIGHT_BACKWARD_SCOPE,
                 "RawPwmLeftForwardRightBackwardValue2000ConnectedProof",
                 "fixed_raw_pwm_left_forward_right_backward_2000_connected_proof_authorized"),
            ):
                harness = session_tests.SessionTests()
                harness.setUp()
                self.addCleanup(harness.doCleanups)
                runner = Mock(side_effect=harness.capture)
                result = harness.run_capture(
                    seconds=10, baudrate=57600, allow_unknown_command=True,
                    probe_profile="legacy", capture_runner=runner,
                    binary_payload_limit=4096, usb_tail_seconds=5,
                    usb_close_grace_seconds=5, actuators_isolated=False,
                    **declarations)
                command = harness.popen.call_args.args[0]
                self.assertEqual(result["scope"], scope)
                self.assertEqual(result["probe_name"], probe)
                self.assertTrue(result[authorization])
                pilot._validate_capture(runner.call_args.kwargs)
                with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(usbmon.main(command[2:]), 0)
                pilot._validate_capture(capture.call_args.kwargs)
        dual_harness = session_tests.SessionTests()
        dual_harness.setUp()
        self.addCleanup(dual_harness.doCleanups)
        dual_runner = Mock(side_effect=dual_harness.capture)
        dual_result = dual_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=dual_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **DECLARATIONS_DUAL_FORWARD_CONNECTED)
        dual_command = dual_harness.popen.call_args.args[0]
        self.assertEqual(
            dual_result["scope"], consent.RAW_PWM_DUAL_FORWARD_CONNECTED_SCOPE)
        self.assertEqual(
            dual_result["probe_name"], "RawPwmDualMotorForwardValue2000ConnectedProof")
        self.assertTrue(
            dual_result["fixed_raw_pwm_dual_motor_forward_2000_connected_proof_authorized"])
        for flag in FLAGS_DUAL_FORWARD_CONNECTED:
            self.assertIn(flag, dual_command)
        pilot._validate_capture(dual_runner.call_args.kwargs)
        reverse_harness = session_tests.SessionTests()
        reverse_harness.setUp()
        self.addCleanup(reverse_harness.doCleanups)
        reverse_runner = Mock(side_effect=reverse_harness.capture)
        reverse_result = reverse_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=reverse_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **DECLARATIONS_DUAL_REVERSE_CONNECTED)
        reverse_command = reverse_harness.popen.call_args.args[0]
        self.assertEqual(
            reverse_result["scope"], consent.RAW_PWM_DUAL_REVERSE_CONNECTED_SCOPE)
        self.assertEqual(
            reverse_result["probe_name"], "RawPwmDualMotorReverseValue2000ConnectedProof")
        self.assertTrue(
            reverse_result["fixed_raw_pwm_dual_motor_reverse_2000_connected_proof_authorized"])
        pilot._validate_capture(reverse_runner.call_args.kwargs)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(reverse_command[2:]), 0)
        pilot._validate_capture(capture.call_args.kwargs)
        for declarations, scope, probe, authorization in (
            (DECLARATIONS_BOTH_CONNECTED_LEFT_FORWARD,
             consent.RAW_PWM_BOTH_CONNECTED_LEFT_FORWARD_SCOPE,
             "RawPwmBothConnectedLeftForwardValue2000Proof",
             "fixed_raw_pwm_both_connected_left_forward_2000_proof_authorized"),
            (DECLARATIONS_BOTH_CONNECTED_RIGHT_FORWARD,
             consent.RAW_PWM_BOTH_CONNECTED_RIGHT_FORWARD_SCOPE,
             "RawPwmBothConnectedRightForwardValue2000Proof",
             "fixed_raw_pwm_both_connected_right_forward_2000_proof_authorized"),
        ):
            harness = session_tests.SessionTests()
            harness.setUp()
            self.addCleanup(harness.doCleanups)
            runner = Mock(side_effect=harness.capture)
            result = harness.run_capture(
                seconds=10, baudrate=57600, allow_unknown_command=True,
                probe_profile="legacy", capture_runner=runner,
                binary_payload_limit=4096, usb_tail_seconds=5,
                usb_close_grace_seconds=5, actuators_isolated=False,
                **declarations)
            self.assertEqual(result["scope"], scope)
            self.assertEqual(result["probe_name"], probe)
            self.assertTrue(result[authorization])
            pilot._validate_capture(runner.call_args.kwargs)

        harness_1000 = session_tests.SessionTests()
        harness_1000.setUp()
        self.addCleanup(harness_1000.doCleanups)
        runner_1000 = Mock(side_effect=harness_1000.capture)
        result_1000 = harness_1000.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=runner_1000,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **DECLARATIONS_1000)
        command_1000 = harness_1000.popen.call_args.args[0]
        self.assertEqual(result_1000["scope"], consent.RAW_PWM_1000_PILOT_SCOPE)
        self.assertEqual(
            result_1000["probe_name"], "DisconnectedLoadRawPwmWord0Value1000Pilot")
        self.assertTrue(result_1000["fixed_raw_pwm_word0_1000_pilot_authorized"])
        self.assertEqual(
            result_1000["immutable_application_transcript_hex"],
            [raw.hex() for raw in pilot.TRANSCRIPT_1000])
        for flag in FLAGS_1000:
            self.assertIn(flag, command_1000)
        pilot._validate_capture(runner_1000.call_args.kwargs)

        harness_2000 = session_tests.SessionTests()
        harness_2000.setUp()
        self.addCleanup(harness_2000.doCleanups)
        runner_2000 = Mock(side_effect=harness_2000.capture)
        result_2000 = harness_2000.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=runner_2000,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **DECLARATIONS_2000)
        command_2000 = harness_2000.popen.call_args.args[0]
        self.assertEqual(result_2000["scope"], consent.RAW_PWM_2000_PILOT_SCOPE)
        self.assertEqual(
            result_2000["probe_name"], "DisconnectedLoadRawPwmWord0Value2000Pilot")
        self.assertTrue(result_2000["fixed_raw_pwm_word0_2000_pilot_authorized"])
        self.assertEqual(
            result_2000["immutable_application_transcript_hex"],
            [raw.hex() for raw in pilot.TRANSCRIPT_2000])
        for flag in FLAGS_2000:
            self.assertIn(flag, command_2000)
        pilot._validate_capture(runner_2000.call_args.kwargs)

        word1_harness = session_tests.SessionTests()
        word1_harness.setUp()
        self.addCleanup(word1_harness.doCleanups)
        word1_runner = Mock(side_effect=word1_harness.capture)
        word1_result = word1_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=word1_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **DECLARATIONS_WORD1_2000)
        word1_command = word1_harness.popen.call_args.args[0]
        self.assertEqual(
            word1_result["scope"], consent.RAW_PWM_WORD1_2000_PILOT_SCOPE)
        self.assertEqual(
            word1_result["probe_name"], "DisconnectedLoadRawPwmWord1Value2000Pilot")
        self.assertTrue(word1_result["fixed_raw_pwm_word1_2000_pilot_authorized"])
        for flag in FLAGS_WORD1_2000:
            self.assertIn(flag, word1_command)
        pilot._validate_capture(word1_runner.call_args.kwargs)

        for declarations, flags, scope, probe, authorization in (
            (DECLARATIONS_WORD2_2000, FLAGS_WORD2_2000,
             consent.RAW_PWM_WORD2_2000_PILOT_SCOPE,
             "DisconnectedLoadRawPwmWord2Value2000Pilot",
             "fixed_raw_pwm_word2_2000_pilot_authorized"),
            (DECLARATIONS_WORD3_2000, FLAGS_WORD3_2000,
             consent.RAW_PWM_WORD3_2000_PILOT_SCOPE,
             "DisconnectedLoadRawPwmWord3Value2000Pilot",
             "fixed_raw_pwm_word3_2000_pilot_authorized"),
        ):
            harness = session_tests.SessionTests()
            harness.setUp()
            self.addCleanup(harness.doCleanups)
            runner = Mock(side_effect=harness.capture)
            result = harness.run_capture(
                seconds=10, baudrate=57600, allow_unknown_command=True,
                probe_profile="legacy", capture_runner=runner,
                binary_payload_limit=4096, usb_tail_seconds=5,
                usb_close_grace_seconds=5, actuators_isolated=False,
                **declarations)
            command = harness.popen.call_args.args[0]
            self.assertEqual(result["scope"], scope)
            self.assertEqual(result["probe_name"], probe)
            self.assertTrue(result[authorization])
            for flag in flags:
                self.assertIn(flag, command)
            pilot._validate_capture(runner.call_args.kwargs)

        connected_harness = session_tests.SessionTests()
        connected_harness.setUp()
        self.addCleanup(connected_harness.doCleanups)
        connected_runner = Mock(side_effect=connected_harness.capture)
        connected_result = connected_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=connected_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **DECLARATIONS_CONNECTED)
        connected_command = connected_harness.popen.call_args.args[0]
        self.assertEqual(connected_result["scope"], consent.RAW_PWM_LEFT_CONNECTED_SCOPE)
        self.assertEqual(
            connected_result["probe_name"], "RawPwmWord0Value1000LeftMotorConnectedProof")
        self.assertTrue(
            connected_result["fixed_raw_pwm_left_motor_connected_proof_authorized"])
        for flag in FLAGS_CONNECTED:
            self.assertIn(flag, connected_command)
        pilot._validate_capture(connected_runner.call_args.kwargs)

        connected_2000_harness = session_tests.SessionTests()
        connected_2000_harness.setUp()
        self.addCleanup(connected_2000_harness.doCleanups)
        with redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(ValueError, "retired after reverse motion"):
            connected_2000_harness.run_capture(
                seconds=10, baudrate=57600, allow_unknown_command=True,
                probe_profile="legacy", capture_runner=Mock(),
                binary_payload_limit=4096, usb_tail_seconds=5,
                usb_close_grace_seconds=5, actuators_isolated=False,
                **DECLARATIONS_2000_CONNECTED)
        connected_2000_harness.preflight.assert_not_called()

        connected_word1_harness = session_tests.SessionTests()
        connected_word1_harness.setUp()
        self.addCleanup(connected_word1_harness.doCleanups)
        connected_word1_runner = Mock(side_effect=connected_word1_harness.capture)
        connected_word1_result = connected_word1_harness.run_capture(
            seconds=10, baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", capture_runner=connected_word1_runner,
            binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False,
            **DECLARATIONS_WORD1_2000_CONNECTED)
        connected_word1_command = connected_word1_harness.popen.call_args.args[0]
        self.assertEqual(
            connected_word1_result["scope"],
            consent.RAW_PWM_WORD1_2000_LEFT_CONNECTED_SCOPE)
        self.assertEqual(
            connected_word1_result["probe_name"],
            "RawPwmWord1Value2000LeftMotorConnectedProof")
        self.assertTrue(
            connected_word1_result[
                "fixed_raw_pwm_word1_2000_left_motor_connected_proof_authorized"])
        for flag in FLAGS_WORD1_2000_CONNECTED:
            self.assertIn(flag, connected_word1_command)
        pilot._validate_capture(connected_word1_runner.call_args.kwargs)

        transport = _Transport()
        report = {}
        with patch.object(pilot, "_response",
                          side_effect=self.response({"cleanup": 0x82})), \
                redirect_stderr(io.StringIO()):
            pilot._observe(transport, report, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline", "set", "cleanup"])
        self.assertEqual(report["restoration"],
                         "zero_cleanup_attempted_but_application_unverified")
        self.assertEqual(report["subsequent_live_phase_gate"],
                         "physical_output_baseline_confirmation_and_power_cycle_acknowledgment_required")
        self.assertEqual(report["status"], pilot.SUCCESS)
        self.assertEqual((report["accepted_tx_bytes"], report["uncertain_tx_bytes"]),
                         (46, 0))
        transport.wait.assert_called_once_with(3)
        transport.close.assert_called_once()

        transport = _Transport()
        report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(transport, report, clock=lambda: 0)
        self.assertEqual(transport.attempts, ["baseline", "set", "cleanup", "verify"])
        self.assertTrue(report["getter_reverified"])
        self.assertEqual(report["restoration"], "getter_zero_baseline_reverified")
        self.assertEqual(report["accepted_tx_bytes"], 56)

        transport_1000 = _Transport()
        transport_1000.steps = pilot.STEPS_1000
        transport_1000.success = pilot.SUCCESS_1000
        report_1000 = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(transport_1000, report_1000, clock=lambda: 0)
        self.assertEqual(
            [row["sequence"] for row in report_1000["responses"]],
            [3333, 3334, 3335, 3336])
        self.assertEqual(report_1000["status"], pilot.SUCCESS_1000)
        self.assertEqual(transport_1000.attempts,
                         ["baseline", "set", "cleanup", "verify"])

        transport_2000 = _Transport()
        transport_2000.steps = pilot.STEPS_2000
        transport_2000.success = pilot.SUCCESS_2000
        report_2000 = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(transport_2000, report_2000, clock=lambda: 0)
        self.assertEqual(
            [row["sequence"] for row in report_2000["responses"]],
            [3337, 3338, 3339, 3340])
        self.assertEqual(report_2000["status"], pilot.SUCCESS_2000)

        word1_transport = _Transport()
        word1_transport.steps = pilot.STEPS_WORD1_2000
        word1_transport.success = pilot.SUCCESS_WORD1_2000
        word1_report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(word1_transport, word1_report, clock=lambda: 0)
        self.assertEqual(
            [row["sequence"] for row in word1_report["responses"]],
            [3349, 3350, 3351, 3352])
        word1_transport.wait.assert_called_once_with(3)
        self.assertEqual(word1_report["status"], pilot.SUCCESS_WORD1_2000)

        for steps, success, sequences in (
            (pilot.STEPS_WORD2_2000, pilot.SUCCESS_WORD2_2000,
             [3357, 3358, 3359, 3360]),
            (pilot.STEPS_WORD3_2000, pilot.SUCCESS_WORD3_2000,
             [3361, 3362, 3363, 3364]),
        ):
            motor_r_transport = _Transport()
            motor_r_transport.steps = steps
            motor_r_transport.success = success
            motor_r_transport.physical_plug_label = "Motor R"
            motor_r_report = {}
            with patch.object(pilot, "_response", side_effect=self.response()), \
                    redirect_stderr(io.StringIO()):
                pilot._observe(motor_r_transport, motor_r_report, clock=lambda: 0)
            self.assertEqual(
                [row["sequence"] for row in motor_r_report["responses"]], sequences)
            motor_r_transport.wait.assert_called_once_with(3)
            self.assertEqual(motor_r_report["status"], success)
        for steps, success, sequences in (
            (pilot.STEPS_WORD2_2000_RIGHT_CONNECTED,
             pilot.SUCCESS_WORD2_2000_RIGHT_CONNECTED,
             [3365, 3366, 3367, 3368]),
            (pilot.STEPS_WORD3_2000_RIGHT_CONNECTED,
             pilot.SUCCESS_WORD3_2000_RIGHT_CONNECTED,
             [3369, 3370, 3371, 3372]),
        ):
            connected_right_transport = _Transport()
            connected_right_transport.steps = steps
            connected_right_transport.success = success
            connected_right_transport.motor_connected = True
            connected_right_transport.physical_plug_label = "Motor R"
            connected_right_transport.physical_motor_label = "RIGHT"
            connected_right_transport.observation_seconds = .25
            connected_right_report = {}
            with patch.object(pilot, "_response", side_effect=self.response()), \
                    redirect_stderr(io.StringIO()) as stderr:
                pilot._observe(
                    connected_right_transport, connected_right_report, clock=lambda: 0)
            self.assertIn("OBSERVE_RIGHT_MOTOR_NOW", stderr.getvalue())
            self.assertNotIn("OBSERVE_LEFT_MOTOR_NOW", stderr.getvalue())
            self.assertEqual(
                [row["sequence"] for row in connected_right_report["responses"]],
                sequences)
            connected_right_transport.wait.assert_called_once_with(.25)
            self.assertEqual(connected_right_report["status"], success)
        dual_transport = _Transport()
        dual_transport.steps = pilot.STEPS_DUAL_FORWARD_2000_CONNECTED
        dual_transport.success = pilot.SUCCESS_DUAL_FORWARD_2000_CONNECTED
        dual_transport.motor_connected = True
        dual_transport.physical_motor_label = "BOTH"
        dual_transport.observation_seconds = .25
        dual_report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()) as stderr:
            pilot._observe(dual_transport, dual_report, clock=lambda: 0)
        self.assertIn("OBSERVE_BOTH_MOTOR_NOW", stderr.getvalue())
        self.assertEqual(
            [row["sequence"] for row in dual_report["responses"]],
            [3387, 3388, 3389, 3390])
        self.assertEqual(dual_report["status"], pilot.SUCCESS_DUAL_FORWARD_2000_CONNECTED)
        sustained_transport = _Transport()
        sustained_transport.steps = pilot.STEPS_DUAL_FORWARD_2000_ONE_SECOND
        sustained_transport.success = pilot.SUCCESS_DUAL_FORWARD_2000_ONE_SECOND
        sustained_transport.motor_connected = True
        sustained_transport.physical_motor_label = "BOTH"
        sustained_transport.observation_seconds = 1.0
        sustained_transport.absolute_cleanup_bound_seconds = 1.5
        sustained_transport.last_write_started = 0
        sustained_report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                patch.object(pilot, "_wait_until") as wait_until, \
                redirect_stderr(io.StringIO()):
            pilot._observe(sustained_transport, sustained_report, clock=lambda: 0)
        wait_until.assert_called_once_with(
            sustained_transport, 1.0, 1.5, clock=ANY)
        self.assertEqual(
            [row["sequence"] for row in sustained_report["responses"]],
            [3411, 3412, 3413, 3414])
        self.assertEqual(
            sustained_report["status"], pilot.SUCCESS_DUAL_FORWARD_2000_ONE_SECOND)
        self.assertEqual(sustained_report["setter_to_cleanup_start_seconds"], 0)
        with self.assertRaisesRegex(OSError, "cannot fit"):
            pilot._wait_until(Mock(), 1.6, 1.5, clock=lambda: 0)
        interrupted_transport = _Transport()
        interrupted_transport.steps = pilot.STEPS_DUAL_FORWARD_2000_ONE_SECOND
        interrupted_transport.success = pilot.SUCCESS_DUAL_FORWARD_2000_ONE_SECOND
        interrupted_transport.motor_connected = True
        interrupted_transport.observation_seconds = 1.0
        interrupted_transport.absolute_cleanup_bound_seconds = 1.5
        interrupted_transport.last_write_started = 0
        with patch.object(pilot, "_response", side_effect=self.response()), \
                patch.object(pilot, "_wait_until", side_effect=InterruptedError("stop")), \
                redirect_stderr(io.StringIO()), \
                self.assertRaises(InterruptedError):
            pilot._observe(interrupted_transport, {}, clock=lambda: 0)
        self.assertEqual(
            interrupted_transport.attempts, ["baseline", "set", "cleanup", "verify"])
        self.assertTrue(interrupted_transport.cleanup_attempted)
        late_transport = _Transport()
        late_transport.steps = pilot.STEPS_DUAL_FORWARD_2000_ONE_SECOND
        late_transport.success = pilot.SUCCESS_DUAL_FORWARD_2000_ONE_SECOND
        late_transport.motor_connected = True
        late_transport.observation_seconds = 1.0
        late_transport.absolute_cleanup_bound_seconds = 1.5
        original_submit = late_transport.submit

        def submit_late(step, *, deadline):
            count = original_submit(step, deadline=deadline)
            if step == "cleanup":
                late_transport.last_write_started = 1.6
            return count

        late_transport.submit = submit_late
        late_report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                patch.object(pilot, "_wait_until"), \
                redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(OSError, "after the absolute"):
            pilot._observe(late_transport, late_report, clock=lambda: 0)
        self.assertEqual(late_report["setter_to_cleanup_start_seconds"], 1.6)
        self.assertEqual(
            late_transport.attempts, ["baseline", "set", "cleanup", "verify"])

        connected_word1_transport = _Transport()
        connected_word1_transport.steps = pilot.STEPS_WORD1_2000_LEFT_CONNECTED
        connected_word1_transport.success = pilot.SUCCESS_WORD1_2000_LEFT_CONNECTED
        connected_word1_transport.observation_seconds = .25
        connected_word1_transport.motor_connected = True
        connected_word1_report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(
                connected_word1_transport, connected_word1_report, clock=lambda: 0)
        self.assertEqual(
            [row["sequence"] for row in connected_word1_report["responses"]],
            [3353, 3354, 3355, 3356])
        connected_word1_transport.wait.assert_called_once_with(.25)
        self.assertEqual(
            connected_word1_report["status"], pilot.SUCCESS_WORD1_2000_LEFT_CONNECTED)

        connected_transport = _Transport()
        connected_transport.steps = pilot.STEPS_LEFT_CONNECTED
        connected_transport.success = pilot.SUCCESS_LEFT_CONNECTED
        connected_transport.observation_seconds = .25
        connected_transport.motor_connected = True
        connected_report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(connected_transport, connected_report, clock=lambda: 0)
        self.assertEqual(
            [row["sequence"] for row in connected_report["responses"]],
            [3341, 3342, 3343, 3344])
        connected_transport.wait.assert_called_once_with(.25)
        self.assertEqual(connected_report["status"], pilot.SUCCESS_LEFT_CONNECTED)

        connected_2000_transport = _Transport()
        connected_2000_transport.steps = pilot.STEPS_2000_LEFT_CONNECTED
        connected_2000_transport.success = pilot.SUCCESS_2000_LEFT_CONNECTED
        connected_2000_transport.observation_seconds = .25
        connected_2000_transport.motor_connected = True
        connected_2000_report = {}
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()):
            pilot._observe(connected_2000_transport, connected_2000_report, clock=lambda: 0)
        self.assertEqual(
            [row["sequence"] for row in connected_2000_report["responses"]],
            [3345, 3346, 3347, 3348])
        connected_2000_transport.wait.assert_called_once_with(.25)
        self.assertEqual(
            connected_2000_report["status"], pilot.SUCCESS_2000_LEFT_CONNECTED)

        live_artifact = bytes.fromhex("53140d0a80080000006400000000002c7045")
        evidence_transport = Mock(
            steps=pilot.STEPS_2000_LEFT_CONNECTED,
            last_write_sequence=3348,
            last_write_started=1.0,
            event=Mock())

        def feed_live_artifact(_transport, evidence, **_):
            evidence.feed(Received(live_artifact, 1.1, 1.2), 1.3)

        with patch.object(pilot.zero, "_observe_response",
                          side_effect=feed_live_artifact), \
                self.assertRaisesRegex(OSError, "unexpected_raw_pwm_payload"):
            pilot._response(
                evidence_transport, {"responses": []}, "verify",
                deadline=2.0, clock=lambda: 1.5)

        faults = (
            (("set", "error"), None),
            (("set", "partial"), None),
            (None, ("set", OSError("timeout"))),
            (None, ("set", OSError("crc"))),
            (None, ("set", OSError("correlation"))),
            (None, ("set", OSError("extra frame"))),
            (None, ("set", KeyboardInterrupt())),
        )
        for submit_fault, response_fault in faults:
            transport = _Transport(submit_fault)
            with self.subTest(submit_fault=submit_fault,
                              response_fault=response_fault), \
                    patch.object(
                        pilot, "_response",
                        side_effect=self.response(fault=response_fault)), \
                    redirect_stderr(io.StringIO()), self.assertRaises(BaseException):
                pilot._observe(transport, {}, clock=lambda: 0)
            self.assertEqual(transport.attempts.count("cleanup"), 1)
            self.assertTrue(transport.cleanup_attempted)
            transport.close.assert_called_once()

        transport = _Transport()
        transport.wait.side_effect = KeyboardInterrupt()
        with patch.object(pilot, "_response", side_effect=self.response()), \
                redirect_stderr(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            pilot._observe(transport, {}, clock=lambda: 0)
        self.assertEqual(transport.attempts.count("cleanup"), 1)
        self.assertTrue(transport.cleanup_attempted)

        live = pilot._RawPwmTransport.__new__(pilot._RawPwmTransport)
        live.may_have_applied = True
        live.cleanup_attempted = False
        live.cleanup_raw80 = False
        live.completed = ["baseline", "set"]
        live.writes = 2
        live.fd = 99
        live.ingress = Mock()
        live._check = Mock()
        live.event = Mock(side_effect=OSError("journal fault"))
        with patch.object(pilot.os, "write",
                          return_value=len(pilot.STEPS["cleanup"])) as write, \
                self.assertRaises(OSError):
            live._cleanup_once(deadline=1)
        write.assert_called_once_with(99, pilot.STEPS["cleanup"])
        self.assertTrue(live.cleanup_attempted)

        evidence = self.root / "evidence"
        evidence.mkdir()
        (evidence / "metadata.json").write_text("{}\n", encoding="utf-8")
        session.evidence_manifest(evidence)
        digest = pilot._verify_manifest(evidence)
        pilot._write_state(self.root, {
            "status": "raw_pwm_restoration_unverified",
            "target": pilot.PROFILES[consent.RAW_PWM_PILOT_SCOPE]["target"],
            "set_evidence": str(evidence.resolve()),
            "set_manifest_sha256": digest,
        })
        with self.assertRaises(ValueError):
            pilot.acknowledge_restoration(
                evidence, physical_output_baseline_confirmed=True)
        result = pilot.acknowledge_restoration(
            evidence, physical_output_baseline_confirmed=True,
            power_cycle_confirmed=True)
        self.assertEqual(result["status"], "power_cycle_reset_confirmed")

        stale_output = self.root / "preflight-never-created"
        pilot._write_state(self.root, {
            "status": "raw_pwm_restoration_unverified",
            "target": pilot.PROFILES[consent.RAW_PWM_WORD1_2000_PILOT_SCOPE]["target"],
            "set_evidence": str(stale_output.resolve()),
        })
        cleared = pilot.clear_stale_preflight_lock(stale_output)
        self.assertEqual(cleared, {
            "status": "aborted_before_hardware",
            "target": "raw-pwm-word1-2000-pilot",
            "hardware_access": False,
            "application_tx_bytes": 0,
        })

        first_output = self.root / "udevadm-preflight-failure"
        second_output = self.root / "permitted-retry"
        preflight = Mock(side_effect=[
            OSError("udevadm by-id failed"),
            OSError("retry reached preflight"),
        ])
        with patch.object(pilot.zero, "_run_diagnostic", preflight), \
                redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(OSError, "udevadm by-id failed"):
                pilot.run_diagnostic(
                    first_output, expected_physical_port="1-3", run=True,
                    **DECLARATIONS_WORD1_2000)
            self.assertEqual(
                pilot._load_state(self.root)["status"], "aborted_before_hardware")
            self.assertEqual(
                pilot._load_state(self.root)["application_tx_bytes"], 0)
            with self.assertRaisesRegex(OSError, "retry reached preflight"):
                pilot.run_diagnostic(
                    second_output, expected_physical_port="1-3", run=True,
                    **DECLARATIONS_WORD1_2000)
        self.assertEqual(preflight.call_count, 2)
        self.assertFalse(first_output.exists())
        self.assertFalse(second_output.exists())

        possible_setter_output = self.root / "possible-setter"

        def possible_setter(output, **_):
            output.mkdir()
            (output / "metadata.json").write_text(json.dumps({
                "observation": {"nonzero_may_have_applied": True},
            }), encoding="utf-8")
            raise OSError("fault after possible setter")

        with patch.object(pilot.zero, "_run_diagnostic", side_effect=possible_setter), \
                redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(OSError, "fault after possible setter"):
            pilot.run_diagnostic(
                possible_setter_output, expected_physical_port="1-3", run=True,
                **DECLARATIONS_WORD1_2000)
        self.assertEqual(
            pilot._load_state(self.root)["status"], "raw_pwm_restoration_unverified")

        boundary = Mock(
            last_write_sequence=3330, last_write_started=10,
            event=Mock(), steps=pilot.STEPS)
        boundary_report = {"responses": []}

        def observed(_, evidence, **__):
            evidence.feed(Mock(
                data=frame(b"", command=0x0B, status=0x82, sequence=3330),
                started_at=10.001, ended_at=10.002), 10.003)
            evidence.finish(10.003)

        with patch.object(pilot.zero, "_observe_response", side_effect=observed):
            packet = pilot._response(
                boundary, boundary_report, "set", deadline=11, clock=lambda: 10.004)
        self.assertEqual(packet.response_field, 0x82)

        def invalid_baseline(_, evidence, **__):
            evidence.feed(Mock(
                data=frame(bytes.fromhex("0100000000000000"), command=0x0A,
                           status=0x80, sequence=3329),
                started_at=10.001, ended_at=10.002), 10.003)
            evidence.finish(10.003)

        boundary.last_write_sequence = 3329
        boundary_report = {"responses": []}
        with patch.object(pilot.zero, "_observe_response",
                          side_effect=invalid_baseline), self.assertRaises(OSError):
            pilot._response(
                boundary, boundary_report, "baseline",
                deadline=11, clock=lambda: 10.004)


if __name__ == "__main__":
    unittest.main()
