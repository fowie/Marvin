# Working on Marvin

## Safety and scope

- Default to offline or read-only software. Never open robot USB/serial devices,
  issue sudo commands, or run hardware probes in CI or unattended coding/review
  sessions. Mock transport boundaries in tests.
- Physical tests require an explicitly authorized operator/session. Power and
  signal isolation is an operator statement, not something software can prove.
- Do not flash firmware, reset USB/controllers, switch power, send motion/servo/
  LED setters, bypass interlocks, or clear safety latches without separate,
  explicit authorization and a reviewed physical test plan.
- Do not substitute a newer firmware command map for the legacy S/E profile.
  Command IDs collide, including getters versus movement/reset operations.
- Preserve raw bytes and confidence labels. A successful write, a CRC-valid
  frame, or a configuration value does not alone prove physical safety,
  calibration, units, firmware identity or application acknowledgment.

## Publication and development

- Never commit recovered/vendor implementation source, decompiled programs,
  firmware images, disk images, credentials or unreviewed captures. Derived
  protocol facts and reviewed fixtures are allowed with provenance/limitations.
- Do not copy the legacy lab repository's Git history into this repository.
- Preserve existing modern/legacy behavior and safety defaults. Add targeted
  tests for changed behavior; use the existing unittest runner.
- Put code changes in pull requests linked to issues. Do not merge or enable
  auto-merge without the owner's approval.
- Answer actionable review feedback in its existing thread. Explain declined
  or deferred changes rather than silently ignoring them.
