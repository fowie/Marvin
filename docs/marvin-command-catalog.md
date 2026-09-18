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

The public repository publishes reviewed metadata for two supplied candidate
firmware images. The original images, archive manifest, recovered symbol files,
and string reports are deliberately not present, so this checkout cannot
repeat `strings`, symbol lookup, or binary constant searches. The retained
names, hashes, entrypoint findings, source constants, USB identities, and
protocol behavior are sufficient to reject an exact match:

| Rank | Candidate | Published identity evidence | Distinguishing evidence | Result |
|---:|---|---|---|---|
| 1 | Historical PCTestApp-compatible legacy generation | No firmware image or exact build constant is published. The sample's 12-byte layout accepts the installed words. | Exact match to installed `53`/`45` framing, 57600/8N1 behavior, legacy IDs `00`, `04`, `0C`, `0E`, `1B`, `1D`, and their observed payload sizes. | **Best protocol/profile match, but not an image match.** No binary or legacy handler is available to compare. |
| 2 | `IOboard_FW45949.bin` | Name/build metadata `45949` (`0x0000B37D`); SHA-256 `28065f57d91b6ede41899be38ecc61cc2513e54369c35c4ef03598b9c9a562af`. Newer source default `commVersion=0x00010300`, `serialNumber=0x01020304`; `FirmwareVersion` is build-dependent. | Installed raw `fwVersion=0x01020000` and `commVersion=0x01020000` do not numerically equal the retained build tag or communications constant; no published mapping equates them. More decisively, the candidate uses successor framing/table, 157-byte raw data, command `03` for raw data and command `1D` for 128-byte SensorInfo; its build targets the newer vendor-bulk generation rather than installed `045e:4444` CDC behavior. | **Not an exact match.** Shared serial default and 12-byte UnitInfo shape are nondiscriminating. |
| 3 | `HeadController_FW45949.bin` | Name/build metadata `45949` (`0x0000B37D`); SHA-256 `ac285e0284c3b638e3895c838b9258037c330b0ae96a99b9c2427200b6c443cc`. Same newer UnitInfo contract family. | Same raw-identity non-match, plus wrong controller role: retained Head raw data is 36 bytes and its command table contains head-specific microphone/servo/LED operations. The installed controller returns the legacy 134-byte body telemetry profile. | **Not an exact match; weaker than IOboard.** |

Source anchors for the candidate metadata are
`tools/marvin_campaign_plan.py:69-92`,
`data/protocol-catalog.json:80-105`, `m_src/m_config.c:18-77`,
`m_inc/m_hw.h:27-37`, and the archive-relative C tables at
`m_src/m_protocol.c:108-240`. The archive itself is identified by SHA-256
`c9977a4091c6186caef552143f5ef672f54a4e3c9b7c67c699dea9dc26ee0118`,
but an archive hash does not identify which image is installed.

Because no supplied image is an exact match, no candidate image is used to
explain installed command `11` or raw response `82`. In particular,
`IOboard_FW45949` command `11` is `HostCommandGPIOPinSet`, while
`HeadController_FW45949` command `11` is `HostCommandMicrophoneGain`; neither is
the legacy `SetMotorVelocity` handler. The exact legacy image, its response
enum, and its command-`11` implementation remain the smallest missing offline
evidence.

## Next powered-session matrix

### Planned live trials

| Stage | Fixed requests | Maximum application writes / bytes | Authorization |
|---|---|---:|---|
| 0 - source recovery | None | **0 / 0** | **Current plan.** Recover and review the missing legacy status enum and command-`11` handler offline. No powered session or command is authorized. |

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
