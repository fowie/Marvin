# Stop/failure acceptance matrix for manual #11

This is an authored acceptance contract for #12 and #14, **not an approved
physical test plan or authorization to operate hardware**. The implemented
[offline model](offline-control-policy.md) can satisfy software checks only.
Every example/trace is SYNTHETIC. #11, #13 and physical Epics #3/#4/#5 remain
blocked until their prerequisites and separately approved operator plans pass.

## Prerequisite gate and outcome rules

Before any physical case, an explicitly authorized operator and reviewer must
approve its exact setup, trigger method, stop method, observation instruments,
limits and abort criteria. #1/#2 require disposition, including damaged PEND
TXCVR and hub port-4 over-current before affected branch use. Motor/servo power
**and signals** remain isolated outside specifically authorized steps.
Wiring changes require removal of all power/back-power. Independently verify
the physical stop and energy-limited fixture under that separate plan. Agents
and CI do not execute any physical row.

No USB/serial opening, privileged probes, firmware flashing/reset, controller
reset, power switching, motion/servo/LED/stop/zero setters, latch clearing or
interlock bypass is authorized by this document or software.

Every row requires an evidence packet with: case ID, operator/reviewer and plan
revision, installed hardware/firmware identity limitations, legacy profile,
connection generation, calibration and unit evidence, policy revision, physical
fixture/isolation statement, instrument identifiers and uncertainty, independent
stop verification, precondition observations, trigger timestamp and uncertainty,
separate host/TX/RX/external time bases and alignment, original raw evidence
references and completeness gaps, measured outcome, and signed review.
Protect unpublished/private originals; only reviewed derived facts/fixtures may
be published. A simulator report hash is not authentication or approval.

Before each case the reviewer must specify **measured installed limits**, with
uncertainty margins: maximum elapsed stop/energy-removal time, displacement,
residual output/energy criteria, observation dwell, and (where relevant)
watchdog/dead-man/transport-loss thresholds. Call these that row's **reviewed
envelope**, not a universal safe constant. Distinguish motion stopping from
removal of actuator energy and from a physically latched stop.

| Outcome | Manual #11 decision |
|---|---|
| PASS | All prerequisites are signed/current, the requested trigger actually occurred, evidence is complete enough to bound uncertainty, software behavior meets the row, and independent physical measurements meet every reviewed-envelope criterion throughout the observation dwell. A reviewer signs the row. |
| FAIL | A measured limit is exceeded, unexpected motion/output/energy or automatic recovery occurs, a required independent stop fails, or a software invariant is violated. Follow only the separately approved operator abort procedure; preserve evidence. Do not retry automatically. |
| BLOCK | Missing/unreviewed/stale/mismatched identity/calibration/units/limits; unsafe/unapproved setup; missing instrumentation, uncertain trigger/time alignment, evidence gaps preventing a conclusion, or physical observations absent. Synthetic success, writes, replies, ACKs and zero telemetry alone always leave physical acceptance BLOCKED. |

PASS is scoped to that installed configuration, test envelope and case, not
general authorization to drive. Any relevant hardware/firmware/profile/calibration
change invalidates prior applicability. Repetition counts and acceptance
statistics belong in the approved plan, not a guessed software default.

## Fault cases

In this table, **A** means the common auditable packet above plus immutable model
records/current scope, authorization, timestamps and primary fault. **W** means
exact request bytes, attempt/sequence, accepted and uncertain counts and
write-time bounds. **R** means exact reply bytes, labels, request status and
correlation/time bounds, without upgrading a candidate to delivery. **X** means
an independent external physical observation, separately time-aligned to the
trigger. W/R/X are distinct categories. Do not reconstruct missing W/R bytes
from a successful write or a CRC. A real crash may lose in-memory A/W/R;
absence must remain an explicit gap.

Each row uses the common PASS/FAIL/BLOCK rules; the last column adds its specific
physical decision. Software terminal states are model-only and do not assert
that a physical mechanism stopped.

