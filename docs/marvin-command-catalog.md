# Marvin command catalogue

This is an offline source-and-evidence inventory, not an execution list. It
combines the 23 commands used by the historical `PCTestApp` with every slot in
the newer Drive and Head C command tables. The machine-readable source facts
remain in [marvin-command-map.json](marvin-command-map.json).

The two generations are not interchangeable:

- The installed `045e:4444` controller has only demonstrated the legacy
  single-byte `53`/`45` framing at 57600/8N1.
- The newer tables describe candidate `EF BE`/`AD DE` firmware built for other
  USB identities. Shared numeric IDs do not establish shared meanings.
- `80` and `82` below are retained raw legacy response fields. Successor
  response-code conventions are not used to decode them.
- Private-archive paths are provenance citations, not files in this repository.
  See [provenance.md](provenance.md).

## Evidence and disposition labels

| Label | Meaning |
|---|---|
| **Proven exchange** | A sealed, correlated installed-controller request/reply matched sequence, command, CRC, raw response field, and payload length. It does not prove physical behavior or application acknowledgment. |
| **Proven status behavior** | The exact installed-controller request was correlated with the stated raw response. It does not decode that response. |
| **Host-only** | USB/application OUT was observed, but no application reply established recognition. |
| **Source-only** | The supplied source/sample identifies the command; the installed controller has not. |
| **Protocol mismatch** | Fact belongs to the newer candidate firmware and must not be sent to the installed legacy controller. |
| **Conditional read candidate** | Could be considered only after missing legacy source identifies a relevant returned field and a new operator plan fixes one request. Not authorized now. |
| **Catalog-only** | Deliberately avoided because it changes state, has an unsafe collision, lacks an installed shape, or cannot resolve the present question. |

## Historical PCTestApp legacy S/E surface

This is the complete set of commands *sent by the sample*: 23 IDs at 30 call
sites. It is not proof that the installed firmware dispatcher has no other
commands. Integer wire ranges follow their types; narrower physical ranges and
units remain unknown unless stated.

