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
| Sensors | Legacy `0x00 ReadRawData` repeatedly returned a correlated 134-byte payload. Source-labelled fields include eight proximity words and five cliff words. Cliff words are five LE `uint16` values at payload-relative byte offsets `20..29`. | Physical apertures, polarity, units, health, and useful thresholds remain unknown. See [cliff/proximity mapping](cliff-proximity-mapping.md). |
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

**Last operator report, not a durable fact:** Marvin was off and host USB was
disconnected. The front AX-12+ was the isolated servo used in the latest work;
the projector was physically disconnected during that testing. A future
operator must physically re-verify all of this before relying on it.

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
9. Continue, in order: physical cliff/proximity mapping; servo word/mechanism
   mapping; reviewed full-control validation; only then vision integration.

The next cliff campaign is one aperture and one reversible stimulus per
separately authorized run, using the existing bounded `0x00` collector and
offline reducer, then power-off review before another channel. Do not duplicate
the full procedure here; follow [PR #28](https://github.com/fowie/Marvin/pull/28)
and [the current plan](cliff-proximity-mapping.md).

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
