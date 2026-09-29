# Jetson AGX Orin bring-up gates

Use this checklist for a **future operator-led session**. It is not permission
to connect hardware, open a device, record media, or send a controller command.
The [canonical handoff](marvin-handoff.md) holds the historical evidence and
confidence limits. Direct-to-Jetson audio and video are **not verified**.

Stop at a failed or unknown gate. Record the Jetson release, kernel, USB paths,
tool versions, operator, date, and applicable plan revision without publishing
private captures. A successful write, CRC-valid reply, or media file does not
prove physical safety, calibration, firmware identity, or application ACK.

## 1. Prepare the host offline

1. Read the [safety rules](../AGENTS.md) and [handoff](marvin-handoff.md).
   **Pass:** the operator can identify the legacy `S`/`E` profile and its
   collision with newer EF/BE commands. **Hold:** firmware/profile is assumed
   from shared command IDs or reported version words.
2. Run the README's [offline setup](../README.md#offline-getting-started) and
   tests with Marvin devices disconnected. **Pass:** tests pass without device
   access. **Hold:** dependency or test failure; do not work around it with
   live access.
3. Record the exact Jetson/L4T release, `uname -a`, architecture, kernel source,
   config, headers, `Module.symvers`, Secure Boot/module-signing policy, and
   installed `git`, Python, `usbutils`, `udev`, `v4l-utils`, `ffmpeg`,
   `alsa-utils`, and PipeWire versions. **Pass:** build inputs and needed tools
   are identified. **Hold:** missing inputs; arrange a reviewed installation,
   not an automatic install or an x86 module/key copy.

## 2. Review physical connections before inventory

1. Obtain a session-specific physical plan, operator, reviewer, and exclusive
   device ownership. Have the operator confirm power **and signal** isolation
   outside the selected mechanism and an independent cutoff. **Hold:** any
   condition is unknown; software cannot certify isolation.
2. Have the operator identify the controller connection and the *direct*
   Jetson USB connections for the LifeCam and microphone. Any wiring or power
   change needs separate authorization. **Hold:** either media device routes
   through Marvin's internal `0451:2046` full-speed hub, or topology is unknown.
3. After the approved connection, inventory USB identities and parent ancestry
   using metadata only. Record current sysfs paths and the controller's stable
   `/dev/serial/by-id/` selector. **Pass:** exactly one controller `045e:4444`,
   one LifeCam `045e:0721`, and one microphone `045e:fff0` match the intended
   connections; both media devices have direct Jetson ancestry. **Hold:** any
   device is missing, duplicated, or behind an unexpected hub. Do not open a
   serial, PCM, or V4L2 node to resolve a mismatch. Historical paths such as
   `1-1.1.2.4` and laptop `/dev/videoN` are not Jetson selectors.

## 3. Qualify the microphone kernel

1. Inspect the **exact running Jetson kernel** for both upstream DMA fix
   [`d0199ae1666ff9ae2d1d568d64c3430d4c47f0e5`](https://github.com/torvalds/linux/commit/d0199ae1666ff9ae2d1d568d64c3430d4c47f0e5)
   and an exact `USB_ID(0x045e, 0xfff0)` quirk setting
   `UAC_EP_CS_ATTR_FILL_MAX`. **Pass:** both are present in the selected
   module. **Hold:** either is absent or the loaded module cannot be identified.
2. If absent, prepare a separately reviewed build of **both** changes against
   that Jetson kernel's source/config/`Module.symvers`; apply the local signing
   policy. Never enable `FILL_MAX` alone: without the DMA fix, the transfer can
   exceed its buffer and corrupt kernel memory. Do not replace the packaged
   module, copy the old x86 module/key, reload a module, or install an override
   without an approved maintenance plan, including module-dependency updates.
   Recheck both changes after updates.
   See [root cause and rollback](microphone-array-discovery.md#confirmed-root-cause).
3. Verify the selected module path/version/signature and current ALSA card/PCM
   ancestry beneath `045e:fff0` before capture. **Hold:** an override exists
   but is not loaded, its signature is unknown, or ALSA points elsewhere. The
   existing microphone CLI also requires its expected signed override path;
   do not treat an upstream built-in fix alone as proof that this CLI is ready.

## 4. Accept one medium at a time

1. Obtain fresh privacy consent and a private destination before opening PCM.
   Use the [direct-host microphone route](microphone-array-discovery.md#public-offline-default-interface)
   with the **current** USB path and `hw:CARD=Array,DEV=0`. First validate
   bounded direct ALSA `S16_LE`, 8-channel, 16 kHz capture; inspect byte count
   and kernel USB/audio errors. A five-second **raw** capture is 1,280,000 bytes;
   a five-second WAV has an additional 44-byte header. **Pass:** the exact
   format, ancestry, byte count, and clean logs agree. **Hold:** a mismatch,
   fallback device, or partial file. PipeWire is a later, separate check.
2. Obtain separate privacy consent before opening video. Match the current
   `045e:0721` USB ancestry to V4L2 driver/card/bus metadata, excluding onboard
   or unrelated nodes. Use the [fixed LifeCam one-frame path](../README.md#lifecam-capture):
   `MJPG` at `352x288` to a new private file. **Pass:** exact ancestry and one
   complete frame. **Hold:** ambiguity, corruption/truncation, or format change.
   A host/SPARE-hub frame is not Jetson acceptance; do not use `DepthCamPower`.

## 5. Hold controller actuation

1. Before any getter, review the [read-only collector gates](legacy-live.md#operator-and-ownership-gates):
   current `045e:4444` identity, stable selector, permissions, private new
   evidence directory, recorder behavior, physical isolation, and cutoff.
   **Hold:** missing review or a competing owner. Permission failure is not
   a reason to use `sudo`.
2. Only a separately authorized operator may run a bounded legacy `0x00`
   getter baseline. Preserve raw replies and confidence labels. **Pass:**
   correlated transport evidence under the reviewed plan; not physical safety.
3. Keep drive, servo, LED setters, and projector control disabled pending
   separate Jetson-specific physical review. Existing bounded on-blocks
   acceptance does not transfer to a new wiring/host setup. Camera-down and
   projector control remain unsupported.

Transfer reviewed summaries and hashes only; move private evidence only with
operator approval. For later sensor and actuator research, use the
[handoff limitations](marvin-handoff.md#completed-13-sensor-campaign), not an
automatic repeat of historical runs.
