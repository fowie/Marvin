# Offline commissioning evidence

`tools.marvin_commissioning` implements issue #10's authored, versioned evidence
contract. It records completeness and missing prerequisites for stage 2 of the
[bring-up plan](marvin-bringup-plan.md). It is independent of polling, live
transport, and future actuation-policy schemas.

**Even a complete report is not a physical safety certification, physical
sign-off, arming permit, or permission to proceed.** Human physical sign-off,
an explicitly authorized operator/session and a separately approved physical
test plan remain mandatory. This tool does not satisfy Epic #2 or manual #9,
perform #3's physical stop verification, or establish that evidence is true.

## Offline CLI

Run from the repository root using Python 3.10 or later:

```sh
python3 -B -m tools.marvin_commissioning template
python3 -B -m tools.marvin_commissioning schema
python3 -B -m tools.marvin_commissioning validate \
  data/commissioning/synthetic-evidence.json \
  --configuration data/commissioning/synthetic-configuration.json \
  --as-of 2026-01-01T12:00:00Z
```

All commands emit JSON to stdout. `template` publishes a fresh blank/incomplete
package, including every checklist item and **no default validity intervals**.
`schema` publishes the authored v1 JSON Schema (draft 2020-12). Neither command
reads inputs or writes files. Save stdout to a new operator-controlled local
file when preparing a record; do not overwrite source evidence.

`validate` reads only the two named local regular files, at most 262144 bytes
each. It uses the existing `marvin_stream.read_regular_file` contract: reject
symlink leaves/ancestors, special files, device/kernel paths, oversized files,
changed file identity and changes during the bounded read. These are snapshot
checks, not locks against hostile concurrent ancestor replacement. Use finished
files in trusted, stable directories. No stdin, URL input, directory scanning,
reference resolution, transport import, USB/serial access or input mutation is
implemented. `marvin_paths.new_output_path` is intentionally not used: there is
no output-file writer. JSON duplicate keys are rejected at every level using
`marvin_json.unique_object`; there is no permissive fallback.

The supplied configuration is a **separate, reviewed comparison target**.
Do not blindly extract it from the package being evaluated: comparison with
itself cannot detect deployment mismatch. The CLI does not discover installed
hardware, authenticate the target, or locate related files automatically.

The published example is wholly **synthetic**, with invented people, ports,
map entries, observations, references and policy intervals. Its complete result
at the explicit example time demonstrates the report format, not a robot result.
At later times it becomes stale. It is not an actual operator record, procedure,
recommended interval, reference measurement or calibration.

| Validation exit | Status | Meaning |
|---|---|---|
| 0 | `complete` | Complete attributed contents, matching declared configuration, current under supplied policy |
| 1 | `incomplete` | Structurally valid, but missing, unknown, unreviewed, failed, conflicting, blocked or mismatched evidence |
| 2 | `malformed` | Invalid JSON/schema/types/bounds/timestamps, unsupported version, or unreadable/unsafe input |
| 3 | `stale` | Otherwise complete contents, but evidence or the reviewed policy has expired |

If evidence is both incomplete and stale, `status` is `incomplete` and
`freshness` is `stale`; every detected reason remains in `issues`. A missing
required JSON field is malformed. Use the template's explicit `null`, `[]`
and `unknown` representations for valid-but-incomplete data. An omitted
checklist entry in an otherwise valid `entries` array is incomplete, not pass.
Argparse usage errors use exit 2 and stderr rather than a validation report.

## Public Python API and report contract

```python
from tools.marvin_commissioning import load_document, validate_evidence

report = validate_evidence(
    load_document("data/commissioning/synthetic-evidence.json"),
    expected_configuration=load_document("data/commissioning/synthetic-configuration.json"),
    as_of="2026-01-01T12:00:00Z",
)
assert report["physical_safety_established"] is False
assert report["authorization_granted"] is False
```

`validate_evidence(package, *, expected_configuration, as_of=None)` is a pure
evaluation apart from reading UTC wall time when `as_of` is omitted. It does
not mutate either argument. Malformed arguments return a `malformed` report;
`load_document(path)` instead raises `OSError`, `ValueError`, `TypeError`,
`OverflowError` or `RecursionError` on invalid input. The CLI catches those
input failures and emits a malformed report. `blank_template()` and
`evidence_schema()` return independent structures and require no I/O.

