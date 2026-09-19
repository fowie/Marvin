# Marvin command catalogue

This is an offline source-and-evidence inventory, not an execution list. It
combines the 23 commands used by the historical `PCTestApp` with every slot in
the newer Drive and Head C command tables. The machine-readable source facts
remain in [marvin-command-map.json](marvin-command-map.json).

The two generations are not interchangeable:

- The installed `045e:4444` controller has only demonstrated the legacy
  single-byte `53`/`45` framing at 57600/8N1.
- Supplied `PCTestApp/SerialPacket.cs` and `PCTestApp/Form1.cs` are legacy host
  source: they define the S/E framing and request syntax used by multiple
  proved installed command IDs. They do not contain the controller handlers.
- Supplied `m_src/m_protocol.c` and `m_inc/protocol.cs` are newer firmware
  source with `EF BE`/`AD DE` framing and a different command map. Shared
  numeric IDs do not establish shared meanings.
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
| `0A` `GetRawMotorPWM` | Nominal read; empty. `Form1.cs:646` | **Proven:** `80`, 8 bytes / 18-byte frame | **Proven exchange.** Sequence 3083 returned `0000000000000000`. This can be grouped as four zero LE `uint16` words consistently with the request name, but field semantics are not proved. Newer Drive `0A` is `SetFanRpm`. | **Installed/proven read-only.** No repeat presently justified. |
| `0B` `SetRawMotorPWM` | Actuator setter; four LE `uint16` words / 8 bytes (`0..65535` wire values; physical range unknown). Button and timer sites `Form1.cs:674,1057`. | Unknown legacy response shape | **Source-only; no live test.** Source-used timer/value bounds are not retained as installed facts. | **Catalog-only: direct raw output setter, no closed-loop safety basis.** |
| `0C` `GetLog` | Diagnostic read; empty; may consume/advance log state. `Form1.cs:961` | **Proven:** `80`, 32 bytes / 42-byte frame | **Proven exchange.** Repeated historical reads and sequence 3076 returned ASCII `taskSystem: after software setup`; the latter used request `53040c0c00000071d045`, 10 TX / 42 RX / 0 uncertain bytes. | **Catalog-only for the current rejection:** repeated result contained no reason. Conditional only if source proves a relevant queued log record. |
| `0E` `GetPowerState` | Read-only; empty. `Form1.cs:940` | **Proven:** `80`, 2 bytes / 12-byte frame | **Proven exchange.** Returned raw mask `0EFF`; bit-to-rail mapping and relation to command acceptance are unknown. | **Conditional read candidate** only after a source-backed mask mapping identifies a relevant bit. |
| `0F` `SetPowerState` | Power-state setter; one LE `uint16`; sample writes `0`. `Form1.cs:949` | Unknown legacy response shape | **Source-only; no live test.** Zero is not known to mean safe/OFF. | **Catalog-only: power switching with unknown bit semantics.** |
| `10` `GetMotorVelocity` | Nominal read; empty. `Form1.cs:868` | **Proven:** `80`, 16 bytes / 26-byte frame | **Proven exchange.** Sequence 3084 returned 16 zero bytes. PCTestApp does not parse the reply, so field layout and units remain unknown. Newer Drive `10` is `ResetCliffStop`. | **Installed/proven read-only.** No repeat presently justified. |
| `11` `SetMotorVelocity` | Actuator setter; left/right LE signed `int16` / 4 bytes (`-32768..32767`; physical units unknown). Button/random/fixed-timer sites `Form1.cs:897,992,1019`; the random source uses `Next(-1000,1000)`. | **Observed:** zero `80`/0 bytes; positive nonzero `82`/0 bytes | **Proven status behavior.** Fixed sequence 3072 zero `53000c11000400000000009a0145` -> `80`; sequence 3073 left `+1` `53010c1100040001000000ca3845` -> `82`; sequence 3073 left `+1000` `53010c11000400e80300000e6445` -> `82`; sequence 3074 cleanup zero `53020c11000400000000003bcb45` -> `80`. Both runs completed final `00`; the `+1000` run reported DMM 0.00 V/no change across the disconnected left output. | **Catalog-only: no larger value or repeat.** Value-dependent rejection is established for `+1` and `+1000`; `82` meaning and physical stop remain unknown. |
| `15` `ResetPC` | Reset; empty. `Form1.cs:955` | Unknown legacy response shape | **Source-only; no live test.** Reset target/effects are not established. | **Catalog-only: reset/destructive lifecycle change.** |
| `17` `GetLedState` | Nominal read; empty. `Form1.cs:680` | **Proven:** `80`, 18 bytes / 28-byte frame | **Proven exchange.** Sequence 3085 returned `000000000000000000000000ff0000ff0000`. PCTestApp prints but does not parse this reply. Newer Drive `17` is `SetDriveVelocities`. | **Installed/proven read-only.** Retain raw until legacy field semantics are recovered. |
| `18` `SetLedState` | Output setter; 18 `uint8` brightness values. Sites `Form1.cs:714,738,746` set one channel with other entries zero, all channels, or random channels; exact physical channels/ranges are unproved. | **Observed:** raw `82`, 0 bytes / 10-byte frame for the earlier value-1 set/restore and every completed index-at-`FF` set/restore through index 17 | **Proven status and physical-effect behavior, not application semantics.** The earlier index-0 value-1 round had no observed change. In the later interactive rounds, each fully transmitted setter returned raw `82`; all but index 15 produced a visible effect. Each exact-baseline restore also returned raw `82` while visibly restoring state. Raw `82` remains opaque and the physical observations do not establish electrical topology. | **Completed interactive mapping, separately authorized.** The two-phase index-at-255 mechanism preserved the exact live baseline and blocked each next index until an operator-confirmed power-cycle acknowledgment after raw `82`. |
| `19` `GetLedBlink` | Nominal read; empty. `Form1.cs:850` | **Proven:** `80`, 18 bytes / 28-byte frame | **Proven exchange.** Sequence 3086 returned `0000000000000000000000000000002a0000`. PCTestApp prints but does not parse this reply. Newer Drive/Head `19` is `SetServoRadians`. | **Installed/proven read-only.** Retain raw until legacy field semantics are recovered. |
| `1A` `SetLedBlink` | Output setter; 18 `uint8` values. `Form1.cs:781-812` zeroes all bytes, parses index `0..17` and value `0..255`, and sets only that index; `Form1.cs:815-844` also sends all-equal or random vectors. Timing semantics remain unknown. | No installed setter response: the earlier pilot stopped before command `1A` | **Fixed OFF-start pilot prepared offline; not executed.** It preserves both exact installed baselines, changes only wheel index 12 to source/installed-backed value `42`, then attempts exact blink and LED-state restores in reverse order. | **Separately authorized fixed pilot only.** Residual application ambiguity is explicit; no generic index/value/payload access. |
| `1B` `GetUnitInfo` | Read with possible telemetry-handshake side effect; empty. `Form1.cs:610` | **Proven:** `80`, 12 bytes / 22-byte frame | **Proven exchange.** Reported words `01020000`, `01020000`, `01020304`; they do not identify a unique image. Separate successor-framed host trial produced OUT but no application RX. | **Catalog-only now.** A future identity read is conditional on source mapping the returned version to the legacy handler; handshake side effect requires separate review. |
| `1C` `SetUnitInfo` | Identity write; 12 bytes; sample uses alternating `AA`/`55`. `Form1.cs:628` | Unknown legacy response shape | **Source-only; no live test.** Persistence is unknown. | **Catalog-only: identity/configuration write.** |
| `1D` `GetServoPosition` | Read; empty. `Form1.cs:778` | **Proven:** `80`, 4 bytes / 14-byte frame | **Proven exchange.** Returned raw values 2500 and 2730; not measured angles. This legacy meaning conflicts with newer `GetSensorInfo`. | **Catalog-only for motor rejection:** unrelated returned state; generation-sensitive despite the proved legacy exchange. |
| `1E` `SetServoPosition` | Actuator setter; two LE `uint16` / 4 bytes. UI suggests `0..3000`; parser only enforces `0..65535`. `Form1.cs:772` | Unknown legacy response shape | **Source-only; no live test.** | **Catalog-only: servo setter and servos are isolated.** |
| `1F` `GetSensorInfo` | Nominal read; empty. `Form1.cs:634` | **Proven:** `80`, 128 bytes / 138-byte frame | **Proven exchange.** Sequence 3087 returned the exact ascending byte sequence `00..7f`. PCTestApp prints but does not parse this reply. The identical-looking newer payload does not establish shared layout or semantics; newer Drive `1F` is `ResetCom` and newer Head `1F` is `DepthCamPower`. | **Installed/proven read-only.** Treat the payload as opaque. |
| `26` `DisableHeartbeat` | Telemetry-control command; empty. `Form1.cs:640` | Unknown legacy response shape | **Source-only; no live test.** There is no proved readback for heartbeat-enable state. Newer Drive `26` is `GetProjectorVersion`. | **Catalog-only: state-changing heartbeat control.** |
| `27` `ResetMotorPositions` | Odometry reset; empty. `Form1.cs:856` | Unknown legacy response shape | **Source-only; no live test.** | **Catalog-only: destructive state reset.** |
| `28` `GetBatteryInfo` | Nominal read; empty. `Form1.cs:928` | **Proven:** `80`, 8 bytes / 18-byte frame | **Proven exchange.** Sequence 3088 returned `fdff6f3f7c02ae00`. PCTestApp prints but does not parse this reply; field types, units, and values remain unknown. Newer Drive `28` moves the projector image. | **Installed/proven read-only.** Retain the payload raw. |

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
`82`, which remains opaque. Legacy PCTestApp host source is available and
source-backs its getter request syntax. The missing evidence is specifically
the installed legacy firmware image, or matching old firmware source/map output
containing its S/E dispatcher, response enum, and command-`11` handler. Without
that device-side artifact, no read-only reply field is source-proved to expose
the rejection cause.

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
were fully screened offline. The installer is 409,855,488 bytes, MD5
`ea798ca7072da6c40f7c784e82b94efe`, and SHA-1
`ac1c25931d23702f6f9dd0576fce63b6bf1dfc28`. Recursive extraction inspected
72,360 decompressed files. It found no Marvin firmware/source, `PID_4444`,
requested legacy protocol identifier, matching UnitInfo payload, or relevant
PDB path. The six `VID_045E` hits were unrelated .NET prerequisite metadata
for `PID_0707`. The bulky extracted tree was removed after inspection; the
detailed private report is retained outside this repository.

