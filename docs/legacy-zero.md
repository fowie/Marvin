# Isolated, one-shot zero-velocity characterization

The separate [motor-power-OFF preparation](legacy-motor-power-off-prep.md)
profile must be used for its reviewed MotorL-attached topology. It does not
reuse this tool's full-isolation declaration or sequence. This isolated
diagnostic's one-write, opaque-response behavior remains unchanged.

This diagnostic advances the **isolated-interface** prerequisite of manual
#11. It is not a powered-wheel trial, validated motor-stop API, commissioning
step or permission to change wiring/power. The installed setter's response and
physical behavior are unvalidated. Keep motor power, motor signals, servo power
and servo signals disconnected under a current operator-confirmed plan.

The authored command map describes legacy `11` as left/right little-endian
signed 16-bit velocity values. This tool admits exactly one immutable transcript:

| Field | Fixed value |
|---|---|
| Legacy profile / sequence | S/E / 1024 |
| Command / outgoing response field | `11` / `00` |
| Payload | `00 00 00 00` (two numeric zeros, not calibrated physical velocities) |
| Entire frame, including CRC/footer | `5300041100040000000000fdc145` |
| Application TX budget | **14 bytes, one syscall, one attempt** |

There are no sequence, opcode, payload, nonzero-value, retry or timing knobs.
No automatic trailing stop, heartbeat, UnitInfo, power/servo/reset command or
reopen is performed. Repeating the tool requires a separately reviewed decision;
the fixed sequence must not be casually reused after an actual submission.

## Offline default and explicit live gates

```sh
python3 -B -m tools.marvin_legacy_zero
```

Default invocation prints the exact transcript, bounds and consent requirements
without preflight, file creation, device access or transmission. Only after
review and explicit hardware-ownership release may an operator authorize:

```sh
python3 -B -m tools.marvin_legacy_zero \
  --output /trusted/private/existing-parent/NEW-ZERO-CAPTURE \
  --expected-physical-port 1-1.1.3.3 \
  --actuators-isolated --authorize-unvalidated-zero-velocity \
  --unprivileged-usbmon --run
```

This example is not standing permission. The three API consent values must be
literal `True`, not truthy strings/integers. The coordinator remains ordinary
user; there is no sudo/permission fallback. The current physical port, USB/tty
generation, descriptors, udev exclusions and visible exclusive ownership are
revalidated before opening. USB recording starts first. Raw unflushed tty
configuration is fixed to 57600/8N1, no flow control, requested low DTR/RTS,
with exact readback checks as described in [LIVE collection](legacy-live.md).
Kernel open/close line transients and first-open echo remain possible.

## Narrow code boundary

The new `_ZeroTransport.write` accepts only the single fixed transcript and
rejects any second call. It reuses the existing internal exact-once submission,
identity, raw recording and same-owner close mechanics. Public
`LiveTransport.write`, `LegacyClient`, getter encoders, polling and
`marvin_tx_policy` remain getter-only and reject this setter. The existing
one-shot probe does not gain a setter option.

An internal coordinator mode records the diagnostic's real 14-byte transcript
and requires this isolated legacy configuration and bounded recording settings.
It cannot be selected by the existing session CLI and is not a general command
or profile bypass. The diagnostic exposes no arbitrary-frame transport API.
These Python internal boundaries are not a sandbox against dishonest callers
or malicious subclass/constant replacement.

## Observation, bounds and evidence

The adapter first observes a one-second post-configuration quiet window.
Any queued/prewrite serial input, unconsumed USB input or unexpected USB OUT
suppresses submission. Only then is the exact frame submitted once. A short
write or exception leaves its accepted/uncertain accounting explicit and stops;
there is no suffix resend or fallback zero.

