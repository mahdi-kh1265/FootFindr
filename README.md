# FootFindr Handoff Pack

This zip is a specification + starter skeleton for **FootFindr** (`ff`), a CLI-first Python package for KiCad footprint resolution, part intelligence, inventory, BOM, and supplier cart workflows.

Start here:

1. `CLAUDE_START_HERE.md`
2. `docs/01_PROJECT_BRIEF.md`
3. `docs/02_CLI_SPEC.md`
4. `docs/10_IMPLEMENTATION_PLAN.md`

## Core first command target

```bash
ff resolve all --apply --backup
```

This should read a KiCad schematic, select approved parts/footprints for supported components, and write the `Footprint` and part fields back into the schematic.

## High-level scope

- CLI alias: `ff`
- Python package: `footfindr`
- MVP: exact ICs, common capacitors, simple/computable resistors, field writing, reports
- Next: inventory, BOM profiles, carts, datasheet/profile drafting
- Later: KiCad GUI, RF extension, power-electronics library extension

## Directory contents

```text
CLAUDE_START_HERE.md
README.md
docs/               Detailed product/architecture specs
schemas/            Example config/data/export profile YAMLs
examples/           Example workflow files
a drs/              Architecture decision records
src/footfindr/      Minimal starter package skeleton
tests/              Initial test plan and placeholders
```
