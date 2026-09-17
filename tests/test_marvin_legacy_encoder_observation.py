"""Software only: synthetic USB/identity and actual retained payloads; native host PTY."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import pty
import select
import struct
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tools import marvin_legacy_live as live
from tools import marvin_legacy_poll as poll
from tools import marvin_legacy_probe
from tools import marvin_legacy_recording as recording
from tools import marvin_motor_power_off_consent as consent
from tools import marvin_probe
from tools import marvin_session as session
from tools import marvin_usbmon as usbmon
from tools import marvin_usbmon_binary as binary
from tests.test_marvin_legacy_client import Clock, FakeTransport, frame
from tests.test_marvin_legacy_live import event, incoming
from tests import test_marvin_session as session_tests
from tests import test_marvin_usbmon as usbmon_tests


DECLARATIONS = dict.fromkeys(consent.ENCODER_FLAGS, True)
REQUESTS = [live.read_raw_data_request(sequence) for sequence in range(2560, 2580)]
FLAGS = ["--" + name.replace("_", "-") for name in consent.ENCODER_FLAGS]


def reply(sequence):
    payload = bytearray(134)
    index = sequence - 2560
    struct.pack_into("<IIHH", payload, 64, (0x7fffffff + index) & 0xffffffff,
                     (0xffffffff + index) & 0xffffffff, (0xfff0 + index) & 0xffff, 0x8000 + index)
    struct.pack_into("<HHHH", payload, 90, 1, 0xffff, 0x8000, index)
    return frame(bytes(payload), sequence=sequence, command=0)


class EncoderObservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".encoder-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def session_options(self):
        return dict(seconds=35, baudrate=57600, actuators_isolated=False,
                    allow_unknown_command=True, probe_profile="legacy", capture_runner=Mock(),
                    binary_payload_limit=4096, usb_tail_seconds=5, usb_close_grace_seconds=5,
                    probe_schedule=tuple(marvin_probe.ScheduledWrite(i, raw, "legacy-read-raw-data")
                                         for i, raw in enumerate(REQUESTS)), **DECLARATIONS)

    def usb_options(self):
        return dict(seconds=45, backend="binary", coordinator_stop=True,
                    binary_payload_limit=4096, max_line_bytes=16384, **DECLARATIONS)

    def test_fixed_plan_cli_and_all_literal_scope_boundaries(self):
        with patch.object(os, "geteuid", return_value=1000), \
                patch.object(session, "preflight", side_effect=AssertionError("no device preflight")), \
                patch.object(usbmon, "read_identity", side_effect=AssertionError("no device identity")), \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(live.main(FLAGS), 0)
            dry = json.loads(output.getvalue())
            self.assertEqual(dry["immutable_application_transcript_hex"], [r.hex() for r in REQUESTS])
            self.assertEqual(REQUESTS[0].hex(), "53000a00000000fb0445")
            self.assertEqual(REQUESTS[-1].hex(), "53130a00000000f9a745")
            self.assertEqual(dry["maximum_application_bytes"], 200)
            self.assertEqual((dry["usb_nominal_seconds"], dry["usb_hard_seconds"]), (40, 45))
            self.assertFalse(dry["motor_supply_off_acknowledged"])
            self.assertFalse(dry["actuator_power_and_signal_isolation_acknowledged"])
            for name, value in (("max-requests", "20"), ("interval", "1"), ("duration", "35"),
                                ("first-sequence", "2560")):
                self.assertEqual(live.main(FLAGS + ["--" + name, value]), 1)
            for flag in FLAGS:
                self.assertEqual(live.main([item for item in FLAGS if item != flag]), 1)
            changes = [{name: value} for name in consent.ENCODER_FLAGS
                       for value in (False, 1, 0, "true", None, [], {})]
            changes += [{name: value} for name in (
                "actuators_isolated", "motor_supply_off", "motor_left_only_connected",
                "motor_right_and_servos_isolated", "left_motor_powered_observation",
                "operator_at_external_cutoff", "authorize_unvalidated_zero_velocity", "new_boot_declared")
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
            for change in ({"max_requests": 19}, {"first_sequence": 2561}, {"duration": 36},
                           {"cleanup_timeout": 6}, {"max_rx_bytes": 9000}, {"request_timeout": .4}):
                with self.assertRaises(ValueError):
                    live.run_live(self.root / "unused", expected_physical_port="1-3", run=True,
                                  plan=replace(live.encoder_observation_plan(), **change), **DECLARATIONS)
            with self.assertRaises(ValueError):
                live.run_live(self.root / "unused", expected_physical_port="1-3", **DECLARATIONS)
            for change in ({"seconds": 36}, {"probe_schedule": ()}, {"sudo_usbmon": True},
                           {"dtr": True}, {"allow_line_state_change": True}, {"baudrate": 9600},
                           {"_isolated_zero_velocity": True}, {"_motor_power_off_preparation": True},
                           {"binary_payload_limit": 32}, {"capture_runner": None}):
                with self.assertRaises(ValueError):
                    session.run_session("mock", self.root / "unused", **(self.session_options() | change))
            for change in ({"seconds": 46}, {"max_bytes": 1048577}, {"max_records": 10001},
                           {"backend": "text"}, {"coordinator_stop": False}, {"max_pending": 100},
                           {"drop_to_invoking_user": True}):
                with self.assertRaises(usbmon.UsbmonError):
                    usbmon.capture("mock", self.root / "unused", **(self.usb_options() | change))
        self.assertEqual(live.live_plan().max_requests, 5)
        with self.assertRaises(ValueError):
            live.live_plan(max_requests=20, duration=35)
        self.assertEqual(live.powered_observation_plan().first_sequence, 2304)
        self.assertIs(consent.validate(actuators_isolated=True), False)
        self.assertIs(consent.validate(**dict.fromkeys(consent.PREPARATION_FLAGS, True)), True)

    def test_coordinator_capture_and_usb_subprocess_truthful(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        runner = Mock(side_effect=harness.capture)
        result = harness.run_capture(**(self.session_options() | {"capture_runner": runner}))
        command = harness.popen.call_args.args[0]
        for flag in FLAGS:
            self.assertIn(flag, command)
        for flag in ("--actuators-isolated", "--motor-supply-off", "--left-motor-powered-observation", "sudo"):
            self.assertNotIn(flag, command)
        self.assertIsNone(harness.popen.call_args.kwargs["stderr"])
        for name in consent.ENCODER_FLAGS:
            self.assertIs(runner.call_args.kwargs[name], True)
        for name in ("actuators_isolated", "motor_supply_off", "left_motor_powered_observation"):
            self.assertIs(runner.call_args.kwargs[name], False)
        self.assertEqual(result["requested_application_bytes"], 200)
        self.assertEqual((result["usb_nominal_duration_seconds"], result["usb_duration_seconds"]), (40, 45))
        self.assertEqual(result["scope"], "encoder_feedback_observation")
        self.assertFalse(result["motor_supply_off_acknowledged"])
        self.assertFalse(result["actuator_power_and_signal_isolation_acknowledged"])
        self.assertIn("unflushed raw tty", result["limitations"][2])
        with patch.object(usbmon, "capture", return_value={"status": "completed"}) as capture, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(usbmon.main(command[2:]), 0)
        for name in consent.ENCODER_FLAGS:
            self.assertIs(capture.call_args.kwargs[name], True)
        self.assertIs(capture.call_args.kwargs["actuators_isolated"], False)
        self.assertIs(capture.call_args.kwargs["motor_supply_off"], False)

    def simulate(self, *, fault=None):
        clock = Clock()
        transport = FakeTransport(clock)
        observer = live.EncoderObservation([])
        path = self.root / f"poll-{len(list(self.root.iterdir()))}.jsonl"
        order = []
        original_close = transport.close
        transport.close = lambda **kw: (order.append("close"), original_close(**kw))[-1]

        def send(packet):
            data = reply(packet.sequence)
            if packet.sequence == 2562:
                if fault == "partial":
                    data = data[:-1]
                elif fault == "unsolicited":
                    data += data
                elif fault == "corrupt":
                    data = data[:-3] + b"\0\0E"
                elif fault == "shape":
                    data = frame(bytes(133), sequence=packet.sequence, command=0)
                elif fault == "status":
                    data = frame(bytes(134), sequence=packet.sequence, command=0, status=0x81)
                elif fault == "unknown":
                    data = frame(bytes(134), sequence=packet.sequence, command=1)
                elif fault == "late":
                    transport.enqueue(data, at=clock.now + 1)
                    return
                elif fault == "short":
                    transport.count = 3
                elif fault == "write":
                    raise OSError("uncertain write")
            transport.enqueue(data)

        transport.on_write = send
        if fault == "deadline":
            transport.revalidate = lambda **kw: (setattr(clock, "now", clock.now + 35), transport.token)[-1]
        if fault == "close":
            transport.on_close = Mock(side_effect=OSError("close failure"))
        test = self

        class CheckedRecorder(recording.Recorder):
            def append(self, row):
                if fault == "append" and row["type"] == "evidence":
                    raise OSError("append failed")
                super().append(row)
                if row["type"] == "evidence":
                    order.append("persist")

            def finish(self, report):
                if fault == "seal":
                    raise OSError("seal failed")
                super().finish(report)

        def persisted(events, deadline, now):
            # Inspect the actual unbuffered journal, not a proxy call-order mock.
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            evidence = [row for row in rows if row["type"] == "evidence"]
            test.assertEqual(len(evidence), len(events))
            test.assertEqual(evidence[-1], recording.evidence_row(len(events) - 1, events[-1]))
            test.assertEqual(order[-1], "persist")
            order.append("marker")
            observer.persisted(events, deadline, now)

        def end(error=None):
            if not observer.ended:
                order.append("end")
            observer.end(error)

        with redirect_stderr(io.StringIO()) as diagnostics:
            try:
                result = poll.collect(
                    transport, path, ownership_key=b"synthetic-encoder", expected_identity=transport.token,
                    plan=live.encoder_observation_plan(), evidence_kind="synthetic",
                    clock=clock, wait=lambda seconds: setattr(clock, "now", clock.now + seconds),
                    recorder_factory=CheckedRecorder, on_evidence=observer.inspect, on_failure=end,
                    on_sample_persisted=persisted, on_collection_ended=end)
                error = None
            except poll.CollectionError as caught:
                result, error = caught.result, caught
        markers = [line for line in diagnostics.getvalue().splitlines()
                   if line.startswith(("BASELINE_READY ", "SAMPLE_PROGRESS "))]
        self.assertLessEqual(len(markers), 20)
        self.assertEqual(transport.writes, REQUESTS[:len(transport.writes)])
        self.assertLess(order.index("end"), order.index("close"))
        self.assertEqual(diagnostics.getvalue().count("COLLECTION_ENDED"), 1)
        return result, error, observer, markers, transport, path

    def test_twenty_actual_nonzero_samples_signed_wrap_and_persisted_markers(self):
        result, error, observer, markers, transport, path = self.simulate()
        self.assertIsNone(error)
        self.assertEqual(transport.writes, REQUESTS)
        self.assertEqual(result.report["accepted_tx_bytes"], 200)
        self.assertEqual(len(markers), 20)
        self.assertTrue(markers[0].startswith("BASELINE_READY "))
        first = json.loads(markers[0].split(" ", 1)[1])
        second = json.loads(markers[1].split(" ", 1)[1])
        self.assertEqual(first["positions"]["motorPositionR"], {"raw_hex": "ffffffff",
                                                              "int32": -1, "uint32": 4294967295})
        self.assertEqual(second["positions"]["motorPositionL"]["int32"], -2147483648)
        self.assertEqual(second["positions"]["motorPositionR"]["uint32"], 0)
        for index, decoded in enumerate(observer.telemetry):
            self.assertEqual(len(decoded["fields"]), 82)
            self.assertEqual(decoded["fields"]["motorVelocityL"]["unsigned"], (0xfff0 + index) & 0xffff)
            self.assertEqual(decoded["fields"]["motorPwmLeftForward"]["unsigned"], 1)
            self.assertEqual(decoded["evidence_kind"], "synthetic")
        self.assertEqual(len(observer.telemetry), 20)
        self.assertTrue(result.report["recording_sealed"])

    def test_fault_prefix_no_append_failure_baseline_and_late_failures_remain_failed(self):
        for fault in ("append", "partial", "corrupt", "shape", "status", "unknown", "unsolicited", "late",
                      "short", "write", "deadline", "close", "seal"):
            with self.subTest(fault=fault):
                result, error, observer, markers, transport, path = self.simulate(fault=fault)
                self.assertIsNotNone(error)
                self.assertEqual(result.report["status"], "failed")
                if fault in ("append", "deadline"):
                    self.assertEqual(markers, [])
                elif fault not in ("close", "seal"):
                    self.assertEqual(len(transport.writes), 3)
                    self.assertEqual(len(markers), 2)
                if fault == "shape":
                    self.assertEqual(len(observer.telemetry), 3)
                    self.assertEqual(observer.telemetry[-1]["reason"], "unknown_payload_size")
                    self.assertIn(reply(2560).hex(), path.read_text())

    def test_live_startup_capture_boundary_tail_cleanup_and_sealing(self):
        for fault in ("none", "preflight", "constructor", "capture_consent", "tail", "cleanup", "sealing"):
            output = self.root / fault
            ingress = Mock(rx_consumed=2880, rx_bytes=2880, completed_tx=200, line_states=[])
            transport = Mock(token=b"synthetic")
            if fault == "tail":
                ingress.finish.side_effect = OSError("unmatched USB tail")
            if fault == "cleanup":
                ingress.close.side_effect = OSError("cleanup failed")
            order = []

            def coordinated(port, directory, **options):
                directory.mkdir()
                runner = options.pop("capture_runner")
                for name in ("expected_usb_identity", "ready_callback", "usbmon_backend", "usb_tail_seconds",
                             "usb_close_grace_seconds", "binary_payload_limit"):
                    options.pop(name)
                options.update(dtr=False, rts=False, bytesize=8, parity="N", stopbits=1,
                               line_state_at_open=True, allow_line_state_change=False,
                               allow_line_state_trial=False, allow_telemetry_state_change=False,
                               probe_delay=0, max_bytes=marvin_probe.MAX_CAPTURE_BYTES)
                if fault == "capture_consent":
                    options["motor_supply_off"] = True
                runner(port, directory / "serial", guard=lambda: None, **options)
                order.append("tail")
                return {"status": "completed"}

            def collection(*args, **kwargs):
                kwargs["on_collection_ended"](None)
                order.append("end")
                return Mock(report={"status": "complete"})

            with patch.object(os, "geteuid", return_value=1000), \
                    patch.object(session, "preflight", return_value=session_tests.BASELINE,
                                 side_effect=OSError("preflight") if fault == "preflight" else None), \
                    patch.object(marvin_legacy_probe, "_validate_baseline"), \
                    patch.object(live, "IngressClock", return_value=Mock(offset=(1, 2))), \
                    patch.object(live, "UsbIngress", return_value=ingress), \
                    patch.object(live, "LiveTransport", return_value=transport,
                                 side_effect=OSError("constructor") if fault == "constructor" else None) as constructor, \
                    patch.object(live, "collect", side_effect=collection), \
                    patch.object(session, "run_session", side_effect=coordinated), \
                    patch.object(session, "evidence_manifest",
                                 side_effect=OSError("sealing") if fault == "sealing" else None), \
                    redirect_stderr(io.StringIO()) as diagnostics:
                if fault == "none":
                    result = live.run_live(output, expected_physical_port="1-3", run=True, **DECLARATIONS)
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(order, ["end", "tail"])
                else:
                    with self.assertRaises((OSError, ValueError)):
                        live.run_live(output, expected_physical_port="1-3", run=True, **DECLARATIONS)
                self.assertIn("COLLECTION_ENDED", diagnostics.getvalue())
                self.assertNotIn("BASELINE_READY", diagnostics.getvalue())
                self.assertNotIn("CUT EXTERNAL POWER", diagnostics.getvalue())
                if fault == "capture_consent":
                    constructor.assert_not_called()
                    ingress.open.assert_not_called()
            if fault != "preflight":
                self.assertEqual(json.loads((output / "metadata.json").read_text())["status"],
                                 "completed" if fault == "none" else "failed")

    def test_constructor_fault_notifies_before_coordinator_drain(self):
        harness = session_tests.SessionTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        order = []
        original_sleep = harness.clock.sleep

        def fail(*args, **kwargs):
            raise OSError("constructor before collector")

        with patch.object(consent, "notify_collection_ended", side_effect=lambda error: order.append("end")), \
                patch.object(session.time, "sleep",
                             side_effect=lambda seconds: (order.append("wait"), original_sleep(seconds))[-1]), \
                self.assertRaises(OSError):
            harness.run_capture(**(self.session_options() | {"capture_runner": fail}))
        self.assertIn("wait", order[order.index("end") + 1:])
        self.assertEqual(json.loads((harness.output / "metadata.json").read_text())["status"], "failed")

    def test_usb_recorder_full_tail_drop_and_close_failure(self):
        harness = usbmon_tests.CaptureTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)

        def advance(*args):
            result = harness.select(*args)
            harness.now += 1e-9
            return result

        for fault in ("none", "drop", "close"):
            harness.output = harness.root / fault
            original_close = usbmon.os.close

            def close(fd):
                original_close(fd)
                if fault == "close" and fd == harness.trace_fd:
                    raise OSError("monitor close failure")

            with patch.object(usbmon.select, "select", side_effect=advance), \
                    harness.binary_monitor({"queued": 0, "dropped": int(fault == "drop")}), \
                    patch.object(usbmon.os, "close", side_effect=close), redirect_stderr(io.StringIO()):
                if fault == "none":
                    result = harness.capture(**(self.usb_options() | {"actuators_isolated": False}))
                    self.assertEqual(result["consent_profile"], "encoder_feedback_observation")
                    self.assertFalse(result["motor_supply_off_acknowledged"])
                else:
                    with self.assertRaises((OSError, usbmon.CaptureError)):
                        harness.capture(**(self.usb_options() | {"actuators_isolated": False}))
                    self.assertEqual(harness.metadata()["status"], "failed")

    def test_native_host_pty_twenty_exact_writes_changing_payload_synthetic_usb(self):
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
                                       guard=lambda: None, plan=live.encoder_observation_plan())
        self.addCleanup(lambda: transport.close(deadline=time.monotonic() + 5))
        original_open, original_close, original_write, original_ioctl = os.open, os.close, os.write, live.fcntl.ioctl
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
                    while not stop.is_set() and len(emulated) < 20:
                        if not select.select([master], [], [], .01)[0]:
                            continue
                        try:
                            pending.extend(os.read(master, 512))
                        except OSError:
                            time.sleep(.005)
                            continue
                        if len(pending) < 10:
                            continue
                        index = len(emulated)
                        raw = bytes(pending[:10])
                        del pending[:10]
                        self.assertEqual(raw, REQUESTS[index])
                        emulated.append(raw)
                        stamp = time.time()
                        evidence.write(event(raw, stamp=stamp, urb=2 * index + 1) +
                                       event(event="C", status=0, data_flag=ord(">"), length=10,
                                             stamp=stamp, urb=2 * index + 1))
                        time.sleep(.02)
                        data = reply(2560 + index)
                        evidence.write(incoming(data, stamp=time.time(), urb=2 * index + 2))
                        original_write(master, data)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=emulate)
        observer = live.EncoderObservation([])
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
            diagnostics = stack.enter_context(redirect_stderr(io.StringIO()))
            worker.start()
            try:
                result = poll.collect(
                    transport, self.root / "poll.jsonl", ownership_key=b"host-pty-encoder-software-only",
                    expected_identity=transport.token, plan=live.encoder_observation_plan(),
                    evidence_kind="synthetic", wait=transport.wait, on_failure=observer.end,
                    on_evidence=observer.inspect, on_sample_persisted=observer.persisted,
                    on_collection_ended=observer.end)
            finally:
                stop.set()
                worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        ingress.finish(200)
        self.assertEqual(writes, REQUESTS)
        self.assertEqual(emulated, writes)
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, opened)
        self.assertEqual(result.report["status"], "complete")
        self.assertEqual(ingress.rx_consumed, 2880)
        self.assertEqual(len(observer.telemetry), 20)
        self.assertEqual(diagnostics.getvalue().count("BASELINE_READY"), 1)
        self.assertEqual(diagnostics.getvalue().count("SAMPLE_PROGRESS"), 19)
        self.assertEqual(diagnostics.getvalue().count("COLLECTION_ENDED"), 1)
        print("HOST_PTY encoder SOFTWARE ONLY: opens=1 writes=20 TX=200 RX=2880 closes=1; "
              "changing actual payloads; synthetic USB/identity/modem readback, no robot access")


if __name__ == "__main__":
    unittest.main()
