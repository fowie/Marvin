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
