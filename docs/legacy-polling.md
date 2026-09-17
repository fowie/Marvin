# Bounded legacy polling and evidence

`tools.marvin_legacy_poll` implements #8 under Epic #1 using one
[persistent legacy client](legacy-client.md). This module remains **injection-only**:
there is no device-opening path, background reader or automatic startup hook.
The separate [guarded LIVE CLI](legacy-live.md) provides the production adapter
under independent operator authorization. The existing four-getter one-shot probe policy and modern EFBE
behavior are unchanged. No config, UnitInfo, enabling/heartbeat or actuator
commands are sent; every request is empty legacy ReadRawData (`00`).

## Offline usage

The default invocation validates and prints a plan without creating output or
accessing a transport:

```sh
python -B -m tools.marvin_legacy_poll
```

Create a new private file using the existing **in-memory synthetic** adapter,
then inspect it offline:

```sh
python -B -m tools.marvin_legacy_poll --synthetic --output synthetic-poll.jsonl \
  --interval 1 --max-requests 3 --duration 10 --request-timeout 0.5
python -B -m tools.marvin_legacy_poll --replay synthetic-poll.jsonl
```

The synthetic adapter uses zero-filled payload shapes and a simulated monotonic
clock, not robot observations. UTC anchors in this demo are real host snapshot
times; they do not turn the simulated monotonic values into real elapsed time.
It is not a template for live RX timestamping.

By default replay rejects missing/truncated terminal seals, malformed JSON,
duplicate keys, invalid numerical/schema values, hash/length/count mismatches
and trailing content. Nullable request/evidence timestamps and correlation
fields must still be present. Packet integer declarations require JSON integers,
not equal-valued booleans or floats. `--replay ... --allow-incomplete` explicitly
permits a missing seal or partial final JSON line: it returns available complete records
and exact partial-line hex as **incomplete**, with exit code 2. It never repairs
or overwrites the input and cannot waive a corrupt complete row or invalid seal.
Sealed failed collections also return exit code 2. A sealed completion claim is
reported separately as `sealed_collection_claim_complete`, not an application
ACK or authenticated success.

## Injected API

```python
from tools.marvin_legacy_client_example import SyntheticTransport
from tools.marvin_legacy_poll import PollPlan, collect

transport = SyntheticTransport()

def wait(seconds):
    transport.now += seconds

result = collect(
    transport,
    "new-synthetic-recording.jsonl",
    ownership_key=b"synthetic-poll",
    expected_identity=b"synthetic-example-generation-1",
    plan=PollPlan(interval=1, max_requests=3, duration=10, request_timeout=0.5),
    evidence_kind="synthetic",
    clock=transport.clock,
    wait=wait,
)
assert result.report["status"] == "complete"
```

`collect()` constructs, starts, requests and closes exactly one `LegacyClient`
on the calling thread. Its inert construction validates identity/sequence/
limits before output creation or transport access. `clock`, `wait(seconds)`,
timezone-aware datetime `wall_clock`, and `recorder_factory` are injectable.
Callbacks must be cooperative and bounded; `wait()` returns exactly `None`.
Tests inject every transport/identity/time/output boundary.

Optional `on_sample_persisted(evidence, deadline, finished)` runs only after a
valid sample's request/evidence rows have been appended to the unbuffered journal
and its completion time checked. It receives cumulative immutable evidence and
monotonic operation deadline/completion times. This is not a terminal fsync,
USB-tail or success guarantee. `on_collection_ended(error)` runs before close
and finalization, on success or failure; it does not cover inert constructor
errors, which the live caller handles. These bounded callbacks support the
separate encoder scope's terminal visibility without changing default output or
waiting for operator input. Callback failures stop collection and retain evidence.

Operational faults raise `CollectionError`, preserving the original exception
in `primary` and as the chained cause. `error.result.client` retains all
available cumulative request/evidence snapshots, even if persistence failed.
`error.result.report` separates primary, secondary cleanup/persistence errors,
observed versus persisted counts, accepted/uncertain TX, rejected RX byte counts,
gap reason and forbidden resume. Caller-argument/path validation can raise
`ValueError`/`OSError` before collection. Programming interruption propagates
after finalization rather than becoming a successful result.

