"""Explicit, offline source-layout interpretation of legacy GetConfig replies.

Names/types come from the later packed confparams layout and agree with the
observed 108-byte legacy reply. This is a source-derived view, not proof of
runtime tuning, calibration, physical limits or exact installed firmware.
Newer Drive defaults are comparison data only, never suggested replacements.
The existing general legacy telemetry interpreter still leaves config opaque.
"""

import argparse
import json

from tools import marvin_legacy_protocol as protocol


_FIELDS = (
    ("unitInfo.fwVersion", "uint32", 32, None),
    ("unitInfo.commVersion", "uint32", 32, 0x00010300),
    ("unitInfo.serialNumber", "uint32", 32, 0x01020304),
    ("accel", "uint32", 34, 1),
    ("kp", "int32", 35, 90),
    ("ki", "int32", 36, 0),
    ("kd", "int32", 37, 30),
    ("integralDivisor", "int32", 38, 1),
    ("maxPwmDelta", "int32", 39, 600),
    ("maxVel", "int32", 40, 3500),
    ("minVel", "int32", 41, -3500),
    ("motionTickTimeout", "uint32", 42, 8),
    ("motorStop1Sec", "uint32", 43, 1),
    ("servoCamMin", "int32", 46, 1200),
    ("servoCamDefault", "int32", 47, 1425),
    ("servoCamMax", "int32", 48, 2300),
    ("servoCamTorque", "int32", 49, 336),
    ("servoProjMin", "int32", 50, 200),
    ("servoProjDefault", "int32", 51, 2730),
    ("servoProjMax", "int32", 52, 2730),
    ("servoProjTorque", "int32", 53, 512),
    ("heartbeatPeriod", "uint32", 56, 8),
    ("sysClockFreq", "uint32", 59, 50_000_000),
    ("cliffStopThreshold", "uint32", 62, 80),
    ("cliffStopHysteresis", "uint32", 63, 32),
    ("batChargeFullThreshold", "uint32", 66, 1400),
    ("batChargeFullHysteresis", "uint32", 67, 80),
)


def interpret_packet(packet, *, direction="unknown", evidence="unspecified"):
    if not isinstance(packet, protocol.LegacyPacket):
        raise TypeError("Expected a LegacyPacket.")
    if not isinstance(packet.raw, bytes) or not isinstance(packet.payload, bytes):
        raise TypeError("Packet raw and payload must be immutable bytes.")
    if direction not in protocol.DIRECTIONS or evidence not in protocol.EVIDENCE_KINDS:
        raise ValueError("Invalid direction or evidence declaration.")
    result = {
        "schema_version": 1, "status": "raw", "offline_only": True,
        "direction": direction, "evidence_kind": evidence, "packet": packet.to_dict(),
        "application_acknowledgment": "not_established",
        "limitations": [
            "Direction/evidence are caller declarations, not authenticated physical origin.",
            "Field names and signedness are a source-layout interpretation; all raw bits remain available.",
            "The later source returns a constant default configuration, not a live calibration/tuning snapshot.",
            "Reported version/serial words do not identify a unique board or exact running image.",
            "Defaults from the newer Drive build must not be written to this older controller.",
            "No physical units, actuator limits, watchdog behavior or readiness are established.",
        ],
    }
    try:
        protocol.validate_get_config_reply(packet, packet.sequence)
    except ValueError as error:
        result.update(reason="not_a_known_config_reply", detail=str(error))
        return result
    if direction != "received":
        result["reason"] = "direction_not_received"
        return result
    fields = {}
    for index, (name, kind, line, default) in enumerate(_FIELDS):
        offset = index * 4
        raw = packet.payload[offset:offset + 4]
        unsigned = int.from_bytes(raw, "little")
        signed = int.from_bytes(raw, "little", signed=True)
        value = signed if kind == "int32" else unsigned
        fields[name] = {
            "word": index, "offset": offset, "size": 4, "bits": 32,
            "raw_hex": raw.hex(), "hex": f"0x{unsigned:08x}",
            "unsigned": unsigned, "signed": signed,
            "source_type": kind, "source_value": value,
            "source": f"m_inc/m_config.h:{line}",
            "newer_drive_default": default,
            "differs_from_newer_drive_default": value != default if default is not None else None,
        }
    fields["unitInfo.fwVersion"]["default_note"] = "FirmwareVersion macro is build-dependent."
    fields["heartbeatPeriod"]["source_unit_note"] = (
        "Later m_config.h:71 uses 5000 microseconds per unit (8 would mean 40 ms there); "
        "installed heartbeat timing is not established."
    )
    fields["sysClockFreq"]["source_unit_note"] = (
        "Reported frequency word; newer m_hw.h:27-37 Drive default 50 MHz, Head 80 MHz. "
        "Not a measured oscillator frequency."
    )
    result.update(
        status="decoded", reason="explicit_source_layout_view",
        profile="legacy-config-108-source-layout", fields=fields,
        source={
            "layout": "m_inc/m_config.h:30-68; m_inc/protocol.cs:2890-2919 UnitInfo",
            "layout_sha256": "82a0bacf35d3316ae0f868521b714d3293278661af1a0dce6b7db6d82a8ecfdd",
            "defaults": "m_src/m_config.c:18-77; m_inc/m_hw.h:27-37",
            "defaults_sha256": "5f7d6c82e3d59c4a0e826b910496bd0ddd7f1f03aa9067013fd6daa92c4daea8",
            "handler": "m_src/m_config.c:82-94 returns constant defConfig",
            "scope": "Supplied newer C layout/defaults compared with observed legacy reply; not an installed-image match.",
        },
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hex_packet", help="One complete legacy packet")
    parser.add_argument("--direction", choices=protocol.DIRECTIONS, default="unknown")
    parser.add_argument("--evidence", choices=protocol.EVIDENCE_KINDS, default="unspecified")
    args = parser.parse_args(argv)
    try:
        packet = protocol.decode_packet(bytes.fromhex(args.hex_packet))
        result = interpret_packet(packet, direction=args.direction, evidence=args.evidence)
    except (ValueError, TypeError) as error:
        print(json.dumps({"status": "input_error", "offline_only": True, "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