After complete submission, the tool observes for at most **three seconds**
within a 15-second operational budget. It continues observing after the first
candidate to retain/reject extra response data. Limits are 8192 serial RX bytes,
512 bytes per read, 4096 observation iterations, 256 decoded response events,
262144 bytes of adapter journal, 4096 bytes per binary USB payload and 1 MiB /
10000 target USB records. Cleanup has a separate five-second bound. Existing
USB recording uses a 20-second nominal window and **25-second hard limit from
recorder readiness**, preserving close and tail events. Bounds are cooperative/
post-call checks, not preemption of blocked kernel or filesystem operations.

`capture/serial/adapter.jsonl` retains every dequeued raw chunk before parsing/
USB correlation, exact attempted TX and accepted count, raw requested/returned
termios arrays, open/close and decoded response events. Full binary USB evidence
retains ingress bytes, OUT completion and line transitions. Timing uses the
existing conservative USB-completion-to-monotonic bridge with its clock-change
guard; queued tty input is not falsely assigned a dequeue-time lower bound.

Unlike getter collection, **no expected reply payload size is supplied**.
Payloads remain opaque. A frame with valid framing/CRC, matching command and
sequence, response field `80`, and conservative strictly-postsubmission,
pre-deadline ingress can be labeled `correlated_command_sequence_only`.
It still carries **`unverified_shape_and_semantics`**, with application
acknowledgment and physical stop both `not_established`. This is not the
getter's `matched_candidate` or a 134-byte telemetry interpretation.

Malformed/noise/partial input, prewrite/ambiguous timing, wrong command/sequence,
additional frames, non-80 status, deadlines, identity/settings changes, USB loss
or byte mismatch, recorder failure and incomplete evidence fail closed.
Non-80 status is retained as `uninterpreted_non80_status`, not assigned an
invented device error meaning. All events in an accepted read batch are
retained before reporting classification failure. Partial tails are finalized
on failure/close; prewrite data remains in the separate raw adapter/USB record.
No response yields explicit `response_not_observed`, not successful stop.

Root metadata records the complete observation report and exact attempt
accounting; a refusal before submission reports zero application submission
attempts. On success, the root status is only
`observation_complete_unverified`, pending parent/operator review. Later
unmatched USB input/OUT, dropped/queued events, tail/close or sealing failure
invalidates the outer outcome even if an earlier frame correlated.
The private manifest covers the final files. No raw capture is published.

## Physical boundary and next hold

Even a valid correlated reply cannot show that a velocity target was applied,
which physical wheel a channel controls, whether zero means brake/coast/hold,
whether stored motor output changed, or whether actuator energy is removed.
No physical motor stop can be verified with the motor power and signals
isolated. Existing telemetry's raw reverse PWM `100` and zero velocities remain
insufficient evidence.

The operator has separately described an accessible TekPower HY1803D external
supply at 12 V DC / 1 A current limit as the sole motor-energy source, with
physical off/disconnect effective even while USB is connected. These are
operator declarations, not measurements or authorization to energize. An
appropriate existing cutoff can support a reviewed motor-only abort method;
new disconnect hardware is not inherently required. Robot elevation on blocks
is not measured restraint or stopping performance. Servo energization is not
authorized.

After this one observation, close all handles and return the actual raw result
for review. No additional command, wiring/power change, powered trial, issue
closure or relaxed safety gate follows automatically.

## First authorized invocation: preflight refusal

On September 16, 2026 at approximately 00:06 UTC (September 15 local time), the
parent released exactly one isolated diagnostic invocation at
`466e06316a2a05b168a55675d0b536e6357e3b84`, following the operator's renewed
four-path isolation confirmation. The command failed during cached udev
preflight: lookup of the approved by-id serial path returned exit status 1.

