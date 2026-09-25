# Legacy projector/front-camera tilt: offline mapping plan

This is an offline evidence summary and future operator checklist, not live
authorization. `tools.marvin_legacy_servo_plan` only builds S/E packet bytes;
it has no transport and cannot open serial, USB or sysfs.

## Authoritative legacy findings

- The installed `045e:4444` controller has proved legacy S/E `1D`
  `GetServoPosition`: empty request, raw-`80` reply, four payload bytes. The
  retained reply contains two little-endian `uint16` values: **2500, 2730**.
- The historical `PCTestApp` sends legacy `1E` `SetServoPosition` with exactly
  two positional little-endian `uint16` words. Its two input boxes are
  unlabeled. The error text says `0-3000`, but `UInt16.Parse` is the only
  enforcement, so this is a UI hint, not a safe range.
- Legacy `ReadRawData` labels its two servo fields in the opposite value order:
  `projectorPosition=2730`, then `depthCam=2500`. This supports a hypothesis,
  not proof, that getter/setter word 0 is camera and word 1 is projector.
- The installed 108-byte configuration reply, interpreted through the later
  source layout, reports camera `min/default/max=1200/2500/2500` and projector
  `200/2730/2730`. The later source defaults differ for camera and its handler
  returns compiled defaults. These words are evidence inputs, not established
  physical limits, calibration, units, active settings, or permission to move.
- The old sample has no servo timer, startup setter, cadence, retry, cleanup,
  neutral restore, response parser, or narrower validation at this call site.

Source anchors: `PCTestApp/Form1.cs:749-778,510-511`,
`PCTestApp/Form1.Designer.cs:413-445,1548-1582`,
`m_inc/m_config.h:45-53`, the sealed getter/config/raw-data evidence cited in
`provenance.md`, and the derived command/configuration/telemetry exports.

## Generation boundary

The retained installed service is successor-generation evidence only. It uses
EF/BE framing and `19 SetServoRadians` with joint 3 `ProjectorTilt` on Drive
and joint 4 `FrontCameraTilt` on Head. Its configured ranges are projector
`0..1.4` rad and front camera `-0.34906585..0.61086524` rad. On startup it
commands front-camera tilt to zero, enables holding current, and starts a
projector shutter/brightness/inversion/reindex/home sequence. None of those
IDs, radians, ranges, board routing, startup actions, or mechanics may be
imported into legacy S/E `1D/1E`.

The successor servo surface also contains `0D RecalibrateServo` (one `uint8`
ID), `1A SetServoHoldingCurrent` (ID plus enable bytes), `35
SetServoCalibrationOffset` (one `int32`), `3B SetServoSequence` (packed motion
sequence), and `3C ConfigPid` (four `int32`). The installed service directly
calls radians, holding-current, recalibration and sequence operations. It has
no legacy `1D` two-word position getter/setter contract. Every one of these
successor operations is protocol-mismatch/catalog-only for the installed
legacy controller.

## Unknowns that block live use

Physical word-to-servo ordering, polarity, units, exact installed handler,
accepted response, active bounds, neutral safety, holding behavior, timeout,
startup behavior, mechanical clearance, and whether a getter reflects command,
feedback, or cached state remain unknown. The reported version words do not
identify an exact image. A successful write, CRC-valid frame, raw response, or
restored getter cannot prove physical restoration.

## Reconnection checklist

Before each separately authorized run: declare exactly one named servo
connected and the other isolated; remove all power including USB back-power
before wiring changes; secure the robot/mechanism; place an operator at an
external actuator-energy cutoff; verify exact physical port and installed
legacy identity; approve one exact word, baseline, neutral, minimum, maximum,
target and source citation; confirm clearances; start fresh serial and USB
evidence; prohibit retry, reconnect and resume; and require one bounded cleanup
attempt plus evidence sealing on every post-setter exit.

## Minimal one-channel sequence

1. With only one declared servo connected, issue one `1D` getter and stop
   closed unless its two words exactly match the reviewed baseline.
2. Change only candidate `word0` **or** `word1`; hold the other word at its
   fresh baseline. Send one exact bounded `1E` setter and observe only the
   declared mechanism with the cutoff ready.
3. Attempt exactly one `1E` restore of the complete fresh two-word baseline,
   including on timeout, rejection, partial/uncertain write, interruption, or
   observation fault. Never retry or reconnect.
4. Only after a correlated restore response, issue one `1D` verification
   getter. Seal raw serial/USB evidence and operator observations regardless of
   result. A mismatch or uncertain physical restore ends the session.
5. Power down and remove USB back-power before changing which servo is
   connected. Review and seal the first channel before planning the other.

Example generation requires caller-supplied reviewed values; it does not make
them safe:

```sh
python -m tools.marvin_legacy_servo_plan \
  --word WORD --baseline WORD0 WORD1 --target TARGET \
  --minimum MIN --maximum MAX --neutral NEUTRAL \
  --connected-servo projector-tilt-only \
  --profile-source REVIEWED_EVIDENCE_CITATION
```

## Dedicated bounded read-only servo-position getter

`tools.marvin_legacy_front_servo_getter` has one immutable application
transcript: sequence `3516`, empty legacy `1D GetServoPosition`, request
`53bc0d1d000000525445`. The command has no channel or servo selector:
PCTestApp requests and returns both unlabeled LE16 words together. The tool
name and connected-front-camera safety profile describe the physical test
configuration, not command-level camera selection. It exposes no setter,
sequence, payload, value, retry, reconnect or follow-up option. It is offline
by default:

```sh
python3 -m tools.marvin_legacy_front_servo_getter
```

The reviewed live invocation template is:

```sh
python3 -m tools.marvin_legacy_front_servo_getter \
  --run --expected-physical-port REVIEWED-PORT --output NEWDIR \
  --operator-present --robot-secured \
  --independent-actuator-cutoff-ready \
  --drive-and-other-actuators-inactive \
  --front-camera-ax12-plus-connected-at-j24 \
  --projector-servo-physically-disconnected \
  --battery-only-passive-scope-on-verified-green-black-via-spare-cable \
  --host-usb-connected --unprivileged-usbmon \
  --authorize-one-read-only-legacy-1d-getter-only \
  --authorize-immediate-physical-power-off-after-getter
```

This is a reviewable command, not standing authorization. `REVIEWED-PORT` must
be the exact fresh physical USB topology and `NEWDIR` must not exist. The tool
requires the scope flag to describe a concurrently attached battery-only
passive scope on the already verified spare-cable green/black points; the
template does not itself authorize attaching it or any powered procedure. It
validates `045e:4444`, captures unprivileged binary usbmon evidence, permits
one application write of ten bytes, waits at most 0.5 seconds for exactly one
unique CRC-valid sequence/command-correlated response with exactly four
payload bytes, and has a seven-second hard overall bound including up to two
seconds reserved for cleanup. It
records the raw response field and both little-endian words without treating
USB completion or the response field as an ACK. Missing, malformed, duplicate,
late, partial or uncertain evidence fails closed with no retry, reconnect or
follow-up. The fresh output directory is immutable capture evidence: raw
serial/USB artifacts, declarations, result metadata and the final SHA-256
manifest are retained even on failure. After the tool exits and evidence is
sealed, the operator physically powers Marvin off immediately; the tool sends
no power command. This tool has not been executed by the agent.

