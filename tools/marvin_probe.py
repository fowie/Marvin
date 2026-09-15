"""Bounded Marvin serial capture with an optional, explicitly authorized probe.

Opening the port sets CDC line coding and control lines. Driver transients may
still occur, and pySerial discards queued input during open. Isolate actuators
before use. Default operation transmits no application bytes. Only exact
profile-specific requests are accepted; authorization flags never allow arbitrary
bytes. Even an allowlisted query may have unknown effects on another firmware.
"""

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import termios
import time

import serial

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_protocol, marvin_tx_policy
from tools.marvin_paths import new_output_path


DEFAULT_PORT = (
    "/dev/serial/by-id/"
    "usb-Microsoft_Corp_2009_Microsoft_Marvin_12345678-if00"
)
MIN_BAUDRATE = 300
MAX_BAUDRATE = 1_000_000
MAX_CAPTURE_BYTES = 65_536


@dataclass(frozen=True)
class ScheduledWrite:
    offset_seconds: float
    data: bytes
    label: str


class SerialSettingsRejected(serial.SerialException):
    """The host rejected line settings during open, before application writes."""


class DeadlineExpired(TimeoutError):
    """A shared operational deadline expired; cleanup must still run."""


def validate_boolean_flags(**flags):
    for name, value in flags.items():
        if type(value) is not bool:
            raise ValueError(f"{name} must be an explicit boolean.")


def validate_framing(bytesize, parity, stopbits):
    if type(bytesize) is not int or bytesize not in (7, 8):
        raise ValueError("Data bits must be 7 or 8.")
    if parity not in ("N", "E", "O"):
        raise ValueError("Parity must be N, E, or O.")
    if type(stopbits) is not int or stopbits not in (1, 2):
        raise ValueError("Stop bits must be 1 or 2.")