| ID / exact sample name | Category and request syntax | Expected response | Installed evidence and tested values | Live-test disposition |
|---|---|---|---|---|
| `00` `ReadRawData` | Read-only; empty request. `PCTestApp/Form1.cs:934` | **Proven:** raw `80`, 134-byte payload / 144-byte frame | **Proven exchange**, repeatedly. Returns 82 raw fields including motor position/velocity/acceleration/current, four PWM words, cliff channels, raw flags, power/environment/IMU fields. | **Conditional read candidate.** Already exercised; no decoded reject/latch field. |
| `04` `GetConfig` | Read-only; empty. `Form1.cs:862` | **Proven:** `80`, 108 bytes / 118-byte frame | **Proven exchange.** 27 words include source-labelled `maxVel=3500`, `minVel=-3500`, `motionTickTimeout=8`, `motorStop1Sec=1`, `heartbeatPeriod=8`, `cliffStopThreshold=80`, `cliffStopHysteresis=32`. The newer handler returns `defConfig`; active installed use is unproved. | **Conditional read candidate** only if recovered legacy code names a relevant config gate. Re-reading cannot prove a setting is active. |
| `05` `SetConfig` | Configuration write; 27 words / 108 bytes. Sample fills a byte ramp. `Form1.cs:973` | Unknown legacy response shape | **Source-only; no live test.** Exact installed field acceptance and persistence are unknown. | **Catalog-only: persistent/destructive configuration write.** |
| `0A` `GetRawMotorPWM` | Nominal read; empty. `Form1.cs:646` | Unknown legacy response shape | **Source-only; no live test.** Four PWM words are already present in proven `00`. Newer Drive `0A` is `SetFanRpm`. | **Catalog-only: installed shape unknown and getter/setter collision.** |
| `0B` `SetRawMotorPWM` | Actuator setter; four LE `uint16` words / 8 bytes (`0..65535` wire values; physical range unknown). Button and timer sites `Form1.cs:674,1057`. | Unknown legacy response shape | **Source-only; no live test.** Source-used timer/value bounds are not retained as installed facts. | **Catalog-only: direct raw output setter, no closed-loop safety basis.** |
| `0C` `GetLog` | Diagnostic read; empty; may consume/advance log state. `Form1.cs:961` | **Proven:** `80`, 32 bytes / 42-byte frame | **Proven exchange.** Repeated historical reads and sequence 3076 returned ASCII `taskSystem: after software setup`; the latter used request `53040c0c00000071d045`, 10 TX / 42 RX / 0 uncertain bytes. | **Catalog-only for the current rejection:** repeated result contained no reason. Conditional only if source proves a relevant queued log record. |
| `0E` `GetPowerState` | Read-only; empty. `Form1.cs:940` | **Proven:** `80`, 2 bytes / 12-byte frame | **Proven exchange.** Returned raw mask `0EFF`; bit-to-rail mapping and relation to command acceptance are unknown. | **Conditional read candidate** only after a source-backed mask mapping identifies a relevant bit. |
| `0F` `SetPowerState` | Power-state setter; one LE `uint16`; sample writes `0`. `Form1.cs:949` | Unknown legacy response shape | **Source-only; no live test.** Zero is not known to mean safe/OFF. | **Catalog-only: power switching with unknown bit semantics.** |
| `10` `GetMotorVelocity` | Nominal read; empty. `Form1.cs:868` | Unknown legacy response shape | **Source-only; no live test.** Velocity is already present in proven `00`. Newer Drive `10` is `ResetCliffStop`. | **Catalog-only: installed shape unknown and getter/reset collision.** |
| `11` `SetMotorVelocity` | Actuator setter; left/right LE signed `int16` / 4 bytes (`-32768..32767`; physical units unknown). Button/random/fixed-timer sites `Form1.cs:897,992,1019`; the random source uses `Next(-1000,1000)`. | **Observed:** zero `80`/0 bytes; positive nonzero `82`/0 bytes | **Proven status behavior.** Fixed sequence 3072 zero `53000c11000400000000009a0145` -> `80`; sequence 3073 left `+1` `53010c1100040001000000ca3845` -> `82`; sequence 3073 left `+1000` `53010c11000400e80300000e6445` -> `82`; sequence 3074 cleanup zero `53020c11000400000000003bcb45` -> `80`. Both runs completed final `00`; the `+1000` run reported DMM 0.00 V/no change across the disconnected left output. | **Catalog-only: no larger value or repeat.** Value-dependent rejection is established for `+1` and `+1000`; `82` meaning and physical stop remain unknown. |
| `15` `ResetPC` | Reset; empty. `Form1.cs:955` | Unknown legacy response shape | **Source-only; no live test.** Reset target/effects are not established. | **Catalog-only: reset/destructive lifecycle change.** |
| `17` `GetLedState` | Nominal read; empty. `Form1.cs:680` | Unknown legacy response shape | **Source-only; no live test.** Newer Drive `17` is `SetDriveVelocities`. | **Catalog-only: installed shape unknown and getter/motion collision.** |
| `18` `SetLedState` | Output setter; 18 `uint8` brightness values. Sites `Form1.cs:714,738,746` set one channel with other entries zero, all channels, or random channels; exact physical channels/ranges are unproved. | Unknown legacy response shape | **Source-only; no live test.** | **Catalog-only: state-changing output with no diagnostic value.** |
| `19` `GetLedBlink` | Nominal read; empty. `Form1.cs:850` | Unknown legacy response shape | **Source-only; no live test.** Newer Drive/Head `19` is `SetServoRadians`. | **Catalog-only: installed shape unknown and getter/servo-setter collision.** |
| `1A` `SetLedBlink` | Output setter; 18 `uint8` values. Sites `Form1.cs:812,836,844` set one/all/random entries; timing semantics and physical range are unknown. | Unknown legacy response shape | **Source-only; no live test.** | **Catalog-only: state-changing output with no diagnostic value.** |
| `1B` `GetUnitInfo` | Read with possible telemetry-handshake side effect; empty. `Form1.cs:610` | **Proven:** `80`, 12 bytes / 22-byte frame | **Proven exchange.** Reported words `01020000`, `01020000`, `01020304`; they do not identify a unique image. Separate successor-framed host trial produced OUT but no application RX. | **Catalog-only now.** A future identity read is conditional on source mapping the returned version to the legacy handler; handshake side effect requires separate review. |
| `1C` `SetUnitInfo` | Identity write; 12 bytes; sample uses alternating `AA`/`55`. `Form1.cs:628` | Unknown legacy response shape | **Source-only; no live test.** Persistence is unknown. | **Catalog-only: identity/configuration write.** |
| `1D` `GetServoPosition` | Read; empty. `Form1.cs:778` | **Proven:** `80`, 4 bytes / 14-byte frame | **Proven exchange.** Returned raw values 2500 and 2730; not measured angles. This legacy meaning conflicts with newer `GetSensorInfo`. | **Catalog-only for motor rejection:** unrelated returned state; generation-sensitive despite the proved legacy exchange. |
| `1E` `SetServoPosition` | Actuator setter; two LE `uint16` / 4 bytes. UI suggests `0..3000`; parser only enforces `0..65535`. `Form1.cs:772` | Unknown legacy response shape | **Source-only; no live test.** | **Catalog-only: servo setter and servos are isolated.** |
| `1F` `GetSensorInfo` | Nominal read; empty. `Form1.cs:634` | Unknown legacy response shape | **Source-only; no live test.** Newer Drive is `ResetCom`; newer Head is `DepthCamPower`. | **Catalog-only: getter/reset/power collision and unknown installed shape.** |
| `26` `DisableHeartbeat` | Telemetry-control command; empty. `Form1.cs:640` | Unknown legacy response shape | **Source-only; no live test.** There is no proved readback for heartbeat-enable state. Newer Drive `26` is `GetProjectorVersion`. | **Catalog-only: state-changing heartbeat control.** |
| `27` `ResetMotorPositions` | Odometry reset; empty. `Form1.cs:856` | Unknown legacy response shape | **Source-only; no live test.** | **Catalog-only: destructive state reset.** |
| `28` `GetBatteryInfo` | Nominal read; empty. `Form1.cs:928` | Unknown legacy response shape | **Source-only; no live test.** Related raw battery fields are already present in `00`; newer Drive `28` moves the projector image. | **Catalog-only: installed shape unknown and getter/output collision.** |

