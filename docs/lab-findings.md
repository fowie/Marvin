# Marvin hardware investigation

This is the detailed historical lab record, not the getting-started guide.
Use the [repository README](../README.md) for offline development and
[provenance](provenance.md) for private archive/evidence citation conventions.
All shell examples assume the repository root, not this `docs/` directory.
Historical approvals and hardware states below are dated observations, not
standing authorization or a statement about the reader's current hardware.
**Broad sweeps, successor-protocol probes and line-state trials remain
experimental and are not recommended for the now-known-working legacy device.**
Do not execute hardware examples in CI or unattended coding/review sessions.

The controller identifies as Microsoft Marvin, USB `045e:4444`, through J10.
Original successor-drive software explicitly supports this legacy USB identity.
Legacy `S`/`E` framing and six read operations are now live-confirmed on this controller.
No motor/servo command library exists yet.

**Read-only mapping completed, September 14:** eight guarded requests returned
eight correlated replies: configuration, unit info, power state, raw telemetry
(twice), diagnostics (twice, same message, then stopped), and servo-position
readback. The old 134-byte telemetry layout exposes 82 raw fields; 24 changed
between snapshots while reported motor/PWM values stayed zero. All 27
configuration words are mapped with source-confidence and raw bits preserved.
No setter, power-switching, reset or firmware operation was used.
The six getter IDs were `00`, `04`, `0C`, `0E`, `1B`, `1D`; each request was
10 bytes and every reply matched sequence/command with status `80` and a
valid legacy CRC. Payloads were configuration 108 bytes, unit information
12 bytes, power state 2 bytes, raw data 134 bytes per read, diagnostic log
32 bytes per read, and servo position 4 bytes.

The [capability map and bring-up plan](marvin-bringup-plan.md) separates
proved reads from untested controls and physical prerequisites.
The [command catalogue](marvin-command-map.json) covers all 23 historical
sample IDs and both newer 63-slot command tables; the
[configuration export](marvin-configuration.json) preserves all 108 bytes, and
the [raw snapshot](marvin-telemetry-snapshot.json) preserves all 82 fields.
Use `python -m tools.marvin_legacy_probe --help` for the guarded read-only CLI;
it defaults to dry-run. The plan contains live-capture and offline-replay examples.
Actuator power and signals must remain isolated until supervised bring-up.

The new mapping totals **80 application OUT bytes / 538 serial RX bytes**.
USB retains **240 RX prefix bytes**, with **298 RX bytes omitted**; the complete
serial responses remain in private evidence. Confirmed cumulative OUT is
**47,767 + 80 = 47,847 bytes**. The historical **13 delivery-uncertain attempted
bytes remain unchanged** and outside the confirmed total.

UnitInfo reports words `01020000`, `01020000`, `01020304` (hex), not a unique
board identity or exact running image. Two raw snapshots changed 24 fields
over 999.863 host seconds, with 98,081 counter increments: about
**98.094 ticks/second**, not established watchdog or control-loop timing.
Reported motor velocities and PWM remained zero; servo values remained
2500 and 2730. LED channel 15 reported brightness 255 and blink 42; its physical
identity and meaning are unknown. The repeated 32-byte diagnostic was
`taskSystem: after software setup`; reading stopped after the repeat.

**First application reply, September 14 at 18:21 PDT:** one 10-byte legacy
GetConfig request at 57600/8N1, DTR/RTS low, returned a complete 118-byte packet:
matching sequence 0 and command 4, status `80`, 108 configuration bytes, and a
valid CRC. It is not an echo. USB confirms the exact 10-byte OUT and reports
118-byte IN; its retained 32-byte IN prefix matches the complete serial capture.
The remaining 86 received bytes are retained in serial evidence, not usbmon.
This establishes this query and framing, not every historical command or
the runtime firmware version. Configuration values may be compiled defaults.
No firmware, power, or actuator-setting command was sent.

**New archive, September 14:** the supplied `MarvinFirmwareAndSample.zip`
contains a historical serial sample with a second packet format: single-byte
`53`/`45` markers and 10-byte minimum frames, rather than the 12-byte
`efbe`/`adde` format previously tested. This led to the successful query above.
The archive mixes generations: its current C builds use
vendor-bulk identities unlike the connected `045e:4444` CDC device, and the PC
sample has an unresolved `UseNewProtocol` reference and outdated command IDs.
Neither the sample nor bundled firmware has been executed or installed.

**Final campaign result, September 12 at 20:31 PDT:** all **5,617 planned
source cases were attempted**. Of these, 5,613 completed at USB-transport
level and four earlier attempts remain delivery-uncertain; none remain
unattempted. Both complete 336-setting grids (binary and text) completed,
covering seven requested baud rates, twelve serial formats and four DTR/RTS
combinations. **No application reply was captured.**
This result applies to the historical successor-framing campaign, before the
September 14 legacy success; it is not a reason to repeat that campaign.

As of that September 12 campaign, confirmed cumulative application OUT was
47,767 bytes, including 129 bytes
from earlier work; another 13 attempted bytes remain uncertain. This confirms
host-to-device USB transfers, not command recognition or a working robot
protocol. Firmware identity/compatibility and the reported LED change remain
unestablished. No flashing, firmware upload/erase tool or software USB reset
was invoked.

After the owner-reported full power cycle, a two-query same-connection check
and the entire information-first continuation completed without another write
failure. The continuation ran 18:01-20:31 PDT: 2,726 probes in 718 split segments,
12,576 confirmed bytes, zero captured application RX. Question/help/empty-line
and low-DTR cases also completed later, so those factors alone do not explain
the earlier stalls. Phase splitting and recovery interventions changed parser
history; later overlapping probes do not resolve the four historical uncertain
attempts. All transmitters and completion watchers are stopped.

