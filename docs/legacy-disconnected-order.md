# Fixed disconnected-load value/order diagnostics

**This is software readiness, not live authorization.** Development and CI
must not open the robot tty, USB monitor, sysfs identity, or any hardware
interface. The operator-confirmed setup for any separately reviewed future run
requires both motor POWER plugs disconnected, both encoder feedback harnesses
connected, servos isolated, the robot secured on blocks, and the operator at
the HY1803D cutoff. These declarations do not prove actuator/signal isolation
or physical stop.

The immutable legacy S/E transcript is:

| Sequence | Request | Frame |
|---|---|---|
| 3072 | command `11`, both words zero | `53000c11000400000000009a0145` |
| 3073 | command `11`, left word raw `+1`, right word zero | `53010c1100040001000000ca3845` |
| 3074 | command `11`, both words zero cleanup | `53020c11000400000000003bcb45` |
| 3075 | command `00` ReadRawData | `53030c00000000733745` |

The decoder verifies every frame and CRC before any separately authorized run.
The three setters are submitted once in immediate bounded succession, with no
delay, retry, reconnect, configurable value, duration, sequence, or count. Once
the `+1` write is fully accepted, the cleanup-zero syscall is the next operation
and is attempted exactly once. A write result does not establish controller
application, motor output, or physical stop.

The getter is suppressed unless all three setters receive one unique,
CRC-valid, empty-payload command-`11` response at the matching sequence. Setter
response fields are retained as raw hex; `82` is not decoded or treated as a
known error. This allows comparison of whether zero-first reports `80` and the
second-position nonzero reports `82` without assuming either status's semantics.
The final getter still requires its proven command-`00`, status-`80`, 134-byte
shape. All evidence retains `application_acknowledgment` and `physical_stop` as
`not_established`.

Exact offline dry-run review, without `--run`:

```sh
python3 -B -m tools.marvin_legacy_disconnected_order \
  --disconnected-load-zero-one-order-diagnostic \
  --authorize-unvalidated-zero-one-order-diagnostic \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

The printed plan is not permission to execute on hardware.

## Proven +1 outcome

A separately authorized disconnected-load run recorded sequence 3072 zero as
raw status `80`, sequence 3073 left `+1` as raw status `82`, and sequence 3074
cleanup zero as raw status `80`. The getter completed. Accounting recorded 52
TX bytes, 174 RX bytes, and zero uncertain TX bytes; all 13 `SHA256SUMS`
entries verified. No private artifact paths are published here.

This eliminates request order and sequence as explanations for the differing
setter status: the installed controller's nonzero rejection is value-dependent
under this fixed disconnected-load setup. Status `82` remains opaque and is not
decoded. The result does not establish physical stop.

## Fixed +1000 follow-up

The follow-up changes only sequence 3073's left signed word from `+1` to fixed
`+1000`:

| Sequence | Request | Frame |
|---|---|---|
| 3072 | command `11`, both words zero | `53000c11000400000000009a0145` |
| 3073 | command `11`, left word raw `+1000`, right word zero | `53010c11000400e80300000e6445` |
| 3074 | command `11`, both words zero cleanup | `53020c11000400000000003bcb45` |
| 3075 | command `00` ReadRawData | `53030c00000000733745` |

The source basis is the original PCTestApp `timer2_Tick` signed
`random.Next(-1000,1000)` test range, where `+1000` is the exclusive upper
boundary, and the captured installed configuration values `minVel=-3500` and
`maxVel=3500`. These facts bound the selected protocol integer; they do not
establish physical units, acceptance, motion, or safety.

Exact offline dry-run review, without `--run`:

```sh
python3 -B -m tools.marvin_legacy_disconnected_order \
  --disconnected-load-zero-plus-1000-order-diagnostic \
  --authorize-unvalidated-left-plus-1000-order-diagnostic \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

This profile has the same fixed immediate zero/nonzero/cleanup-zero submission,
raw response gating, getter shape, no-retry, no-reconnect, and evidence limits.
It has no arbitrary value, timing, sequence, count, or retry options. The
printed plan remains software readiness only, not live authorization.

### Proven +1000 outcome and setter hold

A separately authorized disconnected-load run recorded sequence 3072 zero as
raw status `80`, sequence 3073 left `+1000` as raw status `82`, and sequence
3074 cleanup zero as raw status `80`; the getter completed. Accounting recorded
52 TX bytes, 174 RX bytes, and zero uncertain TX bytes. All 13 `SHA256SUMS`
entries verified. The operator reported the DMM remained at 0.00 V on the
disconnected left output before the run and showed no change during it. This is
an operator observation, not synchronized electrical instrumentation or proof
of physical stop.

