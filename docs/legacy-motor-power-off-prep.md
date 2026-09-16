# Motor-power-OFF preparation: software only until separately authorized

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
