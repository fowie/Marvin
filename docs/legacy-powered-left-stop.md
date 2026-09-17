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

## Recorded outcomes and hardware hold (2026-09-17 UTC)

Three separately released bounded trials used the unchanged left-word `+1`,
right-word `0` start and planned both-zero frame: two with physical Motor L
connected alone, then one with physical Motor R connected alone. The retained
parent-session review reports legacy response field **`82` for every start**
and **`80` for every zero**; the operator reported no motion in either setup.
The final getter was suppressed by the non-`80` response gate. These were
separate operator trials, not automatic retries.

The installed meaning of `82` remains **unknown**. Do not decode it using the
successor EFBE response enum/convention or call it a specific safety inhibit.
The [legacy zero evidence policy](legacy-zero.md#observation-bounds-and-evidence)
retains non-`80` status without assigning semantics. Repeated results do not
prove motor-channel mapping or physical stopping; command value, sequence and
request order were not independently varied.

A subsequent stationary, both-motor-POWER-plugs-disconnected observation reused
the [fixed encoder getter scope](legacy-live.md), with both encoder harnesses
connected and servos isolated. Offline inspection of its retained final JSON
and raw response frames found:

| Observation | Recorded result |
|---|---|
| Replies | 20/20, sequences 2560-2579, command `00`, response `80`, 134-byte payloads, valid CRCs |
| Accounting | 200 application TX / 2880 RX bytes; zero uncertain TX bytes |
| Motor fields | All 12 position, velocity, acceleration, current and PWM fields zero in every sample |
| Raw flags | `0c7e` in 8 samples, `1c7e` in 12; only changing mask `1000`, unlabeled |
| Raw cliff ranges 1-5 | 436-459; 419-441; 12-154; 83-399; 151-299 |
| Raw battery voltage/current | 450-451 / 0 |
| Raw rail reports 5V/12V/9V/19V | 863-866 / 854-855 / 834-836 / 547-548 |

These are reviewed derived observations, not published raw captures or
authenticated physical proof. The final JSON matched capture metadata and
decoded fields matched the retained frames; this review did not independently
replay USB evidence or rehash the complete manifest. Both flag values occurred
with cliff 3 below and above raw 80. Neither this co-occurrence nor later-source
configuration names `cliffStopThreshold=80` / `cliffStopHysteresis=32`
establish the installed comparison, latch behavior or a cause of `82`.
See [configuration limitations](marvin-configuration.json) and
`tools/marvin_legacy_telemetry.py` for source-derived offsets without flag-bit
semantics. Stable rail reports are not measured voltages.

Earlier [manual encoder observations](legacy-live.md#subsequent-robot-right-and-robot-left-feedback-observations)
changed reported PWM with getter-only traffic and disconnected loads; earlier
[post-zero observations](legacy-zero.md#separately-released-post-zero-readrawdata-batch)
also had uncontrolled state/disconnect differences. Current stationary zeros
do not establish an initialization failure, applied zero, or a disabled motor
driver. **Physical stop remains `not_established`.**

At this review, [#9](https://github.com/fowie/Marvin/issues/9) is closed and
[#11](https://github.com/fowie/Marvin/issues/11) remains open with its acceptance
boxes checked. Those tracker states are not raw experimental evidence; this
snapshot does not itself establish #11's measured stop-from-motion, host-exit,
USB-loss or watchdog behavior, nor assess separate owner records not supplied
here.

**No further live command is justified by these observations.** The next desk
action is to obtain and review the original legacy response-code definition
and command `11` handler/precondition checks, subject to the
[private-source provenance limits](provenance.md). Do not raise the setpoint,
repeat the pulse, send guessed heartbeat/power/reset commands, or clear/bypass
interlocks. The invocation examples above are not authorization to resume.
