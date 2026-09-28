"""One real-HTTP lifecycle check for the local continuous operator runtime."""

import json
import io
import os
from pathlib import Path
import socket
import struct
import tempfile
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import subprocess

import marvin
import marvin_dashboard
import marvin_operator


class Source:
    def __init__(self):
        self.reads = 0
        self.closed = False

    def start(self):
        return None

    def read(self):
        self.reads += 1
        fields = {
            f"raw{i}": {
                "offset": i, "size": 1, "raw_hex": f"{i % 256:02x}",
                "unsigned": i, "source_label": f"raw{i}",
            }
            for i in range(82)
        }
        return {
            "schema_version": 1,
            "status": "ready",
            "raw_telemetry": {"status": "decoded", "fields": fields},
            "proximity": {"proximity1": {"mapping_confidence": "decisive"}},
            "cliff": {f"cliff{i}": {"physical_mapping": "unresolved"}
                      for i in range(1, 6)},
            "motors": {"motorPositionL": fields["raw0"]},
            "servos": {"units": "unknown_raw_uint16"},
            "battery": {"status": "unsupported"},
            "bump": {"status": "unsupported"},
        }

    def close(self):
        self.closed = True


class Manager:
    def __init__(self, error=None):
        self.error = error

    def start(self):
        return None

    def status(self):
        return {"error": self.error}

    def close(self):
        return None


class SlowSource(Source):
    startup_timeout = 0.02

    def __init__(self, *, fail=False, delay=0.05):
        super().__init__()
        self.fail = fail
        self.delay = delay

    def start(self):
        time.sleep(self.delay)
        if self.fail:
            raise OSError("deliberate startup failure")