This exhausts the identified public MRDS installer route. The exact installed
S/E firmware or command-`11` handler source must now come from Microsoft or
other internal recovery media, or from a separately reviewed non-destructive
controller dump. The user has rejected JTAG. No currently justified live
command resolves raw `82`.

## Fixed survey and future matrix

### Completed fixed read-only survey

The fixed runner used for the separately authorized survey was:

```bash
python3 -B -m tools.marvin_legacy_disconnected_getter_survey \
  --disconnected-load-legacy-getter-survey \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon \
  --expected-physical-port "$REVIEWED_PHYSICAL_PORT" \
  --output "$NEW_PRIVATE_CAPTURE" --run
```

| Order | Sequence / legacy PCTestApp getter | Exact fixed request | Proven installed reply |
|---:|---|---|---|
| 1 | 3083 / `0A GetRawMotorPWM` | `530b0c0a00000071a745` | `80`, 8 bytes: `0000000000000000` |
| 2 | 3084 / `10 GetMotorVelocity` | `530c0c10000000770845` | `80`, 16 zero bytes |
| 3 | 3085 / `17 GetLedState` | `530d0c1700000077ad45` | `80`, 18 bytes: `000000000000000000000000ff0000ff0000` |
| 4 | 3086 / `19 GetLedBlink` | `530e0c19000000757645` | `80`, 18 bytes: `0000000000000000000000000000002a0000` |
| 5 | 3087 / `1F GetSensorInfo` | `530f0c1f000000742f45` | `80`, 128 bytes: exact ascending `00..7f` |
| 6 | 3088 / `28 GetBatteryInfo` | `53100c28000000783445` | `80`, 8 bytes: `fdff6f3f7c02ae00` |

