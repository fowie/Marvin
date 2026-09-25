# Marvin

Independently authored Linux tooling and engineering findings for the original
Microsoft Marvin robot. Development and CI are **offline/read-only by default**.

## Offline getting started

From the repository root, with Python 3 available:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m unittest discover -s tests -v
marvin --help
```

Tests use self-contained protocol facts, reviewed fixtures and mocked transport
boundaries. No robot, USB/serial access, private archive or elevated privileges
are needed. Native serial-adapter tests use host-created PTYs with synthetic USB
evidence, not robot devices. Installing dependencies may require network access;
tests are offline.

## Operator controls

`marvin.Marvin` is the small public Python facade. `python -m marvin` provides
the matching human-facing CLI. Both are offline by default:

```sh
python -m marvin --help
python -m marvin status
python -m marvin sensors
python -m marvin drive forward
python -m marvin stop
python -m marvin teleop
python -m marvin camera up 5
python -m marvin camera down 5
python -m marvin camera center
python -m marvin projector status
python -m marvin projector power on
```

Live actions require `--run`, the reviewed physical USB port, a new evidence
directory, and `--confirm-safe-setup`. For example:

```sh
python -m marvin drive rotate-left \
  --run --expected-physical-port 1-3 \
  --output evidence/rotate-left-001 --confirm-safe-setup
```

Each live drive command is exactly one proved 0.25-second raw-PWM step and owns
one mandatory all-zero cleanup attempt. Camera up is limited to the directly
observed `2500 -> 2000` profile (approximately five degrees) and owns its
`[2500,2730]` restore. Cleanup and restore run when an operation succeeds,
fails, or receives Ctrl-C; write acceptance and protocol correlation still do
not prove physical stop or restoration.

There is deliberately no arbitrary command, PWM, duration or servo target.
`stop` sends the fixed all-zero raw-PWM request and conditionally verifies the
zero getter; it is a stop request with previously observed stopped wheels, not
proof of braking, de-energization or causation. Camera down 5 provides the fixed
offline `2500 -> 3000` inverse-hypothesis plan, but live camera-down remains
blocked until the installed linkage is observed. Use the external cutoff
whenever cleanup or restoration is reported uncertain.

`teleop` is a line-oriented wrapper over only the proved bounded drive and stop
primitives: `w` forward, `s` backward, `a` rotate left, `d` rotate right, `x`
stop, and `q` stop then quit. Live mode creates one `run-NNNN` evidence
subdirectory per action. EOF and Ctrl-C make one stop request before exit when
one has not just succeeded; action failure ends the loop without retry.

Camera and projector use one internal two-word servo-axis implementation.
Changing one axis constructs the complete setter pair from `[2500,2730]` and
retains the sibling word. Camera is proved on word 0 near its 2500 baseline.
The same-model projector is historically expected on word 1 near 2730, but it
is physically disconnected and its routing, direction, and local scale have not
been exercised. `projector status` and offline `projector center` expose that
boundary. Status also includes the fixed `[2500,2730] -> [2500,2230] -> restore`
plan; its expected AX goal, routing, direction and scale remain hypotheses, and
the inverse `+500` target is prohibited. All live projector writes and movement
are rejected.

Recovered newer source labels command `0x27` as projector power, but that source
map conflicts with the installed legacy profile: installed `0x1D/0x1E` are the
proved servo getter/setter while the recovered map assigns those IDs to sonar
operations. The sealed `0x27` collision experiment produced correlated opaque
responses but no observed illumination, fan, LED, click or other power effect.
`projector power on|off` therefore preserves offline transcript evidence only;
live projector power and source-only shutter commands `0x2B/0x2C` are disabled
until an installed-legacy mapping is established.

Python API:

```python
from marvin import Marvin