class OperatorTests(unittest.TestCase):
    def test_installed_cli_bootstraps_production_owner(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                marvin_operator, "serve_live") as live:
            os.chmod(directory, 0o700)
            arguments = [
                "operator", "serve", "--run", "--port", "0",
                "--expected-physical-port", "1-3",
                "--evidence-root", directory,
                "--unprivileged-usbmon",
                "--authorize-unvalidated-drive-step",
                *("--" + name.replace("_", "-")
                  for name in marvin_operator.drive_step.COMMON_FLAGS
                  if name != "unprivileged_usbmon"),
            ]
            self.assertEqual(marvin.main(arguments), 0)
            live.assert_called_once()
            options = live.call_args.kwargs
            self.assertEqual(options["expected_physical_port"], "1-3")
            self.assertTrue(all(options["drive_declarations"].values()))

    def test_production_bootstrap_uses_sustainable_session_and_reserves(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                marvin_operator.zero, "_run_diagnostic",
                return_value={"status": "planned"}) as run:
            os.chmod(directory, 0o700)
            declarations = {
                "authorize_unvalidated_drive_step": True,
                **dict.fromkeys(
                    marvin_operator.drive_step.COMMON_FLAGS, True),
            }
            result = marvin_operator.serve_live(
                port=0, poll_seconds=2, chunk_seconds=300,
                expected_physical_port="1-3", evidence_root=directory,
                configuration={}, drive_declarations=declarations)
            self.assertEqual(result["status"], "planned")
            options = run.call_args.kwargs
            self.assertEqual(
                options["serial_seconds"],
                marvin_operator.OPERATOR_SESSION_SECONDS
                + marvin_operator.OPERATOR_CLEANUP_RESERVE_SECONDS)
            self.assertEqual(
                options["usb_max_bytes"],
                marvin_operator.OPERATOR_USB_MAX_BYTES)
            self.assertEqual(
                options["usb_max_records"],
                marvin_operator.OPERATOR_USB_MAX_RECORDS)
            limits = options["limits"]
            self.assertEqual(limits.max_requests, marvin_operator.MAX_REQUESTS)
            self.assertEqual(
                limits.max_journal_bytes,
                marvin_operator.OPERATOR_JOURNAL_MAX_BYTES)
            self.assertEqual(
                limits.journal_reserve_records,
                marvin_operator.OPERATOR_JOURNAL_RESERVE_RECORDS)
            review = options["review"]
            self.assertEqual(
                review["cleanup_request_reserve"],
                marvin_operator.OPERATOR_CLEANUP_REQUESTS)
            self.assertEqual(
                review["max_adapter_journal_records"],
                marvin_operator.OPERATOR_JOURNAL_MAX_RECORDS)
            session = options["session_options"]
            self.assertFalse(session["actuators_isolated"])
            self.assertTrue(session["operator_drive_authorized"])
            self.assertEqual(
                session["operator_application_byte_budget"],
                marvin_operator.OPERATOR_MAX_APPLICATION_BYTES)
            self.assertTrue(all(
                session[name]
                for name in marvin_operator.drive_step.COMMON_FLAGS))
        self.assertLessEqual(
            marvin_operator.OPERATOR_SESSION_SECONDS
            * (len(marvin_operator.marvin_sensors.QUERIES)
               / marvin_operator.OPERATOR_MIN_LIVE_POLL_SECONDS),
            marvin_operator.MAX_REQUESTS
            - marvin_operator.OPERATOR_CLEANUP_REQUESTS)
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            with self.assertRaisesRegex(ValueError, "at least 2 seconds"):
                marvin_operator.serve_live(
                    port=0, poll_seconds=0.5, chunk_seconds=300,
                    expected_physical_port="1-3", evidence_root=directory,
                    configuration={}, drive_declarations=declarations)

    def test_live_observer_refuses_success_after_runtime_failure(self):
        declarations = {
            "authorize_unvalidated_drive_step": True,
            **dict.fromkeys(marvin_operator.drive_step.COMMON_FLAGS, True),
        }
        report = {
            "status": "not_started", "uncertain_tx_bytes": 0,
            "accepted_tx_bytes": 0, "responses": 0,
        }

        def diagnostic(*_args, **options):
            options["observe"](SimpleNamespace(token=b"identity"), report)

        def fail_runtime(runtime, **_options):
            runtime._state = "failed"
            runtime._last_error = "mandatory stop failed"

        with tempfile.TemporaryDirectory() as directory, patch.object(
                marvin_operator.zero, "_run_diagnostic",
                side_effect=diagnostic), patch.object(
                    marvin_operator, "serve", side_effect=fail_runtime):
            os.chmod(directory, 0o700)
            with self.assertRaisesRegex(
                    OSError, "failed: mandatory stop failed"):
                marvin_operator.serve_live(
                    port=0, poll_seconds=2, chunk_seconds=300,
                    expected_physical_port="1-3", evidence_root=directory,
                    configuration={}, drive_declarations=declarations)
        self.assertNotEqual(report["status"], "operator_shutdown_complete")

    def test_operator_journal_and_sequence_reserve_mandatory_cleanup(self):
        limits = marvin_operator.zero._Limits(
            max_journal_bytes=4096, max_journal_records=3,
            journal_reserve_bytes=512, journal_reserve_records=1)
        transport = marvin_operator._OperatorTransport(
            "unused", {}, ".", None, guard=lambda: None, plan=limits)
        transport.journal = io.BytesIO()
        transport.event("normal_one")
        transport.event("normal_two")
        with self.assertRaisesRegex(OSError, "journal budget exhausted"):
            transport.event("normal_three")
        transport.begin_cleanup_reserve()
        transport.event("mandatory_zero")
        transport.end_cleanup_reserve()
        self.assertEqual(transport.journal_records, 3)

        owner = marvin_operator.ProductionControllerOwner(
            SimpleNamespace(token=b"identity"), {})
        owner.sequence = 65536 - marvin_operator.OPERATOR_CLEANUP_REQUESTS
        with self.assertRaisesRegex(OSError, "sequence budget exhausted"):
            owner._next_sequence()
        budget_status = owner.budget_status()
        self.assertEqual(budget_status["normal_requests_remaining"], 0)
        self.assertEqual(
            budget_status["total_requests_remaining"],
            marvin_operator.OPERATOR_CLEANUP_REQUESTS)
        self.assertFalse(
            budget_status["continuous_drive_duration_guaranteed"])
        self.assertEqual(
            owner._next_sequence(mandatory=True),
            65536 - marvin_operator.OPERATOR_CLEANUP_REQUESTS)

    def test_mandatory_zero_writes_despite_guard_deadline_and_dirty_evidence(self):
        left, right = socket.socketpair()
        node = f"/proc/self/fd/{left.fileno()}"
        ingress = SimpleNamespace(
            pending=[b"partial"], rx=bytearray(b"unconsumed"),
            rx_bytes=10, expected_tx=[b"older"], outstanding_tx=[b"pending"],
            clock=SimpleNamespace(
                check=lambda: (_ for _ in ()).throw(
                    OSError("session clock expired"))))
        transport = marvin_operator._OperatorTransport(
            "unused", {"tty": node}, ".", ingress,
            guard=lambda: (_ for _ in ()).throw(
                OSError("USB recorder stopped")),
            plan=marvin_operator.zero._Limits())
        transport.fd = left.fileno()
        transport.opened = True
        info = os.fstat(transport.fd)
        transport.node_stat = (info.st_dev, info.st_ino, info.st_rdev)
        transport.journal = io.BytesIO()
        right.sendall(b"late setter response")
        raw = marvin_operator._frame(
            65535, marvin_operator.RAW_PWM_COMMAND,
            marvin_operator.pilot.ZERO_PWM)
        try:
            with patch.object(
                    marvin_operator.marvin_session, "check_identity"), \
                    self.assertRaisesRegex(
                        OSError, "Mandatory zero was attempted") as raised:
                transport.submit(raw, deadline=0, mandatory=True)
            self.assertTrue(raised.exception.mandatory_zero_attempted)
            self.assertIn("USB recorder stopped", str(raised.exception))
            self.assertIn("session clock expired", str(raised.exception))
            self.assertEqual(right.recv(len(raw)), raw)
            self.assertEqual(transport.last_write_sequence, 65535)
        finally:
            left.close()
            right.close()

    def test_mandatory_zero_blocks_changed_pinned_identity(self):
        left, right = socket.socketpair()
        node = f"/proc/self/fd/{left.fileno()}"
        ingress = SimpleNamespace(
            pending=[], rx=bytearray(), rx_bytes=0,
            expected_tx=[], outstanding_tx=[],
            clock=SimpleNamespace(check=lambda: None))
        transport = marvin_operator._OperatorTransport(
            "unused", {"tty": node}, ".", ingress,
            guard=lambda: None, plan=marvin_operator.zero._Limits())
        transport.fd = left.fileno()
        transport.opened = True
        info = os.fstat(transport.fd)
        transport.node_stat = (info.st_dev, info.st_ino, info.st_rdev)
        transport.journal = io.BytesIO()
        raw = marvin_operator._frame(
            65535, marvin_operator.RAW_PWM_COMMAND,
            marvin_operator.pilot.ZERO_PWM)
        right.settimeout(0.05)
        try:
            with patch.object(
                    marvin_operator.marvin_session, "check_identity",
                    side_effect=OSError("pinned USB identity changed")), \
                    self.assertRaisesRegex(OSError, "identity changed"):
                transport.submit(raw, deadline=0, mandatory=True)
            with self.assertRaises(socket.timeout):
                right.recv(len(raw))
        finally:
            left.close()
            right.close()

    def test_drive_uses_accepted_response_window_hold_and_cleanup_bound(self):
        class Transport:
            token = b"identity"
            last_write_started = None

            def __init__(self):
                self.fd, self.writer = os.pipe()
                self.submissions = []
                self.cleanup_reserve = False
                self.ingress = SimpleNamespace(pump=lambda: None)

            def submit(self, raw, *, deadline, mandatory=False):
                request = marvin_operator.protocol.decode_packet(raw)
                self.last_write_started = time.monotonic()
                self.last_write_sequence = request.sequence
                self.submissions.append(
                    (request.payload, self.last_write_started, mandatory))
                body = b"S" + struct.pack(
                    "<HBBH", request.sequence, request.command, 0x80, 0)
                os.write(
                    self.writer,
                    body
                    + struct.pack(
                        "<H", marvin_operator.protocol.crc16(body))
                    + b"E")
                return len(raw)

            def read_response(self, _size, *, deadline):
                started = time.monotonic()
                data = os.read(self.fd, 512)
                return SimpleNamespace(
                    data=data, started_at=started,
                    ended_at=time.monotonic())

            def event(self, _name, **_fields):
                return None

            def wait(self, seconds):
                time.sleep(seconds)

            def begin_cleanup_reserve(self):
                self.cleanup_reserve = True

            def end_cleanup_reserve(self):
                self.cleanup_reserve = False

            def close(self):
                os.close(self.fd)
                os.close(self.writer)

        transport = Transport()
        owner = marvin_operator.ProductionControllerOwner(
            transport,
            {"uncertain_tx_bytes": 0, "accepted_tx_bytes": 0,
             "responses": 0},
            operation_deadline=time.monotonic() + 10)
        owner.started = True
        try:
            owner.drive_step("forward")
        finally:
            transport.close()
        self.assertEqual(len(transport.submissions), 3)
        prezero, nonzero, cleanup = transport.submissions
        self.assertEqual(prezero[0], marvin_operator.pilot.ZERO_PWM)
        self.assertNotEqual(nonzero[0], marvin_operator.pilot.ZERO_PWM)
        self.assertEqual(cleanup[0], marvin_operator.pilot.ZERO_PWM)
        self.assertFalse(prezero[2])
        self.assertFalse(nonzero[2])
        self.assertTrue(cleanup[2])
        hold_before_cleanup = cleanup[1] - nonzero[1]
        self.assertGreaterEqual(
            hold_before_cleanup,
            marvin_operator.pilot.RESPONSE_SECONDS
            + marvin_operator.drive_step.DURATION_SECONDS)
        self.assertLessEqual(
            hold_before_cleanup,
            marvin_operator.pilot.QUARTER_SECOND_CLEANUP_BOUND_SECONDS)

    def test_real_owner_accepts_exact_coordinator_report_shape(self):
        class Transport:
            token = b"identity"
            last_write_started = None
            reads = 0
            waits = []

            def revalidate(self, *, deadline):
                return self.token

            def wait(self, seconds):
                self.waits.append(seconds)

            def submit(self, raw, *, deadline):
                if not self.waits:
                    raise AssertionError("startup request preceded the quiet window")
                request = marvin_operator.protocol.decode_packet(raw)
                self.assert_command = request.command
                payload = bytes(18)
                body = (
                    b"S"
                    + struct.pack(
                        "<HBBH", request.sequence, request.command, 0x80,
                        len(payload))
                    + payload)
                self.response = (
                    body
                    + struct.pack(
                        "<H", marvin_operator.protocol.crc16(body))
                    + b"E")
                self.last_write_started = time.monotonic()
                return len(raw)

            def read_response(self, _size, *, deadline):
                self.reads += 1
                now = time.monotonic()
                return SimpleNamespace(
                    data=self.response, started_at=now, ended_at=now)

            def event(self, _name, **_fields):
                return None

        transport = Transport()
        report = {
            "status": "not_started", "accepted_tx_bytes": 0,
            "uncertain_tx_bytes": 0, "write_status": "not_attempted",
        }
        owner = marvin_operator.ProductionControllerOwner(
            transport, report)
        started = time.monotonic()
        owner.start()
        managers = marvin_operator.managers_for_owner(owner)
        managers["drive"].start()
        managers["leds"].start()
        self.assertEqual(
            transport.waits,
            [marvin_operator.OPERATOR_STARTUP_QUIET_SECONDS])
        self.assertEqual(transport.reads, 1)
        self.assertEqual(
            transport.assert_command,
            marvin_operator.protocol.GET_LED_STATE)
        self.assertEqual(managers["leds"]._baseline, bytes(18))
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(report["responses"], 1)
        self.assertEqual(report["accepted_tx_bytes"], 10)
        self.assertEqual(report["uncertain_tx_bytes"], 0)

    def test_sse_sensors_advance_between_held_pulses_and_stop_is_bounded(self):
        movement = {"active": False}

        class IncrementalSource(Source):
            def __init__(self):
                super().__init__()
                self.parts = []
                self.snapshots = 0
                self.overlap = False
                self.generation = 0
                self.discarded = 0

            def before_pulse(self):
                if self.parts:
                    self.discarded += 1
                self.parts = []
                self.generation += 1

            def read_incremental(self):
                if movement["active"]:
                    self.overlap = True
                    raise AssertionError("sensor getter overlapped a motor pulse")
                time.sleep(0.1)
                self.parts.append(self.generation)
                if len(self.parts) != 4:
                    return None
                if len(set(self.parts)) != 1:
                    raise AssertionError("snapshot straddled a motor pulse")
                self.parts = []
                self.snapshots += 1
                snapshot = super().read()
                snapshot["acquisition"] = {
                    "straddled_motor_pulse": False,
                    "generation": self.generation,
                }
                return snapshot

        source = IncrementalSource()

        def pulse(_direction):
            source.before_pulse()
            movement["active"] = True
            try:
                time.sleep(0.08)
            finally:
                movement["active"] = False

        stops = []
        drive = marvin_operator.marvin_managers.DriveManager(
            pulse, lambda: stops.append(time.monotonic()))
        runtime = marvin_operator.OperatorRuntime(
            source, poll_seconds=0.5, managers={"drive": drive})
        server = marvin_operator.OperatorServer(("127.0.0.1", 0), runtime)
        runtime.start()
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        sensor_events = []
        done = False

        def read_events():
            nonlocal done
            try:
                with urlopen(base + "/api/events", timeout=3) as events:
                    sensor = False
                    while not done:
                        line = events.readline().decode()
                        if not line:
                            return
                        if line == "event: sensor\n":
                            sensor = True
                        elif sensor and line.startswith("data: "):
                            sensor_events.append(
                                json.loads(line.removeprefix("data: ")))
                            sensor = False
            except (OSError, TimeoutError):
                return

        event_thread = Thread(target=read_events, daemon=True)
        event_thread.start()

        def post(path, value):
            request = Request(
                base + path, data=json.dumps(value).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            return json.load(urlopen(request, timeout=2))

        try:
            deadline = time.monotonic() + 2
            while source.snapshots == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            snapshots = source.snapshots
            lease = post("/api/drive/acquire", {})["drive"]["lease"]
            deadline = time.monotonic() + 0.6
            while not source.parts and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(source.parts)
            for _ in range(6):
                post("/api/drive/heartbeat/forward", {"lease": lease})
                time.sleep(0.65)
            self.assertGreater(source.snapshots, snapshots)
            stop_started = time.monotonic()
            post("/api/drive/stop", {})
            self.assertLess(time.monotonic() - stop_started, 0.3)
            self.assertFalse(source.overlap)
            self.assertGreater(source.discarded, 0)
            deadline = time.monotonic() + 2
            while len({
                    event["observed_at"] for event in sensor_events
                    }) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(
                len({event["observed_at"] for event in sensor_events}), 2)
            self.assertTrue(stops)
        finally:
            done = True
            server.shutdown()
            server.server_close()
            runtime.close()
            server_thread.join(2)

    def test_deadman_cancels_pending_acquire_without_motion(self):
        script = marvin_dashboard.DEADMAN_CORE + """
const token={cancelled:false}, calls=[];
let resolve;
const pending=new Promise(done=>resolve=done);
const result=acquireHeld("forward",token,()=>pending,
  lease=>calls.push(["release",lease]),
  (direction,lease)=>calls.push(["pulse",direction,lease]));
token.cancelled=true;
resolve("lease-1");
result.then(value=>{
  if(value!==null||JSON.stringify(calls)!=='[["release","lease-1"]]')
    process.exit(1);
});
"""
        subprocess.run(
            ["node", "-e", script], check=True, timeout=5,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_sse_release_only_for_transport_drive_or_runtime_failure(self):
        script = marvin_dashboard.DEADMAN_CORE + """
const cases=[
  [{data:""},true],
  [{data:JSON.stringify({error:"runtime",managers:{}})},true],
  [{data:JSON.stringify({error:null,managers:{drive:{error:"stop"}}})},true],
  [{data:JSON.stringify({error:null,managers:{camera:{error:"media"}}})},false],
  [{data:JSON.stringify({error:null,managers:{leds:{error:"restore"}}})},false]
];
if(cases.some(([event,want])=>sseRequiresRelease(event)!==want))process.exit(1);
"""
        subprocess.run(
            ["node", "-e", script], check=True, timeout=5,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(
            marvin_operator._event_name({
                "error": None,
                "managers": {"camera": {"error": "retryable"}}}),
            "status")
        self.assertEqual(
            marvin_operator._event_name({
                "error": None,
                "managers": {"drive": {"error": "unsafe"}}}),
            "error")

    def test_heartbeat_loop_serializes_requests(self):
        script = marvin_dashboard.DEADMAN_CORE + """
const token={cancelled:false};let active=0,max=0,count=0;
runHeartbeatLoop(token,t=>!t.cancelled,
  ()=>Promise.resolve(),
  async()=>{active++;max=Math.max(max,active);await Promise.resolve();active--;
    if(++count===4)token.cancelled=true;
  }).then(()=>{if(max!==1||count!==4)process.exit(1)});
"""
        subprocess.run(
            ["node", "-e", script], check=True, timeout=5,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotIn("heartbeat=setInterval", marvin_dashboard.JS)

    def test_operator_transport_admits_exact_led_getter_only(self):
        class Transport:
            submit = marvin_operator._OperatorTransport.submit
            submitted = []
            def _submit_once(self, raw, *, deadline):
                self.submitted.append((raw, deadline))
                return len(raw)

        transport = Transport()
        getter = marvin_operator._frame(
            5000, marvin_operator.protocol.GET_LED_STATE)
        self.assertEqual(
            transport.submit(getter, deadline=1), len(getter))
        self.assertEqual(transport.submitted, [(getter, 1)])
        with self.assertRaisesRegex(OSError, "outside the exact"):
            transport.submit(
                marvin_operator._frame(
                    5001, marvin_operator.protocol.GET_LED_STATE, b"\0"),
                deadline=2)
        self.assertEqual(len(transport.submitted), 1)

    def test_nonzero_fault_notifies_and_still_submits_stop(self):
        class Transport:
            token = b"identity"
            def wait(self, _seconds): return None

        owner = marvin_operator.ProductionControllerOwner(
            Transport(), {}, operation_deadline=time.monotonic() + 10)
        owner.started = True
        submitted = []

        def setter(payload, **_options):
            submitted.append(payload)
            if payload != marvin_operator.pilot.ZERO_PWM:
                raise OSError("nonzero submission uncertain")

        owner._setter = setter
        with patch.object(
                marvin_operator.motor_consent,
                "notify_powered_trial_fault_once") as notify, \
                self.assertRaisesRegex(OSError, "nonzero submission uncertain"):
            owner.drive_step("forward")
        self.assertEqual(submitted[-1], marvin_operator.pilot.ZERO_PWM)
        notify.assert_called()

    def test_drive_cleanup_ignores_expired_admission_window(self):
        self.assertGreaterEqual(
            marvin_operator.OPERATOR_EVIDENCE_SECONDS
            - marvin_operator.OPERATOR_SESSION_SECONDS,
            30)
        now = [5.0]

        class Transport:
            token = b"identity"
            last_write_started = 5.0
            def wait(self, seconds): now[0] += seconds

        owner = marvin_operator.ProductionControllerOwner(
            Transport(), {}, clock=lambda: now[0], operation_deadline=6.0)
        owner.started = True
        owner._snapshot_evidence = {"partial": object()}
        owner._snapshot_started_at = "started"
        owner._snapshot_started_monotonic = now[0]
        submitted = []
        def setter(payload, **_options):
            submitted.append(payload)
            owner.transport.last_write_started = now[0]

        owner._setter = setter
        owner.drive_step("forward")
        self.assertEqual(submitted[0], marvin_operator.pilot.ZERO_PWM)
        self.assertNotEqual(submitted[1], marvin_operator.pilot.ZERO_PWM)
        self.assertEqual(submitted[2], marvin_operator.pilot.ZERO_PWM)
        self.assertEqual(owner._snapshot_evidence, {})
        self.assertEqual(owner.report["discarded_partial_snapshots"], 1)

    def test_runtime_failure_stops_http_server(self):
        class FailedSource:
            reads = 0
            def start(self): return None
            def read(self):
                self.reads += 1
                if self.reads > 1:
                    raise OSError("sensor deadline")
                return {}
            def close(self): return None

        runtime = marvin_operator.OperatorRuntime(
            FailedSource(), poll_seconds=0.5)
        errors = []

        def run():
            try:
                marvin_operator.serve(runtime, port=0)
            except Exception as error:
                errors.append(error)

        thread = Thread(target=run, daemon=True)
        thread.start()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(runtime.status()["state"], "failed")

    def test_real_http_events_recording_and_cleanup(self):
        source = Source()
        runtime = marvin_operator.OperatorRuntime(
            source, poll_seconds=0.5, chunk_seconds=10,
            managers={"camera": Manager("sticky camera error")})
        server = marvin_operator.OperatorServer(("127.0.0.1", 0), runtime)
        runtime.start()
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        try:
            deadline = time.monotonic() + 3
            while runtime.latest() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            status = json.load(urlopen(base + "/api/status", timeout=2))
            self.assertEqual(status["connection"]["status"], "connected")
            with urlopen(base + "/", timeout=2) as page:
                html = page.read().decode()
                self.assertIn("Marvin operator", html)
                self.assertEqual(page.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("script-src 'self'", page.headers["Content-Security-Policy"])
                self.assertEqual(page.headers["Cache-Control"], "no-store")
            with urlopen(base + "/app.js", timeout=2) as script:
                javascript = script.read().decode()
            for contract in (
                    "pointercancel", "keyup", "visibilitychange", "pagehide",
                    'event.code==="Space"', "!event.repeat", "privacy_authorized:true",
                    "/api/leds/reset", "SSE error", "keepalive:true"):
                self.assertIn(contract, javascript)
            self.assertNotIn("innerHTML", javascript)
            self.assertNotIn("eval(", javascript)
            latest = json.load(urlopen(base + "/api/sensors/latest", timeout=2))
            self.assertEqual(len(latest["snapshot"]["raw_telemetry"]["fields"]), 82)

            with urlopen(base + "/api/events", timeout=2) as events:
                payload = events.readline() + events.readline() + events.readline()
            self.assertIn(b"event: status", payload)
            self.assertNotIn(b"event: error", payload)
            self.assertEqual(
                marvin_operator._Handler._event_type({
                    "state": "running", "error": None,
                    "managers": {"drive": {"error": "unsafe drive failure"}},
                }),
                "error",
            )

            for route in (
                    "/api/status", "/api/sensors/latest",
                    "/api/events", "/api/recording", "/api/drive", "/api/leds",
                    "/api/media/audio", "/api/media/video"):
                request = Request(base + route, headers={"Host": "attacker.invalid"})
                with self.subTest(route=route), self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=2)
                self.assertEqual(rejected.exception.code, 400)
                rejected.exception.close()

            with tempfile.TemporaryDirectory() as directory:
                os.chmod(directory, 0o700)
                body = json.dumps({"directory": directory}).encode()
                request = Request(
                    base + "/api/recording/start", data=body,
                    headers={"Content-Type": "application/json"}, method="POST")
                started = json.load(urlopen(request, timeout=2))
                self.assertEqual(started["status"], "ok")
                time.sleep(0.6)
                request = Request(
                    base + "/api/recording/stop", data=b"{}",
                    headers={"Content-Type": "application/json"}, method="POST")
                stopped = json.load(urlopen(request, timeout=2))
                self.assertFalse(stopped["recording"]["active"])
                files = list(Path(directory).glob("*.jsonl"))
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
                rows = [json.loads(line) for line in files[0].read_text().splitlines()]
                self.assertGreaterEqual(len(rows), 1)
                self.assertEqual(len(rows[0]["snapshot"]["raw_telemetry"]["fields"]), 82)

            bad_origin = Request(
                base + "/api/recording/stop", data=b"{}",
                headers={"Content-Type": "application/json",
                         "Origin": "https://example.invalid"}, method="POST")
            with self.assertRaises(HTTPError) as rejected:
                urlopen(bad_origin, timeout=2)
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            runtime.close()
        self.assertTrue(source.closed)
        self.assertEqual(runtime.status()["state"], "stopped")

        self.assertEqual(marvin.main(["operator", "serve", "--run", "--port", "0"]), 1)

    def test_startup_timeout_and_failure_leave_no_owner_thread(self):
        for fail, delay, error in (
                (False, 0.05, TimeoutError),
                (True, 0, RuntimeError)):
            source = SlowSource(fail=fail, delay=delay)
            runtime = marvin_operator.OperatorRuntime(source)
            with self.subTest(fail=fail), self.assertRaises(error):
                runtime.start()
            self.assertFalse(runtime._thread.is_alive())
            self.assertTrue(source.closed)
            self.assertIn(runtime.status()["state"], ("stopped", "failed"))

        class FailedRuntime:
            def __init__(self):
                self.closed = False

            def start(self):
                raise RuntimeError("startup failed")

            def close(self):
                self.closed = True

        runtime = FailedRuntime()
        with patch.object(
                marvin_operator, "OperatorServer") as server, \
                self.assertRaisesRegex(RuntimeError, "startup failed"):
            marvin_operator.serve(runtime, port=0)
        server.assert_not_called()
        self.assertTrue(runtime.closed)


if __name__ == "__main__":
    unittest.main()
