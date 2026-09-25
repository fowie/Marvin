"""Fixed legacy projector-power smoke test; offline by default."""

import hashlib
import os
import time

from tools import marvin_legacy_zero as zero
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet, projector_power_request


FIRST_SEQUENCE = 3600
HOLD_SECONDS = 10.0
MAX_HOLD_OVERRUN_SECONDS = 0.01
SERIAL_SECONDS = 15
CLEANUP_SECONDS = 5
RESPONSE_SECONDS = 0.5
STEPS = {
    "on": projector_power_request(FIRST_SEQUENCE, True),
    "off": projector_power_request(FIRST_SEQUENCE + 1, False),
}
ACKNOWLEDGMENTS = (
    "operator_present",
    "robot_stationary_and_projector_output_clear",
    "independent_power_cutoff_ready",
    "drive_and_servo_movement_inactive",
    "projector_connected",
    "host_usb_connected",
    "unprivileged_usbmon",
    "authorize_exact_legacy_27_on_10_seconds_off",
)


def prepare():
    packets = [decode_packet(STEPS[step]) for step in ("on", "off")]
    if [(packet.sequence, packet.command, packet.payload) for packet in packets] != [
            (FIRST_SEQUENCE, 0x27, b"\x01"),
            (FIRST_SEQUENCE + 1, 0x27, b"\x00")]:
        raise ValueError("Fixed projector-power transcript differs from command 0x27.")
    transcript = [STEPS["on"], STEPS["off"]]
    return {
        "status": "dry_run",
        "live_execution_authorized": False,
        "name": "projector_power_on_10_seconds_off",
        "profile": "marvin-legacy-se",
        "command": 0x27,
        "on_payload_uint8": 1,
        "off_payload_uint8": 0,
        "powered_observation_seconds": HOLD_SECONDS,
        "maximum_hold_overrun_seconds": MAX_HOLD_OVERRUN_SECONDS,
        "immutable_application_transcript_hex": [raw.hex() for raw in transcript],
        "transcript_sha256": hashlib.sha256(b"".join(transcript)).hexdigest(),
        "maximum_writes": 2,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "response_policy": (
            "retain one CRC-valid sequence/command-correlated empty response per "
            "write and its raw response field; never treat it as an application ACK"
        ),
        "cleanup_policy": (
            "after ON may reach the syscall, attempt exactly one OFF in finally "
            "after fresh identity validation, including Ctrl-C and failures"
        ),
        "physical_power_state": "not_established",
        "required": [
            "--run", "--expected-physical-port PORT", "--output NEWDIR",
            *("--" + name.replace("_", "-") for name in ACKNOWLEDGMENTS),
        ],
        "refused": [
            "generic_0f_power_mask", "servo_movement", "arbitrary_payload_or_hold",
            "retry_reconnect_or_persistent_on",
        ],
    }


