# Isolated, one-shot zero-velocity characterization

This diagnostic advances the **isolated-interface** prerequisite of manual
#11. It is not a powered-wheel trial, validated motor-stop API, commissioning
step or permission to change wiring/power. The installed setter's response and
physical behavior are unvalidated. Keep motor power, motor signals, servo power
and servo signals disconnected under a current operator-confirmed plan.

The authored command map describes legacy `11` as left/right little-endian
signed 16-bit velocity values. This tool admits exactly one immutable transcript:

| Field | Fixed value |
|---|---|
| Legacy profile / sequence | S/E / 1024 |
| Command / outgoing response field | `11` / `00` |
| Payload | `00 00 00 00` (two numeric zeros, not calibrated physical velocities) |
| Entire frame, including CRC/footer | `5300041100040000000000fdc145` |
| Application TX budget | **14 bytes, one syscall, one attempt** |

There are no sequence, opcode, payload, nonzero-value, retry or timing knobs.
No automatic trailing stop, heartbeat, UnitInfo, power/servo/reset command or
reopen is performed. Repeating the tool requires a separately reviewed decision;
the fixed sequence must not be casually reused after an actual submission.

## Offline default and explicit live gates

```sh
python3 -B -m tools.marvin_legacy_zero
```

Default invocation prints the exact transcript, bounds and consent requirements
without preflight, file creation, device access or transmission. Only after
review and explicit hardware-ownership release may an operator authorize:

```sh
python3 -B -m tools.marvin_legacy_zero \
  --output /trusted/private/existing-parent/NEW-ZERO-CAPTURE \
  --expected-physical-port 1-1.1.3.3 \
  --actuators-isolated --authorize-unvalidated-zero-velocity \
  --unprivileged-usbmon --run
```

This example is not standing permission. The three API consent values must be
literal `True`, not truthy strings/integers. The coordinator remains ordinary
user; there is no sudo/permission fallback. The current physical port, USB/tty
generation, descriptors, udev exclusions and visible exclusive ownership are
revalidated before opening. USB recording starts first. Raw unflushed tty
configuration is fixed to 57600/8N1, no flow control, requested low DTR/RTS,
with exact readback checks as described in [LIVE collection](legacy-live.md).
Kernel open/close line transients and first-open echo remain possible.

## Narrow code boundary

The new `_ZeroTransport.write` accepts only the single fixed transcript and
rejects any second call. It reuses the existing internal exact-once submission,
identity, raw recording and same-owner close mechanics. Public
`LiveTransport.write`, `LegacyClient`, getter encoders, polling and
`marvin_tx_policy` remain getter-only and reject this setter. The existing
one-shot probe does not gain a setter option.

An internal coordinator mode records the diagnostic's real 14-byte transcript
and requires this isolated legacy configuration and bounded recording settings.
It cannot be selected by the existing session CLI and is not a general command
or profile bypass. The diagnostic exposes no arbitrary-frame transport API.
These Python internal boundaries are not a sandbox against dishonest callers
or malicious subclass/constant replacement.

## Observation, bounds and evidence

The adapter first observes a one-second post-configuration quiet window.
Any queued/prewrite serial input, unconsumed USB input or unexpected USB OUT
suppresses submission. Only then is the exact frame submitted once. A short
write or exception leaves its accepted/uncertain accounting explicit and stops;
there is no suffix resend or fallback zero.

After complete submission, the tool observes for at most **three seconds**
within a 15-second operational budget. It continues observing after the first
candidate to retain/reject extra response data. Limits are 8192 serial RX bytes,
512 bytes per read, 4096 observation iterations, 256 decoded response events,
262144 bytes of adapter journal, 4096 bytes per binary USB payload and 1 MiB /
10000 target USB records. Cleanup has a separate five-second bound. Existing
USB recording uses a 20-second nominal window and **25-second hard limit from
recorder readiness**, preserving close and tail events. Bounds are cooperative/
post-call checks, not preemption of blocked kernel or filesystem operations.

`capture/serial/adapter.jsonl` retains every dequeued raw chunk before parsing/
USB correlation, exact attempted TX and accepted count, raw requested/returned
termios arrays, open/close and decoded response events. Full binary USB evidence
retains ingress bytes, OUT completion and line transitions. Timing uses the
existing conservative USB-completion-to-monotonic bridge with its clock-change
guard; queued tty input is not falsely assigned a dequeue-time lower bound.

Unlike getter collection, **no expected reply payload size is supplied**.
Payloads remain opaque. A frame with valid framing/CRC, matching command and
sequence, response field `80`, and conservative strictly-postsubmission,
pre-deadline ingress can be labeled `correlated_command_sequence_only`.
It still carries **`unverified_shape_and_semantics`**, with application
acknowledgment and physical stop both `not_established`. This is not the
getter's `matched_candidate` or a 134-byte telemetry interpretation.

Malformed/noise/partial input, prewrite/ambiguous timing, wrong command/sequence,
additional frames, non-80 status, deadlines, identity/settings changes, USB loss
or byte mismatch, recorder failure and incomplete evidence fail closed.
Non-80 status is retained as `uninterpreted_non80_status`, not assigned an
invented device error meaning. All events in an accepted read batch are
retained before reporting classification failure. Partial tails are finalized
on failure/close; prewrite data remains in the separate raw adapter/USB record.
No response yields explicit `response_not_observed`, not successful stop.

Root metadata records the complete observation report and exact attempt
accounting; a refusal before submission reports zero application submission
attempts. On success, the root status is only
`observation_complete_unverified`, pending parent/operator review. Later
unmatched USB input/OUT, dropped/queued events, tail/close or sealing failure
invalidates the outer outcome even if an earlier frame correlated.
The private manifest covers the final files. No raw capture is published.

## Physical boundary and next hold

Even a valid correlated reply cannot show that a velocity target was applied,
which physical wheel a channel controls, whether zero means brake/coast/hold,
whether stored motor output changed, or whether actuator energy is removed.
No physical motor stop can be verified with the motor power and signals
isolated. Existing telemetry's raw reverse PWM `100` and zero velocities remain
insufficient evidence.

The operator has separately described an accessible TekPower HY1803D external
supply at 12 V DC / 1 A current limit as the sole motor-energy source, with
physical off/disconnect effective even while USB is connected. These are
operator declarations, not measurements or authorization to energize. An
appropriate existing cutoff can support a reviewed motor-only abort method;
new disconnect hardware is not inherently required. Robot elevation on blocks
is not measured restraint or stopping performance. Servo energization is not
authorized.

After this one observation, close all handles and return the actual raw result
for review. No additional command, wiring/power change, powered trial, issue
closure or relaxed safety gate follows automatically.
