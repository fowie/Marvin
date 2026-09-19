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
`Motor L`; that velocity run did not test the actual `Motor R`-labelled
connector. Later raw-PWM word-2/word-3 evidence below does test that connector.

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

### Completed left-motor-connected raw-1000 proof

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

Exact historical live invocation (completion is **not continuing authorization**):

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

No scope was required concurrently. The operator reported physical outcome
separately as `no_motion`, `motion_direction_uncertain`, or a plainly described
observed direction, plus whether the wheel visibly stopped after cleanup and
whether cutoff was used. A clean command/getter lifecycle is not a stop proof.
No continuing authorization follows from these completed runs.

#### Two completed connected raw-1000 runs

Both separately authorized runs used only the physical left motor connected to
the robot-right-side controller connector printed `Motor L`; the right motor
was unplugged, servos isolated, encoder harnesses connected, wheels clear, and
the operator was at the cutoff. Both sealed evidence trees and every nested
manifest entry verify.

Each run recorded four fully accepted writes / 56 TX bytes, zero uncertain TX
bytes, and 56 serial RX bytes. Sequence 3341 returned raw `80` with exactly
eight zero bytes; sequence 3342 raw-1000 returned opaque raw `82` empty;
sequence 3343 mandatory all-zero cleanup returned raw `80` empty; sequence
3344 returned raw `80` with exactly eight zero bytes. Cleanup was attempted
once. Its syscall preceded the cleanup journal event and returned roughly 752
ms after setter prewrite in each run, consistent with the fixed 500 ms
extra-frame response boundary plus 250 ms motion-observation window and small
scheduling overhead. Protocol cleanliness and final zero getter evidence do
not prove physical stop.

Each usbmon trace recorded four successful bulk OUT completions / 56 captured
and completed OUT bytes and four successful payload-bearing bulk IN completions
/ 56 captured and completed IN bytes. Both reported zero unmatched
completions, endpoint mismatches, submission errors, evictions, retained
pending transfers, or uncaptured bytes.

| Run | Outer metadata | Capture metadata | Adapter journal | Binary usbmon |
|---|---|---|---|---|
| Wrong wheel watched | `25a7e315e13f238b567ac37cc1846dd1fc20d34d60e7a1b2e7b356589ab7f378` | `36a201d958eda264a318b2b9a21f661f11b4ae0adb159df1f023bfda58f9ca5e` | `7c11c0754da2a64e8d173f08f68622d7e713e495dd8becb88d137402d5b74a6f` | `5940ab98101d5765708fb236ebdc356b23816795d35bdafdb2bafc7ed5be5d8a` |
| Physical left wheel watched | `3ed65b40a649cfa79547a2f38d8b4093a5db6d15d894bb26513ff424956d01a2` | `10d9153f0fa06eb2d454bbde88150f445f1ebbcd4b8e8b3e4c1d2ed1eef9ae40` | `a3f76fb7c7ed25c44ddc6acd92790b5f09748947b326a6bf0d22ec49b81ce010` | `bf4f9ba399ae08c91d06b86bb760a61545c699bd0a7335f90515344430dcd197` |

The first run's physical observation is invalid/uncertain because the operator
watched the wrong wheel; it is **not** a no-motion observation. In the repeat,
the operator watched the physical left wheel and reported no motion and no
abnormal behavior. Cleanup completed in both runs. The robot was powered OFF
afterward. This does not establish that raw-1000 cannot produce torque under
different conditions or that the software cleanup physically stopped a motor.

### Completed failed left-motor-connected raw-2000 escalation — retired

This immutable scope changed only the raw word-0 value, fixed sequence
block, and literal authorization from the completed connected raw-1000 proof.
The physical setup and all gates remain identical. Disconnected testing
reported 6.5 Vpp for raw-2000 versus 2.11 Vpp for raw-1000, which justifies
this bounded comparison but does not predict connected torque.

