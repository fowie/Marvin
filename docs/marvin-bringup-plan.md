# Marvin capability map and bring-up plan

Status: bounded read-only mapping complete; physical actuation remains gated.
Eight requests covering six distinct getter IDs returned correlated replies.
No firmware programming, configuration
writes, power switching, motor/servo actuation commands or LED setters have
been performed during this legacy-protocol investigation.

For offline setup and tests, start with the [repository README](../README.md).
Historical hardware results describe an operator-authorized session, not
permission to access devices in CI or unattended work. All examples assume
the repository root. Private source and capture citations follow
[the publication provenance conventions](provenance.md); they are not files
available in this checkout.

## What is established

The connected `045e:4444` controller accepts legacy single-byte `S`/`E` framing
at the tested settings: 57600 baud, 8N1, no flow control, DTR/RTS low after open.
These settings work; they are not proved to be the only working settings.
The driver briefly changes control lines during open/close.

Every result below has a matching sequence/command, response byte `80`, complete
legacy length/footer/CRC, an exact USB OUT, and corresponding USB IN/serial
evidence. Full responses are in serial captures; usbmon retains at most 32
payload bytes per transfer, not necessarily the full response.
The eight guarded 10-byte requests total **80 application OUT bytes** and
**538 serial RX bytes**. USB retains **240 RX prefix bytes**, omitting
**298 bytes**; full serial responses remain privately retained. The earlier
47,767 confirmed OUT bytes plus this mapping give **47,847 confirmed bytes**.
The historical **13 delivery-uncertain attempted bytes remain uncertain**,
excluded from that total.

| Operation | Legacy ID | Returned payload | Established result |
|---|---|---|---|
| GetConfig | `04` | 108 bytes | 27 configuration words; may be compiled defaults |
| GetUnitInfo | `1B` | 12 bytes | Reported words `01020000`, `01020000`, `01020304` |
| GetPowerState | `0E` | 2 bytes | Reported mask `0EFF`; not measured rail voltages |
| ReadRawData | `00` | 134 bytes | Matches the old sample's complete telemetry layout |
| GetLog | `0C` | 32 bytes | `taskSystem: after software setup`; repeated on a second read |
| GetServoPosition | `1D` | 4 bytes | Two reported values: 2500, 2730; not measured joint angles |

The two log replies were identical; reading stopped after the second, rather
than attempting an unbounded drain. UnitInfo words `01020000`, `01020000`,
`01020304` do not identify a unique board or an exact running firmware image.

The complete [machine-readable command catalogue](marvin-command-map.json)
covers all 23 command IDs at 30 send sites in the historical sample, plus all
63 slots in each newer Drive and Head firmware table. Live observations are
separate from source-only entries. This is not a claim to have exercised every
command or all possible numeric opcodes.

The old/new distinction is operational, not just cosmetic: command `00` returns
raw data here but is unimplemented in the newer C tables; `1D` returns four
servo-related bytes here, rather than the newer synthetic 128-byte SensorInfo.
Do not select an entire command table based only on the shared GetConfig and
GetUnitInfo commands.
Successor EFBE and legacy S/E opcodes also collide across getters and
motion/reset operations; neither framing nor numeric IDs may be substituted
without a reviewed profile. Historical broad-sweep tools remain experimental
and **are not recommended for this known-working legacy device**.

## Configuration map

All 27 words are decoded in [the configuration export](marvin-configuration.json)
and by `tools.marvin_legacy_config.interpret_packet`. This is an explicit
source-layout view; the generic telemetry decoder continues to leave config
opaque. The later `confparams` declaration supplies the names and types, not
proof that these are active settings or calibrated limits on the older image.

