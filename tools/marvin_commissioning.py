"""Offline commissioning evidence validation; never a physical safety authority."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re

from tools.marvin_json import unique_object
from tools.marvin_stream import read_regular_file


SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 256 * 1024
MAX_TEXT = 4096
MAX_ITEMS = 64
MAX_AGE_SECONDS = 2**31 - 1  # Representation bound, not a recommended safety lifetime.
IDENTITY_FIELDS = (
    "configuration_id", "revision", "device_id", "physical_port",
    "protocol_profile", "wiring_revision",
)
CHECKLIST = {
    "device_profile_identity": (
        "Identify the individual hardware, traced physical port and reviewed protocol profile.",
        ("unique_device_identified", "physical_port_traced", "profile_distinguished"),
    ),
    "wiring_channel_map": (
        "Review every applicable connector/channel against the versioned wiring map.",
        ("all_connectors_and_channels_mapped",),
    ),
    "motor_power_isolation": ("Attribute motor power isolation.", ("isolated",)),
    "motor_signal_isolation": ("Attribute motor signal isolation.", ("isolated",)),
    "servo_power_isolation": ("Attribute servo power isolation.", ("isolated",)),
    "servo_signal_isolation": ("Attribute servo signal isolation.", ("isolated",)),
    "pend_txcvr_disposition": (
        "Record the damaged PEND TXCVR socket disposition, not just USB communication.",
        ("repaired_or_positively_isolated",),
    ),
    "hub_port4_over_current": (
        "Resolve the hub downstream port-4 over-current before using that branch.",
        ("resolved_before_branch_use",),
    ),
    "wiring_power_removal": (
        "Record removal of every energy/back-power source before wiring changes.",
        ("robot_power_removed", "relevant_batteries_removed", "usb_back_power_removed",
         "removal_before_wiring_changes"),
    ),
    "supply_protection": (
        "Document reviewed fuses, current limits and grounds with evidence and units.",
        ("fuses_reviewed", "current_limits_reviewed", "grounds_reviewed"),
    ),
    "mechanical_support": (
        "Document the fixture/support preventing propulsion and reviewed clearances.",
        ("supported_against_unexpected_motion", "clearances_reviewed"),
    ),
    "independent_stop": (
        "Attribute independent actuator-energy removal, not a software stop command.",
        ("independent_of_linux_usb_firmware", "removes_actuator_energy"),
    ),
    "proximity_observations": (
        "Record each mapped proximity channel's controlled-stimulus observations.",
        ("all_mapped_channels_observed", "controlled_stimuli_documented"),
    ),
    "cliff_observations": (
        "Record each mapped cliff channel's observations without bypassing interlocks.",
        ("all_mapped_channels_observed", "controlled_stimuli_documented",
         "interlocks_not_bypassed"),
    ),
}
STATES = ("unknown", "declared", "observed", "reviewed", "blocked")
OUTCOMES = ("unknown", "satisfied", "failed", "conflicting")
CONFIDENCES = ("unknown", "operator_reported", "measured", "corroborated")
KINDS = ("template", "synthetic", "operator_record")
ENTRY_FIELDS = (
    "id", "configuration", "state", "outcome", "confidence", "operator",
    "reviewer", "observed_at", "reviewed_at", "method", "observation",
    "evidence_references", "assertions", "blockers",
)
POLICY_FIELDS = (
    "policy_id", "configuration", "reviewer", "reviewed_at", "expires_at",
    "evidence_reference", "rationale", "max_age_seconds",
)
LIMITATIONS = (
    "Evidence completeness is not physical safety certification, physical sign-off, "
    "an arming permit, or test authorization.",
    "Physical sign-off and a separately approved operator test plan remain mandatory; "
    "this report does not close physical readiness gates.",
    "Attribution, isolation, stop behavior, measurements and references are supplied "
    "statements, not authenticated or independently established by this software.",
    "USB descriptors, default serial numbers, configuration, CRC-valid traffic and "
    "zero PWM/velocity do not establish hardware identity or physical safety.",
    "Legacy S/E and successor Drive/Head command maps cannot be substituted.",
    "Evidence references are opaque labels: no file, URL, device or transport is "
    "opened to resolve them. Raw observations and confidence labels are preserved.",
    "Freshness is evaluated only under the supplied attributed validity policy; "
    "no universal safety lifetime is assumed.",
)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z"
)


class EvidenceError(ValueError):
    """Malformed schema input, distinct from incomplete attributed evidence."""


def _error(path, message):
    raise EvidenceError(f"{path}: {message}")


def _object(value, fields, path):
    if type(value) is not dict or set(value) != set(fields):
        _error(path, "expected exactly these fields: " + ", ".join(fields))


def _text(value, path, *, nullable=True):
    if nullable and value is None:
        return
    if type(value) is not str or not value.strip() or len(value) > MAX_TEXT:
        _error(path, f"expected nonblank text of at most {MAX_TEXT} characters"
               + (" or null" if nullable else ""))


def _timestamp(value, path, *, nullable=True):
    if nullable and value is None:
        return None
    if type(value) is not str or not _TIMESTAMP.fullmatch(value) or value.endswith("-00:00"):
        _error(path, "expected an aware RFC 3339 timestamp (known offset, at most microseconds)")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        _error(path, f"invalid timestamp: {error}")


def _enum(value, values, path):
    if type(value) is not str or value not in values:
        _error(path, "expected one of: " + ", ".join(values))


def _list(value, path, *, maximum=MAX_ITEMS):
    if type(value) is not list or len(value) > maximum:
        _error(path, f"expected a list with at most {maximum} items")


def _texts(value, path):
    _list(value, path)
    for item in value:
        _text(item, path, nullable=False)
    if len(set(value)) != len(value):
        _error(path, "duplicate values")


def _identity(value, path):
    _object(value, IDENTITY_FIELDS, path)
    for key, item in value.items():
        _text(item, f"{path}.{key}")


def _configuration(value, path):
    _object(value, ("identity", "wiring_map"), path)
    _identity(value["identity"], path + ".identity")
    _list(value["wiring_map"], path + ".wiring_map")
    channels = set()
    for row in value["wiring_map"]:
        _object(row, ("channel", "connector", "function"), path + ".wiring_map[]")
        for key, item in row.items():
            _text(item, path + ".wiring_map[]." + key, nullable=False)
        if row["channel"] in channels:
            _error(path + ".wiring_map", "duplicate channel")
        channels.add(row["channel"])


def _bounded_json(value):
    # Also bounds caller-supplied API trees and rejects cycles, nonfinite numbers
    # and invalid Unicode. Never coerce arbitrary Python objects into JSON.
    raw = json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_INPUT_BYTES:
        _error("$", f"input exceeds {MAX_INPUT_BYTES} bytes")


def _validate_shape(package, expected_configuration):
    _bounded_json(package)
    _bounded_json(expected_configuration)
    _object(package, ("schema_version", "kind", "package_id", "configuration",
                      "validity_policy", "entries", "blockers"), "$")
    if type(package["schema_version"]) is not int or package["schema_version"] != SCHEMA_VERSION:
        _error("$.schema_version", "unsupported schema version")
    _enum(package["kind"], KINDS, "$.kind")
    _text(package["package_id"], "$.package_id")
    _configuration(package["configuration"], "$.configuration")
    _configuration(expected_configuration, "expected_configuration")
    _texts(package["blockers"], "$.blockers")
    policy = package["validity_policy"]
    if policy is not None:
        _object(policy, POLICY_FIELDS, "$.validity_policy")
        _identity(policy["configuration"], "$.validity_policy.configuration")
        for key in ("policy_id", "reviewer", "evidence_reference", "rationale"):
            _text(policy[key], "$.validity_policy." + key)
        for key in ("reviewed_at", "expires_at"):
            _timestamp(policy[key], "$.validity_policy." + key)
        _object(policy["max_age_seconds"], CHECKLIST, "$.validity_policy.max_age_seconds")
        for key, age in policy["max_age_seconds"].items():
            if age is not None and (
                type(age) not in (int, float) or not math.isfinite(age)
                or not 0 < age <= MAX_AGE_SECONDS
            ):
                _error("$.validity_policy.max_age_seconds." + key,
                       f"expected finite seconds greater than 0 and at most {MAX_AGE_SECONDS}, or null")
    _list(package["entries"], "$.entries", maximum=len(CHECKLIST))
    seen = set()
    for index, entry in enumerate(package["entries"]):
        path = f"$.entries[{index}]"
        _object(entry, ENTRY_FIELDS, path)
        _enum(entry["id"], CHECKLIST, path + ".id")
        if entry["id"] in seen:
            _error(path + ".id", "duplicate checklist ID")
        seen.add(entry["id"])
        _identity(entry["configuration"], path + ".configuration")
        for key, values in (("state", STATES), ("outcome", OUTCOMES), ("confidence", CONFIDENCES)):
            _enum(entry[key], values, path + "." + key)
        for key in ("operator", "reviewer", "method", "observation"):
            _text(entry[key], path + "." + key)
        for key in ("observed_at", "reviewed_at"):
            _timestamp(entry[key], path + "." + key)
        for key in ("evidence_references", "blockers"):
            _texts(entry[key], path + "." + key)
        _object(entry["assertions"], CHECKLIST[entry["id"]][1], path + ".assertions")
        for key, value in entry["assertions"].items():
            if value is not None and type(value) is not bool:
                _error(path + ".assertions." + key, "expected a boolean or null")


def _report(as_of, source):
    return {
        "schema_version": SCHEMA_VERSION, "status": "malformed",
        "structurally_valid": False, "content_complete": False,
        "evidence_complete": False, "freshness": "not_evaluated",
        "evaluated_at": as_of, "evaluation_time_source": source,
        "offline_only": True, "physical_safety_established": False,
        "authorization_granted": False, "physical_signoff_established": False,
        "limitations": list(LIMITATIONS), "issues": [], "checks": [],
    }


def validate_evidence(package, *, expected_configuration, as_of=None):
    """Return a JSON report, without I/O or mutation.

    expected_configuration is a separately supplied reviewed comparison target,
    not detected hardware. as_of is an aware RFC 3339 string; omission uses UTC
    wall time, explicitly labeled. Malformed input returns status='malformed'.
    No result (including complete synthetic evidence) grants authorization.
    """
    supplied_clock = as_of is not None
    result = _report(None, "supplied_as_of" if supplied_clock else "system_utc")
    try:
        now = _timestamp(as_of if supplied_clock else datetime.now(timezone.utc).isoformat(),
                         "as_of", nullable=False)
        result["evaluated_at"] = now.isoformat()
        _validate_shape(package, expected_configuration)
    except (ValueError, TypeError, OverflowError, RecursionError) as error:
        result["issues"].append({"code": "malformed_input", "path": "$", "message": str(error)})
        return result
    result["structurally_valid"] = True
    result["package"] = deepcopy(package)
    result["expected_configuration"] = deepcopy(expected_configuration)
    issues = result["issues"]

    def issue(code, path, message):
        issues.append({"code": code, "path": path, "message": message})

    def required(value, path):
        if value is None or value == []:
            issue("missing_evidence", path, "An explicit attributed value is required.")

    identity = package["configuration"]["identity"]
    for name, config in (("$.configuration", package["configuration"]),
                         ("expected_configuration", expected_configuration)):
        for key, value in config["identity"].items():
            required(value, name + ".identity." + key)
        required(config["wiring_map"], name + ".wiring_map")
    if package["configuration"] != expected_configuration:
        issue("configuration_mismatch", "$.configuration",
              "Package configuration differs from the separately supplied comparison target.")
    required(package["package_id"], "$.package_id")
    if package["kind"] == "template":
        issue("template_only", "$.kind", "A template is not an attributed report.")
    for blocker in package["blockers"]:
        issue("unresolved_blocker", "$.blockers", blocker)

    def bound_identity(value, path):
        if value != identity:
            issue("configuration_mismatch", path, "Identity must exactly match the package configuration.")

    policy = package["validity_policy"]
    policy_ready = False
    policy_reviewed = None
    policy_expired = False
    if policy is None:
        required(policy, "$.validity_policy")
    else:
        start = len(issues)
        for key in POLICY_FIELDS:
            if key not in ("configuration", "max_age_seconds"):
                required(policy[key], "$.validity_policy." + key)
        bound_identity(policy["configuration"], "$.validity_policy.configuration")
        policy_reviewed = _timestamp(policy["reviewed_at"], "$.validity_policy.reviewed_at")
        expires = _timestamp(policy["expires_at"], "$.validity_policy.expires_at")
        if policy_reviewed is not None and policy_reviewed > now:
            issue("future_timestamp", "$.validity_policy.reviewed_at", "Policy review is after evaluation.")
        if policy_reviewed is not None and expires is not None and expires <= policy_reviewed:
            issue("conflicting_timestamps", "$.validity_policy.expires_at",
                  "Policy expiry must be later than its review.")
        for key, age in policy["max_age_seconds"].items():
            required(age, "$.validity_policy.max_age_seconds." + key)
        policy_ready = len(issues) == start
        if policy_ready and now >= expires:
            policy_expired = True
            issue("stale_policy", "$.validity_policy.expires_at", "The reviewed validity policy has expired.")

    entries = {entry["id"]: (index, entry) for index, entry in enumerate(package["entries"])}
    for key in CHECKLIST:
        start = len(issues)
        if key not in entries:
            issue("missing_entry", "$.entries", f"Required checklist entry missing: {key}")
            result["checks"].append({"id": key, "status": "missing", "freshness": "not_evaluated"})
            continue
        index, entry = entries[key]
        path = f"$.entries[{index}]"
        bound_identity(entry["configuration"], path + ".configuration")
        for name in ("operator", "reviewer", "observed_at", "reviewed_at",
                     "method", "observation", "evidence_references"):
            required(entry[name], path + "." + name)
        if entry["state"] != "reviewed":
            issue("unreviewed_entry", path + ".state", "Only attributed reviewed evidence can be complete.")
        if entry["outcome"] != "satisfied":
            issue("unsatisfied_entry", path + ".outcome", "Unknown, failed or conflicting evidence blocks completeness.")
        if entry["confidence"] == "unknown":
            issue("unknown_confidence", path + ".confidence", "Confidence must remain explicitly attributed.")
        for name, value in entry["assertions"].items():
            if value is not True:
                issue("unconfirmed_assertion", path + ".assertions." + name,
                      "This operator/reviewer assertion is unknown or false.")
        for blocker in entry["blockers"]:
            issue("unresolved_blocker", path + ".blockers", blocker)
        observed = _timestamp(entry["observed_at"], path + ".observed_at")
        reviewed = _timestamp(entry["reviewed_at"], path + ".reviewed_at")
        for name, value in (("observed_at", observed), ("reviewed_at", reviewed)):
            if value is not None and value > now:
                issue("future_timestamp", path + "." + name, "Timestamp is after evaluation.")
        if observed is not None and reviewed is not None and reviewed < observed:
            issue("conflicting_timestamps", path + ".reviewed_at", "Review precedes observation.")
        if policy_reviewed is not None and reviewed is not None and reviewed < policy_reviewed:
            issue("review_predates_policy", path + ".reviewed_at", "Entry needs review under the supplied policy.")
        freshness = "not_evaluated"
        if policy_ready and observed is not None and observed <= now:
            age = (now - observed).total_seconds()
            freshness = "stale" if policy_expired or age > policy["max_age_seconds"][key] else "current"
            if freshness == "stale":
                issue("stale_evidence", path + ".observed_at", "Evidence exceeds the reviewed validity policy.")
        codes = {item["code"] for item in issues[start:]}
        state = "incomplete" if codes - {"stale_evidence"} else ("stale" if codes else "complete")
        result["checks"].append({"id": key, "status": state, "freshness": freshness})

    stale = any(item["code"] in ("stale_evidence", "stale_policy") for item in issues)
    incomplete = any(item["code"] not in ("stale_evidence", "stale_policy") for item in issues)
    result["content_complete"] = not incomplete
    result["evidence_complete"] = not issues
    result["status"] = "incomplete" if incomplete else ("stale" if stale else "complete")
    result["freshness"] = (
        "stale" if stale else
        ("current" if policy_ready and all(row["freshness"] == "current" for row in result["checks"])
         else "not_evaluated")
    )
    return result


def blank_template():
    """Return independent, deliberately incomplete version-1 evidence."""
    identity = dict.fromkeys(IDENTITY_FIELDS)
    return {
        "schema_version": SCHEMA_VERSION, "kind": "template", "package_id": None,
        "configuration": {"identity": deepcopy(identity), "wiring_map": []},
        "validity_policy": {
            "policy_id": None, "configuration": deepcopy(identity), "reviewer": None,
            "reviewed_at": None, "expires_at": None, "evidence_reference": None,
            "rationale": None, "max_age_seconds": dict.fromkeys(CHECKLIST),
        },
        "entries": [{
            "id": key, "configuration": deepcopy(identity), "state": "unknown",
            "outcome": "unknown", "confidence": "unknown", "operator": None,
            "reviewer": None, "observed_at": None, "reviewed_at": None,
            "method": None, "observation": None, "evidence_references": [],
            "assertions": dict.fromkeys(assertions), "blockers": [],
        } for key, (_, assertions) in CHECKLIST.items()],
        "blockers": [],
    }


def evidence_schema():
    """Return the authored v1 JSON Schema; semantic checks also require the API."""
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    text = {"type": ["string", "null"], "minLength": 1, "maxLength": MAX_TEXT, "pattern": r"\S"}
    nonnull_text = {**text, "type": "string"}
    timestamp = {"anyOf": [
        {"type": "null"},
        {"type": "string", "format": "date-time",
         "pattern": "^" + _TIMESTAMP.pattern.replace(r"\Z", "$"),
         "not": {"pattern": "-00:00$"}},
    ]}
    texts = {"type": "array", "items": nonnull_text, "maxItems": MAX_ITEMS, "uniqueItems": True}
    identity = obj({key: text for key in IDENTITY_FIELDS})
    configuration = obj({
        "identity": identity,
        "wiring_map": {"type": "array", "maxItems": MAX_ITEMS, "uniqueItems": True,
                       "items": obj({key: nonnull_text for key in ("channel", "connector", "function")})},
    })
    entries = []
    for key, (description, assertions) in CHECKLIST.items():
        entry = obj({
            "id": {"const": key}, "configuration": identity,
            "state": {"enum": list(STATES)}, "outcome": {"enum": list(OUTCOMES)},
            "confidence": {"enum": list(CONFIDENCES)},
            "operator": text, "reviewer": text, "observed_at": timestamp,
            "reviewed_at": timestamp, "method": text, "observation": text,
            "evidence_references": texts,
            "assertions": obj({name: {"type": ["boolean", "null"]} for name in assertions}),
            "blockers": texts,
        })
        entry["description"] = description
        entries.append(entry)
    policy = obj({
        "policy_id": text, "configuration": identity, "reviewer": text,
        "reviewed_at": timestamp, "expires_at": timestamp,
        "evidence_reference": text, "rationale": text,
        "max_age_seconds": obj({key: {"type": ["number", "null"], "exclusiveMinimum": 0,
                                     "maximum": MAX_AGE_SECONDS} for key in CHECKLIST}),
    })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Marvin commissioning evidence v1 (not physical authorization)",
        "$comment": "The offline API additionally enforces byte bounds, unique IDs/channels, "
                    "aware timestamps, configuration consistency and completeness/freshness.",
        **obj({
            "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
            "kind": {"enum": list(KINDS)}, "package_id": text, "configuration": configuration,
            "validity_policy": {"anyOf": [{"type": "null"}, policy]},
            "entries": {"type": "array", "items": {"oneOf": entries},
                        "maxItems": len(CHECKLIST), "uniqueItems": True},
            "blockers": texts,
        }),
    }


def _reject_constant(value):
    raise EvidenceError(f"Nonfinite JSON constant: {value}")


def load_document(path):
    """Read one bounded immutable regular-file snapshot; references stay opaque."""
    data = read_regular_file(path, max_bytes=MAX_INPUT_BYTES)
    # Decode UTF-8 explicitly rather than silently accepting UTF-16/32 or a BOM.
    document = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=_reject_constant)
    _bounded_json(document)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("template", help="Print blank/incomplete evidence JSON to stdout")
    commands.add_parser("schema", help="Print authored version-1 JSON Schema to stdout")
    validate = commands.add_parser("validate", help="Validate local evidence without hardware access")
    validate.add_argument("evidence")
    validate.add_argument("--configuration", required=True, help="Separate reviewed configuration JSON")
    validate.add_argument("--as-of", help="Explicit aware RFC 3339 evaluation time (otherwise system UTC)")
    args = parser.parse_args(argv)
    if args.command in ("template", "schema"):
        result = blank_template() if args.command == "template" else evidence_schema()
        exit_code = 0
    else:
        try:
            package = load_document(args.evidence)
            configuration = load_document(args.configuration)
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
            result = _report(None, "supplied_as_of" if args.as_of is not None else "system_utc")
            result["issues"].append({"code": "malformed_input", "path": "$", "message": str(error)})
        else:
            result = validate_evidence(package, expected_configuration=configuration, as_of=args.as_of)
        exit_code = {"complete": 0, "incomplete": 1, "malformed": 2, "stale": 3}[result["status"]]
    print(json.dumps(result, indent=2, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