The installed live evidence above also includes the repeated powered trials in
[legacy-powered-left-stop.md](legacy-powered-left-stop.md) and the fixed
disconnected-load outcomes in
[legacy-disconnected-order.md](legacy-disconnected-order.md). A successful
write, CRC-valid reply, raw `80`, or DMM observation does not establish
application acknowledgment, calibration, motor mapping, or physical stop.

## Newer Drive and Head command tables

These are all 63 numeric slots (`00` through `3E`) in each newer C table.
`Enum` preserves the recovered shared-contract spelling when one exists;
different C-table spellings are retained. Shapes are
`request count x element type -> return count x element type`. `NYI` means the
table routes the slot to `HostCommandCommandNYI`.

Every row in this section has disposition **protocol mismatch / catalog-only**:
none is a live candidate for the installed legacy controller. State-changing,
reset, flash, calibration, raw-output, actuator, power, and heartbeat commands
are additionally unjustified on their own terms.

| ID | Shared enum / exact C-table names | Drive shape | Head shape | Category and evidence |
|---|---|---|---|---|
| `00` | Enum `Unused0`; Drive/Head `ReSync` | `0 NoArg -> 0 NoArg` (NYI) | same (NYI) | Reserved; source-only |
| `01` | Enum `DeviceHeartbeat`; Drive/Head `Heartbeat` | `0 -> 0` (NYI) | same (NYI) | Telemetry; source-only |
| `02` | Enum/Drive/Head `HostHeartbeat` | `sizeof(HeartbeatDataHostToDriveController) UInt8Arg -> 0` | `0 -> 0` | State-changing telemetry; source-only |
| `03` | Enum/Drive/Head `ReadRawData` | `0 -> sizeof(HeartbeatData) UInt8Arg` | same | Nominal read; **host-only** successor-framed OUT, no application RX |
| `04` | Enum/Drive/Head `GetConfig` | `0 -> sizeof(confparams)/sizeof(uint32) UInt32Arg` | same | Nominal read; **host-only** successor-framed OUT, no application RX |
| `05` | no enum member; Drive `HostHeartbeatIgnore`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | State-changing telemetry; source-only |
| `06` | no enum member; Drive `CliffDetectionEnable`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | Safety-state setter; source-only |
| `07` | Enum/Drive `SetProjectorIdleMode`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | Output setter; source-only |
| `08` | Enum/Drive/Head `InitReflash` | `1 UInt32Arg -> 0` | same | Flash operation; deliberately avoided |
| `09` | Enum `ReflashBlock`; Drive/Head `AddReflashBlock` | `BytesInReflashBlock UInt8Arg -> 0` | same | Flash write; deliberately avoided |
| `0A` | Enum `FanPower`; Drive `SetFanRpm`; Head blank | `1 UInt16Arg -> 0` | `0 -> 0` (NYI) | Output setter; source-only |
| `0B` | Enum/Drive/Head `MotorParameter` | `3 UInt16Arg -> 0` | same | Motor-state setter; source-only |
| `0C` | Enum/Drive/Head `ErrorReport` | `0 -> 1 UInt32Arg` (NYI) | `0 -> 0` (NYI) | Unimplemented nominal read; source-only |
| `0D` | Enum/Drive/Head `RecalibrateServo` | `1 UInt8Arg -> 0` | same | Calibration/state change; source-only |
| `0E` | no enum member; Drive `GetPowerState`; Head blank | `0 -> 1 UInt16Arg` | `0 -> 0` (NYI) | Nominal read in mismatched Drive table only; source-only |
| `0F` | Enum/Drive `SetPowerState`; Head blank | `1 UInt16Arg -> 0` | `0 -> 0` (NYI) | Power setter; deliberately avoided |
| `10` | Enum `ResetCliffStoppage`; Drive `ResetCliffStop`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Safety-latch reset; deliberately avoided |
| `11` | Enum/Head `MicrophoneGain`; Drive `GPioPinSet` | `3 UInt8Arg -> 0` | `1 FloatArg -> 0` | Output setters; no relation to installed legacy velocity handler |
| `12` | Enum/Drive `SetBumpSensing`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | Safety-state setter; source-only |
| `13` | Enum/Drive `ResetBumpDetected`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Safety-latch reset; deliberately avoided |
| `14` | Enum `ResetIoBoard`; Drive/Head `ResetController` | `0 -> 0` | same | Reset; deliberately avoided |
| `15` | Enum/Drive `ResetPC`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Reset; deliberately avoided |
| `16` | Enum/Drive/Head `ToggleHeartbeat` | `0 -> 0` | same | Telemetry state change; deliberately avoided |
| `17` | Enum/Drive `SetDriveVelocities`; Head blank | `2 FloatArg -> 0` | `0 -> 0` (NYI) | Actuator setter; deliberately avoided |
| `18` | Enum/Drive `SetTimedDriveVelocities`; Head blank | `3 FloatArg -> 0` | `0 -> 0` (NYI) | Timed actuator setter; deliberately avoided |
| `19` | Enum/Drive/Head `SetServoRadians` | `2 FloatArg -> 0` | same | Actuator setter; deliberately avoided |
| `1A` | Enum/Drive/Head `SetServoHoldingCurrent` | `2 UInt8Arg -> 0` | same | Actuator/current setter; deliberately avoided |
| `1B` | Enum/Drive/Head `GetUnitInfo` | `0 -> sizeof(UnitInfo)/sizeof(uint32) UInt32Arg` | same | Nominal read; **host-only** successor-framed OUT, no application RX |
| `1C` | Enum `ResetMotorPos`; Drive `ResetMotorPosition`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Odometry reset; deliberately avoided |
| `1D` | Enum/Drive/Head `GetSensorInfo` | `0 -> 128 UInt8Arg` | same | Nominal read; **host-only** successor-framed OUT, no application RX |
| `1E` | Enum/Drive `SonarMode`; Head `ResetCom` | `1 UInt8Arg -> 0` | `0 -> 0` | Sensor-mode setter vs reset collision; deliberately avoided |
| `1F` | Enum `DepthCamPower`; Drive `ResetCom`; Head `DepthCamPower` | `0 -> 0` | `1 UInt8Arg -> 0` | Reset vs power setter collision; deliberately avoided |
| `20` | Enum/Head `SetFaceRingLeds`; Drive blank | `0 -> 0` (NYI) | `sizeof(FaceRingLedsParams) UInt8Arg -> 0` | Output setter; source-only |
| `21` | Enum/Head `SetSensoryLeds`; Drive blank | `0 -> 0` (NYI) | `sizeof(SensoryLedsParams) UInt8Arg -> 0` | Output setter; source-only |
| `22` | Enum `SetProjectorFocus`; Drive `ChangeProjectorFocus`; Head blank | `1 Int16Arg -> 0` | `0 -> 0` (NYI) | Output setter; source-only |
| `23` | Enum/Drive `SetProjectorBrightness`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | Output setter; source-only |
| `24` | Enum/Drive `SyncProjectorSignal`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Output/state change; source-only |
| `25` | Enum/Drive `SetProjectorInversion`; Head blank | `2 UInt8Arg -> 0` | `0 -> 0` (NYI) | Output setter; source-only |
| `26` | Enum/Drive `GetProjectorVersion`; Head blank | `0 -> 1 UInt8Arg` | `0 -> 0` (NYI) | Nominal read; **host-only** successor-framed OUT, no application RX |
| `27` | Enum/Drive `SetProjectorPower`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | Power setter; deliberately avoided |
| `28` | Enum/Drive `MoveProjectorVerticalImage`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | Output setter; source-only |
| `29` | no enum member; Drive `ClearProjectorEEPRom`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Persistent erase; deliberately avoided |
| `2A` | Enum/Drive `GoToProjectorHomePosition`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Motion/output setter; deliberately avoided |
| `2B` | Enum `OpenProjectorShutter`; Drive `OpenShutter`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Output setter; source-only |
| `2C` | Enum `CloseProjectorShutter`; Drive `CloseShutter`; Head `GPioPinSet` | `0 -> 0` | `3 UInt8Arg -> 0` | Output setters with cross-build collision |
| `2D` | Enum/Drive/Head `SetCalibrationParams` | `sizeof(CalibrationParams) UInt8Arg -> 0` | same | Calibration write; deliberately avoided |
| `2E` | Enum/Drive/Head `GetCalibrationParams` | `1 UInt8Arg -> sizeof(CalibrationParams) UInt8Arg` | same | Nominal read, but selector semantics and installed compatibility are absent |
| `2F` | Enum/Drive/Head `CommitCalibrationParams` | `0 -> 0` | same | Persistent calibration commit; deliberately avoided |
| `30` | Enum/Drive/Head `ZeroAllCalibrationParams` | `0 -> 0` | same | Destructive calibration reset; deliberately avoided |
| `31` | Enum/Drive `SetProximityCalibrationOffsets`; Head `SetCompassCalibrationOffsets` | `2 Int16Arg -> 0` | same | Calibration writes with cross-build collision |
| `32` | Enum `SetCliffSensorCalibrationOffsets`; Drive `SetCliffCalibrationOffsets`; Head blank | `2 Int16Arg -> 0` | `0 -> 0` (NYI) | Calibration write; deliberately avoided |
| `33` | Enum/Drive `SetAccelerometerCalibrationOffsets`; Head blank | `2 Int16Arg -> 0` | `0 -> 0` (NYI) | Calibration write; deliberately avoided |
| `34` | Enum/Drive `SetGyroscopeCalibrationOffsets`; Head blank | `2 Int16Arg -> 0` | `0 -> 0` (NYI) | Calibration write; deliberately avoided |
| `35` | Enum/Drive/Head `SetServoCalibrationOffset` | `1 Int32Arg -> 0` | same | Calibration write; deliberately avoided |
| `36` | Enum `SetProjectorHomeCalibrationPosition`; Drive `SetProjectorHomePositionOffset`; Head blank | `0 -> 0` | `0 -> 0` (NYI) | Calibration/state change; deliberately avoided |
| `37` | Enum `SetCompassCalibrationOffsets`; Drive/Head `PrintCalibrationParams` | `1 UInt8Arg -> 0` | same | Name/operation mismatch; no installed relevance |
| `38` | Enum/Drive `SetBumpSensingThreshold`; Head blank | `1 FloatArg -> 0` | `0 -> 0` (NYI) | Safety calibration setter; deliberately avoided |
| `39` | no enum member; Drive `GpioExpSetLine`; Head `BlowerEnable` | `3 UInt8Arg -> 0` | `1 UInt8Arg -> 0` | Output setters with cross-build collision |
| `3A` | no enum member; Drive `DriveWithCharger`; Head blank | `1 UInt8Arg -> 0` | `0 -> 0` (NYI) | Drive/power state change; deliberately avoided |
| `3B` | Enum/Drive/Head `SetServoSequence` | `sizeof(MotionSequenceParams) UInt8Arg -> 0` | same | Actuator sequence setter; deliberately avoided |
| `3C` | Enum/Drive/Head `ConfigPid` | `4 Int32Arg -> 0` | same | Control configuration write; deliberately avoided |
| `3D` | Enum/Head `SetTimedFaceRingLeds`; Drive blank | `0 -> 0` (NYI) | `sizeof(TimedFaceRingLedsParams) UInt8Arg -> 0` | Timed output setter; source-only |
| `3E` | Enum/Head `SetTimedSensoryLeds`; Drive blank | `0 -> 0` (NYI) | `sizeof(TimedSensoryLedsParams) UInt8Arg -> 0` | Timed output setter; source-only |

