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
Its reviewed profile is fixed to one physically connected AX-12+ front-camera
tilt servo with the projector servo disconnected: getter baseline
`[2500,2730]`, one setter `[2490,2730]`, at most 0.25 seconds of observation,
one complete `[2500,2730]` restore attempt, then one verification getter only
after a correlated restore response. Raw response status is retained as an
observed field and is not called generic success. Protocol agreement never
proves physical motion or restoration.

Dry run:

```sh
python -m tools.marvin_legacy_front_servo_mapper
```

The exact live invocation is intentionally verbose:

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

This command is documentation only. Do not run it without a separately
reviewed physical test plan and fresh operator authorization.

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

Exact word-1 hypothesis live invocation:

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
`[2450,2730]`. Fifty legacy UI units equals five degrees under the declared
AX-12+ `0..3000` to `0..300` scale and remains inside that UI range. Neither
the scale nor the two negative observations establishes mechanical safety, so
the mode requires a separate literal operator clearance confirmation.

Five-degree dry run:

```sh
python3 -m tools.marvin_legacy_front_servo_mapper \
  --word0-five-degree-diagnostic
```

Exact five-degree live invocation:

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

Exact word-1 five-degree live invocation:

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

This invocation is documentation only and is not authorization to execute it.

### Corrected-cable word-1 result and startup observation

The corrected-cable word-1 five-degree run was protocol-clean: the baseline,
restore and final getter were `[2500,2730]`; setter and restore each returned
raw `82`; restore prewrite began 0.141 ms after setter prewrite; accepted TX
was complete with zero uncertain bytes and zero usbmon drops. The operator
reported no visible movement and no sound.

Separately, with Marvin OFF, the operator manually displaced the connected
AX-12+ front-camera tilt servo. On the next power-on it returned to its zero
position. This is external physical evidence that startup can energize and
communicate with the downstream servo path. It does not identify the AX-12+
ID, baud, packets, controller command, runtime `1E` behavior, or the meaning
of either getter word. Neither observation is protocol proof or channel proof.

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

