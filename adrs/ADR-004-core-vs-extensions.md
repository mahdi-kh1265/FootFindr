# ADR-004 — Keep RF and power-electronics libraries as extensions

## Decision

Core FootFindr handles exact parts, common capacitors, simple/computable resistors, inventory, BOM, and carts. RF and power-electronics-specific libraries live under extensions/namespaces: `ff rf`, `ff pwrlib`.

## Rationale

RF/power design requires deeper models: S-parameters, Q, SRF, saturation current, DCR, thermal behavior, switching waveforms, etc. Core should stay reliable and approachable.

## Consequences

- Core exposes plugin hooks.
- `ff pwr` remains a calculator/checker in core or near-core.
- `ff pwrlib` and `ff rf` can evolve separately.
