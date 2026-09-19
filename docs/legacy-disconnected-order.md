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
response before the next scheduled request. The initial zero requires its
previously proven raw `80`; nonzero train responses retain raw `80` or `82`
opaquely and permit only the predeclared next request. Timeout, partial or
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

### First live attempt: timestamp gate stop after one nonzero

The first separately authorized attempt stopped safely before the repeated
train, but the initial console interpretation that no nonzero was sent was
wrong. The sealed serial and usbmon evidence proves exactly three successful
14-byte OUT completions / 42 accepted TX bytes / zero uncertain TX bytes:

| Sequence | Request | Correlated response |
|---:|---|---|
| 3285 | initial zero | CRC-valid empty command `11`, raw `80` |
| 3286 | left `+1000`, right zero | CRC-valid empty command `11`, raw `82` |
| 3306 | mandatory cleanup zero | CRC-valid empty command `11`, raw `80` |

No request from sequence 3287 through 3305 was transmitted. Serial accounting
was 30 RX bytes. Usbmon retained all 42 OUT payload bytes and all 30 IN payload
bytes, with three successful bulk-OUT and three successful payload-bearing
bulk-IN completions; it reported no unmatched transfers or uncaptured payload
bytes. Both manifests verified every listed artifact. The outer manifest file
SHA-256 is
`9299dcd56acf6568c278e0f0863074f351fbb466828ea43679873a19c572bbdd`;
the nested capture manifest file SHA-256 is
`3711f86c538bf4faa4ecdfa7246f55c20a4c33906ecaac9bc289998371f91d76`.
The adapter journal SHA-256 is
`490f89269de63e918aedc75a72c476dde7152bea352ce998fd6c32b752825077`.

The initial-zero response was clean. The sequence-3286 response began in usbmon
about 0.150 ms after the `write_returned` journal event, but the runner assigned
the response evidence `submitted_at` timestamp only after the write helper and
accounting returned. That post-write software timestamp could therefore be
later than a fast real response and mislabeled this uniquely correlated frame
`prewrite_or_ambiguous`. The fault path then sent the one fixed cleanup zero,
whose response was clean raw `80`, and stopped. The usb recorder metadata is
sealed with failed/signal status because the coordinator stopped it after the
software fault; this is not a sealing gap.

The fix records the matching sequence and monotonic boundary immediately before
the single `os.write` syscall and uses that boundary for response evidence.
Sequence, command, CRC, empty payload, raw-status, extra-frame, serial/usbmon
correlation, identity, timing, no-retry, and cleanup gates remain unchanged. A
response whose USB start is at or before that pre-syscall boundary still fails
as ambiguous.

The operator reported the scope result as **uncertain: scope was not triggered**.
This must not be recorded as no output. The operator cut power after the stop.
The single `+1000` application meaning and physical output remain unknown.

### Two completed corrected left-word trains

Two later separately authorized runs completed the full fixed left-word
transcript after the timestamp fix. In each run:

- all 22 writes completed: 308 accepted TX bytes and zero uncertain TX bytes;
- sequence 3285 initial zero returned raw `80`;
- all 20 sequence 3286 through 3305 left-word `+1000` stimuli returned raw `82`;
- sequence 3306 mandatory zero cleanup completed and returned raw `80`;
- serial RX was 220 bytes; usbmon captured 308 OUT and 220 IN payload bytes;
- usbmon reported no unmatched transfers, submission errors, evictions, or
  uncaptured payload bytes; and both manifests verified every listed artifact.

The first completed run had maximum scheduled-write lateness 1.465 ms and mean
lateness 0.319 ms. Its outer manifest file SHA-256 is
`14d8cd43ddc8debadefd7985405eefd472abc7ac827e7afc31d70de8e43e237e`;
its nested manifest file SHA-256 is
`8037f929ea71ac73d42d6983e6cd88cf8f8c583d711c50fb77d23f8ec0edc769`;
and its adapter journal SHA-256 is
`9a163d4fd13efa7ab1d2bdba2397459a98d34d90c9ecea3c270a54eeb32548d7`.
The operator reported **uncertain: scope did not trigger**.

The requested repeat had maximum lateness 2.445 ms and mean lateness 0.568 ms.
Its outer manifest file SHA-256 is
`29a3c537979d8e21dfff75e820ccae96543189595a5bc4520d3facf419e580a9`;
its nested manifest file SHA-256 is
`de304e79e094a4eaa452fab54943718eb60230a9e6a6ea9e771760aad21ac3b3`;
and its adapter journal SHA-256 is
`b1f8cfaf49ef844a315bd696df17bbcfbcabd972dca8dbbdd98619879879cfea`.
With the differential/isolated scope free-running at 50 ms/div across the
physical plug printed `Motor L`, the operator observed **no output change**.