| Step | Sequence | Fixed operation | Exact request |
|---:|---:|---|---|
| 1 | 3345 | command `0A` baseline getter; require raw `80` and exactly eight zero payload bytes | `53110d0a0000004e5d45` |
| 2 | 3346 | command `0B`; four LE `UInt16` words `[2000,0,0,0]` | `53120d0b000800d0070000000000000dcf45` |
| 3 | 3347 | mandatory command `0B`; exact all-zero cleanup once after setter may reach its syscall | `53130d0b0008000000000000000000769245` |
| 4 | 3348 | conditional command `0A` verification after clean empty raw-`80` cleanup; require eight zero bytes | `53140d0a0000004e0845` |

The concatenated transcript SHA-256 is
`00c4193c533cdcf24d94b280bd866255ceb6d94a9e3fb4e01eb59899749853a8`.
Bounds remain four writes / 56 TX bytes / 56 expected response bytes and 8192
serial RX bytes, or three writes / 46 TX bytes if verification is suppressed.
The clean path retains the 500 ms strict response/extra-frame boundary plus a
fixed 250 ms observation; any response or evidence fault skips observation
and enters the one mandatory cleanup immediately. No retry or reconnect exists.

Exact offline dry run:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word0-2000-left-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-2000-left-motor-connected-proof \
  --physical-left-motor-connected-to-robot-right-motor-l-connector \
  --motor-left-connected --motor-right-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon
```

Historical invocation (**retired; must not be executed again**):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word0-2000-left-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-2000-left-motor-connected-proof \
  --physical-left-motor-connected-to-robot-right-motor-l-connector \
  --motor-left-connected --motor-right-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon \
  --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY \
  --run
```

#### Live result and exact failure boundary

The sealed evidence tree and all nested manifest entries verify. The run
accepted all four planned writes / 56 TX bytes with zero uncertain bytes and
recorded 56 serial RX bytes. usbmon recorded four successful bulk OUT
completions / 56 captured and completed OUT bytes and four successful
payload-bearing bulk IN completions / 56 captured and completed IN bytes, with
zero unmatched completions, endpoint mismatches, submission errors, evictions,
retained pending transfers, or uncaptured bytes. The recorder's final
`failed: Observation recorder stopped: signal` status reflects coordinator
shutdown after the application failure; the four application exchanges were
fully captured.

| Artifact | SHA-256 |
|---|---|
| Outer metadata | `863fb1fca170eecbe189983bfc47a9457340caf8c86745ca8bdc07a0480ffc07` |
| Capture metadata | `84ae0ff7e6df76e2d113c13d83236f975241de41c0630e05949e3e7f421f6bd1` |
| Adapter journal | `7b7284bb5e93ce9b2dbdcce248cacf2d925520f54bcb4f73a413123d03d04a47` |
| Binary usbmon | `750ade3bafec6e81e05d48b43da9d84a92c9ac42bf30763e083672ff8c76d108` |

The exact lifecycle was:

1. Sequence 3345 baseline getter returned CRC-valid correlated raw `80` and
   exact payload `0000000000000000`.
2. Sequence 3346 `[2000,0,0,0]` setter was fully accepted and returned
   CRC-valid correlated opaque raw `82`, empty.
3. Sequence 3347 mandatory all-zero cleanup was fully accepted once and
   returned CRC-valid correlated raw `80`, empty. The cleanup syscall returned
   about 753 ms after setter prewrite. No second cleanup was attempted because
   the fixed policy permits exactly one.
4. Sequence 3348 conditional getter was fully accepted and returned CRC-valid
   correlated raw `80` with payload `0000640000000000`, four little-endian
   words `[0,100,0,0]`. The strict exact-zero check correctly failed with
   `unexpected_raw_pwm_payload`.

The mismatch was therefore **post-cleanup verification**, not the baseline.
It is not a response parser, sequence, CRC, USB-correlation, or prewrite-boundary
bug. It is controller evidence that the nominal raw-PWM getter exposed a
nonzero word after the one all-zero setter cleanup. Whether that represents
stale commanded state, another controller value, or incomplete application is
unknown because firmware handler semantics remain unavailable. Raw `80` still
does not prove application success.

