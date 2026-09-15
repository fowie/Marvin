# Offline stop and bounded-control model

This independently authored software work addresses #12 and #14. It does **not**
enable actuator integration, satisfy manual #11 or #13, or close physical
Epics #3/#4/#5. All executable examples, limits, bytes, review declarations and
traces are **SYNTHETIC**, not robot measurements or recommendations.

`tools.marvin_control_model` is a deterministic state reducer and single-thread
in-memory facade. It has no transport dependency, wire encoder, opcode dispatch,
device-opening path or executable robot-command output. The scenario CLI reads
finished local JSON files only. Even complete modeled prerequisites leave
`physical_authorization = not_granted` and `physical_stop = not_established`.

The [manual acceptance matrix](stop-failure-acceptance.md) specifies the separate
physical observations required for #11. UI/software stop is supplemental to an
independent physical stop. Software cannot self-certify evidence, grant physical
authorization, clear physical latches or prove that motion/energy has stopped.
For the human decisions, evidence handoffs and explicit hold points needed to
work through those gates interactively, use the
[human bring-up runbook](human-bringup-runbook.md).

## Public Python API

| API | Contract |
|---|---|
| `Policy` | Frozen explicit hypothetical velocity, acceleration, duration, dead-man, reply-timeout and command-age bounds. No defaults for these values. |
| `Scope` | Pinned identity including connection generation, legacy profile, calibration revision and unit declarations. These are assertions, not authenticated identity or calibrated units. |
| `Review` | Frozen, scope- and policy-reference-bound declaration for `calibration`, `physical_stop` or `limits`; names reviewer/reference, measurement/review status and validity interval. Source must be `SYNTHETIC`. |
| `Event`, `Intent`, `Write`, `Reply`, `ExternalStop` | Immutable high-level model inputs. Raw bytes are opaque synthetic evidence, never encoded robot requests. |
| `State`, `Pending`, `Record` | Immutable snapshots of state, outstanding work and event audit. Old snapshots never update retroactively. |
| `transition(state, event)` | Pure reducer returning `(new_state, result)` with explicit event time; no wall clock, transport, background timer or side effect. |
| `validate_event(event)` | Validate a bounded immutable event schema without executing a transition. |
| `validate_review(review)` | Validate every declaration's structural schema, including source and interval; does not grant approval or check current scope/policy/time applicability. Also applied to reviews by `validate_event`. |
| `EVENT_FIELDS` | Read-only exact event payload schema shared by the reducer and CLI. |
| `Model(policy, scope).step(event)` | Owns current state and bounded records; constructing thread object owns every operation. Foreign-thread/reentrant access raises `OwnershipError` before mutation. |
| `Model.state`, `Model.records` | Frozen state and tuple snapshots, without consuming evidence. |
| `Token` | In-memory model-only capability issued by explicit authorization. Pass the actual issued object; reconstructed or cross-model tokens are rejected. Not an OS lock, security sandbox, authentication or physical approval. |
| `to_json(value)` | JSON-safe immutable evidence rendering; exact synthetic bytes become `raw_hex`, length and source. Invalid nonfinite model inputs become explicit diagnostic objects, never nonstandard JSON numbers. |

`ModelError` means an invalid policy, structural schema or unretainable input, not
success. The facade visibly faults on an invalid event (or retains an existing
terminal state and primary fault); that rejected event is
not added to its history. The pure reducer raises without changing its input.
Semantic violations (including bounded nonfinite/bool numeric inputs) retain
the event and return a fault record. Review declarations are stricter: invalid
field types, empty text, unknown kinds, non-SYNTHETIC source or invalid intervals
are schema errors, not modeled approval failures. `Record.result` distinguishes
rejection from acceptance; a parseable trace is not a successful policy transition.

The model assumes cooperative application code: frozen dataclasses, thread
ownership and process-local object capabilities do not protect against callers
fabricating a `State`, bypassing the facade or editing Python internals.

## States and explicit transitions

All modes below are **software model states**, never hardware states.