| Word / byte offset | Source field | Reported value | Newer Drive default |
|---|---|---|---|
| 0 / 0 | unitInfo.fwVersion | `01020000` hex | Build-dependent |
| 1 / 4 | unitInfo.commVersion | `01020000` hex | `00010300` hex |
| 2 / 8 | unitInfo.serialNumber | `01020304` hex | Same default |
| 3 / 12 | accel | 1 | 1 |
| 4 / 16 | kp | -256 | 90 |
| 5 / 20 | ki | -8 | 0 |
| 6 / 24 | kd | 768 | 30 |
| 7 / 28 | integralDivisor | 1 | 1 |
| 8 / 32 | maxPwmDelta | 100 | 600 |
| 9 / 36 | maxVel | 3500 | 3500 |
| 10 / 40 | minVel | -3500 | -3500 |
| 11 / 44 | motionTickTimeout | 8 | 8 |
| 12 / 48 | motorStop1Sec | 1 | 1 |
| 13 / 52 | servoCamMin | 1200 | 1200 |
| 14 / 56 | servoCamDefault | 2500 | 1425 |
| 15 / 60 | servoCamMax | 2500 | 2300 |
| 16 / 64 | servoCamTorque | 336 | 336 |
| 17 / 68 | servoProjMin | 200 | 200 |
| 18 / 72 | servoProjDefault | 2730 | 2730 |
| 19 / 76 | servoProjMax | 2730 | 2730 |
| 20 / 80 | servoProjTorque | 512 | 512 |
| 21 / 84 | heartbeatPeriod | 8 | 8 |
| 22 / 88 | sysClockFreq | 50000000 | 50000000 |
| 23 / 92 | cliffStopThreshold | 80 | 80 |
| 24 / 96 | cliffStopHysteresis | 32 | 32 |
| 25 / 100 | batChargeFullThreshold | 1400 | 1400 |
| 26 / 104 | batChargeFullHysteresis | 80 | 80 |

The negative values use the later declaration's signed 32-bit interpretation;
the export also preserves unsigned values and all original bits. The seven
known-default differences include **all three PID coefficients**, not just the
PWM and servo limits. The source comments retain the older -256/-8/768 values,
but its actual newer defaults are 90/0/30.

Source anchors: `m_inc/m_config.h:30-71`,
`m_inc/protocol.cs:2890-2919`, `m_src/m_config.c:18-94`,
and `m_inc/m_hw.h:27-37` in
`private-archive:MarvinFirmwareAndSample.zip` (unpublished source citations).
The later GetConfig handler
returns constant `defConfig`; inspected C call sites do not establish that
these legacy motor fields drive the newer runtime controller.

The newer header uses a 5000-microsecond heartbeat multiplier (8 would mean
40ms there), while its Drive/Head clock defaults differ (50/80MHz). Neither
fact establishes the installed board's heartbeat, oscillator, control loop or
watchdog timing. Do not infer degrees from the source's newer servo comments,
convert battery thresholds into volts, or overwrite this configuration with
the newer defaults.

## Available read-only state

The old 134-byte telemetry layout contains 82 fields: a tick counter, eight
proximity channels, five cliff channels, environmental/power/IMU channels,
two motor position words, velocities, accelerations, currents, status flags,
two servo-related values, four motor PWM values, and 18 LED brightness plus
18 LED blink bytes.
The [recorded telemetry snapshot](marvin-telemetry-snapshot.json) retains every
field and the capture timestamp; it is not a continuously refreshed display.

Field names and offsets come from the old sample. All word bits are preserved
with both unsigned and signed interpretations. Physical units, calibration,
sensor attachment and health are not inferred from the labels.

In the first snapshot both reported motor positions and velocities were zero,
as were all four PWM values. Servo-related telemetry was projector 2730 and
depth-camera 2500; the dedicated getter returned those in the opposite order.
Only LED channel 15 reported nonzero brightness/blink values: 255 and 42.
This does not identify a physical wheel-area LED or explain its earlier change.
The raw battery-voltage field was 438; it must not be displayed as volts.

A second raw-data read, through the public Linux probe tool, returned another
complete 134-byte block. Across 999.863 host seconds the reported counter
advanced by 98,081 and 24 fields changed, including proximity/cliff/IMU values
and flags. That demonstrates changing data, not sensor calibration or health.
The observed rate is about 98.094 counter increments per host second; it does
not establish the motor loop or watchdog frequency. Motor/PWM reports stayed
zero and the servo/LED reports stayed unchanged.

