"""Compact end-to-end coverage for the public read-only sensor snapshot."""

import unittest
import time
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from unittest.mock import patch

import marvin
import marvin_sensors
from tests.test_marvin_legacy_protocol import frame
from tools.marvin_legacy_client import Received


class FakeTransport:
    token = b"synthetic-connection-generation-1"

    def __init__(self):
        self.incoming = None
        self.writes = []
        self.on_write = None

    def revalidate(self, *, deadline):
        return self.token

    def identity(self, *, deadline):
        return self.token

    def write(self, data, *, deadline):
        self.writes.append(data)
        self.on_write(marvin_sensors.protocol.decode_packet(data))
        return len(data)

    def read(self, max_bytes, *, deadline):
        if self.incoming is None:
            return None
        data, self.incoming = self.incoming, None
        now = time.monotonic()
        return Received(data, now, now)

    def close(self, *, deadline):
        return None


class MarvinSensorTests(unittest.TestCase):
    def test_plan_and_persistent_fake_transport_snapshot(self):
        offline = marvin.Marvin().sensors()
        self.assertEqual(offline["status"], "offline_plan")
        self.assertEqual(
            [row["query"] for row in offline["requests"]],
            list(marvin_sensors.QUERIES),
        )
        self.assertEqual(
            [row["request_hex"] for row in offline["requests"]],
            ["5300001b00000065e145", "5301000e000000603c45",
             "5302000000000062e745", "5303001d000000655a45"],
        )

        transport = FakeTransport()
        payloads = {
            "get-unit-info": bytes.fromhex("000002010000020104030201"),
            "get-power-state": bytes.fromhex("ff0e"),
            "read-raw-data": bytes(range(134)),
            "get-servo-position": bytes.fromhex("c409aa0a"),
        }

        def reply(packet):
            query = next(
                name for name, spec in marvin_sensors.protocol.GETTERS.items()
                if spec.command == packet.command
            )
            transport.incoming = frame(
                payloads[query], command=packet.command, sequence=packet.sequence)

        transport.on_write = reply
        snapshot = marvin.Marvin(
            run=True,
            sensor_transport=transport,
            sensor_ownership_key=b"sensor-test-resource",
            sensor_expected_identity=transport.token,
        ).sensors()
        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["connection"]["requests_completed"], 4)
        self.assertEqual(len(transport.writes), 4)
        self.assertEqual(
            snapshot["response_evidence"]["get-servo-position"]["packet"]["payload_hex"],
            "c409aa0a",
        )
        self.assertEqual(snapshot["power"]["raw_mask"]["raw_hex"], "ff0e")
        self.assertEqual(snapshot["proximity"]["proximity1"]["physical_sensor"], "P5")
        self.assertEqual(snapshot["cliff"]["cliff1"]["physical_mapping"], "unresolved")
        self.assertEqual(snapshot["motors"]["motorPositionL"]["offset"], 64)
        self.assertEqual(snapshot["servos"]["word0"]["value"]["unsigned"], 2500)
        self.assertEqual(snapshot["battery"]["status"], "unsupported")
        self.assertEqual(snapshot["bump"]["status"], "unsupported")
        with self.assertRaisesRegex(ValueError, "cannot accept CLI evidence"):
            marvin.Marvin(
                run=True, output="new", sensor_transport=FakeTransport(),
                sensor_ownership_key=b"resource",
                sensor_expected_identity=b"identity").sensors()

    def test_live_trust_boundaries_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "expected_physical_port"):
            marvin.Marvin(run=True).sensors()
        transport = FakeTransport()
        with self.assertRaisesRegex(ValueError, "immutable bytes"):
            marvin_sensors.read_snapshot(
                transport, ownership_key="not-bytes",
                expected_identity=transport.token)
        transport.token = b"changed-generation"
        with self.assertRaisesRegex(Exception, "identity"):
            marvin_sensors.read_snapshot(
                transport, ownership_key=b"resource",
                expected_identity=b"expected-generation")

    def test_installed_live_cli_requires_and_forwards_evidence_scope(self):
        args = [
            "sensors", "--run", "--expected-physical-port", "1-3",
            "--output", "new-evidence", "--actuators-isolated",
            "--unprivileged-usbmon",
        ]
        snapshot = {"status": "ready", "raw_telemetry": {"fields": {}}}
        with patch.object(
                marvin_sensors, "run_live_snapshot",
                return_value=snapshot) as run, redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(marvin.main(args), 0)
        self.assertEqual(json.loads(stdout.getvalue()), snapshot)
        run.assert_called_once_with(
            Path("new-evidence"),
            expected_physical_port="1-3",
            run=True,
            actuators_isolated=True,
            unprivileged_usbmon=True,
        )

        for omitted in ("--output", "--actuators-isolated", "--unprivileged-usbmon"):
            reduced = list(args)
            index = reduced.index(omitted)
            del reduced[index:index + (2 if omitted == "--output" else 1)]
            with self.subTest(omitted=omitted), \
                    patch(
                        "tools.marvin_legacy_zero._run_diagnostic",
                        side_effect=AssertionError("must fail before hardware boundary")), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(marvin.main(reduced), 1)

    def test_live_runner_is_fixed_and_gated_before_hardware(self):
        with patch(
                "tools.marvin_legacy_zero._run_diagnostic",
                side_effect=AssertionError("hardware boundary")):
            for options in (
                {},
                {"run": True},
                {"run": True, "actuators_isolated": True},
                {"run": True, "unprivileged_usbmon": True},
            ):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    marvin_sensors.run_live_snapshot(
                        "unused", expected_physical_port="1-3", **options)

        result = {
            "status": "ready",
            "request_evidence": [
                {"request_hex": raw.hex()} for raw in marvin_sensors.TRANSCRIPT
            ],
        }
        metadata = {
            "status": "sensor_snapshot_complete_protocol_only",
            "output_directory": str(Path("new").absolute()),
            "observation": {"snapshot": result},
            "usb_in_bytes": 192,
            "usb_out_completed_bytes": 40,
        }
        with patch(
                "tools.marvin_legacy_zero._run_diagnostic",
                return_value=metadata) as diagnostic:
            snapshot = marvin_sensors.run_live_snapshot(
                Path("new"), expected_physical_port="1-3", run=True,
                actuators_isolated=True, unprivileged_usbmon=True)
        options = diagnostic.call_args.kwargs
        self.assertEqual(options["expected_tx"], 40)
        self.assertEqual(options["limits"].max_requests, 4)
        self.assertEqual(options["limits"].first_sequence, 0)
        self.assertTrue(options["session_options"]["allow_telemetry_state_change"])
        self.assertTrue(options["session_options"]["_sensor_snapshot"])
        self.assertEqual(snapshot["evidence"]["usb_in_bytes"], 192)


if __name__ == "__main__":
    unittest.main()