The repeated raw `82` establishes value-dependent nonzero rejection for both
tested positive values under this fixed disconnected-load setup; it does not
decode `82`, establish an acceptance threshold, or justify a larger setter.
No larger setter is authorized by these results.

## One-shot disconnected-load GetLog

The read-only follow-up sends only the live-confirmed empty GetLog command at
new fixed sequence 3076:

```text
53040c0c00000071d045
```

Request SHA-256:
`e617b3bc6fd672b38f4c8f963b4c03d7726ac953f48eaee1209cf26b57e41182`.
The request is admitted only once, with no retry, reconnect, follow-up,
setter, power-state, heartbeat, reset, or arbitrary command path. A successful
observation requires exactly command `0C`, status `80`, sequence 3076, a
32-byte payload, valid framing/CRC, and full serial/USB evidence accounting.
This is read-only with respect to actuator/power commands, but the existing
command map conservatively notes that reading a diagnostic log may consume or
advance internal log state. The payload remains raw evidence; prior identical
`taskSystem: after software setup` observations do not guarantee this result or
prove application state.

Exact offline dry-run review, without `--run`:

```sh
python3 -B -m tools.marvin_legacy_disconnected_get_log \
  --disconnected-load-get-log \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

This scope is literal and mutually exclusive with all setter and getter-only
encoder scopes. The printed plan is software readiness only, not live
authorization, physical isolation proof, or a physical-stop claim.

### Proven one-shot GetLog outcome and consolidated hold

The separately authorized one-shot used exact sequence 3076 request
`53040c0c00000071d045`. Its single reply matched command `0C`, raw status `80`,
and the required 32-byte payload; the payload decoded as ASCII
`taskSystem: after software setup`. Accounting recorded 10 TX bytes, 42 RX
bytes, and zero uncertain TX bytes. All 13 `SHA256SUMS` entries verified. The
message was identical to the prior reviewed logs and provided no reason for the
nonzero setter rejection.

Consolidating the bounded observations: left raw `+1` and `+1000` each returned
opaque status `82` at sequence 3073 when bracketed by sequence 3072 and 3074
zeros that each returned `80`. The DMM across the disconnected left output
remained at 0.00 V with no observed change during the `+1000` run. Both final
getters succeeded. These observations do not decode `82`, prove application
acknowledgment, or establish physical stop; `physical_stop` remains
`not_established`.

No larger setter, power-state write, heartbeat, reset, or other live command is
justified by this evidence.

## Prepared fixed 50 ms left `+1000` train

This separately scoped diagnostic tests only the source-backed cadence
hypothesis. `PCTestApp/Form1.cs:978-993` sends command `11` on each `timer2`
tick, `Form1.Designer.cs:2247-2250` fixes that timer at 50 ms, and
`Form1.cs:995-1025` provides a second explicit fixed-value repeating path. The
diagnostic therefore sends one initial zero, then 20 unique-sequence left
`+1000`/right-zero requests at 50 ms targets, then exactly one all-zero cleanup.
It does not add a separate reference pulse: the previous single-shot `+1000`
trial is the reference, and omitting another pulse avoids an extra nonzero
exposure and extra zero.

| Sequence | Payload | Exact frame |
|---:|---|---|
| 3285 | initial zero | `53d50c11000400000000008eb845` |
| 3286 | left `+1000`, right zero | `53d60c11000400e8030000bb1745` |
| 3287 | left `+1000`, right zero | `53d70c11000400e8030000ead245` |
| 3288 | left `+1000`, right zero | `53d80c11000400e8030000dae245` |
| 3289 | left `+1000`, right zero | `53d90c11000400e80300008b2745` |
| 3290 | left `+1000`, right zero | `53da0c11000400e80300007b2845` |
| 3291 | left `+1000`, right zero | `53db0c11000400e80300002aed45` |
| 3292 | left `+1000`, right zero | `53dc0c11000400e80300009b3745` |
| 3293 | left `+1000`, right zero | `53dd0c11000400e8030000caf245` |
| 3294 | left `+1000`, right zero | `53de0c11000400e80300003afd45` |
| 3295 | left `+1000`, right zero | `53df0c11000400e80300006b3845` |
| 3296 | left `+1000`, right zero | `53e00c11000400e80300005bf745` |
| 3297 | left `+1000`, right zero | `53e10c11000400e80300000a3245` |
| 3298 | left `+1000`, right zero | `53e20c11000400e8030000fa3d45` |
| 3299 | left `+1000`, right zero | `53e30c11000400e8030000abf845` |
| 3300 | left `+1000`, right zero | `53e40c11000400e80300001a2245` |
| 3301 | left `+1000`, right zero | `53e50c11000400e80300004be745` |
| 3302 | left `+1000`, right zero | `53e60c11000400e8030000bbe845` |
| 3303 | left `+1000`, right zero | `53e70c11000400e8030000ea2d45` |
| 3304 | left `+1000`, right zero | `53e80c11000400e8030000da1d45` |
| 3305 | left `+1000`, right zero | `53e90c11000400e80300008bd845` |
| 3306 | mandatory all-zero cleanup | `53ea0c1100040000000000be7745` |

The immutable transcript is 22 writes / 308 TX bytes, with at most 220 expected
response bytes inside the 8192-byte RX bound. Its SHA-256 is
`15f8e1754674fbc866b93c812f4438c470db9c7652f94ca9515a1fe451ffd59b`.
Every request requires one unique correlated CRC-valid empty command-`11`
response before the next scheduled request. Raw `80` and `82` are both retained
opaquely and permit the predeclared sequence to continue. Timeout, partial or
uncertain TX, CRC/framing/correlation/extra-frame, identity, timing, evidence,
or interruption faults stop the train. Once any nonzero may have reached its
syscall, the fixed sequence-3306 cleanup syscall is attempted exactly once
before its journal event, including on those faults. There is no retry,
reconnect, negative value, larger magnitude, arbitrary value, count, cadence,
duration, or sequence option.

Exact offline dry-run review, without `--run`:

```sh
python3 -B -m tools.marvin_legacy_velocity_train \
  --disconnected-load-left-plus-1000-velocity-train \
  --authorize-unvalidated-left-plus-1000-velocity-train \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

