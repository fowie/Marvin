"""Bounded, offline SYNTHETIC scenario CLI. Never opens a transport.

python -B -m tools.marvin_control_scenario data/synthetic-control-scenario.json

Exit 0: no modeled fault/exit/crash; 1: modeled fault/exit/crash; 2: invalid
input/output. None is physical acceptance. Output defaults to stdout only.
"""

import argparse
from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import marvin_control_model as model
from tools.marvin_json import unique_object
from tools.marvin_paths import new_output_path
from tools.marvin_stream import read_regular_file


MAX_INPUT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024


def _constant(value):
    raise ValueError(f"Nonstandard JSON number: {value}")


def _object(value, required, optional=()):
    if type(value) is not dict:
        raise ValueError("Expected a JSON object.")
    if not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError(f"Expected fields {sorted(required)}; optional {sorted(optional)}.")
    return value


def _dataclass(cls, value):
    names = {field.name for field in fields(cls)}
    return cls(**_object(value, names))


def _scope(value, expected):
    return expected if value == "expected" else _dataclass(model.Scope, value)


def _raw(value):
    if type(value) is not str or len(value) > 2 * model.MAX_RAW_BYTES:
        raise ValueError("raw_hex must be bounded hexadecimal text.")
    if len(value) % 2 or any(char not in "0123456789abcdefABCDEF" for char in value):
        raise ValueError("raw_hex requires exactly two hex digits per byte.")
    return bytes.fromhex(value)


def _name(value):
    if type(value) is not str or not 1 <= len(value) <= model.MAX_TEXT or not value.strip():
        raise ValueError("Reference names must be nonempty bounded strings.")
    return value


def parse_scenario(document):
    """Validate the entire schema before running; resolve only in-memory aliases."""
    _object(document, ("schema_version", "evidence_kind", "scope", "policy", "reviews", "events"))
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Only schema_version 1 is supported.")
    if document["evidence_kind"] != "SYNTHETIC":
        raise ValueError("Only explicitly SYNTHETIC scenarios are accepted.")
    expected = _dataclass(model.Scope, document["scope"])
    policy_fields = {field.name for field in fields(model.Policy)}
    policy = model.Policy(**_object(
        document["policy"], policy_fields - {"max_commands", "max_events"},
        ("max_commands", "max_events"),
    ))
    if type(document["reviews"]) is not list or len(document["reviews"]) > 3:
        raise ValueError("reviews must be an array of at most three synthetic declarations.")
    reviews = {}
    for row in document["reviews"]:
        _object(row, {field.name for field in fields(model.Review)})
        review = model.Review(**{**row, "scope": _scope(row["scope"], expected)})
        model.validate_review(review)
        name = _name(review.kind)
        if name in reviews:
            raise ValueError("Duplicate review kind.")
        reviews[name] = review
    if type(document["events"]) is not list or not 1 <= len(document["events"]) <= policy.max_events:
        raise ValueError("events must be a nonempty array within max_events.")
    prepared, aliases = [], set()
    for row in document["events"]:
        if type(row) is not dict or type(row.get("kind")) is not str:
            raise ValueError("Each event requires a kind.")
        kind = row["kind"]
        # This is a fixed high-level event schema, not arbitrary opcode dispatch.
        if kind not in model.EVENT_FIELDS:
            raise ValueError("Unknown event kind.")
        alias_fields = ("save_token",) if kind == "authorize" else ()
        _object(row, ("at", "kind", *model.EVENT_FIELDS[kind], *alias_fields))
        values = {key: value for key, value in row.items() if key not in ("save_token", "token")}
        token_name, save_name = None, None
        if "scope" in values:
            values["scope"] = _scope(values["scope"], expected)
        if kind == "authorize":
            names = values["reviews"]
            if type(names) is not list or len(names) > 3:
                raise ValueError("Authorization reviews must be at most three review names.")
            if any(_name(name) not in reviews for name in names):
                raise ValueError("Unknown review reference.")
            values["reviews"] = tuple(reviews[name] for name in names)
            save_name = _name(row["save_token"])
            if save_name in aliases:
                raise ValueError("Token aliases cannot be overwritten or reused.")
            aliases.add(save_name)
        if "token" in row:
            token_name = _name(row["token"])
            if token_name not in aliases:
                raise ValueError("Token must refer to an earlier explicit authorization.")
            values["token"] = model.Token(-1, "schema-validation-only", token_name)
        if "intent" in values:
            values["intent"] = _dataclass(model.Intent, values["intent"])
        for name, cls in (("write", model.Write), ("reply", model.Reply)):
            if name in values:
                data = values[name]
                _object(data, ({field.name for field in fields(cls)} - {"raw"}) | {"raw_hex"})
                data = {**data, "raw": _raw(data["raw_hex"])}
                del data["raw_hex"]
                if name == "reply":
                    if type(data["labels"]) is not list:
                        raise ValueError("Reply labels must be an array.")
                    data["labels"] = tuple(data["labels"])
                values[name] = cls(**data)
        if "observation" in values:
            values["observation"] = _dataclass(model.ExternalStop, values["observation"])
        event = model.Event(**values)
        model.validate_event(event)
        prepared.append((event, token_name, save_name))
    return policy, expected, prepared