The immutable transcript SHA-256 is
`0fac72b34e77fbb432c9a43f786296515cf46fb8dac6ae6b080b5b03f077e24b`.
The maximum is six serial writes and 60 application TX bytes, strictly in the
listed order. Each request waits for one complete CRC-valid response with the
same sequence and command and raw response `80` before the next request.
Payload length and bytes are accepted only as framing-delimited opaque evidence;
no expected length or field meaning is invented. Total serial RX is bounded at
8192 bytes.

The run stops immediately, without retry, reconnect, suffix resend, or another
request, on partial/uncertain TX, timeout, framing/CRC corruption, unexpected
sequence/command, non-`80` raw response, extra/ambiguous frame, identity change,
recorder/accounting fault, or evidence-sealing failure. It sends no setter,
reset, heartbeat, power/config/identity write, malformed enumeration, or
successor-framed command. The named offline encoders are allowlisted; no
arbitrary-command interface is added.

The completed run received all six CRC-valid replies with matching sequence and
command and raw status `80`. Evidence accounting recorded exactly 6 writes, 60
application TX bytes, 256 RX bytes, and zero uncertain TX bytes. All 13
`SHA256SUMS` entries verified. No private artifact path is published.

PCTestApp establishes the legacy request names and syntax but only prints these
replies as raw bytes; it does not parse their layouts. `DB9Cmds.xlsx` describes
newer firmware and must not be used to label the installed S/E payloads. Except
for the explicitly qualified four-zero-word grouping in `GetRawMotorPWM`, the
payloads above remain opaque.

### Failed reversible LED-state round trip

