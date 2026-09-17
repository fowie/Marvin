"""One fixed powered Motor-L +1, planned zero, then readback characterization.

Offline by default. This is not a drive API, watchdog, or physical-stop proof.
See docs/legacy-powered-left-stop.md before a separately authorized live run.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import sys
import time
import uuid

from tools import marvin_legacy_zero as zero
from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet
from tools.marvin_legacy_stream import LegacyStreamDecoder
from tools.marvin_legacy_telemetry import interpret_packet


TRANSCRIPT = tuple(bytes.fromhex(raw) for raw in (
    "53000c11000400010000009bfd45",
    "53010c1100040000000000cbc445",
    "53020c0000000072e645",
))
START, PLANNED_ZERO, FINAL_GETTER = TRANSCRIPT
NOMINAL_DWELL_SECONDS = 0.250
MAX_ZERO_START_LATENESS_SECONDS = 0.050
SETTER_RESPONSE_SECONDS = 0.500
GETTER_RESPONSE_SECONDS = 0.500
SERIAL_SECONDS = 5
CLEANUP_SECONDS = 5
SUCCESS = "powered_left_stop_observation_complete_unverified"


def prepare():
    expected = (
        (3072, 0x11, b"\x01\x00\x00\x00"),
        (3073, 0x11, b"\x00\x00\x00\x00"),
        (3074, 0x00, b""),
    )
    for raw, (sequence, command, payload) in zip(TRANSCRIPT, expected):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                sequence, command, 0, payload):
            raise ValueError("Fixed powered-left transcript disagrees with the legacy frame decoder.")
    return {
        "status": "dry_run",
        "name": "powered_left_stop_characterization",
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "maximum_application_bytes": 38,
        "maximum_writes": 3,
        "nominal_start_to_zero_seconds": NOMINAL_DWELL_SECONDS,
        "maximum_accepted_zero_start_lateness_seconds": MAX_ZERO_START_LATENESS_SECONDS,
        "timing_limit_meaning": "cooperative host acceptance criterion, not physical-output guarantee",
        "operational_seconds": SERIAL_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "usb_nominal_seconds": 10,
        "usb_hard_seconds": 15,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "physical_stop": "not_established",
        "operator_observed_motion": "not_recorded_by_software",
        "operator_observed_stop_after_zero": "not_recorded_by_software",
        "operator_cutoff_stop_observation": "separate_later_trial_not_part_of_this_run",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-") for name in consent.POWERED_TRIAL_FLAGS)],
    }


def _marker(name, detail):
    print(f"{name}: {detail}", file=sys.stderr, flush=True)


class _PoweredLeftTransport(LiveTransport):
    """Exact pulse syscalls; no evidence, RX, identity, or display work between them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accepted_tx_bytes = 0
        self.uncertain_tx_bytes = 0
        self.pulse = {}

    def write(self, data, *, deadline):
        raise OSError("Generic writes are forbidden for the powered-left profile.")

    def _same_owned_writable_fd(self):
        self._owner()
        if self.closed or self.fd is None or self.node_stat is None:
            raise OSError("Owned controller descriptor is unavailable; planned zero suppressed.")
        info = os.fstat(self.fd)
        if (info.st_dev, info.st_ino, info.st_rdev) != self.node_stat:
            raise OSError("Owned controller descriptor identity changed; planned zero suppressed.")
        if fcntl.fcntl(self.fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
            raise OSError("Owned controller descriptor is not writable; planned zero suppressed.")

    def run_pulse(self, *, deadline, clock=time.monotonic, sleep=time.sleep):
        self._check(deadline)
        self._same_owned_writable_fd()
        self.ingress.pump()
        if (self.ingress.pending or self.ingress.rx or self.ingress.expected_tx
                or self.ingress.outstanding_tx or self.ingress.rx_bytes != self.serial_bytes
                or self._read_serial(self.plan.read_size)):
            raise OSError("Pre-start transport is not clean; start suppressed.")
        self.event("powered_left_pre_start_intent", start_hex=START.hex(),
                   planned_zero_hex=PLANNED_ZERO.hex(),
                   nominal_dwell_seconds=NOMINAL_DWELL_SECONDS)
        os.fsync(self.journal.fileno())
        _marker("START", "about to submit fixed Motor-L raw +1; operator owns HY1803D cutoff")

        scheduled_at = clock()
        zero_due = scheduled_at + NOMINAL_DWELL_SECONDS
        self.ingress.expected_tx.append(START)
        self.writes = 1
        start_started = clock()
        try:
            start_count = os.write(self.fd, START)
        except BaseException:
            self.uncertain_tx_bytes += len(START)
            raise
        start_returned = clock()
        if type(start_count) is not int or not 0 <= start_count <= len(START):
            self.uncertain_tx_bytes += len(START)
            raise OSError("Unknown start-write result; planned zero suppressed and cutoff required.")
        self.accepted_tx_bytes += start_count
        self.uncertain_tx_bytes += len(START) - start_count
        if start_count != len(START):
            raise OSError("Partial start write; planned zero suppressed and cutoff required.")

        delay_error = None
        remaining = zero_due - clock()
        if remaining > 0:
            try:
                sleep(remaining)
            except BaseException as error:
                delay_error = error
        self._same_owned_writable_fd()
        self.ingress.expected_tx.append(PLANNED_ZERO)
        self.writes = 2
        zero_started = clock()
        try:
            zero_count = os.write(self.fd, PLANNED_ZERO)
        except BaseException:
            self.uncertain_tx_bytes += len(PLANNED_ZERO)
            raise
        zero_returned = clock()
        if type(zero_count) is not int or not 0 <= zero_count <= len(PLANNED_ZERO):
            self.uncertain_tx_bytes += len(PLANNED_ZERO)
            raise OSError("Unknown planned-zero write result; cutoff required.")
        self.accepted_tx_bytes += zero_count
        self.uncertain_tx_bytes += len(PLANNED_ZERO) - zero_count
        _marker("STOP_ATTEMPT", f"planned zero syscall returned {zero_count}/{len(PLANNED_ZERO)} bytes")
        self.pulse = {
            "scheduled_at": scheduled_at,
            "zero_due": zero_due,
            "start_syscall_started": start_started,
            "start_syscall_returned": start_returned,
            "zero_syscall_started": zero_started,
            "zero_syscall_returned": zero_returned,
            "start_accepted_bytes": start_count,
            "zero_accepted_bytes": zero_count,
            "zero_start_lateness_seconds": max(0, zero_started - zero_due),
            "host_start_return_to_zero_start_seconds": max(0, zero_started - start_returned),
        }
        for name, fields in (
                ("powered_left_start_result", {"accepted_bytes": start_count,
                                                "started_at": start_started, "returned_at": start_returned}),
                ("powered_left_zero_result", {"accepted_bytes": zero_count,
                                               "due_at": zero_due, "started_at": zero_started,
                                               "returned_at": zero_returned})):
            self.event(name, **fields)
        if zero_count != len(PLANNED_ZERO):
            raise OSError("Partial planned-zero write; no retry and cutoff required.")
        if delay_error is not None:
            delay_error.add_note("Planned zero was still attempted once after the dwell wait fault.")
            raise delay_error
        return self.pulse

    def submit_getter(self, *, deadline):
        self._check(deadline)
        if self.writes != 2:
            raise OSError("Final getter requires exactly one full start and zero attempt.")
        count = super()._submit_once(FINAL_GETTER, deadline=deadline)
        if type(count) is not int or not 0 <= count <= len(FINAL_GETTER):
            self.uncertain_tx_bytes += len(FINAL_GETTER)
            raise OSError("Unknown final getter write result; no retry.")
        self.accepted_tx_bytes += count
        self.uncertain_tx_bytes += len(FINAL_GETTER) - count
        if count != len(FINAL_GETTER):
            raise OSError("Partial final getter write; no retry.")
        return count


def _collect_setter_responses(transport, report, *, deadline, clock=time.monotonic):
    decoder = LegacyStreamDecoder(max_input_bytes=8192)
    responses = {}
    response_deadline = min(deadline, clock() + SETTER_RESPONSE_SECONDS)
    for _ in range(4096):
        if clock() >= response_deadline:
            break
        transport.identity(deadline=deadline)
        transport.ingress.pump()
        if not select.select(
                [transport.fd], [], [], min(.005, response_deadline - clock()))[0]:
            continue
        received = transport.read(512, deadline=response_deadline)
        for event in decoder.feed(received.data):
            if event.kind != "frame":
                raise OSError(f"Unclean setter response stream: {event.kind}.")
            packet = event.packet
            if (packet.sequence not in (3072, 3073) or packet.sequence in responses
                    or packet.command != 0x11 or packet.response_field != 0x80 or packet.payload):
                raise OSError("Unexpected powered setter response; no getter.")
            responses[packet.sequence] = packet.raw.hex()
            transport.event("powered_left_setter_response", sequence=packet.sequence,
                            raw_hex=packet.raw.hex(), started_at=received.started_at,
                            ended_at=received.ended_at,
                            application_acknowledgment="not_established")
    if decoder.finish() or set(responses) != {3072, 3073}:
        raise OSError("Both clean correlated setter responses were not observed; no getter.")
    report["setter_responses"] = responses


def _observe(transport, report, *, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + SERIAL_SECONDS
    report.update(status="not_started", operational_deadline_monotonic=deadline,
                  planned_zero_attempted=False, planned_zero_fully_accepted=False,
                  final_getter_attempted=False, physical_stop="not_established",
                  operator_observed_motion="not_recorded_by_software",
                  operator_observed_stop_after_zero="not_recorded_by_software")
    primary = None
    response = None
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        pulse = transport.run_pulse(deadline=deadline, clock=clock, sleep=sleep)
        report.update(pulse=pulse, planned_zero_attempted=True,
                      planned_zero_fully_accepted=True,
                      zero_timing_within_acceptance=(
                          pulse["zero_start_lateness_seconds"] <= MAX_ZERO_START_LATENESS_SECONDS))
        _collect_setter_responses(transport, report, deadline=deadline, clock=clock)
        if not report["zero_timing_within_acceptance"]:
            raise OSError("Planned zero was attempted late; final getter suppressed.")
        transport.submit_getter(deadline=deadline)
        report["final_getter_attempted"] = True
        response = zero._ResponseEvidence(
            transport.event, sequence=3074, command=0,
            validate_packet=lambda packet: (
                [] if len(packet.payload) == 134 else ["unknown_getter_response_shape"]))
        response.submitted_at = clock()
        response.deadline = min(deadline, response.submitted_at + GETTER_RESPONSE_SECONDS)
        zero._observe_response(transport, response, deadline=deadline, clock=clock)
        decoded = interpret_packet(decode_packet(bytes.fromhex(response.events[0]["stream"]["raw_hex"])),
                                   direction="received", evidence="recorded")
        report.update(final_getter_response=response.events, final_telemetry=decoded, status=SUCCESS)
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024],
                      required_operator_action="CUT_POWER_REQUIRED")
        raise
    finally:
        report["planned_zero_attempted"] = transport.writes >= 2
        report["planned_zero_fully_accepted"] = (
            transport.writes >= 2 and transport.accepted_tx_bytes >= len(START) + len(PLANNED_ZERO)
            and transport.uncertain_tx_bytes == 0)
        errors = []
        if response is not None:
            try:
                response.finish(clock())
            except BaseException as error:
                errors.append(error)
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            errors.append(error)
        report.update(application_submission_attempts=transport.writes,
                      accepted_tx_bytes=transport.accepted_tx_bytes,
                      uncertain_tx_bytes=transport.uncertain_tx_bytes,
                      serial_rx_bytes=transport.serial_bytes,
                      cleanup_errors=[str(error)[:1024] for error in errors],
                      application_acknowledgment="not_established",
                      physical_stop="not_established")
        _marker("END", "software collection ended; operator physical motion/stop observations remain external")
        if errors:
            report["status"] = "failed"
            if primary is None:
                raise errors[0]
            for error in errors:
                primary.add_note(f"Additional finalization error: {error}")