The operator watched the physical left wheel and observed it **move in
reverse, then stop**. The user cut external power immediately when instructed.
The evidence does not establish whether the stop resulted from software
cleanup, controller behavior, or external cutoff timing, so no software stop
claim is made. This is the first connected-load proof that raw word 0/value
2000 can produce physical left-wheel motion in this setup.

The exact-zero baseline gate remains unchanged. This raw-2000 connected
profile is now fail-closed and retired in both the runner and coordinator;
offline review remains available, but another live run is rejected before
preflight or serial access.

After the operator explicitly confirmed that the physical left wheel had
stopped and that robot power was cut, the existing evidence-bound offline
acknowledgment verified the sealed manifest and moved the active state lock to
`power_cycle_reset_confirmed` with `hardware_access: false`. This is
bookkeeping based on operator observation and power cycle, not software
verification or reinterpretation of `0000640000000000`.

### Completed disconnected raw-PWM word-1/value-2000 discriminator

PCTestApp command `0B` parses and serializes four positional `UInt16` textbox
values in order. The recovered host source does not name those positions as
motor, direction, bridge leg, duty, or channel. The post-cleanup getter words
`[0,100,0,0]` therefore do not prove that word 1 is the complementary direction
for word 0. Dynamic controller output, braking, closed-loop behavior, stale
command state, or another field meaning remain possible and undecoded.

The smallest safe discriminator returns to disconnected load and changes only
word 1. Both motor power plugs must be disconnected; servos isolated; encoder
harnesses connected; robot secured; operator at cutoff; and unprivileged
usbmon active. The differential/isolated scope remains across the
robot-right-side controller connector printed `Motor L`. The operator compares
waveform polarity with the prior disconnected word-0/value-2000 capture. No
connected word-1 profile exists.

| Step | Sequence | Fixed operation | Exact request |
|---:|---:|---|---|
| 1 | 3349 | command `0A` baseline getter; require raw `80` and exactly eight zero payload bytes after fresh boot | `53150d0a0000004fd945` |
| 2 | 3350 | command `0B`; four LE `UInt16` words `[0,2000,0,0]` | `53160d0b0008000000d00700000000d5c745` |
| 3 | 3351 | mandatory command `0B`; exact all-zero cleanup once after setter may reach its syscall | `53170d0b0008000000000000000000735645` |
| 4 | 3352 | conditional command `0A` verification after clean empty raw-`80` cleanup; require eight zero bytes | `53180d0a0000004ec445` |

The concatenated transcript SHA-256 is
`41b8589d900c4e071e8c5510a70411d43561bf2059314537b63b7b7d20dc5d27`.
Bounds are four writes / 56 TX bytes / 56 expected response bytes and 8192
serial RX bytes, or three writes / 46 TX bytes if verification is suppressed.
There is one setter, no cadence, a fixed three-second observation, exactly one
zero cleanup after any possible nonzero submission, and no retry or reconnect.
The 500 ms strict response/extra-frame boundary, immediate pre-`os.write`
timestamp, CRC/sequence/command/shape/extra-frame/USB gates, state lock, and
exact-zero getter policy are unchanged.

Exact offline dry run:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word1-2000-pilot \
  --authorize-unvalidated-raw-pwm-word1-2000-pilot \
  --motor-power-plugs-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon
```

Exact historical live invocation (completion is **not continuing authorization**):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word1-2000-pilot \
  --authorize-unvalidated-raw-pwm-word1-2000-pilot \
  --motor-power-plugs-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon \
  --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY \
  --run
```

Use the same differential/isolated probe polarity and scope settings as the
word-0/value-2000 capture. Record whether a waveform exists and, if so, whether
its observed polarity is same, opposite, or uncertain relative to word 0.
Amplitude/width/spacing remain raw observations, not calibrated motor-control
units. A null waveform is inconclusive. A nonzero post-cleanup getter fails and
locks exactly as it did in the connected raw-2000 run. Disconnected results
and clean restoration evidence are prerequisites to considering any connected
word-1 test.

