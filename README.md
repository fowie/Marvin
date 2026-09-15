# Marvin

Independently authored Linux tooling and engineering findings for the original
Microsoft Marvin robot. Development and CI are **offline/read-only by default**.

## Offline getting started

From the repository root, with Python 3 available:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests use self-contained protocol facts, reviewed fixtures and mocked transport
boundaries. No robot, USB/serial access, private archive or elevated privileges
are needed. Installing dependencies may require network access; tests are offline.

## Confirmed findings and limits

Eight guarded 10-byte requests covering six legacy `S`/`E` getters
(`00`, `04`, `0C`, `0E`, `1B`, `1D`) returned matching sequence/command,
status `80`, CRC-valid replies: configuration, raw telemetry, diagnostics,
power state, unit information and servo-position readback. This establishes
those exchanges, not physical units, exact firmware identity, actuator safety
or emergency-stop/watchdog behavior.

**Do not substitute successor EFBE commands for legacy S/E commands.**
Opcode meanings collide, including getters versus motion/reset operations.
Existing modern-protocol defaults and behavior are preserved, not silently
switched to legacy. Historical broad-sweep tools remain experimental and are
**not recommended for the known-working legacy device**.

Hardware work requires separate operator authorization and a reviewed physical
test plan. Keep actuator power and signals isolated; software cannot verify that
isolation. Routine development never authorizes firmware programming, resets,
power/configuration writes, motion, servo/LED setters or bypassing interlocks.
The damaged PEND TXCVR USB-A socket and reported hub port-4 over-current remain
unresolved. Keep that branch unused; successful communication does not clear
electrical faults.

## Contributing

Follow [the development and safety rules](AGENTS.md): submit changes through
issue-linked pull requests. **Do not merge or enable auto-merge without the
owner's approval.** Publication acceptance is tracked in
[#6](https://github.com/fowie/Marvin/issues/6), under
[Epic #1](https://github.com/fowie/Marvin/issues/1).

## Documentation

- [Capability map and bring-up plan](docs/marvin-bringup-plan.md)
- [Full historical/modern command catalogue](docs/marvin-command-map.json)
- [Configuration export: 108 bytes, 27 words](docs/marvin-configuration.json)
- [Raw telemetry snapshot: 134 bytes, 82 fields](docs/marvin-telemetry-snapshot.json)
- [Detailed lab findings and historical experiments](docs/lab-findings.md)
- [Publication provenance, evidence citations and exclusions](docs/provenance.md)
- [Generated protocol-fact catalogue](data/protocol-catalog.json)