| Case | Preconditions and trigger | Software terminal state / preserved evidence | Separately required X observation and row decision |
|---|---|---|---|
| S01 stop/disarm intent | Authorized `armed`, no pending request; explicit UI/software stop or disarm | `disarmed`, ownership revoked; A and stop intention, prior W/R retained. No live stop command is produced. | Independently measure stop and residual energy relative to the operator-reviewed trigger and supplemental UI intention. PASS only within the envelope and with no resumed output; a UI event/ACK alone is BLOCK. |
| S02 stop while delivery uncertain | One intended/submitted request; stop/disarm before any qualified reply | `fault: uncertain_delivery`, pending marked failed; A/W plus any R preserved separately | Measure actual response for the uncertain-delivery case without assuming whether the intent arrived. FAIL on continued/excess motion or energy beyond envelope; BLOCK when delivery/measurement uncertainty prevents bounding it. |
| S03 host silence | Armed modeled owner; no further keepalive; inject detected `host_silence` or advance `tick` to dead-man boundary | `fault: host_silence` on declared detection or `deadman_expired` at/after bound; A and last W/R. No retry, catch-up or resumed work. | Independently measure stop after last qualified host activity, including scheduling/timestamp uncertainty. PASS requires observed independent stopping within the measured silence envelope, not merely detection by Linux. |
| S04 normal application exit | Active/disarmed host; declared `host_exit` before expiry, with/without outstanding request | `exited`; outstanding delivery remains uncertain/failed, token revoked; A/W/R. Deadline fault takes precedence if already expired. | Measure output/motion/energy across actual process exit and the full dwell. Verify no reopen/retry/resume effect. PASS needs X; the fact that `finally`/close ran is not enough. |
| S05 host crash/process loss | Armed or pending; external harness declares `host_crash`, including at a deadline | `crashed` is an **external simulation projection**, not crashed-host execution; cleanup explicitly cannot run. Preserve last durably available A/W/R and independent crash witness; mark missing records. | Independently witness actual host/process loss and stopping behavior using an observer not dependent on the failed host. BLOCK without verified independent stopping/trigger evidence. A host cannot guarantee cleanup after its own crash. |
| S06 USB/transport disappearance | Armed or pending; declared `transport_lost`, generation loss or invalid identity | `fault: transport_lost` / `identity_changed`; A/W/R, failed pending and last known identity | Independently observe connection loss and mechanism/output behavior, including control-line or queued-work effects. PASS only if the reviewed loss envelope holds; USB loss or close alone does not establish stop. |
| S07 reply/session deadline | Intended/submitted request; reply/tick at or after the earliest applicable deadline | `fault: deadline` (or earlier dead-man/review/intent expiry); A/W/R includes late candidate, not delivered result | Compare independent motion/energy traces to the reviewed deadline and uncertainty. PASS requires physical stopping within envelope even when the host cannot deliver a reply. Host clock deadline alone is BLOCK. |
| S08 installed watchdog hypothesis | Authorized hypothetical session; explicit `watchdog_hypothesis` or planned missed service interval | `fault: watchdog_hypothesis`; A and declared hypothesis, last W/R; no heartbeat/watchdog setting is guessed or sent | Measure whether an installed independent watchdog exists, what services it, its actual expiry distribution and output/motion/energy behavior. BLOCK until measured/reviewed. FAIL if observed behavior misses the reviewed envelope. |
| S09 late/stale/replayed traffic | Pending/current generation, optionally earlier completed sequence; inject `late`, `stale`, or stale/reused command sequence | Visible fault with original label or `sequence_error`; no delivered intent/reply, no retry; A/W/R verbatim | Establish actual stale/late traffic and measure whether any old action can affect outputs after expiry/reconnect. PASS requires rejection/no delayed activation plus envelope compliance; a label from the model alone is BLOCK. |
| S10 duplicate request/reply | One accepted intent or completed reply; repeat a write/reply/consumed sequence | `duplicate_write`, `duplicate` or `sequence_error`; A/W/R including rejected duplicate; no second write or action is generated | Observe that repetition causes no additional/continued physical output or resumed motion. FAIL on replay-driven output. Retain both attempted and observed events, not just the accepted one. |
| S11 unmatched/unclean reply | Pending or no request; unsolicited/pre-request/echo/unmatched/unverified, unexpected command/shape, malformed/noise/partial/ambiguous boundary | Visible fault; complete input label tuple and raw opaque bytes retained in R; no recovery scan, candidate promotion or automatic retry | Independently observe actual outputs and timing despite untrustworthy correlation. PASS needs physical envelope compliance; missing origins/time bounds force BLOCK, even with CRC-valid bytes. |
| S12 sequence/status failure | Pending; wrong/out-of-range/replayed sequence, status error or `error_status`; candidate whose request status is not `matched` | `sequence_error`, original status label or `reply_not_delivered`; A/W/R, failed pending | Measure response to the reviewed erroneous case without interpreting an unknown status as a stop. PASS requires X and proper software rejection; an error response or ACK alone is BLOCK. |
| S13 partial write | Pending intent; synthetic accepted prefix less than attempted length and explicit uncertain remainder | `fault: partial_write`; A/W retains full synthetic attempted bytes and exact separate counts; no suffix retry | Independently measure outputs for the actual approved interrupted-submission case and any delayed fragment interpretation. FAIL for unintended/excess motion; uncertainty not bounded by X is BLOCK. |
| S14 uncertain write/exception | Pending intent; zero known acceptance, entire attempted write uncertain, or invalid adapter count | `uncertain_write` / `write_contract`; A/W, all uncertainty retained; no replay | Measure behavior without assuming zero accepted means zero physically delivered. PASS requires independently bounded outcome through dwell; missing write/physical evidence is BLOCK. |
| S15 restart | Any mode; explicit modeled restart (if expiry detected first, repeat explicitly after fault) | `new`, old token/pending working state invalidated; historical A/W/R retained, consumed sequences not reused | Observe output across actual restart and queued work, no automatic arm/resume, continued independent stop effectiveness. Fresh reviewed evidence/approval required. Old app state or logs alone cannot PASS. |
| S16 explicit reconnect | Fault/exit/crash requires reset/restart, then fresh matching `connect`; changed generation/profile rejected | No direct reconnect from fault; `new` then `disarmed`, no authorization until separately granted; A including old/new declared identity | Measure reconnect effects and establish a fresh boundary so old queued data cannot become fresh work. PASS requires no unintended output and explicit fresh ownership/identity review; enumeration success alone is BLOCK. |
| S17 owner/authorization loss | Armed model; different owner, reconstructed/old token, missing authority or wrong current scope | `owner_changed`, `ownership`, `identity_changed`, `profile_mismatch` or `scope_mismatch`; A/W/R; token revoked | Observe no concurrent-owner/replayed action can produce output; check independent stop and approved physical access controls. Process-local Python tokens are not physical security proof. |
| S18 invalid/unbounded intention | Armed; unknown units, bool/nonfinite value, out-of-range velocity/acceleration/duration, stale/future intention, target-slew violation or clock regression | Visible `unknown_units`, `invalid_number`, `stale_intent`, `acceleration_limit`, `intent_expired` or `clock_regressed`; A includes rejected input; no clamp/retry | Under a separate reviewed plan, establish actual units, per-mechanism bounds and stopping response. PASS needs independent measured limits and outcome; source names/config values cannot substitute. |
| S19 missing/stale/mismatched proof | Before authorization/arm or during armed events; missing calibration/physical-stop/limit review, false review/measurement flag, expired validity or policy mismatch | Fault/disarmed model; A preserves declarations and rejection; no software self-approval | BLOCK until actual signed, measured, current, scope-matched evidence exists. If observed hardware violates an already approved envelope, FAIL rather than replacing evidence with a new declaration. |
| S20 lifetime work/evidence exhaustion | One pending request or lifetime command/event budget reached, even after modeled reset | `outstanding_work`, `command_budget` or `event_budget`; bounded history and terminal state, no eviction/resume | Confirm separately that software cannot guarantee recording forever or physically stop by running out of memory/work budget. PASS needs measured independent stopping; absent evidence/gaps leave BLOCK. |

