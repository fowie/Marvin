"""Generate a finite source-audited communication campaign, without any I/O.

make_plan("quick"|"full") returns independent JSON-serializable plan data. It does
not import a transport, read source/capture files, inspect devices, or authorize
execution. The optional CLI prints the plan to stdout. Source citations and
hashes below describe the offline audit, not live compatibility.

This historical successor sweep is experimental and is not recommended for the
known-working legacy S/E device. Archive citations are not runtime file inputs.

Runner contract: one persistent connection per segment, 1 s prelisten, indicated
interchunk gaps and per-step response windows, then 2 s final tail. Flow control
is none. Pause on any RX for analysis, and abort on short/uncertain writes,
identity changes or transport errors. Never turn scheduled repeats into retries.
No later phase is a clean-state experiment: reopening/delays are not parser reset.
"""

import argparse
from copy import deepcopy
import json
import struct

from tools.marvin_protocol import crc16


_REFERENCE = "private-archive/successor-robot/decompiled-reference/"
_ARTIFACTS = "local-evidence/"
_EVIDENCE = {
    "command-enum": {
        "path": _REFERENCE + "mars-contracts/Microsoft.Robotics.Firmware.Protocol/CommandID.cs",
        "sha256": "1dee57075a29dda30a3cde4d2ea0a5606ffa316deea5472cf49f9469676afa72",
        "citation": "Complete managed CommandID enumeration; names alone do not establish safe semantics.",
    },
    "host-serial": {
        "path": _REFERENCE + "common-firmware/Microsoft.Robotics.Common.Firmware/SerialPortManager.cs",
        "sha256": "ba30d0db6e5d66fd5f316ac258de06bf74a3caeb07cf3493c355a13f32a4c430",
        "citation": "DriveDeviceIDPattern and DefaultInitializePort: 045e:4444, 115200/8N1, DTR/RTS true.",
    },
    "host-packetizer": {
        "path": _REFERENCE + "common-firmware/Microsoft.Robotics.Common.Firmware/Packetizer.cs",
        "sha256": "ce1b548c128c0912d71cdc84d7409dd2e6f76ffd40de4c654f1c50020304576b",
        "citation": "CreatePacket: EFBE, sequence LE16, opcode, response=0, length LE16, CRC, ADDE; uint16 wire sequence wraps.",
    },
    "host-flash-query": {
        "path": _REFERENCE + "common-firmware/Microsoft.Robotics.Common.Firmware/Flash.cs",
        "sha256": "d32241211dbbbb0aa8b3143e958e078dc82253351330e71e69645435dcd53ee4",
        "citation": "GetUnitInfo(board) sends empty GetConfig4. No constructor, startup or flashing method is executed.",
    },
    "host-manufacturing": {
        "path": _REFERENCE + "manufacturing-firmware/Microsoft.Robotics.Manufacturing.Firmware/FirmwareStack.cs",
        "sha256": "536d972d1ceb3fc96aaa60bd9c0942e8683d8215d9a265c1135b50c6c9b45626",
        "citation": "GetDriveHeartbeat/GetHeadHeartbeat (1541-1572): empty ReadRawData3; calibration getter (2213-2223): opcode46 with flashCal0/1. Raw bootloader fallback excluded.",
    },
    "host-startup": {
        "path": _REFERENCE + "controller-service/Microsoft.Robotics.Services.RobotIoControllerService/RobotIoControllerService.cs",
        "sha256": "5a0415b09ef0f0dbca552f6c08df4090519c078035fe51ac45211a1e80c69a04",
        "citation": "DoInitializationSequence (3258-3370): empty27 then29, then drive38. Full startup/heartbeats/actuation are excluded.",
    },
    "calibration-arg": {
        "path": _REFERENCE + "mars-contracts/Microsoft.Robotics.Firmware.Protocol/GetCalibrationParamsArg.cs",
        "sha256": "e1ef9793e3d8c805f04e345b7264d35e8feecaee80f8fc725d00ba8370603e5f",
        "citation": "Pack=1, one byte flashCal. Only selectors00/01; no address argument.",
    },
    "firmware-tables": {
        "path": _ARTIFACTS + "command-tables.json",
        "sha256": "590e41e3a95a092c1b4df537959bacaf045290163142ee821952242c381fc3b1",
        "citation": "IO45949 table0x475c:3/4/27/29/38 inputs0;46 input1. Head38 unsupported, not a head-query campaign.",
    },
    "io-firmware": {
        "path": _ARTIFACTS + "IOboard_FW45949.bin",
        "sha256": "28065f57d91b6ede41899be38ecc61cc2513e54369c35c4ef03598b9c9a562af",
        "citation": "Getter entrypoints:3=0x884a,4=0x7d08,27=0x87d6,29=0x8804,38=0xf9dc,46=0x7f16; copy helper0x1123a.",
    },
    "head-firmware": {
        "path": _ARTIFACTS + "HeadController_FW45949.bin",
        "sha256": "ac285e0284c3b638e3895c838b9258037c330b0ae96a99b9c2427200b6c443cc",
        "citation": "Corroboration only:3=0x77d4 copies36B;46=0x7324 copies12B. No head interface is opened.",
    },
    "firmware-startup": {
        "path": _ARTIFACTS + "startup-dispatch-findings.md",
        "sha256": "04c32f5292ee07858e3af778b0023e802fccae73271183c93e14ad8d18c6cade",
        "citation": "IO45949 parser0x1734-1886: fixed8B header alignment, retained partial state; reconnect is not reset.",
    },
    "getter-audit": {
        "path": _ARTIFACTS + "campaign-query-audit.json",
        "sha256": "37eb4a6cb1206b1c6ea737f55ae1fee602818cd5e0b79044564749e5c6ec495a",
        "citation": "2026-09-11 static exact-entrypoint disassembly/hash checks; no recovered code executed. ROM critical-section bodies are outside images.",
    },
    "historical-text": {
        "path": _ARTIFACTS + "marvin-command-candidates.json",
        "sha256": "589e01853bfcd815c43fbcc03bac91736175a4a9e10de9cea91c897f2f349023",
        "citation": "108 never-executed heuristic cases; reuse vocabulary/terminators/baud/pacing, not claims of Marvin support.",
    },
}