| Preconditions | Event | Result |
|---|---|---|
| Construction | None | `new`, no owner, no authorization, no timer, no pending work |
| `new` | `connect` with freshly declared matching scope | `disarmed`; no automatic getter, authorization or arm |
| `disarmed`, no owner | `authorize` with owner, approval reference and exactly three valid reviews | Same mode, one new model token; authorization is explicit and model-only |
| Authorized `disarmed` | `arm` with the issued token and matching current scope | `armed`, starts explicit dead-man interval and a synthetic zero baseline |
| `armed`, all prerequisites current, before every deadline | `keepalive` with token/scope | Extends dead-man from this event's time; never extends a pending reply or intent deadline |
| `armed`, no outstanding work | Valid bounded `intent` | One pending `intended` record, consumed sequence, duration/reply deadlines; intention only |
| Pending `intended` | One complete `request_write` declaration | Pending `submitted`; adapter-accepted bytes do not establish delivery |
| Pending `submitted` | Clean `application_reply` declaration strictly after write and before all deadlines | Clears pending only if labels are exactly `matched_candidate` and `request_status` is `matched`; still no ACK or stop proof |
| Valid state, no pending work | `stop` / `disarm` | Revokes owner/token, `disarmed` (or remains `new` if never connected); only a disarm intention |
| Pending work | `stop` / `disarm` | `fault: uncertain_delivery`; preserves failed pending snapshot and original write/reply declarations |
| Session uncertainty, stale traffic, bounds/ownership failure, expiry | Fault trigger | `fault`, revoked token, no retry/rearm/resume; pending work marked `failed` |
| Nonterminal modeled host state | `host_crash` | `crashed`, external simulator projection; `cleanup=cannot_execute_after_host_crash` |
| Valid state before deadlines | `host_exit` | `exited`, revoked authority, uncertainty recorded if pending; no hardware cleanup exists |
| `fault` | `reset` | `new`, invalidates old token and clears working pending state; historical records remain |
| Any state before applicable expiry checks, or terminal state | `restart` | `new`, no owner or resumed intent; old tokens invalid; lifetime budgets/sequences are not reset |
| `new` after reset/restart | Explicit `connect`, then fresh `authorize`, then `arm` | Separate required steps; no reconnect shortcut from fault/crash/exit |
| Any state | `external_stop` declaration | Retains a synthetic observation; fail/block or an exceeded declared limit faults visibly, but pass cannot clear faults, grant authority, or change physical-stop status |

Deadline/clock failures take precedence over an ordinary event, including a stop,
restart or exit arriving at expiry. The event is preserved, but its requested
transition is not executed. Another explicit reset/restart is needed. Host crash
is special: with valid time it is an external projection even at deadline, not
an opportunity for cleanup inside the crashed process. Later external
observations may still be retained in terminal states; they do not revive them.
The first terminal failure stays primary; later records retain their event and
rejected result. A later `host_exit` or `host_crash` cannot replace an existing
`fault`, `crashed` or `exited` mode, change its cleanup evidence or revoke tokens
again; only the validated event time advances. Reset/restart acknowledges the model fault only, not any
physical latch. A new identity/profile/calibration requires a newly constructed,
explicitly reviewed model scope; reconnect does not accept it silently.

Repeated authorization cannot transfer command ownership. A different owner
faults; the same owner cannot silently replace approval. Disarm/revocation and
fresh authorization are necessary. A fabricated token with identical fields is
not the issued token. Restarted/new models cannot use a previous model's token.

## Bounded hypothetical intentions

`Intent` contains a monotonically increasing uint16 `sequence`, `issued_at`,
left/right target velocities, acceleration and duration. These are a small
future-interface hypothesis, not validated legacy command units. Only the
explicit `m/s` / `m/s^2` pair is modeled; claiming those strings alone does not
establish physical units. Arm also requires current measured-and-reviewed
**synthetic declarations** for all three evidence categories.

Both velocity magnitudes must be within `Policy.max_velocity`; acceleration
must be positive and at most `max_acceleration`; duration must be positive and
at most `max_duration`. Invalid/out-of-bounds numbers fault, not clamp. Booleans,
NaN, infinities and numbers outside the representable model domain are rejected.
Exact limit values are admissible if all other checks pass.

The target change per channel may not exceed declared acceleration multiplied
by time since the previous accepted intent (or arm). At equal time no velocity
change is allowed. This is a **discrete target-slew constraint**, not trajectory
generation, actuator acceleration control or proof of achieved velocity. The
zero baseline on arm is a synthetic assumption, not zero telemetry or a physical
measurement. A future integration must supply a separately validated trajectory
and actual-state contract; there is no such integration here.

An intent must not be future-dated, must be strictly younger than
`max_command_age`, and must still be inside its issued-time duration. Its
`issued_at` must also be **strictly greater than `State.armed_at`**, the latest
accepted arm time. Revocation clears this boundary; only an explicit new arm
sets it. It does not move on keepalive or subsequent intents.