The first operator live attempt on 2026-09-23 failed closed before recorder
readiness because the session requested its exact 17-second USB recorder
budget while the recorder still admitted only the mapper's exact 18-second
profile. Marvin was physically powered off, the scope was stopped, and no
unexpected condition was reported. The sealed observation is definitive:
`status=not_started`, `accepted_tx_bytes=0`, `uncertain_tx_bytes=0`, and
`write_status=not_attempted`. The top-level `metadata.json` SHA-256 is
`fc0a5f2e668f3affbc33eaeaed9a2dc558e124034d566ca3307911d6fae60e98`.
This is pre-write failure evidence, not a `1D` protocol observation and not
authorization to retry.

A later retry proceeded only after fresh authorization and fresh physical
gates. The operator reported Marvin physically OFF immediately afterward, the
scope stopped, and no unexpected condition. The sealed execution contains the
single fixed request `53bc0d1d000000525445`: 10 accepted TX bytes, zero
uncertain TX bytes, one submission attempt, one 10-byte completed USB OUT, and
no retry, reconnect or follow-up. It contains exactly one CRC-valid correlated
sequence-3516/command-`1D` response:

```text
53bc0d1d800400c409aa0ac2b145
```

The observed raw response field is `80`; the four-byte payload is
`c409aa0a`, yielding little-endian words `[2500,2730]`. The result retained
one correlation candidate, 14 serial/USB IN bytes, no cleanup error, and final
usbmon counters `queued=0`, `dropped=0`. This proves only one correlated
legacy response shape and its raw data. Neither the raw `80` field nor USB
completion is an application ACK, and the two words are not calibrated or
measured angles. The host/controller exchange was read-only and neither
selected the camera or projector nor sent a downstream control signal to
either. The evidence adds no setter, channel assignment or physical-position
proof.

Marvin was powered on and allowed to complete startup before the operator
armed the ADS1013D specifically for the getter window. Under the declared
10x, DC-coupled, `2 V/div`, `200 us/div`, falling-edge-near-`3 V` setup, the
scope remained armed and waiting through the one getter and afterward: no
falling low transition met the configured trigger and no waveform or
screenshot was retained. After Marvin was physically powered off, the
operator disarmed the scope and, while Marvin remained OFF, removed both
probe tip and ground before any scope USB connection.

This sequencing excludes the startup interval from this instrumentation-
negative observation. It is evidence that legacy `1D` produced no
scope-triggering low transition under that declared setup, which supports but
does not prove controller-held or cached aggregate values rather than a
getter-induced AX-12+ transaction. It does not identify either returned word
with the connected camera. Probe contact, trigger sensitivity and capture
effectiveness were not independently revalidated during or after the getter
window, so the observation cannot establish absence of AX-12+ traffic.

The preserved output remains outside the repository at
`servo2-getter-20260923T2220`. The independently read top-level
`SHA256SUMS` file has SHA-256
`38e806cd9fc98961aaea3eef6d09bf37669d929337a4bbeaf0b347d55cdd0a13`;
that manifest records top-level `metadata.json` SHA-256
`5727edea38db06f9eddccfbb42129ea584e2be539c2b838a000541dbb3b70c4e`.
This completed read-only observation is not standing authorization for any
further live action.

## Dedicated baseline-only restore verifier

`tools.marvin_legacy_front_servo_baseline_restore` is a separate
offline-default tool for one immutable legacy `1E SetServoPosition` request:
sequence `3517`, payload `[2500,2730]`, request
`53bd0d1e000400c409aa0a52a945`. It has no preliminary getter, target, delta,
dwell, sequence, value, follow-up setter, retry or reconnect control.

Dry run:

```sh
python3 -m tools.marvin_legacy_front_servo_baseline_restore
```

The separately authorized live invocation was:

```sh
python3 -m tools.marvin_legacy_front_servo_baseline_restore \
  --run --expected-physical-port REVIEWED-PORT --output NEWDIR \
  --operator-present --robot-secured \
  --independent-actuator-cutoff-ready \
  --drive-and-other-actuators-inactive \
  --front-camera-ax12-plus-connected-at-j24 \
  --projector-servo-physically-disconnected \
  --battery-only-passive-scope-on-verified-green-black-via-spare-cable \
  --operator-confirmed-baseline-restore-clearance \
  --host-usb-connected --unprivileged-usbmon \
  --authorize-exactly-one-legacy-1e-baseline-2500-2730-restore-setter \
  --authorize-immediate-physical-power-off-after-baseline-restore
```

This command is historical evidence, not standing authorization. It permits
one 14-byte application write and one bounded response window, then
closes with no follow-up command. The response must be exactly one unique
CRC-valid sequence/command-correlated empty-payload frame. Its raw response
field and USB completion remain observations, not ACK. The exact
`045e:4444` identity and reviewed physical USB port are pinned, and fresh
serial/usbmon evidence is sealed on every outcome.

The execution retained one 14-byte accepted request, zero uncertain bytes,
one submission, and exactly one unique CRC-valid correlated empty raw-`82`
response `53bd0d1e820000f3e945`; serial RX was 10 bytes and cleanup errors were
empty. A final identity validation nevertheless exceeded the response
deadline after the response was retained, so the tool reported failure.
Marvin was physically powered off immediately, the operator reported no
visible movement, and scope probes were removed before USB export.

The passive J24 waveform contains the complete checksum-valid packet:

```text
FF FF 02 05 03 1E 41 03 93
```

This is Protocol 1.0 WRITE to ID 2, Goal Position address `1E` hex, value
`0341` hex (`833`), checksum `93`. Combined with the earlier observed
`2490 -> 830`, the installed controller outputs the integer-division-by-three
results for both tested values: `2490 / 3 = 830` and `2500 / 3 = 833`.
This proves the baseline setter emitted the front-camera J24 Goal Position
command with value 833. It still does not prove the conversion outside these
two values, actuator acceptance/execution, visible motion or physical
restoration. The later `FF FF` prefix is incomplete and unattributed.

The independently verified preserved scope SHA-256 values are:

- waveform:
  `d22129a17f9624ac3ed6f13551916e2d34b7703f37d7a9c21d008a84ee0a3383`;
- BMP:
  `2a7d95022c5a38125635481bacc7972298a7632982dc0be82746165fa09b9bbb`;
- PNG:
  `61b28bbeb63fc0d3394fdc8404163c5ebe9b942762448995bd30eb08770e8253`.

The later shared offline fix keeps expensive host identity validation outside
fixed response windows. It still checks the owned fd generation, safety guard
and ingress clock while capturing and correlating for the full 0.5 seconds.
Every subsequent application write retains the existing full pre-write
identity checks. No write, retry, reconnect, response uniqueness or cleanup
rule changes. No live rerun is authorized.

## Fixed front-camera live mapper