**This is software readiness, not live authorization.** The fixed runner has a
distinct literal setter scope and retains the disconnected-load requirements:
both motor POWER plugs disconnected, servos isolated, both encoder feedback
harnesses connected, robot secured on blocks, operator at the external cutoff,
and unprivileged usbmon.

Exact offline dry-run review, without `--run`:

```sh
python3 -B -m tools.marvin_legacy_disconnected_led_state \
  --disconnected-load-led-state-round-trip \
  --authorize-unvalidated-led-state-round-trip \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

| Step | Sequence / command | Exact fixed request |
|---:|---|---|
| 1 | 3089 / `17 GetLedState` baseline | `53110c1700000075f145` |
| 2 | 3090 / `18 SetLedState` index 0 from `0` to `1` | `53120c18001200010000000000000000000000ff0000ff00008ab845` |
| 3 | 3091 / `17 GetLedState` test verification | `53130c17000000741345` |
| 4 | 3092 / `18 SetLedState` exact baseline restore | `53140c18001200000000000000000000000000ff0000ff000010fb45` |
| 5 | 3093 / `17 GetLedState` restore verification | `53150c17000000747545` |

The baseline gate requires raw status `80` and exact 18-byte payload
`000000000000000000000000ff0000ff0000` before any setter. The test vector is
`010000000000000000000000ff0000ff0000`; all bytes except index 0 are retained.
The verification getter must return that exact vector. The restore setter then
uses the exact baseline, and the final getter is admitted only after one clean,
CRC-valid, matching command-`18` raw-`80` restore response. Setter response
payloads remain opaque because PCTestApp does not define their shape.

Once any test-setter byte may have been submitted, the runner makes exactly one
fixed restore attempt on the pinned identity within a separate bounded cleanup,
including after partial/uncertain TX, timeout, non-`80`, malformed or mismatched
response, extra frame, or interruption. It never retries or reconnects. The
maximum successful transcript is 5 writes and 86 application TX bytes; serial
RX is capped at 8192 bytes. Full usbmon, serial/USB accounting, and evidence
sealing remain mandatory.

Software reports baseline, test-setter response, test-vector getter, restore
response, and restored-baseline getter verification separately. It does not
infer a physical LED effect. The operator must separately record visible LED
state before, during, and after the trial; no visible change does not invalidate
the raw protocol evidence or establish channel meaning.

In the separately authorized run, sequence 3089 returned raw `80` with the
exact required baseline. The complete 28-byte sequence-3090 test setter was
accepted by the host write and received CRC-valid matching response
`53120c18820000d6fe45`: raw `82`, empty payload. The mandatory complete
28-byte sequence-3092 exact-baseline restore write was then attempted once and
received CRC-valid matching response `53140c18820000d69845`: raw `82`, empty
payload. Neither verification getter was sent. Accounting recorded 3 writes,
66 accepted TX bytes, 48 RX bytes, and zero uncertain TX bytes; those byte
totals match one 28-byte getter reply and two 10-byte setter replies. The
operator observed no visible LED change and cut power after the failure.

Both the test setter and unchanged-baseline restore were rejected at the raw
status level. This does not decode `82`, prove that either payload was applied,
or establish any state change. The failed-run facts supplied to this repository
did not include a `SHA256SUMS` verification result, and no capture manifest is
available in this workspace, so the usual 13-entry evidence-seal claim cannot
be made. The shared lifecycle attempts to seal failed captures, but successful
failure-capture sealing remains unverified here.

### Interactive one-index LED mapping

**This is software readiness, not live authorization.** Value `1` was not a
useful physical mapping stimulus: it may be invisible, observation timing was
not fixed, and the connected LED index is unknown. The replacement mechanism
uses one script and exactly one operator-selected index `0..17` per round.
Value `255` is fixed; payload, sequence, retry, duration, and count are not
configurable.

All round artifacts must be new sibling directories under one existing
`MAPPING_ROOT`. A root-local control record blocks another set phase until the
current round has either a getter-verified restore or an explicit
operator-confirmed power-cycle reset. The control record names the exact set
evidence path and SHA-256 of its complete `SHA256SUMS`; restore rehashes every
sealed file and rejects missing, added, changed, duplicate, unsafe, or symlinked
entries.

For index `N`, fixed sequences are `3200 + 4*N` through `3203 + 4*N`. The set
phase sends only:

1. `17 GetLedState` at the first sequence, requiring raw `80` and exactly 18
   baseline bytes.
2. `18 SetLedState` at the second sequence with an 18-byte one-hot vector:
   index `N` is `FF`, every other byte is zero.

For example, index 0 uses:

```text
53800c17000000697045
53810c18001200ff0000000000000000000000000000000000b1db45
```

Offline review, then separately authorized set:

```sh
python3 -B -m tools.marvin_legacy_led_mapper \
  --phase set --index 0 \
  --disconnected-load-led-mapping-phase \
  --authorize-unvalidated-led-mapping-phase \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon

