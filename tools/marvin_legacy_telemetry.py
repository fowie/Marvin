"""Offline interpretation of four exact original-Marvin reply profiles.

Only received/status80 LegacyPackets with verified raw integrity are interpreted:
ReadRawData00/134B, GetUnitInfo1B/12B, GetPowerState0E/2B and
GetServoPosition1D/4B. Config4 stays opaque.
Nothing opens files/devices or loads successor schemas. Direction and evidence
are caller declarations, not authenticated facts; no application ACK is inferred.

Raw-data offsets/labels derive from old PCTestApp/Form1.cs UpdateDeviceData,
458-552. Its >=133 guard is off by one (index133 is accessed); exactly134 is
required here. parseShort388-391 accumulates unsigned16, while parseInt393-396
uses signed32 C# arithmetic. Both unsigned and signed two's-complement views
are provided for every16/32-bit word, without choosing physical signedness.
Power/temperature/etc labels do NOT establish volts, Celsius, distance, radians,
calibration or health. All field offsets are payload-relative.

The three UnitInfo words are REPORTED values, not measured runtime firmware
identity or a unique hardware serial (01020304 can be a default). Power bits
remain unlabeled; current C/successor bit definitions are deliberately not used.
"""

from tools import marvin_legacy_protocol as protocol


RAW_DATA_PAYLOAD_BYTES = 134
UNIT_INFO_PAYLOAD_BYTES = 12
POWER_STATE_PAYLOAD_BYTES = 2
SERVO_POSITION_PAYLOAD_BYTES = 4
SENSOR_FIELDS = (
    *(f"proximity{i}" for i in range(1, 9)),
    *(f"cliff{i}" for i in range(1, 6)),
)
_SOURCE_PATH = "MarvinFirmwareAndSample/v1/Firmware/PCTestApp/Form1.cs"
_SOURCE_SHA256 = "567edeece799528b0b04304e89c1375ba434fe3b76e031731b0ab3a20f692f61"

# Each tuple is (label, payload offset, byte width, original sample UI label).
_RAW_FIELDS = (
    (("tick", 0, 4, "timestamp_textBox"),)
    + tuple((f"proximity{i + 1}", 4 + 2 * i, 2, f"prox{i + 1}_progressBar") for i in range(8))
    + tuple((f"cliff{i + 1}", 20 + 2 * i, 2, f"cliff{i + 1}_progressBar") for i in range(5))
    + tuple((name, 30 + 2 * i, 2, name + "_textBox") for i, name in enumerate((
        "internalTemp", "externalTemp", "externalHumidity", "batteryVoltage", "batteryCurrent",
        "power5V", "power12V", "power9V", "power19V", "gyroReference",
        "accelX", "accelY", "accelZ", "gyroX", "gyroY", "gyroZ", "compass",
    )))
    + (("motorPositionL", 64, 4, "motorPositionL_textBox"),
       ("motorPositionR", 68, 4, "motorPositionR_textBox"))
    + tuple((name, 72 + 2 * i, 2, name + "_textBox") for i, name in enumerate((
        "motorVelocityL", "motorVelocityR", "motorAccelerationL", "motorAccelerationR",
        "motorCurrentL", "motorCurrentR", "flags", "projectorPosition", "depthCam",
        "motorPwmLeftForward", "motorPwmLeftReverse", "motorPwmRightForward", "motorPwmRightReverse",
    )))
    + tuple((f"led{i}Brightness", 98 + i, 1, f"led{i}Brightness_progressBar") for i in range(18))
    + tuple((f"led{i}Blink", 116 + i, 1, f"led{i}Blink_progressBar") for i in range(18))
)
_UNIT_FIELDS = (("reportedFwVersion", 0, 4), ("reportedCommVersion", 4, 4), ("reportedSerialNumber", 8, 4))
_PROFILES = {
    protocol.READ_RAW_DATA: ("pctestapp-raw-data-134", RAW_DATA_PAYLOAD_BYTES),
    protocol.GET_UNIT_INFO: ("legacy-unit-info-12", UNIT_INFO_PAYLOAD_BYTES),
    protocol.GET_POWER_STATE: ("legacy-power-state-2", POWER_STATE_PAYLOAD_BYTES),
    protocol.GET_SERVO_POSITION: ("legacy-servo-position-4", SERVO_POSITION_PAYLOAD_BYTES),
}


def _word(payload, offset, size):
    raw = payload[offset:offset + size]
    unsigned = int.from_bytes(raw, "little")
    result = {
        "offset": offset, "size": size, "bits": size * 8, "raw_hex": raw.hex(),
        "hex": f"0x{unsigned:0{size * 2}x}", "unsigned": unsigned,
    }
    if size in (2, 4):
        result["signed"] = int.from_bytes(raw, "little", signed=True)
    return result


