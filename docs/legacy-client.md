# Persistent legacy getter client

`tools.marvin_legacy_client.LegacyClient` is a synchronous, transport-independent
implementation for #7 (Epic #1). It opens **no devices** and adds no serial
adapter, polling/recording service (#8), actuator API or live authorization.
The existing `marvin_legacy_probe` one-shot path and modern EFBE behavior are
unchanged. Importing/invoking the client module does not perform I/O.

Run the complete in-memory example, using only synthetic replies and a fake
clock:

```sh
python -B -m tools.marvin_legacy_client_example
```

The [example adapter](../tools/marvin_legacy_client_example.py) is deliberately
offline-only. Its zero-filled payloads are synthetic shapes, not measurements.
The API used there is:

```python
from tools.marvin_legacy_client import LegacyClient
from tools.marvin_legacy_client_example import SyntheticTransport

transport = SyntheticTransport()
session = LegacyClient(
    transport,
    ownership_key=b"synthetic-example",
    expected_identity=b"synthetic-example-generation-1",
    session_timeout=2,
    clock=transport.clock,
    evidence_kind="synthetic",
)
with session:
    config = session.request("get-config", timeout=0.5)
    power = session.request("get-power-state", timeout=0.5)

assert session.state == "closed"
assert session.accepted_bytes == 20
assert config.stream.packet.command == 0x04
assert power.stream.packet.command == 0x0E
```

## Lifecycle, requests and evidence

Construction is inert. `start()` (also `__enter__`) claims the resource and calls
fresh adapter revalidation. The constructing **thread object** owns start,
request and close; other threads and reentrant calls fail with `OwnershipError`.
Process-local claims reject both shared transport objects and duplicate
`ownership_key` values. The caller must supply the same canonical key for aliases
of one resource. This is not an OS lock or protection against dishonest adapters,
other processes or callers bypassing the client. Claims hold the client alive
until explicit cleanup; there is no finalizer that might cause device effects.

Only these named, empty legacy requests are available. Their exact reply shapes
come from the already published [reviewed protocol facts](provenance.md#read-only-mapping-evidence),
not new captures or recovered implementation code:

| Name | Legacy command | Reply payload | Response field |
|---|---|---|---|
| `read-raw-data` | `00` | 134 bytes | `80` |
| `get-config` | `04` | 108 bytes | `80` |
| `get-power-state` | `0E` | 2 bytes | `80` |
| `get-unit-info` | `1B` | 12 bytes | `80` |

`get-unit-info` additionally requires the literal boolean
`allow_telemetry_state_change=True` **on that request** because it can change
handshake/telemetry state. Other observed getters, arbitrary opcodes/payloads and
setters are excluded. `validate_getter_reply()` generalizes the existing
GetConfig validator; `validate_get_config_reply()` remains available.

Every attempted request consumes a monotonically increasing uint16 sequence.
`first_sequence + limits.max_requests` must fit the sequence space: no wrap,
reuse, retry, reconnect or automatic resume. Stale/duplicate lookup uses the
contiguous sequence offset, not a scan or copy of request history.
A new client and explicit fresh
revalidation are required after either close or failure. Identity change/loss,
short or uncertain writes, clock/deadline failure, adapter contract violations
and exhausted budgets permanently invalidate the session and stop writes.
Invalid caller arguments fail before I/O without invalidating an active session.

`request()` returns immutable `Evidence`, including `stream.raw`, absolute stream
offsets, the raw-consistent `LegacyPacket`, conservative RX timestamps, profile,
confidence and caller-declared evidence kind. It checks complete framing/CRC,
command, sequence, status and exact payload size, then identity/deadline again.
The `matched_candidate` label describes correlation only; the corresponding
`session.requests` entry must have status `matched` for a delivered result.
A candidate followed by an identity/deadline failure is retained but not returned.
Even a delivered match is **not authentication, a physical/application ACK,
firmware identity, calibration, units or physical safety evidence**.

`session.evidence` retains all decoded events, not just the selected reply:
`unsolicited`, `pre_request`, `stale`, `late`, `duplicate`, `request_echo`,
`error_status` (uninterpreted non-80 status), `unexpected_command`,
`unexpected_payload`, `malformed`, `noise`, `partial` and `ambiguous_boundary`
labels distinguish classified rejected/uncertain observations. Labels can
coexist. The initial `unverified` label means **classification has not completed**.
It remains on events retained before a timing/clock failure interrupts
classification; it is replaced by the classification labels when that step runs.
A returned `matched_candidate` therefore does not also carry `unverified`.
Neither the presence nor absence of `unverified` indicates authentication:
`confidence` and `application_acknowledgment` retain the limitations above.
Unknown commands/payloads remain opaque. All events from one read are
processed, including frames after a match. A pre-request partial prefix never
becomes a fresh reply. Decoder corruption flags are sticky; this client also
treats unframed noise as boundary uncertainty for subsequent frames. It never
accepts recovered boundaries or searches inside plausible payloads.

`close()` and failures call decoder `finish()` to retain incomplete tails.
After cleanup, concatenating event raw bytes partitions all accepted RX input.
Snapshots do not consume evidence. Bytes still outside the client in adapter/OS
queues are **not** claimed to have been captured; there is no read/drain on close
or after failure. Adapters must separately preserve any such evidence.

`session.requests` preserves exact outgoing bytes and per-attempt accepted and
uncertain counts. Aggregate `accepted_bytes` means only adapter-reported
acceptance, not physical delivery. On a short write, the accepted prefix is
counted and the remainder conservatively remains uncertain. A write exception or
invalid count leaves the entire attempted request uncertain.

`SessionError.code`, `session.failure` and `session.cleanup_errors` expose failure
details. Diagnostic text is limited to 1024 characters; raw evidence is not
truncated. Cleanup attempts close once and releases process-local claims.
Contractual close errors and close-deadline overruns are reported separately
without replacing an existing primary failure. The post-close deadline is
checked even when the adapter raises. A close-only failure is raised.
Unexpected programming exceptions/interruption invalidate the client;
finalization still attempts cleanup once and propagates the initiating exception,
retaining secondary finalization errors separately. The session is marked
`closed` only after successful finalization. Adapters must report operational
failures as `OSError`.

## Bounded operation

Request timeout is explicit (0.001..120 seconds). Total-session timeout
(0.001..600 seconds) starts before revalidation and includes time between
requests. Every operational adapter call receives the same absolute deadline
for that operation, no later than the session deadline. There is no background
timer: an idle expired session cannot write when next called. Reads returning
at/after the deadline cannot deliver success, even with earlier RX timestamps.
Cleanup has a separate 0.001..30 second grace (default 1 second); if the clock
fails, it uses the last validated time and reports the clock diagnostic.

| `Limits` field | Default | Allowed range |
|---|---|---|
| `max_requests` | 256 | 1..65536, subject to starting sequence |
| `max_rx_bytes` | 65536 | 1..16777216 |
| `max_events` | 8192 | 1..65536 |
| `max_reads` | 4096 | 1..65536 |
| `read_size` | 512 | 1..4096 |

The reused decoder holds at most 4106 bytes, with a 4096-byte payload limit.
Receive size is bounded by remaining byte capacity and a conservative
one-event-per-byte reservation for new input **and pending tails**. This can stop
before the nominal event count is reached; it is not eviction or clamping of
invalid options. A new request requires room for at least one complete expected
reply. Budgets are session-wide, including empty reads; even a frozen injected
clock cannot create an unbounded read loop. Numeric options reject booleans,
nonfinite floats and oversized integers without float-conversion overflow.

Conforming bounded reads are retained in full, including invalid timing or
post-read identity failures. If an adapter violates `max_bytes`, the entire
over-read chunk is rejected before feeding the decoder. The session fails with
`adapter_overread` and records its byte count in `rejected_input_bytes`;
**those raw bytes are not retained**. This explicit contract-breach loss avoids
unbounded allocation by the client. It is not a successful or silent drop.

## Required adapter contract and live boundary

The `Transport` protocol is intentionally stricter than a serial file handle:

| Method | Required behavior |
|---|---|
| `revalidate(deadline=...) -> bytes` | Fresh validation of exclusive external ownership, approved settings and full expected connection identity. Establish an unambiguous RX boundary, not a cached token. |
| `identity(deadline=...) -> bytes` | Current pinned identity, including a connection-generation marker that changes on disappearance/re-enumeration; return 1..1024 immutable bytes or raise `OSError` on loss. |
| `write(data, deadline=...) -> int` | Submit exactly once; return the accepted byte count. No internal retry, reconnect or hidden extra writes. |
| `read(max_bytes, deadline=...) -> Received or None` | Return at most `max_bytes` immutable bytes in stream order with trustworthy, conservative ingestion-time bounds; `None` means no input, not disconnect. |
| `close(deadline=...) -> None` | Bounded release, also after failure; return exactly `None` or report operational errors as `OSError`. Any other return is a cleanup error and invalidates the session. No hidden reset, writes or retry. |

Every method and the clock must return within its bound. Python cannot preempt
a blocking/uncooperative adapter; post-call checks detect overruns but are not a
hard real-time watchdog. Identity tokens are caller/adapter assertions, not
authenticated firmware identity. A new session must revalidate ownership and
establish that old queued/outstanding replies cannot masquerade under a reused
sequence; this code does not claim cross-session sequence uniqueness.

`Received(data, started_at, ended_at)` bounds the **earliest and latest ingress
of all included bytes**, in the injected nondecreasing monotonic clock domain.
Both endpoints must be nondecreasing across reads; conservative ranges can
overlap. Bounds must include upstream queued bytes, not just when `read()`
dequeues them. Stamping queued serial bytes with the current time is invalid.
A frame is eligible only if its first-byte lower bound is **strictly later**
than completed full request submission, its start offset is not in a prior
read, and completion/processing is before the deadline. Equal, uncertain or
during-write timing fails closed; even a legitimate fast reply can be rejected.
Host timing and accepted writes still do not prove electrical transmission or
physical origin. No current live adapter is claimed to meet this contract.

Any future live adapter needs separate review and explicit operator
authorization, not reuse of the one-shot `--run` permission. It must enforce
legacy S/E **57600/8N1/no flow control** and request DTR/RTS low after opening.
`SETTINGS` describes those requirements; it is not proof they were applied.
Low-after-open does not guarantee glitch-free driver transitions. Opening or
closing may affect firmware and discard queued input. GetUnitInfo consent does
not authorize line changes or physical operation.

Routine development and CI must remain offline, with injected transport,
identity and clocks. Physical acceptance requires a separately authorized
operator plan and isolation of motor/servo power **and signals**. The damaged
PEND TXCVR and hub port-4 over-current remain unresolved. Do not flash/reset,
switch power, send motion/servo/LED setters, bypass interlocks or clear latches.