`tools.marvin_legacy_front_servo_mapper` is the only setter-capable legacy
servo tool. It is still offline by default and exposes no target, delta, range,
dwell, word, command, stop, calibration, reset, flash or power-state option.
Its historical reviewed profile was authorized under an operator declaration
that the one physically connected front-camera actuator was AX-12+, with the
projector servo disconnected: getter baseline
`[2500,2730]`, one setter `[2490,2730]`, one complete `[2500,2730]` restore
attempt required to start within 0.25 seconds of setter start (a maximum
restore deadline, not a dwell), then one verification getter only after a
correlated restore response. Raw response status is retained as an
observed field and is not called generic success. Protocol agreement never
proves physical motion or restoration.

Dry run:

```sh
python -m tools.marvin_legacy_front_servo_mapper
```

The live recipes below are retained only as historical records of the executed
profiles. **They are retired and must not be reused.** A later inspection of
the actuator body established AX-12+ model identity. At the time of these
recipes it had not established the legacy UI conversion, J24 controller
implementation or runtime `1E` semantics. The later passive capture below
establishes the tested word-0 path/value only. No further live legacy `1E`
delta is authorized.

Historical exact live invocation:

```sh
python -m tools.marvin_legacy_front_servo_mapper \
  --run --expected-physical-port REVIEWED-PORT --output NEWDIR \
  --operator-present --robot-secured \
  --independent-actuator-cutoff-ready \
  --drive-and-other-actuators-inactive \
  --front-camera-tilt-only-connected-projector-servo-physically-disconnected \
  --front-camera-servo-is-ax12-plus \
  --exact-profile-baseline-2500-2730-target-2490-2730-dwell-0-25-seconds \
  --authorize-single-legacy-1e-front-camera-mapping-command \
  --unprivileged-usbmon
```

This command is historical evidence only, not a current procedure.

The clean word-0 run produced correlated raw-`82` setter/restore responses, a
matching final `[2500,2730]` getter, accepted TX only, zero uncertain TX bytes,
zero usbmon drops, and no operator-visible movement. Marvin was then reported
OFF with host USB disconnected. Those run and operator reports are external
evidence that predated the later channel proof.

### Word-0 J24 Goal Position proof

A later freshly authorized execution of the same fixed word-0 profile used a
passive ADS1013D capture on verified J24 green/black after startup. The host
baseline was `[2500,2730]`, the setter was `[2490,2730]`, and the single
restore attempt was `[2500,2730]`. Host evidence retained 38 accepted and zero
uncertain TX bytes across the baseline getter, setter and restore. CRC-valid
raw-`82` setter and restore replies are retained, and restore began
`85.209 us` after setter start, within the fixed bound.

The passive waveform contains this complete checksum-valid packet:

```text
FF FF 02 05 03 1E 3E 03 96
```

This is DYNAMIXEL Protocol 1.0 WRITE, ID 2, length 5, address `1E` hex
(`30`, Goal Position), little-endian value `033E` hex (`830`), checksum `96`.
This directly proves that the installed controller emitted an AX-12+ Goal
Position write on J24 during the fixed legacy `[2490,2730]` setter. Because
the only changed host word was word 0 and `2490 / 3 = 830`, the strongest
source-consistent interpretation is that word 0 maps to that front-camera
path with division by three for this tested value. One input/output pair does
not uniquely prove the transformation, full conversion domain, units,
rounding for other values, or word-1 routing. The retained window does not
establish that no other downstream packet was sent. A later `FF FF 02` prefix
is incomplete and unattributed; no AX-12+ status packet was decoded, so
actuator acceptance and execution remain unproved.

Final restore correlation failed only when a repeated identity check began
too close to the response-window deadline and completed after it. The shared
offline fix keeps expensive host identity validation outside fixed response
windows while retaining lightweight owned-fd, safety-guard and ingress-clock
checks throughout capture. Full identity checks remain mandatory before each
application write; no write, retry, reconnect, correlation or uniqueness rule
changes. The CRC-valid
restore response remains preserved, but the run therefore sent no verification
getter and physical restoration remains unproved. The operator
reported no visible movement, immediately powered Marvin off, and removed the
scope probes before USB export. The run's top-level manifest and nested
capture manifest both verify. Preserved scope SHA-256:

- waveform:
  `b68477ccd301c8722684cd61181c9c13c44ccca295224180711b9f083430ce94`;
- BMP:
  `f2a3d2d7c7d53c66b71d53b80bf37c19a4b84d9adb872b803d331fe69a8705e0`;
- PNG:
  `16f8e240a50e010e779aa0cacfed31357ea951f18a518f69785c0f33fc2efe2a`.

This proof does not authorize another live setter. A separate named word-1
hypothesis changes only `[2500,2730]` to `[2500,2720]`; it cannot accept
arbitrary words or values.

Word-1 hypothesis dry run:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word1-front-camera-hypothesis
```

Historical exact word-1 hypothesis live invocation:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word1-front-camera-hypothesis \
  --run --expected-physical-port REVIEWED-PORT --output NEWDIR \
  --operator-present --robot-secured \
  --independent-actuator-cutoff-ready \
  --drive-and-other-actuators-inactive \
  --front-camera-tilt-only-connected-projector-servo-physically-disconnected \
  --front-camera-servo-is-ax12-plus \
  --exact-profile-baseline-2500-2730-target-2500-2720-word1-hypothesis-dwell-0-25-seconds \
  --authorize-single-legacy-1e-front-camera-word1-hypothesis-command \
  --unprivileged-usbmon
```

The clean word-1 one-degree run likewise produced correlated raw-`82`
setter/restore responses, a matching final `[2500,2730]` getter, accepted TX
only, zero uncertain TX bytes, zero usbmon drops, and no operator-visible
movement. The operator reported that the AX-12+ LED illuminated or blinked
during power-on, then Marvin was OFF with host USB disconnected. Both
one-degree negative observations remain external evidence, not channel proof.

A separate historically named word-0 five-degree diagnostic changes only
`[2500,2730]` to `[2450,2730]`. Its name came from the assumed AX-12+
`0..3000` to `0..300` scale. The later held-direction run instead produced an
operator-observed displacement of approximately 0.5 degrees for those 50
units, so “five-degree” is retained only for CLI/evidence compatibility and is
not installed calibration. Neither observation is precision or full-range
proof, and each live profile requires separate literal clearance.

Historically named five-degree dry run:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word0-five-degree-diagnostic
```

Historical exact five-degree live invocation:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word0-five-degree-diagnostic \
  --run --expected-physical-port REVIEWED-PORT --output NEWDIR \
  --operator-present --robot-secured \
  --independent-actuator-cutoff-ready \
  --drive-and-other-actuators-inactive \
  --front-camera-tilt-only-connected-projector-servo-physically-disconnected \
  --front-camera-servo-is-ax12-plus \
  --operator-confirmed-word0-five-degree-mechanical-clearance \
  --exact-profile-baseline-2500-2730-target-2450-2730-word0-five-degree-dwell-0-25-seconds \
  --authorize-single-legacy-1e-front-camera-word0-five-degree-diagnostic-command \
  --unprivileged-usbmon
```

### First historically named 50-unit run evidence

The first authorized 50-unit execution ended in a software evidence
correlation failure, `KeyError: 'set_prewrite_monotonic'`. The sealed evidence
shows three accepted application writes and three successful USB OUT
completions: the baseline getter, `[2450,2730]` setter, and full `[2500,2730]`
restore. The restore write began 0.142 ms after the setter write began. A
CRC-valid raw-`82` setter reply reached serial correlation; a CRC-valid
raw-`82` restore reply is retained in usbmon during close cancellation but was
not correlated by the application. No verification getter was sent.

