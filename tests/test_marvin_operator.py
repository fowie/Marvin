"""One real-HTTP lifecycle check for the local continuous operator runtime."""

import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import time
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

    def test_production_bootstrap_uses_24_hour_session_and_cleanup_reserve(self):
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
            session = options["session_options"]
            self.assertFalse(session["actuators_isolated"])
            self.assertTrue(session["operator_drive_authorized"])
            self.assertEqual(
                session["operator_application_byte_budget"],
                marvin_operator.OPERATOR_MAX_APPLICATION_BYTES)
            self.assertTrue(all(
                session[name]
                for name in marvin_operator.drive_step.COMMON_FLAGS))

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
            application_bytes = 0
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

        def setter(payload):
            submitted.append(payload)
            if payload != marvin_operator.pilot.ZERO_PWM:
                raise OSError("nonzero submission uncertain")

        owner._setter = setter
        with patch.object(
                marvin_operator.motor_consent,
                "notify_powered_trial_fault") as notify, \
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
            def wait(self, _seconds): now[0] = 7.0

        owner = marvin_operator.ProductionControllerOwner(
            Transport(), {}, clock=lambda: now[0], operation_deadline=6.0)
        owner.started = True
        submitted = []
        owner._setter = submitted.append
        owner.drive_step("forward")
        self.assertEqual(submitted[0], marvin_operator.pilot.ZERO_PWM)
        self.assertNotEqual(submitted[1], marvin_operator.pilot.ZERO_PWM)
        self.assertEqual(submitted[2], marvin_operator.pilot.ZERO_PWM)

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
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            marvin_operator.serve(runtime, port=0)
        self.assertTrue(runtime.closed)


if __name__ == "__main__":
    unittest.main()
