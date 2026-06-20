# ADR-003 — Resolve implementation variants, not just footprints

## Decision

FootFindr resolves a schematic symbol to an implementation variant containing InternalPN, MPN, package, specs, supplier offers, and footprint. It does not merely map symbol names to footprints.

## Rationale

Generic components like `10uF` capacitors and `271` resistors cannot be mapped to footprints from value alone. Package choice depends on voltage/current/power/specs and approved part policy.

## Consequences

- The approved parts database is central.
- Footprint selection is a result of part selection.
- Reports can explain engineering requirements.
