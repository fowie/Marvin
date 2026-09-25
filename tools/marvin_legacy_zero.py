"""ONE isolated, unvalidated legacy zero-velocity characterization; offline by default.

No nonzero values, opcode/sequence options, trailing stop, retry or reconnect.
This is not a physical stop API. See docs/legacy-zero.md before any authorization.
"""

import argparse
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time

from tools.marvin_legacy_live import (
    IngressClock, LiveTransport, MAX_USB_BYTES, MAX_USB_RECORDS, UsbIngress,
    USB_PAYLOAD_LIMIT)
from tools.marvin_legacy_protocol import decode_packet
from tools.marvin_legacy_stream import LegacyStreamDecoder
from tools.marvin_paths import new_output_path


ZERO_TRANSCRIPT = (bytes.fromhex("5300041100040000000000fdc145"),)
SEQUENCE = 1024
COMMAND = 0x11
SERIAL_SECONDS = 15
RESPONSE_SECONDS = 3
CLEANUP_SECONDS = 5


@dataclass(frozen=True)
class _Limits:
    first_sequence: int = SEQUENCE
    max_requests: int = 1
    interval: float = 1
    max_rx_bytes: int = 8192
    read_size: int = 512
    max_lateness: float = 0.05
    max_journal_bytes: int = 262144
    max_journal_records: int = 8192
    journal_reserve_bytes: int = 0
    journal_reserve_records: int = 0


class _ZeroTransport(LiveTransport):
    def write(self, data, *, deadline):
        self._check(deadline)
        if type(data) is not bytes or self.writes != 0 or data != ZERO_TRANSCRIPT[0]:
            raise OSError("Only the single fixed isolated zero-velocity transcript is permitted.")
        return self._submit_once(data, deadline=deadline)


class _ResponseEvidence:
    def __init__(self, record, *, sequence=SEQUENCE, command=COMMAND, validate_packet=None,
                 accepted_response_fields=(0x80,)):
        self.decoder = LegacyStreamDecoder(max_input_bytes=8192)
        self.spans = deque()
        self.events = []
        self.record = record
        self.submitted_at = self.deadline = None
        self.candidates = 0
        self.last_bounds = None
        self.sequence, self.command = sequence, command
        self.validate_packet = validate_packet
        self.accepted_response_fields = accepted_response_fields

    def feed(self, received, now):
        start = self.decoder.input_bytes
        self.spans.append((start, start + len(received.data), received.started_at, received.ended_at))
        events = self.decoder.feed(received.data)
        timing_ok = (received.started_at <= received.ended_at <= now and
                     (self.last_bounds is None or
                      (received.started_at >= self.last_bounds[0] and received.ended_at >= self.last_bounds[1])))
        self.last_bounds = (received.started_at, received.ended_at)
        self._record_events(events, now, timing_ok=timing_ok)

    def _record_events(self, events, now, *, timing_ok=True):
        faults = []
        for event in events:
            if len(self.events) >= 256:
                raise OSError("Decoded response event budget exhausted; adapter raw chunks remain separate evidence.")
            spans = [span for span in self.spans if span[0] < event.end_offset and span[1] > event.offset]
            start = min(span[2] for span in spans) if spans else None
            end = max(span[3] for span in spans) if spans else None
            labels = ["unverified_shape_and_semantics"]
            if not timing_ok:
                labels.append("invalid_ingress_bounds")
            if start is None or self.submitted_at is None or start <= self.submitted_at:
                labels.append("prewrite_or_ambiguous")
            if self.deadline is None or now >= self.deadline or end is None or end >= self.deadline:
                labels.append("late")
            if event.kind != "frame":
                labels.append(event.kind)
            else:
                packet = event.packet
                if packet.command != self.command or packet.sequence != self.sequence:
                    labels.append("unexpected_command_or_sequence")
                if packet.response_field not in self.accepted_response_fields:
                    labels.append("uninterpreted_non80_status")
                if self.candidates:
                    labels.append("additional_frame")
                if event.follows_corruption:
                    labels.append("ambiguous_boundary")
                if self.validate_packet is not None:
                    labels.extend(self.validate_packet(packet))
                if len(labels) == 1:
                    labels.append("correlated_command_sequence_only")
                    self.candidates += 1
            clean = labels == ["unverified_shape_and_semantics", "correlated_command_sequence_only"]
            row = {"stream": event.to_dict(), "started_at": start, "ended_at": end,
                   "labels": labels, "application_acknowledgment": "not_established",
                   "physical_stop": "not_established"}
            self.events.append(row)
            self.record("response_evidence", **row)
            if not clean:
                faults.append(labels)
            while self.spans and self.spans[0][1] <= event.end_offset:
                self.spans.popleft()
        if faults:
            raise OSError(f"Unclean zero-command response evidence: {faults[0]}")

    def finish(self, now):
        self._record_events(self.decoder.finish(), now)