This remains software readiness, not live authorization. Any later authorized
run requires both motor POWER plugs disconnected, servos isolated, both encoder
harnesses connected, robot secured, operator at the HY1803D cutoff, and
unprivileged usbmon. For the left-output observation, use appropriately rated
isolated differential measurement across the disconnected output, DC coupling,
and a timebase/trigger that can show 50 ms spacing across the approximately
one-second train. Probe category, differential/common-mode range, isolation,
and connection method require operator review for the actual instrument. The
software does not authorize connecting an earth-referenced ground clip to
either output terminal.

The operator prompt is to report separately whether the scope showed no output,
a single transient, repeated PWM/enable activity aligned to the 50 ms train, or
another raw waveform observation, including instrument settings. Software
completion and raw `80`/`82` responses do not establish physical output,
application acknowledgment, or stop.

## Proven fixed legacy getter survey

A separately authorized read-only survey used the same disconnected-load
physical setup and sent the six fixed requests documented in
[marvin-command-catalog.md](marvin-command-catalog.md). All six replies were
complete and CRC-valid, matched the requested sequence and command, and
returned raw status `80`:

| Sequence | Command | Raw payload |
|---:|---|---|
| 3083 | `0A GetRawMotorPWM` | 8 bytes: `0000000000000000` |
| 3084 | `10 GetMotorVelocity` | 16 zero bytes |
| 3085 | `17 GetLedState` | 18 bytes: `000000000000000000000000ff0000ff0000` |
| 3086 | `19 GetLedBlink` | 18 bytes: `0000000000000000000000000000002a0000` |
| 3087 | `1F GetSensorInfo` | 128 bytes: exact ascending `00..7f` |
| 3088 | `28 GetBatteryInfo` | 8 bytes: `fdff6f3f7c02ae00` |

Accounting recorded 6 writes, 60 TX bytes, 256 RX bytes, and zero uncertain TX
bytes. All 13 `SHA256SUMS` entries verified. No private artifact path is
published.

The legacy PCTestApp sends these named requests and prints their responses but
does not parse the reply payloads. The newer `DB9Cmds.xlsx` layouts do not apply
to the installed S/E generation. The `GetRawMotorPWM` bytes can be grouped as
four zero LE `uint16` words consistently with its request name, but even that
does not prove individual field semantics. All other payloads remain raw; in
particular, the ascending `GetSensorInfo` bytes must not inherit meaning from
the incompatible successor firmware.
