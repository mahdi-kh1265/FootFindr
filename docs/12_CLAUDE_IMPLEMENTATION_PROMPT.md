# 12 — Suggested Prompt to Give Claude

Copy/paste this into Claude with the whole zip attached or in the repo.

---

You are helping implement **FootFindr**, a CLI-first Python package for KiCad footprint resolution, part intelligence, inventory, BOMs, and supplier carts.

Read `CLAUDE_START_HERE.md`, then read `docs/01_PROJECT_BRIEF.md`, `docs/02_CLI_SPEC.md`, `docs/03_ARCHITECTURE.md`, `docs/04_RESOLVE_ENGINE.md`, and `docs/10_IMPLEMENTATION_PLAN.md`.

Goal for the first coding pass:

1. Create a working Python package with `ff` CLI alias.
2. Implement `ff init`, `ff doctor`, and minimal config loading.
3. Implement a basic KiCad `.kicad_sch` parser that can read symbol properties (`Reference`, `Value`, `Footprint`, custom fields).
4. Implement field writing for specified reference designators, with backup.
5. Implement loading `approved_parts.yaml` with Pydantic models.
6. Implement exact `InternalPN`/`MPN` resolution to footprint.
7. Implement `ff resolve <targets> --apply` for exact matches only.
8. Add JSON decision log and simple Rich terminal report.
9. Add tests with small fixture schematics.

Do **not** implement supplier APIs or AI first. Stub those interfaces. Do **not** implement a GUI. Do **not** write dangerous/risky components automatically.

Respect these safety requirements:

- Dry-run by default; require `--apply` to edit files.
- Create backups before editing.
- Edit only symbol fields/properties.
- Preserve KiCad UUIDs and wiring.
- Do not overwrite existing non-empty `Footprint` unless `--force`.
- Respect `FootFindrLocked=true`.
- Produce a decision log for every apply.

After first pass, report:

- What works.
- What is stubbed.
- How to run tests.
- Next suggested milestone.

---
