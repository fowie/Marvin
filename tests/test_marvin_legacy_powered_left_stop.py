"""Software-only exact-frame and powered-stop scheduling tests."""

from collections import deque
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import select
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_powered_left_stop as powered
from tools import marvin_legacy_live as live
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon
from tools import marvin_usbmon_binary as binary
from tests.test_marvin_legacy_client import frame
from tests.test_marvin_legacy_live import event, incoming
from tests import test_marvin_session as session_tests


DECLARATIONS = dict.fromkeys(consent.POWERED_TRIAL_FLAGS, True)
FLAGS = ["--" + name.replace("_", "-") for name in consent.POWERED_TRIAL_FLAGS]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class PoweredLeftStopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".powered-stop-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def transport(self, writes, *, start_result=None, zero_result=None, same_fd_fault=None,
                  event_fault=None):
        ingress = Mock(pending=b"", rx=[], rx_bytes=0, expected_tx=deque(), outstanding_tx={})
        transport = powered._PoweredLeftTransport(
            "mock", {"tty": "mock"}, self.root, ingress, guard=Mock(),
            plan=powered.zero._Limits(first_sequence=3072, max_requests=3, interval=0))
        transport.fd = 99
        transport.node_stat = (1, 2, 3)
        transport.journal = Mock()
        transport.journal.fileno.return_value = 88
        transport._check = Mock()
        transport._read_serial = Mock(return_value=None)
        transport.event = Mock(side_effect=event_fault)
        same_calls = 0

        def same():
            nonlocal same_calls
            same_calls += 1
            if same_fd_fault == same_calls:
                raise OSError("owned identity lost")

        transport._same_owned_writable_fd = Mock(side_effect=same)

        def write(fd, raw):
            writes.append(raw)
            if raw == powered.START and isinstance(start_result, BaseException):
                raise start_result
            if raw == powered.PLANNED_ZERO and isinstance(zero_result, BaseException):
                raise zero_result
            if raw == powered.START and start_result is not None:
                return start_result
            if raw == powered.PLANNED_ZERO and zero_result is not None:
                return zero_result
            return len(raw)

        return transport, write

    def test_offline_exact_crc_plan_and_no_arbitrary_knobs(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(powered.main([]), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["immutable_application_transcript_hex"], [
            "53000c11000400010000009bfd45",
            "53010c1100040000000000cbc445",
            "53020c0000000072e645",
        ])
        self.assertEqual([len(raw) for raw in powered.TRANSCRIPT], [14, 14, 10])
        self.assertEqual(plan["maximum_application_bytes"], 38)
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(powered.main(FLAGS), 0)
            for knob in ("value", "opcode", "duration", "sequence", "retry", "count"):
                with self.assertRaises(SystemExit):
                    powered.main(FLAGS + ["--" + knob, "1"])

    def test_literal_scope_rejected_before_preflight(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no preflight")):
            for name in consent.POWERED_TRIAL_FLAGS:
                for value in (False, 0, 1, None, "true"):
                    with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                        powered.run_characterization(
                            self.root / "unused", expected_physical_port="1-3", run=True,
                            **(DECLARATIONS | {name: value}))
            with self.assertRaises(ValueError):
                powered.run_characterization(
                    self.root / "unused", expected_physical_port="1-3", run=True,
                    actuators_isolated=True, **DECLARATIONS)

    def test_right_connected_scope_keeps_left_frames_and_rejects_false_or_mixed_consent(self):
        scope = consent.MAPPING_TRIAL_SCOPE
        declarations = dict.fromkeys(consent.MAPPING_TRIAL_FLAGS, True)
        flags = ["--" + name.replace("_", "-") for name in declarations]
        with patch.object(session, "preflight", side_effect=AssertionError("no hardware")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(powered.main(flags), 0)
            plan = json.loads(stdout.getvalue())
            self.assertEqual(plan["name"], scope)
            self.assertEqual(plan["immutable_application_transcript_hex"], [
                "53000c11000400010000009bfd45",
                "53010c1100040000000000cbc445",
                "53020c0000000072e645",
            ])
            self.assertEqual(plan["required"][3:], flags)
            self.assertEqual(plan["nominal_start_to_zero_seconds"], .250)
            self.assertFalse(plan["automatic_retries"])
            self.assertFalse(plan["automatic_reconnect"])
            with patch.object(powered.zero, "_run_diagnostic", return_value={}) as runner:
                powered.run_characterization(
                    self.root / "unused", expected_physical_port="1-3", run=True, **declarations)
                options = runner.call_args.kwargs
                self.assertIs(options["transport_type"], powered._PoweredLeftTransport)
                self.assertIs(options["observe"], powered._observe)
                self.assertEqual(options["review"], plan)
                self.assertEqual(options["declarations"]["scope"], scope)
                self.assertEqual(options["declarations"]["load_scope"],
                                 "MOTOR_L_DISCONNECTED_MOTOR_R_CONNECTED")
                self.assertEqual(options["authorizations"],
                                 {"unvalidated_left_one_and_zero_authorized": True})
                options["capture_validator"](declarations)
                with self.assertRaises(ValueError):
                    options["capture_validator"](DECLARATIONS)
                runner.reset_mock()
                invalid = [
                    declarations | {name: value}
                    for name in declarations for value in (False, 0, 1, None, "true")
                ] + [
                    declarations | {name: True}
                    for name in (*set(consent.ALL_FLAGS).difference(declarations), "actuators_isolated")
                ]
                for wrong in invalid:
                    with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                        powered.run_characterization(
                            self.root / "unused", expected_physical_port="1-3", run=True, **wrong)
                runner.assert_not_called()
            self.assertIn("CUT_POWER_REQUIRED", stderr.getvalue())

    def test_mapping_scope_coordinator_and_recorder_forward_truthful_consent_offline(self):
        declarations = dict.fromkeys(consent.MAPPING_TRIAL_FLAGS, True)
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **declarations)
        command = harness.popen.call_args.args[0]
        self.assertEqual(result["scope"], consent.MAPPING_TRIAL_SCOPE)
        self.assertEqual(result["load_scope"], "MOTOR_L_DISCONNECTED_MOTOR_R_CONNECTED")
        self.assertEqual(result["immutable_application_transcript_hex"],
                         [raw.hex() for raw in powered.TRANSCRIPT])
        self.assertEqual(result["requested_application_bytes"], 38)
        self.assertEqual((result["usb_nominal_duration_seconds"], result["usb_duration_seconds"]), (10, 15))
        for name in consent.POWERED_TRIAL_SCOPES["powered_left_stop_characterization"][:3]:
            self.assertNotIn("--" + name.replace("_", "-"), command)
        powered._validate_capture(runner.call_args.kwargs, scope=consent.MAPPING_TRIAL_SCOPE)
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        powered._validate_capture(capture.call_args.kwargs, scope=consent.MAPPING_TRIAL_SCOPE)
        with patch.object(usbmon, "validate_privilege_drop",
                          side_effect=AssertionError("no recorder startup")), redirect_stderr(io.StringIO()):
            for name in ("powered_left_stop_characterization", "motor_left_connected",
                         "motor_right_disconnected", "motor_supply_off"):
                wrong = declarations | {name: True}
                with self.subTest(name=name), self.assertRaises(usbmon.UsbmonError):
                    usbmon.capture("unused", self.root / "unused", seconds=15, **wrong)

    def test_full_start_always_attempts_zero_before_journal_and_marks_timing(self):
        for fault in (None, OSError("post-zero journal fault")):
            writes = []
            event_fault = None
            if fault is not None:
                event_fault = lambda name, **fields: (
                    (_ for _ in ()).throw(fault) if name == "powered_left_start_result" else None)
            transport, write = self.transport(writes, event_fault=event_fault)
            clock = Clock()
            with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                    redirect_stderr(io.StringIO()) as stderr:
                if fault is None:
                    pulse = transport.run_pulse(deadline=105, clock=clock, sleep=clock.sleep)
                    self.assertEqual(pulse["zero_start_lateness_seconds"], 0)
                else:
                    with self.assertRaisesRegex(OSError, "post-zero journal fault"):
                        transport.run_pulse(deadline=105, clock=clock, sleep=clock.sleep)
            self.assertEqual(writes, [powered.START, powered.PLANNED_ZERO])
            self.assertEqual(transport.writes, 2)
            self.assertIn("START:", stderr.getvalue())
            self.assertIn("STOP_ATTEMPT:", stderr.getvalue())

        writes = []
        transport, write = self.transport(writes)
        clock = Clock()
        with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                redirect_stderr(io.StringIO()), self.assertRaisesRegex(OSError, "wait fault"):
            transport.run_pulse(
                deadline=105, clock=clock,
                sleep=lambda _: (_ for _ in ()).throw(OSError("wait fault")))
        self.assertEqual(writes, [powered.START, powered.PLANNED_ZERO])

    def test_partial_uncertain_start_or_owned_identity_fault_suppresses_zero(self):
        cases = (
            {"start_result": 3},
            {"start_result": OSError("uncertain start")},
            {"same_fd_fault": 1},
            {"same_fd_fault": 2},
        )
        for options in cases:
            writes = []
            transport, write = self.transport(writes, **options)
            clock = Clock()
            with self.subTest(options=options), patch.object(os, "write", side_effect=write), \
                    patch.object(os, "fsync"), redirect_stderr(io.StringIO()), \
                    self.assertRaises(OSError):
                transport.run_pulse(deadline=105, clock=clock, sleep=clock.sleep)
            self.assertNotIn(powered.PLANNED_ZERO, writes)
            self.assertLessEqual(len(writes), 1)

    def test_zero_is_once_even_late_and_zero_failure_never_retries(self):
        writes = []
        transport, write = self.transport(writes)
        clock = Clock()

        def late_sleep(seconds):
            clock.now += seconds + .2

        with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                redirect_stderr(io.StringIO()):
            pulse = transport.run_pulse(deadline=105, clock=clock, sleep=late_sleep)
        self.assertEqual(writes.count(powered.PLANNED_ZERO), 1)
        self.assertGreater(pulse["zero_start_lateness_seconds"],
                           powered.MAX_ZERO_START_LATENESS_SECONDS)

        for result in (2, OSError("uncertain zero")):
            writes = []
            transport, write = self.transport(writes, zero_result=result)
            clock = Clock()
            with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                    redirect_stderr(io.StringIO()), self.assertRaises(OSError):
                transport.run_pulse(deadline=105, clock=clock, sleep=clock.sleep)
            self.assertEqual(writes, [powered.START, powered.PLANNED_ZERO])

    def test_ordinary_response_fault_happens_after_single_zero_and_suppresses_getter(self):
        writes = []
        transport, write = self.transport(writes)
        transport.revalidate = Mock(return_value=transport.token)
        transport.close = Mock()
        clock = Clock()
        with patch.object(os, "write", side_effect=write), patch.object(os, "fsync"), \
                patch.object(powered, "_collect_setter_responses",
                             side_effect=OSError("ordinary RX decode fault")), \
                redirect_stderr(io.StringIO()), self.assertRaisesRegex(OSError, "RX decode fault"):
            powered._observe(transport, {}, clock=clock, sleep=clock.sleep)
        self.assertEqual(writes, [powered.START, powered.PLANNED_ZERO])
        self.assertEqual(writes.count(powered.PLANNED_ZERO), 1)
        transport.close.assert_called_once()

    def test_coordinator_scope_and_native_pty_three_write_order(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(
            seconds=5, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=runner, binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, actuators_isolated=False, **DECLARATIONS)
        command = harness.popen.call_args.args[0]
        for flag in FLAGS:
            self.assertIn(flag, command)
        self.assertNotIn("--actuators-isolated", command)
        powered._validate_capture(runner.call_args.kwargs)
        self.assertEqual(result["requested_application_bytes"], 38)
        self.assertEqual(result["scope"], "powered_left_stop_characterization")
        self.assertEqual((result["usb_nominal_duration_seconds"],
                          result["usb_duration_seconds"]), (10, 15))
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        powered._validate_capture(capture.call_args.kwargs)

        def reply(index):
            payload = bytes(134) if index == 2 else b""
            return frame(payload, command=0 if index == 2 else 0x11,
                         sequence=3072 + index)

        with redirect_stderr(io.StringIO()) as stderr:
            report, journal = self.host_pty(reply)
        self.assertEqual(report["application_submission_attempts"], 3)
        self.assertTrue(report["planned_zero_fully_accepted"])
        self.assertTrue(report["zero_timing_within_acceptance"])
        self.assertEqual([row["raw_hex"] for row in journal if row["event"] == "write_attempt"],
                         [powered.FINAL_GETTER.hex()])
        self.assertIn("START:", stderr.getvalue())
        self.assertIn("STOP_ATTEMPT:", stderr.getvalue())
        self.assertIn("END:", stderr.getvalue())

    def host_pty(self, reply):
        import pty
        master, slave = pty.openpty()
        tty = os.ttyname(slave)
        os.close(slave)
        self.addCleanup(os.close, master)
        baseline = {"tty": tty, "tty_rdev": Path(tty).stat().st_rdev,
                    "usb": {"busnum": 1, "devnum": 10}}
        path = self.root / "binary-events.bin"
        path.write_bytes(binary.FILE_MAGIC)
        clock = live.IngressClock()
        clock.start()
        self.addCleanup(clock.close)
        ingress = live.UsbIngress(path, baseline["usb"], clock)
        ingress.open()
        self.addCleanup(ingress.close)
        transport = powered._PoweredLeftTransport(
            tty, baseline, self.root, ingress, guard=lambda: None,
            plan=powered.zero._Limits(first_sequence=3072, max_requests=3, interval=0))
        self.addCleanup(lambda: transport.close(deadline=time.monotonic() + 5))
        original_open, original_close, original_write = os.open, os.close, os.write
        original_ioctl = live.fcntl.ioctl
        opened, closed, written, emulated, failures = [], [], [], [], []
        stop = threading.Event()

        def tracked_open(path, *args, **kwargs):
            fd = original_open(path, *args, **kwargs)
            if str(path) == tty:
                opened.append(fd)
            return fd

        def tracked_close(fd):
            if fd in opened:
                closed.append(fd)
            return original_close(fd)

        def tracked_write(fd, raw):
            if fd in opened:
                written.append(raw)
            return original_write(fd, raw)

        def ioctl(fd, command, *args):
            if fd in opened and command in (termios.TIOCMBIC, termios.TIOCMGET):
                return bytes(4) if command == termios.TIOCMGET else 0
            return original_ioctl(fd, command, *args)

        def emulator():
            pending = bytearray()
            try:
                with path.open("ab", buffering=0) as evidence:
                    while not stop.is_set() and len(emulated) < len(powered.TRANSCRIPT):
                        if not select.select([master], [], [], .01)[0]:
                            continue
                        try:
                            data = os.read(master, 512)
                        except OSError:
                            time.sleep(.005)
                            continue
                        pending.extend(data)
                        index = len(emulated)
                        expected = powered.TRANSCRIPT[index]
                        if len(pending) < len(expected):
                            continue
                        raw = bytes(pending[:len(expected)])
                        del pending[:len(expected)]
                        if raw != expected or pending:
                            raise AssertionError("PTY observed unexpected transcript bytes")
                        emulated.append(raw)
                        stamp = time.time()
                        evidence.write(event(raw, stamp=stamp, urb=100 + index) +
                                       event(event="C", status=0, data_flag=ord(">"),
                                             length=len(raw), stamp=stamp, urb=100 + index))
                        time.sleep(.02)
                        response = reply(index)
                        evidence.write(incoming(response, stamp=time.time(), urb=200 + index))
                        original_write(master, response)
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=emulator)
        report = {}
        with ExitStack() as stack:
            stack.enter_context(patch.object(session, "preflight", return_value=baseline))
            stack.enter_context(patch.object(session, "check_identity"))
            stack.enter_context(patch.object(live.subprocess, "run", return_value=Mock(
                returncode=0, stdout=str(os.getpid()), stderr="")))
            stack.enter_context(patch.object(os, "open", side_effect=tracked_open))
            stack.enter_context(patch.object(os, "close", side_effect=tracked_close))
            stack.enter_context(patch.object(os, "write", side_effect=tracked_write))
            stack.enter_context(patch.object(live.fcntl, "ioctl", side_effect=ioctl))
            stack.enter_context(patch.object(termios, "tcflush", side_effect=AssertionError("no flush")))
            worker.start()
            try:
                powered._observe(transport, report)
            finally:
                stop.set()
                worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        ingress.finish(38)
        self.assertEqual(written, list(powered.TRANSCRIPT))
        self.assertEqual(emulated, written)
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, opened)
        self.assertEqual(report["status"], powered.SUCCESS)
        self.assertEqual(report["serial_rx_bytes"], 164)
        journal = list(map(json.loads, (self.root / "adapter.jsonl").read_text().splitlines()))
        self.assertEqual(sum(row["event"] == "open_completed" for row in journal), 1)
        self.assertEqual(sum(row["event"] == "close_completed" for row in journal), 1)
        print("HOST_PTY software-only: opens=1 writes=3 tx_bytes=38 "
              "rx_bytes=164 closes=1; synthetic USB/identity, no hardware")
        return report, journal


if __name__ == "__main__":
    unittest.main()
