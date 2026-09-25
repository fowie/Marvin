"""Compact end-to-end coverage for the public read-only sensor snapshot."""

import unittest
import time

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

    def test_live_trust_boundaries_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "require a transport"):
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


if __name__ == "__main__":
    unittest.main()
