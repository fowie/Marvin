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
marvin microphone status
marvin microphone list --run --route direct-host \
  --usb-path CURRENT_MICROPHONE_USB_PATH \
  --device hw:CARD=Array,DEV=0
python -m marvin camera status
python -m marvin camera capture private/frame.jpg
python -m marvin drive forward
python -m marvin stop
python -m marvin teleop
python -m marvin camera up 5
python -m marvin camera down 5
python -m marvin camera center
python -m marvin projector status
python -m marvin projector power on
python -m marvin leds status
python -m marvin leds plan left-position-0-red
python -m marvin leds attention-check
python -m marvin leds wheel-blink
```

Live actions require `--run`, the reviewed physical USB port, a new evidence
directory, and `--confirm-safe-setup`. For example:

```sh
python -m marvin drive rotate-left \
  --run --expected-physical-port 1-3 \
  --output evidence/rotate-left-001 --confirm-safe-setup
```

Each live drive command waits through its complete correlated-response window,
then observes exactly 0.25 seconds before one mandatory all-zero cleanup
attempt. A separate fixed 0.25-second host admission margin keeps that full
post-response observation inside a one-second setter-to-cleanup-start bound.
Camera up is limited to the directly
observed `2500 -> 2000` profile (approximately five degrees) and owns its
`[2500,2730]` restore. Cleanup and restore run when an operation succeeds,
fails, or receives Ctrl-C; write acceptance and protocol correlation still do
not prove physical stop or restoration.

There is deliberately no arbitrary command, PWM, duration or servo target.
`stop` sends the fixed all-zero raw-PWM request and requires its correlated
empty raw-`80` response; it is a stop request with previously observed stopped
wheels, not proof of braking, de-energization or causation. Camera down 5 provides the fixed
offline `2500 -> 3000` inverse-hypothesis plan, but live camera-down remains
blocked until the installed linkage is observed. Use the external cutoff
whenever cleanup or restoration is reported uncertain.

`drive` and `teleop` use an initial all-zero `0B` transaction, one bounded
nonzero `0B` setter, and a mandatory all-zero `0B` cleanup in `finally`.
Success means those three setter transactions completed under the identity,
write, sequence, command, CRC, and usbmon gates. It does not mean application
acknowledgment or physical stop. Command-`0A` values proved variable after
motion, including before a successive teleop action, so public drive plans
never request or interpret that unsupported telemetry. Operator observation
and the external cutoff remain required.

### Operator acceptance boundary

Supervised runs `teleop-006`, `teleop-008`, and `teleop-009` accepted bounded
forward, reverse, rotate-left, rotate-right, explicit stop, and exit cleanup.
The camera servo's center and fixed up profile, the fixed wheel-blink pilot,
and a direct-host microphone capture also passed their supervised checks.
These results establish completed gated transactions plus the recorded
operator observations; they do not establish application acknowledgments,
braking, de-energization, or protocol-proved physical state.

The selected product architecture connects the microphone array and LifeCam
directly to the Jetson, not through Marvin's old internal USB hub. LifeCam
through Marvin produced corrupted/truncated V4L2 buffers and is deferred;
media-through-Marvin is not required for this operator acceptance.
Direct-to-Jetson LifeCam capture has not yet been accepted. Projector movement
and power remain unsupported, and camera-down remains an offline-only plan.

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
the packaged projector runner and coordinator mode permanently reject live
execution before preflight, device open, or write. Source-only shutter commands
`0x2B/0x2C` are likewise disabled.

LED controls preserve the completed mapping evidence without inventing a color
model. `leds plan NAME` prints an immutable offline plan for one mapped channel
at the only tested value, `255`; every other payload byte is zero, so this is an
exclusive full-intensity plan rather than a composable per-LED setter. Named
steady LED plans cannot run live because the mapping procedure restored in a
separate process and therefore does not own cleanup.

`leds attention-check` is one fixed live acceptance sweep, not a general setter.
It requires the exact captured 18-byte baseline, then shows each mapped
robot-left and robot-right red/blue position plus the mapped front-left blue and
front-right red channels at `255` for 0.25 seconds. Every state is exclusive.
The exact baseline is restored once between states and from `finally` after a
setter may apply; restoration is never retried. A final command-`0x17` getter
must exactly match the baseline. Raw `0x80` and `0x82` remain opaque and visible
effect must be recorded by the supervising operator.

`leds wheel-blink` reuses the exact completed
three-second wheel pilot, requires the fixed OFF-start state/blink baselines,
and attempts the exact blink baseline followed by the exact LED-state baseline
once after either setter may have applied. It does not expose payload, color,
intensity, timing, animation, or standalone-OFF controls. The restore is the
captured baseline, not all-off: no all-off cleanup has been live-proven.
Neither live action permits selecting a channel, intensity, color, or timing.

Supervised attention acceptance, only after reviewing the disconnected-load
setup and arranging an operator at the external cutoff:

```sh
python3 -m marvin leds attention-check \
  --run --expected-physical-port 1-3 \
  --output evidence/attention-check-001 --confirm-safe-setup
