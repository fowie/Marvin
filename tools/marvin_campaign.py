"""Finite, audited communication experiments on Marvin's existing CDC interface.

The default prints coverage only. Explicit --run uses one persistent tty open
per segment, stops further transmission on observed RX, and never resumes or
retries uncertain writes. Firmware writes, resets and actuation are excluded.
Execution requires separate acknowledgment of DTR/RTS line-state effects.
This historical successor campaign is experimental, not for the working legacy
device; command authorization is not evidence of safety on unknown firmware.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_probe, marvin_session, marvin_tx_policy, marvin_usbmon
from tools.marvin_paths import new_output_path


DESCRIPTOR_HASH = "7c0df726b51216f29f11f0d078f4673596f3c50c675c9a0419c1316d5446419b"
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,110}")
USB_TAIL_SECONDS = 5
USB_CLOSE_GRACE_SECONDS = 30


def bounded_number(value, minimum, maximum, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be finite and between {minimum} and {maximum}.")


def compile_segment(segment):
    if not isinstance(segment, dict) or not isinstance(segment.get("id"), str) or not ID_PATTERN.fullmatch(segment["id"]):
        raise ValueError("Each segment needs a bounded unique identifier.")
    if type(segment.get("baudrate")) is not int or not 300 <= segment["baudrate"] <= 1000000:
        raise ValueError("Campaign baud rates must be integer values from 300 to 1000000.")
    marvin_probe.validate_framing(segment.get("bytesize"), segment.get("parity"), segment.get("stopbits"))
    if type(segment.get("dtr")) is not bool or type(segment.get("rts")) is not bool:
        raise ValueError("Campaign DTR/RTS levels must be explicit booleans.")
    steps = segment.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 128:
        raise ValueError("Each segment needs 1 to 128 steps.")
    schedule = []
    labels = set()
    offset = 1.0
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str) or not ID_PATTERN.fullmatch(step["id"]):
            raise ValueError("Each step needs a bounded identifier.")
        if step["id"] in labels:
            raise ValueError("Step identifiers cannot repeat within a segment.")
        labels.add(step["id"])
        for key in ("classification", "rationale"):
            if not isinstance(step.get(key), str) or not step[key]:
                raise ValueError("Every step requires its classification and rationale.")
        bounded_number(step.get("interval_seconds"), 0, 0.1, "Inter-fragment interval")
        bounded_number(step.get("response_seconds"), 0.2, 3, "Response window")
        chunks = step.get("chunks_hex")
        if not isinstance(chunks, list) or not 1 <= len(chunks) <= 256:
            raise ValueError("Every step needs a bounded list of exact write fragments.")
        payload = bytearray()
        for index, text in enumerate(chunks):
            if not isinstance(text, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,32}", text):
                raise ValueError("Each write fragment must encode 1 to 32 bytes exactly.")
            data = bytes.fromhex(text)
            payload.extend(data)
            schedule.append(marvin_probe.ScheduledWrite(offset, data, f"{step['id']}/{index}"))
            if index + 1 < len(chunks):
                offset += step["interval_seconds"]
        if len(payload) > 256:
            raise ValueError("A step cannot exceed 256 application bytes.")
        marvin_tx_policy.validate_transmit_stream(bytes(payload), profile="experimental-successor")
        offset += step["response_seconds"]
    seconds = offset + 2.0
    if seconds > 85:
        raise ValueError("A segment's scheduled response windows exceed 85 seconds.")
    return marvin_probe.validate_schedule(schedule, seconds, profile="experimental-successor"), seconds


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("Unsupported campaign plan schema.")
    segments = plan.get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 750:
        raise ValueError("A campaign must contain 1 to 750 segments.")
    ids = set()
    coverage = {"segments": len(segments), "steps": 0, "writes": 0, "application_bytes": 0,
                "serial_seconds": 0.0, "usb_seconds": 0.0}
    for segment in segments:
        schedule, seconds = compile_segment(segment)
        if segment["id"] in ids:
            raise ValueError("Campaign segment identifiers cannot repeat.")
        ids.add(segment["id"])
        coverage["steps"] += len(segment["steps"])
        coverage["writes"] += len(schedule)
        coverage["application_bytes"] += sum(len(item.data) for item in schedule)
        coverage["serial_seconds"] += seconds
        coverage["usb_seconds"] += seconds + USB_TAIL_SECONDS
    if coverage["writes"] > 100000 or coverage["application_bytes"] > 1048576:
        raise ValueError("Campaign exceeds its finite write/byte budget.")
    return coverage


def assess_segment(directory, result, schedule):
    directory = Path(directory)
    serial = result["serial"]
    usb = result["usb"]
    if any(item["status"] != "completed" for item in (result, serial, usb)):
        raise ValueError("The segment did not complete cleanly.")
    marvin_usbmon.validate_capture_completeness(usb)
    marvin_usbmon.validate_monitor_final_stats(usb)
    trace = directory / "usb/usbmon.txt"
    records, summary = marvin_usbmon.read_analyzed_records(trace)
    identity = result["baseline"]["usb"]
    if any((r.busnum, r.devnum) != (identity["busnum"], identity["devnum"]) for r in records):
        raise ValueError("USB trace identity differs from the pinned device.")
    if any(summary["pairing"][key] for key in (
        "unmatched_completions", "unmatched_submission_errors", "pending_submissions_retained",
        "endpoint_mismatches", "duplicate_submission_ids", "evicted_pending_submissions",
        "completion_exceeds_requested",
    )):
        raise ValueError("Incomplete USB pairing prevents further probing.")
    received = (directory / "serial/received.bin").stat().st_size
    if received != serial["bytes_received"]:
        raise ValueError("Serial byte count disagrees with retained evidence.")
    incoming = sum(r.length for r in records if r.event == "C" and r.transfer in ("Bi", "Ii"))
    assessment = {
        "serial_received_bytes": received, "usb_in_reported_bytes": incoming,
        "application_bytes_written": serial["application_bytes_written"],
        "scheduled_writes_completed": serial["scheduled_writes_completed"],
        "usb_records": summary["records"],
    }
    if received or incoming:
        return dict(assessment, outcome="received_data_stop")
    if (serial["transmit_status"] != "written"
            or serial["scheduled_writes_completed"] != len(schedule)):
        raise ValueError("Not all scheduled writes completed; no retries or continuation.")
    expected = b"".join(item.data for item in schedule)
    if serial["application_bytes_written"] != len(expected):
        raise ValueError("Scheduled byte count differs from the serial evidence.")
    if summary["payload"]["uncaptured_bytes"]:
        raise ValueError("Truncated USB payload prevents complete outgoing-byte verification.")
    submissions = []
    pending = {}
    for record in records:
        if record.event == "E":
            raise ValueError("USB submission errors prevent continuation.")
        if record.transfer != "Bo":
            continue
        if record.event == "S":
            if record.key in pending or record.endpoint != 3 or len(record.payload) != record.length:
                raise ValueError("A USB OUT submission is duplicate or incompletely captured.")
            pending[record.key] = record
            submissions.append(record)
        elif record.event == "C":
            sent = pending.pop(record.key, None)
            if (sent is None or record.status != 0 or record.length != sent.length or record.endpoint != 3):
                raise ValueError("A USB OUT transfer was not fully and successfully observed.")
    if pending:
        raise ValueError("USB OUT completions are missing.")
    if b"".join(r.payload for r in submissions) != expected:
        raise ValueError("USB OUT stream differs from the exact scheduled transcript.")
    return dict(assessment, outcome="silent_out_confirmed", usb_out_confirmed_bytes=len(expected))


def partial_segment(directory):
    """Keep known counts separate from an unknown in-flight write after failure."""
    path = Path(directory) / "serial/metadata.json"
    if not path.is_file():
        return {"serial_metadata": "not_created", "application_bytes_written": None}
    value = json.loads(path.read_text(encoding="utf-8"))
    return {key: value.get(key) for key in (
        "status", "transmit_status", "application_bytes_written",
        "known_application_bytes_written", "scheduled_writes_completed", "bytes_received",
        "error", "error_errno",
    )}


def rejected_settings_evidence(directory, baseline):
    directory = Path(directory)
    serial = json.loads((directory / "serial/metadata.json").read_text(encoding="utf-8"))
    if (serial.get("settings_rejected_before_open") is not True
            or serial["application_bytes_written"] != 0 or serial["bytes_received"] != 0):
        raise ValueError("A configuration failure cannot be treated as a clean rejected setting.")
    events = [json.loads(line) for line in (directory / "serial/events.jsonl").read_text().splitlines()]
    if any(event["event"] in ("open_completed", "write_attempt") for event in events):
        raise ValueError("The failed setting already opened or attempted a write; stopping.")
    usb = json.loads((directory / "usb/metadata.json").read_text(encoding="utf-8"))
    marvin_usbmon.validate_capture_completeness(usb)
    marvin_usbmon.validate_monitor_final_stats(usb)
    if (usb["status"] not in ("completed", "interrupted")
            or usb["identity"] != baseline["usb"]):
        raise ValueError("Incomplete USB evidence for a rejected setting.")
    trace = directory / "usb/usbmon.txt"
    records, summary = marvin_usbmon.read_analyzed_records(trace)
    if any(summary["pairing"][key] for key in (
        "pending_submissions_retained", "unmatched_completions", "unmatched_submission_errors",
        "endpoint_mismatches", "duplicate_submission_ids", "evicted_pending_submissions",
        "completion_exceeds_requested",
    )):
        raise ValueError("Rejected setting has incomplete USB pairing; stopping.")
    if any((r.busnum, r.devnum) != (baseline["usb"]["busnum"], baseline["usb"]["devnum"]) for r in records):
        raise ValueError("A rejected setting's trace contains a different device.")
    if any(r.transfer == "Bo" or (r.event == "C" and r.transfer in ("Bi", "Ii") and r.length) for r in records):
        raise ValueError("USB application traffic during a rejected setting requires inspection; stopping.")
    return {"outcome": "setting_rejected_before_application_io", "reason": serial["error"],
            "application_bytes_written": 0, "usb_out_confirmed_bytes": 0,
            "serial_received_bytes": 0, "usb_in_reported_bytes": 0,
            "scheduled_writes_completed": 0, "usb_records": summary["records"]}


def run_campaign(plan, output, *, port=marvin_probe.DEFAULT_PORT, actuators_isolated=False,
                 allow_unknown_command=False, allow_telemetry_state_change=False,
                 allow_line_state_trials=False,
                 sudo_usbmon=False, switch_position=None, max_seconds=14400):
    if os.geteuid() == 0:
        raise ValueError("Run the campaign as the ordinary user, not under sudo.")
    marvin_probe.validate_boolean_flags(
        actuators_isolated=actuators_isolated, allow_unknown_command=allow_unknown_command,
        allow_telemetry_state_change=allow_telemetry_state_change, sudo_usbmon=sudo_usbmon,
        allow_line_state_trials=allow_line_state_trials,
    )
    if (actuators_isolated is not True or allow_unknown_command is not True
            or allow_telemetry_state_change is not True):
        raise ValueError("Campaign requires isolation, unknown-command and telemetry-state authorizations.")
    if allow_line_state_trials is not True:
        raise ValueError("Campaign requires separate line-state authorization for DTR/RTS firmware effects.")
    if switch_position != "RUN":
        raise ValueError("This campaign requires an explicit owner-reported RUN position.")
    bounded_number(max_seconds, 1, 14400, "Campaign wall-clock limit")
    coverage = validate_plan(plan)
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    output = new_output_path(output, allow_missing_parents=True)
    deadline = time.monotonic() + max_seconds
    baseline = marvin_session.preflight(port, deadline=deadline)
    marvin_probe.remaining_time(deadline)
    if baseline["usb"]["descriptors_sha256"] != DESCRIPTOR_HASH:
        raise ValueError("Marvin's descriptor fingerprint changed; review it before transmitting.")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    marvin_session.write_json(output / "plan.json", plan)
    metadata = {
        "status": "incomplete", "started_at": marvin_probe.utc_now(),
        "baseline": baseline, "owner_reported_switch_position": switch_position,
        "line_state_trials_authorized": allow_line_state_trials,
        "plan_sha256": hashlib.sha256(encoded).hexdigest(), "planned": coverage,
        "segments": [], "application_bytes_confirmed": 0,
        "max_wall_seconds": max_seconds, "automatic_retries": False,
        "shared_deadline_monotonic": deadline,
        "usb_tail_seconds": USB_TAIL_SECONDS, "usb_close_grace_seconds": USB_CLOSE_GRACE_SECONDS,
        "limitations": [
            "Finite named catalogue, not all possible byte sequences or numeric settings.",
            "Persistent tty within each segment; reopening between segments is not an MCU reset.",
            "Parser state may carry forward; malformed/framing experiments are ordered last.",
            "Any observed serial RX stops future writes; initial-open flush and read/write race remain.",
            "USB IN is checked after each segment; pre-open data may have escaped serial observation.",
            "The fixed catalogue excludes known destructive commands, not every possible unknown-firmware effect.",
            "Requested CDC line coding is not proof of physical UART behavior or actual switch electrical mode.",
            "The shared deadline stops new work; in-flight calls, safe cleanup and evidence sealing may finish later.",
        ],
    }
    path = output / "metadata.json"
    marvin_session.write_json(path, metadata)
    try:
        for number, segment in enumerate(plan["segments"], 1):
            schedule, seconds = compile_segment(segment)
            if time.monotonic() + seconds + 20 + USB_CLOSE_GRACE_SECONDS > deadline:
                metadata["status"] = "stopped_wall_limit"
                break
            marvin_session.check_identity(port, baseline)
            marvin_probe.remaining_time(deadline)
            entry = {"id": segment["id"], "status": "incomplete", "steps_planned": len(segment["steps"]),
                     "writes_planned": len(schedule), "bytes_planned": sum(len(item.data) for item in schedule)}
            metadata["segments"].append(entry)
            marvin_session.write_json(path, metadata)
            print(f"SEGMENT {number}/{len(plan['segments'])} {segment['id']}: "
                  f"{segment['baudrate']} {segment['bytesize']}{segment['parity']}{segment['stopbits']} "
                  f"DTR={int(segment['dtr'])} RTS={int(segment['rts'])}; "
                  f"{len(segment['steps'])} probes / {entry['bytes_planned']} bytes maximum.", flush=True)
            directory = output / segment["id"]
            try:
                result = marvin_session.run_session(
                    port, directory, seconds=seconds, baudrate=segment["baudrate"],
                    dtr=segment["dtr"], rts=segment["rts"], bytesize=segment["bytesize"],
                    parity=segment["parity"], stopbits=segment["stopbits"],
                    actuators_isolated=True, sudo_usbmon=sudo_usbmon,
                    probe_schedule=schedule, allow_unknown_command=True,
                    probe_profile="experimental-successor",
                    allow_telemetry_state_change=True, expected_usb_identity=baseline["usb"],
                    allow_line_state_trial=allow_line_state_trials,
                    usb_tail_seconds=USB_TAIL_SECONDS, usb_close_grace_seconds=USB_CLOSE_GRACE_SECONDS,
                    deadline=deadline,
                )
            except marvin_probe.SerialSettingsRejected:
                marvin_probe.remaining_time(deadline)
                marvin_session.check_identity(port, baseline)
                marvin_probe.remaining_time(deadline)
                entry["assessment"] = rejected_settings_evidence(directory, baseline)
                marvin_probe.remaining_time(deadline)
                entry["status"] = "unsupported_settings"
                marvin_session.write_json(path, metadata)
                print(f"SEGMENT {number}: unsupported settings; no application write, not retried.", flush=True)
                continue
            marvin_probe.remaining_time(deadline)
            entry["assessment"] = assess_segment(directory, result, schedule)
            entry["status"] = "completed"
            metadata["application_bytes_confirmed"] += entry["assessment"].get("usb_out_confirmed_bytes", 0)
            marvin_probe.remaining_time(deadline)
            marvin_session.write_json(path, metadata)
            print(f"SEGMENT {number}: {entry['assessment']['outcome']}", flush=True)
            if entry["assessment"]["outcome"] == "received_data_stop":
                metadata["status"] = "stopped_on_rx"
                break
        else:
            marvin_probe.remaining_time(deadline)
            metadata["status"] = (
                "completed_with_unsupported_settings"
                if any(entry["status"] == "unsupported_settings" for entry in metadata["segments"])
                else "completed_silent"
            )
    except marvin_probe.DeadlineExpired as error:
        metadata.update(status="stopped_wall_limit", deadline_expired=True, error=str(error))
        if metadata["segments"] and metadata["segments"][-1]["status"] == "incomplete":
            entry = metadata["segments"][-1]
            entry.update(status="stopped_wall_limit", partial=partial_segment(output / entry["id"]))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, marvin_probe.serial.SerialException) as error:
        metadata["status"] = "failed"
        metadata["error"] = str(error)
        if metadata["segments"] and metadata["segments"][-1]["status"] == "incomplete":
            entry = metadata["segments"][-1]
            entry.update(status="failed", partial=partial_segment(output / entry["id"]))
        raise
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        if metadata["segments"] and metadata["segments"][-1]["status"] == "incomplete":
            entry = metadata["segments"][-1]
            entry.update(status="interrupted", partial=partial_segment(output / entry["id"]))
        raise
    finally:
        metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        marvin_session.seal_evidence(output, metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--profile", choices=("quick", "full"), default="full")
    parser.add_argument("--port", default=marvin_probe.DEFAULT_PORT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sudo-usbmon", action="store_true")
    parser.add_argument("--actuators-isolated", action="store_true")
    parser.add_argument("--allow-unknown-command", action="store_true")
    parser.add_argument("--allow-telemetry-state-change", action="store_true")
    parser.add_argument("--allow-line-state-trials", action="store_true",
                        help="Separately acknowledge DTR/RTS transitions and possible firmware state/reset effects")
    parser.add_argument("--switch-position", choices=("RUN",))
    parser.add_argument("--max-seconds", type=float, default=14400,
                        help="Shared operational budget from initial preflight; safe cleanup and sealing may finish later")
    args = parser.parse_args()
    from tools.marvin_campaign_plan import make_plan
    plan = make_plan(profile=args.profile)
    if not args.run:
        print(json.dumps({"coverage": validate_plan(plan), "plan": plan}, indent=2))
        return 0
    if args.output is None:
        parser.error("--output is required with --run")
    try:
        result = run_campaign(
            plan, args.output, port=args.port, actuators_isolated=args.actuators_isolated,
            allow_unknown_command=args.allow_unknown_command,
            allow_telemetry_state_change=args.allow_telemetry_state_change,
            allow_line_state_trials=args.allow_line_state_trials,
            sudo_usbmon=args.sudo_usbmon, switch_position=args.switch_position,
            max_seconds=args.max_seconds,
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, marvin_probe.serial.SerialException) as error:
        print(f"Campaign stopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Campaign interrupted; partial evidence retained, do not restart blindly.", file=sys.stderr)
        return 130
    print(f"Campaign {result['status']}; evidence: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
