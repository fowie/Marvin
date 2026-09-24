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

## Fixed front-camera live mapper

`tools.marvin_legacy_front_servo_mapper` is the only live-capable legacy servo
tool. It is still offline by default and exposes no target, delta, range,
dwell, word, command, stop, calibration, reset, flash or power-state option.
Its historical reviewed profile was authorized under an operator declaration
that the one physically connected front-camera actuator was AX-12+, with the
projector servo disconnected: getter baseline
`[2500,2730]`, one setter `[2490,2730]`, at most 0.25 seconds of observation,
one complete `[2500,2730]` restore attempt, then one verification getter only
after a correlated restore response. Raw response status is retained as an
observed field and is not called generic success. Protocol agreement never
proves physical motion or restoration.

Dry run:

```sh
python -m tools.marvin_legacy_front_servo_mapper
```

The live recipes below are retained only as historical records of the executed
profiles. **They are retired and must not be reused.** A later inspection of
the actuator body established AX-12+ model identity, but it did not establish
the legacy UI's angular conversion, J24 controller implementation or runtime
`1E` semantics. No further live legacy `1E` delta is authorized.

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
evidence, not proof of channel assignment. A separate named word-1 hypothesis
changes only `[2500,2730]` to `[2500,2720]`; it cannot accept arbitrary words
or values.

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

A separate fixed word-0 five-degree diagnostic changes only `[2500,2730]` to
`[2450,2730]`. Fifty legacy UI units was treated as five degrees under the
then-declared AX-12+ `0..3000` to `0..300` scale and remains inside that UI
range. The later photos invalidate using that scale as installed-actuator
evidence. Neither
the scale nor the two negative observations establishes mechanical safety, so
the mode requires a separate literal operator clearance confirmation.

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

### First five-degree run evidence

The first authorized five-degree execution ended in a software evidence
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

## Fixed word-1 five-degree diagnostic

The next discriminator is a separate named profile only. It preserves word 0
and changes `[2500,2730]` to `[2500,2680]`, then immediately makes the single
full `[2500,2730]` restore attempt within the same 0.25-second setter-to-restore
bound. Verification remains conditional on one clean correlated restore
response. It does not expose arbitrary word, target, delta, dwell, retry,
reconnect or resume controls.

The two one-degree runs' lack of visible motion and the word-0 five-degree
run's audible engagement without visible motion are external evidence only,
not channel proof. For the word-0 five-degree run, setter and restore host
submission are established, but restore application correlation and physical
restoration remain unproved.

Word-1 five-degree dry run:

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

The preserved evidence remains outside the repository in
`servo2-startup-capture`. SHA-256:

- proprietary ADS1013D waveform:
  `f645b975b3c85931dfae5ca0a606840f707a42007809a79aa9a22aa0a58d4dc2`;
- `startup-100us-div.jpg`:
  `71ece6c25848fa3115d4aa7df95a0096ef0ee2446e343e40904dbad999e317a2`;
- `startup-20us-div.jpg`:
  `e4e69c5d6d611149cedbb558c4aa6c475eeac98ac415c79e79adb6961259caf3`;
- `README.txt`:
  `ee3352eb2447b00309b2a2fd7406ef220653c35cf0886473e37c83c38ae05d94`.

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
and green as the controller output carrying a Protocol 1.0-compatible WRITE
prefix in the recorded startup state. The retained sample is bounded and
incomplete; it does not establish a complete checksum-valid packet, the full
startup transcript, actuator responses, or the signal's transient envelope.
Any additional capture requires a new review rather than treating this result
as standing authorization.

The AX-12+ connector diagram is now relevant to the marked actuator, but is
not by itself sufficient to assign the J24 contacts. Follow ROBOTIS's warning
to verify both actuator and board pinouts. The retained measurements verify black reference and red 9 V supply in the
recorded state; the passive sample verifies controller-originated Protocol 1.0
compatible traffic on green. Do not attach capture equipment again without a
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