#### Two aborted pre-hardware word-1 attempts

The first requested output path ended in a reported udevadm by-id preflight
failure while robot power was initially OFF. The second requested output path
was immediately rejected by `RESTORATION_ACK_REQUIRED` after power was turned
on. The user then cut power. Neither named output directory exists: there is no
outer manifest, nested capture manifest, serial adapter journal, usbmon trace,
or metadata file for either attempt. Because session output creation follows
preflight and serial opening follows successful capture setup, this absence,
together with the state file pointing only at the first requested path, proves
that both attempts submitted zero application commands / zero TX bytes. The
first failed before output or serial creation; the second failed at the stale
lock before preflight.

Root cause was software state lifecycle: `raw_pwm_started` was persisted before
the shared preflight, and the broad failure path unconditionally converted
every failure into `raw_pwm_restoration_unverified`, even when no output
directory or device access existed. The lifecycle now records:

- `aborted_before_hardware`, `hardware_access: false` when the output path was
  never created;
- `set_aborted_before_nonzero` when sealed observation evidence explicitly
  says no nonzero setter could have applied;
- `raw_pwm_restoration_unverified` conservatively whenever observation says a
  nonzero may have applied, or output evidence is missing/malformed after an
  output directory exists.

All three safe terminal states are explicit; only the first two permit another
run without physical-restoration acknowledgment. The exact artifact-free lock
for the first attempt was updated offline to `aborted_before_hardware` with
`hardware_access: false` and `application_tx_bytes: 0`. No evidence was
invented or sealed after the fact.

A regression reproduces a udevadm/preflight exception, proves the output path
remains absent, and proves the next run reaches preflight instead of the
restoration gate. A separate regression confirms possible-setter evidence still
locks. A fresh disconnected word-1 attempt is now software-justified after new
operator safety confirmation and power-on; this is readiness, not live
authorization.

#### Three completed disconnected word-1 runs

All three sealed evidence trees and every nested manifest entry verify. Each
run recorded four fully accepted writes / 56 TX bytes, zero uncertain TX
bytes, and 56 serial RX bytes. Sequence 3349 returned raw `80` with exactly
eight zero bytes; sequence 3350 `[0,2000,0,0]` returned opaque raw `82` empty;
sequence 3351 one-time all-zero cleanup returned raw `80` empty; sequence 3352
returned raw `80` with exactly eight zero bytes. Each run attempted cleanup
once, reported no cleanup errors, and ended with getter zero-baseline
reverification.

Each usbmon trace recorded four successful bulk OUT completions / 56 captured
and completed OUT bytes and four successful payload-bearing bulk IN completions
/ 56 captured and completed IN bytes. All reported zero unmatched completions,
endpoint mismatches, submission errors, evictions, retained pending transfers,
or uncaptured bytes.

| Run | Outer metadata | Capture metadata | Adapter journal | Binary usbmon |
|---|---|---|---|---|
| Positive-trigger single shot | `f971c8965913f822ed93e47182f85686c9d97e7c4e57d0c89b6d70a54398ac98` | `ea0a763ef4b739a6ffea800f6e6d71ec9ea09177d0b131983635e5857abffe01` | `c9fc15afc3401db02093e0589431eca5b6ee43320c8f29ee23a8c5b2efae1cdd` | `0cfeaa47fe557f140463ab9a12880df9ea03a1eb3e22baece8a338cc12b7fa4c` |
| Continuous centered display | `535b94ab096af144bccddba13b965a5d8eb6ce97d0574a94d3220a8e7878e261` | `c9f2eca2d61bd016bc3f6f8e92940f3f60312b49cd8cb8c6d09e932e1e1121d9` | `f29693a2c732bf39b37ab043e833a5a8ce8b4565c6353a98b8e0b292e34f44b1` | `3c301fe587659f45cfa32cbd71acb417203ef19aac8d14a565910e57830ac114` |
| Negative-edge single shot | `e78bc814ff069d7f83381b4243b61a81f87a5d9af79220759f339742aad08e85` | `951eab6f720b3f2c8142db917924ba42fb1c4be69736c9fdebe51cd233f43196` | `7bc3a77152fc99a2fc5b30df64b4417be6a8f229c260d0f6f0380718943653dd` | `337cd1fdf9bb565d7cde0e376e0093b312aa64766ca144eaefb6814a6630216f` |

