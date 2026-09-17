# Bounded LIVE legacy collection

The separately authorized [post-zero ReadRawData observation](legacy-zero.md#separately-released-post-zero-readrawdata-batch)
records sequences 1280-1284 and compares their raw motor fields with the earlier
baseline/reconnect batches. That comparison is not physical-stop or causality
evidence.

The later [operator power-topology clarification](legacy-motor-power-off-prep.md#operator-declared-shared-supply-topology)
states that the HY1803D powered controller electronics during the successful
isolated captures. Their actuator-load isolation did not mean a deenergized
controller board or USB-only operation; their recorded communication evidence
is unchanged.

`tools.marvin_legacy_live` connects the persistent client and collector to one
Linux tty owner for Epic #1. It is separate from the unchanged synthetic CLI
and unchanged four-getter one-shot probe. **Only empty ReadRawData `00` requests
are available here.** Import and default invocation are offline:

```sh
python3 -B -m tools.marvin_legacy_live
```

There is no startup hook, arbitrary command, GetUnitInfo, setup/heartbeat,
setter, flush, retry, automatic reconnect/resume, reset or privilege escalation.
The default live plan allows five requests, a minimum one-second idle interval,
0.5-second request timeout, 15-second collection deadline, and 5-second cleanup
budget. The CLI permits reducing request count, increasing interval, choosing
a fresh starting sequence and adjusting duration up to 30 seconds. Admission
requires that the complete nominal schedule fits. Input, records and output
remain bounded by the printed plan; unexpected bytes are not discarded to fit
the expected five-reply shape.

## Operator and ownership gates

The following gates are the unchanged fully isolated default. The separately
selected [left-motor-powered read-only scope](#left-motor-powered-read-only-observation)
and [encoder-feedback scope](#encoder-feedback-observation) below
are not full isolation and must never claim it.

Do not run hardware commands in CI or unattended development. A separately
authorized operator must confirm all four isolations (motor power, motor signals,
servo power, servo signals), the reviewed J10 connection and exclusive ownership.
Another session's hardware hold must be explicitly released. The operator's
statement is not an electrical measurement. Repair/readiness, physical stop and
commissioning gates (#9, #11, #13) are independent.

Only after reviewing the exact plan and receiving ownership release:

```sh
python3 -B -m tools.marvin_legacy_live \
  --output /trusted/private/existing-parent/NEW-CAPTURE \
  --expected-physical-port 1-1.1.3.3 \
  --actuators-isolated --unprivileged-usbmon \
  --max-requests 5 --interval 1 --duration 15 --first-sequence 512 --run
```

This is a plan example, not standing permission. Choose a new private output
directory and a separately reviewed fresh sequence range each time. Fresh
sysfs/udev/fuser preflight pins the tty, driver, USB path, bus/address, descriptor
hash and sysfs generation; the device number is never assumed from an old run.
No rule, permission, group, driver or host security setting is changed. Missing
access is an operator blocker, not a reason to invoke sudo or change modes.

The existing coordinator starts target-scoped binary USB recording first and
retains its ready handshake, identity guards, bounded tail and close grace.
An internal `capture_runner` injection supplies the persistent collector instead
of the original one-shot serial capture; existing callers retain the old path.
The USB recorder runs as the same ordinary user, not a privileged fallback.
At the default settings its nominal window is 20 seconds and hard window is
25 seconds from recorder readiness, including serial close; startup waits and
post-process finalization have their existing separate bounds.

The constructing process/thread owns the raw nonblocking tty descriptor.
Fresh preflight, `flock`, `TIOCEXCL`, a post-open visible-owner check and repeated
fd/path generation checks supplement the client's process-local ownership.
These are not protection against root, dishonest code or invisible pre-existing
other-user readers; exclusive operator coordination remains essential.
`57600/8N1`, no flow control, raw mode and low DTR/RTS are requested and read back.
Configuration uses `TCSANOW`, **not an input flush**. Exactly one `os.write`
syscall submits each ten-byte request; short/error/uncertain results stop.
The requested and returned termios arrays are recorded before validation.
Only Linux `CBAUD|CIBAUD` encoding bits are normalized for the initial cflag
comparison; both speed fields must independently equal `B57600`, and every
remaining flag and control character must match exactly. Missing platform
constants fail explicitly. Later identity checks require the exact accepted
readback, not a progressively relaxed comparison.

Kernel first-open echo and line transitions remain possible before configuration.
A one-second post-configuration observation suppresses the first request on any
input; unexpected application OUT, including possible echo, is rejected by USB
correlation. USB control-line requests are retained, not represented as physical
measurements. Low requested/read-back lines do not imply glitch-free open/close.
The adapter never reads/drains the tty during close. The same owner attempts
descriptor close once even if recording fails, and records errors explicitly.

## Encoder-feedback observation

The following scope was initially approved **software only**; the later,
separately authorized [first real result](#first-load-disconnected-encoder-result)
is recorded below. Neither grants another run. At software approval the
reported setup was HY OFF with MotorL attached; the following required setup
was **not done**. A future operator must separately confirm both motor **POWER**
plugs physically disconnected, servo **actuator power and control** isolated,
and **both original encoder harnesses fully connected, including logic power
and reference**, with controller/shared HY powered. This is the
**MOTORLOAD-DISCONNECTED** scope, not full signal isolation, supply OFF,
motor-powered observation, zero preparation, calibration or permission to power.

The opt-in reuses the same client, collector, native transport and USB evidence
path. Its separately constructed immutable `PollPlan` does not widen the old
five-request/30-second live bounds or the fixed sequence-2304 powered scope:

| Bound | Fixed encoder scope |
|---|---|
| Only application TX | 20 empty ReadRawData getters, sequences **2560–2579**, at most **200 bytes** |
| Idle / timeout | At least 1 second **after completion**, 0.5-second request timeout |
| Collector operations / separate cleanup | 35 / 5 seconds |
| USB nominal / hard window from recorder ready | 40 / 45 seconds (existing tail 5 + grace 5) |
| Full USB payload / serial RX | 4096 / 8192 bytes |
| USB evidence | Existing combined 1 MiB / 10000 records |

There are no CLI plan overrides, alternative frames, init/zero/setters,
retry, catch-up, extension or reconnect. Deadlines, quiet checks and identity
guards can refuse **before 20 samples**. These are not end-to-end wall-time or
physical power-dwell guarantees; preflight, startup and finalization are separate.

Future invocation **only after separate physical confirmation**, run directly
in the parent's visible terminal from the committed CLI, not via a cross-session
relay or watcher:

```sh
python3 -B -m tools.marvin_legacy_live \
  --encoder-feedback-observation --motor-power-plugs-disconnected \
  --servos-isolated --both-encoder-feedback-connected --unprivileged-usbmon \
  --expected-physical-port 1-1.1.3.3 --output NEW-PRIVATE-ENCODER-CAPTURE --run
```

For offline plan inspection, omit `--run` and `--output`. Do not add
`--actuators-isolated`, `--motor-supply-off`, left-motor-powered, preparation or
zero declarations. All boundaries preserve the truthful declarations, including
the USB subprocess; metadata is historical, never standing permission.

Wait for the flushed stderr **`BASELINE_READY`**, emitted only after the first
valid sample's raw journal rows have been successfully written through the
existing **unbuffered** recorder. No per-sample fsync is claimed; terminal sealing
retains its existing fsync contract. Then, only in the separately confirmed
physical setup, gently turn the left wheel forward/back without forcing it.
Stop manual movement at **`COLLECTION_ENDED`**, on success or any failure, before
the lengthy USB tail. No human wait extends the fixed collection. Startup or
constructor failure emits an ended/no-movement-window message and no ready.
At most 20 sample markers report sequence, raw/uint32/int32 positions,
raw/uint16/int16 velocities and remaining operation time. Stdout remains the
original final JSON. These markers are preliminary, not an interlock, power cut
or permission; later tail/drop/cleanup/sealing failure invalidates the result.

Every matching CRC-valid status-`80`, exact 134-byte/82-field observation is
accepted regardless of position, velocity or PWM values in this load-disconnected
scope. The actual decoder reads retained bytes: `motorPositionL/R` at payload
64/68 are little-endian 32-bit words; velocities at 72/74 are little-endian
16-bit words. Raw bytes, all fields, both signed/unsigned views, identity and
full USB timing/evidence remain retained. Unknown shape, late/unsolicited,
corrupt/partial input, drops or uncertain writes fail closed and suppress the
suffix. No units, counts/revolution, wheel mapping or calibration are inferred.

Provenance: the parent supplied the **both encoders fully connected** declaration.
Earlier raw position reports were L=72/R=-40 at 512–516, L=72/R=-55 at 768–772,
and both 0 at 1280–1284, 2048 and 2304; reported raw velocities were all 0.
Those facts do not identify which encoder changed or establish units/causality.
No private captures/photos are copied here. Software tests exercise changing
actual payloads and one native **host-created PTY**, with explicitly synthetic
USB, identity and unsupported modem readbacks—not robot access or a live result.

### First load-disconnected encoder result

On **September 17, 2026 at 00:53:29-00:54:09 UTC** (September 16 operator-local
evening), the parent executed the committed CLI **once**, at
`4c0e25ae47798074e28e8eb1b751e9ce3b2cb63a`, in a visible terminal.
Fresh operator preparation was:

> Ready - both motor-power plugs disconnected, encoders connected, servos
> isolated, controller powered.

Both original encoder harnesses included their normal logic supply/reference.
Only motor **POWER** plugs were disconnected; this was not all-signal isolation
or a deenergized board. The shared HY controller supply was powered. The parent
instructed waiting for `BASELINE_READY`, gently turning the left wheel forward
and back without forcing, and stopping at `COLLECTION_ENDED`. After the window,
the operator reported:

> Forward 360 degree rotation, reverse 360 degree rotation. Right wheel untouched.

These are operator observations, not independently measured or time-synchronized
turn angles. The parent separately reported an immediate shell
`CAPTURE_EXIT_STATUS=0` and return to the idle shell.
The operator subsequently clarified that the left wheel was **held away from
its starting angle for several seconds** during the capture. This strengthens
the reported opportunity to observe a displacement, but angle, duration and
overlap with actual sample/marker times were not independently verified.

**Communication completed, but encoder feedback was not demonstrated.** Every
one of the 20 samples, sequences **2560-2579**, had these actual retained words,
independently re-decoded and checked directly at the authored payload offsets:

| Authored field | Payload offset / width | Raw hex in every sample | Unsigned / signed |
|---|---|---|---|
| `motorPositionL` | 64 / 4 | `00000000` | uint32 0 / int32 0 |
| `motorPositionR` | 68 / 4 | `00000000` | uint32 0 / int32 0 |
| `motorVelocityL/R` | 72, 74 / 2 | `0000` | uint16 0 / int16 0 |
| Four `motorPwm*` fields | 90, 92, 94, 96 / 2 | `0000` | uint16 0 / int16 0 |

For **both** position channels, the complete sampled trajectory is 20 zeros;
all 19 adjacent shortest signed modulo-32 differences are zero. Sampled minimum,
maximum, range and net change are zero. This is not proof of no physical
rotation, no unseen wrap, a broken encoder, or a particular wiring/firmware
cause. Neither channel shows a sampled response corresponding to the reported
left-wheel movement and hold. Do not assign wheel mapping from the field names or infer
functioning feedback from successful communication. Approximately 1 Hz sampling
can miss turning points or intervening excursions; no counts/revolution,
direction calibration or closed-loop safety is established.

The raw `tick` field increased from **4056 to 5948**, with all 20 values distinct;
other sensor fields also changed. The replies were not identical payloads.
This does not establish tick units, freshness of each individual field, or
encoder update behavior.

Independent offline verification established:

- Exactly 20 empty ReadRawData OUT frames, sequences 2560-2579, **200 bytes**,
  and 20 matching CRC-valid command-00/status-80 replies, each 144 bytes with
  134-byte payload and 82 fields. All **2880 RX bytes** match full binary USB-IN,
  serial journal, poll evidence and decoded metadata; no other application TX.
- One recorded raw-tty open and close, no input flush, no uncertain TX, no
  rejected input, no collection/cleanup errors; a complete collector seal and
  **26 manifest entries** verified across the two manifests. The manifests
  establish stored consistency, not authenticity or physical application ACK.
- Same approved port `1-1.1.3.3`, `045e:4444`, 71-byte descriptor hash
  `7c0df726b51216f29f11f0d078f4673596f3c50c675c9a0419c1316d5446419b`;
  capture generation **bus 1/device 24, sysfs device 25/inode 101027**,
  `ttyACM0` rdev 42496. Saved identity and truthful consent agree at every layer.
  Successful committed ownership guards and normal close are recorded; no
  post-run owner/device scan was authorized or performed.
- **160 target USB records / 80 paired transfers**, 40 full nonempty bulk-IN
  completions, 20 completed bulk OUTs and 17 zero-byte cancellation completions.
  No unmatched/pending/evicted transfers, other completion errors, incomplete
  records or recorded loss; initial/final queued and dropped counters were zero.
  Normalized text omits 1920 IN bytes, but full binary evidence retains them.
- CDC line requests were again **3 then 0**, with 57600/8N1 line coding.
  Requested low DTR/RTS does not mean glitch-free opening. Prewrite quiet was
  **1.004456 s**, write spacing **1.011974-1.017810 s**, and post-correlation idle
  at least **1.005137 s**. Independently reconstructed conservative USB ingress
  bounds were after submission and before every 0.5 s reply deadline.
- First-to-last reply spanned **19.269451 s**; approximate host UTC reply bounds
  were 00:53:31.730569 through 00:53:51.000020. Close took **0.035305 s**.
  Complete post-close USB tail was **18.492095 s**; normal coordinator stop
  occurred **40.131911 s** after recorder ready, before its 45 s hard deadline.
  The reserved nominal tail includes unused collection time; this was not a
  40-second manual movement window or a measured supply-on duration.

Terminal progress uses inherited stderr and is **not in the sealed capture**.
The journal verifies sample persistence and its monotonic/UTC anchors, not
independent `BASELINE_READY`/`COLLECTION_ENDED` emission timestamps or alignment
of the operator's movements. The committed callback order and software tests
must not be relabeled as captured terminal evidence. The parent's subsequently
preserved terminal history starts mid-final-JSON and retains exit status 0 but
none of the three marker types. Independent offline inspection confirmed that
absence; it does **not** establish that the markers were never emitted.

Private evidence ID: `encoder-feedback-2560-first`; offline audit:
`encoder-feedback-2560-first-verification.json`, with a separate immutable
`encoder-feedback-2560-terminal-addendum.json` for the later terminal evidence.
The later operator hold clarification is preserved separately as
`encoder-feedback-2560-operator-hold-addendum.json`.
Only derived facts are published, not private raw captures.

| Artifact | SHA256 |
|---|---|
| Capture root manifest | `f2bbdb16d5cdbd1a60f4e4ada1f2a2f476959ce62ba59c409c04e532414c9b62` |
| Full 2880-byte RX stream | `799438a45814e24afec513bf6664cf6811a1365abcca6f1ab1b36d47eef38f63` |
| Binary USB | `1666eadecf9c2ca4a0f85955eab6c118566341d5cc5b51d5f809b20fcbcd66d2` |
| Poll journal | `9e858eee6a1e9812fb97a0db8ed8c3ae56331b0299b37d7b818a4e9f177a3117` |
| Offline audit | `598201e175eea26765b7ab99e39c80e0e6f6cfda5245048f0accb660c028cfe7` |
| Parent retained terminal history | `863c8966fa6c7465842b7c46d31aaea206f32c3eeb761038249686173d70deea` |

**Hold:** no automatic retry, additional query, zero, nonzero drive or powered
motion trial follows this result. Parent retains hardware ownership and the next
physical decision. Left-feedback function/mapping remains unresolved; no stop,
calibration or commissioning gate is closed.

## Left-motor-powered read-only observation

**HARDWARE HOLD; one separately authorized observation is recorded below.**
The operator reports HY1803D OFF after that observation. No new device access,
preflight, power action or repeat trial is authorized by this implementation,
its tests or the past observation. Shared HY1803D power supplies
both controller and motors, so the unchanged motor-power-OFF preparation
profile is physically inapplicable to the current wiring. This scope neither
weakens that profile nor sends its zero command.

This opt-in uses the existing `LiveTransport`, `LegacyClient`, `PollPlan`,
collector, coordinator and USB recorder. The immutable plan is:

| Bound | Fixed value |
|---|---|
| Application TX | **One** empty ReadRawData, sequence **2304** |
| Entire allowed frame | `53000900000000bf0445` (10 bytes maximum) |
| Request count / interval / timeout | 1 / 1 second / 0.5 second |
| Collector operational deadline | 3 seconds |
| Separate existing cleanup allowance | 5 seconds |
| USB nominal / hard recording window | 8 / 13 seconds (tail 5 + grace 5) |
| Serial | 57600/8N1, low requested/read-back DTR/RTS, raw/unflushed |
| Raw pre-request quiet | 1 second; any input suppresses the getter |
| Full USB payload / serial RX budget | 4096 / 8192 bytes |
| USB evidence limits | 1 MiB combined evidence, 10000 records |

**Three seconds is not an end-to-end wall-time or power-dwell limit.** It does
not include initial preflight, recorder startup/coordinator pre-open checks,
separate cleanup or evidence finalization. Existing identity/ownership work
inside the collector consumes its operational budget. If guards and quiet
leave insufficient time, the attempt fails closed; no limit is relaxed.
The host cannot remove energy, detect physical movement or guarantee a power
cutoff. Closing the tty is not a motor stop. The operator must continuously
watch, including boot and serial open, with an independent external cutoff.

For **offline review only**, omit `--run` and `--output` from this exact future
invocation. Every positive declaration requires **new, current operator
confirmation and a separately approved bounded physical power plan** before
any future `--run`. This example is not that permission:

```sh
python3 -B -m tools.marvin_legacy_live \
  --left-motor-powered-observation \
  --motor-left-only-connected --motor-right-and-servos-isolated \
  --operator-at-external-cutoff --unprivileged-usbmon \
  --expected-physical-port 1-1.1.3.3 --output NEW-PRIVATE-CAPTURE --run
```

Full actuator isolation and motor-supply-OFF must both be **false**. Do not add
`--actuators-isolated` or `--motor-supply-off`; neither is forwarded as a false
claim to the USB subprocess. Partial, mixed, truthy/non-boolean API declarations,
preparation/zero flags and modified plans are rejected before hardware access.
Explicit conflicting CLI count, sequence, duration or interval flags are errors,
not silently overridden defaults. `run_live` requires literal `run=True` for
this scope. The ordinary isolated CLI/API retains its original defaults.
Pinned reviewed physical port/known descriptor hash, fresh identity, exclusive
ownership and no-sudo recorder gates remain in effect.

Exactly one matching CRC-valid status-`80` 134-byte/82-field reply is required.
The existing decoder interprets **actual retained bytes**, not an injected zero
template. Both motor velocities (payload offsets 72/74) and all four PWM words
(90/92/94/96) must each be raw `0000`, unsigned 0 and signed 0. Decoded values,
raw bytes and confidence labels are retained even when nonzero or unknown.
Nonzero/unknown, malformed/partial/unsolicited/extra input, uncertain/short
write, deadline, constructor/preflight, USB tail/drop, cleanup or sealing
faults fail the observation; there is no retry, reopen, init, zero, setter or
automatic power action.

Detected failures issue a flushed **“OPERATOR: CUT EXTERNAL POWER NOW”**
diagnostic before subsequent slow cleanup/drain work. Client evidence/failure
callbacks expose faults before the client's own close; coordinator faults are
reported before recorder drain. In this scope and the encoder scope the USB subprocess inherits
stderr to emit its urgent diagnostics without waiting for log/tail completion.
Delivery or immediate operator visibility is not guaranteed by a flushed stream.
The scoped `usbmon-stderr.log` is not used for those diagnostics, as recorded in
`usb_diagnostic_delivery`; failure metadata and raw evidence remain independently retained. A fault
only discovered during late validation is reported then, and cannot return a
successful result. This is not a real-time physical interlock.

Even a clean result means **`observation_only_not_stop_or_commissioning`**.
Historical declarations are not standing permission; metadata explicitly keeps
`motor_supply_on_permission=not_granted`. No observed zero word proves absence
of boot/PWM transients, physical stillness, successful stopping or safe future
energization. See the [operator's physical-only stationary trial finding](lab-findings.md#operator-reported-physical-only-left-motor-startup).

Software verification uses synthetic actual-payload variations for each motor
word, truthfulness checks at API/CLI/capture/coordinator/USB boundaries, exact TX
and fault-prefix assertions, notification ordering and late-failure tests.
A **host-created PTY** exercises native one-open/one-write/read/close; its USB
events, identity and unsupported modem-line readbacks are explicitly synthetic.
It is not a robot, USB capture or powered-motor test.

### First authorized powered read-only result

On September 16, 2026, the parent separately authorized one conditional
first-appearance observation at `470d02508cbf9fbb97150f1757b093bd613c6101`.
The operator freshly accepted MotorL only connected, MotorR/servos isolated,
secured supports and clearance, accessible external cutoff, and an independent
**30-second maximum from supply ON**, with earlier OFF for any twitch, motion
or unexpected behavior. HY1803D 12 V / 1 A were operator-reported settings,
not a verified transient-energy limit.

A private cached-only watcher recorded the pinned target and approved by-id
selector absent at **23:56:47.651000 UTC**. It sampled at 0.25-second intervals
with a 90-second arming deadline of **23:58:17.650964 UTC**. That was a wait
limit, not a power-duration allowance. After the parent delivered the power
prompt, first pinned-path presence was recorded at **23:57:49.217488 UTC**.
The same physical port `1-1.1.3.3`, VID/PID `045e:4444` and known 71-byte
descriptor hash matched. The new cached generation was bus 1/device **22**,
sysfs device 25/inode **98548**; the run used `/dev/ttyACM0`, rdev 42496.
Descriptor SHA256:
`7c0df726b51216f29f11f0d078f4673596f3c50c675c9a0419c1316d5446419b`.

The watcher invoked the committed CLI exactly once. Fresh identity/ownership
guards admitted the run; the watcher identity, run/coordinator baselines and
recorder identity agreed. The session ran **23:57:49.517610-23:57:57.781984
UTC**, and the command exited 0 at **23:57:57.795480 UTC**. This was an
explicitly armed first connection, not reconnect/resume or an automatic retry.
The watcher then terminated. No later device check or capture was performed
to prolong or verify operator power.

Exactly **one ten-byte ReadRawData request**, sequence **2304**,
`53000900000000bf0445`, had a successful matching USB OUT completion.
There was no zero, initialization, setter, second getter or nonzero command.
The single CRC-valid status-80 reply contained 144 frame bytes / 134 payload
bytes / 82 decoded fields. All 144 bytes agreed across complete binary USB
payloads, the raw adapter journal, poll evidence and stored decoded telemetry.
The two raw velocity words at offsets 72/74 and four raw PWM words at
90/92/94/96 were each **`00 00`**, signed/unsigned zero.

All 46 target USB records paired into 23 transfers; two nonempty bulk-IN
transfers carried the reply. Seventeen zero-byte cancellation completions
were retained as cancellation evidence, not response semantics. Initial/final
queued and dropped counts were zero, with no incomplete or unmatched retained
transfers. The prewrite quiet interval was 1.004515563 seconds. Conservative
USB-completion ingress bounds were 485.825-608.614 microseconds after host
submission, not controller execution time. Tty close took 0.036853844 seconds;
the recorder retained 5.830690019 seconds after close and stopped normally by
coordinator request at 8.119733968 seconds after readiness, within its
13-second hard limit. CDC line requests were again **`3 -> 0`**.

Full-isolation and supply-OFF acknowledgments were **false** at root,
coordinator and recorder levels; left-only powered observation/cutoff
declarations were retained truthfully. All 26 nested manifest entries were
independently rehashed. Private audit ID: `left-powered-readonly-2304-first`.

| Artifact | SHA256 |
|---|---|
| Root manifest | `2de2468e95e392edf302eb2556b00d9b8741d8f6c29441b6e1e7bc6e502ee703` |
| Root metadata | `170103ec99b84cd28d917933307eabb900403ac4a57a1efe6894a506bed0603f` |
| Adapter journal | `e1f755ad7f80d523f39408db7801aedec2431b4b8a9ce2ce48bcdda44c66a1ed` |
| Poll journal | `8910a30222b0e3cc2cd8a8684dd30d814b6a45a4bf52b18b058c32e2cb20a8de` |
| Full binary USB | `3251aac3009c842248827d75311dfce9b21998bd05c6cac36e59adc088e996c0` |
| Concatenated RX | `eb85bdd181337d6ad6bc3e11cb5d42ac8c5c50287c2f4173167c170a1f94f8f7` |
| Cached appearance/command audit | `f71c0ea57efcf205626c557fe9c67c6a013363cf7d7d5366c16add64b483d3d5` |

Afterward the parent relayed the operator's actual result:
**"Completed--no twitch or motion; supply is off."** This is operator-observed
absence of movement and OFF state, not software measurement. The
8.577990098 seconds from sampled appearance to CLI exit is **not** the supply
ON duration; actual power dwell was not independently measured. The host
neither switched power nor detected physical motion.

This completes only this bounded read-only observation. It does not test
stopping from motion, validate a motor-stop command, prove absence of torque/
PWM/boot transients, establish causal control, or complete readiness/
commissioning gates. Software handles and watcher are closed, the operator
reports supply OFF, and sequence 2304 is consumed. **Hardware hold:** no
further device check, command, capture, retry, reconnect or powered experiment
follows from this result.

## Conservative host-ingress timestamps

A tty dequeue timestamp cannot bound input that was already in USB/kernel/tty
queues. Even an empty tty queue does not establish an empty upstream pipeline.
This adapter instead defines its observed ingress boundary at **Linux USB bulk-IN
completion observation**, before the CDC driver's tty delivery. It does not
measure wire arrival, controller generation, UART timing or physical origin.
Linux usbmon records its timestamp before invoking the driver's completion
callback (`drivers/usb/mon/mon_bin.c` and `drivers/usb/core/hcd.c`); all received
tty bytes must match the full, ordered USB-IN completion payloads exactly.

Binary usbmon uses realtime microseconds, not monotonic timestamps. The adapter
samples monotonic nanoseconds before and after a realtime nanosecond sample,
retaining the resulting whole offset interval. Both Linux clocks share normal
timekeeping slews; their offset is constant absent discontinuities/suspend.
An absolute realtime `timerfd` with `CANCEL_ON_SET` is armed before sampling and
checked throughout collection and after the USB tail. It latches discontinuous
clock changes, including a change followed by a reversal. Subsequent sampling
intervals must overlap the original interval; otherwise collection fails
(including detectable suspend-induced offset changes). Unsupported ABI/timerfd
and cancellation/read errors are failures, not approximate-timestamp fallbacks.

Conversion subtracts the offset interval and includes the full extra microsecond
discarded by usbmon timestamp truncation, rounding floating endpoints outward.
The original interval is not tightened retrospectively. Tty reads split or
coalesce USB payloads: their lower/upper bounds enclose every contributing
completion timestamp. The client still requires the first lower bound to be
**strictly later** than its completed request submission timestamp. Equal,
overlapping or fast/ambiguous replies therefore fail even if otherwise valid.
No timestamp is moved forward to manufacture a match.

This is a host observation boundary, not proof that all physical bytes arrived
after the write. Authentication, firmware identity, application acknowledgment,
calibration, units, physical safety and control-loop timing remain unestablished.
Python/syscall/filesystem checks are cooperative and post-call bounds, not a
hard realtime watchdog capable of preempting blocked kernel close or storage.

## Evidence and failures

The existing recorder's opt-in `binary_payload_limit=4096` preserves full bounded
USB payloads in `binary-events.bin`; its default remains 32. The normalized text
and historical text analyzer intentionally remain 32-byte-prefix representations.
Their truncation counters do not describe the larger binary artifact. Transfers
larger than the binary limit or kernel-captured length, missing/unpaired events,
IN mismatches, unexpected OUT or uncertain completions fail the LIVE result.

`capture/serial/poll.jsonl` is the existing sealed collector format with
`evidence_kind="recorded"`. `capture/serial/adapter.jsonl` additionally preserves
every dequeued serial chunk **before** USB timing/correlation, exact attempted
TX, accepted counts, open/close events and computed ingress bounds. Thus a
failure before the client accepts bytes still has a separate raw evidence path.
Neither recorder failure nor incomplete evidence produces an API success.
USB process failure, final queued/dropped events, byte/record/deadline limits,
extra input during idle/tail, corruption or identity loss stops without retry.
Output remains private and never overwrites/resumes an existing capture.

The outer metadata and `SHA256SUMS` cover the final collection and USB outcome.
A sealed inner collection is only an inner claim: the overall LIVE result can
still fail on later USB tail, close, clock, pairing or sealing checks. Preserve
the CLI/API result alongside the artifacts. On failure, review the primary and
secondary diagnostics and retained raw bytes before authorizing any new command.

Expected default acceptance is five correlated 144-byte replies (134-byte
payload/status `80`/matching command and sequence/CRC), 50 exact completed USB
OUT bytes, complete serial and binary USB-IN preservation, no extra input/output,
zero final monitor queued/dropped events, and one recorded open/close.
This is not a motion-readiness or physical-stop result.

## Explicit disconnect/reconnect

EOF, device disappearance, re-enumeration, tty-generation/settings changes and
USB recorder identity loss invalidate the session and close its descriptor.
There is no resume or automatic reopen. A subsequent operator-approved session
must repeat isolation/ownership/port checks, use a new directory and fresh
sequence range, and pin the current identity rather than an old device number.

Do not unplug/replug, switch power, inject a failure or change branches to exercise
this behavior without a separately reviewed operator procedure. Software
boundary tests are not real disconnect/reconnect acceptance. Epic #1 remains
open until both repeatable real snapshots and the separately authorized
disconnect/reconnect observations are reviewed.

## First authorized adapter attempt

On September 15, 2026 at 23:38:10-12 UTC, the operator released exclusive
ownership with all four isolations and the same J10 connection confirmed.
The single bounded attempt at `a82b604265c25f47ef5715ee7825e2dafedffecd`
stopped at initial termios readback validation, **before any application request**.
It recorded one open attempt and a same-owner close attempt/completion, without
retry. The 38-record USB trace contains no bulk OUT, no nonzero IN payload,
19 paired transfers, no unmatched/pending transfers and final queued/dropped
counts of zero. The trace records CDC control-line requests `3` then `0`;
low requested lines did not prevent the transient assertion.

This is a **failed attempt with retained partial-session evidence**, not a
successful collection. The recorder was interrupted with SIGINT after tty
close and approximately 0.4 seconds of tail, rather than completing its normal
window. All sealed artifact hashes matched. There are no new telemetry,
PWM or velocity observations and no physical disconnect/reconnect acceptance.
Private local evidence ID: `live-readraw-512-first`; no raw captures are published.

That version failed to retain the requested/returned termios arrays, so the
exact controller readback cannot be recovered from this attempt. A separate
host-only PTY reproduced the whole-list comparison failure: requested cflag
`0x8b0`, returned `0x100118b1`, identical remaining fields and exact 57600 speeds.
Linux adds input/output baud encoding to cflag. The surgical normalization
above fixes this reproduced host-side issue without relaxing other settings.
Host PTY verification does not establish successful controller operation; any
subsequent robot attempt needs fresh explicit authorization.

## Reviewed persistent snapshot result

The operator separately authorized one corrected attempt at
`1e8378a5c287b023c7d22c38a7d8b661c388db2f`, September 15, 2026,
23:41:12-32 UTC, without changing the four isolations or J10 connection.
It completed five ReadRawData exchanges, sequences **512 through 516**, on
one persistent open, followed by one recorded same-owner close.

Fresh preflight, capture guards and a post-capture cached-only preflight agreed:
USB `045e:4444`, physical port `1-1.1.3.3`, bus 1/device 7, tty `ttyACM0`
(rdev 42496), sysfs device/inode 25/44386, 71 descriptor bytes, SHA256
`7c0df726b51216f29f11f0d078f4673596f3c50c675c9a0419c1316d5446419b`.
These are that connection's host identifiers, not future device-number pins or
authenticated firmware identity. No tty owner remained after close.

All five replies matched command `00`, status `80`, sequence, CRC and the
134-byte/82-field profile. Independent offline comparison found identical
720-byte streams in the collector events, raw adapter journal and ten complete
USB-IN payloads. Five complete USB-OUT payloads exactly matched the five ten-byte
requests, with successful completions: **50 bytes accepted/completed, zero
uncertain bytes**, and no additional application OUT. All artifact hashes and
the collector seal verified. The 68-record target USB capture ended normally
by coordinator stop at approximately 20.23 seconds, before its 25-second bound,
with queued/dropped counts both zero and no unmatched application transfers.

Host write-attempt spacings were 1.022794, 1.039129, 1.033159 and 1.041388 seconds;
each measured post-correlation idle interval exceeded 1.010441 seconds.
Conservative RX lower bounds were 303-437 microseconds after the client
submission timestamps; no timing, identity, correlation or cleanup error was
reported. Close took approximately 35.554 milliseconds, with another 13.737
seconds of USB tail. Requested/returned termios arrays were retained; the
controller readback exhibited the same baud encoding normalization observed
on the host PTY. CDC control-line requests again included **`3` then `0`**.

Raw tick values were 1071212, 1071314, 1071415, 1071517 and 1071618 (deltas
102/101/102/101). Both source-labeled reverse PWM fields were raw **100** in
every snapshot; both forward PWM and reported velocity fields were zero.
Other raw fields changed between samples. These are not physical units,
controller timing calibration, actuator-stop evidence or commissioning approval.

Private local evidence ID: `live-readraw-512-readback-fix`.
The complete concatenated 720-byte reply stream has SHA256
`45872f5ba302360d333528ca69c4d0277e931eb57d9fba84e92aefc43dd8165f`.
The private `SHA256SUMS` manifest has SHA256
`25db28df45a3a3aedf718c944b341d69387ba93130454c45f43c3036e883515e`.
Only reviewed derived findings are published here, not raw captures.
The historical metadata limitation strings about 32-byte payloads and pySerial
flushing do not describe this adapter: its binary budget was 4096, its complete
IN bytes were verified, and it used raw termios without a flush.

**Hold at the end of this baseline:** no physical disconnect/reconnect had yet
been performed. The proposed next step was a separately authorized, cleanly closed USB reconnect
on only the same J10 host cable, preserving all four isolations and the reviewed
port. Re-pin the returned connection and authorize a new bounded five-snapshot
session with unused sequences (for example 768-772). An operator unplug/replug
may remove controller USB power or restart firmware; no software reset/power
command is authorized. This would test explicit fresh-session reconnect after
clean close, **not in-flight USB loss or a failure-injection experiment**.
Do not close Epic #1 or the independent physical gates from this result alone.

## Reviewed operator reconnect and fresh-session result

After the baseline had closed, the operator was separately instructed to unplug
only the same reviewed J10 host USB cable, leave it unplugged at least three
seconds, and reconnect to the same port without changing any other connection
or power switch. The operator confirmed reconnection, all four isolations still
in place, and nothing unexpected.

A private cached-only observer sampled at 2 Hz with a 120-second maximum,
without opening any USB/tty device node or automatically reopening a session.
It captured 14 missing samples and stopped after 72 samples, approximately
36.17 seconds. First missing observation was **23:44:43.091825 UTC**, and the
first complete returned identity was observed at **23:44:50.099639 UTC** on
September 15, 2026. Monotonic sampling brackets were
2766.751276590-2767.252129014 for disappearance and
2773.758663916-2774.259926451 for return. These bracket sampled host presence,
not exact cable, power or wire events.

The same USB path/physical port, `045e:4444`, 71 descriptor bytes and approved
descriptor hash returned. Bus 1/device **7 -> 10** and sysfs inode
**44386 -> 65032** (sysfs device 25 unchanged) establish a changed host
connection generation. The returned tty was `ttyACM0`, rdev 42496, inode 1353.
Fresh cached udev/fuser preflight passed before any new application open.
Private observer ID: `operator-reconnect-cached`; JSONL SHA256:
`cdd44d6e07b0d373f4f7e94be45973092c80719752eba3e547b42572a9e3eeee`.

Only after reviewing that evidence and the operator confirmation was **one new
session explicitly authorized**, at `8a6de50a92d92f3972261262e3f07afd473aaa5b`
(same implementation as `1e8378a5c287b023c7d22c38a7d8b661c388db2f`).
The 23:45:23-43 UTC capture used previously unused sequences **768-772** with
the same five-request, minimum one-second interval and bounded deadlines.
It opened once, delivered five matching CRC-valid status-80/command-00 replies
of 144 bytes each, and closed once. All 720 serial bytes exactly matched the
adapter journal and ten complete binary USB-IN payloads; all five ten-byte
OUT payloads matched the recorded requests and completed successfully.
Accepted/completed OUT was 50 bytes, uncertain OUT zero. No setters or
additional application commands were sent.

Write spacings were 1.029992, 1.042893, 1.027977 and 1.036449 seconds; every
measured post-correlation idle exceeded 1.013545 seconds. RX lower bounds were
279-456 microseconds after submitted-at timestamps, with no clock/correlation
faults. Close took approximately 36.254 milliseconds; USB recording retained
another 13.782 seconds of tail and stopped normally after about 20.27 seconds.
All 70 target USB records were retained with final queued/dropped counts zero.
Capture hashes and collector seal verified. Post-capture cached preflight
matched the new pre-capture identity and found no tty owner.

CDC line requests were again **`3` then `0`**. Every snapshot again reported
reverse PWM raw **100** on both sides, forward PWM zero and velocities zero.
Ticks were 1095792, 1095894, 1095995, 1096097 and 1096198, with deltas
102/101/102/101; these values do not establish physical stop, calibrated units,
device timing, reset behavior or electrical readiness.

Private evidence ID: `live-readraw-768-after-operator-reconnect`.
Concatenated 720-byte RX SHA256:
`68e4e56f6a835d16d26c18f72a1bd8e4599cafbb0f6986e55c9af03374514969`.
Private manifest SHA256:
`7c39c8ba300639ee38f33f895b37445925030843ed0c0acb6ba1bdac3179fed8`.
No private raw files are published.

**Scope and final hold:** the two successful batches establish ten repeatable
low-rate read-only snapshots, explicit clean close, observed operator
disconnect/return, fresh identity pinning and a separately initiated new session.
They do **not** establish behavior under an in-flight cable pull, partial-write
disconnect, controller power fault, physical emergency stop or motion.
There is no automatic reconnect/resume and no failure-injection claim.
All device handles are closed and further hardware work is stopped.

Epic #1 still needs owner/maintainer review of this evidence and publication/
review of the new adapter through an issue-linked PR; no issue is closed by
this document. In-flight loss remains covered by software boundary tests, not
this live experiment. The independent readiness, physical-stop and commissioning
gates (#9, #11, #13) remain unresolved by successful communication. Do not
reconnect actuator power/signals or begin another experiment from these results.
