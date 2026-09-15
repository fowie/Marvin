"""Explicitly authorized, bounded GetConfig timing/line-state trials.

Default CLI operation prints a plan without opening hardware. --run sends at
most one fixed request per selected case, stopping on any incoming payload,
uncertain transfer, capture loss, or identity change. No resume or retry exists.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_probe, marvin_protocol, marvin_session, marvin_usbmon


CASES = (
    ("high-high", True, True),
    ("high-low", True, False),
    ("low-low", False, False),
    ("low-high", False, True),
)
POWER_STATES = ("existing-unverified", "owner-confirmed-cold-start")


def make_plan(case_names=None):
    names = [case[0] for case in CASES] if case_names is None else list(case_names)
    if not names or len(names) != len(set(names)) or any(name not in {c[0] for c in CASES} for name in names):
        raise ValueError("Select one to four distinct named cases.")
    # Selection cannot silently reorder the fixed baseline-first matrix.
    cases = [{"name": name, "dtr": dtr, "rts": rts}
             for name, dtr, rts in CASES if name in names]
    return {
        "cases": cases,
        "packet_hex": marvin_protocol.get_config_request().hex(),
        "baudrate": 115200, "framing": "8N1",
        "probe_delay_seconds": 5, "serial_seconds_per_case": 15,
        "usb_seconds_per_case": 45,
        "maximum_application_bytes": 12 * len(cases),
        "automatic_retries": False,
        "stop_on_any_rx": True,
        "power_cycling_performed_by_tool": False,
        "limitations": [
            "Port reopen is not a controller reset; parser/state can carry between cases.",
            "Cold-start labels are owner assertions for the initial state only.",
            "Control lines can have custom firmware effects; actuator isolation is required.",
            "Pre-probe suppression applies to observed serial RX, not bytes flushed during open.",
            "USB payload before/during open is assessed after that case; snapshots may be partial.",
        ],
    }


def assess_case(output, result):
    """Require positive OUT evidence before advancing a silent case."""
    output = Path(output)
    serial = result["serial"]
    usb = result["usb"]
    if result["status"] != "completed" or serial["status"] != "completed" or usb["status"] != "completed":
        raise ValueError("Case did not complete cleanly; no subsequent trial is permitted.")
    marvin_usbmon.validate_capture_completeness(usb)
    if usb["monitor_final_stats"] != {"queued": 0, "dropped": 0}:
        raise ValueError("USB monitor loss or queued tail prevents continuation.")
    trace = output / "usb" / "usbmon.txt"
    if trace.stat().st_size > 2 * marvin_usbmon.DEFAULT_MAX_BYTES:
        raise ValueError("Case trace exceeds the bounded analysis size.")
    records = [marvin_usbmon.parse_record(line) for line in trace.read_bytes().splitlines() if line.strip()]
    identity = result["baseline"]["usb"]
    if any((r.busnum, r.devnum) != (identity["busnum"], identity["devnum"]) for r in records):
        raise ValueError("Case trace contains a different USB identity.")
    summary = marvin_usbmon.analyze_file(trace)
    if any(summary["pairing"][key] for key in (
        "unmatched_completions", "unmatched_submission_errors", "pending_submissions_retained",
        "endpoint_mismatches", "duplicate_submission_ids", "evicted_pending_submissions",
        "completion_exceeds_requested",
    )):
        raise ValueError("Incomplete or inconsistent USB pairing prevents continuation.")
    received = (output / "serial" / "received.bin").stat().st_size
    if received != serial["bytes_received"] or received > 65536:
        raise ValueError("Serial byte count disagrees with bounded evidence.")
    incoming = sum(r.length for r in records if r.event == "C" and r.transfer in ("Bi", "Ii"))
    assessment = {"serial_received_bytes": received, "usb_in_reported_bytes": incoming}
    if received or incoming:
        return dict(assessment, outcome="received_data_stop")
    request = marvin_protocol.get_config_request()
    submissions = [r for r in records if r.transfer == "Bo" and r.event == "S"]
    completions = [r for r in records if r.transfer == "Bo" and r.event == "C"]
    errors = [r for r in records if r.event == "E"]
    if (serial["transmit_status"] != "written" or serial["application_bytes_written"] != len(request)
            or result["requested_probe_hex"] != request.hex() or errors
            or len(submissions) != 1 or len(completions) != 1):
        raise ValueError("No unique confirmed GetConfig write; not retrying or advancing.")
    sent, completed = submissions[0], completions[0]
    if (sent.payload != request or sent.length != len(request) or sent.endpoint != 3
            or completed.key != sent.key or completed.endpoint != sent.endpoint
            or completed.status != 0 or completed.length != len(request)):
        raise ValueError("GetConfig USB OUT bytes/completion mismatch; no continuation.")
    return dict(assessment, outcome="silent_out_confirmed", usb_out_bytes=len(request))


def run_trials(
    port, output, *, case_names=None, actuators_isolated=False,
    allow_unknown_command=False, allow_line_state_trials=False,
    sudo_usbmon=False, power_state="existing-unverified",
):
    plan = make_plan(case_names)
    if os.geteuid() == 0:
        raise ValueError("Run the trial coordinator as the ordinary user.")
    marvin_probe.validate_boolean_flags(
        actuators_isolated=actuators_isolated, allow_unknown_command=allow_unknown_command,
        allow_line_state_trials=allow_line_state_trials, sudo_usbmon=sudo_usbmon,
    )
    if (actuators_isolated is not True or allow_unknown_command is not True
            or allow_line_state_trials is not True):
        raise ValueError("Trials require actuator isolation, command authorization, and line-state authorization.")
    if power_state not in POWER_STATES:
        raise ValueError("Power state must be explicit and supported.")
    baseline = marvin_session.preflight(port)
    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    metadata = {
        "status": "incomplete", "started_at": marvin_probe.utc_now(),
        "plan": plan, "baseline": baseline, "initial_power_state": power_state,
        "line_state_trials_authorized": allow_line_state_trials,
        "cases": [], "automatic_retries": False,
    }
    path = output / "metadata.json"
    marvin_session.write_json(path, metadata)
    try:
        for case in plan["cases"]:
            marvin_session.check_identity(port, baseline)
            entry = dict(case, status="incomplete", evidence=case["name"])
            metadata["cases"].append(entry)
            marvin_session.write_json(path, metadata)
            print(f"CASE {case['name']}: record, listen 5s, at most one GetConfig, then collect.", flush=True)
            result = marvin_session.run_session(
                port, output / case["name"], seconds=15, probe_delay=5,
                baudrate=115200, dtr=case["dtr"], rts=case["rts"],
                actuators_isolated=True, sudo_usbmon=sudo_usbmon,
                probe_get_config=True, allow_unknown_command=True,
                allow_line_state_trial=allow_line_state_trials, expected_usb_identity=baseline["usb"],
            )
            assessment = assess_case(output / case["name"], result)
            entry.update(status="completed", assessment=assessment)
            marvin_session.write_json(path, metadata)
            print(f"CASE {case['name']}: {assessment['outcome']}", flush=True)
            if assessment["outcome"] == "received_data_stop":
                metadata["status"] = "stopped_on_rx"
                break
        else:
            metadata["status"] = "completed_silent"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, marvin_probe.serial.SerialException) as error:
        metadata["status"] = "failed"
        metadata["error"] = str(error)
        if metadata["cases"] and metadata["cases"][-1]["status"] == "incomplete":
            metadata["cases"][-1]["status"] = "failed"
        raise
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        raise
    finally:
        metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        marvin_session.write_json(path, metadata)
        marvin_session.evidence_manifest(output)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Execute instead of printing the offline plan")
    parser.add_argument("--case", action="append", choices=tuple(c[0] for c in CASES))
    parser.add_argument("--port", default=marvin_probe.DEFAULT_PORT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--actuators-isolated", action="store_true")
    parser.add_argument("--allow-unknown-command", action="store_true")
    parser.add_argument("--allow-line-state-trials", action="store_true")
    parser.add_argument("--sudo-usbmon", action="store_true")
    parser.add_argument("--power-state", choices=POWER_STATES, default="existing-unverified")
    args = parser.parse_args()
    try:
        if not args.run:
            print(json.dumps(make_plan(args.case), indent=2))
            return 0
        if args.output is None:
            parser.error("--output is required for live trials")
        result = run_trials(
            args.port, args.output, case_names=args.case,
            actuators_isolated=args.actuators_isolated,
            allow_unknown_command=args.allow_unknown_command,
            allow_line_state_trials=args.allow_line_state_trials,
            sudo_usbmon=args.sudo_usbmon, power_state=args.power_state,
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, marvin_probe.serial.SerialException) as error:
        print(f"Trials stopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Trials interrupted; partial evidence retained. Do not restart blindly.", file=sys.stderr)
        return 130
    print(f"Trials {result['status']}; evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
