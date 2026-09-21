# Read-only camera interface discovery

This records a powered-off host-side discovery result from September 21, 2026.
With Marvin powered off and external power disconnected, Linux showed no
additional camera interface after the reported connection of Marvin's rear
camera through the robot's internal wiring/controller. This result cannot
determine the camera's powered transport or identity and does not exclude a
USB/UVC or other interface when Marvin is powered.

Discovery used only `lsusb`, `lsusb -t`,
`v4l2-ctl --list-devices`, `v4l2-ctl --info`, `udevadm info`, and
`media-ctl --print-topology`. No frame was captured, no stream was started, no
control was written, no media link was changed, and no robot power or robot
command was used.

## Unrelated host camera interfaces

The existing V4L2 nodes are backed by Intel IPU3 devices on PCI:

| Nodes | Driver / role | PCI device |
|---|---|---|
| `/dev/video0` through `/dev/video9` | `ipu3-imgu` processing nodes | `0000:00:05.0` |
| `/dev/video10` through `/dev/video13` | `ipu3-cio2` capture nodes | `0000:00:14.3` |

These PCI/MIPI interfaces are host cameras, likely built into the laptop. They
must not be attributed to Marvin. `/dev/media0` contains two `ipu3-imgu`
processing pipelines: pipeline 0 links were enabled during discovery and
pipeline 1 links were mostly disabled.

For completeness, `/dev/media1` reported the following unrelated host topology:

| Node | Attached host sensor | Observed source format and state |
|---|---|---|
| `/dev/video10` via `ipu3-csi2 0` | `ov8865 3-0010` | `SBGGR10` 1632x1224; crop bounds 3264x2448; lens `dw9719 3-000c` |
| `/dev/video11` via `ipu3-csi2 1` | `ov5693 2-0036` | `SBGGR10` 2592x1944 at 30 fps |
| `/dev/video12` via `ipu3-csi2 2` | `ov7251 3-0060` | `Y10` monochrome 640x480; link disabled |
| `/dev/video13` via `ipu3-csi2 3` | No attached sensor entity | No sensor path established |

The sensor names, formats, resolutions, and link states do not identify a
Marvin camera and are retained only to prevent these pre-existing nodes from
being mistaken for one.

## Controller evidence boundary

The published legacy S/E evidence establishes a USB CDC serial controller and
camera/projector servo-position words. It contains no established command or
interface that transports image frames. The incompatible successor command
catalog contains Head command `1F DepthCamPower`, a state-changing power setter;
that is not a video transport, is not established for the installed legacy
controller, and must not be sent to it. See
[the command catalog](marvin-command-catalog.md#newer-drive-and-head-command-tables)
and [legacy servo mapping](legacy-servo-mapping.md).

The unresolved blocker is powered interface identity: this powered-off
observation cannot show whether the internally connected camera reaches the
host through a separate USB path, another bus, or a bridge that requires robot
power. Absence from this enumeration is not proof of absence.

At 11:14:41 PDT the kernel reported
`usb 1-1.1.3-port4: over-current condition`. The TUSB2046 hub is at USB path
`1-1.1.3`, with Marvin's controller downstream at `1-1.1.3.3`. Current
evidence does not identify hub port 4 as the camera path, but the fault blocks
further live diagnostics on that branch: do not probe, power-cycle, reset, or
energize it.

The next safe diagnostic is offline connector/controller-path tracing from
reviewed wiring or topology evidence, with all power and USB back-power
removed. Only after the over-current condition is independently resolved and a
new physical plan is approved should an operator consider a read-only
before/after interface inventory during one controlled internal connection.
That inventory must not open a video node, start a stream, change controls or
links, send a robot command, or switch robot power from software. Do not guess
a transport or try the successor power command.

Any later image capture requires separate consent, privacy review, a fixed
identified device and format, short duration, no automatic retry, and private
output outside the repository. This discovery provides no standing
authorization for capture or robot operation.
