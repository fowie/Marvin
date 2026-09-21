"""Fixed disconnected-load zero/+1/zero order diagnostic.

Offline by default. This is software readiness, not live authorization or a
physical-stop proof. Both motor POWER plugs must remain disconnected.
"""

import argparse
import json
import os
from pathlib import Path
import select
import sys
import time
import uuid

from tools import marvin_legacy_powered_left_stop as powered
from tools import marvin_legacy_zero as zero
from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_protocol import decode_packet
from tools.marvin_legacy_stream import LegacyStreamDecoder
from tools.marvin_legacy_telemetry import interpret_packet


TRANSCRIPT = tuple(bytes.fromhex(raw) for raw in (
    "53000c11000400000000009a0145",
    "53010c1100040001000000ca3845",
    "53020c11000400000000003bcb45",
    "53030c00000000733745",
))
PLUS_1000_TRANSCRIPT = tuple(bytes.fromhex(raw) for raw in (
    "53000c11000400000000009a0145",
    "53010c11000400e80300000e6445",
    "53020c11000400000000003bcb45",
    "53030c00000000733745",
))
INITIAL_ZERO, LEFT_ONE, CLEANUP_ZERO, FINAL_GETTER = TRANSCRIPT
SERIAL_SECONDS = 5
CLEANUP_SECONDS = 5
SETTER_RESPONSE_SECONDS = 0.500
GETTER_RESPONSE_SECONDS = 0.500
SUCCESS = "disconnected_load_zero_one_order_observation_complete_unverified"
PROFILES = {
    consent.DISCONNECTED_ORDER_SCOPE: {
        "transcript": TRANSCRIPT,
        "value": 1,
        "flags": consent.DISCONNECTED_ORDER_FLAGS,
        "authorization": "unvalidated_zero_one_order_diagnostic_authorized",
        "success": SUCCESS,
        "report_key": "disconnected_load_zero_one_order_observation",
    },
    consent.DISCONNECTED_PLUS_1000_SCOPE: {
        "transcript": PLUS_1000_TRANSCRIPT,
        "value": 1000,
        "flags": consent.DISCONNECTED_PLUS_1000_FLAGS,
        "authorization": "unvalidated_left_plus_1000_order_diagnostic_authorized",
        "success": "disconnected_load_zero_plus_1000_order_observation_complete_unverified",
        "report_key": "disconnected_load_zero_plus_1000_order_observation",
    },
}


def transcript_for_scope(scope):
    try:
        return PROFILES[scope]["transcript"]
    except KeyError:
        raise ValueError("Unknown disconnected-load order scope.") from None


def prepare(scope=consent.DISCONNECTED_ORDER_SCOPE):
    profile = PROFILES[scope]
    transcript = profile["transcript"]
    value = profile["value"]
    expected = (
        (3072, 0x11, bytes(4)),
        (3073, 0x11, value.to_bytes(2, "little", signed=True) + bytes(2)),
        (3074, 0x11, bytes(4)),
        (3075, 0x00, b""),
    )
    for raw, (sequence, command, payload) in zip(transcript, expected):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                sequence, command, 0, payload):
            raise ValueError("Fixed disconnected-load transcript disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "name": scope,
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in transcript],
        "maximum_application_bytes": sum(map(len, transcript)),
        "maximum_writes": 4,
        "fixed_left_raw_value": value,
        "source_basis": (
            "Original PCTestApp timer2_Tick signed random range -1000..999 and "
            "captured GetConfig minVel=-3500/maxVel=3500."
            if value == 1000 else "Prior fixed smallest positive raw word diagnostic."),
        "setter_submission": "three_immediate_bounded_successive_syscalls",
        "setter_response_classification": "raw_nonzero_status_correlation_only_semantics_unverified",
        "automatic_retries": False,
        "automatic_reconnect": False,
        "operational_seconds": SERIAL_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "usb_nominal_seconds": 10,
        "usb_hard_seconds": 15,
        "physical_stop": "not_established",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in profile["flags"])],
    }