## Pacing and bounded resources

The configured interval is a **minimum idle delay after completion of the
previous request and its evidence persistence**, not a fixed-period guarantee.
The reciprocal rate is a software maximum: processing lowers the actual rate.
It is not a physically safe polling rate. One synchronous request is outstanding
at most. One wait is attempted per interval; early/frozen, regressing or invalid
clocks, early/invalid waits, excessive scheduler lateness, and request/batch
overruns stop visibly. There is no busy loop, catch-up burst, retry, reopen,
reconnect, sequence reuse or automatic resume.
If the next slot reaches or exceeds the total duration deadline, collection
stops before waiting for that slot.

| `PollPlan` option | Default | Bound |
|---|---|---|
| `interval` | 1 second | 0.1..60 seconds |
| `max_requests` | 10 | 1..256 |
| `duration` | 30 seconds | 0.001..600 seconds |
| `request_timeout` | 0.5 seconds | 0.001..120, no greater than interval |
| `max_lateness` | 0.05 seconds | 0..1, strictly smaller than interval |
| `cleanup_timeout` | 1 second | 0.001..30 seconds |
| `max_rx_bytes` | 65536 | 144..1048576 |
| `max_events` | 8192 | 144..8192 |
| `max_reads` | 4096 | 1..65536 |
| `read_size` | 512 | 1..4096 |
| `max_output_bytes` | 4194304 | 135168..67108864 |
| `max_records` | 16384 | 2..16384, including header/contexts/terminal |
| `first_sequence` | 0 | 0..65535; must fit all requests without wrap |

Booleans, nonfinite floats and huge integers are rejected normally. The duration
must exceed all nominal request timeouts plus intervening intervals. This is
admission, not a guarantee: revalidation, processing, scheduling and recording
also consume real time. Both collector and client enforce their operational
duration; the client's total deadline includes idle time. A batch taking an
interval or longer stops even if a candidate was returned. The finalization
phase has a separate `cleanup_timeout` budget, including client close and
recording work, checked after calls. The client also enforces its own close
grace. Python cannot preempt an uncooperative adapter, clock, scheduler or
filesystem call: these are post-call bounds, not a real-time watchdog.

Client event capacity conservatively reserves pending tails and can exhaust
before the nominal number of events. The decoder retains at most 4106 bytes;
the client bounds raw bytes, event objects, requests and read calls session-wide.
Recorder rows are serialized individually; there is no second unbounded
collection of raw samples. Cumulative immutable snapshots use positional
checkpoints so evidence is recorded once, with a final snapshot after close.
Offline readers additionally cap input at 64 MiB, records at 16384, row bytes at
131072, requests at 256 and events at 8192, and enforce the file's declared plan.
These are object/data bounds, not an exact Python resident-memory guarantee.

## Exact evidence and failure behavior

The JSONL file contains a header, host timestamp contexts, finalized request
records, every client evidence event, and one terminal report/seal. Each request
preserves its exact ten TX bytes, command/query and sequence, deadline, input
boundary, submission time, accepted/uncertain counts, status and candidate
position. Events preserve exact RX bytes and stream offsets, decoded packet
fields, ingestion-time ranges, profile, evidence kind and the client's
unchanged confidence/labels. The accepted raw stream can be reconstructed by
concatenating event `stream.raw_hex` values in order.

All newly retained events in a complete request/read batch are inspected,
including extra frames **after** the matching frame. Any event other than a
clean `matched_candidate` stops collection: unexpected/error/echo replies,
unsolicited/pre-request/stale/late/duplicate frames, malformed/noise/partial/
ambiguous/unverified evidence remain visible rather than being discarded.
The client may finish its bounded current request before the collector inspects
it, but the collector never sends another request after such evidence.
Close/failure calls decoder `finish()` and the recorder takes a final snapshot,
including incomplete tails. There is no drain: upstream/OS queued bytes were
not captured. An adapter overread's `rejected_input_bytes` is explicit loss;
those raw bytes were not retained by the client.

