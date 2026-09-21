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
ordering establishes physical capsule positions. Dynamic PipeWire object IDs
and the USB serial string are intentionally not retained here.

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
Software that opens PCM would cross the consent boundary without adding
offline protocol knowledge.

## Consent-gated capture plan

Actual audio capture requires fresh user presence and explicit consent. Before
opening PCM, confirm that no bystander or unintended private source is audible,
agree on a non-sensitive test phrase and retention policy, and re-check that
`045e:fff0` still resolves to ALSA card `Array`, capture device 0, with the
fixed 8-channel `S16_LE` / 16000 Hz shape above.

The first authorized test should be one five-second capture from only
`hw:CARD=Array,DEV=0` into a new mode-`0700`, git-ignored directory. Fix the
format, rate, channel count, duration, and output path; allow no fallback to a
default source and no automatic retry. Record only the agreed phrase, stop on
identity or format mismatch, and retain the raw eight channels without
downmixing or assigning capsule positions. Playback, transcription, upload,
publication, or a second capture each require separate user approval. Record a
hash and tool metadata without committing the audio.

After those checks and explicit consent, the reviewed command is:

```sh
test -d "$PRIVATE_CAPTURE_DIR" &&
test "$(stat -c %a "$PRIVATE_CAPTURE_DIR")" = 700 &&
test "$(cat /sys/bus/usb/devices/1-1.1.2.4/idVendor):$(cat /sys/bus/usb/devices/1-1.1.2.4/idProduct)" = 045e:fff0 &&
timeout --signal=INT --kill-after=1s 7s \
  arecord --quiet --device=hw:CARD=Array,DEV=0 --file-type=wav \
  --format=S16_LE --rate=16000 --channels=8 --duration=5 \
  "$PRIVATE_CAPTURE_DIR/microphone-array-8ch-16k.wav"
```

This command has not been run. The user must choose `PRIVATE_CAPTURE_DIR`
outside the repository and confirm its retention policy before authorization.