This establishes host submission of the setter and restore, not application
acknowledgment or physical restoration. The operator separately reported no
visible movement, hearing the servo engage, then immediately powering Marvin
off and disconnecting host USB. That physical observation remains separate
from protocol evidence and does not prove channel mapping or restoration.

### Later 50-unit attempt stopped before the setter

A later authorized fixed word-0 50-unit attempt failed while processing the
baseline getter response, before any setter. The sealed evidence records one
10-byte accepted application submission, zero uncertain bytes, one submission,
14 serial RX bytes, no retained protocol candidate, and
`Identity validation exceeded deadline`. It also records
`nonzero_may_have_applied=false`, `restore_attempted=false`, and
`restoration=not_required_before_setter`. Therefore only the baseline getter
was submitted; the setter and restore were not submitted. The operator
confirmed Marvin OFF and no visible movement.

The scope probe was disconnected and false-triggered, so that capture is
invalid and supplies no waveform evidence. The evidence manifest verified;
its SHA-256 is
`60428ca1a3a183209d5da477b7ed140f52191f2e637a1ff8dc0ec26a9f8066cb`.
This failed attempt is not authorization to retry.

### Successful fixed word-0 50-unit run

A later separately authorized execution completed the full fixed profile with
status `front_camera_servo_word0_five_degree_complete_protocol_only`. The
sealed host evidence records 48 accepted and zero uncertain TX bytes across
four submissions. The baseline and final getters both returned
`[2500,2730]`. The `[2450,2730]` setter and `[2500,2730]` restore each had one
unique CRC-valid correlated empty raw-`82` response. Restore started
81.007 microseconds after setter start, within the 0.25-second bound;
`restore_correlated` and `getter_reverified` are both true, finalization errors
are empty, and usbmon completed.

That establishes protocol restoration to the aggregate baseline. Separately,
the operator observed the connected servo visibly move and return, then
physically powered Marvin off. The displacement was too small to classify.
The operator observation establishes physical servo movement and return for
this bounded decrement; it remains distinct from protocol evidence and does
not establish camera tilt, direction, calibrated angle, full range or generic
application ACK. No camera linkage was installed, so mechanical-frame
attribution is unavailable.

The passive J24 waveform contains the checksum-valid packet:

```text
FF FF 02 05 03 1E 30 03 A4
```

This is Protocol 1.0 WRITE to ID 2, Goal Position address `1E` hex, value
`0330` hex (`816`), checksum `A4`. It matches `2450 // 3 = 816`. Together
with the earlier `2490 -> 830` and `2500 -> 833` captures, this proves the
installed controller's positive tested-value truncating division by three and
the word-0/J24 connected-servo control path for this bounded decrement. The
later `FF FF` prefix is incomplete and unattributed.

The top-level manifest verified and has SHA-256
`43ee06c94cad5d89202b895bc9dad078efe2ecb8250e5a66775b83e115a0bfa8`.
The independently verified scope SHA-256 values are:

- waveform:
  `42c7be63a7bfc9b3c7f47bf10852e2dc5324a83445544316e37a000fc84e2e23`;
- BMP:
  `571b69c13e0251739fd5937c3da0ddb7e9f4fafc0345b569602c65a28fe5ca26`;
- PNG:
  `eb6137b79d0b998e56d85bd375b11c4279b9eae62462e4dd10dadd363f744b52`.

No further hardware action is authorized by this result.

### Offline-only word-0 direction diagnostic

The older profiles' `0.250` value is a maximum setter-start-to-restore-prewrite
deadline, not an actual dwell. The successful run restored only 81.007
microseconds after setter start, which explains why movement and return were
visible but direction was too small to classify.

The separately named offline-default direction profile reuses exactly the
proved `[2500,2730] -> [2450,2730] -> [2500,2730]` transcript. Its hold starts
at the end timestamp of one clean correlated setter response. It performs a
full identity check after that response, captures throughout the hold, and
targets restore exactly 0.250 seconds after the boundary. Restore start may be
at most 0.010 seconds late. Because setter correlation itself has a 0.500-second
bound, the maximum setter-start-to-restore-start exposure is 0.760 seconds. A
scheduling overrun fails the run and still makes the one mandatory restore
attempt. Interruption or any hold error skips the remaining hold and enters
that restore path immediately. Verification remains conditional on one unique
clean correlated restore response.

Dry run:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word0-direction-diagnostic
```

Reviewed live template for a future separately authorized execution:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word0-direction-diagnostic \
  --run --expected-physical-port REVIEWED-PORT --output NEW-SESSION-DIR \
  --authorize-unchanged-setup-session-after-fresh-safety-confirmation
```

The immutable requests are:

```text
53b40d1d000000531c45
53b50d1e0004009209aa0ac2cb45
53b60d1e000400c409aa0a234c45
53b70d1d000000532f45
```

Their concatenated SHA-256 is
`64efc52b59c6741af201718ec39b48e1475e40ae3630c4e3cafa510f52a3d92b`.
The combined authorization is one fresh human confirmation that Marvin is
powered on and startup is complete; the correct AX-12+ actuator/linkage is at
J24; the projector servo is disconnected; the fixed-profile motion envelope
is clear and hands are clear; host USB is connected on the reviewed pinned
port; and the independent cutoff is ready. The process then accepts only the
exact operator commands `RUN` and `END`. Each `RUN` deliberately triggers one
fixed transcript and writes sealed evidence under
`NEW-SESSION-DIR/run-NNNN`; there is no automatic retransmission, unattended
count or automatic trigger. `END`, EOF or interruption closes the
authorization, and the operator must immediately power Marvin off.

A completed run leaves the setup-scoped process waiting for another deliberate
`RUN` only while the physical setup is unchanged. Any identity change,
transport loss, uncertain or partial write, restore or verification failure,
cleanup failure, interruption, or setup change ends the process and requires
fresh confirmation. A failure before a proven application write is not
automatically retried; the conservative implementation also ends the session,
so the permitted fresh operator-trigger exception is not exercised. This
profile does not require passive scope evidence, so it has no scope or probe
declaration. Automatic USB/tty identity checks, the immutable transcript,
bounded hold, mandatory `finally` restore, post-restore verification, evidence
capture and cleanup remain machine enforced. The CLI exposes no larger delta,
arbitrary target/dwell/range, retry, reconnect, calibration, reset or power
command. This invocation is not standing authorization.

The one separately authorized execution completed with status
`front_camera_servo_word0_direction_complete_protocol_only`. It retained 48
accepted and zero uncertain TX bytes across four submissions. The requested
hold was 0.250 seconds; actual hold from correlated setter response end to
restore start was 0.250105888 seconds, and setter-to-restore start was
0.267635286 seconds. The restore had one unique correlated raw-`82`
empty-payload response, and the final getter returned `[2500,2730]`.
Protocol restoration is established.

That sealed execution used the former literal
`--operator-confirmed-word0-five-degree-direction-observation-clearance`;
the transcript and evidence are unchanged. The future-facing literal above
was renamed only to remove the disproved calibration claim.