The newer source anchors are `m_src/m_protocol.c:108-170` (Drive) and
`m_src/m_protocol.c:178-240` (Head). Exact handler names, implementation flags,
element counts, types, and per-row source lines are retained in
[marvin-command-map.json](marvin-command-map.json). The shared recovered
contract enum and struct layouts are in
[`data/protocol-catalog.json`](../data/protocol-catalog.json). That file is
explicitly successor-only.

## Command `11` rejection boundary

The old sample establishes only the two-word request and three send sites. The
installed trials establish that request order and sequence do not explain the
different raw response: a zero first returns `80`, either `+1` or `+1000`
second returns `82`, and cleanup zero third returns `80`.

Possible source-labelled state includes velocity bounds, timeout/stop words,
heartbeat period, cliff thresholds, raw flags, reported power mask, motor
velocity/current/PWM, and diagnostic text. None currently identifies a named
acceptance gate:

- both positive values are within the reported `-3500..3500` bounds;
- the configuration names come from a newer layout and may be compiled
  defaults rather than active installed state;
- raw flag bits have no published legacy meaning;
- `0EFF` has no published bit mapping;
- `GetLog` repeated the setup message and gave no rejection reason; and
- no proved getter exposes heartbeat enabled, cliff-stop latched,
  motor-driver ready/enabled, or response-status cause.

