# Marvin canonical handoff

Status checked **2026-09-25**. This is the shortest entry point for a fresh
host/session, especially a Jetson AGX Orin installed in Marvin. It separates
verified evidence, operator reports, hypotheses, and unfinished work. Detailed
procedures remain in the linked documents.

## Start here

On a fresh host, clone this repository and establish the offline baseline before
connecting Marvin:

```sh
git clone https://github.com/fowie/Marvin.git
cd Marvin
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -q
```

Read, in order:

1. this handoff;
2. the [offline safety policy](../AGENTS.md);
3. the [capability/bring-up map](marvin-bringup-plan.md);
4. the [command catalogue](marvin-command-catalog.md);
5. the relevant focused document before any sensor, drive, servo, camera, or
   microphone work.

Useful host packages/capabilities are `git`, Python 3 with `venv`/`pip`,
`usbutils` (`lsusb`), `udev`, `v4l-utils`, `ffmpeg`, `alsa-utils`, PipeWire
tools, and the exact running kernel's headers/build/signing toolchain. Package
names vary by distribution. Do not install a prebuilt module from the old
x86 host onto Jetson.

## Current control/status matrix

| Surface | Verified state | Limits / next reference |
|---|---|---|
| Controller | Microsoft Marvin USB `045e:4444`; stable selector `/dev/serial/by-id/usb-Microsoft_Corp_2009_Microsoft_Marvin_12345678-if00`; legacy single-byte `S`/`E` framing works at 57600 8N1 with no flow control. Six getter IDs returned correlated, CRC-valid replies. | Exact firmware image is unknown. Preserve the legacy profile; successor EF/BE IDs collide with getters, motion, reset, and power operations. See [bring-up](marvin-bringup-plan.md) and [client](legacy-client.md). |
| Motors | Legacy `0x0B SetRawMotorPWM` has bounded on-blocks evidence at raw value `2000`. Named `forward`, `backward`, `rotate-left`, and `rotate-right`, the standalone stop request, and supervised teleoperation passed operator acceptance on blocks with sealed evidence. PR #37 adds the offline-first `tools.marvin_legacy_stop` primitive around the already observed all-zero cleanup frame. | Raw PWM is not calibrated speed/torque/distance. The zero frame is a **stop request**: acceptance and visible stopped wheels do not establish causation, braking, de-energization, application acknowledgement, or a general physical-stop guarantee. None of this authorizes ground driving. See [acceptance boundary](#operator-acceptance-and-remaining-boundary), [stop-request boundary](#motor-stop-request-boundary), and [drive evidence](legacy-disconnected-order.md). |
| Sensors | Legacy `0x00 ReadRawData` repeatedly returned a correlated 134-byte payload. Source-labelled fields include eight proximity words and five cliff words. Cliff words are five LE `uint16` values at payload-relative byte offsets `20..29`. Controlled clear/target/recovery runs cover all eight proximity fields in physical perimeter order. | P5/P9/P11/P13/P4 assignments are decisive within this campaign; P6/P7/P12 are strongly supported but retain cross-coupling caveats. Physical units, firmware internals, health, thresholds, and cliff assignments remain unknown. See [sensor evidence](#sensor-topology-and-mapping-evidence) and [cliff/proximity mapping](cliff-proximity-mapping.md). |
| LEDs | Legacy `0x17/0x19` getters and separately authorized `0x18/0x1A` state/blink setters have protocol and visible-effect evidence. State indices 0-14, 16, and 17 were visibly mapped; index 15 had no visible effect. Wheel blink at index 12 was observed and exact visible baseline restoration was operator-confirmed. | Raw `0x82` remains opaque and visible behavior does not prove application acknowledgement or electrical topology. Reuse the [catalogue evidence](marvin-command-catalog.md#completed-live-interactive-led-mapping); do not remap casually. |
| Tilt servos | Legacy getter `0x1D`, setter `0x1E`; selector-free baseline getter `[2500,2730]`. AX Protocol 1.0 capture establishes that word0 drives the J24 AX-12+ (ID 2). A sealed installed-camera `2500 -> 2000 -> restore` run directly produced approximately 5° upward tilt and return. | Decreasing word0 -> camera upward is directly observed at the tested point. Increasing word0 -> downward is inverse inference, not directly exercised. Local scaling supports about 100 legacy units/degree, not precision, full-range linearity, or endpoints; projector tilt/word1 remains unproved. See [servo status](#servo-result-and-boundary), [servo mapping](legacy-servo-mapping.md), and [PR #34](https://github.com/fowie/Marvin/pull/34). |
| Projector | Installed legacy PCTestApp source does **not** map projector power: `0x26` is empty-payload `DisableHeartbeat`, `0x27` is empty-payload `ResetMotorPositions`, and `0x28` is empty-payload `GetBatteryInfo`. A prior one-byte `0x27` ON/OFF experiment used an incompatible source-derived map and produced no observable effect. | Installed projector-power control remains unknown. Do not use mismatched-map `0x27`, `0x2B`, or `0x2C`. Generic legacy `0x0F SetPowerState` exists, but its bit mapping and rail safety are unproved. See [collision correction](#mismatched-map-0x27-collision-experiment) and the [command catalogue](marvin-command-catalog.md). |
| Camera | Microsoft LifeCam NX-3000 `045e:0721` worked through the **SPARE/TI hub** path; one valid 352x288 MJPEG frame was captured. The accepted architecture connects it directly to Jetson rather than Marvin's internal `0451:2046` full-speed hub. | Direct-Jetson topology is **planned, not verified** until Jetson inventory and capture. Require exact USB ancestry and fixed MJPG 352x288 validation. Laptop IPU3 nodes are not Marvin; REAR CAM and DEPTH CAM did not enumerate it. Successor `DepthCamPower` must not be used. |
| Microphone | Microsoft microphone array `045e:fff0`; USB Audio 1.0 capture at 8-channel `S16_LE`, 16 kHz. Direct ALSA and PipeWire five-second raw captures each produced the expected 1,280,000 bytes on the modified host. The accepted architecture connects it directly to Jetson rather than Marvin's internal `0451:2046` full-speed hub. | Direct-Jetson topology is **planned, not verified** until Jetson inventory and capture. The exact Jetson kernel still requires upstream DMA fix `d0199ae` plus the exact `045e:fff0` fill-max quirk unless both are already present. See [microphone host setup](#microphone-host-kernel-state) and [PR #33](https://github.com/fowie/Marvin/pull/33). |

## Operator acceptance and remaining boundary

The operator accepted the following product behaviors on blocks with sealed
evidence: `forward`, `backward`, `rotate-left`, `rotate-right`, stop request,
and supervised teleoperation; camera center/restore and upward tilt; and wheel
LED blink with restoration. This is operator acceptance of the tested bounded
setups, not calibration, ground-drive authorization, an application ACK, or a
general emergency-stop guarantee. Private artifacts remain private.

Projector control remains unsupported. Installed camera downward motion has not
been exercised live; it remains an inverse inference, not an accepted behavior.

## Evidence semantics and safety boundary

- A successful write, USB completion, CRC-valid reply, raw `0x80`/`0x82`, or
  getter agreement proves only the stated transport/protocol observation. It
  does **not** prove physical behavior, safe units, application acknowledgement,
  or physical restoration. Keep operator observations as separate evidence.
- Never flash firmware, reset controllers, change calibration, switch robot
  power from software, clear/bypass interlocks, or try speculative successor
  commands. In particular, do not substitute newer command maps for legacy
  S/E.
- Any physical campaign requires exact USB identity and topology, one bounded
  reviewed command profile, actuator power **and signal** isolation outside the
  named mechanism, a human at an independent energy cutoff, and a new private
  evidence directory. There is no automatic retry, reconnect, recovery command,
  or resume.
- Preserve raw bytes and confidence labels. Transfer only reviewed summaries
  and hashes unless the operator explicitly moves private evidence. Never
  commit captures, audio, images, keys, firmware, recovered source, or secrets.
- The previously investigated
  `usb 1-1.1.3-port4: over-current condition` warning could not be mapped to a
  plug or hub and was explicitly excluded as non-actionable in the microphone
  root-cause work. Do not use it as a Marvin blocker or attribution signal.
  A new, independently identified electrical fault is a different matter.

## Motor stop-request boundary

[PR #37](https://github.com/fowie/Marvin/pull/37) publishes a standalone,
offline-first bounded all-zero raw-PWM stop-request primitive at commit
`b7bf5747e18a0413967e83c6c690a7fb20dca19d`. Its exact fixed frame is:

```text
53550d0b0008000000000000000000311445
```

The frame SHA-256 is
`07feeec91475227dbeab929d8d61fb00f966b0a84bb710fb168b97afa2df7159`.
This is the same cleanup frame serial/USB-confirmed in multiple forward,
reverse, and opposed-direction runs followed by operator-observed stopped
wheels. That supports reuse without another proof run, but does **not**
attribute stopping to the frame or establish braking, de-energization,
application acknowledgement, causation, or physical stop.

## Camera result and boundary

Verified camera identity is the UVC Microsoft LifeCam NX-3000 `045e:0721`, not
the laptop's `/dev/video0..13` Intel IPU3 devices described in closed,
unmerged [PR #32](https://github.com/fowie/Marvin/pull/32). The successful path
was Marvin's SPARE connector through the TI hub, where one 352x288 MJPEG frame
was captured. REAR CAM and DEPTH CAM did not enumerate it. A ground connection
was necessary in the successful setup, but adding ground did not make REAR CAM
work. Treat connector routing/power requirements beyond those observations as
unknown.

For the Jetson architecture, connect this camera directly to Jetson, not
through Marvin's internal `0451:2046` full-speed hub. This is a bus-contention
avoidance decision, not a completed Jetson result. Before capture, verify that
`045e:0721` has the expected direct-Jetson USB parent ancestry and is not an
IPU3 or unrelated node; then admit only fixed MJPG 352x288 for the first
bounded frame. Keep the direct topology **planned/unverified** until that
inventory and capture succeed.

Do not send successor Head `0x1F DepthCamPower`: on the legacy controller the
same numeric area has incompatible meanings, and that setter is neither a video
transport nor evidence for this camera. Future capture needs a freshly matched
`045e:0721` topology, fixed format and duration, explicit privacy consent, no
retry, and private output.

## Completed 13-sensor campaign

The operator physically labelled P1-P13:

| Label | Operator-reported location/orientation |
|---|---|
| P1 | Rear, angled slightly downward |
| P2 / P3 | Left-rear / right-rear cliff |
| P4 | Center-rear, rear-facing proximity |
| P5 / P6 / P7 | Left-side proximity, rear / middle / front |
| P8 / P9 / P10 | Left-front cliff / front-center sensor / right-front cliff |
| P11 / P12 / P13 | Right-side proximity, front / next / third; P13 points directly right |

All actuator power and control paths were operator-reported isolated and the
chassis was supported. The campaign used an all-masked control followed by one
exposed sensor per run. Each run issued five legacy `0x00` getters at two-second
intervals, sequences 4096-4170, with sealed serial evidence, 50 accepted TX
bytes, and zero uncertain TX bytes. Session-private evidence directory names
are `cliff-sensor-baseline-20260922T1830`,
`cliff-sensor-all-masked-20260922T1830`, and
`cliff-sensor-p1-exposed-20260922T1830` through
`cliff-sensor-p13-exposed-20260922T1830`; contents remain private.

The P3, P5, P6, P8, and P9 outer runs failed only final usbmon queued/dropped
accounting. Their sealed serial poll records still contain five matched
134-byte replies with zero TX uncertainty. Nothing was retried. The operator
stopped before a final all-masked recovery run.

**This masked/exposed matrix alone established no sensor-field mapping.**
Proximity fields showed near-monotonic campaign-time/warm-up drift, so those
changes cannot be assigned to physical apertures. Cliff responses were strongly
cross-coupled: P2 strongly affected cliff3, P3 cliff5, P10 cliff1, P8 cliff2
and cliff5, while P1 was weak or ambiguous. These are candidates/confounds,
not mappings. The later controlled perimeter campaign below supersedes that
general proximity conclusion for the eight proximity sensors, with the stated
confidence distinctions. Do not repeat the original matrix blindly.

## Sensor topology and mapping evidence

The proximity and cliff sensors use separate 5 V daisy chains. On the observed
sensor wiring, three-pin black/red/green is ground/5 V/analog output and
two-pin red/black is daisy-chain power. P4 is powered and is the final proximity
sensor in its chain; only its power-out is unused. Continuity from P4's green
output to the board was confirmed. These are observed topology facts, not a
complete schematic or firmware-internal description.

P4 scope observation was approximately 1.5 V clear, 3.5 V with a matte card
about 3 cm away, then 1.5 V after removal. In the separately sealed
`p4-payload-clear`, `p4-payload-target`, and `p4-payload-recovery` sessions
(sequences 4216-4230), historical proximity8 at payload offset 18 changed:

| P4 phase | Five-sample raw result |
|---|---|
| Clear | 412-597, median 545 |
| Target | exactly 1023 in all five replies |
| Recovery | 518-616, median 589 |

This controlled response, recovery, and confirmed signal continuity strongly
map physical P4 center-rear proximity to historical proximity8. Payload offset
26, historical cliff4, also moved; treat that as demonstrated cross-coupling,
not a primary mapping. The P4 waveform photo
`PXL_20260923_033528466.jpg` shows structured periodic ripple at approximately
2.65 V average and 950 mV peak-to-peak rather than ordinary random noise. Its
cause and consequences remain unknown; do not prescribe filtering yet.

P5 scope output was clean: 0 V clear, 3.2 V target, then 0 V recovery. Exclude
the contaminated first baseline at sequences 4231-4235. In the valid sealed
`p5-payload-clear-repeat`, `p5-payload-target`, and `p5-payload-recovery`
sessions (sequences 4236-4250), historical proximity1 at payload offset 4
changed:

| P5 phase | Five-sample raw result |
|---|---|
| Clear repeat | 10-40, median 33 |
| Target | exactly 1023 in all five replies |
| Recovery | 3-47, median 29 |

This strongly maps physical P5 left-side rear proximity to historical
proximity1.

The remaining perimeter campaign completed the ordered proximity field set.
P6's local green output went clear -> 3.4 V with the card -> clear; P7's green
output likewise reached 3.4 V with the target.

| Physical sensor | Payload field / offset | Sequences and raw clear / target / recovery | Confidence |
|---|---|---|---|
| P5 left-side rear | proximity1 / 4 | 4236-4250; 10-40 / exactly 1023 / 3-47 | **Decisive** |
| P6 left-side middle | proximity2 / 6 | 4251-4265; local green clear / 3.4 V card / clear. Proximity2 had late target spikes of 904 and 1023. | **Strong/provisional:** no independently clean single-field response; ordered adjacency supports it. Proximity1 also rose late: 57-143 / 151-756 / 121-227. |
| P7 left-side front | proximity3 / 8 | 4266-4280; 243-278 / 246, 906, 1023, 1023, 1023 / 173-214 | **Strong:** proximity2 also rose from 97 to 778-893 during target, so adjacent cross-coupling remains. |
| P9 front-center | proximity4 / 10 | 4281-4295; 103-246 / exactly 1023 / 225-260 | **Decisive** |
| P11 right-side front | proximity5 / 12 | 4296-4310; 138-347 / exactly 1023 / 165-286 | **Decisive** |
| P12 right-side next | proximity6 / 14 | 4311-4325; 142-296 / exactly 1023 / 192-342 | **Strong:** proximity5 also saturated (250-325 / 1023 / 228-282); P11 independently maps proximity5. |
| P13 right-side third/right-facing | proximity7 / 16 | 4326-4340; 120-453 / exactly 1023 / 178-447 | **Decisive** |
| P4 center-rear/rear-facing | proximity8 / 18 | 4216-4230; 412-597 / exactly 1023 / 518-616 | **Decisive** |

This exact physical perimeter order strengthens the P6, P7, and P12
assignments, but does not erase their delayed or adjacent cross-coupled
responses. Session-private evidence directory names use
`p6-payload-{clear,target,recovery}-*`, with the same pattern for P7, P9, P11,
P12, and P13. Every valid phase above contained five correlated legacy `0x00`
replies, 50 accepted TX bytes, zero uncertain TX bytes, a sealed record, and
usbmon dropped count zero. Only directory names and reviewed summaries belong
here. Raw payload values are not physical units, and these mappings do not
establish firmware implementation, calibration, range, threshold, or sensor
health.

## Microphone host/kernel state

The original host ran kernel `6.19.8-surface-3`. ALSA requested 384-byte
isochronous receives from endpoint `0x82`; completions alternated between
`EOVERFLOW` and zero-length success, while xHCI logged 253 `Babble error`
records. The device can fill the endpoint's larger packet, so the receive
allocation was too small.

The working override combines **both**:

1. upstream DMA-allocation fix
   [`d0199ae1666ff9ae2d1d568d64c3430d4c47f0e5`](https://github.com/torvalds/linux/commit/d0199ae1666ff9ae2d1d568d64c3430d4c47f0e5);
2. an exact `USB_ID(0x045e, 0xfff0)` quirk setting
   `UAC_EP_CS_ATTR_FILL_MAX`.

**Never apply `FILL_MAX` without the DMA fix.** Doing so can let DMA exceed the
allocated buffer and corrupt kernel heap memory. On the old x86 host the
module was built against that exact kernel/`Module.symvers`, signed with the
host's enrolled MOK, and installed at:

```text
/lib/modules/6.19.8-surface-3/updates/marvin/snd-usb-audio.ko
```

That module, its signing key, its kernel build, and its architecture are not
portable to Jetson. On a fresh system:

1. record `uname -a`, architecture, Jetson/L4T release, Secure Boot/module
   signature enforcement, loaded audio stack, `snd_usb_audio` source/version,
   and exact kernel headers/source/config/`Module.symvers`;
2. check whether both `d0199ae` and an exact `045e:fff0` fill-max quirk are
   already present; if not, port both to the exact Jetson kernel and build only
   the affected module with that kernel's toolchain;
3. if signature enforcement is active, sign with a Jetson-owned enrolled key;
   never copy the x86 module or key;
4. install as an override without replacing the packaged module, run the
   distribution's module-dependency update, and verify the selected module
   path/signature/version before capture;
5. with separate audio/privacy consent, validate direct ALSA `hw` capture at
   exactly 8-channel `S16_LE`, 16 kHz before testing PipeWire. A five-second raw
   capture is exactly 1,280,000 bytes; also check kernel logs for USB/audio
   errors. Do not silently fall back to another device or format.

Rollback is high-level and deliberate: remove only the override, rebuild module
dependencies, then reload `snd-usb-audio` or reboot in a reviewed maintenance
window so the packaged module is selected. Revalidate after every kernel
update. Exact diagnosis, verification, and limitations are in
[PR #33](https://github.com/fowie/Marvin/pull/33) and its updated
`docs/microphone-array-discovery.md`.

## Servo result and boundary

The front-camera actuator body is marked **Dynamixel AX-12+**. Its uninterrupted
J24/SERVO2 three-wire harness has green-to-green, red-to-red, and black-to-black
continuity; cross-pairs measured open. BLACK was independently verified as
controller ground. With J24 powered, RED measured 9 V and GREEN 5 V idle-high.
Passive captures identify AX Protocol 1.0 at approximately 100 kbaud.

Startup capture includes checksum-valid packet
`FF FF 02 05 03 22 50 01 82`: ID 2, write, Torque Limit 336. The selector-free
legacy `0x1D` getter returned two unlabeled words `[2500,2730]`. A post-startup
scope observation saw no trigger during that getter, supporting but not proving
that the values are cached.

The decisive passive setter captures are:

| Legacy setter | Captured AX packet | Established command |
|---|---|---|
| word0 `2490` | `FF FF 02 05 03 1E 3E 03 96` | ID 2 Goal Position 830 |
| word0 baseline `2500` | `FF FF 02 05 03 1E 41 03 93` | ID 2 Goal Position 833 |

These establish J24 front-camera ID 2 command routing and observed integer
division by 3 for those two values. They do not establish a general conversion,
AX status/acceptance, execution, physical position, direction, range, or
restoration. The baseline host setter received one correlated CRC-valid,
empty-payload raw-`82` response, but that raw field is not an acknowledgement.
Wire capture proves commands were emitted, not accepted.

Earlier one-degree word0 and word1 runs each restored and ended with a matching
legacy getter but no visible movement. In the earlier five-degree word0 run,
the baseline restore began 0.142 ms after the setter; its raw response was
retained but not application-correlated, and the now-fixed response-key bug
prevented the final getter. Those runs did not establish motion, physical
restoration, or word1 routing.

The final fixed word0 run completed
`[2500,2730] -> [2450,2730] -> [2500,2730] -> verify` with 48 accepted TX bytes,
zero uncertain bytes, and four writes. Baseline and final getters both returned
`[2500,2730]`; setter and restore each had a correlated CRC-valid empty-payload
raw-`82` response, and restore began within **81.007 microseconds** of the
setter. Protocol restore and verification were clean. Passive J24 capture
retained checksum-valid
`FF FF 02 05 03 1E 30 03 A4`: Protocol 1.0 WRITE, ID 2, Goal Position 816,
matching observed `2450 // 3`.

The operator separately observed the servo output move and return, then powered
Marvin off. This establishes bounded word0 servo control and physical
restoration for this run. The actuator was not mechanically connected to a
camera, so this observation does not establish camera tilt or its sign.

A subsequent authorized fixed-direction run held word0 at 2450 for
**0.250105888 seconds**. The operator observed approximately **0.5 degrees** of
servo-output movement and return, then confirmed Marvin physically powered off.
This explicitly corrects the prior 5-degree assumption as a 10x error. Host
evidence recorded 48 accepted bytes, zero uncertain bytes, four write attempts,
a correlated restore, final getter `[2500,2730]`, and setter-to-restore time
**0.267635286 seconds**. The sealed manifest SHA-256 is
`61bf0a5baa9c6cc9fd74bc9a4d54c931a184aca480ab64db19e5ea19ad29c068`.

The sealed fixed 100-unit run then changed word0 from 2500 to 2400. The
operator observed the servo output rotate approximately **1 degree clockwise**
and return, and confirmed Marvin physically powered off. Hold time was
**0.250208296 seconds**; setter-to-restore time was **0.269347429 seconds**.
Host evidence recorded 48 accepted bytes, zero uncertain bytes, four attempts,
a correlated restore, and final getter `[2500,2730]`. The sealed manifest
SHA-256 is
`556b0225ded01a7a0f313a80bb34d077ff60a354f22167887c989315f2ce0499`.

Thus, for this disconnected setup, the operator-observed scaling is
approximately -100 legacy word0 units -> 1 degree clockwise and -50 units ->
0.5 degrees, with return to baseline. These are setup-specific approximations,
not precision calibration or full-range evidence. Because no camera linkage
was installed during those sealed live runs, the runs themselves do not
establish camera up/down tilt sign.

The operator subsequently reports that the camera linkage is now installed and
that clockwise servo-output rotation tilts the camera **upward**. Composing that
reported mechanical mapping with the sealed disconnected-servo observation
establishes **decreasing word0 -> camera upward** for the installed linkage.
**Increasing word0 -> camera downward** is the inverse mechanical inference; it
has not been directly exercised. This composition does not rewrite the earlier
run as a linked-camera test and does not establish precision calibration or
full range.

One later sealed installed-camera fixed run directly changed word0 from 2500 to
2000. The operator observed the installed camera tilt **upward approximately
5 degrees**, return to baseline, and then confirmed Marvin physically powered
off. Host evidence retained 48 accepted and zero uncertain TX bytes across four
submissions. Baseline and final getters both returned `[2500,2730]`; setter and
restore each had one unique correlated empty raw-`82` response. Actual hold was
**0.250516458 seconds** and setter-to-restore start was
**0.269865906 seconds**. Restore correlation and getter re-verification were
true, finalization errors were empty, USB OUT completed all 48 bytes, and
usbmon retained zero dropped, queued, or pending events.

The top-level manifest SHA-256 is
`aa957634552e0d6b581048e17f1e7de35448952296986573e33b26ddef90c235`;
`metadata.json` SHA-256 is
`df01386107730b7d83e1c969a455e19c85d5cd8a8fa8dcd61fef249e436fd019`.
This directly establishes decreasing word0 -> camera upward at the tested
500-unit point and supports local scaling of approximately 100 legacy units per
degree. It does not establish precision, linearity away from the tested local
range, endpoints, or full range. Increasing word0 -> downward remains an
untested inverse inference. See [PR #34](https://github.com/fowie/Marvin/pull/34)
for the evidence directory and detailed procedure.

Projector tilt/word1 remains unproved. PR
[#34](https://github.com/fowie/Marvin/pull/34) contains the evidence directory
`servo2-word0-50unit-20260924T1950`, hashes, and detailed procedure; do not
duplicate private evidence here. Functioning bounded servo control no longer
requires another live test. Full-range, endpoint, or precision-angle characterization
would require a separately authorized larger or optically measured movement
with an operator and independent cutoff. Directly exercising the installed
camera in the inverse direction would also be a separate authorization. This
handoff update performs and authorizes no live action.

## Mismatched-map `0x27` collision experiment

Direct legacy PCTestApp source establishes the installed map as:

- `0x26 DisableHeartbeat`, empty payload (`Form1.cs:637-640`);
- `0x27 ResetMotorPositions`, empty payload (`Form1.cs:853-856`);
- `0x28 GetBatteryInfo`, empty payload (`Form1.cs:925-928`).

The recovered `m_src` meanings `0x27 SetProjectorPower` and `0x2B`/`0x2C`
projector shutter belong to a mismatched controller map and are **not**
installed legacy command semantics. Do not use them live. Installed legacy
projector-power control remains unknown. Legacy `0x0F SetPowerState` accepts a
generic mask, but its bit mapping and rail safety are unproved; it is not a
projector-power substitute.

Before this map correction, one separately authorized experiment sent one-byte
`0x27` values as presumed ON at sequence 3600 for **10.000316396 seconds**, then
presumed OFF at sequence 3601. Host evidence retained 22 accepted and zero
uncertain TX bytes. The first response was one correlated empty raw-`82`; the
second was one correlated empty raw-`81`. Identity was revalidated before the
second write. Both USB OUT transfers completed successfully for all 22 bytes.
Because installed `0x27 ResetMotorPositions` expects an empty payload, this was
a source-derived incompatible/collision experiment, not a projector-power
test.

The recorder nevertheless marked the run failed because final usbmon accounting
reported queued 8, dropped 0. Therefore this is not a clean recorder result,
despite the two completed OUT transfers and correlated responses. Raw `0x82`
and `0x81` are preserved observations, not decoded acknowledgements; they do
not establish handler selection, application acknowledgement, command
execution, motor-position reset, or any projector state.

The operator directly observed **no illumination, fan, LED, click, or other
power sign** during the dedicated ten-second transcript, then physically
powered Marvin off. Separately, the operator reports that the projector
visibly illuminates for approximately 0.5 seconds on every Marvin power-up
before shutting off. That startup observation establishes that the installed
illumination path works, while the experiment provides only direct negative
observable-effect evidence for its incompatible one-byte `0x27` transcript.
It does not identify an installed projector command or justify longer waits,
shutter commands, or any retry. There was no retry. No public operator-branch
document currently provides a sealed evidence path or manifest hash, so none
is invented here.

## Last reported physical state

**Last operator report, not a durable fact:** Marvin was physically off, host USB was
disconnected, all sensor masks were removed, and actuator power/control paths
remained isolated. The earlier sealed servo runs used the front AX-12+ without
camera linkage and with the projector physically disconnected; the operator
later reported installing the camera linkage. A future operator must physically
re-verify every condition before relying on it.

## Jetson AGX Orin migration

1. Clone the repository and complete the offline test/read order above.
2. Inventory the exact Jetson kernel, architecture, Jetson/L4T release, Secure
   Boot/signature policy, ALSA/PipeWire stack, installed packages, and udev/group
   permissions. Keep a non-secret package/version record with the handoff.
3. With Marvin unpowered, inventory controller/camera/microphone USB identities,
   stable by-id names, and physical topology without opening endpoints or
   commanding anything. Reconcile `045e:4444`, `045e:0721`, and `045e:fff0`;
   do not mistake IPU3 or other onboard Jetson media nodes for Marvin. The
   planned architecture places `045e:0721` and `045e:fff0` directly on Jetson,
   not behind Marvin's internal `0451:2046` full-speed hub. Verify their actual
   USB parent ancestry before treating that plan as implemented.
4. Determine whether the microphone fixes are upstream in the exact Jetson
   kernel. If needed, port/rebuild both changes as described above. Never copy
   the x86 module or signing key.
5. Under explicit privacy consent, validate direct ALSA capture before
   PipeWire. Keep media private and bounded. Separately validate the directly
   attached LifeCam with its exact `045e:0721` ancestry and one fixed MJPG
   352x288 frame before exposing a general video path.
6. Reproduce a read-only legacy `0x00 ReadRawData` baseline only after exact
   controller identity, stable selector, permissions, recorder behavior,
   isolation, cutoff, and a new evidence directory are reviewed.
7. Keep drive and servo execution disabled until identity/topology and evidence
   recording are validated on Jetson. Do not infer safety from matching bytes.
8. Transfer only non-sensitive summaries/hashes unless the operator explicitly
   moves private evidence.
9. Preserve the accepted bounded on-blocks drive/stop/teleoperation, camera
   center/up, and wheel-blink profiles without broadening their values or
   evidence claims. Projector control and live camera-down remain unavailable.
10. Continue, in order: independently refine the provisional/cross-coupled
   P6/P7/P12 proximity assignments if needed; discriminate cliff channels with
   contemporaneous controls/recovery or a less cross-coupled stimulus; map
   servo words/mechanisms; perform reviewed full-control validation; only then
   begin vision integration.

Do not repeat the completed masked/exposed sensor matrix blindly. Design its
successor from the [campaign limitations above](#completed-13-sensor-campaign),
[PR #28](https://github.com/fowie/Marvin/pull/28), and the
[current plan](cliff-proximity-mapping.md).

## Pull request map

State verified from GitHub on 2026-09-24:

| PR | State | Contribution |
|---|---|---|
| [#25](https://github.com/fowie/Marvin/pull/25) | **Merged** | Legacy diagnostics, LED mapping, and bounded motor evidence |
| [#26](https://github.com/fowie/Marvin/pull/26) | **Merged** | Named bounded raw-PWM drive steps |
| [#28](https://github.com/fowie/Marvin/pull/28) | **Merged** | Cliff/proximity reconciliation and mapping plan |
| [#29](https://github.com/fowie/Marvin/pull/29) | **Merged** | Offline legacy tilt-servo mapping plan |
| [#30](https://github.com/fowie/Marvin/pull/30) | **Merged** | Microphone-array discovery |
| [#33](https://github.com/fowie/Marvin/pull/33) | **Open** | Microphone failure diagnosis, kernel fix, verification, and rollback |
| [#34](https://github.com/fowie/Marvin/pull/34) | **Open** | Front-camera AX-12+/J24 identification, word0 command mapping, and directly observed bounded camera motion/restoration |
| [#37](https://github.com/fowie/Marvin/pull/37) | **Open** | Standalone bounded all-zero raw-PWM stop-request primitive with explicit physical-effect limits |