The first run used a positive 5 V trigger and did not trigger; that setup was
invalid for detecting negative polarity and is not a no-waveform result. The
second continuous centered display clearly showed a negative waveform. The
third negative-edge single shot repeated it; the operator manually counted
scope divisions and estimated approximately -11.8 V negative excursion/peak,
26.8 us pulse width, and 15.2 kHz repetition. The operator called the first
quantity “Vpp -11.8”; it is recorded as a negative excursion because Vpp is
unsigned. All three estimates may be slightly inaccurate and are not
calibrated controller units.

On the same robot-right-side connector printed `Motor L`, corrected-polarity
word-0/value-2000 evidence was positive while word-1/value-2000 evidence is
negative. This establishes opposite observed output polarity for these two
positional words under the tested disconnected setup. It does not prove field
names, calibrated voltage/duty/frequency, motor direction, loaded torque, or
bridge topology. The robot was powered OFF after the third run.

### Completed connected left-motor word-1/value-2000 proof

The completed proof used the separate literal scope: only the physical left motor
is connected to the robot-right-side controller connector printed `Motor L`;
the right motor is unplugged, servos isolated, both encoder harnesses
connected, wheels clear on blocks, and the operator remains at independent
cutoff. The disconnected negative polarity makes direction opposite/forward
relative to the prior word-0 reverse motion a hypothesis, not a guarantee; the
operator must report actual direction.

| Step | Sequence | Fixed operation | Exact request |
|---:|---:|---|---|
| 1 | 3353 | command `0A` baseline getter; require raw `80` and exactly eight zero payload bytes | `53190d0a0000004f1545` |
| 2 | 3354 | command `0B`; four LE `UInt16` words `[0,2000,0,0]` | `531a0d0b0008000000d00700000000d9cb45` |
| 3 | 3355 | mandatory command `0B`; exact all-zero cleanup once after setter may reach its syscall | `531b0d0b00080000000000000000007f5a45` |
| 4 | 3356 | conditional command `0A` verification after clean empty raw-`80` cleanup; require eight zero bytes | `531c0d0a0000004f4045` |

The concatenated transcript SHA-256 is
`2bdece99255756fed374341c9d8520eb7e0231c398aa5eb8f1e5da8dc8d58b95`.
Bounds are four writes / 56 TX bytes / 56 expected response bytes and 8192
serial RX bytes, or three writes / 46 TX bytes if verification is suppressed.
The clean path keeps the 500 ms strict response/extra-frame boundary followed
by a fixed 250 ms motion-observation window. Faults skip observation and enter
the mandatory cleanup immediately. There is no retry or reconnect.

Exact offline dry run:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word1-2000-left-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-word1-2000-left-motor-connected-proof \
  --physical-left-motor-connected-to-robot-right-motor-l-connector \
  --motor-left-connected --motor-right-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon
```

Prepared live invocation (software readiness only, **not live authorization**):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word1-2000-left-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-word1-2000-left-motor-connected-proof \
  --physical-left-motor-connected-to-robot-right-motor-l-connector \
  --motor-left-connected --motor-right-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon \
  --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY \
  --run
```

