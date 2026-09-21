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

The narrow conclusion is that USB, ALSA, and PipeWire enumeration and format
metadata are valid, but capture transport currently yields zero frames, with a
direct ALSA I/O error. The cause is unresolved.

The previously investigated, non-actionable
`usb 1-1.1.3-port4: over-current condition` warning is excluded from this
analysis. It could not be mapped to a plug or hub and is not a Marvin blocker;
the microphone is on the distinct path `usb-0000:00:14.0-1.1.2.4`.

## Read-only follow-up

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
its fixed-format and boot-quirk tables contain no exact `045e:fff0`
initialization entry. This is evidence against assuming a known Linux boot
sequence, not proof that undocumented firmware behavior is absent.

## Ranked future diagnostics

Start with non-streaming checks only:

1. Confirm that sysfs path `1-1.1.2.4` still identifies `045e:fff0`, and that
   `/proc/asound/card1/stream0` still reports `Stop` and only the fixed profile
   above.
2. Record `id`, device-node ownership/mode and `getfacl` results. Run
   `fuser -v /dev/snd/pcmC1D0c`, `wpctl status`, `wpctl inspect 99`, and
   `pw-cli info 99` to identify a holder, active link, or node error without
   opening a capture stream. PipeWire serial 504 is dynamic; resolve it again.
3. Review an unfiltered, tightly bounded kernel-journal interval around the
   previous failure for xHCI, USB, isochronous, and `snd-usb-audio` messages.
   Preserve exact timestamps and errors. Also compare `uname -r`, ALSA,
   PipeWire, WirePlumber, and device firmware/descriptor identity before
   looking for an upstream quirk.
4. Search the running kernel's `snd-usb-audio` device table and upstream change
   history for exact identity `045e:fff0`. A nearby Microsoft product or a
   generic synchronous-endpoint workaround is not sufficient evidence to set a
   quirk.
5. Check cached USB power-management state. Runtime suspension is a secondary
   hypothesis only; access should normally resume the device, and no matching
   log evidence has been observed.

Only a failed check justifies a temporary host change. None is currently
needed: the ACL grants access, no holder was found, and the source is managed.
Prefer a per-session correction and restore it immediately. The following
state-changing command families were **not executed**:

| Change | Examples not to run without separate review | Scope |
|---|---|---|
| Device permissions | `setfacl -m u:fowie:rw /dev/snd/controlC1 /dev/snd/pcmC1D0c`; `usermod -aG audio fowie` | ACL is temporary until device recreation; group membership is persistent and requires a new login |
| PipeWire/WirePlumber state | `wpctl set-profile 39 off`; `systemctl --user restart pipewire wireplumber pipewire-pulse` | Current login session; disrupts every audio client and must be restored |
| USB runtime power | writing `on` to the device's `power/control` attribute | Temporary until restored to `auto`; changes device power-management state |
| USB interface ownership | writes to the USB driver's sysfs `unbind` and `bind` attributes | Re-enumerates or reclaims the interface; hardware state changes |
| Kernel driver | `modprobe -r snd_usb_audio`; `modprobe snd_usb_audio` with a `quirk_flags` or device setup override | Host-wide and potentially affects every USB audio device |

Do not use `chmod`, persistent udev rules, group changes, service restarts,
USB resets, driver unbind/rebind, module reloads, or quirk parameters
speculatively. Capture success after an unrecorded state change would not
identify the cause.

After the read-only record and only with fresh, attempt-specific user consent,
the next test should repeat the native hardware profile as raw PCM while
removing both file-container handling and an unbounded wait:

```sh
test -d "$PRIVATE_CAPTURE_DIR" &&
test "$(stat -c %a "$PRIVATE_CAPTURE_DIR")" = 700 &&
test "$(cat /sys/bus/usb/devices/1-1.1.2.4/idVendor):$(cat /sys/bus/usb/devices/1-1.1.2.4/idProduct)" = 045e:fff0 &&
timeout --signal=INT --kill-after=1s 7s \
  arecord --quiet --device=hw:CARD=Array,DEV=0 --file-type=raw \
  --format=S16_LE --rate=16000 --channels=8 --duration=5 \
  "$PRIVATE_CAPTURE_DIR/microphone-array-hw-8ch-16k.raw"
```

This command has not been run. A successful five-second result is exactly
1,280,000 bytes; any other size is incomplete evidence. Capture the tightly
bounded kernel journal separately, then inspect exit status, exact byte/frame
count and final stream status before any next step. Only if native transport
delivers frames would a separately consented `plughw` test usefully isolate
ALSA conversion behavior. Do not retry automatically: every capture requires
fresh consent. Playback, transcription, upload, publication, mixer/control
changes, USB resets, alternate-interface manipulation, HID output, and vendor
USB requests remain outside this procedure.

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
