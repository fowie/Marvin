"""Six fixed PCTestApp legacy getters with disconnected actuator loads.

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
from tools.marvin_legacy_protocol import (
    decode_packet,
    get_battery_info_request,
    get_led_blink_request,
    get_led_state_request,
    get_motor_velocity_request,
    get_raw_motor_pwm_request,
    get_sensor_info_request,
)


REQUESTS = (
    ("GetRawMotorPWM", 3083, 0x0A, get_raw_motor_pwm_request(3083)),
    ("GetMotorVelocity", 3084, 0x10, get_motor_velocity_request(3084)),
    ("GetLedState", 3085, 0x17, get_led_state_request(3085)),
    ("GetLedBlink", 3086, 0x19, get_led_blink_request(3086)),
    ("GetSensorInfo", 3087, 0x1F, get_sensor_info_request(3087)),
    ("GetBatteryInfo", 3088, 0x28, get_battery_info_request(3088)),
)
TRANSCRIPT = tuple(row[3] for row in REQUESTS)
SERIAL_SECONDS = 10
RESPONSE_SECONDS = 0.500
CLEANUP_SECONDS = 5
SUCCESS = "disconnected_load_legacy_getter_survey_complete_unverified"


def prepare():
    for _, sequence, command, raw in REQUESTS:
        packet = decode_packet(raw)
        if (packet.sequence, packet.command, packet.response_field, packet.payload) != (
                sequence, command, 0, b""):
            raise ValueError("Fixed getter survey disagrees with the legacy decoder.")
    return {
        "status": "dry_run",
        "name": consent.DISCONNECTED_GETTER_SURVEY_SCOPE,
        "profile": "marvin-legacy-se",
        "fixed_order": [name for name, _, _, _ in REQUESTS],
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "transcript_sha256": hashlib.sha256(b"".join(TRANSCRIPT)).hexdigest(),
        "maximum_application_bytes": sum(map(len, TRANSCRIPT)),
        "maximum_writes": len(TRANSCRIPT),
        "response_rule": {
            "response_field": 0x80,
            "payload_bytes": "unknown_preserve_raw",
            "correlation": "matching_sequence_and_command_crc_valid",
        },
        "automatic_retries": False,
        "automatic_reconnect": False,
        "follow_up_after_failure": False,
        "max_serial_rx_bytes": 8192,
        "operational_seconds": SERIAL_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "usb_nominal_seconds": 15,
        "usb_hard_seconds": 20,
        "physical_stop": "not_established",
        "required": ["--run", "--expected-physical-port", "--output NEWDIR",
                     *("--" + name.replace("_", "-")
                       for name in consent.DISCONNECTED_GETTER_SURVEY_FLAGS)],
    }


class _SurveyTransport(LiveTransport):
    def write(self, data, *, deadline):
        if self.writes >= len(TRANSCRIPT) or data != TRANSCRIPT[self.writes]:
            raise OSError("Only the fixed six-request legacy getter survey may be written once.")
        return self._submit_once(data, deadline=deadline)


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + SERIAL_SECONDS
    report.update(status="not_started", operational_deadline_monotonic=deadline,
                  write_status="not_attempted", accepted_tx_bytes=0,
                  uncertain_tx_bytes=0, responses=[], physical_stop="not_established")
    primary = None
    current = None
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        for name, sequence, command, request in REQUESTS:
            current = zero._ResponseEvidence(
                transport.event, sequence=sequence, command=command)
            report.update(write_status="attempted",
                          uncertain_tx_bytes=report["uncertain_tx_bytes"] + len(request))
            count = transport.write(request, deadline=deadline)
            if type(count) is not int or not 0 <= count <= len(request):
                raise OSError(f"Unknown {name} write result; no retry.")
            report["accepted_tx_bytes"] += count
            report["uncertain_tx_bytes"] -= count
            if count != len(request):
                raise OSError(f"Partial {name} write; no retry.")
            current.submitted_at = clock()
            current.deadline = min(deadline, current.submitted_at + RESPONSE_SECONDS)
            if current.deadline != current.submitted_at + RESPONSE_SECONDS:
                raise OSError(f"Insufficient remaining budget for {name} response.")
            report.update(write_status="fully_accepted_not_acknowledged",
                          submitted_at=current.submitted_at,
                          response_deadline=current.deadline)
            zero._observe_response(transport, current, deadline=deadline, clock=clock)
            packet = decode_packet(bytes.fromhex(current.events[0]["stream"]["raw_hex"]))
            report["responses"].append({
                "name": name, "sequence": packet.sequence, "command": packet.command,
                "raw_response_field": packet.response_field,
                "payload_bytes": len(packet.payload), "raw_payload_hex": packet.payload.hex(),
                "events": current.events,
            })
            current = None
        report["status"] = SUCCESS
    except BaseException as error:
        primary = error
        report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        errors = []
        if current is not None:
            try:
                current.finish(clock())
            except BaseException as error:
                errors.append(error)
        try:
            transport.close(deadline=clock() + CLEANUP_SECONDS)
        except BaseException as error:
            errors.append(error)
        report.update(serial_rx_bytes=transport.serial_bytes,
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
        raise ValueError("Disconnected-load getter survey forbids other profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) != consent.DISCONNECTED_GETTER_SURVEY_SCOPE:
        raise ValueError("Disconnected-load getter survey requires its complete literal scope.")


def run_survey(output, *, expected_physical_port, run=False,
               actuators_isolated=False, **declarations):
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope != consent.DISCONNECTED_GETTER_SURVEY_SCOPE:
        raise ValueError("Literal --run and disconnected-load getter survey scope are required.")
    return zero._run_diagnostic(
        output, expected_physical_port=expected_physical_port, review=prepare(),
        transport_type=_SurveyTransport, observe=_observe,
        limits=zero._Limits(first_sequence=REQUESTS[0][1],
                            max_requests=len(REQUESTS), interval=0),
        session_options={"actuators_isolated": False, **declarations},
        declarations={**consent.powered_trial_history(declarations),
                      "run_id": str(uuid.uuid4())},
        expected_tx=sum(map(len, TRANSCRIPT)), success_status=SUCCESS,
        report_key="disconnected_load_legacy_getter_survey",
        authorizations={"disconnected_load_legacy_getter_survey_authorized": True},
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
        result = prepare() if not args.run else run_survey(
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