The sealed `raw-pwm-word1-left-motor-connected-20260919T1233` run exited zero.
All manifests verified. It recorded four accepted writes / 56 TX bytes, zero
uncertain TX, and 56 RX bytes: exact-zero raw-`80` baseline, opaque raw-`82`
`[0,2000,0,0]` setter, one raw-`80` all-zero cleanup, and exact-zero raw-`80`
final getter. usbmon independently recorded four successful OUT / 56 bytes
and four payload-bearing successful IN / 56 bytes with no correlation faults.
Evidence hashes are outer metadata
`938afb98fc9cdf284a0d5810f5c099240b2fafe083ea173adaf9ad03bce9d43e`,
capture metadata
`bf0a6e4274cbf7b24e907bd8d6e83c54f945bf9083fdb487e3a3327a319fc5ed`,
adapter journal
`55baf86a273981238c880b50384ffe07ce1a1a59f5d7f2ab99da67f46890ae25`,
and binary usbmon
`03fca236daef6710119bd1c46a87c663c6826830a8c1962862217a2d546bf4ef`.

The operator observed the physical left wheel move forward and visibly stop
after the 250 ms window/cleanup, with no abnormal behavior, then powered the
robot OFF. Combined with the earlier word-0/value-2000 reverse motion, this
establishes those two tested physical directions for the connected left motor.
It does not decode raw `82`, prove that cleanup caused the stop, or establish
generic word/channel topology.

### Prepared disconnected actual-Motor-R word-2 and word-3 mapping

The corrected reciprocal connector map is: robot-right-side printed `Motor L`
controls the physical left motor; robot-left-side printed `Motor R` controls
the physical right motor. The earlier supposed Motor-R scope run used the
former connector and is not evidence for actual printed `Motor R`.

These are separate immutable disconnected-load scopes, never combined. Both
motor power plugs remain disconnected, servos isolated, encoder harnesses
connected, robot secured, and a differential/isolated scope is placed across
the robot-left-side connector printed `Motor R`. Word 2 is tested first.
Words 2/3 forming a direction pair is only a positional hypothesis.

| Scope | Sequences | Setter words | Exact frames | Transcript SHA-256 |
|---|---:|---|---|---|
| word 2 | 3357..3360 | `[0,0,2000,0]` | `531d0d0a0000004e9145`; `531e0d0b00080000000000d0070000f35e45`; `531f0d0b00080000000000000000007a9e45`; `53200d0a0000004a7c45` | `64bf02c70c5b94a5e2845e73468d72a3b441fa4cd0002f38012707a2b2bc8fca` |
| word 3 | 3361..3364 | `[0,0,0,2000]` | `53210d0a0000004bad45`; `53220d0b000800000000000000d0075a6145`; `53230d0b000800000000000000000046a245`; `53240d0a0000004bf845` | `6ecfb0fac2ed75ae890517d913ba82b1e5a460d52826bf50661d0ec01304700d` |

Each performs an exact-zero `0A` baseline, one `0B` setter, fixed three-second
scope window, exactly one all-zero cleanup, then conditional exact-zero getter.
There is no retry or reconnect. The exact offline dry run for word 2 is:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --disconnected-load-raw-pwm-word2-2000-pilot \
  --authorize-unvalidated-raw-pwm-word2-2000-pilot \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

The live form, which is software readiness and **not live authorization**, adds
`--expected-physical-port PORT --output NEW_PRIVATE_EVIDENCE_DIRECTORY --run`.
Only after word 2 evidence is sealed should the same invocation substitute
`word3` for `word2` in both literal flags. Waveform polarity is recorded as an
observation only; it does not prove direction, calibrated duty, or motor binding.

#### Four completed actual-Motor-R disconnected runs

Both word-2 and both word-3 evidence trees and nested manifests verify. Every
run recorded four accepted writes / 56 TX bytes, zero uncertain TX, and 56 RX
bytes. Word 2 used sequences 3357..3360 and word 3 used 3361..3364. Each had
an exact-zero raw-`80` baseline, opaque empty raw-`82` setter, one empty
raw-`80` all-zero cleanup, and exact-zero raw-`80` final getter. usbmon
recorded four successful OUT / 56 bytes and four payload-bearing successful
IN / 56 bytes in each run, without correlation faults.