The missing evidence is the original legacy response-status definition, parser
and validation path, command-`11` handler, and every state field it checks.
Newer command `11` handlers (`HostCommandGPIOPinSet` and
`HostCommandMicrophoneGain`) are mismatched and cannot fill that gap.

## Installed UnitInfo and firmware candidates

The repository retains one standalone installed-controller `1B GetUnitInfo`
response. References in the replay and telemetry tests are the same reviewed
frame, not additional captures:

```text
frame:   5301001b800c00000002010000020104030201a3cb45
payload:                 000002010000020104030201
```

The 22-byte frame is sequence 1, command `1B`, raw response `80`, payload length
12, valid legacy CRC `cba3` as stored little-endian `a3cb`, and footer `45`.
Its SHA-256 is
`fb1d5bd963919f0a24eac9af1bfa3ef57a5cd7f80c39c662001d1f10e05b66e0`;
the 12-byte payload SHA-256 is
`f81073195173bf7c73ace0c58f823c21eec9530ebc5758f591b43c7cc0348446`.
The independently captured installed `04 GetConfig` response starts with the
same 12 payload bytes. That is corroboration of the bytes/layout, not a second
`1B` response.

The original PCTestApp sends the empty request at `Form1.cs:607-610`.
Its `parseInt` at `Form1.cs:393-396` accumulates four bytes least-significant
first using C# 32-bit integer arithmetic. The supplied `UnitInfo` declaration
at `m_inc/protocol.cs:2890-2919` names three consecutive `uint32` fields:
firmware version, communications version, and serial number. Preserving the
wire bytes and applying that little-endian layout gives:

| Payload offset | Original field/type | Raw bytes | Unsigned value | Hex |
|---:|---|---|---:|---|
| 0 | `fwVersion`, `uint32` | `00000201` | 16,908,288 | `0x01020000` |
| 4 | `commVersion`, `uint32` | `00000201` | 16,908,288 | `0x01020000` |
| 8 | `serialNumber`, `uint32` | `04030201` | 16,909,060 | `0x01020304` |

No bytewise dotted-version rendering is present in the published PCTestApp
facts, so `0x01020000` is not relabelled as a semantic version. The serial value
is also the newer source default and therefore is not a unique device identity.
The published facts do not retain a separate PCTestApp response-display call
site for `1B`; the field names come from the supplied shared declaration while
the byte order follows the sample's parser.

