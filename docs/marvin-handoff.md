# Marvin canonical handoff

Status checked **2026-09-22**. This is the shortest entry point for a fresh
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
| Motors | Legacy `0x0B SetRawMotorPWM` has bounded on-blocks evidence at raw value `2000`. Named 0.25 s `forward`, `backward`, `rotate-left`, and `rotate-right` profiles exist; continuous dual-forward rotation was observed for about 1 s in the dedicated proof. | Raw PWM is not calibrated speed/torque/distance. Visible stop does not prove cleanup caused it, and none of this authorizes ground driving. See [drive evidence](legacy-disconnected-order.md) and the offline-default `tools.marvin_legacy_drive_step`. |
| Sensors | Legacy `0x00 ReadRawData` repeatedly returned a correlated 134-byte payload. Source-labelled fields include eight proximity words and five cliff words. Cliff words are five LE `uint16` values at payload-relative byte offsets `20..29`. Controlled clear/target/recovery runs cover all eight proximity fields in physical perimeter order. | P5/P9/P11/P13/P4 assignments are decisive within this campaign; P6/P7/P12 are strongly supported but retain cross-coupling caveats. Physical units, firmware internals, health, thresholds, and cliff assignments remain unknown. See [sensor evidence](#sensor-topology-and-mapping-evidence) and [cliff/proximity mapping](cliff-proximity-mapping.md). |
| LEDs | Legacy `0x17/0x19` getters and separately authorized `0x18/0x1A` state/blink setters have protocol and visible-effect evidence. State indices 0-14, 16, and 17 were visibly mapped; index 15 had no visible effect. Wheel blink at index 12 was observed and exact visible baseline restoration was operator-confirmed. | Raw `0x82` remains opaque and visible behavior does not prove application acknowledgement or electrical topology. Reuse the [catalogue evidence](marvin-command-catalog.md#completed-live-interactive-led-mapping); do not remap casually. |
| Tilt servos | Legacy getter `0x1D`, setter `0x1E`; baseline getter `[2500,2730]`. Clean one-degree word-0 and word-1 runs each restored and ended with a matching getter, with no visible movement. | Word assignment and physical restoration remain unproved; projector remains unmapped. See [servo status](#servo-result-and-boundary) and [servo mapping](legacy-servo-mapping.md). |
| Camera | Microsoft LifeCam NX-3000 `045e:0721` worked through the **SPARE/TI hub** path; one valid 352x288 MJPEG frame was captured. | Laptop Intel IPU3 nodes are not Marvin. REAR CAM and DEPTH CAM did not enumerate this camera. Grounding was necessary in one successful SPARE setup but was insufficient on REAR CAM; that is setup evidence, not a universal electrical prescription. Successor `DepthCamPower` is incompatible and must not be used. |
| Microphone | Microsoft microphone array `045e:fff0`; USB Audio 1.0 capture at 8-channel `S16_LE`, 16 kHz. Direct ALSA and PipeWire five-second raw captures each produced the expected 1,280,000 bytes on the modified host. | The working kernel change is host/kernel-specific and must be recreated or found upstream on Jetson. See [microphone host setup](#microphone-host-kernel-state) and [PR #33](https://github.com/fowie/Marvin/pull/33). |

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

## Camera result and boundary

Verified camera identity is the UVC Microsoft LifeCam NX-3000 `045e:0721`, not
the laptop's `/dev/video0..13` Intel IPU3 devices described in closed,
unmerged [PR #32](https://github.com/fowie/Marvin/pull/32). The successful path
was Marvin's SPARE connector through the TI hub, where one 352x288 MJPEG frame
was captured. REAR CAM and DEPTH CAM did not enumerate it. A ground connection
was necessary in the successful setup, but adding ground did not make REAR CAM
work. Treat connector routing/power requirements beyond those observations as
unknown.

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

The only baseline is `[2500,2730]`. With the front AX-12+ as the isolated
connected servo and the projector disconnected:

- the one-degree word-0 and word-1 runs each had clean setter/restore response
  correlation and a matching final getter, but no visible movement;
- the first five-degree word-0 run submitted `[2450,2730]` and began submitting
  the full `[2500,2730]` restore **0.142 ms** later. The raw restore response was
  retained during close, but was not application-correlated, and a now-fixed
  response-key bug prevented the final getter;
- the operator separately heard servo engagement, saw no movement, then powered
  Marvin off and disconnected host USB.

Therefore host submission of setter and restore is established for the
five-degree run; application acknowledgement, physical restoration, and word
assignment are not. Do not retry or issue a recovery command. PR
[#34](https://github.com/fowie/Marvin/pull/34) fixes the response-key bug at
`bddaa7ff466c079cb84093a24c2408e5155eacfa`. The projector remains unmapped.

## Last reported physical state

**Last operator report, not a durable fact:** Marvin was off, host USB was
disconnected, all sensor masks were removed, and actuator power/control paths
remained isolated. The earlier servo work used the front AX-12+ in isolation
with the projector physically disconnected. A future operator must physically
re-verify every condition before relying on it.

## Jetson AGX Orin migration

1. Clone the repository and complete the offline test/read order above.
2. Inventory the exact Jetson kernel, architecture, Jetson/L4T release, Secure
   Boot/signature policy, ALSA/PipeWire stack, installed packages, and udev/group
   permissions. Keep a non-secret package/version record with the handoff.
3. With Marvin unpowered, inventory controller/camera/microphone USB identities,
   stable by-id names, and physical topology without opening endpoints or
   commanding anything. Reconcile `045e:4444`, `045e:0721`, and `045e:fff0`;
   do not mistake IPU3 or other onboard Jetson media nodes for Marvin.
4. Determine whether the microphone fixes are upstream in the exact Jetson
   kernel. If needed, port/rebuild both changes as described above. Never copy
   the x86 module or signing key.
5. Under explicit privacy consent, validate direct ALSA capture before
   PipeWire. Keep media private and bounded.
6. Reproduce a read-only legacy `0x00 ReadRawData` baseline only after exact
   controller identity, stable selector, permissions, recorder behavior,
   isolation, cutoff, and a new evidence directory are reviewed.
7. Keep drive and servo execution disabled until identity/topology and evidence
   recording are validated on Jetson. Do not infer safety from matching bytes.
8. Transfer only non-sensitive summaries/hashes unless the operator explicitly
   moves private evidence.
9. Continue, in order: independently refine the provisional/cross-coupled
   P6/P7/P12 proximity assignments if needed; discriminate cliff channels with
   contemporaneous controls/recovery or a less cross-coupled stimulus; map
   servo words/mechanisms; perform reviewed full-control validation; only then
   begin vision integration.

Do not repeat the completed masked/exposed sensor matrix blindly. Design its
successor from the [campaign limitations above](#completed-13-sensor-campaign),
[PR #28](https://github.com/fowie/Marvin/pull/28), and the
[current plan](cliff-proximity-mapping.md).

## Pull request map

State verified from GitHub on 2026-09-22:

| PR | State | Contribution |
|---|---|---|
| [#25](https://github.com/fowie/Marvin/pull/25) | **Merged** | Legacy diagnostics, LED mapping, and bounded motor evidence |
| [#26](https://github.com/fowie/Marvin/pull/26) | **Merged** | Named bounded raw-PWM drive steps |
| [#28](https://github.com/fowie/Marvin/pull/28) | **Merged** | Cliff/proximity reconciliation and mapping plan |
| [#29](https://github.com/fowie/Marvin/pull/29) | **Merged** | Offline legacy tilt-servo mapping plan |
| [#30](https://github.com/fowie/Marvin/pull/30) | **Merged** | Microphone-array discovery |
| [#33](https://github.com/fowie/Marvin/pull/33) | **Open** | Microphone failure diagnosis, kernel fix, verification, and rollback |
| [#34](https://github.com/fowie/Marvin/pull/34) | **Open** | Bounded front-servo mapper, run evidence, and response-key fix |
