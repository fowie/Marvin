# Human runbook: from offline policy to reviewed physical evidence

**Start here: answer from existing knowledge only. Do not change connections,
power anything, open a device, or perform a test to answer the first question.**

This runbook organizes the human decisions and evidence still needed. It is not
a physical operating procedure or permission to operate the robot. We can
complete the desk work interactively now; every later physical activity needs
its own reviewed procedure and explicit, session-specific authorization.

The detailed requirements remain in #1, manual readiness #9, manual stop
verification #11, manual commissioning #13, and the
[stop/failure acceptance matrix](stop-failure-acceptance.md).
The [offline model](offline-control-policy.md) supplies no live actuator path.

## How we will work together

I will ask one question at a time, record your answer with its confidence and
limitations, identify the next missing item, and draft the required documents.
You supply facts only you can observe, select the responsible operator/reviewer,
and approve or decline specific plans. I can organize evidence and run offline
software; I cannot certify isolation, electrical safety or a physical stop.

We distinguish **unknown**, **operator-declared**, **observed with a method and
record**, and **reviewed for this configuration**. Saying "done" or checking an
issue box is not a substitute for the underlying record. Unknown is an acceptable
answer and keeps the affected step blocked.

Answers and raw evidence are not automatically published. Keep private captures,
personal information and unreviewed photos local. Share a description or opaque
evidence reference first; public documents receive only reviewed derived facts.
The coordinator reconciles approved records with the issues; no automatic
physical sign-off, issue closure, merge or continuation follows an answer.

## Starting record

