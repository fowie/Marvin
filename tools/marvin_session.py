"""Coordinate a guarded Marvin serial session with a target-scoped USB trace.

Run as the ordinary user. --sudo-usbmon uses noninteractive sudo only for the
recorder, which opens the per-bus monitor and drops privileges before evidence.
The default binary backend uses /dev/usbmon rather than restricted debugfs.
Default operation makes no application writes. Separately authorized options
send one CR byte or one source-derived config/unit/sensor-info frame, without
retry. Unit-info plus sensor-info can enable device telemetry; they are never
sent together automatically.
No reset, driver-detach, or firmware-update operation exists here.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_probe, marvin_protocol, marvin_tx_policy, marvin_usbmon
from tools.marvin_paths import new_output_path


USB_POST_CLOSE_DRAIN_SECONDS = 0.25


def coordinated_deadlines(ready, maximum, nominal, launched):
    """Require the opt-in recorder's actual clock origin and hard limit."""
    started = ready.get("monotonic")
    if (type(started) not in (int, float)
            or (type(started) is float and not math.isfinite(started))
            or not launched <= started <= time.monotonic()
            or ready.get("coordinator_stop") is not True
            or ready.get("coordinator_stop_file") != marvin_usbmon.COORDINATOR_STOP_FILE
            or ready.get("seconds") != maximum
            or ready.get("deadline_monotonic") != started + maximum):
        raise ValueError("USB recorder readiness lacks the required coordinated-stop capability or hard deadline.")
    return started + nominal, started + maximum


