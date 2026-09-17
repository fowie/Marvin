# Fixed disconnected-load zero/+1 order diagnostic

**This is software readiness, not live authorization.** Development and CI
must not open the robot tty, USB monitor, sysfs identity, or any hardware
interface. The operator-confirmed setup for any separately reviewed future run
requires both motor POWER plugs disconnected, both encoder feedback harnesses
connected, servos isolated, the robot secured on blocks, and the operator at
the HY1803D cutoff. These declarations do not prove actuator/signal isolation
or physical stop.

The immutable legacy S/E transcript is:

| Sequence | Request | Frame |
|---|---|---|
| 3072 | command `11`, both words zero | `53000c11000400000000009a0145` |
| 3073 | command `11`, left word raw `+1`, right word zero | `53010c1100040001000000ca3845` |
| 3074 | command `11`, both words zero cleanup | `53020c11000400000000003bcb45` |
| 3075 | command `00` ReadRawData | `53030c00000000733745` |

The decoder verifies every frame and CRC before any separately authorized run.
The three setters are submitted once in immediate bounded succession, with no
delay, retry, reconnect, configurable value, duration, sequence, or count. Once
the `+1` write is fully accepted, the cleanup-zero syscall is the next operation
and is attempted exactly once. A write result does not establish controller
application, motor output, or physical stop.

The getter is suppressed unless all three setters receive one unique,
CRC-valid, empty-payload command-`11` response at the matching sequence. Setter
response fields are retained as raw hex; `82` is not decoded or treated as a
known error. This allows comparison of whether zero-first reports `80` and the
second-position nonzero reports `82` without assuming either status's semantics.
The final getter still requires its proven command-`00`, status-`80`, 134-byte
shape. All evidence retains `application_acknowledgment` and `physical_stop` as
`not_established`.

Exact offline dry-run review, without `--run`:

```sh
python3 -B -m tools.marvin_legacy_disconnected_order \
  --disconnected-load-zero-one-order-diagnostic \
  --authorize-unvalidated-zero-one-order-diagnostic \
  --motor-power-plugs-disconnected --servos-isolated \
  --both-encoder-feedback-connected --robot-secured-on-blocks \
  --operator-at-external-cutoff --unprivileged-usbmon
```

The printed plan is not permission to execute on hardware.