```

The supervisor records each visible named effect and cuts external power on any
mismatch, missing restore, or reported uncertainty. Do not retry. If the run
cannot getter-verify its final baseline, first confirm visible restoration and
power-cycle the controller, then clear only the evidence-bound lock offline:

```sh
python3 -m tools.marvin_legacy_attention_check \
  --acknowledge-restoration --evidence evidence/attention-check-001 \
  --physical-restoration-confirmed --power-cycle-confirmed
```

Python API:

```python
from marvin import Marvin

robot = Marvin()                 # offline plans; no hardware access
print(robot.status())
print(robot.drive("forward"))
print(robot.stop())
print(robot.camera_up(5))
print(robot.camera_down(5))      # offline fixed plan; live remains blocked
print(robot.leds.full_intensity_plan("left-position-0-red"))
print(robot.leds.attention_check()) # offline immutable plan by default
print(robot.leds.wheel_blink())   # offline immutable plan by default
```

`Marvin().sensors()` (or `python -m marvin sensors`) returns the exact four
request frames and response schema without opening hardware. Embedded live
applications may inject an already validated transport, or use the installed
live CLI:

```sh
python -m marvin sensors --run --expected-physical-port PORT \
  --output NEWDIR --actuators-isolated --unprivileged-usbmon
```

For a supervised run, first print and review the offline plan, confirm the
reviewed `045e:4444` physical topology and actuator power/signal isolation,
then run the command once as the ordinary user with a new output directory.
Do not retry or reconnect after any error; preserve the failed sealed directory.
After success, review the returned raw snapshot and `NEWDIR/SHA256SUMS`.

Live mode uses one bounded, identity-pinned `LegacyClient` session for reported
controller words, the raw power mask, raw telemetry, documented proximity
mappings, unresolved cliff/bump fields, motor/encoder values, and two raw
servo-position words. It pins the reviewed `045e:4444` topology, writes exactly
the four displayed getter requests at 57600 8N1, and seals serial/USB evidence
in a new directory. There is no arbitrary command, retry, reconnect, successor
fallback, or per-field subprocess. Dedicated `GetBatteryInfo` remains excluded
because its installed eight-byte response has no proven legacy decoder.

### Continuous local operator API

`marvin operator serve` runs a long-lived stdlib HTTP service bound only to
`127.0.0.1` (default port `8765`) and serves the dependency-free browser
dashboard at `http://127.0.0.1:8765/`. Without `--run` it is status-only, opens
no hardware. With `--run` and every reviewed declaration, the installed CLI
uses the existing identity/preflight, ordinary-user usbmon, and private evidence
coordinator to create one `ProductionControllerOwner`. That owner serializes the
exact four sensor getters, accepted bounded drive/stop profiles, and 18-byte LED
getter/setter transactions on one pinned transport. It never retries,
reconnects, wraps sequences, or exposes arbitrary opcodes. Tests and embedding
applications may still inject the same typed owner boundary without weakening
the installed path.