An event can retain `matched_candidate` while a subsequent identity/deadline
failure prevents delivery. Only its request's final `status == "matched"`
identifies a delivered correlation candidate. Neither state establishes
physical delivery, firmware identity or an application ACK. Host UTC/
monotonic contexts surround snapshot-time clock reads, not individual bytes or
electrical TX. RX ingestion bounds retain the stricter client adapter contract;
wall clocks can move backwards and are not used for scheduling.

## Output integrity and limitations

`new_output_path()` rejects existing destinations, supplied symlink/non-directory
ancestors and kernel-interface paths before transport access. Existing parent
directories are required. The writer repeats the checks, exclusively creates
one file with mode `0600`, and never overwrites, resumes, deletes or truncates a
capture. These are snapshot checks, not a sandbox against hostile ancestor
replacement races. Use trusted local directories and finished replay inputs;
the shared bounded regular-file reader rejects links and nonregular inputs.

Every complete JSON row is encoded before admission against the **actual byte
budget**. Ordinary rows cannot consume the final 131072 bytes reserved for the
terminal; its actual encoding is checked too. Diagnostics are bounded to 16
secondary messages plus primary, 80-character codes and 256-character messages;
additional diagnostics are explicitly counted. The reserve covers worst-case
JSON escaping of these bounded fields, client diagnostics and fixed counters,
not an average per-sample estimate. Record counts include the terminal. A
request can produce more evidence than the remaining ordinary budget: collection
then stops, available raw evidence stays in memory, and persisted/observed
counts explicitly identify incomplete disk evidence. No row is silently
truncated or dropped while claiming complete output.

The terminal SHA256, byte count and record count cover the exact preceding
JSONL bytes, **excluding the terminal record itself**. Replay checks status/count
relationships against the covered prefix, but the terminal claim is outside the
checksum scope. SHA256 is consistency/integrity evidence, not authenticity.
A short/uncertain file write poisons the recorder; no seal is appended afterward.
An fsync failure or interruption also poisons it: direct callers cannot append
more rows or retry sealing after the terminal may already have been written.
Ordinary budget exhaustion before a write can still produce a sealed **failed**
report using the reserve. No final successful API result is returned until client
close, file sealing, fsync, file close and final clock checks succeed.

A terminal cannot attest the outcome of its own subsequent fsync/close or
post-call timing checks. Those failures make the API/CLI fail and are retained in
the in-memory report, even if a completion claim already reached the file.
Replay deliberately describes that file as a **claim**, not proof of successful
finalization or durable storage. Preserve the API outcome alongside the capture.
If the terminal itself cannot be persisted, the existing file remains incomplete;
the original transport/request failure is never replaced by the sealing error.

## Raw interpretation and physical boundary

Offline inspection reuses `marvin_legacy_telemetry.interpret_packet()` for the
exact legacy status-80, 134-byte/82-field profile only. It exposes raw fields,
changed field names, identical-payload runs, raw tick deltas and explicitly
ambiguous wrap/regression/large-gap labels. No inferred units or device clock
rate are added. Tick progression is not watchdog/control-loop timing, and
unchanged data does not establish a stale device, sensor attachment, health,
calibration, active safe limits or physical stop. Battery raw 438 is not volts;
servo reports are not angles. The 27 config words remain possibly-default
reports and are never fetched by polling.

Legacy S/E 57600/8N1/no-flow-control and DTR/RTS low-after-open are **declared
requirements**, not applied hardware facts or a guarantee against line glitches.
The separate LIVE adapter requires review, approved-port checks, explicit
operator authorization, and operator-confirmed motor/servo power **and signal**
isolation. One-shot probe consent cannot authorize polling. No implementation or
merged PR is physical safety sign-off. The damaged PEND TXCVR and hub port-4
over-current remain unresolved; do not use the affected branch.
