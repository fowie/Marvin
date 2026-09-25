"""Fixed all-zero raw-PWM stop request with optional zero getter evidence.

Offline by default. This requests zero raw PWM; it does not prove braking,
motor de-energization, or physical stop.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

from tools import marvin_legacy_raw_pwm_pilot as pilot
from tools import marvin_legacy_zero as zero
from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet


STEPS = {
    "stop": pilot.STEPS_DUAL_FORWARD_2000_ONE_SECOND["cleanup"],
    "verify": pilot.STEPS_DUAL_FORWARD_2000_ONE_SECOND["verify"],
}
TRANSCRIPT = tuple(STEPS.values())
SERIAL_SECONDS = 10
SUCCESS = "raw_pwm_all_zero_stop_request_complete_unverified"


def prepare():
    stop = decode_packet(STEPS["stop"])
    verify = decode_packet(STEPS["verify"])
    if ((stop.sequence, stop.command, stop.payload) != (3413, 0x0B, bytes(8))
            or (verify.sequence, verify.command, verify.payload) != (3414, 0x0A, b"")):
        raise ValueError("Fixed stop transcript disagrees with the proved pilot suffix.")
    return {
        "status": "dry_run",
        "name": consent.RAW_PWM_STOP_SCOPE,
        "interface": "standalone_bounded_stop_request",
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in TRANSCRIPT],
        "stop_frame_hex": STEPS["stop"].hex(),
        "stop_frame_sha256": hashlib.sha256(STEPS["stop"]).hexdigest(),
        "transcript_sha256": hashlib.sha256(b"".join(TRANSCRIPT)).hexdigest(),
        "fixed_stop_words_uint16": [0, 0, 0, 0],
        "maximum_writes": 2,
        "maximum_application_bytes": sum(map(len, TRANSCRIPT)),
        "maximum_serial_rx_bytes": 8192,
        "maximum_expected_response_bytes": 28,
        "response_policy": (
            "stop requires unique CRC-valid correlated empty raw80; "
            "then getter requires unique CRC-valid correlated raw80 exact eight zero bytes"),
        "automatic_retries": False,
        "automatic_reconnect": False,
        "physical_effect": "operator_observation_not_protocol_evidence",
        "physical_stop": "not_established",
        "braking": "not_established",
        "existing_live_evidence": (
            "literal stop frame transmitted as cleanup before observed stopped wheels; "
            "causation not established"),
        "further_live_proof_needed": False,
        "required": [
            "--run",
            "--expected-physical-port",
            "--output NEWDIR",
            *("--" + name.replace("_", "-")
              for name in consent.RAW_PWM_STOP_FLAGS),
        ],
    }


class _StopTransport(LiveTransport):
    steps = STEPS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed = []
        self.stop_raw80 = False

    def submit(self, step, *, deadline):
        if step == "stop":
            if self.completed:
                raise OSError("All-zero stop request must be first and attempted once.")
        elif step == "verify":
            if self.completed != ["stop"] or not self.stop_raw80:
                raise OSError("Zero getter requires a clean raw-80 stop response.")
        else:
            raise OSError("Only the fixed stop request and conditional getter are permitted.")
        count = self._submit_once(self.steps[step], deadline=deadline)
        if count == len(self.steps[step]):
            self.completed.append(step)
        return count


def _observe(transport, report, *, clock=time.monotonic):
    deadline = clock() + SERIAL_SECONDS
    report.update(
        status="not_started", accepted_tx_bytes=0, uncertain_tx_bytes=0,
        responses=[], stop_request_submission_started=False, getter_reverified=False,
        application_acknowledgment="not_established",
        operator_observation="not_collected", physical_stop="not_established",
    )
    try:
        if transport.revalidate(deadline=deadline) != transport.token:
            raise OSError("Fresh transport identity differs from the pinned connection.")
        report["stop_request_submission_started"] = True
        pilot._submit(transport, report, "stop", deadline=deadline)
        packet = pilot._response(
            transport, report, "stop", deadline=deadline, clock=clock)
        report["stop_raw_response_field"] = packet.response_field
        if packet.response_field != 0x80:
            raise OSError("All-zero stop response is opaque; zero getter suppressed.")
        transport.stop_raw80 = True
        pilot._submit(transport, report, "verify", deadline=deadline)
        pilot._response(
            transport, report, "verify", deadline=deadline, clock=clock)
        report["getter_reverified"] = True
        report["status"] = SUCCESS
    finally:
        transport.close(deadline=clock() + pilot.CLEANUP_SECONDS)
        report.update(
            application_submission_attempts=transport.writes,
            serial_rx_bytes=transport.serial_bytes,
            protocol_result=(
                "zero_raw_pwm_getter_reverified"
                if report["getter_reverified"]
                else "stop_request_result_unverified"),
            physical_stop="not_established",
        )


def _validate_capture(options):
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if (options.get("_isolated_zero_velocity")
            or options.get("_motor_power_off_preparation")
            or consent.classify(
                actuators_isolated=options.get("actuators_isolated", False),
                **declarations) != consent.RAW_PWM_STOP_SCOPE):
        raise ValueError("Standalone stop requires its complete literal scope.")


def run_stop(output, *, expected_physical_port, run=False, **declarations):
    scope = consent.classify(**declarations)
    if run is not True or scope != consent.RAW_PWM_STOP_SCOPE:
        raise ValueError("Literal --run and the fixed standalone stop scope are required.")
    return zero._run_diagnostic(
        output, expected_physical_port=expected_physical_port, review=prepare(),
        transport_type=_StopTransport, observe=_observe,
        limits=zero._Limits(first_sequence=3413, max_requests=2, interval=0),
        session_options=declarations,
        declarations={
            **consent.powered_trial_history(declarations),
            "run_id": str(uuid.uuid4()),
        },
        expected_tx=lambda report: report["accepted_tx_bytes"],
        success_status=SUCCESS, report_key="raw_pwm_stop_request",
        authorizations={"unvalidated_raw_pwm_all_zero_stop_authorized": True},
        capture_validator=_validate_capture,
        on_failure=consent.notify_powered_trial_fault,
        serial_seconds=SERIAL_SECONDS,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--actuators-isolated", action="store_true")
    consent.add_arguments(parser)
    consent.add_observation_arguments(parser)
    consent.add_powered_trial_arguments(parser)
    args = consent.parse_observation_arguments(parser, argv)
    declarations = {
        **consent.arguments(args),
        **consent.observation_arguments(args),
        **consent.powered_trial_arguments(args),
    }
    try:
        if args.run:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_stop(
                args.output, expected_physical_port=args.expected_physical_port,
                run=True, actuators_isolated=args.actuators_isolated,
                **declarations)
        else:
            if (args.output or args.expected_physical_port
                    or args.actuators_isolated or any(declarations.values())):
                raise ValueError("Live-only arguments require --run.")
            result = prepare()
    except (Exception, KeyboardInterrupt) as error:
        if args.run:
            consent.notify_powered_trial_fault(error)
        print(json.dumps({
            "status": "failed",
            "error": str(error),
            "physical_stop": "not_established",
        }), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