Exact equality with the arm time is rejected as `stale_intent`, even for an
intent submitted after that arm event: equal timestamps cannot distinguish
pre-arm queued work from post-arm creation. This conservative rule also applies
when disarm/rearm or reset/restart/connect/authorize/arm share a timestamp.
Under the nondecreasing model clock, a pre-session intention cannot be revived
by attaching a fresh event token or using a still-unconsumed sequence. Stale
event tokens still fail ownership first. Callers must create a new intention
strictly after the current arm boundary; there is no relabeling or automatic
resume. These are declared model timestamps, not authenticated creation times
or proof about external queues.

Sequences start at zero, are consumed on accepted intent and never wrap/reuse,
even across model restart. Replays, skips and uint16 exhaustion fault. No retry exists.

At **exact equality** with a dead-man, intent, review or pending-reply deadline,
that condition has expired. The reply deadline is the earliest of explicit reply
timeout, intent duration, dead-man deadline and review expiry. A matched reply
does not remove the intent duration bound. Intent expiry faults rather than
catching up, assuming stop or auto-resuming. Every armed event rechecks review
freshness. Every owner-controlled event rechecks current declared identity,
profile, calibration, units, token and reviews. Clock regression faults. Deadline
addition that rounds back to the current time or exceeds the model time domain
is rejected; no epsilon admits a late event.

There is **no background watchdog**. Advancing synthetic time without calling
`step` does not execute code. `tick` detects expiry when processed;
`host_silence` declares a detected loss and faults immediately.
`watchdog_hypothesis` is an explicit fault injection, not an installed watchdog
configuration or observation. Linux/Python scheduling cannot guarantee execution
at a deadline, particularly after host silence/exit/crash. Independently verified
hardware/physical stopping behavior remains required.

Resource limits are software allocation budgets, not robot safety values:
`max_commands` defaults to 128 (1..65536), `max_events` to 256 (2..1024). The
last event slot is reserved for a visible `event_budget` rejection, preserving
an existing terminal state/primary failure rather than replacing it. All lifetime
records remain; there is no eviction, retry, recorder or unbounded work queue.
One pending request is allowed. Each raw field is at most 4096 immutable bytes,
each text field at most 256 characters, and immutable tuples at most 32 items.
Structural validation is iterative, with a maximum depth of 16 (root depth 0)
and 1024 visited value occurrences per input, counting the root, dataclass
fields, tuple items and repeated references. Both tuple and model-dataclass
nesting consume these budgets; exceeding either raises `ModelError` before
retention, without recursing through arbitrary input. Existing terminal state,
primary failure, pending evidence and bounded history remain preserved.
The numeric time domain is 0..1e12; integer event values are at most 128 bits.
Larger/mutable inputs are rejected structurally, without retaining arbitrary
objects. This is a bounded model audit, not a raw transport capture.

## Evidence is not physical proof

Records preserve the exact accepted event, model timestamp, profile/scope,
authorization/reviews, before/after states, pending status and fault result.
Both `Record.before` and `Record.after` are full frozen `State` snapshots,
rendered as JSON objects. `before` retains the pre-event authorization, reviews,
deadlines, counters and pending write evidence even when the event revokes or
clears them from `after`.
Distinct categories prevent promoting weaker evidence into stronger claims:

| Record category | Meaning and limitation |
|---|---|
| `software_intention` | Bounded target or stop/disarm intention; not a sent robot command |
| `request_write_declaration` | Synthetic raw bytes, sequence, accepted/uncertain counts; no actual write occurred |
| `correlated_application_reply_declaration` | Caller-declared raw reply and verbatim labels/status; correlation alone is not delivery, application ACK or authentication |
| `external_physical_observation_declaration` | Synthetic reference, reviewer, elapsed time, reviewed limit and pass/fail/block; not real measurement or proof |
| `external_simulator_crash_projection` | External test harness's knowledge of a modeled crash, not durable evidence emitted after a real host crash |
| `lifecycle_declaration` | Explicit connect, authorization, arm, timing, fault, reset and restart events |

Partial write evidence retains the accepted prefix count and uncertain remainder
separately. A write exception can be modeled as zero accepted/all uncertain; a
complete-write declaration requires accepted plus uncertain equal the raw byte
length. No returned count proves electrical transmission. The model does not
invent unknown raw bytes or infer missing physical observations.

Review booleans are only caller declarations, even when all are true. Actual
human-reviewed physical proof needs retained measurements, method, provenance,
installed identity, uncertainty, validity/scope and reviewer sign-off under the
separately approved plan. No trust store or physical gate checker exists here.
The acceptance matrix, not simulator success, describes that external decision.

An external-stop `block` declaration may use null elapsed/limit values rather
than invent missing measurements; it faults as `external_stop_blocked`. A
`fail` declaration faults as `external_stop_failed`. A declared `pass` requires
finite elapsed time no greater than its positive declared limit, but still
cannot change physical-stop status or replenish missing prerequisite reviews.