The operator independently observed approximately 0.5 degrees of servo
movement and return while decreasing word 0 from 2500 to 2450, and described
the motion as downward in the test frame. No camera linkage was installed, so
that description does not establish camera upward/downward tilt and cannot be
reconciled to a servo-output clockwise/counterclockwise direction without a
defined viewing frame. The displacement corrects the prior assumed
five-degree label by roughly 10x for this one bounded observation, but is
operator-observed approximate servo calibration only: it does not prove
precision, linearity, endpoint behavior or the full range. Marvin physical
power-off was confirmed.

The sealed run manifest verified and has SHA-256
`61bf0a5baa9c6cc9fd74bc9a4d54c931a184aca480ab64db19e5ea19ad29c068`.
No further hardware action is authorized by this result.

### Word-0 100-unit characterization

The next named profile reuses the validated direction-profile machinery with
one fixed larger decrement only:

```text
baseline [2500,2730]
setter   [2400,2730]
restore  [2500,2730]
verify   GetServoPosition
```

The hold starts at clean correlated setter-response end, requests 0.250
seconds, permits at most 0.010 seconds of scheduling overrun, and preserves the
0.760-second maximum setter-start-to-restore-start exposure. Full identity
checks precede setter and restore. Once the setter may have applied, every exit
uses the same one-attempt restore from `finally`; interruption or overrun skips
the remaining hold and restores immediately. Final getter verification remains
conditional on a uniquely correlated restore response.

Dry run:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word0-100-unit-direction-diagnostic
```

Reviewed live template for a future separately authorized execution:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word0-100-unit-direction-diagnostic \
  --run --expected-physical-port REVIEWED-PORT --output NEW-SESSION-DIR \
  --authorize-unchanged-setup-session-after-fresh-safety-confirmation
```

The single authorization has the same fresh-confirmation semantics described
for the fixed 50-unit profile above; no passive scope or probe declaration is
required.

The immutable requests are:

```text
53be0d1d00000053b645
53bf0d1e0004006009aa0ad05345
53c00d1e000400c409aa0ac17845
53c10d1d000000587945
```

Their concatenated SHA-256 is
`08003ccf126ca7479ef7857e686272e6e1e40a321ccf129e5952732a6e8911fa`.
If the proved positive integer division continues at this input, expected AX
Goal Position is `2400 // 3 = 800`, 33 counts below observed baseline 833
(approximately 9.7 actuator degrees under AX-12 units). Before execution,
extrapolating the operator-observed 50-unit servo displacement suggested
roughly 1 degree of servo-output movement, but that was only a characterization
hypothesis, not a promise, precision calibration, linearity claim or full-range
proof.

The CLI exposes no arbitrary values, larger delta, timing control, retry,
reconnect, calibration, reset or power command.

One separately authorized execution completed with status
`front_camera_servo_word0_100_unit_complete_protocol_only`. It retained 48
accepted and zero uncertain TX bytes across four submissions. Actual hold from
clean correlated setter-response end to restore start was `0.250208296`
seconds, and setter-to-restore start was `0.269347429` seconds. Restore had one
unique correlated raw-`82` empty-payload response, and the final getter
returned `[2500,2730]`. Protocol restoration is established.

The operator observed that decreasing word 0 from 2500 to 2400 rotated the
servo output approximately 1 degree clockwise in the observed test frame,
then returned it. No camera linkage was installed, so this establishes only
operator-observed servo-output direction and approximate calibration for that
setup: -100 legacy units was approximately 1 degree clockwise. The earlier
50-unit “downward” description used a different, undefined mechanical frame
and is therefore retained only as an ambiguous operator description, not a
contradictory camera-direction result. Marvin physical power-off was confirmed.

After that sealed run, the operator installed the camera linkage and separately
established that clockwise servo-output rotation tilts the installed camera
**upward**. Composing those two operator observations establishes the tested
`2500 -> 2400` decrement as upward camera tilt in the now-installed linkage,
without changing the historical fact that the live run itself used an
unattached servo. Increasing word 0 is expected to tilt downward only as the
inferred inverse; it has not been directly exercised with the installed
linkage. These observations do not prove precision, linearity, endpoint
behavior or full range.

The sealed run manifest verified and has SHA-256
`556b0225ded01a7a0f313a80bb34d077ff60a354f22167887c989315f2ce0499`.
This completed execution is not standing authorization, and no further
hardware action is authorized by it.

## Historically named word-1 50-unit diagnostic

The next discriminator is a separate named profile only. It preserves word 0
and changes `[2500,2730]` to `[2500,2680]`, then immediately makes the single
full `[2500,2730]` restore attempt within the same 0.25-second setter-to-restore
bound. Verification remains conditional on one clean correlated restore
response. It does not expose arbitrary word, target, delta, dwell, retry,
reconnect or resume controls.

The two one-degree runs' lack of visible motion and the first word-0
five-degree run's audible engagement without visible movement remain external
evidence only. The later successful fixed word-0 run independently established
bounded connected-servo movement and return plus complete protocol restoration,
but not camera tilt or a mechanically framed direction. It does not prove
word-1 routing.

Historically named word-1 50-unit dry run:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word1-five-degree-diagnostic
```

Historical exact word-1 five-degree live invocation:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word1-five-degree-diagnostic \
  --run --expected-physical-port REVIEWED-PORT --output NEWDIR \
  --operator-present --robot-secured \
  --independent-actuator-cutoff-ready \
  --drive-and-other-actuators-inactive \
  --front-camera-tilt-only-connected-projector-servo-physically-disconnected \
  --front-camera-servo-is-ax12-plus \
  --operator-confirmed-word1-five-degree-mechanical-clearance \
  --exact-profile-baseline-2500-2730-target-2500-2680-word1-five-degree-dwell-0-25-seconds \
  --authorize-single-legacy-1e-front-camera-word1-five-degree-diagnostic-command \
  --unprivileged-usbmon
```

This invocation is historical evidence only and is not authorization to
execute it.

### Corrected-cable word-1 result and startup observation

The corrected-cable word-1 five-degree run was protocol-clean: the baseline,
restore and final getter were `[2500,2730]`; setter and restore each returned
raw `82`; restore prewrite began 0.141 ms after setter prewrite; accepted TX
was complete with zero uncertain bytes and zero usbmon drops. The operator
reported no visible movement and no sound.

Separately, with Marvin OFF, the operator manually displaced the connected
front-camera tilt actuator. On the next power-on it returned to its zero
position. This is external physical evidence that the startup actuation path
can energize and reposition the mechanism. It does not establish J24 pin
functions, live voltage or bus state, actuator ID or baud, packet contents,
controller command, runtime `1E` behavior, or the meaning of either getter
word. Neither observation is protocol proof or channel proof.