python3 -B -m tools.marvin_legacy_led_mapper \
  --phase set --index 0 --output "$MAPPING_ROOT/set-index-0" --run \
  --expected-physical-port "$REVIEWED_PHYSICAL_PORT" \
  --disconnected-load-led-mapping-phase \
  --authorize-unvalidated-led-mapping-phase \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

The setter response is retained if its raw field is either `80` or `82`; no
meaning is assigned to either. After any possible setter submission the set
phase exits and reports **`RESTORE_REQUIRED` regardless of response**. It does
not send a verification getter or automatic restore, leaving a bounded
operator-controlled observation interval. The operator records one of
`changed`, `no_change`, or `uncertain`, then immediately runs the tied restore
phase.

Restore review and execution use no index or payload argument. The script
verifies the exact sealed set artifact, reads its captured baseline, constructs
one command-`18` baseline restore at the third fixed sequence, and records the
operator observation in the restore evidence:

```sh
python3 -B -m tools.marvin_legacy_led_mapper \
  --phase restore --set-evidence "$MAPPING_ROOT/set-index-0" \
  --led-observation changed \
  --disconnected-load-led-mapping-phase \
  --authorize-unvalidated-led-mapping-phase \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon

python3 -B -m tools.marvin_legacy_led_mapper \
  --phase restore --set-evidence "$MAPPING_ROOT/set-index-0" \
  --led-observation changed \
  --output "$MAPPING_ROOT/restore-index-0" --run \
  --expected-physical-port "$REVIEWED_PHYSICAL_PORT" \
  --disconnected-load-led-mapping-phase \
  --authorize-unvalidated-led-mapping-phase \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

Only a CRC-valid matching raw-`80` restore response permits the fourth-sequence
`17` getter; that getter must equal the exact captured baseline before the round
is marked restored. Raw `82`, timeout, partial/uncertain TX, malformed framing,
CRC/correlation fault, extra frame, identity change, accounting fault, or
sealing fault ends the phase without retry or reconnect and blocks another
restore attempt. If restoration is not getter-verified, the operator may
power-cycle and then record that external action without hardware access:

```sh
python3 -B -m tools.marvin_legacy_led_mapper \
  --phase acknowledge-power-cycle \
  --set-evidence "$MAPPING_ROOT/set-index-0" \
  --led-observation no_change --confirm-power-cycle-reset