| Run | Observation | Outer | Capture | Journal | Binary usbmon |
|---|---|---|---|---|---|
| word2 1238 | Continuous display clearly showed a negative waveform; no capture | `97b2f9056588cb79df550bbd0f2f2bbaebc025d259da5c482f94164e14de8e3a` | `1d17f4f9f0b20af183c77dbcc59887b49613d1e913701c4a855da9dc81fcf365` | `7aeff3e9a46550b21cd339040f08e0a22b4af7ff88e4d368f8386a22a842791e` | `334b84e2551a1184770daf1fb663b269336a7f361c22c2228a46982983ea5e0c` |
| word2 1239 | Negative-edge capture; manual estimates about -12 V negative excursion, 26.4 us width, 15.2 kHz | `40579ff19e3c4848017926f6ca3a5dd1c9c4f6ffa06fa801d97e40a9579bd1f8` | `c4a3159714f081b0e0a75b4026716898f96abbdf531013de1ead38c67b35e839` | `50e076474d2e883aacd857b4b0702e1361847c462ad418eb7686a5c98eb7c078` | `fad6814e27a5a7b63781e4fd3cb8330ccd2d3c85d8fc02460fbc887f85724698` |
| word3 1240 | Continuous display clearly showed a positive waveform | `8997d72e708c5bd159520b177ab9856cdae7c00c78f3fc012e4b57e8305cc6d2` | `b4b288f59c735975ee804cb3aefc9e5281f213edff4ef70932f8014c7021abc4` | `c7d7f984b479390c0ac05597ace629fcf70046b88a6239af0aad53efa4cb10a1` | `b4bcd0cd14d22923e7c730f8aa996c644f3f0d47365d2372267f8941710d27b3` |
| word3 1241 | Positive capture; manual estimates about 8 Vpp, 38.8 us width, 15 kHz | `76c829231ffe13893827ffc9d101e5a6f1f4546682a923314fc88db162025412` | `b27f5282bab48a2c63395b55b5612e195f6b7caeeebd2222f6e1104f5e2850b5` | `fca25eae47bf800dd71f450ad5b97b26dbcd29a4d088c3a0aafd2f637b130f42` | `5f3f98e3fca07b10dbf48dfb1b448c9ed7c6c1c93e763662cbf46fa0cbe622b4` |

The operator called the word-2 quantity “Vpp -12”; it is recorded as a
negative excursion because Vpp is unsigned. All values were manually counted
from divisions and carry that uncertainty. Both motors were unplugged and the
differential scope was across the robot-left-side connector printed `Motor R`.
The robot was powered OFF after the final run. This establishes opposite
observed polarity for positional words 2 and 3 on that connector, not
calibrated output, bridge topology, motor direction, or loaded behavior.

### Completed connected right-motor word-2 and word-3 proofs

Each proof is an independent literal scope. Only the physical right motor is
connected to the robot-left-side connector printed `Motor R`; the left motor
is unplugged, servos isolated, both encoders connected, wheels clear on blocks,
and the cutoff operator remains present. Word 2 is executed first. The
operator watches the physical right wheel and reports actual direction, visible
stop or uncertainty, abnormal sound, and cutoff use. No direction is predicted
from unloaded polarity because mounting or wiring may invert it.

| Scope | Sequences | Setter words | Exact frames | Transcript SHA-256 |
|---|---:|---|---|---|
| connected word 2 | 3365..3368 | `[0,0,2000,0]` | `53250d0a0000004a2945`; `53260d0b00080000000000d0070000caa645`; `53270d0b0008000000000000000000436645`; `53280d0a0000004b3445` | `4bb4f48a37e8a08db303b7df19990108504ca4e5800d71c13ddd9fd189a2f064` |
| connected word 3 | 3369..3372 | `[0,0,0,2000]` | `53290d0a0000004ae545`; `532a0d0b000800000000000000d00753a945`; `532b0d0b00080000000000000000004f6a45`; `532c0d0a0000004ab045` | `45853e630a4d4f1fb292b77ffe68c0c0bef1258bfb757de80840f9210c106ccd` |

