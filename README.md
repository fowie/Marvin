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

Transmit APIs validate every byte against an explicit profile: `modern` remains
the default, the named legacy wrapper selects `legacy`, and historical campaign
experiments select `experimental-successor`. Authorization flags do not bypass
request-shape checks. The one-shot CLI accepts named requests, not arbitrary hex.
Stateful identification queries require separate telemetry-state acknowledgment.
The legacy `get-unit-info --run` command requires
`--allow-telemetry-state-change`; boot observation separately requires
`--allow-line-state-change` for its DTR/RTS requests and forwards it through
the coordinator to the serial capture boundary. Direct serial capture and
coordinator sessions likewise require separate `--allow-line-state-change`
consent whenever DTR or RTS is asserted, before or after opening the port.
Historical campaign execution requires `--allow-line-state-trials`, recorded
and forwarded to each segment. Validated coordinator GetConfig/schedule
`allow_line_state_trial=True` consent also authorizes the serial line request.
Generic consent never waives named-query line/framing restrictions. Low/low
defaults, including the fixed legacy wrapper settings, remain usable without
fabricated consent. Neither acknowledgment widens a transmit profile.
Safety acknowledgments and boolean selectors reject strings and integers rather
than interpreting their truthiness as consent.

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
- [Persistent legacy getter client and offline API example](docs/legacy-client.md)
- [Bounded read-only polling, recording and offline inspection](docs/legacy-polling.md)
- [Full historical/modern command catalogue](docs/marvin-command-map.json)
- [Configuration export: 108 bytes, 27 words](docs/marvin-configuration.json)
- [Raw telemetry snapshot: 134 bytes, 82 fields](docs/marvin-telemetry-snapshot.json)
- [Detailed lab findings and historical experiments](docs/lab-findings.md)
- [Publication provenance, evidence citations and exclusions](docs/provenance.md)
- [Generated protocol-fact catalogue](data/protocol-catalog.json)
