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
operator setup intended the differential/isolated DC-coupled scope for the
`Motor R`-labelled connector. The later connector correction below records
that the operator actually selected the robot-right-side connector printed
`Motor L`; the actual `Motor R`-labelled connector remains untested.

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
must be appropriately rated, isolated/differential, DC-coupled, and confirmed
free-running or correctly triggered before the run. The historical target was
described as physical `Motor R`; the later correction below controls the
evidence interpretation and does not establish source-field-to-plug mapping.

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

The earlier live instruction named physical plug `Motor R`, but the operator
later corrected the actual tested connector: they selected the connector on
the robot-right physical side, which is printed **`Motor L`** and controls the
left motor. The free-running differential/isolated scope at 50 ms/div showed
no visible output change on that `Motor L`-labelled connector. This is not
evidence about the actual `Motor R`-labelled connector. It also does not map
source `rightVel` to either connector or robot side, establish application
acknowledgment, exclude pulses outside instrument visibility, or prove physical
stop. Raw `82` remains opaque.

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

1. **Command `0B SetRawMotorPWM`: strongest conceptual discriminator; the raw-1
   and raw-1000 pilots completed below.** It bypasses the
   source-named velocity request and more directly tests a bridge/PWM path.
   No source-used nonzero value or setter-channel mapping exists. The fixed
   first pilot used raw `1`, only because it is the smallest representable
   nonzero `UInt16`; its null result remains inconclusive. The next fixed value
   is raw `1000`, matching the previously bounded numeric magnitude and only
   1.53% of the `UInt16` full scale. Units, effective threshold, and channel
   binding remain unknown. No repetition or other word is prepared.
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

### Completed experimental raw-PWM word-0/value-1 pilot

`tools/marvin_legacy_raw_pwm_pilot.py` supplied the **completed, deliberately
experimental** direct-output discriminator. It does not claim source-backed
units, an effective PWM minimum, duty-cycle meaning, channel binding, or
source-word-to-physical-plug mapping. It sends one nonzero setter only; the
PCTestApp source exposes a repeating command-`0B` timer but supplies no explicit
interval or nonzero example, so repetition would be speculative.

| Step | Sequence | Fixed operation | Exact request |
|---:|---:|---|---|
| 1 | 3329 | command `0A` baseline getter; require raw `80` and exactly eight zero payload bytes | `53010d0a0000004ccd45` |
| 2 | 3330 | command `0B`; four LE `UInt16` words `[1,0,0,0]` | `53020d0b0008000100000000000000a64f45` |
| 3 | 3331 | mandatory command `0B`; exact all-zero cleanup once after the setter may reach its syscall | `53030d0b0008000000000000000000674245` |
| 4 | 3332 | conditional command `0A` verification; only after a clean empty raw-`80` cleanup response; require eight zero bytes | `53040d0a0000004c9845` |

The concatenated transcript SHA-256 is
`6880718e5a54cf8a8225d3d2afc3cd4cc975f6c0bb6552a8de4161bf4a6b0df8`.
There are at most four writes / 56 TX bytes and 56 expected response bytes;
the application RX ceiling remains 8192 bytes. If cleanup is raw `82`, the
conditional getter is suppressed and the completed path is three writes / 46
TX bytes. Setter and cleanup accept only a unique correlated CRC-valid
same-command empty response with opaque raw `80` or `82`. Baseline and
verification require unique correlated CRC-valid command-`0A` raw `80` with the
exact eight-zero payload. Every response is tied to the matching immediate
pre-`os.write` monotonic boundary; prewrite/ambiguous, correlation, CRC,
framing, extra-frame, identity, USB evidence, partial/uncertain TX, timeout, or
interruption faults stop progression.

Once the nonzero setter may reach its syscall, exactly one zero cleanup syscall
is attempted on all fault paths. The cleanup syscall precedes its journal
event, so a journal failure cannot suppress the write. There is no retry,
reconnect, value/index/count/cadence option, or ad hoc follow-up. A clean
raw-`80` cleanup permits the fixed getter verification. Any other cleanup
status or verification fault leaves a state lock requiring separately recorded
physical scope-baseline confirmation and power-cycle acknowledgment before a
later live phase. Protocol status, scope observation, restoration attempt, and
operator acknowledgment remain separate evidence.

