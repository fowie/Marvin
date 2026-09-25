"""Synthetic transport faults and one real host PTY; never robot hardware."""

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

from tools import marvin_legacy_live as live
from tools import marvin_legacy_motor_power_off_prep as prep
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon
from tools import marvin_usbmon_binary as binary
from tools.marvin_legacy_client import Received
from tests.test_marvin_legacy_client import Clock, frame
from tests.test_marvin_legacy_live import event, incoming
from tests import test_marvin_session as session_tests
from tests import test_marvin_usbmon as usbmon_tests


DECLARATIONS = dict.fromkeys(consent.PREPARATION_FLAGS, True)


def reply(index, payload=None, **options):
    return frame(bytes(134) if index and payload is None else payload or b"",
                 command=options.pop("command", 0 if index else 0x11),
                 sequence=options.pop("sequence", 1536 + index), **options)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".prep-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_fixed_offline_transcript_no_hardware_or_arbitrary_knobs(self):
        with patch.object(session, "preflight", side_effect=AssertionError("no host probe")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(prep.main([]), 0)
        review = json.loads(output.getvalue())
        self.assertEqual(review["maximum_application_bytes"], 64)
        self.assertEqual([len(raw) for raw in prep.TRANSCRIPT], [14, 10, 10, 10, 10, 10])
        self.assertEqual(review["immutable_application_transcript_hex"], [
            "5300061100040000000000e4a145", "53010600000000ead445",
            "53020600000000eae745", "53030600000000eb3645",
            "53040600000000ea8145", "53050600000000eb5045",
        ])
        self.assertFalse(review["declarations_are_current_permission"])
        for knob in ("sequence", "value", "opcode", "duration", "retry", "motor-supply-on"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                prep.main(["--" + knob, "1"])
        with redirect_stderr(io.StringIO()):
            for args in (["--actuators-isolated"], ["--motor-supply-off"],
                         ["--run", "--actuators-isolated"],
                         ["--run", "--output", str(self.root / "unused")]):
                self.assertEqual(prep.main(args), 1)

    def session_options(self):
        return dict(
            seconds=15, baudrate=57600, allow_unknown_command=True, probe_profile="legacy",
            capture_runner=Mock(), binary_payload_limit=4096, usb_tail_seconds=5,
            usb_close_grace_seconds=5, _motor_power_off_preparation=True, **DECLARATIONS,
        )

    def usb_options(self):
        return dict(seconds=25, backend="binary", coordinator_stop=True, binary_payload_limit=4096,
                    max_line_bytes=16384, **DECLARATIONS)

    def test_truthy_and_mixed_declarations_rejected_at_all_public_boundaries(self):
        with patch.object(os, "geteuid", return_value=1000), \
                patch.object(session, "preflight", side_effect=AssertionError("no preflight")), \
                patch.object(usbmon, "read_identity", side_effect=AssertionError("no identity")):
            for name in consent.PREPARATION_FLAGS:
                for value in (False, None, 0, 1, "true", [], {}):
                    declarations = DECLARATIONS | {name: value}
                    with self.subTest(name=name, value=value):
                        with self.assertRaises(ValueError):
                            prep.run_preparation(self.root / "unused", expected_physical_port="1-3",
                                                 run=True, **declarations)
                        with self.assertRaises(ValueError):
                            session.run_session("mock", self.root / "unused",
                                                **(self.session_options() | {name: value}))
                        with self.assertRaises(usbmon.UsbmonError):
                            usbmon.capture("mock", self.root / "unused",
                                           **(self.usb_options() | {name: value}))
            for value in (True, 1, "true", None):
                with self.assertRaises(ValueError):
                    prep.run_preparation(self.root / "unused", expected_physical_port="1-3",
                                         run=True, actuators_isolated=value, **DECLARATIONS)
                with self.assertRaises(ValueError):
                    session.run_session("mock", self.root / "unused",
                                        **(self.session_options() | {"actuators_isolated": value}))
                with self.assertRaises(usbmon.UsbmonError):
                    usbmon.capture("mock", self.root / "unused",
                                   **(self.usb_options() | {"actuators_isolated": value}))
            for value in (False, 0, 1, None, "true"):
                with self.assertRaises(ValueError):
                    prep.run_preparation(self.root / "unused", expected_physical_port="1-3",
                                         run=value, **DECLARATIONS)
            for change in ({"_motor_power_off_preparation": 1}, {"_motor_power_off_preparation": False},
                           {"_isolated_zero_velocity": True}, {"dtr": True}, {"sudo_usbmon": True},
                           {"probe_get_config": True}, {"seconds": 16}, {"binary_payload_limit": 32},
                           {"capture_runner": None}, {"probe_schedule": ()}):
                with self.assertRaises(ValueError):
                    session.run_session("mock", self.root / "unused", **(self.session_options() | change))
            for change in ({"drop_to_invoking_user": True}, {"seconds": 26}, {"backend": "text"},
                           {"max_records": 10001}, {"max_bytes": 1048577}, {"coordinator_stop": False}):
                with self.assertRaises(usbmon.UsbmonError):
                    usbmon.capture("mock", self.root / "unused", **(self.usb_options() | change))

    def test_coordinator_usb_argv_and_capture_metadata_are_truthful(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(**(self.session_options() | {
            "actuators_isolated": False, "capture_runner": runner}))
        command = harness.popen.call_args.args[0]
        self.assertNotIn("--actuators-isolated", command)
        self.assertNotIn("sudo", command)
        for flag in consent.PREPARATION_FLAGS:
            self.assertIn("--" + flag.replace("_", "-"), command)
            self.assertIs(runner.call_args.kwargs[flag], True)
        self.assertIs(runner.call_args.kwargs["actuators_isolated"], False)
        self.assertEqual(result["requested_application_bytes"], 64)
        self.assertEqual(result["immutable_application_transcript_hex"], [raw.hex() for raw in prep.TRANSCRIPT])
        self.assertEqual(result["historical_operator_declarations"], DECLARATIONS)
        self.assertFalse(result["declarations_are_current_permission"])
        self.assertEqual((result["usb_nominal_duration_seconds"], result["usb_duration_seconds"]), (20, 25))
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        self.assertIs(capture.call_args.kwargs["actuators_isolated"], False)
        for flag in consent.PREPARATION_FLAGS:
            self.assertIs(capture.call_args.kwargs[flag], True)

    def test_recorder_separate_route_metadata_and_drop_failure_are_retained(self):
        harness = usbmon_tests.CaptureTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        def advancing_select(*args):
            result = harness.select(*args)
            harness.now += 1e-9
            if harness.select_count > 1000:
                raise AssertionError("Synthetic recorder clock did not advance")
            return result
        with patch.object(usbmon.select, "select", side_effect=advancing_select), \
                harness.binary_monitor({"queued": 0, "dropped": 0}):
            result = harness.capture(**(self.usb_options() | {"actuators_isolated": False}))
        self.assertEqual(result["consent_profile"], "motor_power_off_preparation")
        self.assertFalse(result["actuator_power_and_signal_isolation_acknowledged"])
        self.assertEqual(result["historical_operator_declarations"], DECLARATIONS)
        self.assertFalse(result["declarations_are_current_permission"])
        self.assertEqual(harness.metadata()["status"], "completed")
        harness.output = harness.root / "dropped"
        with patch.object(usbmon.select, "select", side_effect=advancing_select), \
                harness.binary_monitor({"queued": 0, "dropped": 1}), \
                self.assertRaises(usbmon.CaptureError):
            harness.capture(**(self.usb_options() | {"actuators_isolated": False}))
        self.assertEqual(harness.metadata()["status"], "failed")
        self.assertTrue((harness.output / "binary-events.bin").is_file())

    def simulate(self, *, replies=None, fault=None, short=None, deadline_jump=None, close_error=None,
                 suppress=None):
        clock = Clock()
        clock.now = 100
        ingress = Mock(pending=b"", rx=[], rx_bytes=0, expected_tx=deque(), outstanding_tx={})
        transport = prep._PreparationTransport("mock", {"tty": "mock"}, self.root, ingress,
                                               guard=Mock(), plan=prep.zero._Limits())
        transport.fd = 999
        transport._check = Mock()
        transport.event = Mock()
        queue, writes, deadlines = deque(), [], []
        transport.close = Mock(side_effect=close_error)

        def opened(*args, deadline):
            deadlines.append(deadline)
            clock.now += 1
            return transport.token

        def write(fd, raw):
            index = len(writes)
            writes.append(raw)
            if fault == index:
                raise OSError("uncertain syscall")
            if short == index:
                return 3
            data = reply(index) if replies is None else replies(index)
            if data:
                queue.append(data)
            ingress.expected_tx.clear()
            return len(raw)

        def read(*args, deadline):
            deadlines.append(deadline)
            data = queue.popleft()
            clock.now += .001
            transport.serial_bytes += len(data)
            ingress.rx_bytes += len(data)
            return Received(data, clock.now - .0005, clock.now)

        def selecting(*args):
            clock.now += args[3]
            if deadline_jump is not None and len(writes) == deadline_jump:
                clock.now = 115
            return ([999] if queue else [], [], [])

        def check(deadline):
            if clock.now >= deadline:
                raise OSError("shared deadline")
            if suppress is not None and len(writes) == suppress and transport.phase == "ready":
                raise OSError("pre-submission refusal")

        transport._check.side_effect = check
        transport.identity = Mock(side_effect=lambda **kw: check(kw["deadline"]) or transport.token)
        transport._read_serial = Mock(return_value=None)
        transport.read_response = Mock(side_effect=read)
        report = {}
        with patch.object(live.LiveTransport, "revalidate", opened), \
                patch.object(time, "monotonic", clock), \
                patch.object(time, "sleep", side_effect=lambda s: setattr(clock, "now", clock.now + s)), \
                patch.object(select, "select", side_effect=selecting), \
                patch.object(os, "write", side_effect=write):
            error = None
            try:
                prep._observe(transport, report)
            except OSError as caught:
                error = caught
            self.assertEqual(writes, list(prep.TRANSCRIPT[:len(writes)]))
            transport.close.assert_called_once()
            self.assertEqual(transport.close.call_args.kwargs["deadline"], clock.now + 5)
            if error:
                with self.assertRaises(OSError):
                    transport.write(prep.TRANSCRIPT[min(len(writes), 5)], deadline=115)
            self.assertEqual(report["application_acknowledgment"], "not_established")
        return report, writes, error

    def test_six_frame_prefix_full_windows_idle_and_shared_deadline(self):
        report, writes, error = self.simulate()
        self.assertIsNone(error)
        self.assertEqual(writes, list(prep.TRANSCRIPT))
        self.assertEqual(report["status"], prep.SUCCESS)
        self.assertEqual(report["operational_deadline_monotonic"], 115)
        self.assertEqual(report["accepted_tx_bytes"], 64)
        self.assertEqual(report["serial_rx_bytes"], 730)
        phases = report["phases"]
        self.assertEqual(phases[0]["submitted_at"], 101)
        self.assertEqual(phases[0]["response_deadline"], 104)
        for previous, current in zip(phases, phases[1:]):
            self.assertGreaterEqual(current["submitted_at"], previous["response_deadline"])
            self.assertGreaterEqual(current["submitted_at"] - previous["events"][0]["ended_at"], 1)
        self.assertEqual(len(report["telemetry"]), 5)

    def test_every_nonzero_raw_word_rejects_actual_payload_and_decoder_fields(self):
        for name, offset in prep.ZERO_FIELDS:
            for value in (1, 256, 65535):
                payload = bytearray(134)
                payload[offset:offset + 2] = value.to_bytes(2, "little")
                report, writes, error = self.simulate(
                    replies=lambda index: reply(index, bytes(payload) if index else b""))
                with self.subTest(name=name, value=value):
                    self.assertIsNotNone(error)
                    self.assertEqual(len(writes), 2)
                    decoded = report["telemetry"][0]
                    self.assertEqual(decoded["fields"][name]["offset"], offset)
                    self.assertEqual(decoded["fields"][name]["unsigned"], value)
                    self.assertEqual(decoded["fields"][name]["raw_hex"], value.to_bytes(2, "little").hex())
                    self.assertIn("nonzero_or_inconsistent_velocity_or_PWM",
                                  report["response_events"][-1]["labels"])

    def test_unknown_extra_corrupt_partial_status_and_missing_reply_suppress_suffix(self):
        for index in range(6):
            for bad in (b"", b"noise", reply(index, b"unknown"), reply(index, status=0x81),
                        reply(index, sequence=999), reply(index)[:-1], reply(index)[:-1] + b"X",
                        reply(index) + reply(index), reply(index) + b"\x53"):
                report, writes, error = self.simulate(
                    replies=lambda current: bad if current == index else reply(current))
                with self.subTest(index=index, bad=bad.hex()):
                    self.assertIsNotNone(error)
                    self.assertEqual(len(writes), index + 1)
                    self.assertEqual(report["status"], "failed")
                    raw_events = b"".join(bytes.fromhex(row["stream"]["raw_hex"])
                                          for row in report["response_events"])
                    self.assertTrue(raw_events.endswith(bad))

    def test_uncertainty_deadline_and_cleanup_faults_never_retry(self):
        for index in range(6):
            for option in ("fault", "short"):
                report, writes, error = self.simulate(**{option: index})
                self.assertIsNotNone(error)
                self.assertEqual(len(writes), index + 1)
                accepted = 3 if option == "short" else 0
                self.assertEqual(report["uncertain_tx_bytes"], len(prep.TRANSCRIPT[index]) - accepted)
                self.assertEqual(report["accepted_tx_bytes"],
                                 sum(map(len, prep.TRANSCRIPT[:index])) + accepted)
            report, writes, error = self.simulate(suppress=index)
            self.assertIsNotNone(error)
            self.assertEqual(len(writes), index)
            self.assertEqual(report["uncertain_tx_bytes"], 0)
            self.assertEqual(report["accepted_tx_bytes"], sum(map(len, prep.TRANSCRIPT[:index])))
        for index in range(1, 7):
            report, writes, error = self.simulate(deadline_jump=index)
            self.assertIsNotNone(error)
            self.assertEqual(len(writes), index)
            self.assertEqual(report["operational_deadline_monotonic"], 115)
        report, writes, error = self.simulate(close_error=OSError("close fault"))
        self.assertIsNotNone(error)
        self.assertEqual(len(writes), 6)
        self.assertEqual(report["cleanup_errors"], ["close fault"])
        self.assertEqual(report["status"], "failed")

    def test_submission_boundary_refuses_wrong_frame_early_phase_and_deadline_then_latches(self):
        for change in ("unopened", "wrong", "early", "awaiting", "deadline", "complete"):
            ingress = Mock()
            transport = prep._PreparationTransport("mock", {}, self.root, ingress,
                                                   guard=Mock(), plan=prep.zero._Limits())
            transport._check = Mock()
            transport.operation_deadline = 100
            transport.not_before = 1 if change != "early" else 50
            transport.phase = {"unopened": "unopened", "awaiting": "awaiting_response",
                               "complete": "complete"}.get(change, "ready")
            with patch.object(time, "monotonic", return_value=10), \
                    patch.object(os, "write") as write:
                with self.assertRaises(OSError):
                    transport._submit_once(prep.TRANSCRIPT[1 if change == "wrong" else 0],
                                           deadline=101 if change == "deadline" else 100)
                self.assertEqual(transport.phase, "failed")
                with self.assertRaises(OSError):
                    transport.write(prep.TRANSCRIPT[0], deadline=100)
                write.assert_not_called()

    def test_prewrite_input_identity_clock_recorder_and_journal_faults_block_syscall(self):
        for fault in ("queued_serial", "queued_usb", "partial_usb", "pending_tx", "identity",
                      "clock", "recorder", "journal"):
            ingress = Mock(pending=b"", rx=[], rx_bytes=0, expected_tx=deque(), outstanding_tx={})
            transport = prep._PreparationTransport("mock", {}, self.root, ingress,
                                                   guard=Mock(), plan=prep.zero._Limits())
            transport.fd = 999
            transport._check = Mock()
            transport._read_serial = Mock(return_value=None)
            transport.event = Mock()
            transport.phase, transport.not_before, transport.operation_deadline = "ready", 0, 100
            if fault == "queued_serial":
                transport._read_serial.return_value = b"retained prewrite raw"
            elif fault == "queued_usb":
                ingress.rx = [b"unsolicited"]
            elif fault == "partial_usb":
                ingress.pending = b"x"
            elif fault == "pending_tx":
                ingress.expected_tx.append(b"old")
            elif fault in ("identity", "clock", "recorder"):
                transport._check.side_effect = OSError(fault)
            else:
                transport.event.side_effect = OSError("journal")
            with patch.object(time, "monotonic", return_value=10), patch.object(os, "write") as writing:
                with self.subTest(fault=fault), self.assertRaises(OSError):
                    transport.write(prep.TRANSCRIPT[0], deadline=100)
                self.assertEqual(transport.phase, "failed")
                writing.assert_not_called()
        observation = prep.zero._ResponseEvidence(Mock(), sequence=1536, command=0x11)
        observation.submitted_at, observation.deadline = 10, 13
        with self.assertRaisesRegex(OSError, "prewrite"):
            observation.feed(Received(reply(0), 10, 10.1), 10.2)
        self.assertEqual(observation.events[0]["stream"]["raw_hex"], reply(0).hex())

    def test_lifecycle_seals_failures_and_validates_capture_consent_before_transport(self):
        from tools import marvin_legacy_probe
        for fault in ("none", "tail", "clock", "capture_consent"):
            output = self.root / fault
            ingress = Mock(rx_consumed=730, rx_bytes=730, completed_tx=64, line_states=[])
            bridge = Mock(offset=(0, 1))
            if fault == "tail":
                ingress.finish.side_effect = OSError("unmatched USB tail")
            if fault == "clock":
                bridge.check.side_effect = OSError("clock changed")
            transport = Mock()

            def observe(adapter, report):
                report.update(status=prep.SUCCESS, serial_rx_bytes=730)

            def coordinated(port, directory, **options):
                directory.mkdir()
                args = DECLARATIONS | {"actuators_isolated": fault == "capture_consent"}
                options["capture_runner"](port, directory / "serial", guard=lambda: None, **args)
                return {"status": "completed"}

            with patch.object(os, "geteuid", return_value=1000), \
                    patch.object(session, "preflight", return_value=session_tests.BASELINE), \
                    patch.object(marvin_legacy_probe, "_validate_baseline"), \
                    patch.object(prep.zero, "IngressClock", return_value=bridge), \
                    patch.object(prep.zero, "UsbIngress", return_value=ingress), \
                    patch.object(prep, "_PreparationTransport", return_value=transport) as constructor, \
                    patch.object(prep, "_observe", side_effect=observe), \
                    patch.object(session, "run_session", side_effect=coordinated):
                if fault == "none":
                    result = prep.run_preparation(output, expected_physical_port="1-3", run=True, **DECLARATIONS)
                    self.assertEqual(result["status"], prep.SUCCESS)
                else:
                    with self.assertRaises((OSError, ValueError)):
                        prep.run_preparation(output, expected_physical_port="1-3", run=True, **DECLARATIONS)
                if fault == "capture_consent":
                    constructor.assert_not_called()
                    ingress.open.assert_not_called()
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["status"], prep.SUCCESS if fault == "none" else "failed")
            self.assertFalse(metadata["declarations_are_current_permission"])
            self.assertEqual(metadata["historical_operator_declarations"], DECLARATIONS)
            self.assertEqual(metadata["new_boot_basis"], "operator_declaration_not_enumeration_proof")
            self.assertIn("run_id", metadata)
            self.assertNotIn("fresh_generation", metadata)
            self.assertTrue((output / "SHA256SUMS").is_file())
            ingress.close.assert_called_once()
            bridge.close.assert_called_once()

    def test_real_host_pty_one_raw_open_six_writes_same_owner_close(self):
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
        transport = prep._PreparationTransport(tty, baseline, self.root, ingress,
                                               guard=lambda: None, plan=prep.zero._Limits())
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
                    while not stop.is_set() and len(emulated) < 6:
                        if not select.select([master], [], [], .01)[0]:
                            continue
                        try:
                            data = os.read(master, 512)
                        except OSError:
                            time.sleep(.005)  # Slave has not yet been opened.
                            continue
                        pending.extend(data)
                        index = len(emulated)
                        expected = prep.TRANSCRIPT[index]
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
                prep._observe(transport, report)
                with self.assertRaises(OSError):
                    transport.write(prep.TRANSCRIPT[0], deadline=time.monotonic() + 15)
                with self.assertRaises(OSError):
                    transport.revalidate(deadline=time.monotonic() + 15)
            finally:
                stop.set()
                worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        ingress.finish(64)
        self.assertEqual(written, list(prep.TRANSCRIPT))
        self.assertEqual(emulated, written)
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, opened)
        self.assertEqual(report["status"], prep.SUCCESS)
        self.assertEqual(report["serial_rx_bytes"], 730)
        journal = list(map(json.loads, (self.root / "adapter.jsonl").read_text().splitlines()))
        self.assertEqual(sum(row["event"] == "open_completed" for row in journal), 1)
        self.assertEqual(sum(row["event"] == "close_completed" for row in journal), 1)
        self.assertEqual(len(report["telemetry"]), 5)
        print("HOST_PTY software-only: opens=1 writes=6 tx_bytes=64 rx_bytes=730 closes=1 "
              "status=preparation_observation_complete_unverified; synthetic USB, no hardware")


if __name__ == "__main__":
    unittest.main()