robot = Marvin()                 # offline plans; no hardware access
print(robot.status())
print(robot.drive("forward"))
print(robot.stop())
print(robot.camera_up(5))
print(robot.camera_down(5))      # offline fixed plan; live remains blocked
```

`Marvin().sensors()` (or `python -m marvin sensors`) returns the exact four
request frames and response schema without opening hardware. Embedded live
applications may construct `Marvin(run=True, sensor_transport=...,
sensor_ownership_key=..., sensor_expected_identity=...)`; `sensors()` then
uses one bounded, identity-pinned `LegacyClient` session for reported
controller words, the raw power mask, raw telemetry, documented proximity
mappings, unresolved cliff/bump fields, motor/encoder values, and two raw
servo-position words. The CLI intentionally has no serial-device constructor
or arbitrary command path. Dedicated `GetBatteryInfo` remains excluded because
its installed eight-byte response has no proven legacy decoder.

## Confirmed findings and limits

Eight guarded 10-byte requests covering six legacy `S`/`E` getters
(`00`, `04`, `0C`, `0E`, `1B`, `1D`) returned matching sequence/command,
status `80`, CRC-valid replies: configuration, raw telemetry, diagnostics,
power state, unit information and servo-position readback. This establishes
those exchanges, not physical units, exact firmware identity, actuator safety
or emergency-stop/watchdog behavior.

**Do not substitute successor EFBE commands for legacy S/E commands.**
Opcode meanings collide, including getters versus motion/reset operations.
Existing modern-protocol defaults and behavior are preserved, not silently
switched to legacy. Historical broad-sweep tools remain experimental and are
**not recommended for the known-working legacy device**.

Transmit APIs validate every byte against an explicit profile: `modern` remains
the default, the named legacy wrapper selects `legacy`, and historical campaign
experiments select `experimental-successor`. Authorization flags do not bypass
request-shape checks. The one-shot CLI accepts named requests, not arbitrary hex.
Stateful identification queries require separate telemetry-state acknowledgment.
The legacy `get-unit-info --run` command requires
`--allow-telemetry-state-change`; boot observation separately requires
`--allow-line-state-change` for its DTR/RTS requests and forwards it through
the coordinator to the serial capture boundary. Direct serial capture and
coordinator sessions likewise require separate `--allow-line-state-change`
consent whenever DTR or RTS is asserted, before or after opening the port.
Historical campaign execution requires `--allow-line-state-trials`, recorded
and forwarded to each segment. Validated coordinator GetConfig/schedule
`allow_line_state_trial=True` consent also authorizes the serial line request.
Generic consent never waives named-query line/framing restrictions. Low/low
defaults, including the fixed legacy wrapper settings, remain usable without
fabricated consent. Neither acknowledgment widens a transmit profile.
Safety acknowledgments and boolean selectors reject strings and integers rather
than interpreting their truthiness as consent.

Hardware work requires separate operator authorization and a reviewed physical
test plan. Keep actuator power and signals isolated; software cannot verify that
isolation. Routine development never authorizes firmware programming, resets,
power/configuration writes, motion, servo/LED setters or bypassing interlocks.
The damaged PEND TXCVR USB-A socket and reported hub port-4 over-current remain
unresolved. Keep that branch unused; successful communication does not clear
electrical faults.

The offline-default `tools.marvin_legacy_drive_step` facade exposes only the
proved named raw-PWM mappings: `forward`, `backward`, `rotate-left`, and
`rotate-right`. It requires explicit `--duration 0.25 --raw-pwm 2000`; other
values are intentionally rejected. Live use
retains the fixed pilot's exact-zero baseline, one bounded nonzero dwell,
single all-zero cleanup attempt, evidence lock, and operator declarations.
These are raw wire values and bounded observations, not calibrated movement or
proof of stop/cleanup effect. For an offline plan:

```sh
python -m tools.marvin_legacy_drive_step forward --duration 0.25 --raw-pwm 2000
```

The separate offline-default stop-request primitive reuses the literal all-zero
frame already captured before observed stopped wheels:

```sh
python -m tools.marvin_legacy_stop
```

Its public Python API is `tools.marvin_legacy_stop.prepare()` and
`run_stop(...)`. A live run sends exactly one fixed all-zero `0B` request and,
only after its proved raw-`80` response, one fixed `0A` getter. This is a
zero-PWM request with protocol evidence, not proof of braking, de-energization,
cleanup causation, or physical stop.

The explicitly selected [left-motor-powered read-only observation](docs/legacy-live.md#left-motor-powered-read-only-observation)
has one separately authorized recorded result and remains **under HARDWARE HOLD**: exactly one fixed
ReadRawData (sequence 2304), no zero/setter or power switch. It truthfully does
not claim full isolation or supply-OFF. Its 3-second collector deadline is not
a wall-time/power-dwell guarantee; separate cleanup and USB tail remain.
Fresh future operator confirmation and a new bounded physical power plan are
required. A clean observation is not a motor-stop or commissioning result.

## Contributing

Follow [the development and safety rules](AGENTS.md): submit changes through
issue-linked pull requests. **Do not merge or enable auto-merge without the
owner's approval.** Publication acceptance is tracked in
[#6](https://github.com/fowie/Marvin/issues/6), under
[Epic #1](https://github.com/fowie/Marvin/issues/1).

## Documentation

- [Capability map and bring-up plan](docs/marvin-bringup-plan.md)
- [Persistent legacy getter client and offline API example](docs/legacy-client.md)
- [Bounded read-only polling, recording and offline inspection](docs/legacy-polling.md)
- [Cliff/proximity command reconciliation and controlled mapping plan](docs/cliff-proximity-mapping.md)
- [Explicitly guarded LIVE serial/USB collection](docs/legacy-live.md)
- [Isolated one-shot zero-velocity characterization](docs/legacy-zero.md)
- [Offline projector/front-camera tilt servo mapping plan](docs/legacy-servo-mapping.md)
- [Motor-power-OFF preparation (offline default; no power-ON permission)](docs/legacy-motor-power-off-prep.md)
- [Rear/top microphone array discovery and capture diagnostics](docs/microphone-array-discovery.md)
- Offline-default microphone status and bounded capture: `python -m tools.marvin_microphone`
- [Complete source/evidence command catalogue and held test matrix](docs/marvin-command-catalog.md)
- [Full historical/modern command catalogue](docs/marvin-command-map.json)
- [Configuration export: 108 bytes, 27 words](docs/marvin-configuration.json)
- [Raw telemetry snapshot: 134 bytes, 82 fields](docs/marvin-telemetry-snapshot.json)
- [Detailed lab findings and historical experiments](docs/lab-findings.md)
- [Publication provenance, evidence citations and exclusions](docs/provenance.md)
- [Generated protocol-fact catalogue](data/protocol-catalog.json)
