# ADR-001 — CLI-first architecture

## Decision

Build FootFindr as a CLI-first Python package with executable alias `ff`. Do not start with KiCad GUI/plugin integration.

## Rationale

- CLI is easier to test and script.
- The user explicitly wants rich CLI access before GUI.
- KiCad GUI/plugin APIs can be version-sensitive.
- Build/BOM/cart/inventory flows are naturally CLI-friendly.

## Consequences

- GUI can later call the same Python API.
- All logic must live in reusable engine modules, not CLI command bodies.