### Image/source comparison

The supplied archive was reviewed offline at SHA-256
`c9977a4091c6186caef552143f5ef672f54a4e3c9b7c67c699dea9dc26ee0118`.
All 159 retained BIN, HEX, and `.old` image artifacts were decoded; equivalent
BIN/HEX representations collapse to 107 unique image byte streams. This is a
content inventory, not evidence that any image was installed:

| Image family | Unique images | Comparison with installed facts |
|---|---:|---|
| IO/controller | 60 | Every image was searched for the complete UnitInfo payload, its component words, the proven GetLog text, the installed GetConfig bytes, and legacy command names/tables. No exact identity and protocol match exists. |
| Head controller | 41 | No exact identity match; the role, command tables, and raw-data profile disagree with the installed 134-byte body-controller response. |
| RevC1 `blinky_wa` patch | 6 | Unrelated sample images. The archive's AXF/OUT symbol-bearing files belong only to this patch, not to a Marvin controller image. |

No unique image contains the installed 12-byte sequence
`000002010000020104030201`. The little-endian firmware/communications word
`00000201` occurs once, isolated in `IoBoard_FW25723.hex.old`, without the
second word or serial value and in a successor-protocol image. The serial
default `04030201` occurs in 101 of 107 unique images and is therefore
nondiscriminating. No image contains the exact proven 108-byte GetConfig
payload, its 96-byte tail, or its 52-byte motor-config subsection.

| Rank | Candidate | Why it ranks here | Excluding evidence |
|---:|---|---|---|
| 1 | Unretained PCTestApp-era S/E firmware | Only known profile matching installed framing and proven IDs/shapes | No matching image, map, response enum, or handler is supplied |
| 2 | IO builds 22169, 22280, 22619 | Exact log text plus `0C GetLog`, four-byte `11 SetMototrVelocity`, and `1B GetUnitInfo` descriptors | Successor framing; wrong `00` and `1D` meanings; UnitInfo bytes absent |
| 3 | IO builds 23977-25107 | Two-signed-16-bit `11 SetMotorVelocity` handler is retained | Successor framing/table; exact log absent; UnitInfo bytes absent |
| 4 | IO build 25723 | Sole isolated occurrence of installed firmware/communications word | No adjacent identity words and a materially newer command map |
| 5 | Later IO and all Head images | Build metadata, tables, and source are best documented | Command `11`, controller role, framing, and response profiles disagree |

The exact ASCII GetLog payload `taskSystem: after software setup` occurs only
in seven historical IO images: builds 21770, 21833, 21885, 22126, 22169,
22280, and 22619. Builds 22169, 22280, and 22619 also retain diagnostic names
and a 12-byte command-descriptor table. They are the strongest binary family
candidates because their tables include `0C GetLog`, `11 SetMototrVelocity`
with a four-byte request, and `1B GetUnitInfo`. They are nevertheless excluded
as the installed image:

- their parser uses `BEEF`/`DEAD` framing rather than installed `53`/`45`;
- command `00` is `ReSync`, not the proven 134-byte `ReadRawData`;
- command `1D` is `GetSensorInfo`, not the proven four-byte
  `GetServoPosition`.

The archive-relative `obj/old/IoBoard_FW22619.hex.old` table is at image offset
`0x71BC`; its command-`11` descriptor points to Thumb handler `0x1312`.
That candidate handler contains no value or state branch: it unconditionally
clears the command-response byte. Its dispatcher initializes that byte before
the call and reports a parameter error only if the handler leaves it set.
Builds 22169 and 22280 use the same branch-free handler shape at `0x1B7E` and
`0x1BAA`. These candidate-only paths cannot produce the installed
zero=`80`, nonzero=`82` distinction.

The next transitional IO family, builds 23977 through 25107, retains command
`11 SetMotorVelocity` with two signed 16-bit arguments but has successor
framing and maps `00` to `ReSync`, `03` to `ReadRawData`, `0C` to
`ErrorReport`, and `1D` to `GetSensorInfo`. In builds 23977 through 24572 the
stripped command-`11` handler has one explicit rejection: a global board-ID
byte equal to `4` returns candidate code `2`; otherwise it bounds/scales the
values and returns `0`. The supplied `m_inc/m_hw_drive.h:28-32` names board ID
`4` as `D1`, but stripped-image symbol binding is not proved. The rejection is
independent of requested velocity, so it also cannot explain the installed
value-dependent result. Builds 24770 and 25107 remove that gate, scale/call
the velocity path, and return candidate code `0`.

Later generations diverge further: command `11` becomes unimplemented and
then `GPioPinSet` in IO images, while current Head images use it for
`MicrophoneGain`. Current source identifies build 50480 at
`m_inc/protocol.cs:48-54`, initializes communications version `0x00010300` and
serial `0x01020304` at `m_src/m_config.c:18-20`, and defines the mismatched
tables at `m_src/m_protocol.c:108-240`.

**Conclusion:** no supplied firmware image matches the installed identity,
framing, command layout, and observed response shapes. Candidate return code
`2` and current `ResponseCode` names are not transferred to installed raw
`82`, which remains opaque. The missing evidence is specifically the installed
legacy firmware image, or matching legacy source/map output containing its
S/E dispatcher, response enum, and command-`11` handler. Without that artifact,
no existing read-only command is source-proved to expose the rejection cause.