def request_recorder_stop(directory):
    fd = os.open(directory / marvin_usbmon.COORDINATOR_STOP_FILE,
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    os.close(fd)


def validate_coordinated_completion(usb, ready, requested_at, hard_deadline):
    stopped = usb.get("stopped_monotonic")
    if (usb.get("coordinator_stop") is not True
            or usb.get("coordinator_stop_file") != marvin_usbmon.COORDINATOR_STOP_FILE
            or usb.get("seconds") != ready["seconds"]
            or usb.get("started_monotonic") != ready["monotonic"]
            or usb.get("deadline_monotonic") != hard_deadline
            or usb.get("signal") is not None
            or type(stopped) not in (int, float)
            or (type(stopped) is float and not math.isfinite(stopped))
            or stopped > time.monotonic()):
        raise OSError("USB recorder completion lacks the coordinated-stop capability or matching hard deadline.")
    reason = usb.get("stop_reason")
    if reason == "coordinator_stop":
        if requested_at is not None and requested_at <= stopped < hard_deadline:
            return
    elif reason == "duration" and stopped >= hard_deadline:
        return
    raise OSError("USB recorder completed prematurely or without the requested coordinated stop.")


def usb_path_for_tty(node_name, sys_class_tty=Path("/sys/class/tty")):
    tty_device = (sys_class_tty / node_name / "device").resolve(strict=True)
    usb_path = next(
        (parent for parent in (tty_device, *tty_device.parents)
         if (parent / "idVendor").is_file()),
        None,
    )
    if usb_path is None:
        raise ValueError("Could not establish the tty's physical USB parent.")
    driver_names = []
    for parent in (tty_device, *tty_device.parents):
        if parent == usb_path:
            break
        driver = parent / "driver"
        if driver.is_symlink():
            driver_names.append(driver.resolve(strict=True).name)
    if "cdc_acm" not in driver_names:
        raise ValueError("The selected interface is not bound to cdc_acm.")
    return usb_path


def preflight(port, *, deadline=None):
    """Read cached host identity only; never open the serial or USB device."""
    properties = marvin_probe.check_device(port, deadline=deadline)
    node = Path(port).resolve(strict=True)
    node_stat = node.stat()
    if not stat.S_ISCHR(node_stat.st_mode):
        raise ValueError("The selected serial path is not a character device.")
    usb_path = usb_path_for_tty(node.name)
    identity = marvin_usbmon.read_identity(usb_path)
    marvin_probe.check_port_available(port, deadline=deadline)
    marvin_probe.preflight_timeout(deadline)
    return {
        "usb": identity,
        "tty": str(node),
        "tty_rdev": node_stat.st_rdev,
        "udev_path": properties.get("DEVPATH"),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "pyserial": marvin_probe.serial.VERSION,
    }


def check_identity(port, baseline):
    node = Path(port).resolve(strict=True)
    if str(node) != baseline["tty"] or node.stat().st_rdev != baseline["tty_rdev"]:
        raise OSError("Serial device identity changed; no reconnection will be attempted.")
    if marvin_usbmon.read_identity(baseline["usb"]["usb_path"]) != baseline["usb"]:
        raise OSError("USB identity changed; no reconnection will be attempted.")


def write_json(path, value):
    marvin_probe.save_metadata(path, value)


def evidence_manifest(output):
    """Hash local evidence, excluding the manifest itself."""
    entries = []
    manifest = output / "SHA256SUMS"
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise ValueError("Refusing a symlink inside session evidence.")
        if path.is_file() and path != manifest:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            entries.append(f"{digest}  {path.relative_to(output).as_posix()}\n")
    with manifest.open("x", encoding="utf-8") as stream:
        stream.writelines(entries)


def seal_evidence(output, metadata):
    """Persist final metadata and hashes without masking an active capture failure."""
    original_error = (sys.exc_info()[1]
                      if metadata["status"] in ("incomplete", "failed", "interrupted") else None)
    try:
        write_json(output / "metadata.json", metadata)
        evidence_manifest(output)
    except (OSError, ValueError) as error:
        metadata.update(status_before_sealing=metadata["status"], status="failed",
                        evidence_sealing_error=str(error))
        failure = original_error if original_error is not None else error
        try:
            write_json(output / "metadata.json", metadata)
        except (OSError, ValueError) as metadata_error:
            metadata["evidence_failure_metadata_error"] = str(metadata_error)
            failure.add_note(f"Could not persist failed evidence metadata: {metadata_error}")
        if original_error is None:
            raise
        original_error.add_note(f"Evidence sealing also failed: {error}")


def wait_ready(process, directory, baseline, port, timeout=10, *, deadline=None):
    ready_deadline = time.monotonic() + min(timeout, marvin_probe.remaining_time(deadline))
    while time.monotonic() < ready_deadline:
        marvin_probe.remaining_time(deadline)
        if process.poll() is not None:
            raise OSError(recorder_error(directory, "USB recorder exited before readiness"))
        path = directory / "ready.json"
        if path.is_file():
            ready = json.loads(path.read_text(encoding="utf-8"))
            expected = baseline["usb"]
            for key in ("usb_path", "busnum", "devnum"):
                if ready.get(key) != expected[key]:
                    raise ValueError(f"USB recorder readiness identity mismatch: {key}.")
            check_identity(port, baseline)
            marvin_probe.remaining_time(deadline)
            return ready
        time.sleep(min(0.05, marvin_probe.remaining_time(deadline)))
    marvin_probe.remaining_time(deadline)
    raise TimeoutError("USB recorder did not become ready; serial port was not opened.")


def recorder_error(directory, message):
    path = directory.parent / "usbmon-stderr.log"
    try:
        with path.open("rb") as stream:
            text = stream.read(4096).decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return f"{message}; recorder error log is missing."
    if not text:
        return f"{message}; recorder error log is empty."
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        detail = text
    else:
        detail = report.get("error") if isinstance(report, dict) else None
        if not isinstance(detail, str):
            detail = "See usbmon-stderr.log for recorder diagnostics."
    return f"{message}: {detail}"


def stop_recorder(process):
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise OSError("USB recorder required forced termination; evidence may be incomplete.")
        raise OSError("USB recorder ignored SIGINT and required termination.")


def run_session(
    port, output, *, seconds=60, baudrate=115200, dtr=False, rts=False,
    actuators_isolated=False, sudo_usbmon=False, usbmon_backend="binary",
    probe_cr=False, probe_get_config=False, probe_get_unit_info=False,
    probe_get_sensor_info=False,
    allow_unknown_command=False, allow_telemetry_state_change=False,
    probe_delay=0, allow_line_state_trial=False,
    allow_line_state_change=False,
    ready_callback=None, expected_usb_identity=None,
    probe_schedule=None, bytesize=8, parity="N", stopbits=1,
    probe_profile="modern",
    usb_tail_seconds=30, usb_close_grace_seconds=0,
    deadline=None,
):
    """Keep USB evidence through serial close, optionally reserving bounded grace.

    usb_duration_seconds in session metadata is the maximum recorder budget;
    usb_nominal_duration_seconds excludes grace. With grace enabled, the normal
    stop is based on recorder readiness, after serial close and a short drain.
    Grace changes only host recording, never serial timing, writes, or retries.
    Asserting either line requires separate allow_line_state_change=True consent
    or an authorized GetConfig/schedule line-state trial. Generic consent never
    waives named query line/framing restrictions. Low/low defaults are unchanged.
    An optional shared monotonic deadline bounds operational waits and serial
    I/O admission, not safe shutdown or evidence sealing.
    """
    if os.geteuid() == 0:
        raise ValueError("Run the coordinator as the ordinary user, not under sudo.")
    marvin_probe.validate_boolean_flags(
        actuators_isolated=actuators_isolated, sudo_usbmon=sudo_usbmon,
        allow_unknown_command=allow_unknown_command,
        allow_telemetry_state_change=allow_telemetry_state_change,
        allow_line_state_trial=allow_line_state_trial, dtr=dtr, rts=rts,
        allow_line_state_change=allow_line_state_change,
        probe_cr=probe_cr, probe_get_config=probe_get_config,
        probe_get_unit_info=probe_get_unit_info, probe_get_sensor_info=probe_get_sensor_info,
    )
    if actuators_isolated is not True:
        raise ValueError("Physical motor/servo isolation must be acknowledged.")
    line_state_authorized = allow_line_state_change or allow_line_state_trial
    if (dtr or rts) and line_state_authorized is not True:
        raise ValueError("Asserting DTR or RTS requires separate line-state authorization.")
    marvin_probe.validate_capture_limits(seconds, baudrate, marvin_probe.MAX_CAPTURE_BYTES)
    if seconds > 90:
        raise ValueError("Serial observation must be greater than 0 and at most 90 seconds.")
    marvin_probe.validate_framing(bytesize, parity, stopbits)
    marvin_tx_policy.validate_profile(probe_profile)
    if probe_profile != "modern" and probe_schedule is None:
        raise ValueError("Named coordinator probes require the modern profile.")
    if type(usb_tail_seconds) not in (int, float) or not 5 <= usb_tail_seconds <= 30:
        raise ValueError("USB tail must be finite and between 5 and 30 seconds.")
    if (type(usb_close_grace_seconds) not in (int, float)
            or (type(usb_close_grace_seconds) is float and not math.isfinite(usb_close_grace_seconds))
            or not 0 <= usb_close_grace_seconds <= 30):
        raise ValueError("USB close grace must be finite and between 0 and 30 seconds.")
    usb_nominal_seconds = seconds + usb_tail_seconds
    usb_max_seconds = usb_nominal_seconds + usb_close_grace_seconds
    if usb_max_seconds > 120:
        raise ValueError("Serial duration plus USB tail and close grace must not exceed 120 seconds.")
    if usbmon_backend not in ("text", "binary"):
        raise ValueError("USB monitor backend must be text or binary.")
    if sum((probe_cr, probe_get_config, probe_get_unit_info, probe_get_sensor_info,
            probe_schedule is not None)) > 1:
        raise ValueError("Select only one probe per capture.")
    if (probe_cr or probe_get_config or probe_get_unit_info or probe_get_sensor_info) and allow_unknown_command is not True:
        raise ValueError("An active probe requires explicit unknown-command authorization.")
    if (probe_get_unit_info or probe_get_sensor_info) and allow_telemetry_state_change is not True:
        raise ValueError("GetUnitInfo and GetSensorInfo require acknowledgment of telemetry-state side effects.")
    if probe_schedule is not None:
        if allow_unknown_command is not True:
            raise ValueError("Campaign schedules require command authorization.")
        if probe_delay:
            raise ValueError("A campaign schedule already defines its own delays.")
        probe_schedule = marvin_probe.validate_schedule(probe_schedule, seconds, profile=probe_profile)
    probe = None
    probe_name = None
    expected_payload_bytes = None
    if probe_get_config:
        probe = marvin_protocol.get_config_request()
        probe_name = "GetConfig"
        expected_payload_bytes = 108
    elif probe_get_unit_info:
        probe = marvin_protocol.get_unit_info_request()
        probe_name = "GetUnitInfo"
        expected_payload_bytes = 12
    elif probe_get_sensor_info:
        probe = marvin_protocol.get_sensor_info_request()
        probe_name = "GetSensorInfo"
        expected_payload_bytes = marvin_protocol.GET_SENSOR_INFO_PAYLOAD_BYTES
    elif probe_cr:
        probe = b"\r"
        probe_name = "CR"
    elif probe_schedule is not None:
        probe_name = "Campaign"
    stateful = marvin_probe.validate_named_query_settings(
        b"".join(item.data for item in probe_schedule) if probe_schedule is not None else probe,
        profile=probe_profile, scheduled=probe_schedule is not None,
        baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits,
        dtr=dtr, rts=rts, allow_line_state_trial=allow_line_state_trial,
        allow_telemetry_state_change=allow_telemetry_state_change,
    )
    if (probe_schedule is not None and (probe_profile != "legacy" or stateful)
            and allow_telemetry_state_change is not True):
        raise ValueError("This schedule requires telemetry-state authorization.")
    marvin_probe.validate_probe_delay(probe, seconds, probe_delay)
    output = new_output_path(output, allow_missing_parents=True)
    marvin_probe.remaining_time(deadline)
    deadline_options = {} if deadline is None else {"deadline": deadline}
    baseline = preflight(port, **deadline_options)
    marvin_probe.remaining_time(deadline)
    if expected_usb_identity is not None and baseline["usb"] != expected_usb_identity:
        raise OSError("USB identity changed before the requested capture segment.")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    usb_output = output / "usb"
    metadata = {
        "status": "incomplete",
        "started_at": marvin_probe.utc_now(),
        "baseline": baseline,
        "requested_application_bytes": (
            sum(len(item.data) for item in probe_schedule) if probe_schedule is not None
            else len(probe) if probe is not None else 0
        ),
        "requested_probe_hex": probe.hex() if probe is not None else None,
        "probe_name": probe_name,
        "probe_profile": probe_profile,
        "source_expected_response_payload_bytes": expected_payload_bytes,
        "telemetry_state_change_authorized": bool(
            (probe_get_unit_info or probe_get_sensor_info or probe_schedule is not None)
            and allow_telemetry_state_change
        ),
        "unknown_command_authorized": bool(
            (probe is not None or probe_schedule is not None) and allow_unknown_command
        ),
        "probe_delay_seconds": probe_delay,
        "line_state_trial_authorized": allow_line_state_trial,
        "line_state_change_authorized": line_state_authorized,
        "serial_duration_seconds": seconds,
        "usb_duration_seconds": usb_max_seconds,
        "usb_nominal_duration_seconds": usb_nominal_seconds,
        "usb_tail_seconds": usb_tail_seconds,
        "usb_close_grace_seconds": usb_close_grace_seconds,
        "framing": f"{bytesize}{parity}{stopbits}",
        "dtr_requested": dtr,
        "rts_requested": rts,
        "baudrate_is_trial_setting": True,
        "privileged_usbmon_open_requested": sudo_usbmon,
        "usbmon_backend": usbmon_backend,
        "privacy": "Only target USB records retained; other bus events can enter reader memory.",
        "limitations": [
            "No tool writes does not exclude automatic kernel output.",
            "USB completion does not establish application acknowledgment.",
            "Kernel-open line transitions and initial pySerial input flush remain possible.",
            "Electrical faults are not cleared by successful capture.",
        ],
    }
    if deadline is not None:
        metadata["shared_deadline_monotonic"] = deadline
    metadata_path = output / "metadata.json"
    write_json(metadata_path, metadata)
    command = [
        sys.executable, str(Path(marvin_usbmon.__file__).resolve()),
        "--usb-path", baseline["usb"]["usb_path"],
        "--output", str(usb_output),
        "--seconds", str(usb_max_seconds),
        "--actuators-isolated",
        "--backend", usbmon_backend,
    ]
    if usb_close_grace_seconds:
        command.append("--coordinator-stop")
    if sudo_usbmon:
        command = ["sudo", "-n", *command, "--drop-to-invoking-user"]
    process = None
    hard_deadline = None
    serial_returned = None
    stop_requested_at = None
    try:
        with (output / "usbmon-stdout.log").open("xb") as stdout, (
            output / "usbmon-stderr.log"
        ).open("xb") as stderr:
            launched = time.monotonic()
            marvin_probe.remaining_time(deadline)
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            ready = wait_ready(process, usb_output, baseline, port, **deadline_options)
            if usb_close_grace_seconds:
                nominal_deadline, hard_deadline = coordinated_deadlines(
                    ready, usb_max_seconds, usb_nominal_seconds, launched)
                recorder_deadline = hard_deadline + 5
                metadata.update(usb_nominal_deadline_monotonic=nominal_deadline,
                                usb_hard_deadline_monotonic=hard_deadline)
            else:
                recorder_deadline = time.monotonic() + usb_max_seconds + 5
            metadata["usb_ready"] = ready
            write_json(metadata_path, metadata)

            def guard():
                marvin_probe.remaining_time(deadline)
                if process.poll() is not None:
                    raise OSError("USB recorder stopped; ending serial observation.")
                if hard_deadline is not None and time.monotonic() >= hard_deadline:
                    raise OSError("USB recorder hard deadline reached; ending serial observation.")
                check_identity(port, baseline)
                marvin_probe.remaining_time(deadline)

            # Establish a quiet pre-open window without changing device state.
            pre_open_deadline = time.monotonic() + 1
            while time.monotonic() < pre_open_deadline:
                guard()
                time.sleep(min(0.05, marvin_probe.remaining_time(deadline)))
            if ready_callback is not None:
                ready_callback(ready)
            marvin_probe.remaining_time(deadline)
            probe_options = (
                {"probe": probe, "allow_unknown_command": True} if probe is not None else {}
            )
            if probe_schedule is not None:
                probe_options = {"probe_schedule": probe_schedule, "allow_unknown_command": True}
            try:
                serial_result = marvin_probe.capture(
                    port, output / "serial", seconds=seconds,
                    baudrate=baudrate, max_bytes=marvin_probe.MAX_CAPTURE_BYTES,
                    actuators_isolated=True, dtr=dtr, rts=rts,
                    line_state_at_open=True, guard=guard,
                    allow_line_state_change=allow_line_state_change,
                    allow_line_state_trial=allow_line_state_trial,
                    probe_delay=probe_delay,
                    bytesize=bytesize, parity=parity, stopbits=stopbits,
                    probe_profile=probe_profile,
                    allow_telemetry_state_change=allow_telemetry_state_change,
                    **probe_options, **deadline_options,
                )
            finally:
                serial_returned = time.monotonic()
                metadata["serial_returned_monotonic"] = serial_returned
            guard()
            metadata["serial"] = serial_result
            write_json(metadata_path, metadata)
            # Grace is a maximum, not an added delay on every normal segment.
            # Never request stop until capture's finally/tty close has returned
            # and the reader has had a bounded opportunity to drain close events.
            while process.poll() is None:
                marvin_probe.remaining_time(deadline)
                check_identity(port, baseline)
                marvin_probe.remaining_time(deadline)
                now = time.monotonic()
                if now >= recorder_deadline:
                    raise TimeoutError("USB recorder exceeded its bounded observation window.")
                if (usb_close_grace_seconds and stop_requested_at is None
                        and max(nominal_deadline, serial_returned + USB_POST_CLOSE_DRAIN_SECONDS) <= now < hard_deadline):
                    request_recorder_stop(usb_output)
                    stop_requested_at = now
                    metadata["usb_stop_requested_monotonic"] = now
                    write_json(metadata_path, metadata)
                time.sleep(min(0.1, marvin_probe.remaining_time(deadline)))
            marvin_probe.remaining_time(deadline)
            if process.returncode != 0:
                raise OSError(recorder_error(usb_output, "USB recorder failed"))
            usb_metadata = json.loads((usb_output / "metadata.json").read_text(encoding="utf-8"))
            if usb_metadata.get("status") != "completed":
                raise OSError("USB recorder did not report a completed capture.")
            marvin_usbmon.validate_capture_completeness(usb_metadata)
            if usbmon_backend == "binary":
                marvin_usbmon.validate_monitor_final_stats(usb_metadata)
            if usb_close_grace_seconds:
                validate_coordinated_completion(usb_metadata, ready, stop_requested_at, hard_deadline)
            check_identity(port, baseline)
            marvin_probe.remaining_time(deadline)
            metadata["usb"] = usb_metadata
            metadata["status"] = "completed"
    except (OSError, ValueError, subprocess.SubprocessError, marvin_probe.serial.SerialException) as error:
        metadata["status"] = "failed"
        metadata["error"] = str(error)
        if isinstance(error, marvin_probe.DeadlineExpired):
            metadata.update(deadline_expired=True, stop_reason="shared_deadline")
        if process is not None and (
            isinstance(error, marvin_probe.SerialSettingsRejected)
            or (usb_close_grace_seconds and serial_returned is not None)
        ):
            # Serial errors emerge only after capture's close/finally. Retain
            # cancellation evidence, but do not relabel uncertainty as success.
            drain_seconds = 5 if isinstance(error, marvin_probe.SerialSettingsRejected) else USB_POST_CLOSE_DRAIN_SECONDS
            drain_deadline = time.monotonic() + drain_seconds
            if hard_deadline is not None:
                drain_deadline = min(drain_deadline, hard_deadline)
            if deadline is not None:
                drain_deadline = min(drain_deadline, deadline)
            try:
                while process.poll() is None and time.monotonic() < drain_deadline:
                    check_identity(port, baseline)
                    time.sleep(max(0, min(0.1, drain_deadline - time.monotonic())))
            except (OSError, ValueError, subprocess.SubprocessError) as drain_error:
                metadata["drain_error"] = str(drain_error)
        raise
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        raise
    finally:
        try:
            if process is not None:
                stop_recorder(process)
            if metadata["status"] == "completed":
                marvin_probe.remaining_time(deadline)
        except (OSError, subprocess.SubprocessError) as error:
            original_failure = metadata["status"] in ("failed", "interrupted")
            metadata["status"] = "failed"
            if isinstance(error, marvin_probe.DeadlineExpired):
                metadata.update(deadline_expired=True, stop_reason="shared_deadline", error=str(error))
            else:
                metadata["shutdown_error"] = str(error)
            if not original_failure:
                raise
        finally:
            metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
            seal_evidence(output, metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=marvin_probe.DEFAULT_PORT)
    parser.add_argument("--preflight", action="store_true",
                        help="Read cached identity and ownership only; do not open the tty")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--actuators-isolated", action="store_true")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--dtr", action="store_true")
    parser.add_argument("--rts", action="store_true")
    parser.add_argument("--sudo-usbmon", action="store_true")
    parser.add_argument("--usbmon-backend", choices=("binary", "text"), default="binary")
    parser.add_argument("--usb-close-grace-seconds", type=float, default=0,
                        help="Extra maximum USB time for slow tty close (0..30); normal captures stop at the nominal tail")
    probes = parser.add_mutually_exclusive_group()
    probes.add_argument("--probe-cr", action="store_true",
                        help="Send exactly one separately authorized CR byte under USB capture")
    probes.add_argument("--probe-get-config", action="store_true",
                        help="Send one source-derived 12-byte GetConfig request; no initialization or retries")
    probes.add_argument("--probe-get-unit-info", action="store_true",
                        help="Send one alternative identity request; may change device telemetry state")
    probes.add_argument("--probe-get-sensor-info", action="store_true",
                        help="Send one sensor-info handshake request; may enable telemetry, not live sensor readings")
    parser.add_argument("--allow-unknown-command", action="store_true")
    parser.add_argument("--allow-telemetry-state-change", action="store_true")
    parser.add_argument("--probe-delay", type=float, default=0)
    parser.add_argument("--allow-line-state-trial", action="store_true",
                        help="Allow nondefault DTR/RTS levels for a GetConfig-only trial")
    parser.add_argument("--allow-line-state-change", action="store_true",
                        help="Acknowledge DTR/RTS assertions without relaxing named-query line/framing restrictions")
    args = parser.parse_args()
    try:
        if args.preflight:
            print(json.dumps(preflight(args.port), indent=2))
            return 0
        if args.output is None:
            parser.error("--output is required for recording")
        result = run_session(
            args.port, args.output, seconds=args.seconds, baudrate=args.baudrate,
            dtr=args.dtr, rts=args.rts, actuators_isolated=args.actuators_isolated,
            sudo_usbmon=args.sudo_usbmon,
            usbmon_backend=args.usbmon_backend,
            usb_close_grace_seconds=args.usb_close_grace_seconds,
            probe_cr=args.probe_cr, probe_get_config=args.probe_get_config,
            probe_get_unit_info=args.probe_get_unit_info,
            probe_get_sensor_info=args.probe_get_sensor_info,
            allow_unknown_command=args.allow_unknown_command,
            allow_telemetry_state_change=args.allow_telemetry_state_change,
            probe_delay=args.probe_delay, allow_line_state_trial=args.allow_line_state_trial,
            allow_line_state_change=args.allow_line_state_change,
        )
    except (OSError, ValueError, subprocess.SubprocessError, marvin_probe.serial.SerialException) as error:
        print(f"Session failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Session interrupted; partial evidence retained.", file=sys.stderr)
        return 130
    print(f"Session {result['status']}; evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