These are distinct operator observations. The first is not evidence of no
output; the second is not a calibration or proof that PCTestApp's source name
`leftVel` maps to the physical `Motor L` label. Raw `82` remains opaque, and no
application acknowledgment, output topology, motion, or physical stop is
inferred.

## Prepared mirrored PCTestApp `rightVel` train

PCTestApp serializes `leftVel` into payload bytes 0–1 and `rightVel` into bytes
2–3 at `Form1.cs:891-895`, `987-990`, and `1013-1017`. The mirrored diagnostic
changes only the source-named second signed word to `+1000`; it does not claim
that this field drives a particular robot side. The separately reviewed
operator setup places the differential/isolated DC-coupled scope across the
disconnected physical plug printed `Motor R`.

| Sequence | Payload | Exact frame |
|---:|---|---|
| 3307 | initial zero | `53eb0c1100040000000000efb245` |
| 3308 | `leftVel=0`, `rightVel=+1000` | `53ec0c110004000000e803506945` |
| 3309 | `leftVel=0`, `rightVel=+1000` | `53ed0c110004000000e80301ac45` |
| 3310 | `leftVel=0`, `rightVel=+1000` | `53ee0c110004000000e803f1a345` |
| 3311 | `leftVel=0`, `rightVel=+1000` | `53ef0c110004000000e803a06645` |
| 3312 | `leftVel=0`, `rightVel=+1000` | `53f00c110004000000e80391c345` |
| 3313 | `leftVel=0`, `rightVel=+1000` | `53f10c110004000000e803c00645` |
| 3314 | `leftVel=0`, `rightVel=+1000` | `53f20c110004000000e803300945` |
| 3315 | `leftVel=0`, `rightVel=+1000` | `53f30c110004000000e80361cc45` |
| 3316 | `leftVel=0`, `rightVel=+1000` | `53f40c110004000000e803d01645` |
| 3317 | `leftVel=0`, `rightVel=+1000` | `53f50c110004000000e80381d345` |
| 3318 | `leftVel=0`, `rightVel=+1000` | `53f60c110004000000e80371dc45` |
| 3319 | `leftVel=0`, `rightVel=+1000` | `53f70c110004000000e803201945` |
| 3320 | `leftVel=0`, `rightVel=+1000` | `53f80c110004000000e803102945` |
| 3321 | `leftVel=0`, `rightVel=+1000` | `53f90c110004000000e80341ec45` |
| 3322 | `leftVel=0`, `rightVel=+1000` | `53fa0c110004000000e803b1e345` |
| 3323 | `leftVel=0`, `rightVel=+1000` | `53fb0c110004000000e803e02645` |
| 3324 | `leftVel=0`, `rightVel=+1000` | `53fc0c110004000000e80351fc45` |
| 3325 | `leftVel=0`, `rightVel=+1000` | `53fd0c110004000000e803003945` |
| 3326 | `leftVel=0`, `rightVel=+1000` | `53fe0c110004000000e803f03645` |
| 3327 | `leftVel=0`, `rightVel=+1000` | `53ff0c110004000000e803a1f345` |
| 3328 | mandatory all-zero cleanup | `53000d1100040000000000979145` |

The transcript is 22 writes / 308 TX bytes, with the same 220 expected-response
bytes and 8192-byte RX bound. Its SHA-256 is
`07d2dec07a7db89cc859ac16f4aeb16390361076385aafa41af1fb563c43ec5d`.
Cadence, response, strict pre-`os.write` time boundary, fault, cleanup,
no-retry/reconnect, and evidence rules are identical to the left-word profile.
There is no side/value/count/cadence/duration/sequence CLI.

Exact offline dry-run review:

```sh
python3 -B -m tools.marvin_legacy_velocity_train \
  --disconnected-load-right-plus-1000-velocity-train \
  --authorize-unvalidated-right-plus-1000-velocity-train \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

This is software readiness, not live authorization. A separately authorized
run requires the same disconnected-load setup and cutoff operator. The scope
must be appropriately rated, isolated/differential, DC-coupled, confirmed
free-running or correctly triggered before the run, and connected across the
disconnected physical plug printed `Motor R`; this does not establish the
source-field-to-plug mapping.

### Completed mirrored `rightVel` train

The separately authorized mirrored run completed all 22 writes: 308 accepted
TX bytes, zero uncertain TX bytes, and 220 serial RX bytes. Sequence 3307
initial zero returned raw `80`; all 20 sequence 3308 through 3327 source-named
`rightVel=+1000` stimuli returned raw `82`; and sequence 3328 mandatory zero
cleanup completed and returned raw `80`. Usbmon captured all 308 OUT and 220 IN
payload bytes and reported no unmatched transfers, submission errors, evictions,
or uncaptured payload bytes. Both manifests verified every listed artifact.

Maximum scheduled-write lateness was 3.555 ms and mean lateness was 0.577 ms.
The outer manifest file SHA-256 is
`21662434bc94d1d52197715091d8a7fe7c4e94af3d93638fe1a57f21a7da6c7e`;
the nested capture manifest file SHA-256 is
`5e16c73a6cdb71cbcf5a3ea30a510c473acd994505f2b1f9b4291fdf44254886`;
and the adapter journal SHA-256 is
`f13bceee624d40a17639c457a5733c7295de70f14a5e2f99f14acf260ec964fa`.

With the differential/isolated scope free-running at 50 ms/div across the
disconnected physical plug printed `Motor R`, the operator observed **no
visible output change**. This establishes only the tested source-word/physical-
plug pairing and observation. It does not establish robot-side naming,
source-field-to-plug topology, application acknowledgment, absence of pulses
outside instrument visibility, or physical stop. Raw `82` remains opaque.

## Ranked next motor-output discriminator

The matching PCTestApp source supports the following facts:

- `SetMotorVelocity` parses two signed `Int16` values and serializes source-named
  `leftVel` then `rightVel`; it provides no percent conversion
  (`Form1.cs:873-897,995-1019`). The random path chooses each word from
  `-1000..999` at an explicit 50 ms timer interval
  (`Form1.cs:978-993`; `Form1.Designer.cs:2247-2250`). The fixed timer has no
  explicit interval or default textbox values in the supplied source.
- `SetRawMotorPWM` parses four `UInt16` textboxes and serializes four LE words,
  admitting `0..65535` by parser type (`Form1.cs:649-674,1032-1057`). Its
  textboxes have no supplied defaults, examples, labels tying textbox order to
  a motor/direction, or source-used nonzero values. Its repeating timer has no
  explicit interval (`Form1.Designer.cs:2252-2258`).
- Heartbeat display code names four reported fields left-forward, left-reverse,
  right-forward, and right-reverse (`Form1.cs:512-515`), but the host source
  does not bind those reported fields to the four command-`0B` setter words.
  Heartbeat handling only displays telemetry; startup does not enable motors or
  issue heartbeat, power, reset, mode, or unlock commands.
- Encoder position, velocity, acceleration, and current are displayed beside
  the PWM fields (`Form1.cs:501-515`), so closed-loop control is plausible, but
  the missing legacy handler means it is inference only. The host source does
  not state an encoder prerequisite or prove that command `11` can remain at
  zero output because of control state.

Ranked reversible disconnected-load options:

1. **Conditional command `0B SetRawMotorPWM`: strongest conceptual
   discriminator, presently blocked.** It would bypass the source-named
   velocity request and more directly test a bridge/PWM path. A rigorous future
   plan would require the proven command-`0A` eight-zero getter baseline, one
   source-mapped channel and source-backed nonzero value, exact all-zero cleanup
   once any setter may reach its syscall, then conditional getter verification.
   No such nonzero value or setter-channel mapping exists in the supplied
   legacy source. Raw `1` is merely the smallest parser-admitted integer and may
   be below an effective PWM threshold; any larger value is arbitrary and
   higher consequence. The setter response shape is also unproved. Therefore
   no raw-PWM runner or frame is prepared.
2. **Larger command `11` velocity: not recommended.** Both source-named words
   have now received complete `+1000` 20 Hz trains with raw `82` and no visible
   output change on the separately selected physical plugs. A larger value
   remains inside captured `-3500..3500` configuration words but does not bypass
   a possible velocity-control gate and adds consequence without resolving
   whether those configuration words are active.
3. **Getter-only repetition: safe but not discriminating.** Command `0A` has
   already returned eight zero bytes and command `00` exposes raw velocity/PWM
   telemetry. Without the missing field/handler mapping, another read cannot
   distinguish bridge disable, closed-loop state, command rejection, or wiring.

The next safe action is offline recovery of the installed legacy command-`0B`
handler, its response enum, PWM units/effective range, and setter-word mapping,
or a reviewed original-host capture showing a known nonzero command-`0B`
payload. Power-state, heartbeat-control, reset, configuration, identity, and
flash commands remain excluded; no inverse/readback basis was found.

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