Later inspection found explicit `Dynamixel AX-12+` and `www.robotis.com`
markings on the actuator body. This establishes the actuator model identity.
Its harness was operator-traced to `J24/SERVO2`. A later all-power-removed
test, with both harness ends unplugged, established three independent
straight-through conductors: green-to-green, red-to-red and black-to-black.
Every cross-pair was open/OL. This establishes that the apparent fourth J24
conductor is not a fourth conductor in the tested harness; the visual source
of that appearance remains unresolved. It does not assign any conductor
function. A subsequent all-power-off board-side test independently verified
the J24 black contact as controller ground/return. Red showed a
polarity-dependent semiconductor path to the labelled `+12V`/servo-power
point. A later bounded, actuator-disconnected power-on measurement established
steady 9 V DC on red and steady 5 V DC on green relative to verified black
ground in that exact state. Red is therefore verified actuator supply under
that state, though its upstream circuit remains unknown. Green is a verified
5 V idle-high logic candidate, strongly consistent with AX-12+ TTL
half-duplex DATA, but no transition or packet was captured. The model marking
makes AX-12+ electrical and protocol documentation directly relevant, but
does not prove J24 traffic or legacy command translation.

### Offline semantic assessment

The complete historical PCTestApp command inventory contains one
`setServoPosition_btn_Click` call site (`Form1.cs:749-772`) and one
`getServoPosition_btn_Click` call site (`Form1.cs:778`). The setter directly
sends command `1E` with two parsed `uint16` values; the getter directly sends
empty command `1D`. There is no adjacent apply/update/enable command, servo
timer, cadence, retry, startup setter, cleanup, or command-specific response
parser. The sample's heartbeat control is a separate `26 DisableHeartbeat`
button, not part of the servo event path.

This host source establishes intended standalone runtime get/set UI behavior,
not installed controller behavior. Command `05 SetConfig` is a separate
108-byte configuration write, while `1E` is four bytes, so the sample does not
present `1E` as the stored configuration operation. However, the matching
PCTestApp-era S/E controller handler is absent from all supplied source and
159 reviewed firmware artifacts. No retained image simultaneously matches
installed `53`/`45` framing, command layout, identity and response shapes.
Therefore the installed `1E` handler could still apply a live target, update a
cached target, reject or ignore it, or depend on a controller loop that is not
represented in available artifacts.

The corrected run does not distinguish those cases because its full restore
started only 0.141 ms after the setter. No source establishes that the missing
handler can emit a downstream servo packet synchronously inside that interval,
and no target-state getter was taken before restore. The earlier word-0 run's
audible engagement without visible motion remains external evidence only.