def run_scenario(document):
    policy, scope, prepared = parse_scenario(document)
    simulation, tokens = model.Model(policy, scope), {}
    for event, token_name, save_name in prepared:
        if token_name is not None:
            # Failed authorizations issue no token. A missing capability remains
            # explicitly invalid and causes an ownership fault, never fallback auth.
            event = replace(event, token=tokens[token_name])
        record = simulation.step(event)
        if save_name is not None:
            tokens[save_name] = (
                simulation.state.token if record.result == "accepted"
                else model.Token(-1, "authorization-denied", save_name)
            )
    faulted = any(record.result not in ("accepted", "recorded")
                  or record.after.mode in ("fault", "crashed", "exited") or record.after.fault
                  for record in simulation.records)
    return {
        "schema_version": 1,
        "evidence_kind": "SYNTHETIC",
        "complete": True,
        "result": "model_fault" if faulted else "model_trace_completed",
        "physical_authorization": "not_granted",
        "physical_stop": "not_established",
        "manual_11": "BLOCKED_requires_separate_operator_plan_and_physical_evidence",
        "resume_permitted": False,
        "limitations": [
            "Every state, limit, byte and review is SYNTHETIC, not a recommendation.",
            "No transport, wire encoder, hardware cleanup or robot command exists here.",
            "A crashed host cannot run cleanup; crash state is an external simulator projection.",
            "Write acceptance and correlated replies are not authentication, ACK or physical stop.",
            "Declared external measurements and reviews cannot self-certify physical proof.",
            "No background watchdog; expiry is evaluated only when a model event is processed.",
        ],
        "state": model.to_json(simulation.state),
        "records": model.to_json(simulation.records),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", help="Finished regular SYNTHETIC JSON file, never a device.")
    parser.add_argument("--output", help="New file in existing real directories; never overwrite.")
    args = parser.parse_args(argv)
    try:
        output = new_output_path(args.output) if args.output is not None else None
        raw = read_regular_file(args.scenario, max_bytes=MAX_INPUT_BYTES)
        document = json.loads(raw, object_pairs_hook=unique_object, parse_constant=_constant)
        report = run_scenario(document)
        report["input"] = {
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "meaning": "Input integrity identifier only, not authentication.",
        }
        encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("Output budget exceeded; no truncated report written.")
        if output is not None:
            with output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        else:
            print(encoded, end="")
        return 1 if report["result"] == "model_fault" else 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({
            "complete": False, "result": "input_or_output_error",
            "error": f"{type(error).__name__}: {str(error)[:1024]}",
            "evidence_kind": "SYNTHETIC", "physical_stop": "not_established",
            "physical_authorization": "not_granted",
        }), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
