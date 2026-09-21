"""Offline cliff/proximity evidence plan and recording reducer.

Default invocation prints the fixed plan. --recording reads one completed local
ReadRawData JSONL recording; this module never opens transport or device paths.
"""

import argparse
import json
import sys

from tools.marvin_legacy_recording import inspect_recording
from tools.marvin_legacy_telemetry import SENSOR_FIELDS


def plan():
    channels = [
        {"name": name, "payload_offset": 4 + 2 * index, "bytes": 2,
         "wire_views": ["raw_hex", "uint16", "int16"], "physical_mapping": "unresolved"}
        for index, name in enumerate(SENSOR_FIELDS)
    ]
    return {
        "schema_version": 1,
        "status": "offline_plan",
        "offline_only": True,
        "transport_accessed": False,
        "selected_getter": {
            "profile": "marvin-legacy-se", "command_hex": "00",
            "name": "ReadRawData", "request_payload_bytes": 0,
            "response_field_hex": "80", "response_payload_bytes": 134,
            "installed_evidence": "repeated correlated CRC-valid exchanges",
        },
        "channels": channels,
        "fixed_live_bounds": {
            "collector": "tools.marvin_legacy_live",
            "getters_only": True, "maximum_requests": 5,
            "minimum_interval_seconds": 1, "maximum_duration_seconds": 30,
            "automatic_retries": False, "automatic_reconnect": False,
            "fresh_run_authorization": "--run",
            "exact_usb_identity_gate": "--expected-physical-port plus fresh preflight identity",
            "required_isolation_gate": "--actuators-isolated",
            "machine_evidence": "sealed poll.jsonl plus target-scoped USB and session journals",
        },
        "excluded_candidates": [
            {"name": "legacy GetConfig 04", "reason": "reports threshold/hysteresis words, not sensor samples"},
            {"name": "legacy GetSensorInfo 1F", "reason": "installed 128-byte payload is opaque and generation-colliding"},
            {"name": "successor ReadRawData 03", "reason": "EFBE generation mismatch despite mm-labelled source fields"},
            {"name": "successor GetSensorInfo 1D", "reason": "EFBE generation mismatch"},
            {"name": "historical text ADC/READ", "reason": "neighbouring-device hypotheses, not installed Marvin commands"},
            {"name": "setters/resets/calibration/heartbeat", "reason": "state-changing and unnecessary for mapping"},
        ],
        "claims": {
            "units": "unknown_raw_words",
            "thresholds": "reported values 80/32 are not proved active comparisons",
            "physical_channel_locations": "require controlled one-stimulus operator observation",
            "application_acknowledgment": "not_established",
        },
    }


def summarize(replay):
    if replay.get("status") != "sealed_collection_claim_complete":
        raise ValueError("Sensor mapping requires a complete sealed ReadRawData recording.")
    samples = replay.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Sensor mapping recording has no delivered samples.")
    baseline = samples[0]["interpretation"]["fields"]
    previous = None
    reduced = []
    for sample in samples:
        fields = sample["interpretation"]["fields"]
        channels = {}
        for name in SENSOR_FIELDS:
            field = fields[name]
            channels[name] = {
                "raw_hex": field["raw_hex"],
                "uint16": field["unsigned"],
                "int16": field["signed"],
                "raw_unsigned_delta_from_baseline":
                    field["unsigned"] - baseline[name]["unsigned"],
                "raw_unsigned_delta_from_previous": None if previous is None else
                    field["unsigned"] - previous[name]["unsigned"],
            }
        reduced.append({
            "request_index": sample["request_index"],
            "channels": channels,
            "changed_sensor_channels": [] if previous is None else
                [name for name in SENSOR_FIELDS
                 if fields[name]["raw_hex"] != previous[name]["raw_hex"]],
        })
        previous = fields
    return {
        "schema_version": 1,
        "status": "offline_sensor_evidence",
        "offline_only": True,
        "source_status": replay["status"],
        "source_bytes": replay["source_bytes"],
        "samples": reduced,
        "limitations": [
            "Output omits host paths, USB identity, timestamps and unrelated telemetry; preserve the sealed source recording.",
            "Raw changes do not establish sensor location, units, threshold, calibration, health or causality.",
            "Assign a physical mapping only from an operator log with exactly one controlled stimulus at a time.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", help="Completed local poll.jsonl to reduce offline")
    args = parser.parse_args(argv)
    try:
        result = plan() if args.recording is None else summarize(inspect_recording(args.recording))
    except (OSError, ValueError, TypeError, RecursionError) as error:
        print(json.dumps({"status": "input_error", "offline_only": True,
                          "error": str(error)[:1024]}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