```

The acknowledgment is an operator declaration, not software verification. Set
and restore phases each allow at most 2 writes and 38 application TX bytes,
with 8192-byte serial RX bounds and independently sealed evidence. Both motor
POWER plugs and servos remain disconnected; no DMM is requested. A visible
change maps only the observed connected LED under that round's setup; it does
not establish command acceptance semantics, complete channel identity, or
physical safety.

#### Completed live interactive LED mapping

Every completed individual round through index 17 followed the same observed
protocol pattern: the baseline getter returned matching CRC-valid raw `80`;
the command-`18` set was fully transmitted and returned matching CRC-valid raw
`82`; the exact captured baseline restore was fully transmitted and returned
matching CRC-valid raw `82`; and the visible baseline state returned. Index 15
had no visible set effect; it is not labelled unused. Because raw `82` prevents
getter verification, an operator-confirmed power-cycle acknowledgment was
recorded before the next round and after index 17. These observations do not
decode `82`, prove application acknowledgment, or establish electrical
topology.

Individual operator-controlled rounds established these visible effects under
the disconnected-load setup:

| Index at `FF` | Operator observation | Supply observation |
|---:|---|---|
| 0 | robot-left position 0 red ON; exact restore visibly restored | not recorded |
| 1 | robot-left position 0 blue ON; wheel LEDs OFF | not recorded |
| 2 | robot-left position 1 red ON; wheel LEDs stayed ON during the set; exact restore turned this red LED OFF and wheel LEDs OFF | not recorded |
| 3 | robot-left position 1 blue ON; wheel LEDs OFF | baseline 12.6 V / 0.40 A; set 12.6 V / 0.29 A; restore 12.6 V / 0.40 A |
| 4 | robot-left position 2 red ON; wheel LEDs OFF | set 12.6 V / 0.30 A |
| 5 | robot-left position 2 blue ON; wheel LEDs OFF | set 12.3 V / 0.30 A |
| 6 | robot-right position 0 red ON; wheel LEDs OFF | set 12.3 V / 0.29 A |
| 7 | robot-right position 0 blue ON; wheel LEDs OFF | set 12.4 V / 0.44 A |
| 8 | robot-right position 1 red ON; wheel LEDs OFF | set 12.3 V / 0.31 A |
| 9 | robot-right position 1 blue ON; wheel LEDs and bottom red bar OFF | set 12.3 V / 0.31 A |
| 10 | robot-right position 2 red ON; wheel LEDs and bottom red bar OFF | set current approximately 0.31 A |
| 11 | robot-right position 2 blue ON; wheel LEDs and bottom red bar OFF | set current approximately 0.32 A |
| 12 | wheel LEDs ON; bottom blinking red bar OFF; exact restore turned wheel LEDs OFF and the bottom bar back ON | not recorded |
| 13 | blue LED on the robot's front left ON; wheel LEDs and bottom flashing red bar OFF; exact baseline restore visibly complete | set 12.3 V / 0.28 A |
| 14 | red LED on the robot's front right ON; wheel LEDs and bottom bar OFF; exact baseline restore visibly complete | set 12.3 V / 0.29 A |
| 15 | no visible change; after exact baseline restore, visible state exactly matched baseline | not recorded |
| 16 | bottom bar changed from flashing red to solid green; restore returned it to flashing red and visibly restored all state | set 12.3 V / 0.33 A |
| 17 | bottom bar changed from flashing red to solid blue; restore returned it to flashing red and visibly restored all state | set 12.3 V / 0.33 A |

The final operator-confirmed side convention is indices 0 through 5 for the
robot-left display and indices 6 through 11 for the robot-right display. Within
each side, consecutive red/blue pairs represent positions 0, 1, and 2.

Through index 14, the operator observed that setting an index also turned OFF
the blinking red LED bar on the bottom of the robot and restoring the exact
baseline turned that bar back ON. Index 15 had no visible effect; indices 16
and 17 changed the bar to solid green and solid blue respectively. These are
operator observations, not inferred circuit relationships.

Supply readings are retained exactly as operator observations without
interpretation; the inexpensive supply varied between 12.3 V and 12.6 V.
All 18 payload indices now have an operator-observed disposition.

For indices 13 through 17, all 13 entries in each of the ten set/restore
`SHA256SUMS` manifests verified. Each set phase recorded 38 accepted TX, 38 RX,
zero uncertain TX, matching USB OUT/IN accounting, an 18-byte raw-`80` baseline
`000000000000000000000000000000ff0000`, and a zero-payload raw-`82` setter
response. Each restore phase recorded 28 accepted TX, 10 RX, zero uncertain TX,
matching USB accounting, and a zero-payload raw-`82` restore response; therefore
no verification getter was sent.

| Index | Exact transmitted set frames; transcript SHA-256 | Exact set response frames | Planned restore transcript; transcript SHA-256 | Exact restore response frame |
|---:|---|---|---|---|
| 13 | `53b40c170000006d0445`<br>`53b50c1800120000000000000000000000000000ff0000000083c045`<br>`9413bd4a9da1739a581cbb49ea97b0d6317769f8df87d68deed60ec094b0288b` | `53b40c17801200000000000000000000000000000000ff0000daf945`<br>`53b50c18820000cfe945` | restore `53b60c18001200000000000000000000000000000000ff0000431b45`<br>gated getter not sent `53b70c170000006d3745`<br>`065898e8cc089cf79892abed7df7bf3ca852542ce824b27dea1eec91a52a043d` | `53b60c18820000cfda45` |
| 14 | `53b80c170000006dc845`<br>`53b90c180012000000000000000000000000000000ff00000032ff45`<br>`d2c5605e032a5e6f05fff4e399e97c7b4c89e704086ae42c605036ab311221ef` | `53b80c17801200000000000000000000000000000000ff00004fc645`<br>`53b90c18820000cf2545` | restore `53ba0c18001200000000000000000000000000000000ff0000d62445`<br>gated getter not sent `53bb0c170000006dfb45`<br>`e5c8a1238de9c59b065a819f02b9a173cfc3b524086e2c605924c4534493cf6b` | `53ba0c18820000cf1645` |
| 15 | `53bc0c170000006c4c45`<br>`53bd0c18001200000000000000000000000000000000ff0000400e45`<br>`e04ec25c081867a2e3a9c6578259490ab33a01c67ee441c28cd0fd433fe81196` | `53bc0c17801200000000000000000000000000000000ff00003d1345`<br>`53bd0c18820000cea145` | restore `53be0c18001200000000000000000000000000000000ff0000a4f145`<br>gated getter not sent `53bf0c170000006c7f45`<br>`5b2bee088e1fa790ac72c7d8cf14d991f0e0fefb97cbd00bb70d1457805c35ec` | `53be0c18820000ce9245` |
| 16 | `53c00c1700000067b045`<br>`53c10c1800120000000000000000000000000000000000ff00c2d845`<br>`77146992279c7bdc5843b903ee171746d7b06eb5a46144228cd62bd538a02777` | `53c00c17801200000000000000000000000000000000ff0000ce0545`<br>`53c10c18820000c55d45` | restore `53c20c18001200000000000000000000000000000000ff000057e745`<br>gated getter not sent `53c30c17000000678345`<br>`42e13b82cf4c71493910a69ab51249108d2803caa919dd29e38dc4916fac3af0` | `53c20c18820000c56e45` |
| 17 | `53c40c17000000663445`<br>`53c50c180012000000000000000000000000000000000000ffb1bd45`<br>`aeec02a1bdec220eb8f2885bf8797d48fd23516b450323bfbcbf92d6bc04817b` | `53c40c17801200000000000000000000000000000000ff0000bcd045`<br>`53c50c18820000c4d945` | restore `53c60c18001200000000000000000000000000000000ff0000253245`<br>gated getter not sent `53c70c17000000660745`<br>`f934ca86887d65df3f9e7bec21dddd33c7a035efdbff6b8c6d6f3f4c0be19912` | `53c60c18820000c4ea45` |

#### Retired wheel-LED blink pilot

The one prepared `wheel-led-blink-pilot` live set phase stopped at its first
precondition on 2026-09-19 UTC. It sent only sequence 3276 command `17`,
`53cc0c17000000677c45`, and received one matching CRC-valid raw-`80` 18-byte
reply:

```text
53cc0c17801200000000000000000000000000000000ff00005b3a45
```

The payload was `000000000000000000000000000000ff0000`: index 12 was
`00`, not the pilot's required `FF`. The runner stopped with
`Wheel LED state byte 12 must be FF before the blink pilot`. Its serial
accounting was one submission, 10 accepted TX, 28 RX, and zero uncertain TX;
`setter_may_have_been_submitted` and `restore_required` were both false.
Usbmon independently retained one successful 10-byte bulk OUT and one
successful 28-byte bulk IN. No command `19`, `1A`, or `18` was sent.

All 12 outer manifest entries verified. The nested capture manifest also
verified, but the recorder ended with failed status because it was signaled
during shutdown after the intentional precondition fault. This is sealed
failure evidence, not a normal successful-session seal. The operator cut power
afterward and reported that the earlier issue causing wheel LEDs to start ON
had been fixed; wheels now start OFF. That power and visible-state statement is
an operator observation, not software verification.

The earlier two-phase executable was retired. Its replacement is a single
bounded process so interruption or a protocol fault during the fixed
three-second observation window enters the same mandatory cleanup. This is the
strongest achievable software cleanup, not proof that an opaque setter or
restore was applied.

#### Fixed OFF-start wheel LED blink pilot

**Prepared offline; never executed. This is software readiness, not live
authorization.** It requires the exact installed OFF-start LED-state baseline
`000000000000000000000000000000ff0000` and exact blink baseline
`0000000000000000000000000000002a0000`. Any mismatch or non-`80` baseline
reply stops before a setter.

The fixed transcript is:

| Step | Sequence/command | Exact request |
|---|---:|---|
| LED-state baseline | 3277 / `17` | `53cd0c1700000066ad45` |
| blink baseline | 3278 / `19` | `53ce0c19000000647645` |
| wheel state ON | 3279 / `18` | `53cf0c18001200000000000000000000000000ff0000ff00008b4245` |
| wheel blink pilot | 3280 / `1A` | `53d00c1a0012000000000000000000000000002a00002a000096f345` |
| exact blink restore | 3281 / `1A` | `53d10c1a0012000000000000000000000000000000002a0000ccac45` |
| exact LED-state restore | 3282 / `18` | `53d20c18001200000000000000000000000000000000ff00009a7245` |
| conditional blink getter | 3283 / `19` | `53d30c1900000067fb45` |
| conditional state getter | 3284 / `17` | `53d40c1700000064a445` |

The state setter changes only byte 12 from `00` to `FF`. The blink setter
changes only byte 12 from `00` to `2A`; byte 15 remains the installed baseline
`2A`. Raw `80` and `82` setter replies are recorded without decoding and the
pilot continues to its fixed three-second visual observation window. The
operator should report whether the wheel LEDs visibly blinked.

Before each setter syscall, that component is conservatively marked
`may_have_applied`. In cleanup, every still-required restore is attempted
exactly once, blink first and LED state second, across raw `80`/`82`, timeout,
CRC/correlation/extra-frame fault, partial/uncertain TX, interruption, or
journal failure. Restore-attempt state is recorded before each restore syscall,
and the syscall precedes its journal event, so a journal failure cannot suppress
the second restore. There is no retry or reconnect.

Only a matching CRC-valid raw-`80` restore permits its corresponding getter.
Raw `82`, a missing response, or any other fault leaves application restoration
unverified even when the exact restore write was fully accepted. The root-local
state lock then blocks another LED mapper or blink phase until the operator
confirms visible restoration, power cycles the controller, and records both
facts offline against the exact sealed evidence manifest.

Offline review:

```sh
python3 -B -m tools.marvin_legacy_wheel_led_blink \
  --disconnected-load-wheel-led-blink-pilot \
  --authorize-unvalidated-wheel-led-blink-pilot \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