Each production live invocation has an eight-hour wall-clock ceiling with a
minimum two-second live polling interval. Eight hours is guaranteed only for
idle polling, not continuous driving: getters, three-transaction pulses, stops,
and LEDs share the non-wrapping request budget shown live in status and the
dashboard. Exhaustion fails closed while explicit transaction and
adapter-journal reserves remain for mandatory stop and LED restore. The exact
allowlist plus the non-wrapping transaction cap also bound application bytes.
Its serial evidence envelope reserves a further 30 seconds for those
cleanups, transport close, and evidence sealing, followed by the existing
coordinated usbmon tail/close allowance. A runtime or cleanup failure closes the
HTTP listener and cannot be reported as success. Any operator recorder,
session-guard, transport, or post-nonzero fault prints `CUT_POWER_REQUIRED`
directly on the parent terminal even though usbmon stderr is also retained
privately when nonzero motor output may have applied or its cleanup is
uncertain. Pre-motion startup and idle failures do not issue that warning.
Recorder/guard/clock failure and dirty serial/USB evidence are
recorded but cannot suppress the exact emergency zero attempt; changed pinned
USB identity or tty generation still blocks writing to the wrong device.
Production startup observes two quiet seconds after the exclusive tty open
before its single LED-baseline request, and binds the HTTP listener only after
the controller owner and managers are ready. A missing response still fails
closed without retry or reconnect.
`OperatorRuntime` also accepts typed named `RuntimeManager` injections and owns
their start/status/close lifecycle, so later drive, LED, and media layers can
reuse this server and shutdown path instead of creating parallel runtimes.

Layer 2 adds injected `drive`, `leds`, `microphone`, and `camera` managers plus
narrow JSON actions on that same server. Controller actions execute on the
polling owner thread, pause sensor polling only through each bounded action and
mandatory zero cleanup, reject movement backlog, and publish state/errors
through the existing SSE stream. Between held pulses the owner admits one
150 ms-bounded getter at a time and checks priority commands between getters.
Every new pulse discards an incomplete cycle; the browser leaves 650 ms between
pulses within the 0.75-second lease, and SSE publishes only complete
four-getter cycles with start/completion/duration metadata. Proximity and
unresolved raw cliff timestamps therefore advance without a read overlapping
nonzero PWM or a snapshot straddling a pulse.
Dead-man motion requires a fresh
0.75-second lease heartbeat for each proved 250 ms pulse; fixed-key motion is
four sequential proved pulses, not calibrated distance or uninterrupted exact
motion. LED control preserves one exact 18-byte baseline and permits only one
evidence-mapped binary channel at a time because simultaneous effects are not
proved. Direct-host WAV and MJPG/MKV recorders are private, exclusive, capped at
five minutes, and clean partial files. Camera recording remains planned and
unverified until Jetson acceptance. The complete route and integration contract
is in [docs/operator-api.md](docs/operator-api.md).

The browser uses the same HTTP/SSE listener and owner-thread command queue. It
shows raw sensor confidence/boundaries, JSONL recording state, dead-man and
fixed-pulse drive controls, evidence-mapped individual LEDs, and private media
controls. No device value is inserted as HTML, and no media is previewed,
played, uploaded, or transcribed. Live configuration pins the
controller port, private evidence/media roots, isolation declarations, and the
current exact direct-host microphone and LifeCam USB paths; missing
configuration leaves the corresponding controls disabled with a reason. See
the API document for the exact flags and supervised acceptance sequence.

Poll intervals are bounded to `0.5..60` seconds (default `2`). Recording chunks
are bounded to `10..3600` seconds (default `300`) and are new mode-`0600` JSONL
files in an operator-selected existing directory with no group/other
permissions. Recording errors stop recording and remain visible without
silently stopping a healthy display session.

| Route | Method | Purpose |
|---|---|---|
| `/api/status` | GET | Lifecycle, connection, freshness, errors, recording |
| `/api/sensors/latest` | GET | Latest complete timestamped exact snapshot |
| `/api/events` | GET | One-way Server-Sent Events status stream |
| `/api/recording` | GET | Recording status |
| `/api/recording/start` | POST | `{"directory":"EXISTING_PRIVATE_DIR"}` |
| `/api/recording/stop` | POST | Stop, fsync, and close the active chunk |