# Exact request shapes only; no address, count, mode, or arbitrary payload API.
_QUERIES = (
    ("unit-info", 27, b"", "stateful-query",
     "host-startup DoInitializationSequence; io-firmware0x87d6 copies12B and sets identity-seen; may enable telemetry with29."),
    ("sensor-info", 29, b"", "stateful-query",
     "host-startup after27; io-firmware0x8804 returns synthetic00..7f (128B), sets sensor-seen and may enable telemetry; not live sensing."),
    ("config", 4, b"", "source-query",
     "host-flash-query GetUnitInfo actually sends4; io-firmware0x7d08 copies108B metadata with12B UnitInfo prefix; no flash/actuator setter."),
    ("projector-version", 38, b"", "source-query",
     "host-startup empty38; io-firmware0xf9dc-f9e8 reads cached byte at RAM20000ba3 into response; no projector action; head38 unsupported."),
    ("raw-telemetry", 3, b"", "source-query",
     "host-manufacturing GetDriveHeartbeat/GetHeadHeartbeat; io-firmware0x884a-8872 copies157B cached telemetry (head36B); no arbitrary address."),
    ("calibration-ram", 46, b"\x00", "source-query",
     "host-manufacturing calibration getter plus calibration-arg flashCal00; io-firmware0x7f16-7f50 copies56B from selected RAM calibration to response (head12B); no setter."),
    ("calibration-persisted", 46, b"\x01", "source-query",
     "host-manufacturing persisted getter plus calibration-arg flashCal01; io-firmware0x7f16-7f50 copies56B from fixed persisted-calibration source to response; no commit/erase/program."),
)

