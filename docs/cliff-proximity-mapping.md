# Cliff and proximity mapping

This is an offline evidence plan, not live authorization. The installed
`045e:4444` controller has one authoritative sensor-bearing query: empty legacy
S/E command `00` `ReadRawData`. Repeated correlated replies establish a
134-byte payload. Reviewed PCTestApp host source labels payload offsets `4..18`
as eight proximity words and `20..28` as five cliff words, all little-endian
16-bit values. Those labels and raw values do **not** establish physical
locations, millimetres, ADC units, polarity, calibration, health, or active
safety behavior.

Print the machine-readable plan without touching files or devices:

```sh
python3 -B -m tools.marvin_legacy_sensor_map
```

Reduce an existing sealed `poll.jsonl` offline to only the 13 sensor words,
raw unsigned baseline/previous differences, and changed-channel names:

```sh
python3 -B -m tools.marvin_legacy_sensor_map --recording PRIVATE-CAPTURE/poll.jsonl
```

The reducer deliberately omits file paths, USB identity, timestamps, full
packets, and unrelated telemetry. Preserve the original sealed recording; the
reduction is not independently authenticated.

## Reconciled candidates

| Candidate | Finding | Disposition |
|---|---|---|
| Legacy `00 ReadRawData` | Installed replies; 8 proximity + 5 cliff raw words | **Use** |
| Legacy `04 GetConfig` | Reports source-labelled `cliffStopThreshold=80` and hysteresis `32` | Exclude from sampling; values may be defaults and do not prove an active comparison |
| Legacy `1F GetSensorInfo` | Installed reply was the opaque byte ramp `00..7f` | Exclude; no legacy field layout and newer meanings collide |
| Successor EFBE `03 ReadRawData` | Source labels proximity/cliff fields “InMm” in a 157-byte Drive heartbeat | Exclude; wrong framing/generation for the installed controller |
| Successor EFBE `1D GetSensorInfo` | Source declares 128 bytes | Exclude; wrong generation |
| Historical text `ADC` / `READ` | Neighbouring Eddie-device hypotheses only | Exclude; not Marvin command evidence |
| Bump/bumper/range | No distinct installed legacy getter or decoded field found | Unresolved; do not alias flags, proximity, or opaque bytes |
| Calibration, cliff-reset, bump-enable/reset, heartbeat and power commands | Setters or state changes | Prohibited and unnecessary |

Existing observations show changing raw channels, including cliff values on
both sides of 80, but do not identify sensor placement or prove that 80/32 is
active. Approximately 1 Hz historical polling is a host cadence, not a sensor
rate or safety limit.

## Future controlled mapping procedure

Each run needs a fresh operator authorization and fresh sequence range. Before
power is applied, the operator must confirm: robot secured on stable blocks;
all motor power **and signal** paths isolated; all servo power **and signal**
paths isolated; only the reviewed controller/shared supply energized; exact
USB physical port and exclusive ownership; the damaged PEND/hub branch unused;
and a person stationed at the HY1803D cutoff for the entire powered window.
Software cannot verify these statements.

1. Run the default sensor-plan command above and review the exact getter,
   limits, exclusions, and intended new private evidence directory.
2. With no stimulus, run the existing collector for five samples at a
   deliberately slow two-second interval. Replace `PORT`, `SEQ`, and `NEWDIR`
   only after review:

   ```sh
   python3 -B -m tools.marvin_legacy_live \
     --output NEWDIR --expected-physical-port PORT \
     --actuators-isolated --unprivileged-usbmon \
     --max-requests 5 --interval 2 --duration 15 \
     --first-sequence SEQ --run
   ```

3. Power off and inspect/seal the baseline before continuing. A clean run is
   not permission for the next run.
4. For one candidate physical sensor, repeat under a new authorization,
   directory, and sequence range. Apply exactly one reversible stimulus during
   the middle samples: one matte target moved toward one proximity aperture, or
   one nonreflective drop simulation under one cliff aperture while the robot
   remains immobilized. Do not cover, move, lift, or illuminate any other
   sensor; do not move wheels or reset a latch.
5. Cut power at the planned end or immediately on unexpected motion, output,
   noise, heat, smell, current, USB fault, unknown bytes, or loss of operator
   certainty. Do not retry, reconnect, extend, or continue to another sensor.
6. Reduce the sealed recording offline. Accept a tentative association only
   when one raw channel changes repeatably with stimulus and returns after
   removal across separately authorized trials. Record ambiguous or coupled
   responses as unresolved. Repeat one physical aperture at a time until all
   eight proximity and five cliff source channels are covered.

No physical mapping, units, polarity, useful threshold, or bumper semantics are
authoritative until those controlled observations exist.