For the identified actuator, the authoritative
[AX-12+ control table](https://emanual.robotis.com/docs/en/dxl/ax/ax-12a/)
excludes a direct legacy wire-value assumption. Native Goal Position is a
volatile two-byte value `0..1023` at approximately `0.29` degrees per unit;
Torque Enable at address 24 is volatile and defaults OFF; ID and baud are
EEPROM settings; and communication uses DYNAMIXEL Protocol 1.0 half-duplex
packets. The legacy UI's `0..3000` values therefore require an unproved
controller-side conversion if they are live positions. No matching Marvin
artifact contains AX-12 packet generation, servo IDs, bus baud,
torque-enable writes, goal-position writes, or startup-zero logic.

No further host servo command is justified by this evidence. The minimum
offline next step is recovery of the matching PCTestApp-era S/E firmware or
source containing the `1D`/`1E` handlers and downstream actuator task. If a
later separately reviewed physical investigation is still needed, the
remaining discriminator is passive activity capture on the now-identified
green candidate using independently qualified instrumentation. This document
does not authorize that capture.

## Passive actuator startup-interface capture plan

This section is an offline procedure draft, not authorization to connect an
instrument or power Marvin. It preserves the decision that no further live
legacy `1E` delta is justified.

### Established topology and blocker

Six later operator-supplied photos show the connected front-camera mechanism
and controller header `J24`, silkscreened `SERVO2`; adjacent `J3` is
silkscreened `SERVO1`. The operator reports continuously tracing that exact
actuator, with no hidden splice or branch, to J24. A later close inspection
found explicit `Dynamixel AX-12+` and `www.robotis.com` body markings. This
establishes the actuator model and physical harness destination as
operator/photo evidence. It does not identify J24 contact functions or
controller circuitry.

With battery, charger, host USB and all external power disconnected, and both
the actuator-end and J24-end connectors unplugged, the operator verified:
green-to-green continuity only, red-to-red only and black-to-black only. All
green/red, green/black and red/black cross-pairs were open/OL. This establishes
a three-conductor, point-to-point harness with no measured conductor-to-
conductor short. It does not establish supply, ground, command, data, feedback
or motor drive; color names are identifiers only.

With the same sources disconnected and J24 unplugged, a second test used an
independently identified, clearly labelled controller `GND`/`0V` point and a
clearly labelled `+12V`/servo-power point:

- J24 black to controller `GND`/`0V` was near zero ohms with continuity;
  J24 red and green to that point were open/OL.
- J24 red to the labelled `+12V`/servo-power point measured approximately
  500 ohms in one resistance-probe orientation and open/OL when reversed; the
  resistance-mode lead orientation was not retained.
- In diode mode, red meter lead on the labelled `+12V`/servo-power point and
  black meter lead on J24 red measured `0.500 V`; reversing those exact leads
  measured open/OL.
- J24 green to the same labelled `+12V`/servo-power point was open/OL.

The actuator-end keyed plug, viewed into its mating openings with key/latch up,
is black, red, green from left to right; no molded contact numbers are visible.
These measurements independently verify black as controller ground/return.
The asymmetric red-to-rail result establishes a unidirectional semiconductor
path, not a direct `+12V` rail, its circuit topology, or a live voltage. Green
is the remaining AX-12+ DATA candidate by elimination and the vendor interface
model, but has not been electrically identified as DATA.

A later measurement used a fresh safety gate: robot mechanically secured,
actuators and wheels clear, J24 unplugged, loose contacts insulated, meter
black lead clipped to the independently labelled controller `GND` before
power, and host USB disconnected. No host command was issued. The operator
physically powered Marvin on and observed, relative to verified ground:

- J24 red: steady `9 V DC`;
- J24 green: steady `5 V DC`.

The operator then physically powered Marvin off. Both readings fell to
approximately `0 V`, and J24 remained unplugged. This verifies red as the 9 V
actuator supply under that exact powered state. It verifies green as a 5 V
idle-high logic candidate and is strongly consistent with the AX-12+ TTL
half-duplex DATA conductor. A steady meter reading captures no edge or frame,
so it does not establish packet activity, baud, ID, transmitter direction,
logic thresholds, transient envelope, or any relationship to legacy `1D`/`1E`.

A subsequent passive startup capture used a separately confirmed gate:
mechanically secured robot; J24 and the AX-12+ unplugged; host USB
disconnected; ADS1013D on internal battery only with USB/charger disconnected;
10x probe ground attached to independently verified controller ground and the
probe tip attached only to J24 green. The operator armed one Single capture,
physically powered Marvin on, then physically powered it off. No host command
was issued.

The `20 us/div` scope image reports `T = 99.6 us` between equivalent falling
edges. Treating these as consecutive UART byte starts gives approximately
`100.4 kbaud` (`10 / 99.6 us`). Offline extraction of the available
proprietary sample window decodes:

```text
FF FF 02 05 03 22 50 01 ...
```

This is a DYNAMIXEL Protocol 1.0 instruction prefix addressed to ID 2:
`LENGTH=5`, `WRITE=03`, RAM address `22` hex (`34`, Torque Limit), and
little-endian value `0150` hex (`336`). The preceding observed bytes imply
checksum `82` hex, because
`~(02 + 05 + 03 + 22 + 50 + 01) & FF = 82`; the exported sample window
truncates the checksum's upper bits, so `82` is inferred, not observed.

Because the AX-12+ was unplugged, all captured traffic is controller-originated
and there can be no actuator status response in this capture. The observed
bytes and timing directly establish startup traffic on green that decodes as
a DYNAMIXEL Protocol 1.0 WRITE prefix, with controller target ID 2 and
approximately 100 kbaud. The missing checksum prevents classifying it as a
complete checksum-validated packet. It does not independently read the
actuator's EEPROM ID, show actuator acceptance, reveal other startup packets,
or connect this traffic to legacy `1D`/`1E`.

A separately gated, one-second passive startup recapture under the same
electrical and no-command conditions independently reproduced the same eight
complete exported bytes:

```text
FF FF 02 05 03 22 50 01
```

The ADS1013D proprietary export retains only a centered 1,500-sample CH1
window. Moving the on-screen trigger did not move that retained window far
enough to preserve the checksum byte. The reproduction strengthens the byte
decode and startup-repeatability evidence, but does not convert inferred
checksum `82` into an observation. No further repeat is justified.

The preserved evidence remains outside the repository in
`servo2-startup-capture`. SHA-256:

- proprietary ADS1013D waveform:
  `f645b975b3c85931dfae5ca0a606840f707a42007809a79aa9a22aa0a58d4dc2`;
- `startup-100us-div.jpg`:
  `71ece6c25848fa3115d4aa7df95a0096ef0ee2446e343e40904dbad999e317a2`;
- `startup-20us-div.jpg`:
  `e4e69c5d6d611149cedbb558c4aa6c475eeac98ac415c79e79adb6961259caf3`;
- recapture proprietary ADS1013D waveform:
  `6599c77ba6c81d561cfe3f8f7e28f659ef9636cab4cb41ad6160727f7ef5fda6`;
- recapture scope BMP:
  `b9926accf845127fcec3fabbea0564540f84df46345e4ccfaac56f9332ff6342`;
- `README.txt`:
  `ca91f2479a9a7d0fa127eb532cb2bc64dcf2410ee6d7d96628800ee524763557`.

A later separately gated connected-bus capture inserted the original harness
into one keyed AX-12+ port and an insulated spare AX cable into the other.
Power-off continuity through the actuator was straight-through
green-to-green, red-to-red and black-to-black only. With host USB and charger
disconnected, connectors seated, spare red insulated, the ADS1013D on battery
only, ground on spare black and the probe tip only on spare green, the
operator performed one `200 us/div` Single capture during physical power-on,
then powered Marvin off after STOP. No host command was issued and there was
no abort condition. The export contains the full checksum-valid frame:

```text
FF FF 02 05 03 22 50 01 82
```

This is a complete Protocol 1.0 WRITE to ID 2, `LENGTH=5`,
`INSTRUCTION=03`, RAM Torque Limit address `22` hex, little-endian value
`0150` hex (`336`), checksum `82`. After an idle interval another `FF FF`
header begins, but the one-channel half-duplex capture cannot attribute its
transmitter or decode the remaining bytes. It is consistent with either a
status response or a next controller instruction; it is not evidence of an
actuator reply. The complete controller instruction still does not establish
actuator acceptance, the actuator's independently read EEPROM ID, or any
legacy `1D`/`1E` translation.

Connected-capture SHA-256:

- proprietary ADS1013D waveform:
  `56e2de78b8b5985a9b224fa25a0163108b16f1ffc35d4b6e5827835e164f9842`;
- scope BMP:
  `941149ccb6932f59c51ab51fcf35ef72c93ab6b0cf13303426a32e87c6dfafff`;
- converted PNG:
  `32c88cce05c9f8f12a811972e32167e687f2c199e446384e6894d727a5021a01`.

The authoritative
[ROBOTIS AX-12+ manual](https://emanual.robotis.com/docs/en/dxl/ax/ax-12a/)
specifies a `9.0..12.0 V` input (`11.1 V` recommended), digital packets and a
TTL-level multidrop half-duplex asynchronous serial connection using 8 data
bits, one stop bit and no parity. The marked actuator and verified
three-conductor harness therefore make supply, return and a Protocol 1.0
half-duplex DATA path the source-backed interface expectation. They do not
alone assign those roles to colors or J24 contacts. The independent board-side
measurements now establish black as return and red as a 9 V supply in the
observed state. The passive startup capture establishes green as the
controller's output carrying a Protocol 1.0-compatible WRITE prefix in that
state. ROBOTIS explicitly warns users to verify the pinout on both the
actuator and board because connector pinout may vary by connector
manufacturer.

The powered return to zero is consistent with a controller communicating with
and enabling the AX-12+, especially because Torque Enable defaults OFF after
power-on. It does not reveal the instruction sequence or prove that legacy
`1E` caused any downstream packet. The remaining unknowns are red's upstream
supply-path circuitry, green's half-duplex driver details and transient
envelope, the actuator's independently confirmed EEPROM ID, configured return
delay, any other startup packets, controller conversion from legacy
`0..3000`, and installed `1D`/`1E` handler semantics.

Ten earlier operator-supplied local photos were reviewed offline. They show the main
controller board and harnesses labelled for proximity/cliff sensors, ring,
speaker, encoder, PC control, power/battery, USB cameras and PC front-panel
functions. They did not identify the front-camera servo connector. The six
later photos and disconnected continuity tests identify J24/SERVO2, both
harness endpoints, three independent conductors and black as controller
ground/return. The bounded voltage observation additionally identifies red as
9 V supply, and the passive startup capture identifies green as the controller
output carrying a Protocol 1.0-compatible WRITE prefix under the recorded
state. A visible
six-position `SERIAL` footprint and other test points are not attributed to
the actuator interface and must not be used as probe points from appearance
alone. The photos are not committed.

The reviewed Yeapook ADS1013D photo proves only that this portable two-channel
scope is available. No local manual or isolation/common-mode specification was
available, and the channels appear to share a common reference. It must not be
treated as an isolated differential probe or connected across two unknown
nodes. Battery operation alone does not establish channel-to-channel or
input-to-USB/charger isolation.

J24 evidence establishes black as local reference, red as 9 V actuator supply
and green as the controller output carrying Protocol 1.0 traffic. Two
actuator-disconnected captures preserve the same incomplete WRITE prefix; the
connected-bus capture preserves the complete checksum-valid controller WRITE.
None establishes an actuator response, the full startup transcript, or the
signal's transient envelope. No further repeat is justified. Any different
capture requires a new review rather than treating this result as standing
authorization.

The AX-12+ connector diagram is now relevant to the marked actuator, but is
not by itself sufficient to assign the J24 contacts. Follow ROBOTIS's warning
to verify both actuator and board pinouts. The retained measurements verify black reference and red 9 V supply in the
recorded state; passive evidence includes a complete checksum-valid
controller-originated Protocol 1.0 WRITE on green. Do not attach capture
equipment again without a
separate review of the driver path, transient envelope, safe measurement point
and specific evidence objective.

### Non-driving instrument boundary

No powered observation method can be selected until the interface is
classified and its reference, normal range, maximum possible voltage and
transient envelope are established. A rated isolated differential probe into
a battery-powered scope is the only candidate for the first bounded
characterization. A high-impedance receive-only buffer or logic analyzer is a
later option only for a proven compatible digital signal.

Do not use an earth-grounded bench-scope ground clip. Do not connect a
battery-powered instrument to a charging cable, host USB or another grounded
instrument during capture. Do not enable analyzer pull-ups, open-drain output,
pattern generation, protocol transmission or automatic voltage injection.
Avoid bidirectional level shifters because their pull-ups and direction
behavior can alter the bus.

Electrical qualification is staged. The first powered attachment may use
only an isolated high-impedance differential probe independently rated above
the reviewed source's maximum possible voltage and common-mode/transient
envelope. The exact pair measured depends on the completed classification; do
not assume DATA-to-ground. That characterization requires its own bounded
operator approval. Only afterward may an analyzer, attenuator or receive-only
buffer be selected by comparing its input rating, thresholds, leakage and
loading with the retained envelope. “TTL” is not permission to assume a 5
V-safe probe or a particular logic threshold.

If classification proves a single-ended digital command/data signal and local
reference, a floating battery scope or logic analyzer may observe it passively
only when its input is genuinely high-impedance and receive-only, its ratings
exceed the retained envelope, and it remains electrically floating except for
that local reference. Pulse, analog, differential or motor-drive findings
require a new reviewed measurement branch; this procedure intentionally does
not improvise one.

### Requirements for any additional capture

The completed capture is not standing authorization. Any additional capture
must be separately reviewed and should preserve raw edges rather than trusting
one UART decoder:

- arm before the separately controlled Marvin power-on;
- retain at least 100 ms before the first DATA transition and stop the capture
  no later than 10 s after power-on; if motion or bus activity continues at the
  limit, remove power rather than extending or retrying;
- use a falling-edge DATA trigger if the verified idle state is high;
  otherwise use an instrument-supported pulse/activity trigger and preserve
  the pre-trigger record;
- sample at least 20 MS/s with the largest available memory that covers the
  window; preserve the native waveform/export, instrument settings and clock
  accuracy before decoding;
- do not probe the servo supply rail in the first capture. A second channel is
  allowed only after its separate point and voltage rating are established.

Preserve undecoded timing and derive candidate symbol periods from repeated
edges. The retained startup instruction measures approximately 100 kbaud,
which is not one of the common settings listed below and should not be rounded
to another rate. For offline comparison only, the AX-12+ manual lists `1,000,000`
(factory default), `500,000`, `400,000`, `250,000`, `200,000`, `115,200`,
`57,600`, `19,200` and `9,600` bit/s. Do not configure or transmit any of
them. Accept a decode only when multiple complete frames have consistent
timing, lengths and checksums; a few plausible bytes are insufficient.

Any future actuator-connected capture must inject no host USB/serial command
and requires a reviewed starting-pose photo and mechanical plan showing an
unobstructed envelope for the complete plausible travel, a secured mechanism,
exclusion zone and independent cutoff threshold. Any startup motion outside
that envelope requires immediate cutoff; motion continuing after the one
expected return or approaching a stop is not allowed to run to the capture
deadline. If repeating the visible return-to-zero is later judged necessary,
the separate physical plan must additionally bound the power-OFF manual
displacement; this document does not authorize it.

### AX-12+ offline decode expectations

The marked model makes the authoritative
[DYNAMIXEL Protocol 1.0](https://emanual.robotis.com/docs/en/dxl/protocol1/)
format directly relevant, but this is an offline interpretation guide, not a
powered capture authorization. An instruction packet is:

```text
FF FF ID LENGTH INSTRUCTION PARAMETER... CHECKSUM
```

A status packet is:

```text
FF FF ID LENGTH ERROR PARAMETER... CHECKSUM
```

`LENGTH` is parameter count plus two. The checksum is the low-byte one's
complement of `ID + LENGTH + INSTRUCTION/ERROR + parameters`. Relevant
control-table fields are ID at address 3, baud at 4, Return Delay Time at 5,
Torque Enable at 24, Goal Position at 30 and Present Position at 36. Goal and
present position use little-endian values `0..1023`; the legacy `0..3000`
words are not native AX-12+ positions.

Retain every candidate frame with timestamps and checksum result. Multiple
timing-consistent, checksum-valid frames can establish Protocol 1.0-compatible
traffic and observed packet IDs and fields. A target ID in an instruction,
especially broadcast ID `FE`, does not establish the actuator's configured ID;
that requires an attributable status response or verified read of address 3.
A single-wire capture cannot itself prove which physical endpoint drove each
frame; instruction/status structure and turnaround only support attribution.
Broadcast ID `FE` may produce no status reply, and configured Status Return
Level may suppress write replies. None of those cases establishes how
installed command `1E` maps to the AX-12+.

For the retained startup capture, the addressed WRITE prefix and UART timing
establish controller-originated traffic that decodes as Protocol 1.0 even
though the proprietary export truncates the expected checksum byte. It is not
a complete checksum-validated packet and is labelled accordingly. Addressed
ID 2 is the controller's startup target ID, not an independent read of
actuator EEPROM address 3.

### Bounded operator checklist

Before any future attachment, a separately authorized operator must confirm:

1. Marvin, actuator power and host USB are OFF/disconnected; stored-energy
   handling and independent cutoff are defined.
2. The reviewed plan explicitly declares whether the intended front-camera
   actuator is disconnected or connected; projector and other actuators remain
   physically isolated as required by that setup.
3. The verified keyed conductor map, black ground/return, red 9 V supply and
   green 5 V idle-high observation are recorded; the expanded board-side map
   establishes green's driver and a reviewed measurement pair without relying
   on color alone; the probe point cannot short adjacent conductors.
4. The separately approved isolated differential-probe stage has retained the
   signal envelope. Capture-instrument ratings, thresholds and isolation are
   reviewed against it; all transmitters, pull-ups and output modes are
   disabled.
5. The probe is attached while power is absent, mechanically strain-relieved,
   and inspected for shorts or contact with adjacent conductors.
6. The starting pose, complete plausible travel envelope, fixture, exclusion
   zone and independent cutoff threshold have separate operator/reviewer
   approval.
7. Capture is armed before power-on. No robot USB/serial command is issued.
8. The operator watches the mechanism and supply continuously with immediate
   independent cutoff access. After the bounded capture, power is removed
   before probes are detached.
9. Raw waveform, setup screenshots/settings, operator observations and exact
   wiring photos are retained separately; decoding never replaces the raw
   capture.

Abort before power-on for any unknown pin, missing rating, non-floating
instrument, missing electrical-envelope record, enabled output/pull-up, exposed
short risk, unreviewed starting pose/travel envelope or inability to secure the
mechanism. Cut power immediately for motion outside the reviewed envelope,
motion continuing after the single expected return, approach to a hard stop,
collision, repeated hunting, abnormal sound beyond the already reported
startup action, heat, odor, smoke, LED fault pattern, supply anomaly, probe
upset or loss of capture/operator control. Do not retry in the same session,
change baud, transmit a packet, or follow with a legacy `1E` command.