def prepare():
    packet = decode_packet(ZERO_TRANSCRIPT[0])
    if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
            SEQUENCE, COMMAND, 0, bytes(4)):
        raise ValueError("Fixed zero-velocity transcript does not match the reviewed diagnostic.")
    return {
        "status": "dry_run", "evidence_kind": "not_captured",
        "name": "isolated_unvalidated_zero_velocity_characterization",
        "profile": "marvin-legacy-se", "sequence": SEQUENCE, "command": COMMAND,
        "immutable_application_transcript_hex": [raw.hex() for raw in ZERO_TRANSCRIPT],
        "maximum_application_bytes": 14, "maximum_writes": 1,
        "serial_seconds": SERIAL_SECONDS, "postwrite_observation_seconds": RESPONSE_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS, "usb_nominal_seconds": 20, "usb_hard_seconds": 25,
        "max_serial_rx_bytes": 8192, "read_size": 512, "max_read_iterations": 4096,
        "max_response_events": 256, "max_adapter_journal_bytes": 262144,
        "binary_payload_limit": USB_PAYLOAD_LIMIT, "max_usb_bytes": 1048576, "max_usb_records": 10000,
        "settings": {"baudrate": 57600, "bytesize": 8, "parity": "N", "stopbits": 1,
                     "flow_control": "none", "dtr": False, "rts": False, "input_flush": False},
        "source": "Authored docs/marvin-command-map.json: legacy 11, left/right LE int16; installed setter unvalidated.",
        "response_classification": "unverified_shape_and_semantics; unknown payload remains opaque",
        "application_acknowledgment": "not_established", "physical_stop": "not_established",
        "automatic_retries": False, "automatic_reconnect": False, "trailing_command": False,
        "required": ["--run", "--actuators-isolated", "--authorize-unvalidated-zero-velocity",
                     "--unprivileged-usbmon", "--expected-physical-port", "--output NEWDIR"],
    }


def _observe_response(transport, response, *, deadline, clock=time.monotonic):
    """Observe a complete bounded window without owning open, write or close."""
    if response.deadline > deadline:
        raise OSError("Response window exceeds its operation deadline.")
    for _ in range(4096):
        if clock() >= response.deadline:
            break
        transport.ingress.pump()
        remaining = response.deadline - clock()
        if remaining <= 0:
            break
        readable, _, _ = select.select([transport.fd], [], [], min(0.005, remaining))
        if readable:
            received = transport.read_response(512, deadline=response.deadline)
            response.feed(received, clock())
    else:
        raise OSError("Response observation iteration budget exhausted.")
    response.finish(clock())
    if response.candidates != 1:
        raise OSError("response_not_observed: no single clean correlation candidate; no stop/ACK conclusion.")