_EXCLUDED_COMMANDS = (
    (0, "Unused0", "sentinel", "Managed unused slot; native ReSync name is not permission to send a reset/resynchronization command."),
    (1, "DeviceHeartbeat", "device-output", "Device-origin telemetry, not an audited host getter."),
    (2, "HostHeartbeat", "watchdog-state", "Services watchdog/control state; not an information query."),
    (7, "SetProjectorIdleMode", "power-or-actuation", "Changes projector operating state."),
    (8, "InitReflash", "firmware-write", "Enters firmware programming flow."),
    (9, "ReflashBlock", "firmware-write", "Writes firmware blocks."),
    (10, "FanPower", "power-or-actuation", "Changes fan output."),
    (11, "MotorParameter", "control-write", "Changes motor parameters."),
    (12, "ErrorReport", "device-output", "Device error reporting path, not an audited host getter."),
    (13, "RecalibrateServo", "actuation-or-calibration", "Homes/reindexes servo; physical movement."),
    (15, "SetPowerState", "power-or-actuation", "Switches hardware power."),
    (16, "ResetCliffStoppage", "safety-state", "Clears a safety stop."),
    (17, "MicrophoneGain", "configuration-write", "Changes microphone gain."),
    (18, "SetBumpSensing", "safety-state", "Changes bump sensing."),
    (19, "ResetBumpDetected", "safety-state", "Clears bump state."),
    (20, "ResetIoBoard", "reset", "Resets controller."),
    (21, "ResetPC", "reset", "Resets host PC."),
    (22, "ToggleHeartbeat", "watchdog-state", "Explicit telemetry state change; audited27/29 handshake only."),
    (23, "SetDriveVelocities", "actuation", "Commands wheel motion."),
    (24, "SetTimedDriveVelocities", "actuation", "Commands timed wheel motion."),
    (25, "SetServoRadians", "actuation", "Commands servo position."),
    (26, "SetServoHoldingCurrent", "actuation", "Enables/changes actuator current."),
    (28, "ResetMotorPos", "control-write", "Resets odometry/motor position."),
    (30, "SonarMode", "sensing-actuation", "Actively switches/triggers sonar."),
    (31, "DepthCamPower", "power-or-actuation", "Switches sensor power."),
    (32, "SetFaceRingLeds", "output-write", "Changes LED output."),
    (33, "SetSensoryLeds", "output-write", "Changes LED output."),
    (34, "SetProjectorFocus", "actuation", "Moves projector focus."),
    (35, "SetProjectorBrightness", "output-write", "Changes projector brightness."),
    (36, "SyncProjectorSignal", "configuration-write", "Changes projector signal state."),
    (37, "SetProjectorInversion", "configuration-write", "Changes projector inversion."),
    (39, "SetProjectorPower", "power-or-actuation", "Switches projector power."),
    (40, "MoveProjectorVerticalImage", "actuation", "Moves projector image mechanism."),
    (42, "GoToProjectorHomePosition", "actuation", "Homes projector."),
    (43, "OpenProjectorShutter", "actuation", "Opens shutter."),
    (44, "CloseProjectorShutter", "actuation", "Closes shutter."),
    (45, "SetCalibrationParams", "calibration-write", "Writes calibration values."),
    (47, "CommitCalibrationParams", "persistent-write", "Commits calibration to flash."),
    (48, "ZeroAllCalibrationParams", "calibration-write", "Erases/zeros calibration values."),
    (49, "SetProximityCalibrationOffsets", "calibration-write", "Writes proximity offsets."),
    (50, "SetCliffSensorCalibrationOffsets", "calibration-write", "Writes cliff offsets."),
    (51, "SetAccelerometerCalibrationOffsets", "calibration-write", "Writes accelerometer offsets."),
    (52, "SetGyroscopeCalibrationOffsets", "calibration-write", "Writes gyro offsets."),
    (53, "SetServoCalibrationOffset", "calibration-write", "Writes servo offset."),
    (54, "SetProjectorHomeCalibrationPosition", "calibration-write", "Writes projector calibration."),
    (55, "SetCompassCalibrationOffsets", "calibration-write", "Writes compass offsets."),
    (56, "SetBumpSensingThreshold", "safety-state", "Changes bump threshold."),
    (59, "SetServoSequence", "actuation", "Queues servo motion."),
    (60, "ConfigPid", "control-write", "Writes feedback-controller tuning."),
    (61, "SetTimedFaceRingLeds", "output-write", "Queues LED output."),
    (62, "SetTimedSensoryLeds", "output-write", "Queues LED output."),
    (63, "CommandCount", "sentinel", "Enum count; outside native valid opcode range."),
)
_QUERY_NAMES = {
    3: "ReadRawData", 4: "GetConfig", 27: "GetUnitInfo",
    29: "GetSensorInfo", 38: "GetProjectorVersion", 46: "GetCalibrationParams",
}
_TEXT_BODIES = ("?", "help", "h", "VER", "HWVER", "ADC", "READ", "HELP",
                "ver", "version", "VERSION", "info", "INFO", "status", "STATUS")
_TEXT_SEEDS = ("?", "help", "VER", "HWVER", "version", "status")
_TERMINATORS = (("cr", b"\r"), ("lf", b"\n"), ("crlf", b"\r\n"))
_BAUDRATES = (115200, 9600, 38400, 57600, 19200, 230400, 4800)
_GRID_FORMATS = tuple((bits, parity, stops) for bits in (8, 7)
                      for parity in ("N", "E", "O") for stops in (1, 2))