class _OrderTransport(powered._PoweredLeftTransport):
    """Submit only the fixed adjacent zero/+1/cleanup-zero syscalls."""

    transcript = TRANSCRIPT
    success = SUCCESS

    def run_order(self, *, deadline):
        self._check(deadline)
        self._same_owned_writable_fd()
        self.ingress.pump()
        if (self.ingress.pending or self.ingress.rx or self.ingress.expected_tx
                or self.ingress.outstanding_tx or self.ingress.rx_bytes != self.serial_bytes
                or self._read_serial(self.plan.read_size)):
            raise OSError("Pre-start transport is not clean; transcript suppressed.")
        self.event("disconnected_order_pre_start_intent",
                   transcript_hex=[raw.hex() for raw in self.transcript[:3]])
        os.fsync(self.journal.fileno())
        powered._marker("START", "about to submit fixed disconnected-load zero/+1/zero transcript")

        results = []
        for raw in self.transcript[:3]:
            self.ingress.expected_tx.append(raw)
            self.writes += 1
            try:
                count = os.write(self.fd, raw)
            except BaseException:
                self.uncertain_tx_bytes += len(raw)
                raise
            if type(count) is not int or not 0 <= count <= len(raw):
                self.uncertain_tx_bytes += len(raw)
                raise OSError("Unknown fixed setter write result; no retry.")
            self.accepted_tx_bytes += count
            self.uncertain_tx_bytes += len(raw) - count
            results.append(count)
            if count != len(raw):
                raise OSError("Partial fixed setter write; no retry.")
        powered._marker("CLEANUP_ATTEMPT",
                        f"cleanup zero syscall returned {results[-1]}/{len(self.transcript[2])} bytes")
        self.event("disconnected_order_write_results", accepted_bytes=results)
        return results

    def submit_getter(self, *, deadline):
        self._check(deadline)
        if self.writes != 3 or self.accepted_tx_bytes != sum(map(len, self.transcript[:3])):
            raise OSError("Getter requires three fully accepted fixed setter writes.")
        getter = self.transcript[3]
        count = powered.LiveTransport._submit_once(self, getter, deadline=deadline)
        if type(count) is not int or not 0 <= count <= len(getter):
            self.uncertain_tx_bytes += len(getter)
            raise OSError("Unknown final getter write result; no retry.")
        self.accepted_tx_bytes += count
        self.uncertain_tx_bytes += len(getter) - count
        if count != len(getter):
            raise OSError("Partial final getter write; no retry.")
        return count


class _Plus1000OrderTransport(_OrderTransport):
    transcript = PLUS_1000_TRANSCRIPT
    success = PROFILES[consent.DISCONNECTED_PLUS_1000_SCOPE]["success"]


def _collect_setter_responses(transport, report, *, deadline, clock=time.monotonic):
    decoder = LegacyStreamDecoder(max_input_bytes=8192)
    responses = {}
    response_deadline = min(deadline, clock() + SETTER_RESPONSE_SECONDS)
    for _ in range(4096):
        if clock() >= response_deadline:
            break
        transport.identity(deadline=deadline)
        transport.ingress.pump()
        remaining = response_deadline - clock()
        if remaining <= 0 or not select.select([transport.fd], [], [], min(.005, remaining))[0]:
            continue
        received = transport.read(512, deadline=response_deadline)
        for event in decoder.feed(received.data):
            if event.kind != "frame":
                raise OSError(f"Unclean setter response stream: {event.kind}.")
            packet = event.packet
            if (packet.sequence not in (3072, 3073, 3074)
                    or packet.sequence in responses or packet.command != 0x11
                    or packet.response_field == 0 or packet.payload):
                raise OSError("Unexpected disconnected-load setter response; no getter.")
            row = {
                "raw_hex": packet.raw.hex(),
                "response_field_hex": f"{packet.response_field:02x}",
                "application_acknowledgment": "not_established",
            }
            responses[packet.sequence] = row
            transport.event("disconnected_order_setter_response",
                            sequence=packet.sequence, **row,
                            started_at=received.started_at, ended_at=received.ended_at)
    if decoder.finish() or set(responses) != {3072, 3073, 3074}:
        raise OSError("Three clean correlated setter responses were not observed; no getter.")
    report["setter_responses"] = responses