The refusal occurred **before any tty open, USB recorder launch, application
submission or capture-directory creation**. Subsequent cached-only exact-path
checks at 00:06:53.421320 UTC found the expected by-id link, `ttyACM0` and the
previously reviewed USB sysfs path `1-1.1.3.3` all absent. This records host path
absence; it does not identify the physical cause or authorize changing cables,
power or ports.
At 00:07:47.763384-00:07:47.767005 UTC, a second cached-only check confirmed
the by-id link still had no target and `/sys/bus/usb/devices/1-1.1.3.3` was
absent. The exact cached udev query again returned 1 with `No such device`
and empty stdout. No matching `045e:4444` identity was available at the pinned
port. This is more than a single transient udev-query failure, but still does
not distinguish cable, power, hub, enumeration or other physical causes.

No zero request was sent, sequence 1024 remains unsubmitted, and there is no
installed zero-command response or stop evidence. No USB trace was opened, so
there is no wire-capture claim about other host USB activity. No handle created
by this diagnostic remained open, and no retry or other command was attempted.
Private audit ID: `zero-1024-preflight-refusal`. The planned capture directory
was not created; the audit is not a capture or synthetic replacement for one.

**Hold:** the parent/operator must review the absent expected connection.
Any subsequent invocation requires fresh explicit release and all normal
preflight/isolation/ownership guards. Powered motion and physical-stop claims
remain out of scope.

## Separately released observation after operator USB return

After the operator reported unplugging/reconnecting Marvin and requested
continuation, the parent separately released one new invocation at
`c26cadcfd17745f38641ae8edce0b690a3f42880` (unchanged diagnostic implementation
`466e06316a2a05b168a55675d0b536e6357e3b84`). The last explicit operator statement
was that all four actuator power/signal paths remained disconnected; no
actuator reconnection was requested or reported. This was not an automatic
retry. The earlier refusal had submitted no request.

The real observation ran on September 16, 2026, 01:07:36.384578 through
01:07:56.802256 UTC. Cached preflight at 01:07:12.498738-01:07:12.610920 UTC,
the fresh run baseline and recorder identity, and post-close cached preflight
at 01:08:05.195676 UTC agreed on:

| Identity field | Observed value |
|---|---|
| Device / physical port | `045e:4444` / `1-1.1.3.3` |
| USB generation | Bus 1, device 14; sysfs device 25, inode 67806 |
| Serial node / rdev | `/dev/ttyACM0` / 42496, through the approved by-id selector |
| Cached descriptors | 71 bytes; SHA256 `7c0df726b51216f29f11f0d078f4673596f3c50c675c9a0419c1316d5446419b` |

The previous successful read-only generation was bus 1/device 10, inode 65032;
the intervening refusal observed no device at the pinned port. No continuous
presence observer covered this later operator reconnect, so its disappearance/
return timestamps and intermediate generations are not established.

### Exact request and opaque response

Exactly **one sequence-1024 request** was submitted, with 14 accepted bytes,
zero uncertain bytes and one matching successful 14-byte USB bulk-OUT
completion. Independent offline parsing of the complete binary USB records
matched the request to the serial write journal. There was no second request,
getter, trailing zero or other application OUT.

Exactly one nonempty USB bulk-IN completion contained the same complete
10 bytes as the sole serial RX chunk:

| Field | Observed value |
|---|---|
| Request | `5300041100040000000000fdc145` |
| Response | `53000411800000961145` |
| Response sequence / command / response field | 1024 / `11` / `80` |
| Response payload | Empty, declared length 0 |
| Response CRC | `1196`, stored little-endian as `96 11`; framing/CRC valid |
| Classification | `unverified_shape_and_semantics`, `correlated_command_sequence_only` |

This is **command/sequence correlation only**, not an established application
ACK, applied zero target, zero motor output, validated stop command or physical
stop. The reply has no telemetry fields; the getter's 134-byte layout was not
applied. The observation does not validate nonzero values, physical channel
mapping, units, brake/coast behavior or watchdog behavior.

### Timing, close and evidence health

The prewrite quiet interval after completed tty configuration was
1.005706305 seconds. Conservative USB-completion ingress bounds were
131.766-134.044 microseconds after the host submission marker, not a measured
controller execution time. Close began 3.000868208 seconds after that marker,
after the full three-second receive window, and completed in 0.034601712
seconds. The journal records one open and one close with no cleanup errors.