_GRID_LINES = ((True, True), (True, False), (False, True), (False, False))
_GRID_TEXT_CR = ("?", "help", "VER", "version", "status")
_GRID_RESPONSE_SECONDS = 0.75
_FULL_MAX_SEGMENTS = 750
_FULL_MAX_SECONDS = 14400
_USB_TAIL_SECONDS = 5.0
_BASE = (115200, 8, "N", 1, True, True)
_PACING = (("byte-1ms", "byte", 0.001), ("byte-10ms", "byte", 0.01),
           ("byte-50ms", "byte", 0.05), ("byte-100ms", "byte", 0.1),
           ("marker-10ms", "marker", 0.01), ("header-100ms", "header", 0.1),
           ("trailer-10ms", "trailer", 0.01))


def _request(command, sequence, payload=b""):
    if type(sequence) is not int or not 0 <= sequence <= 65535:
        raise ValueError("Sequence must fit uint16.")
    if type(command) is not int or (command, payload) not in {(q[1], q[2]) for q in _QUERIES}:
        raise ValueError("Only audited getter opcodes and exact payloads are allowed.")
    header = b"\xef\xbe" + struct.pack("<HBBH", sequence, command, 0, len(payload))
    body = header + payload
    return body + struct.pack("<H", crc16(body)) + b"\xad\xde"


def _chunks(data, mode):
    if mode == "whole":
        return [data.hex()]
    if mode == "byte":
        return [data[index:index + 1].hex() for index in range(len(data))]
    cut = {"marker": 2, "header": 8, "trailer": len(data) - 4}[mode]
    return [data[:cut].hex(), data[cut:].hex()]


def _step(identifier, data, classification, rationale, *, mode="whole", interval=0.0, response=2.0):
    return {
        "id": identifier, "chunks_hex": _chunks(data, mode), "interval_seconds": float(interval),
        "response_seconds": float(response), "classification": classification, "rationale": rationale,
    }


def _settings_id(settings):
    baud, bits, parity, stops, dtr, rts = settings
    return f"{baud}-{bits}{parity.lower()}{stops}-dtr{int(dtr)}-rts{int(rts)}"


def _segment(identifier, settings, steps):
    baud, bits, parity, stops, dtr, rts = settings
    return {"id": identifier, "baudrate": baud, "bytesize": bits, "parity": parity,
            "stopbits": stops, "dtr": dtr, "rts": rts, "steps": steps}


def _binary_settings():
    settings = [_BASE]
    settings += [(115200, 8, "N", 1, dtr, rts) for dtr, rts in ((True, False), (False, True), (False, False))]
    settings += [(baud, 8, "N", 1, True, True) for baud in _BAUDRATES[1:]]
    settings += [(115200, 8, parity, stops, True, True)
                 for parity, stops in (("N", 2), ("E", 1), ("E", 2), ("O", 1), ("O", 2))]
    return settings


def _cartesian_settings():
    return [(baud, bits, parity, stops, dtr, rts) for baud in _BAUDRATES
            for bits, parity, stops in _GRID_FORMATS for dtr, rts in _GRID_LINES]


def _binary_grid_steps(settings):
    transport = (
        "CDC-line-coding hypothesis: full logical8-bit USB bytes remain unchanged; "
        "not valid7-bit UART binary encoding. Requested7-bit line coding may be ignored, "
        "rejected or applied by the driver/device; never strip marker/payload high bits."
        if settings[1] == 7 else
        "Requested8-bit line coding is a transport hypothesis, not evidence that firmware uses these UART settings."
    )
    return [
        _step(
            f"seed-{name}-seq-{index}", _request(command, index, payload), classification,
            f"{source} Finite Cartesian seed coverage; same open across27 then29, "
            f"not whole startup. {transport}",
            response=_GRID_RESPONSE_SECONDS,
        )
        for index, (name, command, payload, classification, source) in enumerate(_QUERIES)
    ]


def _binary_steps(*, mode="whole", interval=0.0):
    steps = []
    seeds = (0, 1, 65535) if mode == "whole" else (0, 65535)
    for round_number, seed in enumerate(seeds):
        for index, (name, command, payload, classification, source) in enumerate(_QUERIES):
            sequence = (seed + index) & 0xFFFF
            steps.append(_step(
                f"round-{round_number}-{name}-seq-{sequence}", _request(command, sequence, payload),
                classification,
                f"{source} Keep same open across27 then29; fixed raw query only, not whole host method. "
                f"host-packetizer sequence seed{seed} with uint16 wrap; FFFF is a boundary hypothesis, not an ACK. "
                f"Host chunking={mode}; not a promise of USB packet boundaries.",
                mode=mode, interval=interval,
            ))
    for repetition in range(2):
        steps.append(_step(
            f"fixed-config-seq-0-repeat-{repetition}", _request(4, 0), "source-query",
            "host-flash-query empty4; exact repeated sequence0 tests duplicate handling only. "
            "Explicit scheduled repetition after completed writes/noRX, never a retry after uncertainty.",
            mode=mode, interval=interval,
        ))
    return steps


