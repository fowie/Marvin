"""One guarded, source/live-supported legacy getter; dry-run by default.

  python -m tools.marvin_legacy_probe read-raw-data --sequence 6 --output NEWDIR \
      --expected-physical-port 1-1.1.3.3 --actuators-isolated --sudo-usbmon --run

Dry-run prints exact bytes/settings without importing transport modules, reading
device identity, or creating output. --run acknowledges the selected query and
possible line effects. GetUnitInfo additionally requires the separate
--allow-telemetry-state-change acknowledgment for handshake effects.
Execution requires ordinary-user coordination,
physical power AND signal isolation, an expected physical USB port and privileged
usbmon recording. Only the recorder uses sudo, via the existing run_session.

Settings and timing are fixed: 57600/8N1, DTR/RTS false, no flow control, one 10-byte
write at5s, serial observation20s, USB tail5s, maximum close grace30s. There is
no retry, reconnect, reset, arbitrary command/payload, setter or settings sweep.
Any early RX suppresses the scheduled query in the existing serial coordinator.

The new output directory must not exist; its parent must already exist. Failures
retain partial evidence. Success means capture completion, NOT an application
ACK. In particular, the reused assessment's received_data_stop outcome does not
fully validate USB OUT. Inspect the sealed capture offline as a separate action.
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_legacy_protocol as protocol


_ENCODERS = {
    "get-config": protocol.get_config_request,
    "get-unit-info": protocol.get_unit_info_request,
    "get-power-state": protocol.get_power_state_request,
    "read-raw-data": protocol.read_raw_data_request,
}
_SETTINGS = {"baudrate": 57600, "bytesize": 8, "parity": "N", "stopbits": 1, "dtr": False, "rts": False}


def prepare(query, sequence=0):
    """Return a pure-data review; generation does not authorize device access."""
    if not isinstance(query, str) or query not in _ENCODERS:
        raise ValueError("Select one of the four proved legacy getter names.")
    request = _ENCODERS[query](sequence)
    packet = protocol.decode_packet(request)
    return {
        "schema_version": 1, "status": "dry_run", "query": query, "sequence": sequence,
        "command": packet.command, "request_hex": request.hex(), "maximum_application_bytes": 10,
        "settings": {**_SETTINGS, "flow_control": "none"},
        "port_selector": "tools.marvin_probe.DEFAULT_PORT",
        "write_offset_seconds": 5, "serial_seconds": 20,
        "usb_tail_seconds": 5, "usb_close_grace_seconds": 30,
        "usb_nominal_seconds": 25, "usb_maximum_seconds": 55,
        "automatic_retries": False, "automatic_reconnect": False,
        "telemetry_state_change_acknowledgment_required": packet.command == protocol.GET_UNIT_INFO,
        "application_acknowledgment": "not_established",
        "identity_policy": {
            "idVendor": "045e", "idProduct": "4444", "descriptors_bytes": 71,
            "descriptor_hash_source": "tools.marvin_campaign.DESCRIPTOR_HASH",
            "physical_port": "Must equal --expected-physical-port before capture.",
        },
        "source": "Legacy S/E packet facts and four independently reviewed 2026-09-14 query/reply captures; no recovered program executed.",
        "required_run_acknowledgments": [
            "--run (selected query and possible line effects)",
            "--actuators-isolated (motor/servo power AND signals)",
            "--sudo-usbmon (privileged recorder only, ordinary-user coordinator)",
            "--expected-physical-port and --output NEWDIR",
            *(["--allow-telemetry-state-change (GetUnitInfo handshake effects)"]
              if packet.command == protocol.GET_UNIT_INFO else []),
        ],
        "limitations": [
            "A source/live-supported getter is not a guarantee of every firmware's semantics.",
            "GetUnitInfo can change handshake state; no automatic follow-up or telemetry-enable query is sent.",
            "Serial open/close may transiently change lines and discard queued input.",
            "Any early serial RX suppresses the single scheduled query; capture completion need not mean transmission.",
            "Driver-accepted bytes, received data and source assessment are not an application ACK.",
            "received_data_stop does not fully validate USB OUT; retained USB IN payloads may be prefixes only.",
            "Actuator power/signal isolation is owner-acknowledged, not electrically measured.",
        ],
    }


def _load_runtime():
    # Deliberately unreachable during import/dry-run or before run acknowledgments.
    from tools import marvin_campaign, marvin_probe, marvin_session
    return marvin_campaign, marvin_probe, marvin_session


def _new_output_path(output):
    if output is None:
        raise ValueError("--output NEWDIR is required for --run.")
    path = Path(os.path.abspath(output))
    if len(path.parts) > 1 and path.parts[1] in ("dev", "proc", "sys"):
        raise ValueError("Output must not be a device or kernel-interface path.")
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError("Output already exists; previous captures are never overwritten or resumed.")
    for parent in reversed(path.parents):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("Output parents must be existing directories, not symlinks or special files.")
    return path


def _validate_baseline(baseline, expected_port, descriptor_hash):
    if not isinstance(baseline, dict) or not isinstance(baseline.get("usb"), dict):
        raise ValueError("Preflight did not return a full baseline identity.")
    usb = baseline["usb"]
    expected = {
        "idVendor": "045e", "idProduct": "4444", "physical_port": expected_port,
        "descriptors_sha256": descriptor_hash, "descriptors_bytes": 71,
    }
    for key, value in expected.items():
        if type(usb.get(key)) is not type(value) or usb[key] != value:
            raise ValueError(f"Preflight identity mismatch: {key}.")
    for key in ("busnum", "devnum", "sysfs_device", "sysfs_inode"):
        if type(usb.get(key)) is not int or usb[key] <= 0:
            raise ValueError(f"Preflight lacks a pinned USB identity field: {key}.")
    if (not isinstance(usb.get("usb_path"), str) or not Path(usb["usb_path"]).is_absolute()
            or Path(usb["usb_path"]).name != expected_port
            or not isinstance(baseline.get("tty"), str)
            or type(baseline.get("tty_rdev")) is not int or baseline["tty_rdev"] <= 0):
        raise ValueError("Preflight lacks a consistent full USB/tty identity.")


def _check_capture_result(result, baseline):
    if not isinstance(result, dict) or result.get("baseline") != baseline:
        raise ValueError("Capture baseline differs from the full pinned preflight identity.")
    if any(not isinstance(value, dict) or value.get("status") != "completed"
           for value in (result, result.get("serial"), result.get("usb"))):
        raise ValueError("Capture/serial/USB did not all report completion.")
    serial = result["serial"]
    written, completed = serial.get("application_bytes_written"), serial.get("scheduled_writes_completed")
    if type(written) is not int or type(completed) is not int:
        raise ValueError("Application write outcome is unknown; no retry.")
    if serial.get("transmit_status") == "written" and (written, completed) == (10, 1):
        return
    if (serial.get("transmit_status") == "suppressed_schedule_rx" and (written, completed) == (0, 0)
            and type(serial.get("bytes_received")) is int and serial["bytes_received"] > 0):
        return
    raise ValueError("The sole scheduled query was not fully accepted or safely suppressed on early RX; no retry.")


def run_probe(query, output, *, sequence=0, expected_physical_port=None,
              actuators_isolated=False, sudo_usbmon=False, allow_telemetry_state_change=False):
    """Run only after explicit guards; transport and cleanup remain in run_session."""
    review = prepare(query, sequence)
    if actuators_isolated is not True:
        raise ValueError("--actuators-isolated must acknowledge motor/servo power AND signal isolation.")
    if sudo_usbmon is not True:
        raise ValueError("--sudo-usbmon is required; only the recorder may use sudo.")
    if type(allow_telemetry_state_change) is not bool:
        raise ValueError("allow_telemetry_state_change must be an explicit boolean.")
    if review["telemetry_state_change_acknowledgment_required"] and allow_telemetry_state_change is not True:
        raise ValueError("--allow-telemetry-state-change is required for GetUnitInfo handshake effects.")
    if (not isinstance(expected_physical_port, str) or len(expected_physical_port) > 100
            or not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*(?:\.[1-9][0-9]*)*", expected_physical_port)):
        raise ValueError("--expected-physical-port must name the reviewed physical USB port.")
    if os.geteuid() == 0:
        raise ValueError("Run as the ordinary user, not under sudo.")
    output = _new_output_path(output)
    campaign, probe, session = _load_runtime()
    baseline = deepcopy(session.preflight(probe.DEFAULT_PORT))
    _validate_baseline(baseline, expected_physical_port, campaign.DESCRIPTOR_HASH)
    schedule = (probe.ScheduledWrite(5.0, bytes.fromhex(review["request_hex"]), f"legacy-{query}"),)
    output.mkdir(mode=0o700, exist_ok=False)
    metadata = {
        "schema_version": 1, "status": "incomplete", "started_at": probe.utc_now(),
        "baseline": baseline, "query": query, "sequence": sequence, "request_hex": review["request_hex"],
        "maximum_application_bytes": 10, "automatic_retries": False,
        "expected_physical_port": expected_physical_port,
        "actuator_power_and_signal_isolation_acknowledged": True,
        "telemetry_state_change_authorized": allow_telemetry_state_change,
        "privileged_recorder_only": True, "application_acknowledgment": "not_established",
        "usb_out_validation": "Not established by this wrapper; inspect the complete trace separately.",
    }
    try:
        session.write_json(output / "review.json", review)
        session.write_json(output / "metadata.json", metadata)
        session.check_identity(probe.DEFAULT_PORT, baseline)
        result = session.run_session(
            probe.DEFAULT_PORT, output / "capture", seconds=20,
            actuators_isolated=True, sudo_usbmon=True, usbmon_backend="binary",
            probe_schedule=schedule, allow_unknown_command=True,
            allow_telemetry_state_change=allow_telemetry_state_change,
            probe_profile="legacy",
            expected_usb_identity=deepcopy(baseline["usb"]),
            ready_callback=lambda _ready: session.check_identity(probe.DEFAULT_PORT, baseline),
            usb_tail_seconds=5, usb_close_grace_seconds=30, **_SETTINGS,
        )
        metadata["session_result"] = result
        _check_capture_result(result, baseline)
        session.check_identity(probe.DEFAULT_PORT, baseline)
        metadata["source_assessment"] = campaign.assess_segment(output / "capture", result, schedule)
        metadata["assessment_limitation"] = (
            "received_data_stop is not full USB OUT validation. No assessment outcome establishes an application ACK."
        )
        metadata["status"] = "capture_completed"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        metadata.update(status="failed", error=str(error))
        raise
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        raise
    finally:
        if metadata["status"] == "incomplete":
            metadata.update(status="failed", error="Execution terminated before capture completion.")
        if metadata["status"] != "capture_completed":
            try:
                metadata["partial"] = campaign.partial_segment(output / "capture")
            except (OSError, ValueError) as error:
                metadata["partial_metadata_error"] = str(error)
        metadata["finished_at"] = probe.utc_now()
        try:
            session.write_json(output / "metadata.json", metadata)
            session.evidence_manifest(output)
        except (OSError, ValueError) as error:
            metadata.update(status="failed", evidence_sealing_error=str(error))
            session.write_json(output / "metadata.json", metadata)
            raise
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", choices=tuple(_ENCODERS))
    parser.add_argument("--sequence", type=int, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--actuators-isolated", action="store_true", help="Acknowledge motor/servo power AND signal isolation")
    parser.add_argument("--sudo-usbmon", action="store_true", help="Authorize privileged recorder only, not a root coordinator")
    parser.add_argument("--allow-telemetry-state-change", action="store_true",
                        help="Separately acknowledge GetUnitInfo handshake/telemetry effects")
    parser.add_argument("--run", action="store_true", help="Authorize this query and possible line effects; otherwise dry-run")
    args = parser.parse_args(argv)
    try:
        if not args.run:
            result = prepare(args.query, args.sequence)
            result["requested_output"] = str(args.output) if args.output is not None else None
            result["expected_physical_port"] = args.expected_physical_port
        else:
            result = run_probe(
                args.query, args.output, sequence=args.sequence,
                expected_physical_port=args.expected_physical_port,
                actuators_isolated=args.actuators_isolated, sudo_usbmon=args.sudo_usbmon,
                allow_telemetry_state_change=args.allow_telemetry_state_change,
            )
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error), "automatic_retry": False,
                          "application_acknowledgment": "not_established"}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "partial_evidence_retained_if_created": True,
                          "application_acknowledgment": "not_established"}))
        return 130
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