def _observe(transport, report, *, clock=time.monotonic):
    response = _ResponseEvidence(transport.event)
    primary = None
    try:
        deadline = clock() + SERIAL_SECONDS
        report["operational_deadline_monotonic"] = deadline
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the expected connection.")
        transport.identity(deadline=deadline)
        report.update(write_status="attempted", uncertain_tx_bytes=14)
        count = transport.write(ZERO_TRANSCRIPT[0], deadline=deadline)
        if type(count) is not int or not 0 <= count <= 14:
            raise OSError("Unknown write outcome; no second command.")
        report.update(accepted_tx_bytes=count, uncertain_tx_bytes=14 - count)
        if count != 14:
            raise OSError("Short zero-command write; no retry or suffix resend.")
        response.submitted_at = clock()
        response.deadline = min(deadline, response.submitted_at + RESPONSE_SECONDS)
        report.update(write_status="fully_accepted_not_acknowledged", submitted_at=response.submitted_at,
                      response_deadline=response.deadline)
        if response.deadline != response.submitted_at + RESPONSE_SECONDS:
            raise OSError("Insufficient remaining budget for the approved observation window.")
        _observe_response(transport, response, deadline=deadline, clock=clock)
        report["observation_window_completed"] = True
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        cleanup_errors = []
        try:
            response.finish(clock())
        except (OSError, ValueError) as error:
            cleanup_errors.append(error)
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except (OSError, ValueError) as error:
            cleanup_errors.append(error)
        report.update(response_events=response.events, serial_rx_bytes=transport.serial_bytes,
                      application_submission_attempts=transport.writes,
                      correlated_candidates=response.candidates,
                      response_classification="unverified_shape_and_semantics",
                      application_acknowledgment="not_established", physical_stop="not_established",
                      cleanup_errors=[str(error)[:1024] for error in cleanup_errors])
        if transport.writes == 0:
            report.update(write_status="suppressed_before_submission", accepted_tx_bytes=0, uncertain_tx_bytes=0)
        if cleanup_errors:
            report["status"] = "failed"
            if primary is None:
                raise cleanup_errors[0]
            for error in cleanup_errors:
                primary.add_note(f"Additional finalization error: {error}")
    report["status"] = "observation_complete_unverified"


def run_zero(output, *, expected_physical_port, actuators_isolated=False,
             authorize_unvalidated_zero_velocity=False, unprivileged_usbmon=False):
    review = prepare()
    if any(flag is not True for flag in (
            actuators_isolated, authorize_unvalidated_zero_velocity, unprivileged_usbmon)):
        raise ValueError("Literal isolation, unvalidated-zero-command and ordinary-user USB recording consent required.")
    return _run_diagnostic(
        output, expected_physical_port=expected_physical_port, review=review,
        transport_type=_ZeroTransport, observe=_observe, limits=_Limits(),
        session_options={"actuators_isolated": True, "_isolated_zero_velocity": True},
        declarations={"actuator_power_and_signal_isolation_acknowledged": True},
        expected_tx=14, success_status="observation_complete_unverified",
    )