def _text_steps(bodies, terminators, *, mode="whole", interval=0.0, empty=False, response=3.0):
    steps = []
    for terminator_id, terminator in terminators:
        for index, body in enumerate(bodies):
            basis = (
                "TI cserial help query in historical-text" if body in ("?", "help", "h") else
                "Eddie v1.1 information query in historical-text" if body in ("VER", "HWVER", "ADC", "READ") else
                "Heuristic information-looking spelling from historical-text, not a proven Marvin command"
            )
            steps.append(_step(
                f"term-{terminator_id}-word-{index}-{body.lower() if body != '?' else 'question'}",
                body.encode("ascii") + terminator, "text-hypothesis",
                f"{basis}; exact ASCII{body!r}+{terminator_id.upper()}, not arbitrary operands. "
                "Neighbouring dialect only: no Marvin compatibility/harmlessness guarantee; pause on anyRX. "
                f"Host chunking={mode}, not USB timing.",
                mode=mode, interval=interval, response=response,
            ))
        if empty:
            steps.append(_step(
                f"term-{terminator_id}-empty", terminator, "text-hypothesis",
                "historical-text empty terminator control after complete queries; may retain/poison binary alignment; not a parser reset.",
                response=response,
            ))
    return steps


def _coalesced_steps():
    pairs = [
        (f"handshake-seed-{seed}", (27, seed, b""), (29, (seed + 1) & 0xFFFF, b""), "stateful-query")
        for seed in (0, 1, 65535)
    ]
    pairs += [
        ("config-seq-0-1", (4, 0, b""), (4, 1, b""), "source-query"),
        ("config-seq-65535-0", (4, 65535, b""), (4, 0, b""), "source-query"),
        ("config-fixed-repeat", (4, 0, b""), (4, 0, b""), "source-query"),
        ("cached-version-telemetry", (38, 0, b""), (3, 1, b""), "source-query"),
        ("calibration-selectors", (46, 0, b"\x00"), (46, 1, b"\x01"), "source-query"),
    ]
    return [
        _step(
            name, _request(*first) + _request(*second), classification,
            "host-packetizer independent complete frames concatenated in one24/26B host write; "
            "host-startup27->29 or getter-audit exact queries. Coalescing is a delivery hypothesis, "
            "not original response-gated startup; no3-frame burst, no mid-write RX decision possible.",
            response=3.0,
        )
        for name, first, second, classification in pairs
    ]


def _malformed_segments(profile):
    valid = _request(4, 0)
    crc_bad = bytearray(valid)
    crc_bad[-4] ^= 1
    footer_bad = bytearray(valid)
    footer_bad[-1] ^= 1
    header_bad = bytearray(valid)
    header_bad[:2] = b"\xbe\xef"
    header_bad[-4:-2] = struct.pack("<H", crc16(header_bad[:-4]))
    wrong_length_body = valid[:6] + b"\x01\x00\x00"
    length_bad = wrong_length_body + struct.pack("<H", crc16(wrong_length_body)) + b"\xad\xde"
    cases = (
        ("crc-bit", bytes(crc_bad), "One checksum bit changed; otherwise exact empty opcode4 request."),
        ("footer-bit", bytes(footer_bad), "One footer bit changed; exact header/payload/CRC unchanged."),
        ("header-order", bytes(header_bad), "Marker byte order swapped with matching CRC; no invented command."),
        ("extra-payload", length_bad, "Opcode4 length1 and one00 payload, internally CRC-valid but violates audited zero-input ABI."),
    )
    if profile == "quick":
        cases = cases[:1]
    return [
        _segment(f"malformed-{name}", _BASE, [_step(
            name, data, "malformed-hypothesis",
            f"firmware-startup parser validation hypothesis, deliberately last. {reason} "
            "Can poison retained parser state; later cases are not independent and no reset/recovery bytes are implied.",
            response=3.0,
        )])
        for name, data, reason in cases
    ]