Reports contain schema version, `status`, `structurally_valid`,
`content_complete` (all requirements other than freshness),
`evidence_complete` (contents **and** freshness), `freshness`,
`evaluated_at`, `evaluation_time_source`, `checks`, `issues`, and `limitations`.
Every issue has `code`, a JSON-style `path`, and `message`; each required check
has its own `id`, `status` and `freshness`. Global failures can block the package
even if an individual check is complete. Well-formed input and comparison
configuration are copied into `package` and `expected_configuration` so original
statements, timestamps, confidence, observations and blockers remain visible.
Malformed reports do not echo unvalidated input. Treat stdout reports as private
when using private records.

`offline_only` is always true. `physical_safety_established`,
`physical_signoff_established` and `authorization_granted` are **always false**,
including for complete synthetic or operator reports. No status is named
`safe`, `pass`, `armed` or `authorized`; downstream code must not translate
completeness into those claims.

With `--as-of` / `as_of`, the normalized UTC evaluation time is labeled
`supplied_as_of`, not a trusted clock reading. Otherwise it is labeled
`system_utc`. Invalid clocks produce malformed input. Supply an explicit time
for reproducible historical evaluations, not to misrepresent current freshness.
When file loading fails before evaluation, `evaluated_at` is null.

## Version 1 schema

The machine-readable schema is authored by `evidence_schema()` and emitted by
the `schema` CLI. The validator additionally enforces semantic relationships
that JSON Schema alone does not express. All object fields are required,
unknown fields are rejected, and v1 never migrates another version implicitly.

| Object | Fields and constraints |
|---|---|
| Package | Integer `schema_version: 1` (not bool/float); `kind: template/synthetic/operator_record`; nullable `package_id`; `configuration`; nullable `validity_policy`; `entries`; `blockers` |
| Configuration | `identity` and `wiring_map`; required also as the separate CLI/API comparison document |
| Identity | Nullable text `configuration_id`, `revision`, `device_id`, `physical_port`, `protocol_profile`, `wiring_revision`; all need values for completeness |
| Wiring row | Nonblank text `channel`, `connector`, `function`; channel names unique; no inference from USB names or serial defaults |
| Entry | Checklist `id`; full `configuration` identity; `state`, `outcome`, `confidence`; nullable `operator`, `reviewer`, `observed_at`, `reviewed_at`, `method`, `observation`; `evidence_references`, check-specific `assertions`, `blockers` |
| Policy | Nullable `policy_id`, `reviewer`, `reviewed_at`, `expires_at`, `evidence_reference`, `rationale`; full `configuration` identity; `max_age_seconds` object with every checklist ID |

Text is nonblank and at most 4096 characters, or explicit null where allowed.
Lists have at most 64 items, except `entries`, bounded to the 14 known checks.
Text lists reject exact duplicates. Entries reject duplicate/unknown IDs, even
if duplicates agree. Wiring maps reject duplicate channels, even if other
columns differ. Shared physical connectors are permitted; describe individual
pins/channel roles rather than claiming every connector is a separate device.
The exact identity strings and wiring-map array must match the comparison
configuration; there is no profile aliasing, inferred mapping or normalization.
Every entry and policy must carry the package's exact identity, including both
configuration and wiring revisions. A wiring or configuration change requires
new scoped evidence and review, not editing identifiers on old observations.

Timestamps must be aware RFC 3339 with `T`, seconds, a known `Z`/numeric offset,
and at most six fractional digits. Naive times, unknown `-00:00` offsets,
invalid dates and unrepresentable UTC dates are malformed. Observation/review
times after evaluation, reviews before observation, and reviews before the
policy review are incomplete conflicts, never fresh successes.

`state` separates `unknown`, `declared`, `observed`, `reviewed`, and `blocked`.
`outcome` separates `unknown`, `satisfied`, `failed`, and `conflicting`.
`confidence` is `unknown`, `operator_reported`, `measured`, or `corroborated`;
these are source labels, not computed probabilities or ranked safety scores.
Completeness requires `reviewed`, `satisfied`, a non-unknown confidence, all
attribution/method/time/reference fields, and no blockers. Review attribution
does not authenticate a person or require operator and reviewer to differ.
All check-specific assertions must be explicit booleans or null; only true can
satisfy them. False, null, an unresolved blocker or contradictory state/outcome
blocks completeness regardless of any other claim. Free text is preserved,
not interpreted by an LLM: reviewers must encode contradictions as
`conflicting`, false assertions or blockers rather than burying them in prose.

