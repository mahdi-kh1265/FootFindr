# FootFindr — Claude Handoff Pack

This repository pack is a handoff specification for **FootFindr** (`ff`), a CLI-first Python package for resolving KiCad schematic symbols into correct footprints, approved part numbers, inventory decisions, BOMs, and supplier carts.

The human user wants a serious tool, not a toy script. The first implementation should be a CLI and Python API. A KiCad GUI plugin may come later, but the engine must work entirely from the terminal.

## Core thesis

FootFindr is **not** an AI that randomly guesses footprints. It is a local part-intelligence and implementation compiler for KiCad:

```text
KiCad schematic + local rules + approved parts DB + supplier/datasheet data + optional IC profiles
    -> selected real parts / implementation variants
    -> correct KiCad Footprint fields
    -> MPN/manufacturer/order fields
    -> inventory/BOM/cart outputs
    -> audit reports and reversible edits
```

The first practical command should feel like:

```bash
ff resolve all --apply
```

After this command runs, FootFindr should directly edit the `.kicad_sch` file by writing the chosen `Footprint` field for each high-confidence symbol, plus optional part fields such as `MPN`, `Manufacturer`, `InternalPN`, `DKPN`, `LCSC`, etc. It should make a backup and write a decision log.

## Why this exists

The user is building KiCad boards and hates manually assigning footprints. KiCad’s built-in footprint assignment flow is slow when there are hundreds of parts. However, choosing the correct footprint is not always a direct string lookup:

- For exact ICs, the MPN/package usually determines the footprint.
- For capacitors, package depends on voltage, rail, dielectric, derating, effective capacitance, stock, and approved parts.
- For resistors, package depends on power/current/voltage constraints.
- For inductors and RF parts, selection depends on current, saturation, DCR, Q, SRF, S-parameters, frequency range, etc.; this should be deferred to extensions.

The tool should eliminate the boring 70–90% of footprint work while forcing review for risky RF/HV/GaN/power/mechanical parts.

## Minimal MVP scope

Do these first:

1. CLI alias `ff` installed by Python package.
2. Read a KiCad `.kicad_sch` file.
3. Parse/represent symbols and fields, especially `Reference`, `Value`, `Footprint`, custom fields.
4. Build enough net context to detect simple rail-to-GND capacitors and rail-to-rail resistors. This can initially use parsed KiCad schematic data and/or `kicad-cli sch export netlist` if available.
5. Load a local YAML or SQLite approved parts database.
6. Resolve exact MPN ICs and exact internal parts.
7. Resolve common rail-to-GND capacitors by value + rail voltage + policy + approved parts.
8. Resolve simple resistors only when exact MPN/internal part is known, when explicit `PowerMin`/`CurrentRMS` fields exist, or when connected between known DC rails.
9. Write selected `Footprint` and part fields back into the schematic.
10. Produce JSON and HTML/Markdown reports.
11. Provide `ff inventory`, `ff bom`, and `ff cart` skeleton commands, even if supplier integrations are mocked initially.

Do **not** do first:

- No KiCad GUI plugin initially.
- No full ERC/DRC requirement.
- No uncontrolled AI direct editing.
- No full RF/power-electronics selection in core.
- No blind live supplier lookups for every run.
- No redistribution of vendor model libraries.

## Safety rules

FootFindr will edit KiCad files, so be careful:

- Default to dry-run unless `--apply` is supplied.
- Create `.footfindr.bak` backup before editing.
- Write `footfindr_decisions.json` for every run.
- Edit only symbol properties/fields, not geometry or wiring.
- Preserve KiCad UUIDs and formatting as much as practical.
- Respect `FootFindrLocked=true` fields.
- Never overwrite an existing non-empty footprint unless `--force` or policy allows it.
- High-risk components should be marked review/block, not auto-applied.

High-risk nets/patterns include: `HV`, `EOM`, `RF`, `CLK`, `GATE`, `SW`, `RESET`, `VPI`, `PIEZO`, `LASER`, `BIAS`, `SENSE`.

## Developer orientation

Read these files next, in order:

1. `docs/01_PROJECT_BRIEF.md`
2. `docs/02_CLI_SPEC.md`
3. `docs/03_ARCHITECTURE.md`
4. `docs/04_RESOLVE_ENGINE.md`
5. `docs/05_DATA_MODEL.md`
6. `docs/06_KICAD_IO.md`
7. `docs/07_INVENTORY_BOM_CART.md`
8. `docs/08_AI_AND_DATASHEETS.md`
9. `docs/09_EXTENSIONS_PWR_RF.md`
10. `docs/10_IMPLEMENTATION_PLAN.md`

Start coding from the `src/footfindr` skeleton. The `schemas/` and `examples/` directories define the expected user-facing config shapes.