Offline dry run, which performs no device access:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot
```

Exact historical live invocation (completion is **not continuing authorization**):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word0-one-pilot \
  --authorize-unvalidated-raw-pwm-word0-one-pilot \
  --motor-power-plugs-disconnected \
  --servos-isolated \
  --both-encoder-feedback-connected \
  --robot-secured-on-blocks \
  --operator-at-external-cutoff \
  --unprivileged-usbmon \
  --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY \
  --run
```

The initial operator-selected target is the disconnected physical plug printed
`Motor L`; this does not claim word 0 maps to that plug or robot side. Use an
appropriately rated differential/isolated probe, DC coupling, and a confirmed
free-running display during the fixed three-second window. Do not connect an
earth-referenced probe ground to an unverified bridge node. No waveform is
inconclusive because raw `1` may be below the effective PWM minimum. Any
correlated waveform would establish a lower-level output path for the tested
pairing, not channel topology, calibrated duty, application acknowledgment, or
physical safety.

If the sealed run remains software-unverified, the offline acknowledgment is:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --acknowledge-restoration \
  --evidence SEALED_EVIDENCE_DIRECTORY \
  --physical-output-baseline-confirmed \
  --power-cycle-confirmed
```

This acknowledgment performs no hardware access and is valid only after the
operator has separately confirmed both facts.

#### Two completed raw-1 runs

Both separately authorized runs completed with the same exact accounting:
four fully accepted writes / 56 TX bytes, zero uncertain TX bytes, and 56
serial RX bytes. Sequence 3329 returned raw `80` with exactly eight zero bytes;
the sequence-3330 setter returned opaque raw `82` with no payload; sequence
3331 all-zero cleanup returned raw `80` with no payload; and conditional
sequence 3332 returned raw `80` with exactly eight zero bytes. Cleanup was
attempted once and the zero baseline was getter-reverified. This is protocol
store/readback evidence, not a decoded application acknowledgment or physical
stop.

Usbmon recorded four successful bulk OUT completions / 56 captured and
completed OUT bytes and four payload-bearing successful bulk IN completions /
56 captured and completed IN bytes in each run. There were zero unmatched
completions, unmatched submission errors, endpoint mismatches, evictions,
pending retained transfers, or uncaptured payload bytes. Both outer and nested
manifests verified every listed artifact.

| Run | Outer manifest | Nested manifest | Adapter journal | Binary usbmon |
|---|---|---|---|---|
| First | `3cd5d838d4b67500124e4fe126d73ea8cd989fe41d45e05195979bdde3da76fa` | `90d68d1886b92488bf31c3d908d0399c6ccff7329aac3f3fee1749d9eee0f522` | `5fcace9a6bc50547b87a7fcabade13a40c01fbb56da3a8fcb5a077baecaeec5b` | `3e751237ed945a0a343fedf37b01514ef262a80ca8367664b894fa11d6c70200` |
| Repeat | `00edc54c095ba6f29f6166cada745df2d5d997d4cf843cce3b216f62e748b519` | `54370b8741527a16acf354d2eba4ce1ddf3b4bc8ae4bba2c6160fc23e1d01f5c` | `1cf27b62c313e6bf3224eb5ebe3f215036bf49a6daaaf0ddb5ca218eb2c89d91` | `c7b90dc29d07e0adac470f5a3f5448f02cb03268d04807a4d114187d2155a738` |

The first scope observation is **uncertain** because the instrument was not
correctly configured; it must not be recorded as no output. The operator then
power-cycled. For the repeat, the differential/isolated scope was armed at
1 V/div, 500 us/div, and approximately 1.5 V trigger across the disconnected
connector printed `Motor L`; it did not trigger and showed no visible waveform.
The operator then powered the robot OFF. A null raw-1 result remains
inconclusive because its effective PWM significance is unknown.

Connector naming is now operator-confirmed separately: the controller
connector physically on the **robot-right side** is printed `Motor L` and
controls the **left motor**. Keep these three descriptions distinct from the
PCTestApp source fields and raw-PWM word numbers. No mapping between command
`0B` word 0 and that connector follows from the null observation.

### Completed fixed raw-PWM word-0/value-1000 escalation

The next profile reuses the same runner and changes only the immutable scope,
sequence block, and word-0 value. It still sends one setter only and observes
the same disconnected physical connector printed `Motor L` for three seconds.
Raw `1000` is 1.53% of the `UInt16` full scale and matches the already bounded
numeric magnitude used in the velocity diagnostics; that does **not** establish
PWM units, duty cycle, source use, effective minimum, or channel mapping.

| Step | Sequence | Fixed operation | Exact request |
|---:|---:|---|---|
| 1 | 3333 | command `0A` baseline getter; require raw `80` and exactly eight zero payload bytes | `53050d0a0000004d4945` |
| 2 | 3334 | command `0B`; four LE `UInt16` words `[1000,0,0,0]` | `53060d0b000800e8030000000000005ea945` |
| 3 | 3335 | mandatory command `0B`; exact all-zero cleanup once after the setter may reach its syscall | `53070d0b0008000000000000000000628645` |
| 4 | 3336 | conditional command `0A` verification after clean empty raw-`80` cleanup; require eight zero bytes | `53080d0a0000004c5445` |

The concatenated transcript SHA-256 is
`f9fb5e2ae27dbbeb0c8c57b3d9bb1663ca2d34fa16f2c2ae7966e505310e012b`.
Bounds remain four writes / 56 TX bytes / 56 expected response bytes and 8192
serial RX bytes, or three writes / 46 TX bytes when verification is suppressed.
All response, pre-`os.write`, cleanup, state-lock, no-retry, no-reconnect, and
evidence rules above are unchanged.

Exact offline dry run:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word0-1000-pilot \
  --authorize-unvalidated-raw-pwm-word0-1000-pilot \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

Exact historical live invocation (completion is **not continuing authorization**):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word0-1000-pilot \
  --authorize-unvalidated-raw-pwm-word0-1000-pilot \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon \
  --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY \
  --run
```

