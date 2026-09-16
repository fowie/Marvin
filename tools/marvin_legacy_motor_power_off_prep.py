"""Fixed SOFTWARE preparation for a separately authorized motor-power-OFF run.

Offline by default. No motor-supply-ON authorization, retries or corrective zero.
MotorL remains attached: full actuator isolation must NOT be asserted.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet
from tools.marvin_legacy_telemetry import interpret_packet
from tools import marvin_legacy_zero as zero


TRANSCRIPT = tuple(bytes.fromhex(raw) for raw in (
    "5300061100040000000000e4a145",
    "53010600000000ead445", "53020600000000eae745", "53030600000000eb3645",
    "53040600000000ea8145", "53050600000000eb5045",
))
ZERO_FIELDS = (
    ("motorVelocityL", 72), ("motorVelocityR", 74),
    ("motorPwmLeftForward", 90), ("motorPwmLeftReverse", 92),
    ("motorPwmRightForward", 94), ("motorPwmRightReverse", 96),
)
SUCCESS = "preparation_observation_complete_unverified"


def prepare():
    for index, raw in enumerate(TRANSCRIPT):
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                1536 + index, 0 if index else 0x11, 0, b"" if index else bytes(4)):
            raise ValueError("Fixed preparation transcript disagrees with the reviewed plan.")
    review = zero.prepare()
    review.update(
        name="motor_power_off_preparation", sequence=1536,
        immutable_application_transcript_hex=[raw.hex() for raw in TRANSCRIPT],
        maximum_application_bytes=sum(map(len, TRANSCRIPT)), maximum_writes=6,
        quiet_seconds=1, getter_response_seconds=0.5, reply_to_request_idle_seconds=1,
        response_classification="correlation_only_semantics_unverified_not_ACK",
        zero_response_payload_bytes=0, getter_response_payload_bytes=134,
        required_zero_fields={name: offset for name, offset in ZERO_FIELDS},
        required=["--run", "--expected-physical-port", "--output NEWDIR",
                  *("--" + flag.replace("_", "-") for flag in consent.PREPARATION_FLAGS)],
        declarations_are_current_permission=False, motor_supply_on_permission="not_granted",
        new_boot_basis="fresh_generation_and_operator_declaration_not_enumeration_proof",
        success_label=SUCCESS,
    )
    review["max_read_iterations_per_window"] = review.pop("max_read_iterations")
    review["max_response_events_per_window"] = review.pop("max_response_events")
    return review


class _PreparationTransport(LiveTransport):
    """Exact transcript AND observation state enforced at the syscall boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.phase = "unopened"
        self.operation_deadline = None
        self.not_before = None
        self.response = None
        self.accepted_tx_bytes = 0
        self.uncertain_tx_bytes = 0

    def revalidate(self, *, deadline):
        if self.phase != "unopened" or self.operation_deadline is not None:
            self.phase = "failed"
            raise OSError("Preparation is single-use.")
        self.operation_deadline = deadline
        try:
            token = super().revalidate(deadline=deadline)
            self.phase = "ready"
            self.not_before = time.monotonic()
            return token
        except BaseException:
            self.phase = "failed"
            raise

    def _read_serial(self, size):
        remaining = self.plan.max_rx_bytes - self.serial_bytes
        if remaining <= 0:
            raise OSError("Preparation total serial RX budget exhausted.")
        return super()._read_serial(min(size, remaining))

    def write(self, data, *, deadline):
        return self._submit_once(data, deadline=deadline)

    def _submit_once(self, data, *, deadline):
        before = self.writes
        accounted = False
        try:
            self._check(deadline)
            if (deadline != self.operation_deadline or self.phase != "ready"
                    or self.writes >= len(TRANSCRIPT) or type(data) is not bytes
                    or data != TRANSCRIPT[self.writes] or self.not_before is None
                    or time.monotonic() < self.not_before):
                raise OSError("Preparation transcript/phase/deadline gate refused submission.")
            window = 3 if self.writes == 0 else 0.5
            if time.monotonic() + window >= deadline:
                raise OSError("Insufficient shared deadline for the full response window.")
            self.phase = "awaiting_response"
            count = super()._submit_once(data, deadline=deadline)
            if type(count) is int and 0 <= count <= len(data):
                self.accepted_tx_bytes += count
                self.uncertain_tx_bytes += len(data) - count
            else:
                self.uncertain_tx_bytes += len(data)
            accounted = True
            if type(count) is not int or count != len(data):
                raise OSError("Uncertain/short preparation write; no retry.")
            return count
        except BaseException:
            if self.writes > before and not accounted:
                self.uncertain_tx_bytes += len(data)
            self.phase = "failed"
            raise

    def complete_observation(self, response):
        if (self.phase != "awaiting_response" or response is not self.response
                or response.candidates != 1 or len(response.events) != 1
                or time.monotonic() < response.deadline
                or time.monotonic() >= self.operation_deadline):
            self.phase = "failed"
            raise OSError("Preparation observation phase gate refused continuation.")
        self.not_before = max(response.deadline, response.events[0]["ended_at"] + 1)
        self.phase = "complete" if self.writes == len(TRANSCRIPT) else "ready"


def _packet_validation(transport, report, index, packet):
    if index == 0:
        return [] if packet.payload == b"" else ["unknown_zero_response_shape"]
    decoded = interpret_packet(packet, direction="received", evidence="recorded")
    report["telemetry"].append(decoded)
    transport.event("preparation_telemetry", interpretation=decoded)
    if decoded["status"] != "decoded" or len(packet.payload) != 134 or packet.command != 0:
        return ["unknown_getter_response_shape"]
    for name, offset in ZERO_FIELDS:
        field = decoded["fields"][name]
        if (field["offset"] != offset or field["size"] != 2
                or field["raw_hex"] != packet.payload[offset:offset + 2].hex()
                or field["raw_hex"] != "0000" or field["unsigned"] != 0 or field["signed"] != 0):
            return ["nonzero_or_inconsistent_velocity_or_PWM"]
    return []