**Earlier result, September 12 at 17:29 PDT:** communication stalled again.
The continuation's first two-byte `?\r` at 9600/8N1, DTR/RTS high, timed out.
A separately reviewed, previously unattempted four-byte `VER\r` after returning
to 115200/8N2 and high DTR/RTS also timed out. Both OUTs were canceled on close
with status -2 and actual length zero; their serial outcomes remain uncertain.
No application RX was captured. The single earlier successful transfer did
not establish sustained recovery after USB reconnection.

All transmission is stopped. Current catalogue accounting is **2,885 completed,
four uncertain attempts, and 2,728 unattempted probes**. Confirmed cumulative
OUT remains 35,177 bytes, with another 13 attempted bytes kept uncertain.
A full controller power-and-USB cold cycle is the next requested recovery
step; it has not been confirmed. No software USB reset or firmware operation
was performed. Cached per-device power state was `control=on`, `active`, with
zero runtime-suspended time, so current device autosuspend was not implicated.

**Earlier result, September 12 at 17:11 PDT:** after the owner's USB reconnection,
a previously unattempted two-byte `?\r` at 115200/8N2 with DTR/RTS high completed
successfully at USB OUT. The 45-second serial / 75-second USB observation
captured no application RX and included complete transfer pairing.
This establishes restored write capability for that probe, not protocol
recognition. Cumulative confirmed OUT is now 35,177 bytes; 2,730 catalogue
probes remain unattempted and two earlier attempts remain uncertain.

**Earlier result, September 11 at 20:05 PDT:** the broad campaign completed
2,884 probes, including all 336 binary settings combinations, without captured
application RX. It then stopped on a five-byte `help\r` USB OUT that never
completed successfully and was canceled on close. A separately reviewed,
previously unattempted two-byte `?\n` at 115200/8N2 with DTR/RTS high also
timed out; its USB trace ended before the slow tty close, so its final OUT
disposition is unknown. No further transmission is running.

The campaign confirmed 35,046 successful USB OUT bytes, or 35,175 including
earlier work; the two uncertain attempts total another seven bytes and are
not included in that confirmed total. There are 2,731 unattempted probes.
Transport recovery and review are required before continuation; do not restart
the full catalogue or automatically retry either uncertain request. No USB
reset or firmware programming was performed. The stall does not establish
which command, line state, earlier parser state, or hardware condition caused it.

At 22:15 the owner reported reconnecting USB and a blinking LED having become
solid during earlier testing. Re-enumeration with unchanged descriptors was
confirmed, but the LED's meaning and post-reconnect state are unknown.
Our tools have sent no application write since then; OUT recovery is unverified.

On September 10, initial GetConfig and GetUnitInfo requests each completed at
USB OUT without application RX. A later bounded series tried GetConfig after
five seconds with all four held DTR/RTS combinations: four more successful
12-byte OUT transfers, still zero RX. This was not a confirmed cold start;
the kernel also briefly asserted both lines during each open before applying
the selected state. After reconnection on September 11, a further single
delayed high/high GetConfig also completed OUT without RX. These successor
EFBE trials did not establish a working protocol; do not repeat commands
automatically. The private source-reference index,
`reference/successor-robot/README.md`, described predominantly Windows/.NET
material, not an installed Python driver. The index and recovered sources
are excluded from publication; this is an archive citation, not a repository
link (see [provenance](provenance.md)).

**Switch-position clarification, September 11:** the owner reported that the
switch had remained at **PRG** since the pre-SSD switch experiments, including
all source-derived binary queries above. This is retrospective owner testimony,
not an electrically measured mode; the label alone does not prove why the
controller is silent.
A subsequent owner-confirmed cold start in PRG produced no application RX
during the completed return observation. No query was sent in that capture;
USB enumeration and the reconnect gap were not recorded.

The owner then confirmed a fully unpowered change to **RUN** with actuator
power and signals isolated. Its completed startup return observation was also
silent. One separately recorded GetConfig, sent after five seconds of listening,
completed its exact 12-byte USB OUT transfer without serial or USB IN payload.
Current owner-reported switch position is RUN; no further command is queued.
These results establish neither firmware acknowledgment nor the switch's
electrical function.

A later explicitly approved RUN experiment sent GetUnitInfo (27), then
GetSensorInfo (29) after reviewing the first capture. Both exact 12-byte USB
OUT transfers completed, still with zero application RX or observed telemetry.
The captures used separate tty opens, not the recovered service's continuous,
response-dependent startup connection. Telemetry enablement remains unverified;
no further command is queued.

Known hazards: the TI USB hub reported an active port-4 over-current indication;
the PEND TXCVR USB-A socket has a missing plastic contact support. These remain
unresolved. Keep damaged/low-voltage sockets unused, do not bypass protection,
and disconnect all motor and servo power before protocol investigation.
Make/remove actuator wiring only while all supplies, including USB back-power,
are disconnected. Leave actuator signal connections isolated too; removing only
the main motor supply is not sufficient.

## Coordinated USB/serial observation

`tools/marvin_session.py` records one persistent, receive-only serial session
inside a longer, target-scoped usbmon recording. It makes no application writes by
default. Hardware readiness and the specific powered session must be
approved before running it.

The recorder starts before tty open and has a budget intended to extend past
tty close. An abnormally slow close can outlast that fixed recording window;
check timestamps and pending transfers rather than assuming complete coverage.
The optional `--usb-close-grace-seconds 30` reserves extra maximum recording
time for slow close; normal captures still stop after their nominal budget,
tty close and a short drain. The total USB budget must not exceed 120 seconds.
The defaults are 60 seconds of serial observation inside a 90-second USB
recording. Both stop if the device identity changes; no reconnection or retry
is attempted. Requested control-line states are set before open and then held,
although kernel-open transients remain possible.

Cached identity/driver/ownership inspection, without opening the tty:

```sh
.venv/bin/python tools/marvin_session.py --preflight
```

Once a controller-only J10 setup is approved, the following command records
the already-tried 115200/8N1, DTR/RTS asserted configuration. This remains a
source-derived setting from the recovered legacy serial manager, not yet proof
that our installed firmware accepts the same commands:

```sh
.venv/bin/python tools/marvin_session.py \
  --actuators-isolated --dtr --rts --allow-line-state-change \
  --seconds 60 --baudrate 115200 \
  --output /absolute/path/to/a/new/observation
```

**Host permissions:** the coordinator defaults to the supported character
interface `/dev/usbmonBUS`, which normally requires privilege to open.
If usbmon is not loaded, the separately
approved host setup is `sudo modprobe usbmon`. The recorder does not load
modules, change security policy, or change device permissions automatically.

This host's kernel lockdown blocks the older debugfs text interface even for
root. The binary character interface works without disabling lockdown or Secure
Boot. `--usbmon-backend text` explicitly selects the older debugfs path on hosts
where it is permitted; there is no silent fallback or security-policy change.
The implemented binary ABI is restricted to little-endian x86-64 and rejects
other architectures rather than guessing their ioctl layout.

If opening the monitor needs sudo, run this sequence in the same terminal
so the sudo authentication applies to the capture subprocess:

```sh
sudo -v &&
.venv/bin/python tools/marvin_session.py \
  --sudo-usbmon --actuators-isolated --dtr --rts --allow-line-state-change \
  --seconds 60 --baudrate 115200 \
  --output /absolute/path/to/a/new/observation
```

Only the usbmon reader starts under sudo; it opens the verified bus monitor,
drops supplementary groups and root privileges to the invoking user, and then
creates evidence. The coordinator and serial collector run as the ordinary
user. Noninteractive sudo failure stops the session before tty open; it does
not hang waiting for a password. Do not run the coordinator or a GUI as root,
make all USB monitors readable, or disable ModemManager globally.

Before runtime identity/ownership preflight or output creation, the coordinator,
boot wrapper, campaign, trials, direct serial capture, and direct USB recorder
validate the new output destination. Existing destinations (including dangling
links), symlink or non-directory ancestors, and `/dev`, `/proc`, or `/sys` paths
are rejected.
Every supplied component is checked, including ancestors hidden by `..`;
links are not resolved. The coordinator, boot wrapper, campaign, trials, and direct
serial capture still allow missing nested parents, created only after preflight.
The legacy probe requires existing parent directories and checks them before
loading its transport runtime. The direct USB recorder also requires existing
parents and validates its destination before identity checks, monitor access, or
privilege drop; evidence-directory creation remains after privilege drop.
These are snapshot checks, not protection against concurrent ancestor replacement.
Final directory creation remains exclusive: a destination created after the
precheck is not overwritten or resumed.

Each new private output directory contains:

| Path | Content |
|---|---|
| `metadata.json` | Whole-session identity, timing, status, limits and errors |
| `usb/` | Filtered original binary events, normalized text, cached descriptors, readiness, metadata and transport summary |
| `serial/` | Raw received bytes, timestamped chunks and lifecycle events |
| `usbmon-stdout.log`, `usbmon-stderr.log` | Recorder diagnostics |
| `SHA256SUMS` | Host-side hashes of retained evidence, including partial failed sessions |

Root manifests include nested segment manifests; only the root manifest itself
is excluded. Metadata or manifest sealing failures mark the run failed and
record `evidence_sealing_error`, without replacing an earlier capture exception.
If failed metadata cannot be persisted either, the exception carries that
additional failure; files alone may then retain a stale status and must not be
treated as sealed evidence. Hashes attest retained host bytes, not physical safety.

Direct binary recordings and binary-backed sessions require explicit integer-zero
final `queued` and `dropped` monitor statistics before reporting completion.
Missing, malformed or nonzero counts turn otherwise completed captures into
failures, including boot and legacy wrapper captures. Direct recordings already
stopped by a signal or limit retain their existing non-success status; queued
tails are not drained with extra reads or extended budgets. Text captures do not
invent binary statistics; their existing completeness checks still apply.

**Privacy and interpretation:** unrelated devices' events can enter the
usbmon reader's memory, but are filtered out before storage. Use an isolated
bus if this transient exposure is unacceptable. Both backends avoid libpcap's
capture-start descriptor probing. The initial diagnostic retains at most 32
payload bytes per event: the text interface's limit, and an explicit GETX copy
limit for the binary interface. It records host USB
requests/completions, not individual wire-level ACK/NAK tokens. Pending IN
requests are not proven timeouts; successful OUT completions are not application
acknowledgments. A tool that makes no writes does not rule out automatic kernel
echo during tty opening. The GetConfig handler only copies metadata in the
examined successor firmware. The later successful legacy GetConfig exchange
does not identify the installed handler implementation or prove its side effects.

With the binary backend, `usbmon.txt` is normalized analysis text, not original
debugfs output. Its timestamps are Unix realtime microseconds. The authoritative
`binary-events.bin` starts with ASCII `MVUSBBIN1\n`; each frame contains a
little-endian 16-bit copied-payload length, the exact 64-byte Linux GETX header,
and those payload bytes. Header `len_cap` may exceed the copied length.
Only target events are stored in either file. The byte budget covers both
event files together. Monitor drop counters are retained, and any reported loss
makes the capture explicitly incomplete.

