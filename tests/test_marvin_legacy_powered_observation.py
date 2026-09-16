"""Software-only powered-scope tests. All USB/identity is synthetic; one host PTY."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import pty
import select
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_live as live
from tools import marvin_legacy_poll as poll
from tools import marvin_legacy_probe
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_probe
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon
from tools import marvin_usbmon_binary as binary
from tests.test_marvin_legacy_client import Clock, FakeTransport, frame
from tests.test_marvin_legacy_live import event, incoming
from tests import test_marvin_session as session_tests
from tests import test_marvin_usbmon as usbmon_tests


DECLARATIONS = dict.fromkeys(consent.OBSERVATION_FLAGS, True)
REQUEST = bytes.fromhex("53000900000000bf0445")


def reply(payload=bytes(134), **options):
    return frame(payload, sequence=options.pop("sequence", 2304),
                 command=options.pop("command", 0), **options)


class PoweredObservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".powered-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def session_options(self):
        return dict(seconds=3, baudrate=57600, actuators_isolated=False,
                    allow_unknown_command=True, probe_profile="legacy", capture_runner=Mock(),
                    binary_payload_limit=4096, usb_tail_seconds=5, usb_close_grace_seconds=5,
                    probe_schedule=(marvin_probe.ScheduledWrite(0, REQUEST, "legacy-read-raw-data"),),
                    **DECLARATIONS)

    def usb_options(self):
        return dict(seconds=13, backend="binary", coordinator_stop=True,
                    binary_payload_limit=4096, max_line_bytes=16384, **DECLARATIONS)

    def test_fixed_cli_defaults_conflicts_and_no_permission_from_history(self):
        flags = ["--" + name.replace("_", "-") for name in consent.OBSERVATION_FLAGS]
        with patch.object(session, "preflight", side_effect=AssertionError("no preflight")), \
                patch.object(os, "open", side_effect=AssertionError("no open")), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.assertEqual(live.main(flags), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["immutable_application_transcript_hex"], [REQUEST.hex()])
            self.assertEqual(result["maximum_application_bytes"], 10)
            self.assertEqual((result["usb_nominal_seconds"], result["usb_hard_seconds"]), (8, 13))
            self.assertFalse(result["declarations_are_current_permission"])
            self.assertEqual(result["outcome_meaning"], "observation_only_not_stop_or_commissioning")
            for knob, value in (("max-requests", "2"), ("interval", "2"), ("duration", "15"),
                                ("first-sequence", "512")):
                self.assertEqual(live.main(flags + ["--" + knob, value]), 1)
            self.assertEqual(live.main(flags + ["--actuators-isolated"]), 1)
            self.assertEqual(live.main(flags + ["--motor-supply-off"]), 1)
            for flag in flags:
                self.assertEqual(live.main([item for item in flags if item != flag]), 1)
            with self.assertRaises(ValueError):
                live.run_live(self.root / "unused", expected_physical_port="1-3", **DECLARATIONS)
            for change in ({"first_sequence": 512}, {"duration": 4}, {"request_timeout": .4},
                           {"max_rx_bytes": 9000}, {"cleanup_timeout": 6}, {"max_requests": 2}):
                with self.assertRaises(ValueError):
                    live.run_live(self.root / "unused", expected_physical_port="1-3",
                                  run=True, plan=replace(live.powered_observation_plan(), **change), **DECLARATIONS)
        self.assertIs(consent.validate(actuators_isolated=True), False)
        self.assertIs(consent.validate(**dict.fromkeys(consent.PREPARATION_FLAGS, True)), True)

    def test_all_public_boundaries_reject_partial_truthy_mixed_and_changed_plans(self):
        with patch.object(os, "geteuid", return_value=1000), \
                patch.object(session, "preflight", side_effect=AssertionError("no preflight")), \
                patch.object(usbmon, "read_identity", side_effect=AssertionError("no identity")), \
                redirect_stderr(io.StringIO()):
            changes = [{name: value} for name in consent.OBSERVATION_FLAGS
                       for value in (False, 1, 0, "true", None, [], {})]
            changes += [{name: value} for name in ("actuators_isolated", "motor_supply_off",
                                                  "new_boot_declared", "authorize_unvalidated_zero_velocity")
                        for value in (True, 1, None, "false")]
            for change in changes:
                with self.subTest(change=change):
                    with self.assertRaises(ValueError):
                        live.run_live(self.root / "unused", expected_physical_port="1-3",
                                      run=True, **(DECLARATIONS | change))
                    with self.assertRaises(ValueError):
                        session.run_session("mock", self.root / "unused", **(self.session_options() | change))
                    with self.assertRaises(usbmon.UsbmonError):
                        usbmon.capture("mock", self.root / "unused", **(self.usb_options() | change))
            for change in ({"seconds": 4}, {"probe_schedule": ()}, {"sudo_usbmon": True},
                           {"dtr": True}, {"allow_line_state_change": True}, {"baudrate": 9600},
                           {"_isolated_zero_velocity": True}, {"_motor_power_off_preparation": True},
                           {"binary_payload_limit": 32}, {"capture_runner": None}):
                with self.assertRaises(ValueError):
                    session.run_session("mock", self.root / "unused", **(self.session_options() | change))
            for change in ({"seconds": 14}, {"max_bytes": 1048577}, {"max_records": 10001},
                           {"backend": "text"}, {"coordinator_stop": False}, {"max_pending": 100},
                           {"drop_to_invoking_user": True}):
                with self.assertRaises(usbmon.UsbmonError):
                    usbmon.capture("mock", self.root / "unused", **(self.usb_options() | change))

    def test_coordinator_capture_usb_argv_and_metadata_truthful(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(**(self.session_options() | {"capture_runner": runner}))
        command = harness.popen.call_args.args[0]
        self.assertNotIn("--actuators-isolated", command)
        self.assertNotIn("--motor-supply-off", command)
        self.assertNotIn("sudo", command)
        self.assertIsNone(harness.popen.call_args.kwargs["stderr"])
        for flag in consent.OBSERVATION_FLAGS:
            self.assertIn("--" + flag.replace("_", "-"), command)
            self.assertIs(runner.call_args.kwargs[flag], True)
        self.assertIs(runner.call_args.kwargs["actuators_isolated"], False)
        self.assertIs(runner.call_args.kwargs["motor_supply_off"], False)
        self.assertEqual(result["requested_application_bytes"], 10)
        self.assertEqual((result["usb_nominal_duration_seconds"], result["usb_duration_seconds"]), (8, 13))
        self.assertFalse(result["actuator_power_and_signal_isolation_acknowledged"])
        self.assertFalse(result["motor_supply_off_acknowledged"])
        self.assertEqual(result["motor_supply_on_permission"], "not_granted")
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        self.assertIs(capture.call_args.kwargs["actuators_isolated"], False)
        self.assertIs(capture.call_args.kwargs["motor_supply_off"], False)
        for flag in consent.OBSERVATION_FLAGS:
            self.assertIs(capture.call_args.kwargs[flag], True)

    def simulate(self, data=None, fault=None):
        clock = Clock()
        transport = FakeTransport(clock)
        telemetry, order = [], []
        transport.on_write = lambda packet: transport.enqueue(reply() if data is None else data)
        if fault == "short":
            transport.count = 3
        if fault == "write":
            transport.on_write = Mock(side_effect=OSError("uncertain write"))
        if fault == "open":
            transport.revalidate = Mock(side_effect=OSError("open/preflight fault"))
        if fault == "start_deadline":
            def late_start(**kwargs):
                clock.now += 3
                return transport.token
            transport.revalidate = late_start
        if fault == "identity":
            transport.on_identity = Mock(side_effect=OSError("fresh identity guard"))
        if fault == "deadline":
            transport.on_read = lambda size, deadline: setattr(clock, "now", deadline)
        if fault == "close":
            transport.on_close = Mock(side_effect=OSError("close failed"))
        original_close = transport.close

        def close(**kwargs):
            order.append("close")
            return original_close(**kwargs)

        transport.close = close
        path = self.root / f"poll-{len(list(self.root.iterdir()))}.jsonl"
        with redirect_stderr(io.StringIO()) as diagnostic:
            def notify(error):
                order.append("notify")
                consent.notify_cut_power(error)

            try:
                result = poll.collect(
                    transport, path, ownership_key=b"software-only-powered",
                    expected_identity=transport.token, clock=clock, plan=live.powered_observation_plan(),
                    evidence_kind="synthetic", on_failure=notify,
                    on_evidence=lambda events: live.inspect_powered_evidence(events, telemetry))
                error = None
            except poll.CollectionError as caught:
                result, error = caught.result, caught
        self.assertEqual(transport.writes, [] if fault in ("open", "start_deadline", "identity") else [REQUEST])
        self.assertEqual(order.count("close"), 1)
        if error:
            self.assertEqual(result.report["status"], "failed")
            self.assertIn("CUT EXTERNAL POWER NOW", diagnostic.getvalue())
            if fault != "close":
                self.assertLess(order.index("notify"), order.index("close"))
        else:
            self.assertEqual(result.report["status"], "complete")
        return result, telemetry, error

    def test_actual_decoder_each_nonzero_word_and_all_faults_alert_before_close(self):
        result, decoded, error = self.simulate()
        self.assertIsNone(error)
        self.assertEqual(result.report["accepted_tx_bytes"], 10)
        self.assertEqual(len(decoded[0]["fields"]), 82)
        self.assertEqual(decoded[0]["evidence_kind"], "synthetic")
        for name, offset in live.POWERED_ZERO_FIELDS:
            self.assertEqual(decoded[0]["fields"][name]["raw_hex"], "0000")
            for raw in (b"\x01\x00", b"\x00\x80", b"\xff\xff"):
                payload = bytearray(134)
                payload[offset:offset + 2] = raw
                with self.subTest(name=name, raw=raw):
                    result, decoded, error = self.simulate(reply(bytes(payload)))
                    self.assertIsNotNone(error)
                    field = decoded[0]["fields"][name]
                    self.assertEqual(field["raw_hex"], raw.hex())
                    self.assertEqual(field["unsigned"], int.from_bytes(raw, "little"))
                    self.assertEqual(field["signed"], int.from_bytes(raw, "little", signed=True))
                    self.assertIn(bytes(payload), [e.stream.packet.payload for e in result.client.evidence
                                                  if e.stream.packet is not None])
        for data in (reply()[:-1], reply() + b"S", reply() + reply(), b"noise" + reply(),
                     reply(sequence=2305), reply(status=0x81), reply(bytes(133)), reply(command=1),
                     reply()[:-3] + b"\x00\x00E"):
            with self.subTest(data=data[:10]):
                self.assertIsNotNone(self.simulate(data)[2])
        for fault in ("open", "start_deadline", "identity", "write", "short", "deadline", "close"):
            with self.subTest(fault=fault):
                self.assertIsNotNone(self.simulate(fault=fault)[2])

    def test_late_session_failure_notifies_before_tail_and_seals_failed(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        order = []

        def fail(port, output, **kwargs):
            harness.capture(port, output, **kwargs)
            raise OSError("serial fault")

        original_sleep = harness.clock.sleep
        with patch.object(consent, "notify_cut_power", side_effect=lambda error: order.append("notify")), \
                patch.object(session.time, "sleep",
                             side_effect=lambda s: (order.append("wait"), original_sleep(s))[-1]), \
                self.assertRaises(OSError):
            harness.run_capture(**(self.session_options() | {"capture_runner": fail}))
        self.assertIn("notify", order)
        self.assertIn("wait", order[order.index("notify") + 1:])
        self.assertEqual(json.loads((harness.output / "metadata.json").read_text())["status"], "failed")

    def test_usb_recorder_metadata_and_drop_failures(self):
        harness = usbmon_tests.CaptureTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)

        def advance(*args):
            result = harness.select(*args)
            harness.now += 1e-9
            return result

        with patch.object(usbmon.select, "select", side_effect=advance), \
                harness.binary_monitor({"queued": 0, "dropped": 0}):
            result = harness.capture(**(self.usb_options() | {"actuators_isolated": False}))
        self.assertEqual(result["consent_profile"], "left_motor_powered_observation")
        self.assertFalse(result["motor_supply_off_acknowledged"])
        harness.output = harness.root / "dropped"
        with patch.object(usbmon.select, "select", side_effect=advance), \
                harness.binary_monitor({"queued": 0, "dropped": 1}), \
                redirect_stderr(io.StringIO()) as diagnostic, self.assertRaises(usbmon.CaptureError):
            harness.capture(**(self.usb_options() | {"actuators_isolated": False}))
        self.assertIn("CUT EXTERNAL POWER NOW", diagnostic.getvalue())
        self.assertEqual(harness.metadata()["status"], "failed")
        harness.output = harness.root / "close-failed"
        original_close = usbmon.os.close

        def close(fd):
            original_close(fd)
            if fd == harness.trace_fd:
                raise OSError("monitor close failed")

        with patch.object(usbmon.select, "select", side_effect=advance), \
                harness.binary_monitor({"queued": 0, "dropped": 0}), \
                patch.object(usbmon.os, "close", side_effect=close), \
                redirect_stderr(io.StringIO()) as diagnostic, self.assertRaises(OSError):
            harness.capture(**(self.usb_options() | {"actuators_isolated": False}))
        self.assertIn("CUT EXTERNAL POWER NOW", diagnostic.getvalue())
        self.assertEqual(harness.metadata()["status"], "failed")
        self.assertEqual(harness.metadata()["stop_reason"], "monitor_close_error")

    def test_live_startup_capture_validation_tail_cleanup_and_sealing_faults(self):
        for fault in ("none", "preflight", "constructor", "capture_consent", "tail", "clock",
                      "cleanup", "sealing", "session_status"):
            output = self.root / fault
            clock = Mock(offset=(1, 2))
            ingress = Mock(rx_consumed=144, rx_bytes=144, completed_tx=10, line_states=[])
            transport = Mock(token=b"synthetic")
            if fault == "tail":
                ingress.finish.side_effect = OSError("unmatched USB tail")
            if fault == "cleanup":
                ingress.close.side_effect = OSError("cleanup failed")
            if fault == "clock":
                clock.check.side_effect = OSError("clock failed")

            def coordinated(port, directory, **options):
                directory.mkdir()
                options.pop("expected_usb_identity")
                options.pop("ready_callback")
                runner = options.pop("capture_runner")
                for name in ("usbmon_backend", "usb_tail_seconds", "usb_close_grace_seconds",
                             "binary_payload_limit"):
                    options.pop(name)
                options.update(dtr=False, rts=False, bytesize=8, parity="N", stopbits=1,
                               line_state_at_open=True, allow_line_state_change=False,
                               allow_line_state_trial=False, allow_telemetry_state_change=False,
                               probe_delay=0, max_bytes=marvin_probe.MAX_CAPTURE_BYTES)
                if fault == "capture_consent":
                    options["motor_supply_off"] = True
                runner(port, directory / "serial", guard=lambda: None, **options)
                return {"status": "failed" if fault == "session_status" else "completed"}

            with patch.object(os, "geteuid", return_value=1000), \
                    patch.object(session, "preflight", return_value=session_tests.BASELINE,
                                 side_effect=OSError("preflight") if fault == "preflight" else None), \
                    patch.object(marvin_legacy_probe, "_validate_baseline"), \
                    patch.object(live, "IngressClock", return_value=clock), \
                    patch.object(live, "UsbIngress", return_value=ingress), \
                    patch.object(live, "LiveTransport", return_value=transport,
                                 side_effect=OSError("constructor") if fault == "constructor" else None) as constructor, \
                    patch.object(live, "collect", return_value=Mock(report={"status": "complete"})), \
                    patch.object(session, "run_session", side_effect=coordinated), \
                    patch.object(session, "evidence_manifest",
                                 side_effect=OSError("sealing") if fault == "sealing" else None), \
                    redirect_stderr(io.StringIO()) as diagnostic:
                if fault == "none":
                    result = live.run_live(output, expected_physical_port="1-3", run=True, **DECLARATIONS)
                    self.assertEqual(result["status"], "completed")
                else:
                    with self.assertRaises((OSError, ValueError)):
                        live.run_live(output, expected_physical_port="1-3", run=True, **DECLARATIONS)
                    self.assertIn("CUT EXTERNAL POWER NOW", diagnostic.getvalue())
                if fault == "capture_consent":
                    constructor.assert_not_called()
                    ingress.open.assert_not_called()
            if fault != "preflight":
                self.assertEqual(json.loads((output / "metadata.json").read_text())["status"],
                                 "completed" if fault == "none" else "failed")

    def test_native_host_pty_exact_one_open_write_read_close_with_synthetic_usb(self):
        master, slave = pty.openpty()
        tty = os.ttyname(slave)
        os.close(slave)
        self.addCleanup(os.close, master)
        baseline = {"tty": tty, "usb": {"busnum": 1, "devnum": 10}}
        path = self.root / "binary-events.bin"
        path.write_bytes(binary.FILE_MAGIC)
        clock = live.IngressClock()
        clock.start()
        self.addCleanup(clock.close)
        ingress = live.UsbIngress(path, baseline["usb"], clock)
        ingress.open()
        self.addCleanup(ingress.close)
        transport = live.LiveTransport(tty, baseline, self.root, ingress,
                                       guard=lambda: None, plan=live.powered_observation_plan())
        self.addCleanup(lambda: transport.close(deadline=time.monotonic() + 5))
        original_open, original_close, original_write = os.open, os.close, os.write
        original_ioctl = live.fcntl.ioctl
        opened, closed, writes, emulated, errors = [], [], [], [], []
        stop = threading.Event()

        def opening(path, *args, **kwargs):
            fd = original_open(path, *args, **kwargs)
            if str(path) == tty:
                opened.append(fd)
            return fd

        def closing(fd):
            if fd in opened:
                closed.append(fd)
            return original_close(fd)

        def writing(fd, data):
            if fd in opened:
                writes.append(data)
            return original_write(fd, data)

        def ioctl(fd, command, *args):
            if fd in opened and command in (termios.TIOCMBIC, termios.TIOCMGET):
                return bytes(4) if command == termios.TIOCMGET else 0
            return original_ioctl(fd, command, *args)

        def emulate():
            pending = bytearray()
            try:
                with path.open("ab", buffering=0) as evidence:
                    while not stop.is_set():
                        if not select.select([master], [], [], .01)[0]:
                            continue
                        try:
                            pending.extend(os.read(master, 512))
                        except OSError:
                            time.sleep(.005)
                            continue
                        if len(pending) < 10:
                            continue
                        self.assertEqual(bytes(pending), REQUEST)
                        emulated.append(bytes(pending))
                        stamp = time.time()
                        evidence.write(event(REQUEST, stamp=stamp) +
                                       event(event="C", status=0, data_flag=ord(">"), length=10, stamp=stamp))
                        time.sleep(.02)
                        evidence.write(incoming(reply(), stamp=time.time()))
                        original_write(master, reply())
                        return
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=emulate)
        telemetry = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(session, "preflight", return_value=baseline))
            stack.enter_context(patch.object(session, "check_identity"))
            stack.enter_context(patch.object(live.subprocess, "run", return_value=Mock(
                returncode=0, stdout=str(os.getpid()), stderr="")))
            stack.enter_context(patch.object(os, "open", side_effect=opening))
            stack.enter_context(patch.object(os, "close", side_effect=closing))
            stack.enter_context(patch.object(os, "write", side_effect=writing))
            stack.enter_context(patch.object(live.fcntl, "ioctl", side_effect=ioctl))
            stack.enter_context(patch.object(termios, "tcflush", side_effect=AssertionError("no flush")))
            worker.start()
            try:
                result = poll.collect(
                    transport, self.root / "poll.jsonl", ownership_key=b"host-pty-software-only",
                    expected_identity=transport.token, plan=live.powered_observation_plan(),
                    evidence_kind="synthetic", wait=transport.wait, on_failure=consent.notify_cut_power,
                    on_evidence=lambda events: live.inspect_powered_evidence(events, telemetry))
                with self.assertRaises(OSError):
                    transport.write(REQUEST, deadline=time.monotonic() + 3)
                with self.assertRaises(OSError):
                    transport.revalidate(deadline=time.monotonic() + 3)
            finally:
                stop.set()
                worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        ingress.finish(10)
        self.assertEqual(writes, [REQUEST])
        self.assertEqual(emulated, writes)
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, opened)
        self.assertEqual(result.report["status"], "complete")
        self.assertEqual(ingress.rx_consumed, 144)
        self.assertEqual(len(telemetry), 1)
        print("HOST_PTY powered observation SOFTWARE ONLY: opens=1 writes=1 TX=10 RX=144 closes=1; "
              "synthetic USB and identity, no robot access or physical permission")


if __name__ == "__main__":
    unittest.main()
