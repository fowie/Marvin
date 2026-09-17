# Fixed powered Motor-L stop characterization

**Software preparation is not live authorization.** Only the Motor power test
operator session may run this entrypoint after reviewing the committed SHA.
Development and CI must not open the robot tty, USB monitor, sysfs identity, or
power controls.

The immutable legacy S/E transcript is:

| Sequence | Request |
|---|---|
| 3072 Motor L raw +1 / Motor R zero | `53000c11000400010000009bfd45` |
| 3073 both zero | `53010c1100040000000000cbc445` |
| 3074 ReadRawData | `53020c0000000072e645` |

The offline decoder recomputes each CRC and verifies command, sequence, request
field, and payload. Raw `+1` is only the smallest positive protocol integer; it
is not a known speed, duty cycle, torque, or physical safety bound.

The runner owns one raw 57600/8N1 low-line descriptor. It persists pre-start
intent, emits a flushed `START`, schedules zero before the start syscall, submits
the start once, and—only after a full start write to that same owned writable
descriptor—attempts the fixed zero once at a nominal 250 ms. It performs no RX,
decode, evidence journaling, identity lookup, pacing, or terminal output between
the two syscalls. `STOP_ATTEMPT` is emitted after the zero syscall returns.
Ordinary response, journal, pacing, capture, and deadline faults cannot prevent
that attempt. A partial/uncertain start or unavailable/changed/non-writable owned
descriptor suppresses zero, forbids reopen, and emits `CUT_POWER_REQUIRED`.

The accepted zero-start lateness is at most 50 ms. This cooperative host
criterion is not a hard-real-time pulse or physical-output-duration guarantee;
host scheduling, a blocked syscall, controller behavior, and a host crash remain
outside it. The operator must remain at the HY1803D cutoff throughout.

Only after a full zero write, both clean correlated empty setter responses, and
acceptable timing does the runner submit the final getter. Replies, CRC validity,
reported raw zero, successful writes, and USB evidence do not prove physical
motion or stop. Operator-observed motion and operator-observed stop after zero
remain distinct external observations. Independent HY1803D cutoff stop
verification is a later separately authorized trial, never an automatic suffix.

The operational budget is 5 seconds plus 5 seconds cleanup. USB recording is 10
seconds nominal and 15 seconds hard, with full binary target evidence, raw serial
journal, hashes, no retries, no reconnect, and no resume. These are software and
evidence budgets, not a power dwell bound.

All declarations are literal and mutually exclusive with isolated-zero,
motor-power-OFF preparation, getter-only powered observation, and encoder-only
observation:

```text
--powered-left-stop-characterization
--motor-left-connected
--motor-right-disconnected
--both-encoder-feedback-connected
--servos-isolated
--robot-secured-on-blocks
--authorize-unvalidated-left-one-and-zero
--operator-at-external-cutoff
--unprivileged-usbmon
```

Offline review:

```sh
python3 -B -m tools.marvin_legacy_powered_left_stop
```

## Left-command / physical Motor-R mapping scope

The same runner also accepts the separate
`--powered-left-command-right-connected` cabling/channel mapping scope.
Its **command bytes are unchanged**: left velocity word `+1`, right velocity
word `0`, then both zero, then the gated getter. The physical setup is instead
Motor L **power disconnected**, Motor R **power connected**, both encoder
harnesses connected, all servos isolated, robot secured on blocks, and the
operator continuously at the HY1803D cutoff. The command-word names do not
establish which physical motor responds.

For this scope, replace the three declarations
`--powered-left-stop-characterization --motor-left-connected --motor-right-disconnected`
with
`--powered-left-command-right-connected --motor-left-disconnected --motor-right-connected`.
Keep every other declaration, including
`--authorize-unvalidated-left-one-and-zero`: it truthfully authorizes the
unchanged **left command word**, not a claim about physical wiring.
The scopes cannot be mixed. Evidence records the separate scope and
`MOTOR_L_DISCONNECTED_MOTOR_R_CONNECTED` load declaration; declarations are
operator statements, not software-verified wiring or physical-stop proof.
All existing timing, response gates, cutoff, no-retry, and no-reconnect rules
above apply unchanged. Software preparation does not authorize a live trial.

Offline review of this complete alternate scope (no `--run`):

```sh
python3 -B -m tools.marvin_legacy_powered_left_stop \
  --powered-left-command-right-connected \
  --motor-left-disconnected --motor-right-connected \
  --both-encoder-feedback-connected --servos-isolated \
  --robot-secured-on-blocks --authorize-unvalidated-left-one-and-zero \
  --operator-at-external-cutoff --unprivileged-usbmon
```

## Separately authorized original Motor-L trial

Future operator-only invocation:

```bash
python3 -B -m tools.marvin_legacy_powered_left_stop \
  --powered-left-stop-characterization \
  --motor-left-connected --motor-right-disconnected \
  --both-encoder-feedback-connected --servos-isolated \
  --robot-secured-on-blocks \
  --authorize-unvalidated-left-one-and-zero \
  --operator-at-external-cutoff --unprivileged-usbmon \
  --run --expected-physical-port "$REVIEWED_PHYSICAL_PORT" \
  --output "$NEW_CAPTURE_DIR" \
  >"$FINAL_JSON" 2> >(tee "$PHASE_LOG" >&2)
```