A waveform establishes a lower-level path only for raw word 0/value 1000 and
the tested `Motor L`-labelled connector. It does not establish calibrated duty,
motor motion, the opposite output polarity/direction, complete bridge health,
or a source-word/channel mapping beyond this tested pairing.

#### Three completed raw-1000 runs

All three separately authorized runs completed with identical application
accounting and response shapes: four fully accepted writes / 56 TX bytes, zero
uncertain TX bytes, and 56 serial RX bytes. Sequence 3333 returned raw `80`
with exactly eight zero bytes; the sequence-3334 `[1000,0,0,0]` setter returned
opaque raw `82` with no payload; sequence 3335 all-zero cleanup returned raw
`80` with no payload; and conditional sequence 3336 returned raw `80` with
exactly eight zero bytes. Cleanup was attempted once and the zero baseline was
getter-reverified after every physical observation. The robot was powered OFF
after the third run.

Each usbmon trace recorded four successful bulk OUT completions / 56 captured
and completed OUT bytes and four payload-bearing successful bulk IN completions
/ 56 captured and completed IN bytes. Each reported zero unmatched
completions, unmatched submission errors, endpoint mismatches, evictions,
retained pending transfers, and uncaptured payload bytes. Every outer and
nested manifest entry verified.

| Run | Outer manifest | Nested manifest | Adapter journal | Binary usbmon |
|---|---|---|---|---|
| First | `6d1376fd3e557fe7430bed12a3c4ea23702ba1802595e05440bb614c9e1dc412` | `b05719b5de6b90e95620b946924936091554064e690a1a818d00bdbdcaf88722` | `857be73085abf3d4a3831253ad89feba23d4cd8c1dccfab32c0219c412074110` | `b34901f0c836664ebe9f7f8c380c3fff3bf82e634ed3a64fa80681de2faa5ca7` |
| Reversed-polarity capture | `5c978373fe8c01e79b1964b61bd1f9e160ba4a05dbeec59b313f4358c06d0251` | `be4fcdfaed37f1726fb33c4449aed83001bb0cafb28359764fdc34b9483b03bb` | `1f3cffbf431a4e224ef28f5b18ae01e614fd0cc10976f5764f133a8d23494aad` | `ffbbed752974121d77a1ab17a1f4acadc8b85bed85e5a8578c35b4e87e79dce7` |
| Corrected-polarity capture | `ba0ad9fbd41cfac7e0bf934b5a500c6fac4ba7e030a8e906da11052ab432ddcf` | `70f0d9c488b1337e63a2870571af49189545862311f05f806bacebe65316300f` | `fc20ba9f24354e420bec08d329e6e0eba81ab2c08615bc29ea70214f28886d0c` | `e3703a5990c313a2f547c77699b68f12f5844e5646cd32a4bd9c9fa5ece4aaf3` |