def _catalogue():
    entries = [
        {"command": command, "name": name, "included": False, "classification": classification,
         "reason": reason, "evidence": ["command-enum"]}
        for command, name, classification, reason in _EXCLUDED_COMMANDS
    ]
    for command, name in _QUERY_NAMES.items():
        queries = [query for query in _QUERIES if query[1] == command]
        entries.append({
            "command": command, "name": name, "included": True,
            "classification": queries[0][3], "reason": " ".join(q[4] for q in queries),
            "request_payloads_hex": [q[2].hex() for q in queries],
            "evidence": ["command-enum", "firmware-tables", "io-firmware", "getter-audit"],
        })
    return sorted(entries, key=lambda entry: entry["command"])


def _summarize_and_check(segments, *, full_grid=False):
    seen = set()
    summaries = []
    for segment in segments:
        if segment["id"] in seen:
            raise ValueError("Duplicate campaign segment ID.")
        seen.add(segment["id"])
        step_ids = set()
        writes = tx_bytes = 0
        seconds = 3.0
        for step in segment["steps"]:
            if step["id"] in step_ids:
                raise ValueError("Duplicate step ID within segment.")
            step_ids.add(step["id"])
            chunks = [bytes.fromhex(chunk) for chunk in step["chunks_hex"]]
            if not chunks or any(not 1 <= len(chunk) <= 32 for chunk in chunks):
                raise ValueError("Chunks must contain 1..32 bytes.")
            size = sum(map(len, chunks))
            if size > 256 or not 0 <= step["interval_seconds"] <= 0.1 or not 0.2 <= step["response_seconds"] <= 3.0:
                raise ValueError("Step exceeds byte/timing limits.")
            writes += len(chunks)
            tx_bytes += size
            seconds += (len(chunks) - 1) * step["interval_seconds"] + step["response_seconds"]
        if seconds > 85 or writes > 256 or tx_bytes > 4096:
            raise ValueError("Segment exceeds duration/write/byte bounds.")
        summaries.append({"id": segment["id"], "steps": len(segment["steps"]), "writes": writes,
                          "tx_bytes": tx_bytes, "scheduled_seconds": round(seconds, 6)})
    total_writes = sum(row["writes"] for row in summaries)
    total_bytes = sum(row["tx_bytes"] for row in summaries)
    max_segments = _FULL_MAX_SEGMENTS if full_grid else 400
    if len(segments) > max_segments or total_writes > 100000 or total_bytes > 1024 * 1024:
        raise ValueError("Campaign exceeds global bounds.")
    summary = {
        "segments": len(segments), "steps": sum(row["steps"] for row in summaries),
        "writes": total_writes, "tx_bytes": total_bytes,
        "scheduled_seconds": round(sum(row["scheduled_seconds"] for row in summaries), 6),
        "per_segment": summaries,
    }
    if full_grid:
        estimated = round(summary["scheduled_seconds"] + len(segments) * _USB_TAIL_SECONDS, 6)
        if estimated > _FULL_MAX_SECONDS:
            raise ValueError("Campaign exceeds estimated duration bound including USB tails.")
        summary["usb_tail_seconds_per_segment"] = _USB_TAIL_SECONDS
        summary["estimated_seconds_with_usb_tail"] = estimated
    return summary