As of 2026-09-15, the [operator comment on #9](https://github.com/fowie/Marvin/issues/9#issuecomment-5687976621)
reports: "PEND TXCVR usb port has been repaired." We will not ask you to repeat
that repair. The combined damaged-port/over-current checklist item is checked,
but that comment alone does not specify a separate hub port-4 over-current
observation, verification method or current applicability. We need to reconcile
the evidence, not assume either that the repair failed or that every related
hazard is resolved.

The offline commissioning, polling and policy PRs are separate workstreams.
Their approval/merge does not establish a physical gate. Before using a tool,
confirm the reviewed revision actually contains it; this runbook does not import
an unfinished schema or invent a live adapter.

## Steps and hold points

Complete these in order. Documentation can advance while a physical gate is
blocked, but a later physical step cannot bypass an earlier gate.

| Step | What I need from you | What I do | Completion / hold point |
|---|---|---|---|
| 1. Declare the current state | From existing knowledge, describe whether motor power, motor signals, servo power and servo signals are isolated. State unknowns; do not inspect or change anything yet. | Record the four declarations separately and identify uncertainty. | A recorded starting state, not proof of safety. Unknown or connected paths block physical progression. Any isolation work needs a separate procedure. |
| 2. Identify people and scope | Name the responsible operator and appropriate reviewer, which installed robot/controller/USB branch is in scope, and the intended first goal. Roles may be identified privately. | Draft a scope record with known identity limitations and excluded mechanisms. | Responsible people and exact scope agreed; no physical authorization yet. |
| 3. Supply existing readiness records | Provide available wiring/channel/USB diagrams or evidence references, repair record, separate port-4 disposition, known supplies/back-power paths, protection/grounding information and prior changes. Unknown fields stay unknown. | Build a gap list against #9; preserve the repair report without inventing observations. Map evidence into the reviewed #10 checklist only when that version is available. | Each item has an attributed record or an explicit blocker. Creating a wiring map is not permission to trace live wiring. |
| 4. Establish the reviewed read-only baseline | Identify any existing authorized #1 session records and their applicable configuration. If more collection is needed, approve a separate read-only plan only after its setup and isolation requirements are reviewed. | Review available evidence offline; document missing repeated snapshots, identity/ownership boundaries, disconnect/reconnect behavior and capture gaps. | Human sign-off for #1 on this configuration. Existing getter code or a successful packet alone does not pass this gate. No automatic UnitInfo/setup or implied live adapter. |
| 5. Complete electrical and independent-stop readiness | Supply or arrange qualified review of fixture/support, clearances, supplies/grounds/fuses/current limits, power AND signal isolation, independent energy-removing stop, and proximity/cliff observations. Any new inspection/measurement needs its own approved plan. | Organize the #9 evidence and unresolved risks; distinguish declarations from measurements. | Signed #2/#9 readiness for this configuration. The stop must not depend on Linux, USB or firmware. A UI stop is supplemental. |
| 6. Approve one stop-characterization plan | With the reviewer, select the first #11 case, exact installed-system stop hypothesis, isolated-interface stage, instruments, independent abort method and a narrowly bounded procedure. Supply reviewed limits and their basis, or identify the need for a separate characterization plan. | Draft a case-specific evidence sheet from the matrix and flag missing bounds, methods or unsafe assumptions. | Written plan revision approved before any physical step. No guessed timeout, command ID, units, speed or energy limit is supplied by the model. |
| 7. Perform and review the approved stop cases | Only the authorized operator performs the approved procedure. Return the actual observations, raw-evidence references, timing uncertainty and unexpected outcomes after each case. | Compare the record with its reviewed envelope; preserve separate write, reply and external-stop evidence and propose PASS/FAIL/BLOCK for human review. | Human-reviewed #11/#3 outcome. Any critical FAIL/unknown blocks commissioning; no automatic repeat or waiver. |
| 8. Commission one mechanism | After #1/#2/#3 pass, select one wheel or servo and approve a separate #13 trial plan with measured bounds, failure response, fixture and independent stop. Supply channel/direction/neutral/unit-conversion/limit measurements. | Prepare and review the per-mechanism calibration record without inferring units from raw values. | Human-reviewed calibration and failure record for that mechanism only. A further connection or actuator requires separate approval. |
| 9. Decide whether to pursue live integration | Review the physical evidence and decide whether to commission a separately reviewed bounded-control implementation and operating plan. | Use measured/reviewed inputs to assess future requirements; do not turn this simulator into a transport. | Physical teleoperation remains blocked until that separate work and approvals are complete. Autonomy, camera/audio integration and inferred sensor-health claims are excluded. |

Step 4's physical execution still requires its own electrical/isolation
preconditions; the order above is not permission to use an unresolved branch.
Existing readiness documentation can be reviewed before #1's physical gate.
If a precondition cannot be established without physical work, stop desk-based
progression there and have the responsible reviewer define that work explicitly.

## What to return for a desk-work item

A short response is enough; I will ask for missing fields individually:

```text
Item:
What I know:
Basis: existing knowledge / operator declaration / observation / reviewed record
Observed when:
Applicable robot, controller, branch and wiring revision:
Evidence reference, if any:
Unknowns or changes since the record:
```

Do not fill an unknown field with a guessed value. Historical firmware-like
identity words, USB enumeration, config values or zero telemetry do not establish
the installed firmware image, safety, calibrated units or current isolation.

## Required approval before any physical activity

The responsible reviewer must define and approve the exact activity, not just
this runbook. The record must identify:

1. Operator/reviewer, date/session, configuration, case and plan revision.
2. Prerequisite evidence, including isolation and independent stop as applicable;
   whether the procedure permits any specific power, signal or connection change.
3. The exact allowed procedure and approved implementation, duration/energy/range
   bounds, their evidence basis, and prohibited actions.
4. Instruments, observation method, clock alignment/uncertainty, expected outcome,
   explicit abort criteria and the independently available abort procedure.
5. Evidence retention, PASS/FAIL/BLOCK decision rules and the point at which the
   operator stops and returns results for review.

Wiring changes require an operator-confirmed removal of robot power, applicable
batteries and USB back-power under that procedure. Outside specifically approved
steps, motor/servo power AND signals remain isolated. An instruction to
"continue" is not authorization for new hardware access, power switching,
actuator/stop/zero sends, resetting/flashing, or physical latch handling.
Interlocks are not bypassed to obtain a preferred result.

## Stop-case sequence to plan, not execute now

The #11 reviewer chooses the ordering and applicability using matrix cases
S01-S20. Start with review of the stop hypothesis and isolated-interface
observations; advance to energy-limited physical observations only under a
separately approved stage. Cover host silence, exit/crash, transport loss and
watchdog/deadline behavior as well as uncertain delivery, stale/error traffic,
restart and reconnect. This is a coverage list, not instructions to unplug a
running system or inject arbitrary commands.

For every physical case, return the precondition/trigger record, software state,
request-write evidence, reply evidence, independent actual-motion/output/energy
observations, uncertainty and the reviewed decision. A crashed host cannot
guarantee cleanup or preserve its final in-memory logs; the physical witness
must not rely on that host remaining alive. Missing evidence remains a gap.

SYNTHETIC traces cannot supply installed limits or actual stop evidence. If those
limits do not yet exist, do not substitute example numbers: the reviewer must
first approve a bounded measurement/characterization procedure. Completion of
this conversation, a schema validator or an offline test never substitutes for
that human decision.