## Implemented Linux foundation

`tools.marvin_legacy_protocol` provides strict legacy framing and the proved
read-only request encoders. `marvin_legacy_stream` handles fragmented/coalesced
frames with bounded buffers and explicit corruption/truncation evidence.
`marvin_legacy_replay` reads finished captures without changing them.
`marvin_legacy_telemetry` interprets only the exact old raw-data, UnitInfo and
power-mask profiles, with explicit received-direction declarations.
`marvin_legacy_probe` provides a working one-shot guarded capture for the four
proved core getter names; it defaults to an offline dry-run.
The public probe was exercised on this controller and its full capture was
independently correlated, not merely accepted by the host serial driver.

For offline inspection:

```sh
.venv/bin/python -m tools.marvin_legacy_protocol generate \
  --command read-raw-data --sequence 14
.venv/bin/python -m tools.marvin_legacy_replay NEW_CAPTURE_DIRECTORY/capture/serial/received.bin \
  --chunks NEW_CAPTURE_DIRECTORY/capture/serial/chunks.jsonl --evidence recorded \
  --direction received --telemetry
```

Generation and replay do not transmit. Supply your own authorized local capture
for replay; the cited private evidence IDs are not downloadable capture paths.
Existing modern EFBE tools retain their defaults and behavior and must not be
substituted for the legacy codec. No motor/servo control API is enabled.

The following is a live-read example, **not offline getting started or standing
authorization**. It requires an explicitly authorized operator/session, a
reviewed physical test plan, and the physical isolation conditions below.
Use only the operator-reviewed port identifier; software cannot prove isolation:

```sh
sudo -v
.venv/bin/python -m tools.marvin_legacy_probe read-raw-data \
  --sequence 14 --output NEW_CAPTURE_DIRECTORY \
  --expected-physical-port OPERATOR_REVIEWED_PORT \
  --actuators-isolated --sudo-usbmon --run
```

The output parent must already exist and the new leaf directory must not.
If the ordinary user already has read permission on the target bus's
`/dev/usbmonN` node, omit `sudo -v` and replace `--sudo-usbmon` with
`--unprivileged-usbmon`. Exactly one recorder mode must be selected for a live
run. Both modes require the same USB recording, identity checks and evidence
validation; permission failure never triggers automatic escalation or a
serial-only fallback. USB monitor permission covers every device on that bus,
not just Marvin; do not grant all-bus `/dev/usbmon0` access. Reconfirm the USB bus
after reconnect/reboot, and have the operator load the `usbmon` module if its
nodes are absent. The probe does not load modules or change permissions.

Run the coordinator as the ordinary user, not under sudo. It checks the known
USB fingerprint and physical port, pins the device instance, records both
directions, and permits one 10-byte write only. Without `--run`, it does not
open hardware or create output. Capture completion alone is not an ACK; inspect
the resulting `NEW_CAPTURE_DIRECTORY/capture/serial/` files and USB evidence.

## Why not run the supplied Windows program

It has a missing protocol selector and an unsafe/incomplete receive parser.
Some sample buttons send arbitrary configuration bytes or alternating identity
bytes; timers issue random/repeated motor commands, and the power-setting
button writes zero. It is a reference for wire facts, not a safe turnkey client.
The archive has a source-use restriction; no proprietary implementation has
been incorporated or redistributed. The newer service can automatically flash
firmware and must not be launched against this robot.

## Path to a controllable robot

### 1. Establish a reliable read-only session

The offline foundation is implemented by the
[persistent legacy getter client](legacy-client.md): single-owner lifecycle,
command/sequence/status/shape correlation, labeled unsolicited/error evidence,
deadlines, bounded retention, and invalidation on identity or uncertain-write
failures. Unexpected/error replies cannot satisfy requests. It has no live
adapter, reconnect/retry path or setter API; this does not establish physical
acceptance. Reserve a separately reviewed, explicitly authorized path for any
future live integration or actuator commands.