Separately authorized live invocation, not authorized by this document:

```sh
python3 -B -m tools.marvin_legacy_wheel_led_blink \
  --output "$MAPPING_ROOT/wheel-led-blink-off-start" --run \
  --expected-physical-port "$REVIEWED_PHYSICAL_PORT" \
  --disconnected-load-wheel-led-blink-pilot \
  --authorize-unvalidated-wheel-led-blink-pilot \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

If either baseline is not getter-reverified after cleanup, power off, confirm
the visible baseline has returned, power cycle, then record the external
recovery without device access:

```sh
python3 -B -m tools.marvin_legacy_wheel_led_blink \
  --acknowledge-restoration \
  --evidence "$MAPPING_ROOT/wheel-led-blink-off-start" \
  --physical-restoration-confirmed --power-cycle-confirmed
```

The successful maximum is 8 writes and 152 application TX bytes; a raw-`82`
cleanup path skips both conditional getters and uses 6 writes / 132 TX bytes.
Serial RX is capped at 8192 bytes. Full usbmon/accounting and shared evidence
sealing are mandatory. Both motor power plugs and all servos remain physically
disconnected; no motion or DMM observation is requested. Successful writes,
CRC-valid frames, visible blinking, restore attempts, and power cycling do not
decode raw status or prove application acknowledgment, electrical topology, or
physical safety.

#### Fixed left-attention photo pattern

The historical left-side photo of the indices-1-through-5 pattern shows two
magenta sections and one blue-only triangle because the fixed pattern omitted
robot-left position-0 red index 0. The pattern was visibly restored after the
photo.

The named `left-attention-photo` pattern is retained as a historical exact
transcript, not a complete all-left pattern. It uses sequence block 3272
through 3275. Its set phase first reads the current 18-byte baseline at
sequence 3272, then derives one command-`18` payload by setting only indices 1,
2, 3, 4, and 5 to `FF`. Index 0 and all indices 6 through 17 remain
byte-for-byte equal to that captured baseline. There is no arbitrary mask or
value.

Offline review and separately authorized set:

```sh
python3 -B -m tools.marvin_legacy_led_mapper \
  --phase set --pattern left-attention-photo \
  --disconnected-load-led-mapping-phase \
  --authorize-unvalidated-led-mapping-phase \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon

