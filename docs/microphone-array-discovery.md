# Rear/top microphone array discovery

This records read-only discovery from September 20, 2026. The operator
identifies this as the rear/top microphone-array portion of Marvin's media
interface. USB metadata does not establish its physical placement.

No audio was recorded. No PCM device or serial endpoint was opened, no mixer
state was changed, no USB interface was claimed, and no USB request or robot
command was sent.

## USB identity and topology

The newly connected USB 2.0 hub appeared at 21:40:35 PDT. Its downstream port
4 then enumerated at 21:40:37 as:

| Property | Observed value |
|---|---|
| Physical sysfs path | `1-1.1.2.4`, downstream from hub `2109:2817` |
| Identity | `045e:fff0`, Microsoft Corporation, `Microphone Array` |
| Device release / speed | `0.83` / full-speed (12 Mbit/s) |
| Cached descriptor | 143 bytes, SHA-256 `4774df490a5ffae75deb7e72c197bddd145dff6d13d2e8f97a9798e2502aff0e` |
| Configuration | Bus-powered, 500 mA maximum; three interfaces |
| Drivers | `usbhid` and `snd-usb-audio` |

| Interface | Class / driver | Endpoints |
|---|---|---|
| 0 | HID `03/00/00` / `usbhid` | Interrupt IN `0x81`, 1-byte maximum packet, 16 ms interval |
| 1 | AudioControl `01/01/00` / `snd-usb-audio` | None |
| 2, alternate 0 | AudioStreaming `01/02/00` / `snd-usb-audio` | None |
| 2, alternate 1 | AudioStreaming `01/02/00` / `snd-usb-audio` | Isochronous IN `0x82`; synchronous; 512 bytes; 1 ms |

The HID interface's 27-byte cached report descriptor (SHA-256
`3ac888f45cd5b554949db4e52c1e6aa98ba4b419e54dc74130ec5db4ee8a56cc`)
contains one Consumer Control Mute input bit plus constant padding. It has no
HID Output or Feature item.

The audio streaming interface was at alternate setting 0 with no active
endpoint during discovery.

The Marvin controller identity `045e:4444` was not present. A 10-second passive
`/dev/usbmon1` observation at 21:42 PDT saw no target-device events and no
monitor drops. This quiet, idle window does not prove the device never produces
traffic.

## ALSA and PipeWire

Metadata-only enumeration found one ALSA capture PCM:

| Property | Observed value |
|---|---|
| ALSA card / PCM | `Array` / capture device 0 |
| State during inspection | stopped |
| Format | `S16_LE` |
| Channels | 8 |
| Rate | 16000 Hz only |
| ALSA channel map | `FL FR FC LFE RL RR FLC FRC` |
| Mixer controls | none enumerated |

PipeWire exposed the same ALSA card as one eight-channel `Audio/Source`, using
the `analog-surround-71` profile and positions
`FL FR RL RR FC LFE SL SR`. These are host software channel labels; neither
ordering establishes physical capsule positions. PipeWire identifiers recorded
below are observations from that session and are not stable device identities.

The descriptors and host metadata establish a standard USB Audio 1.0,
capture-only interface plus a mute-button HID input. They do not establish beam
forming, acoustic calibration, channel-to-capsule placement, usable signal
level, privacy behavior, or application quality.

## Legacy evidence boundary

The public repository contains no legacy host implementation for this USB
microphone array. Its only source-derived audio command is successor Head
command `11 MicrophoneGain`, accepting one `FloatArg` at
`m_src/m_protocol.c:195`. That private-archive citation is a declarative
protocol fact, not available implementation source. It is a state-changing
successor robot command, is not linked to `045e:fff0`, and must not be sent to
the legacy controller or used as an ALSA mixer substitute.

No new utility is warranted yet. Cached sysfs, ALSA proc metadata, and PipeWire
registry metadata provide the complete non-recording interface inventory.
At discovery time, software that opened PCM would have crossed the consent
boundary without adding offline protocol knowledge.

## Consented capture attempts

