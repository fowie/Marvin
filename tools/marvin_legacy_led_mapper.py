"""Two-phase legacy LED mapper: set one index to 255, then restore its baseline.

Offline by default. This is software readiness, not live authorization.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import time
import uuid

from tools import marvin_legacy_zero as zero
from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_live import LiveTransport
from tools.marvin_legacy_protocol import decode_packet
from tools.marvin_paths import new_output_path
from tools.marvin_protocol import crc16


STATE_FILE = ".marvin-led-mapping-state.json"
SERIAL_SECONDS = 5
RESPONSE_SECONDS = 0.500
CLEANUP_SECONDS = 5
SET_COMPLETE = "led_mapping_set_phase_complete_restore_required"
RESTORED = "led_mapping_restore_verified"
RESTORE_REJECTED = "led_mapping_restore_rejected_power_cycle_required"


def _index(value):
    if type(value) is not int or not 0 <= value <= 17:
        raise ValueError("LED index must be an integer from 0 through 17.")
    return value


def _frame(sequence, command, payload=b""):
    body = b"\x53" + struct.pack("<HBBH", sequence, command, 0, len(payload)) + payload
    return body + struct.pack("<H", crc16(body)) + b"\x45"


def sequences(index):
    first = 3200 + 4 * _index(index)
    return first, first + 1, first + 2, first + 3


def test_vector(index):
    payload = bytearray(18)
    payload[_index(index)] = 255
    return bytes(payload)


def transcript_for(phase, index, baseline=None):
    baseline_sequence, set_sequence, restore_sequence, verify_sequence = sequences(index)
    if phase == "set":
        return (
            _frame(baseline_sequence, 0x17),
            _frame(set_sequence, 0x18, test_vector(index)),
        )
    if phase == "restore":
        if type(baseline) is not bytes or len(baseline) != 18:
            raise ValueError("Restore requires the exact captured 18-byte baseline.")
        return (
            _frame(restore_sequence, 0x18, baseline),
            _frame(verify_sequence, 0x17),
        )
    raise ValueError("LED mapping phase must be set or restore.")


def prepare_set(index):
    transcript = transcript_for("set", index)
    return {
        "status": "dry_run",
        "name": consent.LED_MAPPING_SCOPE,
        "phase": "set",
        "index": _index(index),
        "fixed_value": 255,
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in transcript],
        "transcript_sha256": hashlib.sha256(b"".join(transcript)).hexdigest(),
        "maximum_application_bytes": sum(map(len, transcript)),
        "maximum_writes": 2,
        "max_serial_rx_bytes": 8192,
        "response_fields_recorded": [0x80, 0x82],
        "automatic_retries": False,
        "automatic_reconnect": False,
        "result": "RESTORE_REQUIRED_regardless_of_setter_response",
        "operator_action": "observe_LED_then_run_restore_phase",
        "physical_led_effect": "not_established",
    }


def prepare_restore(index, baseline):
    transcript = transcript_for("restore", index, baseline)
    return {
        "status": "dry_run",
        "name": consent.LED_MAPPING_SCOPE,
        "phase": "restore",
        "index": _index(index),
        "fixed_value": 255,
        "baseline_payload_hex": baseline.hex(),
        "profile": "marvin-legacy-se",
        "immutable_application_transcript_hex": [raw.hex() for raw in transcript],
        "transcript_sha256": hashlib.sha256(b"".join(transcript)).hexdigest(),
        "maximum_application_bytes": sum(map(len, transcript)),
        "maximum_writes": 2,
        "max_serial_rx_bytes": 8192,
        "automatic_retries": False,
        "automatic_reconnect": False,
        "verification_getter_gate": "only_after_matching_crc_valid_raw80_restore_response",
        "raw82_result": "restoration_not_established_power_cycle_required",
    }


def _state_path(root):
    return Path(root) / STATE_FILE


def _load_state(root):
    path = _state_path(root)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
        raise ValueError("LED mapping state must be a small regular file, not a symlink.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or type(value.get("status")) is not str:
        raise ValueError("LED mapping state is malformed.")
    return value


def _write_state(root, value):
    path = _state_path(root)
    temporary = path.with_name(f"{STATE_FILE}.{os.getpid()}.tmp")
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise OSError("Uncertain LED mapping state write.")
        os.fsync(fd)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(fd)
    os.replace(temporary, path)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _evidence_path(value):
    supplied = Path(value)
    absolute = supplied if supplied.is_absolute() else Path.cwd() / supplied
    for path in (absolute, *absolute.parents):
        try:
            if path.is_symlink():
                raise ValueError("Set-phase evidence path and ancestors must not be symlinks.")
        except OSError as error:
            raise ValueError("Could not validate the set-phase evidence path.") from error
    return absolute.resolve(strict=True)


def _verify_manifest(output):
    output = Path(output).resolve(strict=True)
    manifest = output / "SHA256SUMS"
    raw = manifest.read_bytes()
    entries = {}
    for line in raw.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\0]+)", line)
        if not match or match.group(2) in entries:
            raise ValueError("Set-phase SHA256SUMS is malformed.")
        relative = Path(match.group(2))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Set-phase SHA256SUMS contains an unsafe path.")
        path = output / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("Set-phase SHA256SUMS entry is missing or unsafe.")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != match.group(1):
            raise ValueError("Set-phase evidence hash mismatch.")
        entries[relative.as_posix()] = digest
    paths = list(output.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Set-phase evidence contains a symlink.")
    actual = {
        path.relative_to(output).as_posix()
        for path in paths if path.is_file() and path != manifest
    }
    if set(entries) != actual:
        raise ValueError("Set-phase SHA256SUMS does not cover the exact evidence file set.")
    return hashlib.sha256(raw).hexdigest()


class _MappingTransport(LiveTransport):
    transcript = ()

    def write(self, data, *, deadline):
        if self.writes >= len(self.transcript) or data != self.transcript[self.writes]:
            raise OSError("Only the current fixed LED mapping phase may be written once.")
        return self._submit_once(data, deadline=deadline)


def _transport_type(transcript):
    class FixedMappingTransport(_MappingTransport):
        pass
    FixedMappingTransport.transcript = transcript
    return FixedMappingTransport


def _write(transport, report, request, *, deadline):
    report["uncertain_tx_bytes"] += len(request)
    count = transport.write(request, deadline=deadline)
    if type(count) is not int or not 0 <= count <= len(request):
        raise OSError("Unknown LED mapping write result; no retry.")
    report["accepted_tx_bytes"] += count
    report["uncertain_tx_bytes"] -= count
    if count != len(request):
        raise OSError("Partial LED mapping write; no retry.")


def _response(transport, report, request, *, deadline, expected_payload=None,
              accepted_response_fields=(0x80,), clock=time.monotonic):
    sent = decode_packet(request)
    evidence = zero._ResponseEvidence(
        transport.event, sequence=sent.sequence, command=sent.command,
        accepted_response_fields=accepted_response_fields,
        validate_packet=(
            None if expected_payload is None else
            lambda packet: ([] if packet.payload == expected_payload
                            else ["unexpected_led_mapping_payload"])))
    evidence.submitted_at = clock()
    evidence.deadline = min(deadline, evidence.submitted_at + RESPONSE_SECONDS)
    if evidence.deadline != evidence.submitted_at + RESPONSE_SECONDS:
        raise OSError("Insufficient LED mapping response budget.")
    zero._observe_response(transport, evidence, deadline=deadline, clock=clock)
    packet = decode_packet(bytes.fromhex(evidence.events[0]["stream"]["raw_hex"]))
    report.setdefault("responses", []).append({
        "sequence": packet.sequence,
        "command": packet.command,
        "raw_response_field": packet.response_field,
        "payload_bytes": len(packet.payload),
        "raw_payload_hex": packet.payload.hex(),
        "events": evidence.events,
    })
    return packet


def _finalize(transport, report, primary, *, clock):
    errors = []
    try:
        transport.close(deadline=clock() + CLEANUP_SECONDS)
    except BaseException as error:
        errors.append(error)
    report.update(
        serial_rx_bytes=transport.serial_bytes,
        application_submission_attempts=transport.writes,
        cleanup_errors=[str(error)[:1024] for error in errors],
        application_acknowledgment="not_established",
        physical_stop="not_established",
    )
    if errors:
        report["status"] = "failed"
        if primary is None:
            raise errors[0]
        for error in errors:
            primary.add_note(f"Additional finalization error: {error}")


def _set_observer(transcript, index, baseline_callback):
    def observe(transport, report, *, clock=time.monotonic):
        deadline = clock() + SERIAL_SECONDS
        report.update(
            status="not_started", accepted_tx_bytes=0, uncertain_tx_bytes=0,
            responses=[], index=index, fixed_value=255, baseline_payload_hex=None,
            setter_may_have_been_submitted=False, restore_required=False,
            operator_led_observation="pending_external_observation",
            physical_led_effect="not_established", physical_stop="not_established",
        )
        primary = None
        try:
            if transport.revalidate(deadline=deadline) != transport.token:
                raise OSError("Fresh transport identity differs from the pinned connection.")
            _write(transport, report, transcript[0], deadline=deadline)
            baseline = _response(
                transport, report, transcript[0], deadline=deadline,
                accepted_response_fields=(0x80,),
                clock=clock).payload
            if len(baseline) != 18:
                raise OSError("GetLedState baseline must be exactly 18 bytes.")
            report["baseline_payload_hex"] = baseline.hex()
            baseline_callback(baseline)
            report["setter_may_have_been_submitted"] = True
            _write(transport, report, transcript[1], deadline=deadline)
            setter = _response(
                transport, report, transcript[1], deadline=deadline,
                accepted_response_fields=(0x80, 0x82), clock=clock)
            report.update(
                setter_raw_response_field=setter.response_field,
                restore_required=True,
                status=SET_COMPLETE,
                operator_action="RESTORE_REQUIRED",
            )
        except BaseException as error:
            primary = error
            report.update(
                setter_may_have_been_submitted=(
                    report["setter_may_have_been_submitted"] or transport.writes >= 2),
                restore_required=(
                    report["setter_may_have_been_submitted"] or transport.writes >= 2),
                status="failed", error=f"{type(error).__name__}: {error}"[:1024])
            raise
        finally:
            _finalize(transport, report, primary, clock=clock)
    return observe


def _restore_observer(transcript, index, baseline, operator_observation):
    def observe(transport, report, *, clock=time.monotonic):
        deadline = clock() + SERIAL_SECONDS
        report.update(
            status="not_started", accepted_tx_bytes=0, uncertain_tx_bytes=0,
            responses=[], index=index, baseline_payload_hex=baseline.hex(),
            operator_led_observation=operator_observation,
            restore_attempted=False, restore_response_verified=False,
            baseline_reverified=False, restoration="not_established",
            physical_led_effect="not_established", physical_stop="not_established",
        )
        primary = None
        try:
            if transport.revalidate(deadline=deadline) != transport.token:
                raise OSError("Fresh transport identity differs from the pinned connection.")
            report["restore_attempted"] = True
            _write(transport, report, transcript[0], deadline=deadline)
            setter = _response(
                transport, report, transcript[0], deadline=deadline,
                accepted_response_fields=(0x80, 0x82), clock=clock)
            report["restore_raw_response_field"] = setter.response_field
            if setter.response_field == 0x82:
                report.update(
                    status=RESTORE_REJECTED,
                    restoration="not_established",
                    operator_action="POWER_CYCLE_RESET_REQUIRED_BEFORE_NEXT_INDEX",
                )
                return
            report["restore_response_verified"] = True
            _write(transport, report, transcript[1], deadline=deadline)
            _response(
                transport, report, transcript[1], deadline=deadline,
                expected_payload=baseline, accepted_response_fields=(0x80,), clock=clock)
            report.update(
                baseline_reverified=True,
                restoration="getter_baseline_reverified",
                status=RESTORED,
            )
        except BaseException as error:
            primary = error
            report.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
            raise
        finally:
            _finalize(transport, report, primary, clock=clock)
    return observe


def _validate_capture(options):
    if options.get("_isolated_zero_velocity") or options.get("_motor_power_off_preparation"):
        raise ValueError("LED mapping forbids other diagnostic profiles.")
    declarations = {name: options.get(name, False) for name in consent.ALL_FLAGS}
    if consent.classify(
            actuators_isolated=options.get("actuators_isolated", False),
            **declarations) != consent.LED_MAPPING_SCOPE:
        raise ValueError("LED mapping requires its complete literal disconnected-load scope.")


def _run_live(output, *, phase, index, baseline, observe, declarations,
              expected_physical_port):
    transcript = transcript_for(phase, index, baseline)
    return zero._run_diagnostic(
        output, expected_physical_port=expected_physical_port,
        review=prepare_set(index) if phase == "set" else prepare_restore(index, baseline),
        transport_type=_transport_type(transcript), observe=observe,
        limits=zero._Limits(first_sequence=sequences(index)[0], max_requests=2, interval=0),
        session_options={
            "actuators_isolated": False,
            "_led_mapping_phase": phase,
            "_led_mapping_index": index,
            "_led_mapping_baseline": baseline,
            **declarations,
        },
        declarations={**consent.powered_trial_history(declarations),
                      "run_id": str(uuid.uuid4())},
        expected_tx=lambda report: report["accepted_tx_bytes"],
        success_status=f"led_mapping_{phase}_phase_complete_unverified",
        report_key=f"led_mapping_{phase}_phase",
        authorizations={"unvalidated_led_mapping_phase_authorized": True},
        capture_validator=_validate_capture,
        on_failure=consent.notify_powered_trial_fault,
        serial_seconds=SERIAL_SECONDS,
    )


def run_set(output, *, index, expected_physical_port, run=False,
            actuators_isolated=False, **declarations):
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope != consent.LED_MAPPING_SCOPE:
        raise ValueError("Literal --run and disconnected-load LED mapping scope are required.")
    output = new_output_path(output)
    root = output.parent
    previous = _load_state(root)
    if previous and previous["status"] not in (
            "restored", "power_cycle_reset_confirmed", "set_aborted_before_baseline"):
        raise ValueError("RESTORE_REQUIRED: finish or power-cycle-confirm the prior LED round.")
    state = {
        "status": "set_started",
        "index": _index(index),
        "set_evidence": str(output),
        "created_at": time.time(),
    }
    _write_state(root, state)

    def baseline_callback(baseline):
        state.update(status="baseline_captured", baseline_payload_hex=baseline.hex())
        _write_state(root, state)

    try:
        result = _run_live(
            output, phase="set", index=index, baseline=None,
            observe=_set_observer(transcript_for("set", index), index, baseline_callback),
            declarations=declarations, expected_physical_port=expected_physical_port)
    except BaseException:
        metadata = output / "metadata.json"
        if metadata.is_file():
            observation = json.loads(metadata.read_text(encoding="utf-8")).get("observation", {})
            if observation.get("setter_may_have_been_submitted"):
                state.update(
                    status="restore_required",
                    baseline_payload_hex=observation.get(
                        "baseline_payload_hex", state.get("baseline_payload_hex")),
                    set_phase_status="failed_after_possible_setter_submission",
                )
            elif state["status"] == "set_started":
                state["status"] = "set_aborted_before_baseline"
            else:
                state["status"] = "restore_required"
        elif state["status"] == "set_started":
            state["status"] = "set_aborted_before_baseline"
        else:
            state["status"] = "restore_required"
        _write_state(root, state)
        raise
    digest = _verify_manifest(output)
    observation = result["observation"]
    state.update(
        status="restore_required",
        baseline_payload_hex=observation["baseline_payload_hex"],
        setter_raw_response_field=observation["setter_raw_response_field"],
        set_manifest_sha256=digest,
        set_phase_status=observation["status"],
    )
    _write_state(root, state)
    return result


def _bound_set_state(set_evidence):
    evidence = _evidence_path(set_evidence)
    root = evidence.parent
    state = _load_state(root)
    if not state or state.get("set_evidence") != str(evidence):
        raise ValueError("Restore must name the exact active set-phase evidence path.")
    if state["status"] != "restore_required":
        raise ValueError("The named LED set phase is not awaiting restoration.")
    digest = _verify_manifest(evidence)
    if state.get("set_manifest_sha256") != digest:
        raise ValueError("Set-phase manifest digest differs from the active mapping state.")
    baseline = bytes.fromhex(state["baseline_payload_hex"])
    if len(baseline) != 18:
        raise ValueError("Active mapping baseline is not exactly 18 bytes.")
    metadata = json.loads((evidence / "metadata.json").read_text(encoding="utf-8"))
    review = metadata.get("review", {})
    observation = metadata.get("observation", {})
    if (review.get("name") != consent.LED_MAPPING_SCOPE
            or review.get("phase") != "set"
            or review.get("index") != state.get("index")
            or observation.get("baseline_payload_hex") != baseline.hex()
            or observation.get("restore_required") is not True):
        raise ValueError("Set-phase metadata does not match the active mapping state.")
    return evidence, root, state, baseline


def run_restore(set_evidence, output, *, expected_physical_port, run=False,
                operator_observation, actuators_isolated=False, **declarations):
    if operator_observation not in ("changed", "no_change", "uncertain"):
        raise ValueError("Record the operator LED observation before restoration.")
    scope = consent.classify(actuators_isolated=actuators_isolated, **declarations)
    if run is not True or scope != consent.LED_MAPPING_SCOPE:
        raise ValueError("Literal --run and disconnected-load LED mapping scope are required.")
    evidence, root, state, baseline = _bound_set_state(set_evidence)
    output = new_output_path(output)
    if output.parent != root:
        raise ValueError("Restore evidence must be a new sibling of its set-phase evidence.")
    index = state["index"]
    state.update(status="restore_started", restore_evidence=str(output),
                 operator_led_observation=operator_observation)
    _write_state(root, state)
    try:
        result = _run_live(
            output, phase="restore", index=index, baseline=baseline,
            observe=_restore_observer(
                transcript_for("restore", index, baseline), index, baseline,
                operator_observation),
            declarations=declarations, expected_physical_port=expected_physical_port)
    except BaseException:
        attempted = False
        metadata = output / "metadata.json"
        if metadata.is_file():
            attempted = json.loads(
                metadata.read_text(encoding="utf-8")).get(
                    "observation", {}).get("restore_attempted") is True
        state.update(
            status=("restore_attempt_failed" if attempted else "restore_required"),
            restore_phase_status="failed",
        )
        _write_state(root, state)
        raise
    restore_digest = _verify_manifest(output)
    observation = result["observation"]
    state.update(
        status=("restored" if observation["status"] == RESTORED else "restore_rejected"),
        restore_phase_status=observation["status"],
        restore_manifest_sha256=restore_digest,
        restoration=observation["restoration"],
    )
    _write_state(root, state)
    return result


def acknowledge_power_cycle(set_evidence, *, operator_observation, confirmed=False):
    if confirmed is not True:
        raise ValueError("Literal --confirm-power-cycle-reset is required.")
    if operator_observation not in ("changed", "no_change", "uncertain"):
        raise ValueError("Record the operator LED observation before power-cycle acknowledgment.")
    evidence = _evidence_path(set_evidence)
    root = evidence.parent
    state = _load_state(root)
    if not state or state.get("set_evidence") != str(evidence):
        raise ValueError("Power-cycle confirmation must name the active set-phase evidence.")
    if state["status"] not in (
            "restore_required", "restore_started", "restore_rejected",
            "restore_attempt_failed", "baseline_captured"):
        raise ValueError("No active LED mapping round requires power-cycle confirmation.")
    state.update(status="power_cycle_reset_confirmed", confirmed_at=time.time(),
                 operator_led_observation=operator_observation)
    _write_state(root, state)
    return {
        "status": "power_cycle_reset_confirmed",
        "index": state["index"],
        "hardware_access": False,
        "restoration": "operator_declaration_not_software_verification",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("set", "restore", "acknowledge-power-cycle"),
                        required=True)
    parser.add_argument("--index", type=int)
    parser.add_argument("--set-evidence", type=Path)
    parser.add_argument("--confirm-power-cycle-reset", action="store_true")
    parser.add_argument("--led-observation", choices=("changed", "no_change", "uncertain"))
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
        if args.phase == "acknowledge-power-cycle":
            if (args.run or args.output or args.index is not None or args.actuators_isolated
                    or any(declarations.values())):
                raise ValueError("Power-cycle acknowledgment is offline state bookkeeping only.")
            result = acknowledge_power_cycle(
                args.set_evidence, operator_observation=args.led_observation,
                confirmed=args.confirm_power_cycle_reset)
        else:
            if args.confirm_power_cycle_reset:
                raise ValueError("Power-cycle confirmation is a separate offline phase.")
            if args.actuators_isolated or any(declarations.values()):
                _validate_capture({"actuators_isolated": args.actuators_isolated, **declarations})
            if args.phase == "set":
                if (args.index is None or args.set_evidence is not None
                        or args.led_observation is not None):
                    raise ValueError("Set phase requires --index 0..17 and forbids --set-evidence.")
                result = prepare_set(args.index) if not args.run else run_set(
                    args.output, index=args.index,
                    expected_physical_port=args.expected_physical_port, run=True,
                    actuators_isolated=args.actuators_isolated, **declarations)
            else:
                if (args.index is not None or args.set_evidence is None
                        or args.led_observation is None):
                    raise ValueError("Restore phase requires --set-evidence and forbids --index.")
                evidence, _, state, baseline = _bound_set_state(args.set_evidence)
                result = prepare_restore(state["index"], baseline) if not args.run else run_restore(
                    evidence, args.output, expected_physical_port=args.expected_physical_port,
                    operator_observation=args.led_observation,
                    run=True, actuators_isolated=args.actuators_isolated, **declarations)
    except (Exception, KeyboardInterrupt) as error:
        if args.phase != "acknowledge-power-cycle":
            consent.notify_powered_trial_fault(error)
        print(json.dumps({"status": "failed", "error": str(error),
                          "physical_stop": "not_established"}), file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