def make_plan(profile="full"):
    """Return a deterministic independent data-only plan; no runtime source reads."""
    if profile not in ("quick", "full"):
        raise ValueError("profile must be 'quick' or 'full'.")
    binary_settings = _binary_settings() if profile == "full" else [_BASE]
    segments = [_segment("binary-" + _settings_id(settings), settings, _binary_steps())
                for settings in binary_settings]
    pacing = _PACING if profile == "full" else (_PACING[1],)
    segments += [_segment("binary-pacing-" + name, _BASE, _binary_steps(mode=mode, interval=interval))
                 for name, mode, interval in pacing]
    segments.append(_segment("binary-coalesced-pairs", _BASE, _coalesced_steps()))
    if profile == "full":
        grid_settings = _cartesian_settings()
        segments += [_segment("binary-grid-" + _settings_id(settings), settings,
                              _binary_grid_steps(settings)) for settings in grid_settings]
        for terminator in _TERMINATORS:
            segments.append(_segment(
                "text-vocabulary-" + terminator[0], _BASE,
                _text_steps(_TEXT_BODIES, (terminator,), empty=True),
            ))
        text_settings = binary_settings[1:] + [
            (115200, 7, parity, stops, True, True) for parity in ("N", "E", "O") for stops in (1, 2)
        ]
        segments += [_segment("text-settings-" + _settings_id(settings), settings,
                              _text_steps(_TEXT_SEEDS, _TERMINATORS)) for settings in text_settings]
        segments += [_segment(f"text-pacing-byte-{milliseconds}ms", _BASE,
                              _text_steps(_TEXT_SEEDS, _TERMINATORS, mode="byte", interval=milliseconds / 1000))
                     for milliseconds in (10, 50)]
        segments += [_segment(
            "text-grid-" + _settings_id(settings), settings,
            _text_steps(_GRID_TEXT_CR, _TERMINATORS[:1], response=_GRID_RESPONSE_SECONDS)
            + _text_steps(("?",), _TERMINATORS[1:], response=_GRID_RESPONSE_SECONDS),
        ) for settings in grid_settings]
    else:
        segments.append(_segment("text-seeds-baseline", _BASE, _text_steps(_TEXT_SEEDS, _TERMINATORS)))
    segments += _malformed_segments(profile)
    summary = _summarize_and_check(segments, full_grid=profile == "full")
    catalogue = _catalogue()
    plan = {
        "schema_version": 1, "profile": profile, "plan_kind": "offline-source-audited-hypothesis-catalogue",
        "exclusions": [
            "Every excluded CommandID and reason is enumerated in command_catalogue; no unknown numeric sweep.",
            "No firmware/reflash/erase/unlock, bootloader entry/raw bootloader fallback, resets, debug-probe or flash operations.",
            "No persistent/calibration/control writes, power switching, actuator/LED/sonar commands or watchdog servicing.",
            "No arbitrary memory addresses, guessed raw-read parameters, random padding or random byte/opcode streams.",
            "No PING, IN, OUT, HIGH, LOW, GO, GOSPD, TRVL, TURN, ACC, STOP, RST, VERB, SGP, SPNG, BLINK, BLNK, led or echo text verbs.",
            ("No claim of valid7-bit UART binary encoding:7-bit binary grid trials retain logical8-bit "
             "USB frames solely as CDC-line-coding hypotheses; no high-bit stripping or rewritten packets."
             if profile == "full" else
             "No binary frames under7-bit encoding (marker high bits are not representable); 7-bit cases use ASCII only."),
            "No flow control, serial BREAK, baud0 hangup, DTR pulse reset, driver detach or USB control/vendor request sweep.",
            "No replay of full service/manufacturing startup or automatic response-triggered continuation.",
        ],
        "limitations": [
            "Historical experimental successor sweep; not recommended for the known-working legacy S/E device. Opcode maps collide.",
            "Evidence paths are private archive/local-evidence citations, not bundled inputs. Original-source hash verification requires the private archive and cannot run in CI.",
            ("Exhaustive only within the enumerated7 baud x12 format x4 control-line grid and exact "
             "seven binary/seven ASCII seeds per setting; not all possible commands, payloads, "
             "sequences, pacing combinations or communication options. Installed compatibility remains unproven."
             if profile == "full" else
             "Finite selected grid, not all possible communication options; installed Marvin protocol compatibility remains unproven."),
            "Six binary getter opcodes are source-audited against successor software/firmware, not the installed predecessor image.",
            "GetUnitInfo27 and GetSensorInfo29 change handshake flags and can enable telemetry; not strictly state-free reads.",
            "ReadRawData3 copies a fixed cached snapshot, not arbitrary memory. Calibration46 only copies values; ROM critical-section bodies are outside these firmware images.",
            "Persistent27->29 is an exploratory same-open sequence even without a reply, not a faithful full startup: original service advances after responses and later actuates.",
            "Repeated fixed requests are scheduled probes only after complete writes/noRX; no retries after short/uncertain writes or errors.",
            "SequenceFFFF is a uint16 boundary hypothesis; unsolicited device heartbeats also useFFFF, so match direction/opcode/CRC before interpretation.",
            "CDC settings and inter-write gaps need not change firmware baud/framing or USB packet boundaries; unsupported host settings must be recorded, not silently substituted.",
            "Coalesced pairs contain only2 complete audited frames (24/26B); a response to the first cannot stop the second inside the same write.",
            "Reopen, DTR/RTS changes, and quiet waits do not prove a clean parser state; no intentional reset/ROM-boot entry is included.",
            "ASCII query terms come from neighbouring dialects/heuristics and may have unknown effects on Marvin; labels are not a harmlessness guarantee.",
            "Binary comes first, text next, malformed cases last; parser poisoning in any stage can confound subsequent cases.",
            "One selected-byte CRC/footer/header mutation and one audited-length violation only; no arbitrary malformed fuzzing.",
            "Timing totals include1s prelisten, interchunk gaps, response windows and2s tail, not capture/setup overhead or host scheduling/write latency.",
            "Pause and analyze anyRX before further writes; USB OUT completion, echo, CRC validity or silence is not a matched application acknowledgment.",
            "Bus/device numbers are intentionally not pinned here; parent runner must revalidate current physical identity and isolation before any open.",
        ],
        "execution_contract": {
            "flow_control": "none", "persistent_connection_per_segment": True,
            "prelisten_seconds": 1.0, "final_tail_seconds": 2.0,
            "phase_order": ["binary", "text", "malformed"],
            "pause_on_any_rx": True, "retry_uncertain_write": False,
            "abort_on_transport_or_identity_error": True,
            "require_owner_confirmed_run_and_power_and_signal_isolation": True,
            "no_hardware_authorization_from_plan_generation": True,
        },
        "bounds": {"segment_seconds": 85, "segment_writes": 256, "segment_tx_bytes": 4096,
                   "campaign_segments": 400, "campaign_writes": 100000, "campaign_tx_bytes": 1048576},
        "evidence": deepcopy(_EVIDENCE),
        "command_catalogue": catalogue, "unassigned_opcodes_excluded": [5, 6, 14, 41, 57, 58],
        "coverage": {
            "binary_opcodes": sorted(_QUERY_NAMES), "calibration_selectors_hex": ["00", "01"],
            "baudrates": list(dict.fromkeys(segment["baudrate"] for segment in segments)),
            "serial_formats": sorted({f"{s['bytesize']}{s['parity']}{s['stopbits']}" for s in segments}),
            "control_line_states": [list(state) for state in sorted({(s["dtr"], s["rts"]) for s in segments})],
            "text_vocabulary": list(_TEXT_BODIES if profile == "full" else _TEXT_SEEDS),
            "text_terminators_hex": [value.hex() for _, value in _TERMINATORS],
            "selection_policy": "Factor-at-a-time baud/line/framing coverage; baseline pacing. Not a full Cartesian product.",
        },
        "summary": summary, "segments": segments,
    }
    if profile == "full":
        plan["limitations"] += [
            "Binary7-bit cases are CDC-line-coding hypotheses, not valid7-bit UART binary encoding; "
            "the host sends unchanged logical8-bit USB bytes, and actual driver/device handling is unknown.",
            "Grid seeds allow0.75s response per step; late replies may overlap later steps. "
            "Pause on anyRX, preserve absolute timing and do not assign a reply from timing alone.",
            "Estimated duration adds5s USB capture tail per segment to scheduled time; excludes setup, "
            "write/host latency and other capture overhead. Runner must enforce the14400s wall-clock cap, "
            "record unfinished cases and not shorten windows or continue past the cap.",
        ]
        plan["bounds"]["campaign_segments"] = _FULL_MAX_SEGMENTS
        plan["bounds"]["campaign_seconds"] = _FULL_MAX_SECONDS
        plan["coverage"]["selection_policy"] = (
            "Complete finite Cartesian settings grid for seven exact binary and seven ASCII seeds; "
            "original richer factor-at-a-time and baseline pacing/sequence/text variants retained."
        )
        plan["coverage"]["cartesian_grid"] = {
            "baudrates": list(_BAUDRATES),
            "serial_formats": [f"{bits}{parity}{stops}" for bits, parity, stops in _GRID_FORMATS],
            "control_line_states": [list(state) for state in _GRID_LINES],
            "settings_combinations": len(grid_settings),
            "binary_segments": len(grid_settings), "ascii_segments": len(grid_settings),
            "binary_steps_per_setting": len(_QUERIES), "ascii_steps_per_setting": 7,
            "binary_7bit_cdc_hypothesis_segments": sum(settings[1] == 7 for settings in grid_settings),
            "response_seconds": _GRID_RESPONSE_SECONDS,
            "binary_seeds": [
                {"command": command, "sequence": index, "payload_hex": payload.hex()}
                for index, (_, command, payload, _, _) in enumerate(_QUERIES)
            ],
            "ascii_seeds_hex": [(body.encode("ascii") + b"\r").hex() for body in _GRID_TEXT_CR]
                               + [b"?\n".hex(), b"?\r\n".hex()],
            "completeness_scope": "Planned cases, not executed or successful exchanges; no RX compatibility claim.",
        }
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("quick", "full"), default="full")
    args = parser.parse_args(argv)
    print(json.dumps(make_plan(args.profile), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