Physical observations, kept separate from opaque protocol status:

1. The first auto-mode observation saw something but did not retain a usable
   capture; record it as **uncertain**, not a waveform measurement.
2. The second single-shot run captured a clear waveform with reversed probe
   polarity, so it displayed negative. The user powered OFF before changing
   the probe.
3. After fresh safety confirmation and corrected polarity, the third
   single-shot run captured a clear positive waveform on the disconnected
   robot-right-side connector printed `Motor L`, which the operator identifies
   as controlling the left motor.

The third display used CH1 DC coupling, a 10x probe, and 2 V/div. The operator
reported `Vpp=2.11 V`, `Vavg=+509 mV`, `Vrms=2.24 V`, and a 37 us pulse width.
An overview at 200 us/div showed irregular pulse spacing and amplitudes and a
cursor delta of 588 us / reciprocal 1.70 kHz; a 50 us/div detail showed the
37 us width and reciprocal 27.0 kHz. These are raw scope observations, not
calibration. The `Vrms > Vpp` inconsistency and window/trigger dependence make
the automatic voltage measurements uncertain. The 27.0 kHz display is the
reciprocal of pulse width, **not an established repetition or PWM frequency**;
the operator could not obtain a stable actual repetition rate.

This result strongly localizes the prior command-`11` velocity null above or
outside the direct-output path: raw command-`0B` word 0/value 1000 produced a
physical waveform where the velocity trains did not show one. It does not
decode raw `82`, establish why velocity was rejected or ineffective, prove a
stable PWM period/duty relationship, demonstrate motor motion, or establish
full bridge health.

#### Ranked next reversible tests

1. **Prepared below: one fixed second word-0 value with the same disconnected
   connector and exact zero lifecycle.** Raw `2000`
   would preserve direction/word/connector and change only magnitude. Capturing
   pulse width and spacing with fixed manual scope settings could test whether
   width scales with the raw value. It still needs a separately reviewed fixed
   profile, fresh authorization, exact-zero baseline, one setter, one cleanup,
   conditional zero getter, and the same state lock.
2. **Alternative: repeat raw `1000` only to stabilize period acquisition.**
   This has lower software consequence but adds little value-to-width
   calibration evidence because the commanded value does not change. It is
   justified only with a predeclared manual timebase/trigger measurement plan,
   not automatic reciprocal readouts.
3. **Later and higher consequence: carefully bounded motor-connected proof.**
   This would test whether the demonstrated disconnected waveform produces
   controlled torque/motion, but requires a separate physical test plan,
   current/energy bounds, one-motor topology, motion/stop acceptance criteria,
   external cutoff, and reviewed cleanup. The present evidence does not
   authorize or prepare it.

Do not escalate first to another raw-PWM word, reverse polarity, repeated
setters, power/heartbeat/reset/configuration writes, or a larger connected-load
command. Those change more than the value-to-pulse-width question requires.

### Completed fixed raw-PWM word-0/value-2000 comparison