def _run_diagnostic(output, *, expected_physical_port, review, transport_type, observe,
                    limits, session_options, declarations, expected_tx, success_status,
                    report_key="zero_observation", authorizations=None, capture_validator=None,
                    on_failure=None, serial_seconds=SERIAL_SECONDS,
                    usb_max_bytes=MAX_USB_BYTES, usb_max_records=MAX_USB_RECORDS):
    """Shared evidence/USB lifecycle; each fixed diagnostic keeps its own consent gate."""
    if (not isinstance(expected_physical_port, str) or len(expected_physical_port) > 100 or
            not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*(?:\.[1-9][0-9]*)*", expected_physical_port)):
        raise ValueError("Name the separately reviewed physical USB port.")
    if os.geteuid() == 0:
        raise ValueError("Run as the ordinary user, never sudo.")
    output = new_output_path(output)
    from tools import marvin_campaign, marvin_probe, marvin_session
    from tools.marvin_legacy_probe import _validate_baseline
    baseline = marvin_session.preflight(marvin_probe.DEFAULT_PORT)
    _validate_baseline(baseline, expected_physical_port, marvin_campaign.DESCRIPTOR_HASH)
    clock = IngressClock()
    ingress = UsbIngress(
        output / "capture" / "usb" / "binary-events.bin",
        baseline["usb"], clock, max_bytes=usb_max_bytes,
        max_records=usb_max_records)
    report = {"status": "not_started", "accepted_tx_bytes": 0, "uncertain_tx_bytes": 0,
              "write_status": "not_attempted"}
    metadata = {"status": "incomplete", "evidence_kind": "recorded",
                "output_directory": str(output), "review": review, "baseline": baseline,
                **declarations,
                **({"unvalidated_zero_velocity_authorized": True} if authorizations is None else authorizations),
                "application_acknowledgment": "not_established",
                "physical_stop": "not_established", "automatic_retries": False, "automatic_reconnect": False}
    output.mkdir(mode=0o700)

    def capture(port, directory, *, guard, **options):
        if capture_validator is not None:
            capture_validator(options)
        if session_options.get("_motor_power_off_preparation"):
            from tools import marvin_motor_power_off_consent as consent
            if not consent.validate(
                    actuators_isolated=options.get("actuators_isolated"),
                    **{name: options.get(name) for name in consent.PREPARATION_FLAGS}):
                raise ValueError("Preparation capture requires its truthful separate declarations.")
        directory.mkdir(mode=0o700)
        ingress.open()
        transport = transport_type(port, baseline, directory, ingress, guard=guard, plan=limits)
        observe(transport, report)
        return {"status": "completed", report_key: report}

    try:
        clock.start()
        metadata["clock_offset_nanoseconds"] = clock.offset
        marvin_session.write_json(output / "metadata.json", metadata)
        result = marvin_session.run_session(
            marvin_probe.DEFAULT_PORT, output / "capture", seconds=serial_seconds,
            baudrate=57600, allow_unknown_command=True,
            probe_profile="legacy", usbmon_backend="binary", expected_usb_identity=baseline["usb"],
            ready_callback=lambda _: clock.check(), usb_tail_seconds=5, usb_close_grace_seconds=5,
            capture_runner=capture, binary_payload_limit=USB_PAYLOAD_LIMIT, **session_options,
            operator_usb_max_bytes=usb_max_bytes,
            operator_usb_max_records=usb_max_records,
        )
        ingress.finish(expected_tx(report) if callable(expected_tx) else expected_tx)
        clock.check()
        if ingress.rx_consumed != report["serial_rx_bytes"]:
            raise OSError("Serial/USB evidence byte accounting differs.")
        metadata.update(status=success_status, session=result,
                        usb_in_bytes=ingress.rx_bytes, usb_out_completed_bytes=ingress.completed_tx,
                        observed_line_states=ingress.line_states)
    except BaseException as error:
        metadata.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        if on_failure is not None:
            on_failure(error)
        raise
    finally:
        original = sys.exc_info()[1]
        try:
            failures = []
            for release in (ingress.close, clock.close):
                try:
                    release()
                except BaseException as error:
                    failures.append(error)
                    if on_failure is not None:
                        on_failure(error)
            if failures:
                metadata.update(status="failed", cleanup_errors=[str(error)[:1024] for error in failures])
                if original is None:
                    raise failures[0]
                for error in failures:
                    original.add_note(str(error))
        finally:
            metadata.update(observation=report, finished_at=marvin_probe.utc_now())
            pending = sys.exc_info()[1]
            try:
                marvin_session.seal_evidence(output, metadata)
            except BaseException as error:
                if on_failure is not None:
                    on_failure(error)
                    metadata.update(status="failed", sealing_error=f"{type(error).__name__}: {error}"[:1024])
                    try:
                        marvin_session.write_json(output / "metadata.json", metadata)
                    except BaseException as retention_error:
                        error.add_note(f"Could not retain failed sealing metadata: {retention_error}")
                if pending is None:
                    raise
                pending.add_note(f"Additional evidence sealing error: {error}")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--actuators-isolated", action="store_true")
    parser.add_argument("--authorize-unvalidated-zero-velocity", action="store_true")
    parser.add_argument("--unprivileged-usbmon", action="store_true")
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.run:
            result = prepare()
        else:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_zero(args.output, expected_physical_port=args.expected_physical_port,
                              actuators_isolated=args.actuators_isolated,
                              authorize_unvalidated_zero_velocity=args.authorize_unvalidated_zero_velocity,
                              unprivileged_usbmon=args.unprivileged_usbmon)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error),
                          "physical_stop": "not_established"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