## Evidence review sequence

The operator/reviewer fills and approves the common packet and case-specific
envelope **before** choosing any physical trigger. This matrix deliberately
provides no executable physical trigger commands.

1. Confirm prerequisite disposition and the exact installed configuration and
   scope; mark BLOCK before a physical step if anything is unresolved.
2. Record the reviewed hypothesis and both successful and fault-trigger
   preconditions. Keep host intention, request write, correlated reply and X on
   separate tracks with explicit timestamps, uncertainty and completeness.
3. Apply only the separately approved operator procedure, including its
   independent abort path. Preserve negative results and uncertain writes, not
   just passing intervals. Never automatically retry a failed physical case.
4. Compare each X measurement and dwell against its pre-reviewed envelope,
   separately checking residual motion, energy and unintended resumption.
   Record PASS/FAIL/BLOCK with reviewer, rationale and exact evidence references.
5. Reauthorization/rearming, any physical latch handling, and any actuator
   integration require independent explicit approval; this software cannot
   perform them. No row can be approved by simulator success alone.

The committed synthetic scenario includes distinct W/R/X-declaration records,
but its X is explicitly not a real externally measured stop. It therefore never
passes this physical gate. Offline unittest exercises the rows using fake events
and explicit fake times; actual CLI subprocesses exercise the file/report path.
Neither tests nor the CLI operate hardware.

## Historical command and timing limitations

The already published [bring-up findings](marvin-bringup-plan.md#3-validate-stop-behavior-before-commanding-motion)
describe old legacy IDs `11` (signed left/right velocity), `0B` (four raw-PWM
words), and `1E` (two servo values). These remain **source-only, unvalidated
actuator descriptions**, not observed actuation, encoders, units or permission.
No executable mapping is added. Their meanings must never be replaced by newer
Drive/Head tables; getter/movement/reset IDs collide. Raw PWM is not an initial
general-purpose driving interface.

The installed timeout/stop limits must be measured and reviewed on the actual
firmware/hardware. Config fields such as `motionTickTimeout`, `motorStop1Sec`,
`heartbeatPeriod`, velocity/acceleration/PID words, newer firmware defaults,
the observed telemetry tick rate and reported zero PWM/velocity supply **none**
of those limits or proofs. ACKs, CRC-valid frames and firmware-like identity
words likewise do not establish safe stopping, physical units or calibration.

Physical tethered-control integration is blocked on #1 -> #2 -> #3 -> #4 and a
separately approved operator integration plan. Autonomy, inferred camera/audio
capabilities and claims about odometry, proximity/cliff health or actual battery
measurements are outside this model. Each needs its own measured evidence,
inventory and operating approval; no synthetic declaration here replaces it.
