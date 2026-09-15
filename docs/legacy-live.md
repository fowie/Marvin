# Bounded LIVE legacy collection

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