def _observe(transport, report, *, clock=time.monotonic):
    transcript = transport.transcript
    deadline = clock() + SERIAL_SECONDS
    report.update(status="not_started", operational_deadline_monotonic=deadline,
                  cleanup_zero_attempted=False, cleanup_zero_fully_accepted=False,
                  final_getter_attempted=False, physical_stop="not_established")
    primary = None
    response = None
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        transport.run_order(deadline=deadline)
        report.update(cleanup_zero_attempted=True, cleanup_zero_fully_accepted=True)
        _collect_setter_responses(transport, report, deadline=deadline, clock=clock)
        transport.submit_getter(deadline=deadline)
        report["final_getter_attempted"] = True
        response = zero._ResponseEvidence(
            transport.event, sequence=3075, command=0,
            validate_packet=lambda packet: (
                [] if len(packet.payload) == 134 else ["unknown_getter_response_shape"]))
        response.submitted_at = clock()
        response.deadline = min(deadline, response.submitted_at + GETTER_RESPONSE_SECONDS)
        zero._observe_response(transport, response, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(response.events[0]["stream"]["raw_hex"]))
        report.update(final_getter_response=response.events,
                      final_telemetry=interpret_packet(
                          packet, direction="received", evidence="recorded"),
                      status=transport.success)
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024],
                      required_operator_action="KEEP_MOTOR_POWER_PLUGS_DISCONNECTED")
        raise
    finally:
        report["cleanup_zero_attempted"] = transport.writes >= 3
        report["cleanup_zero_fully_accepted"] = (
            transport.writes >= 3
            and transport.accepted_tx_bytes >= sum(map(len, transcript[:3]))
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
        powered._marker("END", "software collection ended; no physical-stop conclusion")
        if errors:
            report["status"] = "failed"
            if primary is None:
                raise errors[0]
            for error in errors:
                primary.add_note(f"Additional finalization error: {error}")


def _validate_capture(options, scope=consent.DISCONNECTED_ORDER_SCOPE):
    if options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation"):
        raise ValueError("Disconnected-load order capture forbids other diagnostic profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) != scope or scope not in consent.DISCONNECTED_ORDER_SCOPES:
        raise ValueError("Disconnected-load order capture requires its complete literal scope.")


def run_diagnostic(output, *, expected_physical_port, run=False,
                   actuators_isolated=False, **declarations):
    try:
        scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
        if run is not True or scope not in consent.DISCONNECTED_ORDER_SCOPES:
            raise ValueError("Literal --run and disconnected-load setter scope are required.")
        profile = PROFILES[scope]
        transcript = profile["transcript"]
        transport_type = (_Plus1000OrderTransport
                          if scope == consent.DISCONNECTED_PLUS_1000_SCOPE else _OrderTransport)
        return zero._run_diagnostic(
            output, expected_physical_port=expected_physical_port, review=prepare(scope),
            transport_type=transport_type, observe=_observe,
            limits=zero._Limits(first_sequence=3072, max_requests=4, interval=0),
            session_options={"actuators_isolated": False, **declarations},
            declarations={**consent.powered_trial_history(declarations),
                          "run_id": str(uuid.uuid4())},
            expected_tx=sum(map(len, transcript)), success_status=profile["success"],
            report_key=profile["report_key"],
            authorizations={profile["authorization"]: True},
            capture_validator=lambda options: _validate_capture(options, scope),
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
        scope = consent.DISCONNECTED_ORDER_SCOPE
        if args.actuators_isolated or any(declarations.values()):
            scope = consent.classify(actuators_isolated=args.actuators_isolated, **declarations)
            _validate_capture({"actuators_isolated": args.actuators_isolated, **declarations}, scope)
        if args.run and args.output is None:
            raise ValueError("--output NEWDIR is required.")
        result = prepare(scope) if not args.run else run_diagnostic(
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