The recorder stopped normally by coordinator request 20.197632136 seconds
after readiness, before its 25-second hard deadline, retaining
14.888707921 seconds after completed tty close. All 44 records paired into
22 transfers, without unmatched transfers, dropped/queued records, partial
records or omitted payload bytes. The 17 zero-byte cancellation completions
remain close/cancellation evidence, not command errors or responses.
CDC line requests were again **3 -> 0** despite requested low DTR/RTS; there
is no glitch-free-line claim. Post-close cached preflight found no visible
conflicting tty owner; this retains the usual other-user/root visibility
limitation.

All 24 entries across the nested evidence manifests were independently
rehashed. Private audit ID: `isolated-zero-1024-after-return`.

| Artifact | SHA256 |
|---|---|
| Root manifest | `3f00214236cf7eff14498c73dcb8ef77282c6399a5072f67d81592ece3106037` |
| Root metadata | `c12deae3de8f9104f738135862bafb5749c3915d457782443a5d646bbd5a5e7c` |
| Adapter journal | `22fb73ed93e60ce70d91e18d3e363ea1c8ce7da6ad406c3d7a1ea379a2edcc53` |
| Full binary USB | `bb8057449769b1d95322fa9b4e9bafeadde534be4c902a973e419ddc5dfcfb4d` |
| Concatenated raw RX | `d4b4e0fcaaa910b5f161397b6a62ae192eca83395a0d57e83d69b3eadbf4bb10` |

The private captures remain unpublished. Sequence 1024 is now submitted and
must not be treated as unused. This observation advances only #11's isolated
interface evidence; #9 readiness, #11 physical stop and #13 commissioning
remain independent, unresolved operator gates.

**Next hold:** no further hardware request is authorized by this result.
A separately released read-only batch at unused sequences 1280-1284 may inspect
raw PWM/velocity fields while all four actuator paths remain isolated, before
any motor-power reconnection. Compare against the prior raw reverse PWM 100,
forward PWM 0 and velocity 0 without inferring physical stop or causation from
a post-command snapshot. No second zero or powered trial follows automatically.

## Separately released post-zero ReadRawData batch

The parent subsequently released exactly one read-only batch at
`7dec580e6ed9bc02cef962e98872428c36f1c403`, with the same operator-declared
four-path isolation and no wiring or power change authorized. It ran on
September 16, 2026, 01:09:48.400513-01:10:08.840957 UTC, using the existing
getter-only LIVE tool, one persistent tty open and sequences **1280-1284**.
No further zero or other setter was sent.

All five CRC-valid status-80 replies were 144 bytes with 134-byte payloads.
The offline authored layout decoded 82 fields per reply. Independent extraction
from the original payload offsets agreed with the telemetry decoder:

| Raw field (payload offset, LE uint16 view) | Prior 512-516 and 768-772, every sample | Post-zero 1280-1284, every sample |
|---|---|---|
| `motorVelocityL` (72), `motorVelocityR` (74) | 0, 0 | 0, 0 |
| `motorPwmLeftForward` (90), `motorPwmRightForward` (94) | 0, 0 | 0, 0 |
| `motorPwmLeftReverse` (92), `motorPwmRightReverse` (96) | 100, 100 | 0, 0 |

Both earlier actual captures were re-decoded for this comparison. The reverse
PWM words changed from raw `64 00` to `00 00`; the other listed words remained
`00 00`. These are source-derived field labels and raw values, not calibrated
velocity, measured PWM pins or motor-energy evidence. In particular, an
intervening USB disconnect/return and the absence of an immediate pre-zero
snapshot prevent assigning this difference solely to the zero request.
**No causal effect, applied-zero confirmation or physical stop is established.**