def interpret_packet(packet, *, direction="unknown", evidence="unspecified"):
    """Return a JSON-safe interpretation or raw-only result with an explicit reason.

LegacyPacket is required, never a successor packet or bare payload. Invalid
integrity or mismatched claimed fields stays raw with a reason. Invalid API
types/declarations raise instead of substituting defaults.
    """
    if not isinstance(packet, protocol.LegacyPacket):
        raise TypeError("Expected a LegacyPacket, not bare payload or a successor packet.")
    if not isinstance(packet.raw, bytes) or not isinstance(packet.payload, bytes):
        raise TypeError("LegacyPacket raw and payload must be immutable bytes.")
    if direction not in protocol.DIRECTIONS or evidence not in protocol.EVIDENCE_KINDS:
        raise ValueError("Invalid direction or evidence declaration.")
    result = {
        "schema_version": 1, "protocol": "marvin-legacy-se", "offline_only": True,
        "status": "raw", "direction": direction, "evidence_kind": evidence,
        "provenance": "Caller-declared direction/evidence; no authenticated origin or automatic sequence correlation.",
        "packet": packet.to_dict(), "application_acknowledgment": "not_established",
        "limitations": [
            "Source-derived labels and exact profile length do not establish calibration, health or physical units.",
            "Unsigned and signed word values are alternative bit interpretations, not a choice of physical signedness.",
            "Reported firmware/communication words are not verified runtime identity; serial01020304 may be a default.",
            "Power-state bits have no assigned hardware labels; current C/successor maps are not reused.",
            "No config108, successor157/36, unknown command, echo or non80 response interpretation is attempted.",
        ],
    }
    try:
        decoded = protocol.decode_packet(packet.raw)
    except ValueError as error:
        result.update(reason="invalid_frame_integrity", detail=str(error))
        return result
    if decoded != packet:
        result["reason"] = "packet_fields_disagree_with_raw"
        return result
    if direction != "received":
        result["reason"] = "direction_not_received"
        return result
    if packet.response_field != 0x80:
        result["reason"] = "response_not_80_or_request_echo"
        return result
    if packet.command == protocol.GET_CONFIG:
        result["reason"] = "config_left_opaque"
        return result
    if packet.command not in _PROFILES:
        result["reason"] = "unknown_command"
        return result
    profile, expected = _PROFILES[packet.command]
    if len(packet.payload) != expected:
        result.update(reason="unknown_payload_size", expected_payload_bytes=expected)
        return result
    fields = {}
    if packet.command == protocol.READ_RAW_DATA:
        for name, offset, size, source_label in _RAW_FIELDS:
            fields[name] = {**_word(packet.payload, offset, size), "source_label": source_label}
        result["source"] = {
            "path": _SOURCE_PATH, "sha256": _SOURCE_SHA256,
            "citation": "UpdateDeviceData458-552; parseShort388-391; parseInt393-396. Source guard>=133 is insufficient for index133.",
            "profile_evidence": "Source field layout plus a correlated134-byte ReadRawData00 reply on2026-09-14; no runtime image identity inferred.",
        }
    elif packet.command == protocol.GET_UNIT_INFO:
        fields = {name: _word(packet.payload, offset, size) for name, offset, size in _UNIT_FIELDS}
        result["source"] = {
            "citation": "Reviewed legacy UnitInfo three-word layout; correlated12-byte1B reply on2026-09-14 matches GetConfig's reported prefix.",
            "meaning": "Reported firmware/communication/serial words only; not attested identities.",
        }
    elif packet.command == protocol.GET_POWER_STATE:
        fields["powerState"] = _word(packet.payload, 0, 2)
        result["source"] = {
            "path": _SOURCE_PATH, "sha256": _SOURCE_SHA256,
            "citation": "getPowerState_btn_Click937-940; correlated2-byte0E reply on2026-09-14.",
            "meaning": "Unlabeled reported 16-bit power-state mask only.",
        }
    else:
        fields = {
            "word0": _word(packet.payload, 0, 2),
            "word1": _word(packet.payload, 2, 2),
        }
        result["source"] = {
            "citation": "PCTestApp GetServoPosition aggregate two-word layout; correlated installed 4-byte reply.",
            "meaning": "Two raw reported position words; not measured or calibrated joint angles.",
        }
    result.update(status="decoded", reason="exact_legacy_profile_match", profile=profile, fields=fields)
    return result
