"""Observe an owner-operated power cycle, allowing one verified USB return.

Uses separate target-scoped USB/serial segments, not an uninterrupted bus trace.
Enumeration and the gap before the second recorder is ready are not captured.
No application bytes, reset requests, or power-control operations are sent.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_probe, marvin_session


def identity_changed(baseline):
    """Recognize disappearance/re-enumeration without opening any device."""
    path = Path(baseline["usb"]["usb_path"])
    try:
        current = path.stat()
    except FileNotFoundError:
        return True
    expected = baseline["usb"]
    return (current.st_dev, current.st_ino) != (
        expected["sysfs_device"], expected["sysfs_inode"]
    )


def validate_return(baseline, returned):
    for key in ("usb_path", "physical_port", "busnum", "idVendor", "idProduct",
                "descriptors_sha256", "descriptors_bytes"):
        if returned["usb"][key] != baseline["usb"][key]:
            raise ValueError(f"Returned device does not match the original {key}.")
    if returned["usb"] == baseline["usb"]:
        raise ValueError("No new USB enumeration was established.")


def wait_for_return(port, baseline, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(port).exists():
            try:
                returned = marvin_session.preflight(port)
            except FileNotFoundError:
                # The tty or its USB parent disappeared during enumeration.
                time.sleep(0.1)
                continue
            validate_return(baseline, returned)
            return returned
        time.sleep(0.1)
    raise TimeoutError("Marvin did not return within 90 seconds; no further attempts.")


def run_boot_capture(port, output, *, actuators_isolated=False, sudo_usbmon=False):
    if os.geteuid() == 0:
        raise ValueError("Run the boot observer as the ordinary user, not under sudo.")
    if not actuators_isolated:
        raise ValueError("Physical motor/servo isolation must be acknowledged.")
    baseline = marvin_session.preflight(port)
    output = Path(output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    metadata = {
        "status": "incomplete",
        "started_at": marvin_probe.utc_now(),
        "baseline": baseline,
        "application_bytes_requested": 0,
        "maximum_usb_returns": 1,
        "limitations": [
            "Segmented recording: USB enumeration and the reconnect gap are not captured.",
            "Each USB recorder is ready before its corresponding serial open.",
            "No automatic retries after a failure unrelated to USB identity change.",
            "No second reconnection after the return segment starts.",
            "Opening the tty changes CDC line settings; 115200/8N1 is a trial setting.",
            "No USB re-enumeration does not prove that a physical power cycle occurred.",
            "USB back-power can prevent the controller from turning fully off.",
        ],
        "events": [],
    }

    def event(name, **details):
        entry = {"event": name, "at": marvin_probe.utc_now(),
                 "monotonic_seconds": time.monotonic(), **details}
        metadata["events"].append(entry)
        marvin_session.write_json(output / "metadata.json", metadata)
        print(json.dumps(entry), flush=True)

    def ready(segment, message):
        def callback(usb_ready):
            event("segment_ready", segment=segment, usb_ready=usb_ready)
            marvin_session.write_json(output / f"{segment}-ready.json", usb_ready)
            print(message, flush=True)
        return callback

    options = {
        "actuators_isolated": True, "sudo_usbmon": sudo_usbmon,
        "usbmon_backend": "binary", "baudrate": 115200, "dtr": True, "rts": True,
    }
    marvin_session.write_json(output / "metadata.json", metadata)
    try:
        event("initial_segment_starting")
        try:
            first = marvin_session.run_session(
                port, output / "before-cycle", seconds=90,
                expected_usb_identity=baseline["usb"],
                ready_callback=ready(
                    "before-cycle",
                    "READY: USB recording is active. Perform the one planned power cycle now.",
                ),
                **options,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            if not (output / "before-cycle-ready.json").is_file():
                raise
            if not identity_changed(baseline):
                raise
            # Preserve the interrupted segment's error; do not relabel it successful.
            metadata["initial_segment_error"] = str(error)
            event("usb_identity_changed", error=str(error))
        else:
            metadata["initial_segment"] = first
            metadata["status"] = "completed_without_reenumeration"
            event("window_ended_without_usb_return")
            return metadata

        event("waiting_for_one_return", timeout_seconds=90)
        returned = wait_for_return(port, baseline)
        metadata["returned_baseline"] = returned
        event("usb_return_validated")
        metadata["return_segment"] = marvin_session.run_session(
            port, output / "after-cycle", seconds=60,
            expected_usb_identity=returned["usb"],
            ready_callback=ready(
                "after-cycle",
                "RETURN CAPTURE ACTIVE: do not power-cycle again; observing for 60 seconds.",
            ),
            **options,
        )
        metadata["status"] = "completed_with_reconnect_gap"
        event("return_capture_completed")
        return metadata
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        metadata["status"] = "failed"
        metadata["error"] = str(error)
        raise
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        raise
    finally:
        metadata["finished_at"] = marvin_probe.utc_now()
        marvin_session.write_json(output / "metadata.json", metadata)
        marvin_session.evidence_manifest(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=marvin_probe.DEFAULT_PORT)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--actuators-isolated", action="store_true")
    parser.add_argument("--sudo-usbmon", action="store_true")
    args = parser.parse_args()
    try:
        result = run_boot_capture(
            args.port, args.output, actuators_isolated=args.actuators_isolated,
            sudo_usbmon=args.sudo_usbmon,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Boot observation failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Boot observation interrupted; partial evidence retained.", file=sys.stderr)
        return 130
    print(f"Boot observation {result['status']}; evidence: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