## Required checklist and human evidence

Each item requires its own attributed operator and reviewer, method/time,
observation, evidence reference(s), confidence, scoped configuration and
unresolved blockers. References are opaque IDs, not verified documents.

| Checklist ID | Required evidence |
|---|---|
| `device_profile_identity` | Individual board identification beyond shared USB/default serials, physical-port tracing, reviewed legacy S/E versus successor profile distinction |
| `wiring_channel_map` | Reviewed connector/pin/channel/function map for every applicable channel, supply and physical LED, with explicit unconnected/unknown dispositions |
| `motor_power_isolation` | Operator-reported motor power isolation |
| `motor_signal_isolation` | Separate operator-reported motor signal isolation |
| `servo_power_isolation` | Operator-reported servo power isolation |
| `servo_signal_isolation` | Separate operator-reported servo signal isolation |
| `pend_txcvr_disposition` | Damaged PEND TXCVR socket repaired or positively isolated, with disposition evidence |
| `hub_port4_over_current` | Downstream hub port-4 over-current resolved before branch use; unresolved indications remain blockers |
| `wiring_power_removal` | Robot power, relevant batteries and USB back-power removed, explicitly before wiring changes |
| `supply_protection` | Fuse details, current-limit settings/units and reviewed grounds; no software-invented electrical limits |
| `mechanical_support` | Fixture/support preventing unexpected propulsion and reviewed mechanical clearances |
| `independent_stop` | Attributed independent actuator-energy removal, independent of Linux, USB and firmware |
| `proximity_observations` | Per-channel controlled-stimulus observations tied to map, raw readings and units/confidence where actually known |
| `cliff_observations` | Per-channel controlled-stimulus observations, preserving interlocks without bypass/latch clearing |

The template exposes exact assertion keys for each item. Measurements and raw
bytes belong verbatim in `observation` or referenced evidence, with their
method, units and limitations; do not invent conversions. A boolean assertion
only represents the named person's report. No automatic electrical threshold,
health conclusion or physical verification follows from it.

This checklist is **not a physical test procedure**. Do not perform wiring,
stop tests, stimuli or actuator reconnection merely to fill it. Missing evidence
must remain unknown/blocked until a separately approved operator plan supplies
it. Keep motor/servo power **and signals** isolated outside that plan. Never
infer isolation, safety, calibration or exact firmware from descriptors,
reported configuration/power masks, CRC validity, a successful write, zero
PWM/velocity or getter responses. Do not replace legacy S/E commands with newer
Drive/Head maps: getter/movement/reset IDs collide.

## Reviewed freshness policy

There is no default interval and no policy inferred from timestamps.
Completeness requires an attributed policy reviewer, review time, explicit
expiry, rationale, evidence reference, matching configuration and a finite
positive `max_age_seconds` for **each** check. Numeric booleans, NaN, Infinity,
overflowed JSON exponents, zero and negative intervals are malformed.
The upper bound `2147483647` seconds is solely a representation/resource bound,
**not** a suggested safety lifetime. Fractional positive seconds are supported.

An observation is current when its age at evaluation is at most that check's
reviewed interval. Equality is current; any greater age is stale. Entry review
does not renew the observation clock. Policy validity is half-open:
`reviewed_at <= evaluation < expires_at`. Equality at policy expiry is stale.
Policy expiry must be later than its review. Entry reviews must occur under
that policy (at or after its review); a new policy requires renewed review.
Missing or conflicting policy provenance makes freshness `not_evaluated`,
never an assumed pass. Expired policies and aged evidence are separately
reported. An explicit blocker invalidates completeness even inside an interval.

## Provenance and publication

The contract and synthetic fixtures are independently authored from public
issue #10 and the published bring-up plan's physical prerequisites. No private
operator record, vendor/recovered implementation, firmware, capture, private
history or license assignment is included. Preserve the repository's existing
publication exclusions. Keep actual operator records and referenced material
outside Git; publication requires separate review.