On September 21, 2026, the user separately and explicitly consented to each
capture attempt. Before capture, `/proc/asound/card1/stream0` advertised USB
Audio capture interface 2, alternate setting 1: `S16_LE`, 8 channels, endpoint
`0x82` synchronous, 16000 Hz, 16 bits, with channel map
`FL FR FC LFE RL RR FLC FRC`. The endpoint was `hw:Array,0` on
`card 1: Array`.

PipeWire described the source as `Microphone Array Analog Surround 7.1`, with
8 channels, node name
`alsa_input.usb-Microsoft_Corporation_Microphone_Array_0000000000000000-01.analog-surround-71`,
and object serial 504. These values identify the observed host objects; they do
not prove that capture transport works.

| Attempt | Requested capture | Result | Artifact |
|---|---|---|---|
| Direct ALSA | `arecord -D hw:Array,0 -t wav -f S16_LE -r 16000 -c 8 -d 5` | Failed immediately with `pcm_read: read error: Input/output error` | 44-byte WAV header; zero frames |
| PipeWire WAV | WAV capture through the PipeWire source | Failed before stream opening because libsndfile reported the format was not recognized | 0 bytes |
| PipeWire explicit container | Corrected WAV attempt with an explicit container | Failed identically before stream opening | 0 bytes |
| PipeWire raw | Exactly 80,000 frames at 16 kHz, 8 channels, signed 16-bit | Received no frames and did not self-terminate after 30 seconds; explicitly stopped to preserve the bounded-capture requirement | 0-byte raw artifact; no WAV generated |

The exact PipeWire invocations were:

```sh
pw-record --target 504 --rate 16000 --channels 8 --channel-map 'FL,FR,RL,RR,FC,LFE,SL,SR' --format s16 --sample-count 80000 "$out"
pw-record --target 504 --rate 16000 --channels 8 --channel-map 'FL,FR,RL,RR,FC,LFE,SL,SR' --format s16 --container wav --sample-count 80000 "$out"
pw-record --target 504 --rate 16000 --channels 8 --channel-map 'FL,FR,RL,RR,FC,LFE,SL,SR' --format s16 --raw --sample-count 80000 "$raw"
```

`pw-record --list-containers` advertised three `wav` variants and
`--list-formats` advertised `s16`; that makes the pre-stream failure unexpected
but does not turn it into transport evidence.

After the final stop, `/proc/asound/card1/stream0` reported `Stop`. Across all
attempts, no audio content or PCM frames were captured, no playback occurred,
and no mixer or control state was changed. Empty artifacts are not retained in
this repository.

The initial conclusion was that USB, ALSA, and PipeWire enumeration and format
metadata were valid, but capture transport yielded zero frames with a direct
ALSA I/O error. Later USB tracing and a targeted kernel fix identified and
corrected the cause, as recorded below.

The previously investigated, non-actionable
`usb 1-1.1.3-port4: over-current condition` warning is excluded from this
analysis. It could not be mapped to a plug or hub and is not a Marvin blocker;
the microphone is on the distinct path `usb-0000:00:14.0-1.1.2.4`.

## Initial exclusion work

Later read-only host inspection found:

- user `fowie` is not in group `audio`, but both `/dev/snd/controlC1` and
  `/dev/snd/pcmC1D0c` are mode `0660`, owned by `root:audio`, and grant
  `fowie` an effective read/write ACL; the direct ALSA attempt also opened and
  configured the PCM before its read failed, so current evidence does not
  support a permission failure;
- PipeWire 1.6.2 and WirePlumber 0.5.13 were active, WirePlumber owned the
  exported source node, and the node was `suspended` with no source stream or
  process holding `/dev/snd/pcmC1D0c` at inspection time; this rules out a
  current holder, not a transient holder during the attempts;
- the installed userspace versions were alsa-lib 1.2.15.3, alsa-utils 1.2.15.2,
  libsndfile 1.2.2, PipeWire 1.6.2, and WirePlumber 0.5.13, on kernel
  `6.19.8-surface-3`;