The
[AX-12+ vendor control table](https://emanual.robotis.com/docs/en/dxl/ax/ax-12a/)
also excludes a direct wire-value assumption:
native Goal Position is a volatile two-byte value `0..1023` for `0..300`
degrees, Torque Enable is volatile and defaults OFF, ID and baud are EEPROM
settings, and communication uses DYNAMIXEL Protocol 1.0 half-duplex packets.
The legacy UI's `0..3000` values therefore require an unproved controller-side
conversion if they are live positions. No matching artifact contains AX-12
packet generation, servo IDs, bus baud, torque-enable writes, goal-position
writes, or startup-zero logic.

No further host servo command is justified by this evidence. The minimum
offline next step is recovery of the matching PCTestApp-era S/E firmware or
source containing the `1D`/`1E` handlers and downstream servo task. If a later
separately reviewed physical investigation is still needed, the least
semantically invasive discriminator is passive, high-impedance capture of the
already-observed startup-zero downstream bus event, with no host command
injection. That would still require an explicit physical test plan and fresh
operator authorization.

## Passive AX-12 startup-bus capture plan

This section is an offline procedure draft, not authorization to connect an
instrument or power Marvin. It preserves the decision that no further live
legacy `1E` delta is justified.

### Established topology and blocker

The
[AX-12+ manual](https://emanual.robotis.com/docs/en/dxl/ax/ax-12a/)
specifies a TTL-level, multidrop, half-duplex asynchronous serial bus with
separate DATA, supply and ground conductors. DYNAMIXEL Protocol 1.0 uses one
DATA wire for controller instructions and servo status packets. The reported
startup return-to-zero makes a controller-to-servo path likely, but does not
establish Marvin connector pins, intermediate buffers or level conversion,
bus branches, signal voltage, pull-up arrangement, servo ID or baud.

No reviewed repository photo or wiring record identifies a safe Marvin probe
point. **That is the attachment blocker.** Before any capture plan can be
approved, supply:

- sharp photos of both sides of the controller connector area, the complete
  front-camera servo harness and all visible labels, with power OFF and USB
  disconnected;
- the connector pin count and keyed orientation, without assigning functions
  from wire color;
- an all-power-removed continuity map from each harness conductor to the
  AX-12+ actuator-side ground, DATA and supply contacts, plus confirmation that
  no continuity measurement caused the actuator to move or become powered;
- instrument make/model, input impedance/capacitance, maximum input and
  common-mode ratings, logic thresholds, isolation method and whether any USB,
  charger or earth connection exists during capture;
- the controller/servo supply source, its configured and maximum possible
  voltage, and a separate reviewed electrical-envelope measurement made with
  an independently qualified isolated differential probe.

Do not attach based on an AX-12 connector diagram alone: that diagram describes
the actuator, not Marvin's controller connector or harness routing.

### Non-driving instrument boundary

The preferred observation is a rated differential probe into a battery-powered
scope or a high-impedance receive-only buffer into a battery-powered logic
analyzer. Reference the measurement between the established bus DATA conductor
and the established bus ground at the same local connector. A logic analyzer
still needs that reference; “one-wire bus” does not mean ground-free.

Do not use an earth-grounded bench-scope ground clip. Do not connect a
battery-powered instrument to a charging cable, host USB or another grounded
instrument during capture. Do not enable analyzer pull-ups, open-drain output,
pattern generation, protocol transmission or automatic voltage injection.
Avoid bidirectional level shifters because their pull-ups and direction
behavior can alter the bus.

Electrical qualification is two-stage. The first powered attachment may use
only an isolated high-impedance differential probe independently rated above
the reviewed source's maximum possible voltage and common-mode/transient
envelope; it measures DATA-to-local-ground idle, high, low and peak voltages
without a logic analyzer attached. That characterization requires its own
bounded operator approval. Only afterward may an analyzer, attenuator or
receive-only buffer be selected by comparing its input rating, thresholds,
leakage and loading with the retained envelope. “TTL” is not permission to
assume a 5 V-safe probe or a particular logic threshold.

A battery scope or logic analyzer can observe the half-duplex DATA line
passively only when all of the following are true: its input is genuinely
high-impedance and receive-only; its DATA and ground connections are proved;
its normal and transient ratings exceed the retained bus envelope; and it
remains electrically floating except for the local bus reference. If those
facts are unavailable, do not connect it.

### Initial capture settings

Capture raw edges rather than trusting one UART decoder:

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

The AX-12+ manual documents 8N1 and these baud settings: 1,000,000 (factory
default), 500,000, 400,000, 250,000, 200,000, 115,200, 57,600, 19,200 and
9,600 bit/s. They are decode candidates, not claims about Marvin. Test each
candidate offline against the same raw capture. Accept a candidate only when
multiple complete packets have valid lengths and checksums; do not select a
baud merely because a few bytes look plausible. Preserve undecoded timing in
case Marvin uses another supported divisor.

The first physical capture should inject no host USB/serial command and should
not require manual displacement. It remains blocked until the operator supplies
a reviewed starting-pose photo and a mechanical plan showing an unobstructed
envelope for the complete plausible servo travel, a secured mechanism,
exclusion zone and an independent cutoff threshold. Any startup motion outside
that envelope requires immediate cutoff; motion continuing after the one
expected return or approaching a stop is not allowed to run to the capture
deadline. If repeating the visible return-to-zero is later judged necessary,
the separate physical plan must additionally bound the power-OFF manual
displacement; this document does not authorize it.

### Protocol 1.0 offline decoding

Per the
[DYNAMIXEL Protocol 1.0 specification](https://emanual.robotis.com/docs/en/dxl/protocol1/),
an instruction packet is:

```text
FF FF ID LENGTH INSTRUCTION PARAMETER... CHECKSUM
```

A status packet is:

```text
FF FF ID LENGTH ERROR PARAMETER... CHECKSUM
```

`LENGTH` equals parameter count plus two. For either packet:

```text
CHECKSUM = ~(ID + LENGTH + byte4 + every parameter) & FF
```

Decode and retain every packet, including malformed candidates, with start/end
times, baud hypothesis and checksum result. Relevant instruction patterns are:

- `03 WRITE`, address `18` hex (`24` decimal), one byte: Torque Enable;
- `03 WRITE`, address `1E` hex (`30` decimal), two little-endian bytes:
  Goal Position;
- `02 READ`, address `24` or `30`, followed by requested byte count;
- `04 REG_WRITE` followed later by `05 ACTION`;
- `83 SYNC_WRITE`, commonly broadcast ID `FE`, with start address, per-servo
  data width, then repeated servo ID/data groups.

The AX-12+ manual defines Torque Enable `0` as OFF and `1` as ON. It defines
native Goal Position as `0..1023` across `0..300` degrees; do not apply the
legacy UI's `0..3000` scale to captured AX-12 words. Record servo ID, target
word and packet timing without calling the target mechanically safe or
calibrated.

On a single DATA capture, transmitter identity is inferred, not electrically
observed. Controller-origin candidates contain defined instruction bytes;
servo status candidates place an error bitfield in byte 4 and normally follow
an addressed instruction after a short turnaround. Broadcast instructions may
produce no reply. Checksum, matching ID, request/return parameter lengths and
turnaround timing strengthen a pairing, but cannot prove which physical device
drove the wire. A second direction-enable or endpoint probe must not be added
without separately identifying and reviewing that point.

### Bounded operator checklist

Before attachment, a separately authorized operator must confirm:

1. Marvin, actuator power and host USB are OFF/disconnected; stored-energy
   handling and independent cutoff are defined.
2. Only the intended front-camera AX-12+ is connected; projector and other
   actuators remain physically isolated as required by the reviewed setup.
3. Photos and continuity results establish DATA and local ground without color
   assumptions; the probe point cannot short adjacent conductors.
4. The separately approved isolated differential-probe stage has retained the
   DATA envelope. Capture-instrument ratings, thresholds and isolation are
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