### Repository object-history search

The complete local Git object database was also searched offline, including
all branch, remote-tracking, Copilot checkpoint/preserved, and dangling
objects. At the time of review it contained 1,544 objects: 660 commits, 522
blobs, and 362 trees. All 1,225 objects reachable from the 352 refs were
covered, as were the 319 unreachable objects (264 commits, 27 blobs, and 28
trees). The refs comprised 12 local heads, 10 `origin` remote-tracking refs,
329 checkpoint refs, and one preserved ref; there were no tags.

The 111 paths ever named by reachable history contain this repository's
Python tools/tests, JSON evidence, and Markdown documentation. None is vendor
C/C# implementation source, a firmware/archive image, a linker map, or a
symbol file. Object-content and path-history checks found:

- no Git LFS pointer, `.gitmodules` object, gitlink, release tag, or
  release/changelog/version artifact;
- no ZIP/ELF/Intel-HEX magic, large hidden payload, or historical path ending
  in `.bin`, `.hex`, `.zip`, `.axf`, `.elf`, or `.map`;
- no blob containing the installed 12 identity bytes; textual hex instances
  are reviewed fixtures and documentation;
- `SetMototrVelocity` occurs only in derived catalogue versions, including
  object `f31b40502c06d0d9e84539a44425626af0ba706e`;
- the exact log text, VID:PID, command names, and S/E framing occur only in
  authored evidence or tooling. Representative current blobs are
  `docs/lab-findings.md`
  (`c3a19ea26d7d33be0659fe0a1db639e25fec3feb`),
  `docs/marvin-command-map.json`
  (`9ae284eb83f1b7c0813587028d08faaa364a4638`), and
  `tools/marvin_legacy_protocol.py`
  (`2152debd0065e65544504db7dfb24f3d114ce810`).

All 27 unreachable blobs are text variants of already named repository
documentation, JSON, Python tools, or tests. Reachable refs contain one
deleted-path commit,
`270bc6fda4d4e6d5c6bfb6bbafb514634ca4f6b0`, retained solely by
`refs/copilot/checkpoints/6fc4e9d7-0166-4282-9565-83ce22c9e98b/00000000000000000007/f448f3fa-a890-4a29-a166-ef747c66c5f1`.
One dangling commit, `f70860fe4da7842cda06f3ef3dc0dee58f7f33c5`,
records the same three deletions. Their shared parent
`b4f0060c4c924f887c3773f449ad090866880f40` retains only the superseded no-load
velocity runner, test, and documentation; it contains no firmware or vendor
implementation.

Nothing recoverable in local repository history can identify or trace the
installed command-`11` implementation. The next realistic acquisition is an
owner/vendor backup of the PCTestApp-era S/E controller build output
(firmware plus map/listing or matching source), keyed to the installed
UnitInfo words and command shapes. A separately authorized remote-release/API
search could check hosting metadata that Git does not store. Neither route
justifies hardware readback or transferring semantics from successor images.

### Public-source search

A public-source review completed on 2026-09-17 found no exact public match for
the installed S/E framing and command tuple, the GetLog text,
`SetMototrVelocity`, or the 12-byte UnitInfo payload. This is time-bounded
negative search evidence, not proof that the artifact was never published.
The verified searches were:

- GitHub code search for
  [`SetMototrVelocity`](https://github.com/search?q=%22SetMototrVelocity%22&type=code),
  the exact
  [GetLog phrase](https://github.com/search?q=%22taskSystem%3A+after+software+setup%22&type=code),
  and
  [`045e` + `4444` + Marvin](https://github.com/search?q=%22045e%22+%224444%22+Marvin&type=code);
- Sourcegraph global search including forks and archives for
  [`SetMototrVelocity`](https://sourcegraph.com/search?q=context%3Aglobal+fork%3Ayes+archived%3Ayes+%22SetMototrVelocity%22)
  and the exact
  [GetLog phrase](https://sourcegraph.com/search?q=context%3Aglobal+fork%3Ayes+archived%3Ayes+%22taskSystem%3A+after+software+setup%22);
- Internet Archive's
  [`SetMototrVelocity` metadata search](https://archive.org/advancedsearch.php?q=SetMototrVelocity&fl%5B%5D=identifier&fl%5B%5D=title&output=json).

[DeviceHunt](https://devicehunt.com/view/type/usb/vendor/045E/device/4444)
lists `045e:4444` without a product identity. The strongest retained provenance
is instead the reviewed 2007 Microsoft INF:
`reference/successor-robot/drivers/active/Windows/inf/oem6.inf:1-23,29-50`.
It is dated 2007-03-26, binds `USB\Vid_045E&Pid_4444` through `usbser.sys`,
names Microsoft as manufacturer, and names `Marvin Drive USB serial port` and
`Marvin USB CDC serial port`. This retained file is not claimed to have a
public GitHub URL.

Public archive context confirms that Microsoft distributed Robotics Developer
Studio and published robot-support/release pages: the archived
[RDS 2008 R2 Express download](https://web.archive.org/web/20090701093538/http://www.microsoft.com/downloads/details.aspx?displaylang=en&FamilyID=f9d8ddca-ab60-4c62-9770-2aaa87dfd01e),
[supported-robots page](https://web.archive.org/web/20110524012700/http://www.microsoft.com/robotics/Content.aspx?pg=Robots),
[CodePlex front page](https://web.archive.org/web/20110404030622/http://robotics.codeplex.com/),
and [CodePlex releases](https://web.archive.org/web/20120508201829/http://robotics.codeplex.com/releases).
Those pages do not establish that an installed-controller image or source is
present.

The later reviewed Mars lineage retains the VID/PID and UnitInfo concepts but
is not protocol-compatible. Its setup searches
`USB\VID_045E&PID_4444\12345678` and opens 115200/8N1 with DTR/RTS
(`reference/successor-robot/docs/protocol-and-safety.md:7-13`); its command IDs
are documented at
`reference/successor-robot/decompiled-reference/mars-contracts/Microsoft.Robotics.Firmware.Protocol/CommandID.cs:6-34`;
and its packets use `EFBE`/`ADDE` framing with different response shapes
(`reference/successor-robot/decompiled-reference/controller-service/Microsoft.Robotics.Firmware.ControlBoardIO/Packet.cs:20-56,66-83,120-130`).
The collection's provenance limits are recorded at
`reference/successor-robot/README.md:18-40`. These facts must not be
transferred to the installed S/E controller.

A public
[RDS 2008 Academic installer item](https://archive.org/details/microsoft-robotics-developer-studio-2008-academic-edition_202201)
and its
[installer executable](https://archive.org/download/microsoft-robotics-developer-studio-2008-academic-edition_202201/Microsoft_Robotics_Developer_Studio_2008_Academic_Edition.exe)
have been identified. Full installer extraction is **in progress and not yet
concluded**. Remaining acquisition routes are to complete that extraction and
inspect its nested installer payloads, recover historical CodePlex release
artifacts, or obtain an owner/vendor PCTestApp-era S/E build with firmware plus
map/listing or matching source. No current result justifies a new live command.

## Next powered-session matrix

### Planned live trials

| Stage | Fixed requests | Maximum application writes / bytes | Authorization |
|---|---|---:|---|
| 0 - source recovery | None | **0 / 0** | **Current plan.** Locate the absent installed legacy image or matching S/E dispatcher/status/command-`11` source offline. No powered session or command is authorized. |

No generic getter runner is added. Existing allowlists remain unchanged.

### Conditional fixed reads after source recovery

These frames are offline-generated review references, not authorization. At
most **one** row could advance into a future plan, and only if recovered legacy
source establishes that its returned field directly addresses the rejection
ambiguity. A new operator plan must fix the row, physical setup, evidence path,
and consent before any device access.

| Priority | Conditional one-shot request | Fixed reference frame | Expected clean reply | Ambiguity it could resolve after source mapping | Current blocker |
|---|---|---|---|---|---|
| 1 | sequence 3077 `00 ReadRawData` | `53050c00000000735145` | `00/80`, 134-byte payload; 144 RX bytes | Whether a newly source-named inhibit/ready field is set after rejection | No such field is presently decoded |
| 2 | sequence 3078 `0E GetPowerState` | `53060c0e000000718a45` | `0E/80`, 2-byte payload; 12 RX bytes | Whether a newly mapped power/enable bit blocks nonzero velocity | `0EFF` bits are presently unmapped |
| 3 | sequence 3079 `04 GetConfig` | `53070c04000000738345` | `04/80`, 108-byte payload; 118 RX bytes | Whether the handler checks a specific readable configuration word | Runtime use of the returned words is unproved |
| held | sequence 3080 `0C GetLog` | `53080c0c000000711c45` | `0C/80`, 32-byte payload; 42 RX bytes | A source-proved queued rejection log | Existing repeated text gave no reason; reads may advance the log |
| held | sequence 3081 `1B GetUnitInfo` | `53090c1b000000757945` | `1B/80`, 12-byte payload; 22 RX bytes | Exact legacy image selection if the words become source-identifiable | Current words are non-unique; possible handshake side effect |
| excluded | sequence 3082 `1D GetServoPosition` | `530a0c1d00000075c245` | `1D/80`, 4-byte payload; 14 RX bytes | None known for motor rejection | Proven but unrelated and generation-sensitive |

Any future one-shot row is bounded at one 10-byte request. It must stop closed
before serial on missing/mixed consent, identity or setup mismatch, or recorder
failure; after opening it must stop without retry/reconnect on early unexpected
RX, partial/uncertain TX, timeout, malformed framing, CRC failure, mismatched
sequence/command, non-`80` raw response, wrong payload length, evidence sealing
failure, or any unexpected physical observation. It must not automatically
continue to another row.

No installed request/response shape is established for `0A`, `10`, `17`, `19`,
`1F`, or `28`, so none appears in the conditional matrix. No setter, reset,
flash/configuration write, power-state change, heartbeat control, raw PWM,
servo/motor command, malformed packet, enumeration sweep, or successor-framed
command is planned.