The existing runner now includes one immutable raw-2000 profile. It changes
only word 0 and the sequence block; word index, physical connector, one-setter
lifecycle, three-second observation window, all-zero cleanup, conditional
getter, response gates, state lock, and evidence bounds remain identical.
Raw `2000` is 3.05% of the `UInt16` full scale. That percentage is only numeric
scale context, not duty-cycle or physical-unit calibration.

| Step | Sequence | Fixed operation | Exact request |
|---:|---:|---|---|
| 1 | 3337 | command `0A` baseline getter; require raw `80` and exactly eight zero payload bytes | `53090d0a0000004d8545` |
| 2 | 3338 | command `0B`; four LE `UInt16` words `[2000,0,0,0]` | `530a0d0b000800d00700000000000015d745` |
| 3 | 3339 | mandatory command `0B`; exact all-zero cleanup once after the setter may reach its syscall | `530b0d0b00080000000000000000006e8a45` |
| 4 | 3340 | conditional command `0A` verification after clean empty raw-`80` cleanup; require eight zero bytes | `530c0d0a0000004dd045` |

The concatenated transcript SHA-256 is
`9a3a6fa3110df4769b62391cd289cebbd06bf801b04154117f4a989c1a78d51b`.
Bounds remain four writes / 56 TX bytes / 56 expected response bytes and 8192
serial RX bytes, or three writes / 46 TX bytes if verification is suppressed.
There is no arbitrary word/value/count/cadence/duration/sequence CLI and no
retry or reconnect.

Exact offline dry run:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word0-2000-pilot \
  --authorize-unvalidated-raw-pwm-word0-2000-pilot \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

Exact historical live invocation (completion is **not continuing authorization**):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word0-2000-pilot \
  --authorize-unvalidated-raw-pwm-word0-2000-pilot \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon \
  --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY \
  --run
