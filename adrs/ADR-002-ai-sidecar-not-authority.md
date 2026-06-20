# ADR-002 — AI sidecar, not authority

## Decision

AI may extract datasheet/eval-board information, draft IC profiles, answer datasheet questions, and explain reports. AI may not directly edit KiCad files or make final safety-critical decisions.

## Rationale

Footprint and part selection can cause board respins. Freeform AI can hallucinate. Deterministic rules and approved parts are safer.

## Consequences

- AI outputs must be structured JSON/YAML.
- AI outputs must pass schema validation.
- IC profiles drafted by AI must be human-approved before auto-apply.