def _validate_capture(options):
    if options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation"):
        raise ValueError("Powered-left capture forbids other diagnostic profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(actuators_isolated=options.get("actuators_isolated", False),
                        **declarations) != "powered_left_stop_characterization":
        raise ValueError("Powered-left capture requires its complete literal scope.")


def run_characterization(output, *, expected_physical_port, run=False,
                         actuators_isolated=False, **declarations):
    try:
        if run is not True or consent.classify(
                actuators_isolated=actuators_isolated,
                **declarations) != "powered_left_stop_characterization":
            raise ValueError("Literal --run and the powered-left scope are required.")
        return zero._run_diagnostic(
            output, expected_physical_port=expected_physical_port, review=prepare(),
            transport_type=_PoweredLeftTransport, observe=_observe,
            limits=zero._Limits(first_sequence=3072, max_requests=3, interval=0),
            session_options={"actuators_isolated": False, **declarations},
            declarations={**consent.powered_trial_history(declarations), "run_id": str(uuid.uuid4())},
            expected_tx=38, success_status=SUCCESS,
            report_key="powered_left_stop_observation",
            authorizations={"unvalidated_left_one_and_zero_authorized": True},
            capture_validator=_validate_capture,
            on_failure=consent.notify_powered_trial_fault,
            serial_seconds=SERIAL_SECONDS,
        )
    except BaseException as error:
        consent.notify_powered_trial_fault(error)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--actuators-isolated", action="store_true")
    consent.add_arguments(parser)
    consent.add_observation_arguments(parser)
    consent.add_powered_trial_arguments(parser)
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    args = consent.parse_observation_arguments(parser, argv)
    declarations = {**consent.arguments(args), **consent.observation_arguments(args),
                    **consent.powered_trial_arguments(args)}
    try:
        if args.actuators_isolated or any(declarations.values()):
            _validate_capture({"actuators_isolated": args.actuators_isolated, **declarations})
        if args.run and args.output is None:
            raise ValueError("--output NEWDIR is required.")
        result = prepare() if not args.run else run_characterization(
            args.output, expected_physical_port=args.expected_physical_port,
            run=args.run, actuators_isolated=args.actuators_isolated, **declarations)
    except (Exception, KeyboardInterrupt) as error:
        consent.notify_powered_trial_fault(error)
        print(json.dumps({"status": "failed", "error": str(error),
                          "physical_stop": "not_established"}), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