- the source had `node.pause-on-idle = "false"`, but a suspended source is
  normal until linked and does not itself explain why the raw recording
  received no frames; and
- the attempt-window kernel log query had no microphone or `snd-usb-audio`
  failure line. Absence from that filtered window is not proof that the USB
  transfer succeeded.

The failures separate into two layers. The two WAV attempts failed in
`libsndfile` before a PipeWire stream opened. That is a file/container-path
failure and cannot explain the direct ALSA `pcm_read` error. Raw mode bypassed
that file-open path, linked far enough to wait for data, and still received no
frames. Despite its option name, `pw-record --sample-count` counts multichannel
frames after they are written. With zero frames its counter never advances and
it supplies no wall-clock bound, which explains the hang without identifying
the transport fault. Current libsndfile accepts 8-channel PCM16 WAV, so that
profile alone does not explain the earlier pre-stream container error.

`hw:Array,0` performs no ALSA conversion. `plughw:Array,0` can convert a
requested format, rate, or channel layout before handing a native stream to the
same kernel PCM. Because the request already exactly matched the sole
advertised hardware profile, `plughw` is a useful differential test but is not
expected to repair missing USB packets.

The single advertised alternate setting is internally consistent: 8 channels
times 16 bits times 16000 frames/second is 256000 bytes/second, within the
endpoint's 512-byte-per-millisecond maximum. A synchronous isochronous IN
endpoint derives its service timing from USB frames; descriptor consistency
does not prove that the device emitted packets or that the host controller and
`snd-usb-audio` accepted them. No alternate capture profile was observed, so
2-channel or 48000 Hz tests against `hw` would not be evidence-based.

No reviewed evidence shows that `045e:fff0` needs a controller initialization
or HID command before audio starts. Its cached HID report descriptor exposes a
mute input and no Output or Feature item, which weighs against HID being the
missing initialization path but does not exclude undocumented vendor USB
control traffic. Do not invent or replay such traffic. Upstream
`snd-usb-audio` applies Microsoft's vendor-wide
`QUIRK_FLAG_GET_SAMPLE_RATE`, which avoids unreliable sample-rate readback, but
its fixed-format and boot-quirk tables had no exact `045e:fff0`
initialization entry. The successful fix required no controller or HID
initialization.

## Confirmed root cause

An authorized usbmon capture showed ALSA submitting 384-byte isochronous reads
to endpoint `0x82`. Completions alternated between per-packet `EOVERFLOW` and
zero-length success. Temporary xHCI dynamic debug then recorded 253
`Babble error` lines and no buffer-overrun lines. This established that the
device's packets exceeded the requested transfer size; the host rejected them
before ALSA could deliver PCM frames.

Permissions, PipeWire ownership and USB autosuspend were separately excluded.
The libsndfile failures remain a distinct pre-stream issue and were not the
cause of the zero-frame transport failure.

The kernel override was built from linux-surface `surface/v6.19.8` commit
`57d61aff0b53b089227f5a794363fec829114fc5`. It combines two changes:

1. Backport upstream commit
   [`d0199ae1666ff9ae2d1d568d64c3430d4c47f0e5`](https://github.com/torvalds/linux/commit/d0199ae1666ff9ae2d1d568d64c3430d4c47f0e5),
   which sets `maxsize = curpacksize` when `fill_max` is active so the DMA
   buffer allocation matches the requested transfer size.
2. Add an exact `USB_ID(0x045e, 0xfff0)` format-attribute quirk that sets
   `UAC_EP_CS_ATTR_FILL_MAX`, causing the endpoint to request its full packet
   size instead of the smaller sample-rate-derived size.

**These changes are inseparable.** Applying the device quirk without the DMA
allocation fix is unsafe: `fill_max` would enlarge the transfer request without
enlarging its buffer, allowing DMA to write beyond the allocation and corrupt
kernel heap memory.

## Installed override and verification

Only the audio module was built against the installed `Module.symvers`, signed
with the enrolled local MOK, and installed as:

```text
/lib/modules/6.19.8-surface-3/updates/marvin/snd-usb-audio.ko
```

The distribution-packaged module remains untouched. No kernel source, module
binary, signing key, captured audio or PCM artifact is committed here.

With the override loaded, the separately consented direct ALSA five-second raw
capture exited 0 and produced exactly 1,280,000 bytes: 16000 frames/second,
8 channels, 2 bytes/sample, 5 seconds. A separately consented PipeWire raw
capture also produced exactly 1,280,000 bytes, with 639,915 of 640,000 channel
samples nonzero. No kernel error was recorded. These results establish working
PCM delivery through both direct ALSA and PipeWire on this host/kernel/module
combination; they do not establish acoustic calibration, channel placement,
privacy behavior or portability to another kernel.

For rollback, remove only the override module, run `depmod`, and reload
`snd-usb-audio` or reboot so the packaged module is selected again. Removal,
module reload and reboot are state-changing operations and require a separately
reviewed maintenance window; do not perform them during capture. After any
kernel update, treat the override as incompatible until its two changes and
build inputs are revalidated. Do not carry only the device quirk forward.

## Public offline-default interface

`tools.marvin_microphone` imports without hardware access and defaults to an
offline status report:

```sh
python -m tools.marvin_microphone
```

The report names the expected override module for the running kernel, reports
whether that path exists, and leaves its signature/loaded image and device
readiness explicitly unchecked. Live operations refuse to proceed when the
override path is absent. The interface uses standard ALSA only.
It never sends recovered successor controller power/enable commands: those
commands are unrelated to this USB microphone, and their opcodes collide with
proved legacy meanings. A live, non-streaming list verifies the exact USB
identity, ALSA metadata and selected endpoint:

```sh
python -m tools.marvin_microphone list \
  --run --device hw:CARD=Array,DEV=0
```

Capture accepts only the proved native `S16_LE`, 16000 Hz, 8-channel profile,
requires a new output path, and is limited to one through five seconds. The
caller must supply the exact byte bound: `duration * 16000 * 8 * 2`, plus 44
bytes for WAV. For example, a five-second WAV is:

```sh
python -m tools.marvin_microphone capture \
  --run --authorize-audio-capture \
  --device hw:CARD=Array,DEV=0 \
  --duration 5 --max-bytes 1280044 --type wav \
  --output /private/microphone-array-5s.wav
```

This command is an example, not authorization to record. Every live capture
requires current user consent and an appropriate private destination. The
command uses an independent seven-second subprocess timeout, rejects partial or
oversized results, never overwrites a path, and writes successful output mode
`0600`. It exposes ALSA errors rather than retrying or falling back to another
device. The signed override containing both the DMA fix and exact device quirk
is a prerequisite.

## Upstream references

- ALSA defines [`hw` as direct kernel PCM access and `plug` as automatic
  conversion](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html).
- The PipeWire recorder documents
  [`--sample-count`, raw mode, target selection, and container
  handling](https://docs.pipewire.org/page_man_pw-cat_1.html); its
  [capture loop counts frames only after a successful
  write](https://github.com/PipeWire/pipewire/blob/27822fcebaf21e3281b46037c88ce9b5538f9ff8/src/tools/pw-cat.c#L1033-L1062).
- Linux derives captured frames from each isochronous packet's actual length in
  [`snd-usb-audio` PCM
  handling](https://github.com/torvalds/linux/blob/93f51579e7df248780214094418f205253383cc5/sound/usb/pcm.c#L1319-L1367).
- The upstream USB-audio
  [quirk table and boot-quirk
  dispatcher](https://github.com/torvalds/linux/blob/93f51579e7df248780214094418f205253383cc5/sound/usb/quirks.c#L1640-L1695)
  contain no exact `045e:fff0` initialization sequence.
- Linux documents
  [USB runtime power management](https://www.kernel.org/doc/html/latest/driver-api/usb/power-management.html);
  changing it remains unjustified without matching status or log evidence.