Independent binary parsing verified exactly five ten-byte ReadRawData OUT
submissions/completions, with request sequences 1280-1284 and no other
application OUT. All 720 received bytes matched across the complete binary
USB payloads, adapter journal and poll evidence. Ten nonempty USB-IN transfers
carried the five replies without omitted bytes. Every conservative ingress
interval was strictly after its host submission and before its deadline.

Write-attempt intervals were 1.024176125, 1.022048610, 1.020430012 and
1.015304154 seconds. The shortest correlated-reply-to-next-write idle interval
was 1.006640351 seconds; initial post-configuration quiet was 1.008738919
seconds. The collection accepted 50 TX bytes, with zero uncertain TX bytes,
no rejected input, no gap/failure and a sealed recording.

The 68 USB records paired into 34 transfers with no unmatched/partial records
or queued/dropped events. Seventeen zero-byte cancellation completions were
retained. Same-owner tty close took 0.035173581 seconds; the recorder retained
13.684503415 seconds after close and stopped normally by coordinator request
20.174346241 seconds after readiness, within its 25-second hard limit.
CDC line requests remained **3 -> 0**. The raw unflushed adapter journal is
authoritative for tty configuration; the nested coordinator's inherited
generic pySerial-flush limitation string does not describe this adapter.

Run baseline, recorder identity and post-close cached preflight at
01:10:18.078181 UTC matched the zero observation's bus 1/device 14, inode
67806, port `1-1.1.3.3`, tty/rdev and 71-byte descriptor hash above.
The post-close visible-owner guard passed. All 26 entries across the nested
manifests were independently rehashed. Private audit ID:
`live-readraw-1280-after-isolated-zero`.

| Artifact | SHA256 |
|---|---|
| Root manifest | `0e44a8e9bc2a6d117f4f1639bd87b2e664c3c0ce69139cb897ef682490322e9d` |
| Root metadata | `7b0ea7abfae6794aac444785ece24d9ecd7b831cd41114289b7663ee5744cab9` |
| Poll journal | `f9b9583c9431fd60030df50d7c5e200ef58d777cefb66c55d4de8901ce008ff2` |
| Adapter journal | `8e4089a33cbc7b21296a7c7fb41bf14f34db7fee6f04cec6dc799878f0e00e47` |
| Full binary USB | `03ed4756cec23faad43d05675835f3af61fe3d6002b0750f08c0919e470f0514` |
| Concatenated raw RX | `ea5f2a0cf5b853928f090df6da5709f47a1e441d01def7d0722544fc0898c8e6` |

**Hardware hold:** all diagnostic handles are closed. Sequences 1280-1284
are consumed. No further getter, setter, reconnect, motor-power wiring or
powered experiment follows from this result; the independent readiness,
physical-stop and commissioning gates remain unresolved.

Any later all-power/USB-off wiring step creates a new controller boot/state
boundary. Do not carry these raw PWM-zero observations forward across it:
the earlier reverse-PWM 100 values remain unresolved initialization/state
behavior. The parent is requesting operator evidence of support/clearance
and disconnected motor connections/supply wiring before considering a new
plan. The accessible HY1803D supply remains an operator-declared motor-energy
abort method, not a measured stop or a requirement to install a new switch.

## Later power-topology clarification

On September 16, 2026 at approximately 05:09 UTC, the parent relayed the
operator's clarification that the HY1803D powers controller electronics as
well as motors and was ON during the successful isolated captures above.
The motor load power/signals were disconnected; the controller board was
powered. Earlier isolation declarations must not be rewritten as claims of
a deenergized board or USB-only controller operation.

This newly clarified shared-supply dependency makes the subsequent
[MotorL-connected, supply-OFF preparation](legacy-motor-power-off-prep.md#operator-declared-shared-supply-topology)
physically inapplicable in the current wiring. Its no-TX refusal is consistent
with absent controller power, not proof of a USB fault. The statement is
operator-declared, not electrically measured. No supply-ON authorization,
guard bypass or change to the earlier raw-byte findings follows.