def validate_capture_limits(seconds, baudrate, max_bytes):
    if type(seconds) not in (int, float) or not 0 < seconds <= 120:
        raise ValueError("Capture duration must be finite, greater than 0 and at most 120 seconds.")
    for name, value, minimum, maximum in (
        ("Baud rate", baudrate, MIN_BAUDRATE, MAX_BAUDRATE),
        ("Byte limit", max_bytes, 1, MAX_CAPTURE_BYTES),
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")


def validate_named_query_settings(transcript, *, profile, scheduled, baudrate,
                                  bytesize, parity, stopbits, dtr, rts, allow_line_state_trial,
                                  allow_telemetry_state_change):
    """Validate the transcript and modern query settings, independent of sequence.

    The allowlist decodes every frame. Only GetConfig-only modern transcripts
    can use the low-line exception; historical experimental and legacy profiles
    retain their separate settings policy.
    """
    stateful = (marvin_tx_policy.validate_transmit_stream(transcript, profile=profile)
                if transcript is not None else False)
    if stateful and allow_telemetry_state_change is not True:
        raise ValueError("The selected query requires telemetry-state authorization.")
    modern_query = profile == "modern" and transcript is not None and transcript != b"\r"
    # GetConfig is the modern allowlist's only stateless binary request.
    config_only = modern_query and not stateful
    if allow_line_state_trial and not (config_only or scheduled):
        raise ValueError("Line-state trials require GetConfig or an explicitly validated schedule.")
    if modern_query and (
        baudrate != 115200 or (bytesize, parity, stopbits) != (8, "N", 1)
        or (not (dtr and rts) and not (config_only and allow_line_state_trial))
    ):
        raise ValueError("Source-derived queries require 115200/8N1 and DTR/RTS high unless a GetConfig line-state trial is authorized.")
    return stateful


def validate_schedule(schedule, seconds, *, profile="modern"):
    if not isinstance(schedule, (tuple, list)) or not 1 <= len(schedule) <= 256:
        raise ValueError("A schedule must contain 1 to 256 bounded writes.")
    previous = -1
    total = 0
    for item in schedule:
        if not isinstance(item, ScheduledWrite):
            raise ValueError("Each scheduled entry must be a ScheduledWrite.")
        if (type(item.offset_seconds) not in (int, float)
                or not math.isfinite(item.offset_seconds)
                or not 0 <= item.offset_seconds < seconds
                or item.offset_seconds < previous):
            raise ValueError("Scheduled offsets must be finite, ordered, and inside the capture.")
        if not isinstance(item.data, bytes) or not 1 <= len(item.data) <= 32:
            raise ValueError("Scheduled writes must contain 1 to 32 bytes.")
        if not isinstance(item.label, str) or not 1 <= len(item.label) <= 160:
            raise ValueError("Each scheduled write needs a bounded label.")
        previous = item.offset_seconds
        total += len(item.data)
    if total > 4096:
        raise ValueError("A schedule may send at most 4096 bytes.")
    marvin_tx_policy.validate_transmit_stream(b"".join(item.data for item in schedule), profile=profile)
    return tuple(schedule)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_metadata(path, metadata):
    path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def remaining_time(deadline=None):
    """Check an optional absolute monotonic deadline without extending it."""
    if deadline is None:
        return math.inf
    if (type(deadline) not in (int, float)
            or not -sys.float_info.max <= deadline <= sys.float_info.max):
        raise ValueError("Shared deadline must be a finite monotonic time.")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExpired("Shared capture deadline expired.")
    return remaining


def preflight_timeout(deadline=None):
    """Bound each subprocess by its usual limit and an optional shared deadline."""
    return min(5, remaining_time(deadline))


def check_device(port, *, deadline=None):
    result = subprocess.run(
        ["udevadm", "info", "--query=property", f"--name={port}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=preflight_timeout(deadline),
    )
    preflight_timeout(deadline)
    properties = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    if (properties.get("ID_VENDOR_ID"), properties.get("ID_MODEL_ID")) != (
        "045e",
        "4444",
    ):
        raise ValueError("Refusing to open a device other than Microsoft Marvin.")
    if any(
        properties.get(key) != "1"
        for key in ("ID_MM_DEVICE_IGNORE", "ID_MM_PORT_IGNORE")
    ):
        raise ValueError(
            "ModemManager exclusion is not active. Install the udev rule, "
            "reload rules, and reconnect Marvin before capture."
        )
    return properties


def check_port_available(port, *, deadline=None):
    result = subprocess.run(
        ["fuser", str(Path(port).absolute())],
        capture_output=True, text=True, timeout=preflight_timeout(deadline),
    )
    preflight_timeout(deadline)
    if result.returncode == 0:
        raise ValueError(f"Serial port already has an owner (PID(s): {result.stdout.strip()}).")
    if result.returncode != 1 or result.stderr.strip() or result.stdout.strip():
        raise OSError(f"Could not check serial ownership: {result.stderr.strip()}")


def validate_probe_delay(probe, seconds, delay):
    if type(delay) not in (int, float) or not 0 <= delay <= 30 or delay >= seconds:
        raise ValueError("Probe delay must be finite, from 0 to 30 seconds, and below capture duration.")
    if delay and probe is None:
        raise ValueError("A probe delay requires an explicitly selected probe.")


def capture(
    port,
    output,
    *,
    seconds,
    baudrate,
    max_bytes,
    actuators_isolated,
    dtr=False,
    rts=False,
    probe=None,
    allow_unknown_command=False,
    line_state_at_open=False,
    allow_line_state_change=False,
    allow_line_state_trial=False,
    guard=None,
    probe_delay=0,
    probe_schedule=None,
    probe_profile="modern",
    allow_telemetry_state_change=False,
    bytesize=8,
    parity="N",
    stopbits=1,
    deadline=None,
):
    validate_boolean_flags(
        actuators_isolated=actuators_isolated, allow_unknown_command=allow_unknown_command,
        allow_telemetry_state_change=allow_telemetry_state_change,
        allow_line_state_change=allow_line_state_change,
        allow_line_state_trial=allow_line_state_trial,
        dtr=dtr, rts=rts, line_state_at_open=line_state_at_open,
    )
    if actuators_isolated is not True:
        raise ValueError("Physical motor/servo isolation must be acknowledged.")
    line_state_authorized = allow_line_state_change or allow_line_state_trial
    if (dtr or rts) and line_state_authorized is not True:
        raise ValueError("Asserting DTR or RTS requires separate line-state authorization.")
    validate_capture_limits(seconds, baudrate, max_bytes)
    validate_framing(bytesize, parity, stopbits)
    marvin_tx_policy.validate_profile(probe_profile)
    schedule = None
    if probe_schedule is not None:
        if allow_unknown_command is not True:
            raise ValueError("A multi-probe schedule requires explicit authorization.")
        if probe is not None or probe_delay:
            raise ValueError("A schedule cannot be combined with a one-shot probe or delay.")
        schedule = validate_schedule(probe_schedule, seconds, profile=probe_profile)
    if probe is not None:
        if allow_unknown_command is not True:
            raise ValueError("An unknown application command requires explicit authorization.")
        if not isinstance(probe, bytes) or not 1 <= len(probe) <= 16:
            raise ValueError("A one-shot probe must contain between 1 and 16 bytes.")
    transcript = b"".join(item.data for item in schedule) if schedule is not None else probe
    validate_named_query_settings(
        transcript, profile=probe_profile, scheduled=schedule is not None,
        baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits,
        dtr=dtr, rts=rts, allow_line_state_trial=allow_line_state_trial,
        allow_telemetry_state_change=allow_telemetry_state_change,
    )
    validate_probe_delay(probe, seconds, probe_delay)

    output = new_output_path(output, allow_missing_parents=True)
    remaining_time(deadline)
    deadline_options = {} if deadline is None else {"deadline": deadline}
    properties = check_device(port, **deadline_options)
    remaining_time(deadline)
    check_port_available(port, **deadline_options)
    remaining_time(deadline)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    metadata = {
        "started_at": utc_now(),
        "status": "incomplete",
        "port": str(port),
        "usb_identity": "045e:4444",
        "serial_number": properties.get("ID_SERIAL_SHORT"),
        "udev_path": properties.get("DEVPATH"),
        "actuators_isolated_acknowledged": True,
        "baudrate": baudrate,
        "baudrate_is_trial_setting": True,
        "framing": f"{bytesize}{parity}{stopbits}",
        "dtr_initial_requested": dtr if line_state_at_open else False,
        "dtr_requested": dtr,
        "rts_initial_requested": rts if line_state_at_open else False,
        "rts_requested": rts,
        "flow_control": "none",
        "duration_limit_seconds": seconds,
        "byte_limit": max_bytes,
        "requested_probe_hex": probe.hex() if probe is not None else None,
        "probe_profile": probe_profile,
        "telemetry_state_change_authorized": bool(allow_telemetry_state_change),
        "probe_delay_seconds": probe_delay,
        "suppress_probe_on_early_rx": bool(probe is not None and probe_delay),
        "transmit_status": "not_requested",
        "application_bytes_written": 0,
        "bytes_received": 0,
        "line_state_at_open": line_state_at_open,
        "line_state_change_authorized": line_state_authorized,
        "line_state_trial_authorized": allow_line_state_trial,
        "ownership_check": "fuser; best effort, other-user processes may be invisible",
        "limitations": [
            "Opening changes CDC line coding and control lines.",
            "The driver may transiently assert control lines during open/close.",
            "pySerial discards queued input during open; an early banner may be lost.",
            "A successful capture does not clear the board's electrical faults.",
            "Bytes written means accepted by the serial driver, not device acknowledgment.",
            "Unknown application commands may change firmware state or settings.",
            "No tool writes does not rule out the kernel's first-open echo race.",
            "Kernel exclusive-open prevents later unprivileged opens, not existing readers.",
        ],
    }
    if deadline is not None:
        metadata["shared_deadline_monotonic"] = deadline
    if schedule is not None:
        metadata.update({
            "probe_schedule": [
                {"offset_seconds": item.offset_seconds, "hex": item.data.hex(), "label": item.label}
                for item in schedule
            ],
            "scheduled_writes_completed": 0,
            "known_application_bytes_written": 0,
            "suppress_schedule_on_any_rx": True,
        })
    metadata_path = output / "metadata.json"
    save_metadata(metadata_path, metadata)
    port_handle = None
    with ExitStack() as files:
        events = files.enter_context((output / "events.jsonl").open("x", encoding="utf-8"))

        def event(name, **fields):
            events.write(json.dumps({
                "event": name, "at": utc_now(),
                "monotonic_seconds": time.monotonic(), **fields,
            }) + "\n")
            events.flush()

        try:
            raw = files.enter_context((output / "received.bin").open("xb"))
            chunks = files.enter_context((output / "chunks.jsonl").open("x", encoding="utf-8"))
            event("session_prepared", baudrate=baudrate, framing=metadata["framing"])
            remaining_time(deadline)
            port_handle = serial.Serial(
                port=None,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=0.1,
                write_timeout=0.1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
                exclusive=True,
            )
            port_handle.dtr = metadata["dtr_initial_requested"]
            port_handle.rts = metadata["rts_initial_requested"]
            port_handle.port = str(port)
            if guard is not None:
                guard()
            event("open_attempt", dtr=port_handle.dtr, rts=port_handle.rts)
            remaining_time(deadline)
            try:
                port_handle.open()
            except OSError as error:
                if (getattr(error, "errno", None) == errno.EINVAL
                        or re.fullmatch(r"Could not configure port: \(22, ['\"]Invalid argument['\"]\)", str(error))):
                    raise SerialSettingsRejected(errno.EINVAL, str(error)) from error
                raise
            event("open_completed")
            remaining_time(deadline)
            fcntl.ioctl(port_handle.fileno(), termios.TIOCEXCL)
            metadata["kernel_exclusive_open"] = True
            event("exclusive_open_enabled")
            start = time.monotonic()
            capture_deadline = start + seconds
            if dtr and not line_state_at_open:
                event("dtr_change_attempt", requested=True)
                remaining_time(deadline)
                port_handle.dtr = True
                metadata["dtr_asserted_at"] = utc_now()
                event("dtr_change_completed", requested=True)
            if rts and not line_state_at_open:
                event("rts_change_attempt", requested=True)
                remaining_time(deadline)
                port_handle.rts = True
                metadata["rts_asserted_at"] = utc_now()
                event("rts_change_completed", requested=True)
            def write_probe(data=probe, schedule_index=None):
                if guard is not None:
                    guard()
                remaining_time(deadline)
                if (probe_delay or schedule is not None) and time.monotonic() >= capture_deadline:
                    metadata["transmit_status"] = "not_sent_before_deadline"
                    event("probe_suppressed", reason="capture_ended_before_write")
                    return False
                known_before = metadata["application_bytes_written"] if schedule is not None else 0
                metadata["transmit_status"] = "attempting"
                metadata["application_bytes_written"] = None
                metadata["transmit_attempted_at"] = utc_now()
                save_metadata(metadata_path, metadata)
                details = (
                    {"schedule_index": schedule_index, "label": schedule[schedule_index].label}
                    if schedule_index is not None else {}
                )
                event("write_attempt", hex=data.hex(), size=len(data), **details)
                try:
                    if deadline is not None:
                        port_handle.write_timeout = min(0.1, remaining_time(deadline))
                    remaining_time(deadline)
                except DeadlineExpired:
                    # Metadata/event I/O may exhaust the budget before write().
                    metadata["application_bytes_written"] = known_before
                    metadata["transmit_status"] = "not_sent_before_deadline"
                    event("write_cancelled", reason="shared_deadline", **details)
                    raise
                written = port_handle.write(data)
                if type(written) is not int or not 0 <= written <= len(data):
                    raise serial.SerialTimeoutException("Invalid write result; outcome unknown, not retrying.")
                metadata["application_bytes_written"] = known_before + written
                if schedule is not None:
                    metadata["known_application_bytes_written"] = known_before + written
                event("write_returned", accepted_bytes=written, **details)
                if written != len(data):
                    metadata["transmit_status"] = "short_write"
                    raise serial.SerialTimeoutException(
                        f"Only {written} of {len(data)} probe bytes written; not retrying."
                    )
                metadata["transmit_status"] = "written"
                if schedule_index is not None:
                    metadata["scheduled_writes_completed"] = schedule_index + 1
                    if schedule_index + 1 < len(schedule):
                        metadata["transmit_status"] = "schedule_in_progress"
                metadata["transmit_completed_at"] = utc_now()
                save_metadata(metadata_path, metadata)
                remaining_time(deadline)
                return True

            schedule_index = 0
            schedule_cancelled = False
            schedule_last_completed = None
            if schedule is not None:
                metadata["transmit_status"] = "waiting_for_quiet_window"
                event("schedule_started", writes=len(schedule), offset_origin_monotonic=start)
                save_metadata(metadata_path, metadata)
            probe_pending = probe is not None and probe_delay > 0
            probe_due = time.monotonic() + probe_delay if probe_pending else None
            if probe_pending:
                metadata["transmit_status"] = "waiting_for_quiet_window"
                event("probe_scheduled", delay_seconds=probe_delay, due_monotonic=probe_due)
                save_metadata(metadata_path, metadata)
            elif probe is not None:
                write_probe()

            def received(data):
                nonlocal probe_pending, schedule_cancelled
                offset = metadata["bytes_received"]
                raw.write(data)
                raw.flush()
                chunks.write(json.dumps({
                    "elapsed_seconds": time.monotonic() - start,
                    "at": utc_now(), "offset": offset,
                    "size": len(data), "hex": data.hex(),
                }) + "\n")
                chunks.flush()
                metadata["bytes_received"] += len(data)
                event("received", offset=offset, size=len(data))
                if probe_pending:
                    probe_pending = False
                    metadata["transmit_status"] = "suppressed_pre_probe_rx"
                    event("probe_suppressed", reason="received_data_before_write", offset=offset)
                    save_metadata(metadata_path, metadata)
                if schedule is not None and not schedule_cancelled and schedule_index < len(schedule):
                    schedule_cancelled = True
                    metadata["transmit_status"] = "suppressed_schedule_rx"
                    event("schedule_suppressed", reason="received_data",
                          writes_remaining=len(schedule) - schedule_index, offset=offset)
                    save_metadata(metadata_path, metadata)

            metadata["stop_reason"] = "duration_limit"
            while metadata["bytes_received"] < max_bytes:
                if guard is not None:
                    guard()
                remaining_time(deadline)
                now = time.monotonic()
                remaining = capture_deadline - now
                if remaining <= 0:
                    break
                schedule_due = None
                if schedule is not None and not schedule_cancelled and schedule_index < len(schedule):
                    schedule_due = start + schedule[schedule_index].offset_seconds
                    if schedule_last_completed is not None:
                        spacing = (schedule[schedule_index].offset_seconds
                                   - schedule[schedule_index - 1].offset_seconds)
                        schedule_due = max(schedule_due, schedule_last_completed + spacing)
                    if now >= schedule_due:
                        port_handle.timeout = 0
                        remaining_time(deadline)
                        data = port_handle.read(min(4096, max_bytes - metadata["bytes_received"]))
                        if data:
                            received(data)
                        elif write_probe(schedule[schedule_index].data, schedule_index):
                            schedule_last_completed = time.monotonic()
                            schedule_index += 1
                        else:
                            schedule_cancelled = True
                        continue
                if probe_pending and now >= probe_due:
                    # Drain already queued bytes before deciding whether to write.
                    port_handle.timeout = 0
                    remaining_time(deadline)
                    data = port_handle.read(min(4096, max_bytes - metadata["bytes_received"]))
                    if data:
                        received(data)
                    else:
                        write_probe()
                        probe_pending = False
                    continue
                port_handle.timeout = min(0.1, remaining, remaining_time(deadline))
                if probe_pending:
                    port_handle.timeout = min(port_handle.timeout, probe_due - now)
                if schedule_due is not None:
                    port_handle.timeout = min(port_handle.timeout, schedule_due - now)
                remaining_time(deadline)
                data = port_handle.read(
                    min(4096, max_bytes - metadata["bytes_received"])
                )
                if not data:
                    continue
                received(data)
            if metadata["bytes_received"] >= max_bytes:
                metadata["stop_reason"] = "byte_limit"
            if probe_pending:
                metadata["transmit_status"] = "not_sent_before_deadline"
                event("probe_suppressed", reason="capture_ended_before_write")
            if schedule is not None and not schedule_cancelled and schedule_index < len(schedule):
                metadata["transmit_status"] = "not_sent_before_deadline"
                event("schedule_suppressed", reason="capture_ended_before_write",
                      writes_remaining=len(schedule) - schedule_index)
            remaining_time(deadline)
            metadata["status"] = "completed"
        except (OSError, ValueError, serial.SerialException) as error:
            metadata["status"] = "failed"
            metadata["error"] = str(error)
            metadata["error_errno"] = getattr(error, "errno", None)
            metadata["settings_rejected_before_open"] = isinstance(error, SerialSettingsRejected)
            if isinstance(error, DeadlineExpired):
                metadata.update(deadline_expired=True, stop_reason="shared_deadline")
            if metadata["transmit_status"] == "attempting":
                metadata["transmit_status"] = "unknown"
            event("failed", error=str(error))
            raise
        except KeyboardInterrupt:
            metadata["status"] = "interrupted"
            if metadata["transmit_status"] == "attempting":
                metadata["transmit_status"] = "unknown"
            event("interrupted")
            raise
        finally:
            original_error = (sys.exc_info()[1]
                              if metadata["status"] in ("incomplete", "failed", "interrupted") else None)
            try:
                if port_handle is not None:
                    event("close_attempt")
                    port_handle.close()
                    event("close_completed")
            except (OSError, serial.SerialException) as error:
                metadata["status"] = "failed"
                metadata["close_error"] = str(error)
                if original_error is None:
                    raise
                original_error.add_note(f"Serial close also failed: {error}")
            finally:
                metadata["finished_at"] = utc_now()
                if deadline is not None and time.monotonic() >= deadline:
                    metadata["deadline_expired"] = True
                    if metadata["status"] == "completed":
                        error = DeadlineExpired("Shared capture deadline expired during serial close.")
                        metadata.update(status="failed", stop_reason="shared_deadline", error=str(error))
                        save_metadata(metadata_path, metadata)
                        raise error
                save_metadata(metadata_path, metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--output", type=Path, required=True, help="New capture directory")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--baudrate", type=int, default=115200,
                        help=f"Requested baud rate, {MIN_BAUDRATE}..{MAX_BAUDRATE}; not proof of device support")
    parser.add_argument("--max-bytes", type=int, default=MAX_CAPTURE_BYTES,
                        help=f"Maximum received bytes, 1..{MAX_CAPTURE_BYTES}")
    parser.add_argument(
        "--dtr",
        action="store_true",
        help="Assert host-ready DTR after open; may start/reset custom firmware",
    )
    parser.add_argument(
        "--rts",
        action="store_true",
        help="Assert RTS after open; may affect custom firmware behavior",
    )
    parser.add_argument("--actuators-isolated", action="store_true", required=True)
    parser.add_argument("--probe", choices=("cr", "get-config", "get-unit-info", "get-sensor-info"),
                        help="One fixed modern request; binary queries require 115200/8N1 and DTR/RTS high")
    parser.add_argument("--allow-unknown-command", action="store_true")
    parser.add_argument("--allow-telemetry-state-change", action="store_true")
    parser.add_argument("--allow-line-state-change", action="store_true",
                        help="Separately acknowledge DTR/RTS assertions and possible firmware state/reset effects")
    parser.add_argument("--probe-delay", type=float, default=0,
                        help="Listen before writing; any received byte suppresses the probe. Included in --seconds.")
    parser.add_argument(
        "--line-state-at-open", action="store_true",
        help="Request --dtr/--rts values before open instead of changing them afterward",
    )
    args = parser.parse_args()
    try:
        probes = {
            "cr": b"\r", "get-config": marvin_protocol.get_config_request(),
            "get-unit-info": marvin_protocol.get_unit_info_request(),
            "get-sensor-info": marvin_protocol.get_sensor_info_request(),
        }
        probe = probes[args.probe] if args.probe is not None else None
        result = capture(
            args.port,
            args.output,
            seconds=args.seconds,
            baudrate=args.baudrate,
            max_bytes=args.max_bytes,
            actuators_isolated=args.actuators_isolated,
            dtr=args.dtr,
            rts=args.rts,
            probe=probe,
            allow_unknown_command=args.allow_unknown_command,
            allow_telemetry_state_change=args.allow_telemetry_state_change,
            line_state_at_open=args.line_state_at_open,
            allow_line_state_change=args.allow_line_state_change,
            probe_delay=args.probe_delay,
        )
    except (ValueError, OSError, serial.SerialException, subprocess.SubprocessError) as error:
        print(f"Capture failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Received {result['bytes_received']} bytes; "
        f"wrote {result['application_bytes_written']} application bytes. "
        f"Stopped at {result['stop_reason']}. Files: {args.output}"
    )
    if result["bytes_received"] == 0:
        print("No output observed; this does not identify or validate the protocol.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
