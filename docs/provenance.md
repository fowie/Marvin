# Publication provenance and evidence

## Imported material

This repository imports an explicitly selected set of independently authored
Linux tools, tests and engineering findings from a private investigation.
[Lab findings](lab-findings.md) preserve the original investigation README,
with publication redactions, corrected links and historical-context warnings.
The [bring-up plan](marvin-bringup-plan.md), [command map](marvin-command-map.json),
[configuration export](marvin-configuration.json) and
[telemetry snapshot](marvin-telemetry-snapshot.json) retain derived protocol
facts, reviewed packet bytes, source citations and confidence limitations.
The private lab repository's Git history is not imported.

The generated fact catalogue is published at
[`data/protocol-catalog.json`](../data/protocol-catalog.json). It contains
declarative protocol facts, not a translation or serialization of vendor
implementation code. Public tests use self-contained facts, reviewed fixtures,
synthetic inputs and semantic command/payload assertions with mocked hardware
boundaries; they do not read the private reference source tree.

The catalogue is copied verbatim from the sole approved generated reference
file, with SHA-256
`0aa5d95b3ddc16d27ffcbdbf48e8d0f0d1a1a5d1e595596220a5b02ec17fd894`.
Tests pin that public-file hash, compare all 58 command-enum facts and preserve
original source-hash citations. Calibration-selector tests exhaust all 256
single-byte values, admitting exactly `00` and `01` for the source-described
read operation; this does not authorize sending them to legacy hardware.

Quick/full campaign execution segments remain unchanged and hash-pinned.
Publication citation metadata and explicit experimental/not-for-working-legacy
limitations change the complete plan JSON hash, not its execution segments.
The default catalogue now resolves to `data/`; no private runtime file input
is required or added.

## Citation conventions

- `local-evidence:<capture-basename>` identifies a privately retained capture
  directory. `local-evidence:<analysis-basename>.json` identifies its private
  analysis artifact. These are stable evidence IDs, **not repository paths,
  download URLs or files provided by CI**. Capture basenames, useful filenames,
  dates, line citations, hashes and reviewed packet hex are preserved; personal
  home paths and session UUIDs are removed.
- Legacy JSON keys such as `capture.directory`, `parent_evidence_review`,
  `evidence` and `analysis` retain their structure but now carry those IDs,
  not usable filesystem locations.
- Campaign metadata uses the equivalent `local-evidence/<basename>` notation
  and `private-archive/successor-robot/decompiled-reference/...` for original
  source citations. These slash-separated labels are also private evidence
  IDs, not files to resolve inside this repository.
- `private-archive:MarvinFirmwareAndSample.zip` identifies the original supplied
  archive. `PCTestApp/Form1.cs`, `m_inc/...` and `m_src/...` citations in these
  documents are paths **inside that private archive**, not missing public
  repository files. The longer
  `MarvinFirmwareAndSample/v1/Firmware/PCTestApp/Form1.cs` names the same
  sample within its archive hierarchy. Line numbers refer to the cited original.
- References to recovered `Common.Firmware` or the historical
  `reference/successor-robot/README.md` index concern a separate private
  source-reference collection. That tree and index are intentionally unpublished.

The original archive and source evidence are unavailable in public CI.
**Verifying an original source/archive/capture SHA-256 requires access to the
matching private archive or capture.** Public tests can check published facts
and their internal consistency, but cannot authenticate unavailable originals.
Retained hashes are provenance anchors, not a claim that CI re-hashes vendor
files, that a capture has authenticated physical origin, or that the exact
running firmware image is known.

## Read-only mapping evidence

All IDs below use the `local-evidence:` prefix and are private citations:

| Capture basename | Read | Payload |
|---|---|---|
| `marvin-legacy-getconfig-20260914-01` | `04` GetConfig | 108 bytes |
| `marvin-legacy-unit-info-20260914-01` | `1B` GetUnitInfo | 12 bytes |
| `marvin-legacy-power-state-20260914-01` | `0E` GetPowerState | 2 bytes |
| `marvin-legacy-raw-data-20260914-01` | `00` ReadRawData | 134 bytes |
| `marvin-legacy-log-20260914-01` | `0C` GetLog | 32 bytes |
| `marvin-legacy-log-page-2-20260914-01` | `0C` GetLog, same message; then stopped | 32 bytes |
| `marvin-legacy-servo-position-20260914-01` | `1D` GetServoPosition | 4 bytes |
| `marvin-legacy-raw-repeat-20260914-01` | `00` ReadRawData, second snapshot | 134 bytes |

The eight guarded requests total **80 application OUT bytes** and **538 serial
RX bytes**, with matching sequence/command, status `80` and valid legacy CRCs.
USB evidence retains **240 RX prefix bytes**, omitting **298 RX bytes**; the
complete serial responses remain privately retained. These are distinct
evidence limits, not full-payload USB verification.

The earlier confirmed OUT total was 47,767 bytes; the new mapping raises it to
**47,847 bytes**. The historical **13 delivery-uncertain attempted bytes remain
uncertain** and are not added to either confirmed total. No firmware, reset,
power-switching, configuration-write, motion, servo-setter or LED-setter
operation was used in this new mapping.

Correlated replies establish the documented exchanges, not safe physical
behavior. Generic decoder exports deliberately retain
`application_acknowledgment: "not_established"`: the decoder alone does not
correlate requests or authenticate origin. Separate reviewed capture analysis
supports the matched-reply findings in the command map and bring-up plan.
Likewise, historical `live_probe_policy` candidate labels are not blanket
authorization; the separate `live_observations` record what was observed.

## Excluded from publication

- Vendor/recovered implementation source, decompiled implementations and the
  reference source tree, including implementation code serialized into JSON.
- Firmware binaries/images, disk images and private disk contents.
- Raw captures, unreviewed capture material and private analysis artifacts.
  Only reviewed derived packet facts/fixtures and explicit citations are public.
- Credentials, personal files, private machine paths and session identifiers.
- Git history from the private lab repository.

No license choice is assigned by this import. No vendor licensing grant or
permission to redistribute excluded material is implied. Existing source-use
restrictions remain relevant to privately held originals.

Routine development and CI must remain offline/read-only. Historical hardware
examples document separately authorized experiments, not standing permission
to open devices, invoke privileged host setup or operate the robot.
