"""One real-HTTP lifecycle check for the local continuous operator runtime."""

import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import marvin
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