def _quiet_until(transport, until, deadline):
    for _ in range(4096):
        if time.monotonic() >= until:
            return
        transport.identity(deadline=deadline)
        transport.ingress.pump()
        data = transport._read_serial(512)
        if data or transport.ingress.rx or transport.ingress.rx_bytes != transport.serial_bytes:
            raise OSError("Unsolicited input during reply-to-request idle; no continuation.")
        time.sleep(min(0.005, max(0, until - time.monotonic())))
    raise OSError("Preparation quiet iteration budget exhausted.")


def _observe(transport, report):
    responses = []
    primary = None
    report.update(telemetry=[], phases=[], accepted_tx_bytes=0, uncertain_tx_bytes=0)
    deadline = time.monotonic() + 15
    report["operational_deadline_monotonic"] = deadline
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh preparation identity differs from the pinned connection.")
        for index, raw in enumerate(TRANSCRIPT):
            _quiet_until(transport, transport.not_before, deadline)
            transport.identity(deadline=deadline)
            response = zero._ResponseEvidence(
                transport.event, sequence=1536 + index, command=0 if index else 0x11,
                validate_packet=lambda packet, index=index: _packet_validation(transport, report, index, packet))
            transport.response = response
            responses.append(response)
            report.update(write_status="attempted", uncertain_tx_bytes=len(raw))
            count = transport.write(raw, deadline=deadline)
            report["accepted_tx_bytes"] += count
            report["uncertain_tx_bytes"] = 0
            response.submitted_at = time.monotonic()
            response.deadline = response.submitted_at + (0.5 if index else 3)
            report.update(write_status="fully_accepted_not_acknowledged")
            phase = {"sequence": 1536 + index, "submitted_at": response.submitted_at,
                     "response_deadline": response.deadline, "events": response.events}
            report["phases"].append(phase)
            if response.deadline > deadline:
                raise OSError("Insufficient shared budget for the complete observation.")
            zero._observe_response(transport, response, deadline=deadline, clock=time.monotonic)
            transport.complete_observation(response)
            phase["observation_window_completed"] = True
        report["status"] = SUCCESS
    except BaseException as error:
        primary = error
        transport.phase = "failed"
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        errors = []
        for response in responses:
            try:
                response.finish(time.monotonic())
            except (OSError, ValueError) as error:
                errors.append(error)
        cleanup_deadline = time.monotonic() + 5
        report["cleanup_deadline_monotonic"] = cleanup_deadline
        try:
            transport.close(deadline=cleanup_deadline)
        except (OSError, ValueError) as error:
            errors.append(error)
        report.update(
            serial_rx_bytes=transport.serial_bytes, application_submission_attempts=transport.writes,
            accepted_tx_bytes=transport.accepted_tx_bytes, uncertain_tx_bytes=transport.uncertain_tx_bytes,
            response_events=[event for response in responses for event in response.events],
            cleanup_errors=[str(error)[:1024] for error in errors],
            response_classification="correlation_only_semantics_unverified_not_ACK",
            application_acknowledgment="not_established", physical_stop="not_established",
            motor_supply_on_permission="not_granted",
        )
        if not transport.writes:
            report.update(write_status="suppressed_before_submission", uncertain_tx_bytes=0)
        if errors:
            report["status"] = "failed"
            if primary is None:
                raise errors[0]
            for error in errors:
                primary.add_note(f"Additional finalization error: {error}")


def run_preparation(output, *, expected_physical_port, run=False, actuators_isolated=False,
                    motor_supply_off=False, motor_left_only_connected=False,
                    motor_right_and_servos_isolated=False, authorize_unvalidated_zero_velocity=False,
                    unprivileged_usbmon=False, new_boot_declared=False):
    declarations = dict(
        motor_supply_off=motor_supply_off, motor_left_only_connected=motor_left_only_connected,
        motor_right_and_servos_isolated=motor_right_and_servos_isolated,
        authorize_unvalidated_zero_velocity=authorize_unvalidated_zero_velocity,
        unprivileged_usbmon=unprivileged_usbmon, new_boot_declared=new_boot_declared,
    )
    if run is not True or not consent.validate(actuators_isolated=actuators_isolated, **declarations):
        raise ValueError("Literal run and all motor-power-OFF declarations required.")
    return zero._run_diagnostic(
        output, expected_physical_port=expected_physical_port, review=prepare(),
        transport_type=_PreparationTransport, observe=_observe,
        limits=zero._Limits(first_sequence=1536, max_requests=6),
        session_options={"_motor_power_off_preparation": True, **declarations},
        declarations={**consent.history(declarations), "run_id": str(uuid.uuid4()),
                      "actuator_power_and_signal_isolation_acknowledged": False},
        expected_tx=64, success_status=SUCCESS,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--actuators-isolated", action="store_true")
    consent.add_arguments(parser)
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    declarations = consent.arguments(args)
    try:
        if args.actuators_isolated or (any(declarations.values()) and not all(declarations.values())):
            raise ValueError("Ambiguous or mixed declarations; this is not an all-actuators-isolated plan.")
        if not args.run:
            result = prepare()
        else:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_preparation(args.output, expected_physical_port=args.expected_physical_port,
                                     run=args.run, actuators_isolated=args.actuators_isolated, **declarations)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error),
                          "motor_supply_on_permission": "not_granted"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
