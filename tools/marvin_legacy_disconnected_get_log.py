"""One fixed read-only GetLog capture with disconnected motor loads.

Offline by default. This is software readiness, not live authorization.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

from tools import marvin_legacy_zero as zero
from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet, get_log_request


SEQUENCE = 3076
REQUEST = get_log_request(SEQUENCE)
TRANSCRIPT = (REQUEST,)
SERIAL_SECONDS = 5
RESPONSE_SECONDS = 0.500
CLEANUP_SECONDS = 5
SUCCESS = "disconnected_load_get_log_observation_complete_unverified"


def prepare():
    packet = decode_packet(REQUEST)
    if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
            SEQUENCE, 0x0C, 0, b""):
        raise ValueError("Fixed GetLog request disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "name": consent.DISCONNECTED_GET_LOG_SCOPE,
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [REQUEST.hex()],
        "request_sha256": hashlib.sha256(REQUEST).hexdigest(),
        "maximum_application_bytes": len(REQUEST),
        "maximum_writes": 1,
        "response_shape": {"command": 0x0C, "response_field": 0x80, "payload_bytes": 32},
        "automatic_retries": False,
        "automatic_reconnect": False,
        "follow_up_requests": False,
        "operational_seconds": SERIAL_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "usb_nominal_seconds": 10,
        "usb_hard_seconds": 15,
        "physical_stop": "not_established",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in consent.DISCONNECTED_GET_LOG_FLAGS)],
    }


class _GetLogTransport(LiveTransport):
    def write(self, data, *, deadline):
        if self.writes or data != REQUEST:
            raise OSError("Only the fixed sequence-3076 GetLog request may be written once.")
        return self._submit_once(data, deadline=deadline)


def _observe(transport, report, *, clock=time.monotonic):
    response = zero._ResponseEvidence(
        transport.event, sequence=SEQUENCE, command=0x0C,
        validate_packet=lambda packet: (
            [] if len(packet.payload) == 32 else ["unknown_get_log_response_shape"]))
    deadline = clock() + SERIAL_SECONDS
    report.update(status="not_started", operational_deadline_monotonic=deadline,
                  write_status="not_attempted", accepted_tx_bytes=0,
                  uncertain_tx_bytes=0, physical_stop="not_established")
    primary = None
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        report.update(write_status="attempted", uncertain_tx_bytes=len(REQUEST))
        count = transport.write(REQUEST, deadline=deadline)
        if type(count) is not int or not 0 <= count <= len(REQUEST):
            raise OSError("Unknown GetLog write result; no retry.")
        report.update(accepted_tx_bytes=count, uncertain_tx_bytes=len(REQUEST) - count)
        if count != len(REQUEST):
            raise OSError("Partial GetLog write; no retry.")
        response.submitted_at = clock()
        response.deadline = min(deadline, response.submitted_at + RESPONSE_SECONDS)
        if response.deadline != response.submitted_at + RESPONSE_SECONDS:
            raise OSError("Insufficient remaining budget for the fixed response window.")
        report.update(write_status="fully_accepted_not_acknowledged",
                      submitted_at=response.submitted_at,
                      response_deadline=response.deadline)
        zero._observe_response(transport, response, deadline=deadline, clock=clock)
        packet = decode_packet(bytes.fromhex(response.events[0]["stream"]["raw_hex"]))
        report.update(status=SUCCESS, response_events=response.events,
                      raw_payload_hex=packet.payload.hex())
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        errors = []
        try:
            response.finish(clock())
        except BaseException as error:
            errors.append(error)
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            errors.append(error)
        report.update(response_events=response.events, serial_rx_bytes=transport.serial_bytes,
                      application_submission_attempts=transport.writes,
                      cleanup_errors=[str(error)[:1024] for error in errors],
                      application_acknowledgment="not_established",
                      physical_stop="not_established")
        if errors:
            report["status"] = "failed"
            if primary is None:
                raise errors[0]
            for error in errors:
                primary.add_note(f"Additional finalization error: {error}")


def _validate_capture(options):
    if options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation"):
        raise ValueError("Disconnected-load GetLog forbids other diagnostic profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) != consent.DISCONNECTED_GET_LOG_SCOPE:
        raise ValueError("Disconnected-load GetLog requires its complete literal scope.")


def run_get_log(output, *, expected_physical_port, run=False,
                actuators_isolated=False, **declarations):
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope != consent.DISCONNECTED_GET_LOG_SCOPE:
        raise ValueError("Literal --run and disconnected-load GetLog scope are required.")
    return zero._run_diagnostic(
        output, expected_physical_port=expected_physical_port, review=prepare(),
        transport_type=_GetLogTransport, observe=_observe,
        limits=zero._Limits(first_sequence=SEQUENCE, max_requests=1, interval=0),
        session_options={"actuators_isolated": False, **declarations},
        declarations={**consent.powered_trial_history(declarations),
                      "run_id": str(uuid.uuid4())},
        expected_tx=len(REQUEST), success_status=SUCCESS,
        report_key="disconnected_load_get_log_observation",
        authorizations={"disconnected_load_get_log_authorized": True},
        capture_validator=_validate_capture,
        on_failure=consent.notify_powered_trial_fault,
        serial_seconds=SERIAL_SECONDS,
    )


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
        result = prepare() if not args.run else run_get_log(
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