## Relationship to the merged getter client

There is no runtime coupling to `LegacyClient`, `Limits`, `Received`, `Transport`,
`SessionError` or its `OwnershipError`; see [the current contract](legacy-client.md).
The model follows its lifecycle conservatism: one constructing thread, one
outstanding request, current identity/ownership revalidation, immutable evidence,
no sequence wrap/reuse/retry/reconnect. Its token and sequence are model
capabilities/counters, **not** client tokens, wire sequences or cross-session
protocol correlation.

The real client offers only `read-raw-data`, `get-config`, `get-power-state` and
`get-unit-info`. UnitInfo needs literal `allow_telemetry_state_change=True` on
each request and is never automatic setup. This model does not call any getter.
`matched_candidate` is only correlation: delivery also needs the immutable
`Request.status == matched`. Even then, framing/status/CRC and accepted bytes
are not authentication, firmware identity, calibration, units, application ACK,
or physical safety. All non-clean labels fault this deliberately conservative
policy hypothesis, not a change to getter-client behavior.

The #8 collector and #10 commissioning work have independent schemas. No
recorder or commissioning evidence evaluator is duplicated or imported here.
Model record completeness describes accepted synthetic inputs only; it claims
nothing about OS/device queues, rejected adapter input, durable capture sealing,
crash-surviving logs or primary/secondary failures from a real client. Those
original client/collector facts must remain distinct in any future integration.

## Running actual offline scenarios

```sh
python3 -B -m tools.marvin_control_scenario data/synthetic-control-scenario.json
python3 -B -m tools.marvin_control_scenario data/synthetic-control-scenario.json \
  --output NEW_SYNTHETIC_REPORT.json
python3 -B -m unittest discover -s tests -p 'test_marvin_control*.py' -v
```

The example's bytes spell `SYNTHETIC`, not a robot frame. Every numerical limit
is hypothetical and must not be copied into an installed controller.

Schema version 1 requires `evidence_kind: "SYNTHETIC"`, `policy`, `scope`,
`reviews` and a nonempty `events` array. The example is a complete reference.
Event `kind` is a closed list from the transition table, plus `request_write`,
`application_reply`, `intent`, `keepalive`, `tick`, `transport_lost`,
`session_invalid`, `host_silence` and `watchdog_hypothesis`. Each event has `at`
and only its exact named payload fields. `scope: "expected"` explicitly repeats
the root scope; an inline scope permits mismatch scenarios. Review `scope` has
the same syntax. Authorization names root reviews and creates a unique
`save_token` alias. Later `token` fields reference it; aliases cannot be
overwritten. Failed authorization produces no usable capability. Old aliases
remain available to demonstrate replay rejection after revocation.

Every root review is schema-checked before execution, even if no authorization
references it. Kinds must be `calibration`, `physical_stop` or `limits`;
measurement/review flags must be exact booleans; all reference/reviewer/scope
fields must be nonempty bounded text; source must be exactly `SYNTHETIC`.
Validity endpoints must be finite non-boolean numbers in 0..1e12 with
`valid_from < expires_at`. False flags, an interval not current at authorization,
or nonempty but mismatched policy/scope/profile/unit declarations remain valid
synthetic negative scenarios: when used they produce modeled faults (exit 1),
not schema errors. Malformed declarations instead produce exit 2 with no
trace/output file, whether used or unused. Schema version 1 and the `Intent`
fields are unchanged; serialized state snapshots additionally include `armed_at`.

The CLI reuses `unique_object`, `read_regular_file` and `new_output_path`: no
duplicate/unknown JSON keys, nonstandard numbers, input symlinks (including
ancestors), special files, device/kernel paths or overwritten files. Input is
bounded to 1 MiB; output to 32 MiB. Parents must already exist; file output uses
exclusive creation. These are snapshot path checks, not protection against a
hostile process replacing ancestors concurrently. File output is not a durable,
atomic or crash-proof recorder; an I/O error is explicit and may leave a partial
new file, never a successful sealed report. Input bytes and their SHA-256 are
preserved/identified, not authenticated. Output defaults to stdout.

Exit 0 means the trace completed without modeled faults, exits or crashes,
**not physical PASS**. Exit 1 means the trace completed with one or more modeled
faults, host exits or crashes, even if later explicitly reset/restarted. A normal
`host_exit` without pending work is still terminal session loss for this report;
it cannot be hidden by restarting. Exit 2 means schema/input/output failure; stderr JSON
has `complete: false`. An invalid document is fully schema-checked before model
execution. There are no `--run`, serial, arbitrary opcode or actuator switches.