Both require an exact-zero baseline, one setter, a 250 ms observation after a
clean response, exactly one zero cleanup, and a conditional exact-zero getter.
The response budget remains 500 ms from pre-`os.write`; there is no retry or
reconnect. Any nonzero final getter faults and retains the restoration lock.

Both sealed evidence trees and all nested manifest entries verify. The word-2
run exited zero with four accepted writes / 56 TX bytes, zero uncertain TX,
and 56 RX bytes: exact-zero raw-`80` baseline, opaque raw-`82` setter, one
raw-`80` zero cleanup, and exact-zero raw-`80` final getter. usbmon recorded
four successful bulk OUT / 56 bytes and four successful payload-bearing bulk
IN / 56 bytes without correlation faults. Hashes are outer
`188915eb2cd531990072bd41321ad4401469a1f9f78e6c62e9300bb2e722ab26`,
capture
`b5e62ee7ba8f9caebcd5bf3dbbd8ab0bfe51fc3b7ab008f402e178ee45882542`,
journal
`adbb29836b1e2bcf83ac64716f374f82aecc51aa49dd1128471207e3dc3d286c`,
and binary usbmon
`ead985abaa6ac53307f5bff0f951dbe67d70c353128070917b8da42d91483d08`.
The operator observed the physical right wheel move backward and visibly stop
after the 250 ms window/cleanup.

The word-3 run exited one only because its conditional final getter was
nonzero. The exact-zero baseline was clean; the `[0,0,0,2000]` setter was fully
accepted and returned opaque raw `82`; the all-zero cleanup was fully accepted
exactly once and returned raw `80`. The final sequence-3372 getter was also
fully transmitted and returned CRC-valid raw `80` payload
`0000000064000000`, LE words `[0,0,100,0]`; this caused
`unexpected_raw_pwm_payload`. Accounting was still four accepted writes /
56 TX, zero uncertain TX, and 56 RX, with the same clean usbmon counts as the
word-2 run. Hashes are outer
`e8adc9915b0910b4a768c1376ca457ea58bad579c1800add0c20c85fc57d6345`,
capture
`9d0ed2467cbe345aad7c535a9fef2a11921bc324b1448d2a3ab3c5d456c33e26`,
journal
`7a84a229f6909c7be97e83bac0338735cf21fe336e8fac0010f43de819705211`,
and binary usbmon
`16756cf83784bf453a2f6141247d731640212be038b5fe9d884c89588fb1702f`.

The operator observed the physical right wheel move forward and visibly stop,
then cut power immediately when prompted. Software cleanup, observed stop, and
external cutoff are separate facts; the stop is not attributed to cleanup.
The evidence-bound state lock was acknowledged offline with
`hardware_access:false` after the operator's physical-stop and power-cycle
confirmation. This did not reinterpret the nonzero getter or claim software
restoration.

Exact word-2 offline dry run:

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word2-2000-right-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-word2-2000-right-motor-connected-proof \
  --physical-right-motor-connected-to-robot-left-motor-r-connector \
  --motor-right-connected --motor-left-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon
```

Historical prepared live form (software readiness only, not continuing live
authorization):

```bash
python3 -m tools.marvin_legacy_raw_pwm_pilot \
  --raw-pwm-word2-2000-right-motor-connected-proof \
  --authorize-unvalidated-raw-pwm-word2-2000-right-motor-connected-proof \
  --physical-right-motor-connected-to-robot-left-motor-r-connector \
  --motor-right-connected --motor-left-disconnected \
  --servos-isolated --both-encoder-feedback-connected \
  --robot-secured-on-blocks --operator-at-external-cutoff \
  --unprivileged-usbmon --expected-physical-port PORT \
  --output NEW_PRIVATE_EVIDENCE_DIRECTORY --run
```

Word 3 used the corresponding `word3` literal flags. Neither completed scope
authorizes another live run.

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
