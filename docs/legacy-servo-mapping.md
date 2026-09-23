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