python3 -B -m tools.marvin_legacy_led_mapper \
  --phase set --pattern left-attention-photo \
  --output "$MAPPING_ROOT/set-left-attention-photo" --run \
  --expected-physical-port "$REVIEWED_PHYSICAL_PORT" \
  --disconnected-load-led-mapping-phase \
  --authorize-unvalidated-led-mapping-phase \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

The fixed baseline getter frame is `53c80c1700000066f845`. The setter frame is
intentionally generated only after that getter; precomputing it from an older
baseline would violate the preservation rule. For the last documented baseline
`000000000000000000000000ff0000ff0000`, the derived payload would be
`00ffffffffff000000000000ff0000ff0000` and the sequence-3273 frame would be
`53c90c1800120000ffffffffff000000000000ff0000ff000024cc45`. This example is
not a baseline expectation.

After the operator takes the photo and records `changed`, `no_change`, or
`uncertain`, restoration uses the same `--phase restore --set-evidence ...`
workflow above. The bound sequence-3274 restore sends the exact captured
baseline once; only raw `80` permits the sequence-3275 getter verification.
Raw `82` requires the existing power-cycle fallback. Each phase retains the
same 2-write / 38-TX / 8192-RX bounds, full sealing, and no-retry/no-reconnect
rules as an individual mapping round.

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

Installed response lengths are now established for `0A`, `10`, `17`, `19`,
`1F`, and `28`, but their field layouts and meanings are not. The fixed survey
therefore preserves each payload without decoding.
No setter, reset, flash/configuration write, power-state change, heartbeat
control, raw PWM setter, servo/motor setter, malformed packet, enumeration
sweep, or successor-framed command is planned.