The service sends no permissive CORS header and has no remote-bind, command,
opcode, or arbitrary-file API. Snapshot labels retain raw source fields and
their unknown/unsupported confidence boundaries; they do not invent units.

`marvin microphone` delegates to the bounded microphone module without adding
another capture implementation. `status` is offline, `list` verifies only
`hw:CARD=Array,DEV=0`, and `capture` accepts only 1-5 seconds of native
8-channel `S16_LE` audio with exact byte bounds, explicit privacy authorization,
and a new mode-`0600` raw/WAV output. Before `arecord`, the selected ALSA card
and capture PCM node must both resolve through `/sys/class/sound` beneath the
exact operator-selected microphone node. The recommended `direct-host` route
requires `--usb-path`, requires that exact node to be `045e:fff0`, and forbids
`--hub-path`. Closed `marvin-internal` (`0451:2046`) and
`historical-external` (`2109:2817`) hub routes remain only for interpreting old
evidence and require exactly one microphone descendant. The discovery path
`1-1.1.2.4` is historical only. Missing, duplicate, or mismatched ancestry is
rejected.
The output is registered with cleanup while SIGINT is blocked, so creation or
write interruption removes the private reservation rather than leaving a
partial file.
The signed kernel override containing `d0199ae` and the exact-device `FILL_MAX`
quirk remains mandatory.

### LifeCam capture

`marvin_camera` and `python -m marvin camera` expose only the standard V4L2
host interface proven for the Microsoft LifeCam NX-3000 (`045e:0721`). Offline
status and capture calls return plans without reading sysfs, opening a device or
launching a process. Live status is read-only. Live capture is fixed to one
`MJPG` `352x288` frame written to a new `.jpg`/`.jpeg` path:

```sh
CAMERA_USB_PATH=1-2.3  # example only; replace with the currently observed path
python -m marvin camera status \
  --run --expected-camera-usb-path "$CAMERA_USB_PATH"

python -m marvin camera capture private/frame.jpg \
  --run --expected-camera-usb-path "$CAMERA_USB_PATH" \
  --confirm-privacy
```

The selected architecture connects the LifeCam directly to the Jetson rather
than through Marvin's old hub. Before live use, the operator must obtain the
current physical USB path and independently establish that direct connection.
The implementation then requires that exact sysfs device to be
`045e:0721`, correlates each candidate V4L2 node by sysfs ancestry and
`bus_info`, requires complete V4L2 driver/card/bus metadata and the fixed frame
format, and refuses ambiguity, Intel IPU3, REAR/DEPTH cameras, existing output,
timeout or subprocess failure. The final path is reserved at mode `0600`;
ffmpeg emits one size-capped frame to stdout, and a private sibling is atomically
installed only on success. Timeout, nonzero exit and interruption remove every
reservation or partial artifact. It does not hard-code unpreserved V4L2
metadata, accept arbitrary ffmpeg options, or send a Marvin controller,
camera-power or DepthCamPower command.

LifeCam capture through Marvin produced corrupted/truncated V4L2 buffers and
is intentionally deferred; media-through-Marvin is not required for operator
acceptance. Direct-to-Jetson LifeCam capture remains unproved until separately
accepted.

The host proof captured one valid frame, but did not preserve a stable sysfs
path, `/dev/videoN`, V4L2 driver/card string or `bus_info`; those are runtime
facts, not constants. Jetson enumeration and capture remain untested, so its
exact path and V4L2 metadata are the remaining live integration gap.

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
values are intentionally rejected. Live use sends an initial all-zero setter,
one bounded nonzero dwell, and one mandatory all-zero cleanup attempt under the
evidence lock and operator declarations.
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
`run_stop(...)`. A live run sends exactly one fixed all-zero `0B` request and
requires one correlated empty raw-`80` response. It sends no post-stop getter.
This is a zero-PWM request with protocol evidence, not proof of braking,
de-energization, cleanup causation, or physical stop.

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
