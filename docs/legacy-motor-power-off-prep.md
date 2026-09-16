# Motor-power-OFF preparation: software only until separately authorized

**Physically inapplicable in the currently reported shared-supply wiring.**
On September 16, 2026, the operator clarified that the HY1803D powers both
controller electronics and motors; USB alone does not provide operational
controller power in this setup. With MotorL attached and the HY1803D
OFF/output disconnected, the preparation cannot communicate with the
controller. Do not bypass `--motor-supply-off`, turn on the shared supply,
or treat this profile as a powered-motor mode. The tool and refusal history
below are retained; no successful robot execution of this profile exists.
See [the topology clarification](#operator-declared-shared-supply-topology).

This is a separate fixed preparation profile, **not a motor-supply-ON test,
validated stop API, or permission to change wiring or power**. MotorL is
attached under this profile, so `--actuators-isolated` would be false and is
rejected. Motor power remains OFF; MotorR and all servos remain isolated.
Software cannot prove any of these physical conditions.

Only offline development and synthetic/host-PTY verification accompany this
implementation. No robot preflight, device check, USB monitor open or serial
open was performed for implementation verification. The host PTY is not
hardware evidence.

## Offline default and future invocation declarations

```sh
python3 -B -m tools.marvin_legacy_motor_power_off_prep
```

This prints the immutable transcript and requirements without preflight,
device/file creation or transmission. A **future, separately authorized**
operator invocation requires all of:

- `--run`, `--motor-supply-off`, `--motor-left-only-connected`;
- `--motor-right-and-servos-isolated`;
- `--authorize-unvalidated-zero-velocity`, `--unprivileged-usbmon`;
- `--new-boot-declared`, a fresh operator declaration, not an inference from
  enumeration, tty/USB identity, or returned firmware values;
- `--expected-physical-port` and `--output NEWDIR` in a trusted existing parent.

There is deliberately no runnable live command example here. This document,
previous declarations, saved metadata, successful tests and a successful
preparation observation are **not current invocation consent or standing
authorization**. API flags must be literal booleans; positive requirements
must be literal `True`. Partial, truthy or mixed full-isolation/preparation
consent fails before device identity access. A fresh UUID run ID and the
historical operator declarations are stored for each admitted run. This ID is
not a USB generation; actual connection identity is retained in the baseline.

The coordinator's private fixed profile passes the actual preparation
declarations to the capture boundary and USB recorder, never substituting
`--actuators-isolated`. The recorder's separate route requires ordinary-user
operation, full binary evidence and the fixed budgets. Existing recorder
invocations still require full isolation by default. No sudo fallback exists.

## Immutable state machine and transcript

| Phase | Sequence | Exact application frame including CRC and footer |
|---|---:|---|
| Unvalidated zero velocity | 1536 | `5300061100040000000000e4a145` |
| ReadRawData 1 | 1537 | `53010600000000ead445` |
| ReadRawData 2 | 1538 | `53020600000000eae745` |
| ReadRawData 3 | 1539 | `53030600000000eb3645` |
| ReadRawData 4 | 1540 | `53040600000000ea8145` |
| ReadRawData 5 | 1541 | `53050600000000eb5045` |

Maximum application output is **64 bytes / six writes**, each exactly one
syscall. Any interrupted sequence is only a prefix; there is no retry,
corrective zero, suffix resend, trailing command or reopen.

The existing live adapter owns one raw, unflushed 57600/8N1, no-flow-control,
requested-low-DTR/RTS open and same-process/thread close. Existing settings
readback, physical identity, generation, exclusive ownership, clock bridge
and full USB correlation guards are retained. After configuration it requires
one second of quiet before the zero. It then observes the **entire three-second
zero response window**, even after receiving a candidate.

Exactly one CRC-valid sequence-1536 / command-11 / status-80 **empty-payload**
reply is required. This is correlation only, with semantics unverified and
**not an ACK**. An opaque nonempty reply, which the separate isolated-zero
characterization intentionally retains as an opaque candidate, fails this
stricter preparation profile.

Only after that complete clean window may the five fixed getters proceed.
Each has a 0.5-second response deadline and a full-window observation that
rejects additional input. At least one second separates the previous reply's
conservative USB ingress end from the next request. Both the exact next
transcript frame and observation/idle/deadline state are enforced at the
submission boundary, not merely in the outer loop.

Every getter requires one CRC-valid matching status-80 reply with exactly
134 payload bytes. `marvin_legacy_telemetry.interpret_packet` interprets the
actual received packet; both raw payload slices and the decoder's offset,
size, raw hex, unsigned and signed values must agree and be exactly zero:

| Decoder field | Payload-relative offset | Required raw bytes |
|---|---:|---|
| `motorVelocityL`, `motorVelocityR` | 72, 74 | `0000` each |
| `motorPwmLeftForward`, `motorPwmLeftReverse` | 90, 92 | `0000` each |
| `motorPwmRightForward`, `motorPwmRightReverse` | 94, 96 | `0000` each |

These are raw source-derived words, not calibrated velocity, physical motion,
brake/coast state or measured motor output. Other decoded telemetry fields
are preserved without adding unsupported physical conclusions.

## Faults, bounds and evidence

Any unknown shape/status, nonzero required word, unsolicited/prewrite input,
corrupt/partial/additional RX, uncertain write, identity/settings change,
clock uncertainty, USB loss/drop/byte mismatch, recorder failure or incomplete
evidence suppresses remaining requests and closes. There is no recovery path.
All dequeued raw bytes, packet classifications and full decoded telemetry are
kept even on failure; USB evidence preserves close/tail events.

- **One absolute 15-second operational deadline**, established before raw-open
  validation/quiet and shared across all serial phases; never refreshed.
- Separate **five-second cleanup budget**. No close drain or application write.
- USB **20-second nominal / 25-second hard** lifetime from recorder readiness,
  including final tail. The serial deadline does not terminate that tail.
- 8192 total serial RX bytes, reads up to 512 bytes; 4096 observation iterations
  and 256 decoded events per response window, with a bounded journal of 256 KiB.
- 4096 bytes per binary USB payload; 1 MiB combined recorder evidence and
  10,000 target USB records. Existing dropped/queued/final-stat checks apply.

Limits are cooperative/post-call checks, not preemption of blocked kernel or
filesystem operations. Kernel line transients remain possible despite low-line
requests. Clock correlation and cached host identity are not authenticated
physical identity or firmware attestation.

Private metadata, raw adapter journal, binary USB evidence and final hashes
reuse the isolated diagnostic's lifecycle. The extracted response-window
helper does not own open/write/close. The isolated wrapper preserves its
single-write sequence-1024 and opaque-payload behavior. Existing getter
transports, clients, encoders, TX policy and the general session CLI gain no
arbitrary setter support. Private Python helpers are not a sandbox against
malicious callers replacing internal state or constants.

Only a clean final serial, USB-tail and evidence-sealing result receives
`preparation_observation_complete_unverified`. Metadata explicitly records
historical declarations, `declarations_are_current_permission: false`,
`motor_supply_on_permission: not_granted`, and acknowledgment/physical stop
as `not_established`. Any later tail, close or sealing failure invalidates the
overall result. A successful observation grants no power permission.

## Software verification

`tests.test_marvin_legacy_motor_power_off_prep` consolidates literal/mixed
consent, truthful subprocess argv, recorder metadata/drop failure, exact
transcript/phase gates, every required actual raw/decoded nonzero word,
unknown/status/noise/partial/additional responses at every phase, uncertain
writes, shared deadlines, cleanup, evidence sealing and prewrite guards.

Its real host-created PTY test exercises native raw termios, flock/TIOCEXCL,
one serial open, six native serial writes, reads and same-owner close. Only
unsupported PTY modem bits and host identity/ownership boundaries are replaced;
the USB publication comes from an explicitly synthetic emulator, with the real
ingress reader and clock bridge. It does not claim USB hardware capture,
physical isolation, reboot, motor behavior or safety. No captures, private
paths or photos belong in the repository.

## First released preparation: cached preflight refusal

On September 16, 2026, the parent released one attempt at implementation
`2f6c6e47fb27d1813f350442e91a687f1ca98819` after the operator declared:
"Prepared: Motor L connected; right motor/servos isolated; USB restored after
full power removal; motor supply off and disconnected." This is an operator
statement, not an electrical measurement or proof of reboot.

The **first cached preflight failed**, before invoking the preparation CLI or
opening any serial device/USB recorder. The approved by-id selector's
`udevadm info --query=property` command returned exit status 1. The traceback
retained the command and exit status but did not expose captured stdout/stderr;
the query was not repeated to recover those streams.

A bounded exact-path metadata check at **04:58:55.182376 UTC** found both the
approved by-id link and `/sys/bus/usb/devices/1-1.1.3.3` absent. The planned
capture directory was also absent. No current USB identity/generation could be
pinned; no alternative port was searched. This supports absence of the
expected host connection at that time, not a diagnosis of its physical cause.

The parent then separately authorized **one cached sysfs inventory** for exact
VID/PID `045e:4444` across ports, without device opens or substitution.
At 04:59:23.675808-04:59:23.677381 UTC it found no match anywhere in that
inventory; the expected by-id link, `/dev/ttyACM0` and pinned port were absent.
The inventory had no cached-read errors or stderr. No udev/preflight query
was repeated. These snapshots do not prove a physical disconnection or rule
out an enumeration problem, but provide no evidence of a changed host port
containing the expected identity.

There were **zero device-open attempts, recorder launches or application
submissions** by this attempt. All sequences **1536-1541 remain unsubmitted**.
There is no USB/serial capture, reply, decoded motor field or tail evidence
from this refusal, and no claim about unrelated host bus activity.
Private audit ID: `motor-power-off-prep-1536-first-refusal`, retained outside
the nonexistent capture directory.

The released attempt is consumed by this refusal. Hardware remains on hold
for the parent/operator to review the missing connection. A subsequent
attempt requires a new explicit release and fresh guards; no supply
connection/ON, wiring change, retry or other command is authorized.

## Operator-declared shared-supply topology

At 05:09 UTC on September 16, 2026 (22:09 September 15 operator-local time),
the parent relayed the operator's clarification: the HY1803D was **ON during
earlier successful captures**, with the motor plugs disconnected, and supplies
**controller electronics as well as motors**. The preparation plan's
assumption that restoring only J10 USB would power operational controller
logic was incorrect for this reported topology.

The current declared configuration is MotorL attached, HY1803D OFF/output
disconnected, and USB restored. The missing cached controller identity and
zero-TX refusal are consistent with absent operational controller power;
they do not prove a USB fault. This explanation is operator-declared, not a
new electrical measurement, cached device observation or powered experiment.
The earlier refusal record and its then-unknown cause are preserved.

The earlier all-actuators-isolated captures remain valid communication
evidence: **motor load power/signals were disconnected while the controller
board was powered**. Full actuator isolation did not mean the controller board
was deenergized or USB-only powered. Do not reinterpret those observations as
board-power-off tests or infer that USB presence alone keeps the board running.

This preparation profile's required supply-OFF condition prevents operational
communication in the current wiring. Turning the shared supply on with MotorL
attached would change the physical test envelope, not fix a software guard.
No guard bypass, code broadening, supply connection/ON, retry or powered
MotorL test is authorized. The parent is determining available meter/scope
capability for a separately reviewed **no-load motor-output characterization**
before considering any powered-MotorL attempt; no measurement procedure or
hardware action is authorized here. Hardware remains closed and on hold.
