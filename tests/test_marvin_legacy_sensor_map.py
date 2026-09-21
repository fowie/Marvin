"""Offline cliff/proximity plan and evidence reduction tests."""

from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from tools import marvin_legacy_sensor_map as sensor_map
from tools import marvin_legacy_telemetry as telemetry
from tools import marvin_legacy_protocol as protocol
from tests.test_marvin_legacy_protocol import frame


def sample(payload, index):
    decoded = telemetry.interpret_packet(
        protocol.decode_packet(frame(payload, command=0, sequence=index)),
        direction="received", evidence="synthetic")
    return {"request_index": index, "interpretation": decoded}


class LegacySensorMapTests(unittest.TestCase):
    def test_default_is_inert_and_inventory_is_fixed(self):
        with patch.object(sensor_map, "inspect_recording",
                          side_effect=AssertionError("must not read")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sensor_map.main([]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "offline_plan")
        self.assertFalse(result["transport_accessed"])
        self.assertEqual(result["selected_getter"]["command_hex"], "00")
        self.assertEqual([row["name"] for row in result["channels"]],
                         [f"proximity{i}" for i in range(1, 9)] +
                         [f"cliff{i}" for i in range(1, 6)])
        self.assertEqual([row["payload_offset"] for row in result["channels"]],
                         list(range(4, 30, 2)))
        self.assertTrue(result["fixed_live_bounds"]["getters_only"])

    def test_reducer_emits_only_sensor_words_and_deltas(self):
        first = bytearray(134)
        second = bytearray(first)
        second[4:6] = (500).to_bytes(2, "little")
        second[24:26] = (0xFFFF).to_bytes(2, "little")
        replay = {
            "status": "sealed_collection_claim_complete", "source_bytes": 1234,
            "samples": [sample(bytes(first), 10), sample(bytes(second), 11)],
        }
        result = sensor_map.summarize(replay)
        self.assertEqual(result["status"], "offline_sensor_evidence")
        self.assertEqual(result["samples"][1]["changed_sensor_channels"],
                         ["proximity1", "cliff3"])
        self.assertEqual(
            result["samples"][1]["channels"]["proximity1"]["raw_unsigned_delta_from_baseline"],
            500)
        self.assertEqual(result["samples"][1]["channels"]["cliff3"]["int16"], -1)
        self.assertNotIn("packet", json.dumps(result))
        with self.assertRaises(ValueError):
            sensor_map.summarize({**replay, "status": "incomplete_recording"})


if __name__ == "__main__":
    unittest.main()