```

The comparison target was the disconnected robot-right-side connector
printed `Motor L`, with corrected probe polarity. Use the same CH1 DC coupling,
10x probe, 2 V/div, and 50 us/div single-shot setup as the proven raw-1000
detail capture. Record pulse width and Vpp and, only if stable peak-to-peak
spacing is actually captured, period/repetition rate. Preserve irregular
spacing/amplitude rather than reducing it to one automatic number. Compare any
width ratio empirically; do not infer linear duty units from two points. No
continuing authorization follows from these completed runs.

#### Two completed raw-2000 runs

Both separately authorized runs completed with identical application
accounting: four fully accepted writes / 56 TX bytes, zero uncertain TX bytes,
and 56 serial RX bytes. Sequence 3337 returned raw `80` with exactly eight zero
bytes; sequence 3338 `[2000,0,0,0]` returned opaque raw `82` with no payload;
sequence 3339 all-zero cleanup returned raw `80` with no payload; and sequence
3340 returned raw `80` with exactly eight zero bytes. Cleanup was attempted
once and the zero baseline was getter-reverified in both runs.

Each usbmon trace recorded four successful bulk OUT completions / 56 captured
and completed OUT bytes and four payload-bearing successful bulk IN completions
/ 56 captured and completed IN bytes. Both reported zero unmatched
completions, submission errors, endpoint mismatches, evictions, retained
pending transfers, and uncaptured payload bytes. Every outer and nested
manifest entry verified.

| Run | Outer manifest | Nested manifest | Adapter journal | Binary usbmon |
|---|---|---|---|---|
| Width | `32f9b89a497ac3316337ca3d191f114a7ef96f69ce326305fcb39964093fca09` | `af50926558be59b99a7fa1e753cd9fc3f9ce6c065e204b0c5f9f6d174f6057d5` | `604bf054c23657c43e810057f8211bd2f0990a0ae566a1a2c15b93efcd8676d6` | `6fa8ae516d94be51255683497cbe1e4705343db2a646f377a3e54c3543297d7c` |
| Amplitude | `2cd7129aa137fdb4c9f4cf8f9ca9f7e91a11eafecd080d0f02413c3a862f43bb` | `9f655a5038ffdb698ff519f82f66e6b0e2caa59b2d93e22f5ad3d0c57ece96a2` | `cc4cfa909bd7a7fd5207a59311662585b04168aa8de815f739b746de80a251da` | `e2b2525adfef3deb48183e793b5911a2c5bfc11289c7430606f217910f77b14e` |

The first single-shot triggered; the operator reported 37.6 us pulse width and
a displayed 14.9 kHz frequency. The repeat reported 6.5 Vpp. Treat those as
raw operator observations: no stable period trace was supplied, so the
displayed frequency is not calibrated or established. Compared with raw
1000's 37 us and 2.11 Vpp positive capture, the reported pulse widths are
nearly equal while amplitude changed. This establishes value-dependent
unloaded output for the tested word/connector, but does not establish linear
voltage scaling, duty units, stable frequency, loaded voltage, or bridge
current capability. Probe loading, unloaded bridge behavior, trigger/window
selection, and scope automatic measurement can shape the observations. The
robot was powered OFF after the repeat.

### Prepared left-motor-connected raw-1000 proof

The smallest connected-load proof is a separate fixed scope. It reconnects
**only the physical left motor** to the robot-right-side controller connector
printed `Motor L`; the right motor remains unplugged, servos isolated, both
encoder harnesses connected, robot secured on blocks with wheels clear, and an
operator remains at the external cutoff. It does not authorize any other
connector, motor, word, value, duration, cadence, or repeated command.

| Step | Sequence | Fixed operation | Exact request |
|---:|---:|---|---|
| 1 | 3341 | command `0A` baseline getter; require raw `80` and exactly eight zero payload bytes | `530d0d0a0000004c0145` |
| 2 | 3342 | command `0B`; four LE `UInt16` words `[1000,0,0,0]` | `530e0d0b000800e803000000000000576145` |
| 3 | 3343 | mandatory command `0B`; exact all-zero cleanup once after the setter may reach its syscall | `530f0d0b00080000000000000000006b4e45` |
| 4 | 3344 | conditional command `0A` verification after clean empty raw-`80` cleanup; require eight zero bytes | `53100d0a0000004f8c45` |

The concatenated transcript SHA-256 is
`35c73a4732f7ee75e95f27c291018d7a038172a80c3a9bc9827aabd2d0cac575`.
Bounds remain four writes / 56 TX bytes / 56 expected response bytes and 8192
serial RX bytes, or three writes / 46 TX bytes if verification is suppressed.
The motion observation window is fixed at 250 ms after a clean correlated
setter response. The response budget is 500 ms from the immediate
pre-`os.write` boundary, so cleanup starts within at most approximately 750 ms
of that boundary on the planned clean path; response timeout/fault enters
cleanup immediately instead of opening the observation window.

Once the setter may reach its syscall, exactly one all-zero cleanup syscall is
attempted across raw `80`, raw `82`, timeout, correlation/CRC/framing/extra
frame, partial/uncertain write, evidence/journal fault, identity fault, or
interruption. The cleanup syscall precedes its journal event. There is no retry
or reconnect. The external cutoff is primary because software cannot prove
torque removal, motion stop, or application acceptance; raw `82` remains
opaque. Any abnormal motion or sound, unexpected direction, evidence fault, or
operator uncertainty requires immediate independent cutoff.

Exact offline dry run:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word0-1000-left-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-left-motor-connected-proof \
  --physical-left-motor-connected-to-robot-right-motor-l-connector \
  --motor-left-connected --motor-right-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon
```

Prepared live invocation (software readiness only, **not live authorization**):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word0-1000-left-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-left-motor-connected-proof \
  --physical-left-motor-connected-to-robot-right-motor-l-connector \
  --motor-left-connected --motor-right-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon \
  --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY \
  --run
```

No scope is required concurrently. The operator reports physical outcome
separately as `no_motion`, `motion_direction_uncertain`, or a plainly described
observed direction, plus whether the wheel visibly stopped after cleanup and
whether cutoff was used. A clean command/getter lifecycle is not a stop proof.
No hardware execution or authorization follows from this preparation.

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