The binary ABI is based on the Linux
[`mon_bin.c` implementation](https://github.com/torvalds/linux/blob/9f0346dcbea363787186c94ef94dd01aaa215afa/drivers/usb/mon/mon_bin.c).

For offline USB evidence interpretation (does not access hardware):

```sh
.venv/bin/python tools/marvin_usbmon.py --analyze /path/to/usb/usbmon.txt
```

Offline analysis uses the shared bounded regular-file reader: device/kernel
paths, symlinks in any input path component, special files, oversized files, and
inputs changing during the read are rejected. `--max-bytes` and `--max-records` also apply to `--analyze`
(Python API: `analyze_file(..., max_bytes=..., max_records=...)`). Defaults are
1 MiB and 10,000 records; explicit maxima are 64 MiB and 1,000,000 records.
Existing per-line and pending-pair bounds still apply. Analysis of a retained
file alone cannot establish that its original capture was complete.

For text capture, a partial target or unclassified line at a stop boundary is
counted as `unretained_partial_line_bytes`; raw partial data is not published
because it may contain unrelated bus traffic. A positively identified
unrelated tail is counted separately as `ignored_partial_line_bytes`. On a stop
within an already-read batch, the recorder finishes only bounded in-memory
framing/filtering, never another monitor read or an over-budget evidence write.
Complete potentially-target records not retained are counted as
`unprocessed_records` and `unprocessed_record_bytes`; unrelated records are
counted but their payloads are discarded.

A duration/coordinator stop with either target gap becomes a failed capture,
not successful empty or truncated evidence. Signal/limit stops retain their
non-success statuses. Session, campaign, and trial assessment also reject
nonzero or malformed gap counters (and `unaccounted_retained_bytes`), even
when older metadata says `completed`; rejected-setting continuation uses the
same check. Absent counters remain accepted for historical metadata
compatibility, not as proof of losslessness. Retained byte/record counts and
raw evidence still describe only what was actually saved.

## Settled queries and bounded line-state trials

Historical successor-framing experiments below predate the legacy success.
They remain experimental, not recommended follow-up for this working device.

`--probe-delay 5` adds a listening window before the selected one-shot probe.
Any serial data observed before the write suppresses that probe; reception
continues for the remaining capture duration. A final nonblocking read checks
already queued data before the write. The default delay remains zero for
compatibility with earlier experiments. Delay must be finite, at most 30
seconds, and shorter than the total `--seconds` duration.

For a five-second settling experiment with roughly ten seconds remaining
after the query, use `--seconds 15 --probe-delay 5` with the existing guarded
GetConfig invocation. Delays are included in the serial duration, not appended
to an unbounded session. Metadata/events distinguish a written request,
early-RX suppression, deadline suppression, and an uncertain write.
This is a timing experiment, not a recovered firmware requirement.

`tools/marvin_trials.py` provides a fixed, GetConfig-only matrix at 115200/8N1:
held DTR/RTS high/high, high/low, low/low, and low/high. A complete run has at
most four writes / 48 bytes. Its default operation only prints the plan:

```sh
.venv/bin/python tools/marvin_trials.py
.venv/bin/python tools/marvin_trials.py --case high-high
```

After explicit approval of the powered, actuator-isolated setup and line-state
effects, a live run can use:

```sh
sudo -v &&
.venv/bin/python tools/marvin_trials.py --run \
  --sudo-usbmon --actuators-isolated --allow-unknown-command \
  --allow-line-state-trials --power-state existing-unverified \
  --output /absolute/path/to/a/new/trial-series
```

Use `--case high-high` to run only the settled baseline. Named cases cannot
repeat within a run. Each gets its own 15-second serial / 45-second USB
capture, with the same initial USB identity pinned across the series. A
silent case advances only after its exact request bytes and unique successful
USB OUT completion are established. Any serial or bulk/interrupt USB input,
capture error, queued/dropped monitor tail, pairing problem, uncertain write,
or identity change stops the series. It never resumes or retries a case.

For named coordinator queries, departures from DTR/RTS high/high require the
separate `--allow-line-state-trial` option; that exception still applies only
to GetConfig at 115200/8N1, not GetUnitInfo or GetSensorInfo. The coordinator's
Python schedule API requires `allow_line_state_trial=True` or the generic
`allow_line_state_change=True` whenever DTR or RTS is asserted, for every
transmit profile. Generic line-state consent also supports passive sessions,
CR, and named queries using their required settings; it never waives the
GetConfig-only exception or the named-query line/framing restrictions. Valid
trial consent is forwarded as explicit line-state consent to serial capture.
Its existing low/low defaults, including the fixed legacy wrapper settings,
remain unchanged. Authorization never bypasses byte/profile validation or
permits combining a schedule with a named probe.

Reopening the port does not reset the controller: firmware/parser state can
carry across cases, and kernel-open line transients remain possible.
`existing-unverified` makes no cold-start claim. Only select
`owner-confirmed-cold-start` after the owner has actually removed all relevant
power, including USB back-power, and restored the isolated setup. That label
describes the initial state, not a fresh restart before every case. The tool
does not power-cycle hardware or infer the RUN/PRG switch's electrical role.

Pre-write suppression covers serial bytes the listener observes. pySerial's
initial open flush can still discard an early banner; the USB trace starts
earlier, but its incoming payload is assessed after that case. No guarantee
of atomic detection of a concurrently arriving byte is implied.

## Separately approved telemetry handshake

This is the historical successor-protocol experiment, not the working legacy
profile: legacy `1D` is GetServoPosition, not successor GetSensorInfo.
The old approval does not authorize repeating the experiment.

`--probe-get-sensor-info` sends one empty GetSensorInfo request (opcode 29,
sequence 2). It requires both `--allow-unknown-command` and
`--allow-telemetry-state-change`, with 115200/8N1 and DTR/RTS high, just like
the stateful GetUnitInfo option. All probe options are mutually exclusive;
the coordinator never automatically sends the two-query handshake.

In examined successor firmware, GetUnitInfo (27) and GetSensorInfo (29) set
two flags that together enable controller-generated heartbeat telemetry.
GetSensorInfo itself returns 128 synthetic bytes `00..7f`, not live sensor
measurements. Neither compatibility nor those effects are established for
Marvin's installed firmware. This experiment requires explicit approval and
continued isolation of actuator power and signals.

Only after separately capturing and reviewing GetUnitInfo, a specifically
approved second stage can use a fresh output directory:

```sh
.venv/bin/python tools/marvin_session.py --sudo-usbmon \
  --actuators-isolated --dtr --rts --seconds 15 --probe-delay 5 \
  --allow-line-state-change \
  --probe-get-sensor-info --allow-unknown-command \
  --allow-telemetry-state-change \
  --output /absolute/path/to/a/new/sensor-info-capture
```

Each stage sends at most 12 bytes and never retries. Stop on unknown received
data, an uncertain write, identity change, or incomplete evidence; do not
automatically advance. Review a valid reply before deciding whether to proceed.
Separate captures reopen the tty and can change control lines; they are not
an exact replay of the original service's persistent connection. Sending the
second query after a silent first one is an explicitly approved experiment,
not the original service's response-dependent startup behavior. No host
heartbeat, motion, reset, or flash operation is part of these named probes.

## Broad, persistent-connection communication campaign

**Historical and experimental; NOT recommended for the known-working legacy
device.** The successor command audit does not make these opcodes safe in
legacy S/E framing; getters can collide with setters or resets. Preserve the
old execution segments/bytes for reproducibility, but do not restart the
sweep or use it as a fallback when a legacy read fails.

`tools/marvin_campaign_plan.py` builds a finite, source-audited catalogue without
opening hardware. `tools/marvin_campaign.py` executes it only with `--run`;
without that flag it prints the planned coverage and exact transcript.

The full catalogue contains **724 segments, 5,617 probes, 6,553 individual
writes, and at most 47,651 application bytes**. It includes a full Cartesian
grid of seven baud rates (4800 through 230400), twelve serial formats
(7/8 data bits, N/E/O parity, 1/2 stop bits), and four DTR/RTS combinations:
**336 settings combinations in each of the binary and text phases**.
Additional segments vary sequence numbers, bounded repetitions, byte/fragment
pacing, coalesced requests, text spelling/terminators, and limited framing/CRC
errors. Malformed cases run last because they may leave parser state behind.
This is exhaustive only for that enumerated grid and seed set, not all possible
opcodes, payloads, flow-control modes, or timing values.

Binary queries are limited to the source-audited opcodes 3, 4, 27, 29, 38 and
46. In examined successor firmware, opcode 3 reads cached telemetry (not an
arbitrary memory address), and opcode 46's `00`/`01` selectors read RAM/persisted
calibration, without committing it. The catalogue classifies all managed
command IDs and excludes known firmware writes, erase/unlock, persistent
writes, resets, power switching, actuation, and unassigned opcodes. Text probes
are query hypotheses, not established Marvin commands. Flow control, BREAK,
baud zero, and arbitrary padding/random-byte sweeps are excluded.

```sh
.venv/bin/python -m tools.marvin_campaign --profile quick
.venv/bin/python -m tools.marvin_campaign --profile full
```

The historical RUN campaign required separately approved actuator power **and
signal** isolation. The equivalent invocation below includes the now-required
separate line-state acknowledgment; it is not a recommendation or authorization
to run it now:

```sh
sudo -v &&
.venv/bin/python -m tools.marvin_campaign --run --profile full \
  --sudo-usbmon --actuators-isolated --allow-unknown-command \
  --allow-telemetry-state-change --allow-line-state-trials \
  --switch-position RUN --max-seconds 14400 \
  --output /absolute/path/to/a/new/communication-campaign
```

`run_campaign(..., allow_line_state_trials=True)` requires an exact boolean
acknowledgment even for a low/low-only plan. Isolation, command authorization,
and telemetry-state consent do not imply line-state consent. Missing/false or
non-boolean consent fails before identity preflight, output creation, or a
capture. Campaign metadata records `line_state_trials_authorized`, and every
segment receives the corresponding coordinator `allow_line_state_trial` flag,
recorded as `line_state_trial_authorized`. The coordinator forwards this
validated consent to serial capture as `allow_line_state_change=True`;
both record `line_state_change_authorized`. This acknowledges possible DTR/RTS
firmware state/reset effects; it does not establish safety or eliminate
kernel-open transients. Planned bytes, line settings, and segment timing are
unchanged.

Each segment keeps one tty open across its probes, including the 27/29 pair.
Reads continue between fragments and response windows. Any observed serial
byte suppresses all remaining scheduled writes while bounded reception
continues. Incoming USB payload also stops the campaign at segment assessment.
The initial pySerial flush and the non-atomic read/write boundary remain
limitations; the USB recorder starts before tty open. Serial configurations
are requested settings, not proof of accepted firmware/UART behavior.
Seven-bit binary trials retain logical eight-bit USB bytes and are explicitly
CDC-line-coding hypotheses, not valid seven-bit UART packet encodings.

Successful silent segments must match the complete outgoing byte stream and
successful USB completions, allowing normal URB-ID reuse and write
coalescing/splitting. RX, identity changes, monitor loss, incomplete pairing,
truncated outgoing evidence, and uncertain/short writes prevent continuation.
A narrowly identified host line-setting rejection is recorded as
`unsupported_settings`, never as a tested exchange; continuation requires
proof of zero application I/O, stable identity, and intact USB evidence.
Unexpected USB completion statuses, other negative submission statuses, and
submission-error (`E`) events prevent silent or rejected-setting continuation
across campaign and trial assessments. Existing handling of known, matched
zero-length IN cancellation/shutdown completions is unchanged; these events do
not establish application delivery or acknowledgment.
No failed write or segment is automatically retried.

The observed write-timeout path can spend about 30 seconds closing the tty.
The original five-second USB allowance alone missed cleanup in a short failed
recovery capture. The campaign now reserves another **30 seconds of maximum
close grace** without adding that delay to healthy segments. After tty close
and the nominal recording window, the coordinator requests completion through
an opt-in empty regular file in the private recording directory. Readiness,
stop timing, signal state, capture limits and complete pairing remain checked;
an unexpected early exit cannot become a successful segment.

Metadata distinguishes nominal and maximum USB budgets. The recorder's own
maximum remains bounded, and the campaign's shared deadline may stop it earlier;
an unpaired OUT is still uncertain, and cancellation or delivery must never be
inferred after the retained trace ends.

`plan.json` is frozen before transmission. Campaign and per-segment metadata,
raw serial chunks/events, USB evidence and hashes retain planned, attempted,
completed, rejected, and unfinished coverage. The full plan budgets about
3.23 hours including five extra USB recording seconds per segment, before
setup/host overhead. `--max-seconds` (at most 14400) establishes one absolute
monotonic operational deadline before the initial runtime preflight. The existing
per-next-segment reservation is prospective, not a cumulative charge. Each
admitted session shares the same deadline across preflight subprocess timeouts,
recorder readiness, the quiet window, serial reads/writes, and tail/grace waits.
No new application write is started once expiry is observed, including after slow
metadata/event writes; no retry or extra transport read extends the budget.
Deadline expiry stops further segments with `stopped_wall_limit` and marks an
affected capture failed rather than completed. Earlier independent failures keep
their error and non-success status.

This is not a hard-real-time guarantee that the function returns within four
hours: OS scheduling and in-flight calls cannot be preempted reliably, accepted
driver bytes cannot be retracted, and safe tty close, recorder shutdown, raw-byte
retention and evidence sealing still run even after expiry. Work completed before
the deadline is not retroactively failed solely because final evidence sealing
finishes later. The shared absolute deadline is recorded in campaign, session and
serial metadata; standalone sessions/captures without it retain their usual
timing limits.
Reopening between segments does not reset the MCU, so later negative results
can still depend on earlier parser state.

## Offline streaming, replay, and telemetry

These modules do not access serial ports, USB interfaces, or recovered
executables:

- `tools.marvin_stream.StreamDecoder`: incremental framing with absolute byte
  offsets and explicit frame/noise/error/partial spans. It handles split and
  coalesced valid packets without treating their embedded delimiters as boundaries.
- `tools.marvin_telemetry.TelemetryDecoder`: interprets exact successor
  UnitInfo, 157-byte drive heartbeat, and 36-byte head heartbeat layouts from
  the curated catalogue. GetConfig retains its extra 96 metadata bytes.
  Unknown layouts and error responses stay raw; names/units are not guessed
  or calibrated for our predecessor.
- `tools.marvin_replay.replay_capture`: bounded, read-only replay of a finished
  `received.bin`, optionally checking exact coverage and byte agreement with
  its timestamped `chunks.jsonl`.

```sh
.venv/bin/python -m tools.marvin_replay /path/to/serial/received.bin \
  --chunks /path/to/serial/chunks.jsonl --evidence recorded
```

Output is JSON on stdout, never a modification to the capture. Use
`--evidence synthetic` for constructed fixtures. Evidence kind and direction
are caller declarations, not authentication; a valid frame/layout does not
establish a matched-request acknowledgment. Outgoing/unknown-direction bytes
and possible echoes are not silently interpreted as telemetry.

The stream decoder defaults to a 4096-byte payload bound (configurable through
65535) and a 4108-byte retained buffer. It waits conservatively for an in-bound
declared frame length; a damaged length can postpone later frames until EOF.
At EOF it can recover a later CRC-valid candidate with an explicit
`ambiguous_eof_resync` diagnostic, since that candidate might instead be part
of a truncated payload. `--retain-incomplete` disables only that incomplete-EOF
recovery, not the separate complete-invalid-candidate policy.

Modern complete candidates with invalid CRC/footer and out-of-bound lengths
are rejected **one byte at a time**. This preserves recovery of real following
frames after length damage, but can also select a valid frame nested in a
rejected outer payload. In contrast, the legacy decoder retains a complete
invalid candidate as one span and never searches inside it. Neither decoder
searches inside an incomplete in-bound candidate while feeding, and valid outer
frames retain nested frames as payload.

Modern `invalid_frame.raw` covers the one rejected header byte, not the entire
candidate; its bounded preview is diagnostic only. Successive raw spans still
partition every input byte. After a rejected candidate or ambiguous EOF
recovery, events carry sticky `follows_corruption: true`; later validated frame
boundaries remain **resynchronization hypotheses**, including frames that really
followed damaged length fields. This flag does not itself prove physical
corruption. Replay exposes it, retains the same decoded bytes/layout fields,
and adds a boundary warning to any telemetry interpretation. CRC validity alone
does not distinguish nested payload from a genuine next-frame boundary.

Replay defaults to 16 MiB raw input, 64 MiB chunk metadata, and 100000 chunks
and events. Limit violations or inconsistent chunk metadata produce an
explicit input error, not a truncated success. Empty receive files are valid.
The shared reader used for modern/legacy replay, firmware snapshots, catalogues
and usbmon analysis rejects links in every supplied path component before
opening (including components that `..` would otherwise normalize away).
Regular-file, byte-bound, inode and in-read change guards remain in force.
These path checks are pre-open snapshots, not an atomic lock on parent-directory
identity against concurrent replacement.
Campaign, trial and rejected-setting trace assessments derive both their parsed
records and pairing summaries from the same bounded snapshot, without a second
path read. They preserve the analyzer's effective 1 MiB and 10,000-record defaults;
the former 2 MiB preliminary size check did not override that stricter analyzer
limit. Line and pending-pair limits also remain in force.
Nonfinite telemetry floats keep their original bits and use JSON-safe labels.
The published generated fact catalogue is a read-only input. The private
source-reference collection is neither distributed nor required to run public
tests; original source-hash verification requires that private archive.

## Owner-operated boot observation

`tools/marvin_boot_capture.py` wraps the existing guarded coordinator for one
explicitly requested power cycle. Run it as the ordinary user after the same
usbmon setup/authentication described above:

```sh
.venv/bin/python tools/marvin_boot_capture.py \
  --sudo-usbmon --actuators-isolated --allow-line-state-change \
  --output /absolute/path/to/a/new/boot-observation
```

Wait for `READY` before the owner cycles power. The initial segment listens
for up to 90 seconds, with USB recording through a further 30-second tail.
If the USB device disappears, the interrupted segment is preserved, the
observer uses a 90-second deadline for one return on the same physical port
with matching VID/PID and descriptors, then starts a new USB recorder before
opening the returned serial port for 60 seconds. A second disconnect or an
unrelated error stops the observation. No application bytes are sent.
Return preflight shares the remaining deadline across its `udevadm` and `fuser`
checks, each still capped at five seconds. Expired preflight results are rejected
before return-identity acceptance. Process creation/cleanup, host scheduling and
cached filesystem operations are not hard-real-time operations and may delay
timeout reporting; a late result cannot authorize the return segment.
The separate `--allow-line-state-change` flag acknowledges both segments'
DTR/RTS-high requests, which can affect or reset custom firmware despite the
absence of application writes. Isolation alone does not authorize these effects.
The wrapper forwards that same explicit consent through both coordinator
segments to serial capture; no synthetic probe, schedule, or GetConfig trial
is needed or selected for a line-only observation.

This is **segmented recording**, not a continuous capture of enumeration:
there is a gap between device removal and readiness of the second recorder.
A boot message before that point may be missed. If USB does not re-enumerate,
the original segment remains open; its completion does not itself prove that
a power cycle occurred. USB back-power may keep the MCU running.

The wrapper saves overall timing/status/readiness plus each segment's original
USB/serial evidence and errors. An expected disconnect is not relabelled as a
successful uninterrupted capture. Keep actuator isolation in effect and do
not reconnect damaged ports or change RUN/PRG during this experiment.

## Prevent automatic modem probing

With the robot disconnected from the computer:

```sh
sudo install -m 644 udev/78-mm-marvin.rules /etc/udev/rules.d/78-mm-marvin.rules
sudo udevadm control --reload-rules
```

Reconnect after installing and reloading the rule. The capture tool checks the
live tty's USB identity and both ModemManager ignore tags before opening it.
This does not prevent every possible program from accessing the serial port;
close serial terminals and other clients before use.

## Receive-only capture

Use the existing project virtual environment, with `pyserial==3.5` installed.

```sh
.venv/bin/python tools/marvin_probe.py \
  --actuators-isolated \
  --seconds 10 \
  --baudrate 115200 \
  --output /path/to/a/new/capture-directory
```

The output directory must not already exist and must pass the output-path checks
above before device identity or `fuser` ownership checks. It contains raw `received.bin`,
timestamped read chunks in `chunks.jsonl`, and session details in `metadata.json`.
`events.jsonl` records attempted/completed open, control-line, write and close
operations with wall-clock and monotonic timestamps. Captures check for existing
owners with `fuser`, use pySerial's advisory lock, and request Linux `TIOCEXCL`
after open to prevent later unprivileged opens. These measures cannot exclude
an already-open or privileged reader, and other-user owners may be invisible.
Read chunks are transport reads, not identified protocol message boundaries.
The default byte limit is 65536; capture also has a time limit.
Python callers must supply positive integers for baud rate and byte limit.
Duration and probe delay accept bounded finite integers/floats, not booleans.
These checks precede device checks and evidence-directory creation.

115200 baud, 8N1 matches the recovered legacy host; installed-firmware
compatibility remains unverified.
The tool initially requests DTR and RTS low and disables software/hardware flow
control. By default it transmits no application data. The optional `--dtr`
flag asserts host-ready DTR after opening, so any DTR-triggered response is not
discarded by pySerial's initial input flush. This is a deliberate control-line
change that may start or reset custom firmware; it is not the default. The
optional `--rts` flag similarly asserts RTS after opening. These are fixed line
states, not automatic hardware flow control.
Use `--line-state-at-open` to request the selected DTR/RTS values before open
instead of making those additional post-open transitions.

Both direct serial capture and the coordinator require separate
`--allow-line-state-change` consent whenever either line is asserted, regardless
of whether the assertion is requested before or after open. In Python use
`allow_line_state_change=True`; strings, integers, and other truthy values are
rejected before identity/device access or output creation. Isolation,
telemetry-state consent, and selection/authorization of a query or schedule do
not imply line-state consent. `--line-state-at-open` with both lines low does
not require it. Serial metadata records the exact forwarded consent as
`line_state_change_authorized`; coordinator metadata records the effective
generic or valid trial consent and separately records trial authorization.
Neither consent guarantees freedom from kernel-open/close transients.

**Receive-only is not electrically passive.** Opening sets CDC line coding and
control lines, driver transients may occur, and pySerial discards queued input
during open. A very early startup banner could therefore be missed. Silence
does not establish a dead controller, the correct line settings, or a protocol.
Neither successful enumeration nor capture clears the electrical faults.

## One-shot active probes

These are retained historical successor-EFBE and text experiments, not the
recommended legacy interface. They are not authorized by reading this document.

Active probing requires a separate, explicit acknowledgment of unknown-command
risk. Even with actuators disconnected, commands might change settings or
firmware state on the unverified predecessor.

The historical first query was source-derived **GetConfig, opcode 4**, using
successor framing. The later working legacy query has a different 10-byte frame;
do not substitute this 12-byte request:

```sh
sudo -v &&
.venv/bin/python tools/marvin_session.py \
  --sudo-usbmon --actuators-isolated --dtr --rts --allow-line-state-change \
  --probe-get-config --allow-unknown-command \
  --seconds 10 --baudrate 115200 \
  --output /absolute/path/to/a/new/get-config-observation
```

This attempts exactly one 12-byte frame, `ef be 00 00 04 00 00 00 11 33 ad de`,
only after USB recording is ready. It never retries or sends initialization,
heartbeats, resets, or firmware-update commands. GetConfig mode requires the
recovered 115200/8N1 and DTR/RTS-high settings; it cannot be combined with CR.

This named-query settings policy is shared by the coordinator and direct serial
capture, before output validation or runtime preflight. It covers all
modern-profile GetConfig/GetUnitInfo/GetSensorInfo request sequences, including
fragmented or combined modern schedules, not just the fixed CLI sequence bytes.
Generic `--allow-line-state-change` consent does not waive the settings policy.
Only a GetConfig-only modern request or schedule may use lower DTR/RTS levels,
with genuine `allow_line_state_trial=True` API authorization (or the coordinator's
existing `--allow-line-state-trial`); it must still use 115200/8N1. The direct CLI
does not expose that trial exception. GetUnitInfo/GetSensorInfo still require
high/high lines and separate telemetry-state authorization.

The coordinator forwards generic and trial consent separately; serial metadata
records `line_state_trial_authorized` rather than inferring it from generic
consent. Receive-only and CR operation retain their existing settings rules.
Explicit legacy-profile getters keep their legacy framing and low-line behavior,
and separately authorized historical `experimental-successor` schedules retain
their broader settings. These profiles are never selected by guessing from bytes.

The examined successor firmware returns **108 payload bytes / 120 frame
bytes**, with a 12-byte UnitInfo prefix (three little-endian uint32 values:
firmware version, communications version, serial number). Keep the full serial
stream, not just the first read or prefix. The USB recorder still snapshots
at most 32 bytes per event and explicitly counts omitted payload bytes;
this limitation must not be described as complete USB packet evidence.

`tools/marvin_protocol.py` is an offline builder/strict single-frame decoder:

```sh
.venv/bin/python tools/marvin_protocol.py generate
.venv/bin/python tools/marvin_protocol.py inspect --response "<one complete received frame in hex>"
```

It validates length, CRC and footer before interpreting known layouts, retains
the entire payload, and does not infer metadata fields for an unknown length.
Read chunks are not necessarily packet boundaries. The decoder has no device
access, and its generator exposes only the two documented identity queries,
not arbitrary opcodes.
Packet framing comes from the recovered `Common.Firmware` Packetizer/Crc16
and is corroborated by successor firmware and historical logged packets.

Do not substitute GetUnitInfo (27) based on its name: it returns a different
length and sets a handshake flag. In the examined firmware, queries 27/29
together enable telemetry. HostHeartbeat (2) instead rearms a wheel watchdog.
The original robot service automatically operates mechanisms during startup;
running it is not an equivalent diagnostic.

After independently reviewing the first capture, a separately authorized
GetUnitInfo experiment may use `--probe-get-unit-info` in place of
`--probe-get-config`, additionally requiring `--allow-telemetry-state-change`.
It sends sequence 1, `ef be 01 00 1b 00 00 00 17 36 ad de`, once, expecting a
12-byte payload / 24-byte frame in the examined successor. It is not an
automatic fallback or a complete initialization sequence; it can enable
telemetry if the sensor-info flag was already set. No sensor-info request
is sent by this tool.

For an independently authorized single carriage-return experiment:

```sh
.venv/bin/python tools/marvin_probe.py \
  --actuators-isolated --dtr --allow-line-state-change \
  --probe cr --allow-unknown-command \
  --output /path/to/a/new/carriage-return-capture
```

Only one application write is attempted, with no automatic retries. Probes are
restricted to named fixed requests (the earlier raw-hex CLI was removed).
Metadata records the requested bytes, write result, and
response capture. A timeout may leave the number of bytes written unknown;
never blindly retry such a probe. A completed write only means acceptance by
the serial driver, not acknowledgment or successful execution by Marvin.

To verify USB OUT completion for one **separately authorized** CR byte, use
the coordinated recorder rather than sending an untraced probe:

```sh
sudo -v &&
.venv/bin/python tools/marvin_session.py \
  --sudo-usbmon --actuators-isolated --dtr --rts --allow-line-state-change \
  --probe-cr --allow-unknown-command \
  --seconds 10 --baudrate 115200 \
  --output /absolute/path/to/a/new/traced-cr-observation
```

This uses the same one-write/no-retry implementation and stops if the USB
recorder is not ready or exits. It records the intent before transmission.
The CR option accepts only the single byte `0d`; the identity-query options
expose only their two fixed frames. The USB trace must establish its actual transfer outcome;
CR is not a verified harmless firmware command.

## Future firmware acquisition: local screening only

`tools/marvin_firmware.py` is ready for future dumps, but does not connect to a
debugger, read a device, unlock protection, or flash anything. No firmware has
been read from this controller; recovered successor images are not its backup.
Debug-header pinout, reference voltage, adapter selection and
non-programming attachment configuration remain separate prerequisites.

Given two independently acquired full 256-KiB flash images:

```sh
.venv/bin/python tools/marvin_firmware.py flash-A.bin flash-B.bin \
  --registers recorded-registers.json --output new-screening-report.json
```

The optional register file must contain `FMPRE0`, `FMPRE1`, `FMPRE2`, `FMPRE3`
as 32-bit integer values or `0x`-prefixed strings recorded during that same
acquisition. Do not invent values or substitute FMPPE write/erase protection
for FMPRE read protection. Without that record the result remains explicitly
`protection_unverified`, even if the images match.

Screening checks size, matching SHA-256 hashes, blank/protected-read patterns,
and Cortex-M vector plausibility at flash base zero. The top-of-SRAM initial
stack value `0x20018000` is allowed. Exit 0 means preliminary screening passed;
exit 2 means investigation/protection evidence is still needed; exit 1 means
invalid inputs or another execution error. Even a pass does not authenticate
the firmware, prove the register snapshot belongs to it, establish a protocol,
or authorize programming. Original inputs are never changed.

Device reference: [TI LM3S5B91 datasheet, preserved copy](https://datasheet.datasheetarchive.com/originals/library/Datasheets-UD1/DSAUD009783.pdf).

## Offline tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

These tests use mocked transport/sysfs/privilege operations and synthetic
USB/firmware fixtures; they do not contact hardware.