class _Transport(LiveTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_may_have_applied = False
        self.off_attempted = False
        self.completed = []

    def submit(self, step, *, deadline):
        if step == "off":
            return self._off_once(deadline=deadline)
        if step != "on" or self.completed:
            raise OSError("Only the fixed projector ON then OFF transcript is permitted.")
        self.on_may_have_applied = True
        count = self._submit_once(STEPS["on"], deadline=deadline)
        if count == len(STEPS["on"]):
            self.completed.append("on")
        return count

    def _off_once(self, *, deadline):
        if self.off_attempted:
            raise OSError("Projector OFF has already been attempted; no retry.")
        if not self.on_may_have_applied:
            raise OSError("Projector OFF cleanup is admitted only after ON may apply.")
        self._owner()
        self._check(deadline)
        raw = STEPS["off"]
        self.off_attempted = True
        self.ingress.expected_tx.append(raw)
        self.writes += 1
        self.last_write_sequence = decode_packet(raw).sequence
        self.last_write_started = time.monotonic()
        count = os.write(self.fd, raw)
        self.last_write = time.monotonic()
        self.event("projector_power_off_returned", raw_hex=raw.hex(), accepted_bytes=count)
        if count == len(raw):
            self.completed.append("off")
        return count


def _submit(transport, report, step, *, deadline):
    raw = STEPS[step]
    report["uncertain_tx_bytes"] += len(raw)
    count = transport.submit(step, deadline=deadline)
    if type(count) is not int or not 0 <= count <= len(raw):
        raise OSError(f"Unknown projector {step} write result; no retry.")
    report["accepted_tx_bytes"] += count
    report["uncertain_tx_bytes"] -= count
    if count != len(raw):
        raise OSError(f"Partial projector {step} write; no retry.")


def _response(transport, report, step, *, deadline, clock=time.monotonic):
    request = decode_packet(STEPS[step])
    evidence = zero._ResponseEvidence(
        transport.event,
        sequence=request.sequence,
        command=request.command,
        accepted_response_fields=tuple(range(256)),
        validate_packet=lambda packet: (
            [] if packet.payload == b"" else ["unexpected_projector_power_payload"]),
    )
    evidence.submitted_at = transport.last_write_started
    evidence.deadline = min(deadline, evidence.submitted_at + RESPONSE_SECONDS)
    try:
        zero._observe_response(transport, evidence, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(evidence.events[0]["stream"]["raw_hex"]))
        report["responses"].append({
            "step": step,
            "sequence": packet.sequence,
            "command": packet.command,
            "raw_response_field": packet.response_field,
            "application_acknowledgment": "not_established",
        })
        return packet
    finally:
        evidence.finish(clock())


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + SERIAL_SECONDS
    report.update(
        status="not_started",
        accepted_tx_bytes=0,
        uncertain_tx_bytes=0,
        responses=[],
        on_may_have_applied=False,
        off_attempted=False,
        off_correlated=False,
        powered_observation_seconds=HOLD_SECONDS,
        application_acknowledgment="not_established",
        physical_power_state="not_established",
    )
    primary = None
    final_errors = []
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        if transport.identity(deadline=deadline) != transport.token:
            raise OSError("Projector ON identity differs from the pinned connection.")
        _submit(transport, report, "on", deadline=deadline)
        _response(transport, report, "on", deadline=deadline, clock=clock)
        hold_started = clock()
        report["hold_started_monotonic"] = hold_started
        transport.wait(HOLD_SECONDS)
        report["actual_hold_seconds"] = clock() - hold_started
        if not HOLD_SECONDS <= report["actual_hold_seconds"] <= (
                HOLD_SECONDS + MAX_HOLD_OVERRUN_SECONDS):
            raise OSError("Projector powered observation missed its fixed 10.0-second window.")
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        if transport.on_may_have_applied and not transport.off_attempted:
            try:
                if transport.identity(deadline=deadline) != transport.token:
                    raise OSError("Projector OFF identity differs from the pinned connection.")
                report["off_identity_revalidated"] = True
                _submit(transport, report, "off", deadline=deadline)
                _response(transport, report, "off", deadline=deadline, clock=clock)
                report["off_correlated"] = True
            except BaseException as error:
                final_errors.append(("off", error))
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            final_errors.append(("close", error))
        report.update(
            on_may_have_applied=transport.on_may_have_applied,
            off_attempted=transport.off_attempted,
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            finalization_errors=[
                {"step": step, "error": f"{type(error).__name__}: {error}"[:1024]}
                for step, error in final_errors
            ],
            physical_power_state="not_established",
            restoration=(
                "off_response_correlated_physical_power_unproved"
                if report["off_correlated"]
                else "off_attempted_but_power_state_uncertain"
                if transport.off_attempted
                else "off_not_attempted_identity_or_transport_unavailable"),
        )
        if primary is None and not final_errors:
            report["status"] = "projector_power_smoke_complete_protocol_only"
        elif final_errors:
            report["status"] = "failed"
            if primary is None:
                raise final_errors[0][1]
            for step, error in final_errors:
                primary.add_note(f"Additional projector {step} error: {error}")


def run_smoke(output, *, expected_physical_port, run=False, **acknowledgments):
    if run is not True or set(acknowledgments) != set(ACKNOWLEDGMENTS):
        raise ValueError("Literal --run and the complete projector-power scope are required.")
    if any(acknowledgments[name] is not True for name in ACKNOWLEDGMENTS):
        raise ValueError("Every projector-power acknowledgment must be literal true.")
    return zero._run_diagnostic(
        output,
        expected_physical_port=expected_physical_port,
        review=prepare(),
        transport_type=_Transport,
        observe=_observe,
        limits=zero._Limits(
            first_sequence=FIRST_SEQUENCE, max_requests=2, interval=0),
        session_options={
            "actuators_isolated": True,
            "_projector_power_smoke": True,
        },
        declarations={"operator_declarations": dict(acknowledgments)},
        expected_tx=lambda report: report["accepted_tx_bytes"],
        success_status="projector_power_smoke_complete_protocol_only",
        report_key="projector_power_smoke",
        authorizations={"single_legacy_27_projector_power_smoke_authorized": True},
        serial_seconds=SERIAL_SECONDS,
    )
