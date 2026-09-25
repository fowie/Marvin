"""Public read-only Marvin sensor snapshots over one persistent legacy session."""

from hashlib import sha256

from tools.marvin_legacy_client import LegacyClient, Limits
from tools import marvin_legacy_protocol as protocol
from tools.marvin_legacy_telemetry import interpret_packet


QUERIES = (
    "get-unit-info",
    "get-power-state",
    "read-raw-data",
    "get-servo-position",
)
FIRST_SEQUENCE = 0
PROXIMITY_MAP = {
    "proximity1": ("P5", "left-side rear", "decisive"),
    "proximity2": ("P6", "left-side middle", "strong_provisional_cross_coupling"),
    "proximity3": ("P7", "left-side front", "strong_cross_coupling"),
    "proximity4": ("P9", "front-center", "decisive"),
    "proximity5": ("P11", "right-side front", "decisive"),
    "proximity6": ("P12", "right-side next", "strong_cross_coupling"),
    "proximity7": ("P13", "right-side third/right-facing", "decisive"),
    "proximity8": ("P4", "center-rear/rear-facing", "decisive"),
}
MOTOR_FIELDS = (
    "motorPositionL", "motorPositionR",
    "motorVelocityL", "motorVelocityR",
    "motorAccelerationL", "motorAccelerationR",
    "motorCurrentL", "motorCurrentR",
    "motorPwmLeftForward", "motorPwmLeftReverse",
    "motorPwmRightForward", "motorPwmRightReverse",
)


def plan():
    """Return the immutable request plan and output schema without transport access."""
    requests = []
    for index, query in enumerate(QUERIES):
        spec = protocol.GETTERS[query]
        sequence = FIRST_SEQUENCE + index
        requests.append({
            "query": query,
            "sequence": sequence,
            "command": spec.command,
            "request_hex": spec.encode(sequence).hex(),
            "expected_response_field": protocol.GETTER_RESPONSE_FIELD,
            "expected_payload_bytes": spec.payload_bytes,
        })
    return {
        "schema_version": 1,
        "status": "offline_plan",
        "transport_accessed": False,
        "profile": "marvin-legacy-se",
        "requests": requests,
        "session": {
            "persistent": True,
            "maximum_requests": len(QUERIES),
            "automatic_retries": False,
            "automatic_reconnect": False,
            "identity_pinned": True,
        },
        "snapshot_schema": {
            "connection": "session readiness and pinned transport identity digest",
            "controller": "reported unit words; not attested firmware identity",
            "power": "unlabeled raw GetPowerState uint16 mask",
            "raw_telemetry": "all 82 source-labelled fields with raw/signed/unsigned views",
            "proximity": "eight raw words plus documented physical mapping/confidence",
            "cliff": "five raw words; physical mapping unresolved",
            "bump": "unsupported; no proven legacy field",
            "motors": "raw encoder, velocity, acceleration, current and PWM words",
            "servos": "two aggregate raw position words",
            "battery": "unsupported; installed GetBatteryInfo payload has no proven decoder",
        },
        "unknowns": [
            "Physical units, calibration, health and application acknowledgment are not established.",
            "Cliff physical assignments and all bump semantics remain unresolved.",
            "GetBatteryInfo is excluded because its installed 8-byte payload has no proven decoder.",
        ],
    }


def read_snapshot(transport, *, ownership_key, expected_identity):
    """Read one bounded snapshot using an injected validated transport boundary."""
    if transport is None:
        raise ValueError("Live sensor reads require a transport.")
    if type(ownership_key) is not bytes or type(expected_identity) is not bytes:
        raise ValueError("ownership_key and expected_identity must be immutable bytes.")
    client = LegacyClient(
        transport,
        ownership_key=ownership_key,
        expected_identity=expected_identity,
        session_timeout=5,
        cleanup_timeout=1,
        limits=Limits(max_requests=len(QUERIES), max_rx_bytes=4096,
                      max_events=512, max_reads=256, read_size=256),
        first_sequence=FIRST_SEQUENCE,
        evidence_kind="recorded",
    )
    decoded = {}
    evidence_by_query = {}
    with client:
        for query in QUERIES:
            evidence = client.request(
                query, timeout=1,
                allow_telemetry_state_change=query == "get-unit-info",
            )
            decoded[query] = interpret_packet(
                evidence.stream.packet, direction="received",
                evidence=evidence.evidence_kind,
            )
            evidence_by_query[query] = evidence
    if any(item["status"] != "decoded" for item in decoded.values()):
        raise ValueError("A matched getter did not produce a proven decoded profile.")
    raw = decoded["read-raw-data"]["fields"]
    unit = decoded["get-unit-info"]["fields"]
    servo = decoded["get-servo-position"]["fields"]
    return {
        "schema_version": 1,
        "status": "ready",
        "connection": {
            "ready": True,
            "profile": "marvin-legacy-se",
            "persistent_session": True,
            "requests_completed": len(client.requests),
            "identity_pinned": True,
            "expected_identity_sha256": sha256(expected_identity).hexdigest(),
            "application_acknowledgment": "not_established",
        },
        "controller": {
            "readiness": "reported_not_attested",
            "fields": unit,
            "warning": "Reported words are not authenticated firmware or unique hardware identity.",
        },
        "power": {
            "raw_mask": decoded["get-power-state"]["fields"]["powerState"],
            "bit_meanings": "unknown",
        },
        "raw_telemetry": decoded["read-raw-data"],
        "proximity": {
            name: {
                "value": raw[name],
                "physical_sensor": sensor,
                "documented_location": location,
                "mapping_confidence": confidence,
            }
            for name, (sensor, location, confidence) in PROXIMITY_MAP.items()
        },
        "cliff": {
            f"cliff{index}": {
                "value": raw[f"cliff{index}"],
                "physical_mapping": "unresolved",
            }
            for index in range(1, 6)
        },
        "bump": {
            "status": "unsupported",
            "reason": "No distinct installed legacy getter or decoded bump field is proven.",
        },
        "motors": {name: raw[name] for name in MOTOR_FIELDS},
        "servos": {
            "word0": {
                "value": servo["word0"],
                "documented_mapping": "camera",
                "mapping_confidence": "directly_observed_installed_camera",
            },
            "word1": {
                "value": servo["word1"],
                "documented_mapping": "projector_hypothesis",
                "mapping_confidence": "historical_expected_not_directly_exercised",
            },
            "units": "unknown_raw_uint16",
        },
        "battery": {
            "status": "unsupported",
            "reason": "Installed GetBatteryInfo has a proven exchange but no proven legacy decoder.",
            "raw_telemetry_labels_present": ["batteryVoltage", "batteryCurrent"],
        },
        "request_evidence": [
            {
                "query": request.query,
                "sequence": request.sequence,
                "request_hex": request.raw.hex(),
                "status": request.status,
            }
            for request in client.requests
        ],
        "response_evidence": {
            query: {
                "packet": decoded[query]["packet"],
                "labels": list(evidence_by_query[query].labels),
                "confidence": evidence_by_query[query].confidence,
            }
            for query in QUERIES
        },
    }