The downstream polling/recording work (#8) should start with low-rate raw-data
polling, not a guessed heartbeat-enabling command.
Measure tick progression and changing values, collect repeatable baselines,
and expose raw data plus source-confidence labels in a dashboard. Record
driver control-line transitions. Do not reopen the port for each eventual
control-loop tick.

Exit criterion: stable repeated read-only snapshots and clean disconnect/
reconnect behavior, without changing motor, servo, power or configuration state.

### 2. Clear physical and electrical prerequisites

The [offline commissioning checklist and evidence validator](commissioning-evidence.md)
records attributed prerequisites, configuration mismatches and reviewed-policy
freshness. A complete package is not physical sign-off or authorization.

Human intervention is required before actuator reconnection. Keep motor/servo
power **and signal connections** isolated for current work. Make wiring changes
only with robot power, batteries where applicable, and USB back-power removed.

Identify every connector, motor/servo channel, supply and physical LED. Repair
or positively isolate the damaged PEND TXCVR socket and resolve the hub port-4
over-current indication before using that branch. Do not bypass protection or
infer electrical safety from successful USB communication.

Provide a physical emergency stop that removes actuator energy independently
of Linux, USB and firmware. Check fusing/current limits, grounds, mechanical
clearances and support the robot so unexpected motion cannot propel it. Verify
proximity/cliff inputs with controlled physical stimuli; do not disable or
clear safety interlocks merely to make motion work.

Exit criterion: documented wiring/channel map, independent stop and safe test
fixture, with an operator present.

### 3. Validate stop behavior before commanding motion

The old sample describes signed 16-bit left/right velocity commands at `11`,
four unsigned raw-PWM words at `0B`, and two unsigned servo values at `1E`.
These are source descriptions, not yet validated actuator APIs or units.
Their newer-map meanings differ.

First validate a reviewed zero-output/stop command on the isolated interface,
then under supervised, energy-limited physical conditions. Establish whether
outputs actually stop on host silence, application exit, USB loss and watchdog
expiry. A response packet alone cannot prove that a motor stopped.

Do not use the newer firmware's watchdog timing, PID coefficients, command
units or safety-state logic as proof of this older image's behavior. Do not
use raw PWM as the first general-purpose driving interface.

Exit criterion: observed safe stop and timeout behavior, a trustworthy
disarm path, and measured limits for the actual installed firmware/hardware.

### 4. Bring up one mechanism at a time

Reconnect one actuator only during a powered-down wiring step. With an operator
and emergency stop, establish channel identity, direction, neutral/stop, encoder
sign and a small safe range. Measure wheel geometry/count conversion before
claiming metres/second. Establish servo pulse/position units, polarity and
mechanical limits before exposing angles.

Use short, bounded commands with conservative limits, acceleration limiting
and an independent watchdog. Never run random motor tests, full-range sweeps,
factory calibration writes or head/projector commands copied from the newer
map. Reconnect additional actuators only after the preceding mechanism passes.

Exit criterion: each mechanism has individually documented limits and safe
failure behavior, not merely an acknowledgment.

### 5. Add supervised control, then autonomy

Expose a high-level Linux interface with explicit arm/disarm, dead-man timeout,
velocity and duration caps, source/profile checks, and logged request/reply
state. Start with tethered supervised teleoperation. Integrate odometry,
proximity/cliff sensing and actual battery measurements before obstacle
avoidance or autonomous navigation.

A UI stop button is supplemental to the physical stop. Camera, audio and other
host media devices must be inventoried and integrated separately rather than
inferred from fields or capabilities in the newer firmware. Read-only
discovery with Marvin powered off and external power disconnected found no
additional camera interface after the reported internal rear-camera connection.
The pre-existing PCI/MIPI Intel IPU3 paths belong to the host and must not be
attributed to Marvin. This powered-off result cannot determine the camera's
powered transport or identity. See
[camera interface discovery](camera-discovery.md).

## Not required for the next phase

No firmware flashing, debug adapter or replacement firmware is needed to keep
developing the now-working read-only interface. Exact firmware-image identity,
physical unit conversions, actuator behavior and the earlier LED/stall causes
remain distinct unanswered questions.
