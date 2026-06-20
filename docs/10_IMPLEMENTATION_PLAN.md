# 10 — Implementation Plan for Claude

This is the recommended order. Do not try to build everything at once.

## Milestone 0 — Repo and CLI shell

Goal: installable package with `ff` command.

Tasks:

1. Create `pyproject.toml`.
2. Add `src/footfindr` package.
3. Add Typer CLI with root command `ff`.
4. Add `ff --version`, `ff init`, `ff doctor`.
5. Add config loading from `footfindr.yaml`.
6. Add Rich logging/tables.
7. Add pytest skeleton.

Acceptance:

```bash
pip install -e .
ff --help
ff init --path /tmp/test_ff
ff doctor
```

## Milestone 1 — KiCad field parser/writer

Goal: read and write symbol fields in `.kicad_sch`.

Tasks:

1. Implement S-expression tokenizer/parser or use a small robust dependency if appropriate.
2. Read symbols and their properties.
3. Extract `Reference`, `Value`, `Footprint`, and custom fields.
4. Implement field update/create by reference designator.
5. Preserve schematic file reasonably.
6. Add backup creation.
7. Add golden fixture tests.

Acceptance:

```bash
ff scan examples/simple_board.kicad_sch
ff fields normalize examples/simple_board.kicad_sch --apply --backup
```

## Milestone 2 — Approved parts YAML/DB

Goal: load approved parts and lookup by InternalPN/MPN/value.

Tasks:

1. Define Pydantic schemas.
2. Load `approved_parts.yaml`.
3. Support searching by category/value/package.
4. Support exact InternalPN and MPN lookup.
5. Add unit parsing via Pint.

Acceptance:

```bash
ff part search "10uF 16V X7R 0805"
ff part show CAP-10U-16V-X7R-0805
```

## Milestone 3 — Footprint indexer

Goal: verify selected footprint refs exist.

Tasks:

1. Scan configured `.pretty` directories.
2. Build footprint reference index.
3. Parse basic pad count for `.kicad_mod` when possible.
4. Add `ff lib scan-footprints` command.

Acceptance:

```bash
ff lib scan-footprints ./footprints
ff part verify-footprint CAP-10U-16V-X7R-0805
```

## Milestone 4 — Resolve exact parts

Goal: resolve exact InternalPN/MPN to footprint and write fields.

Tasks:

1. Build `ResolveEngine`.
2. Implement target selector parser.
3. Implement exact InternalPN resolver.
4. Implement exact MPN resolver.
5. Implement dry-run decision report.
6. Implement `--apply` writing.

Acceptance:

```bash
ff resolve U3 --apply --backup
ff why U3
ff undo board.kicad_sch
```

## Milestone 5 — Basic net context

Goal: know component nets.

Tasks:

1. Implement netlist export/parser or direct graph construction.
2. Map component pins to nets.
3. Load rail definitions from config.
4. Identify caps between known rail and GND.
5. Identify resistors between known rails.

Acceptance:

```bash
ff what-is C17
# prints value, nets, recognized rail context
```

## Milestone 6 — Capacitor resolver

Goal: auto-resolve common rail-to-GND capacitors.

Tasks:

1. Parse capacitance values.
2. Compute required voltage from rail and derating.
3. Apply package policy.
4. Search approved parts.
5. Score candidates.
6. Block risky nets.
7. Write selected fields.

Acceptance:

```bash
ff resolve caps --apply --backup
```

A 10uF cap on +5V/GND should select an approved 10uF 16V X7R 0805 part if configured.

## Milestone 7 — Simple resistor resolver

Goal: exact/simple/computable resistors.

Tasks:

1. Parse resistance values.
2. Support explicit `PowerMin`.
3. Support explicit `CurrentRMS`.
4. Compute rail-to-rail power.
5. Apply derating.
6. Search approved resistors.
7. Block risky contexts.

Acceptance:

```bash
ff resolve R17 --apply
ff pwr res R17 --current 1.5A --write
ff resolve R17 --apply
```

## Milestone 8 — Reports

Goal: useful report and diff.

Tasks:

1. JSON decision log.
2. Rich terminal summary.
3. HTML report.
4. `ff diff`.
5. `ff why` / `ff explain`.

Acceptance:

```bash
ff resolve all --report report.html
ff diff board.kicad_sch
ff why C17
```

## Milestone 9 — Inventory/BOM basics

Goal: compile clean BOM and local inventory checks.

Tasks:

1. BOM compiler grouping by InternalPN/MPN.
2. POSM BOM profile.
3. JLCPCB BOM profile skeleton.
4. Local inventory records.
5. Inventory check/shortage.
6. Reservation commands.

Acceptance:

```bash
ff bom fpga-lock --profile posm --csv
ff bom fpga-lock --profile jlcpcb --csv
ff inv check fpga-lock --builds 3
ff inv reserve fpga-lock --builds 3
```

## Milestone 10 — Supplier/datasheet skeleton

Goal: provider interfaces and cache.

Tasks:

1. Define `SupplierProvider` interface.
2. Add mock/local provider.
3. Add DigiKey/Mouser/Nexar config placeholders.
4. Add datasheet cache commands.
5. Do not require live APIs in tests.

Acceptance:

```bash
ff ds add LMH6702 ./LMH6702.pdf
ff stock check CAP-10U-16V-X7R-0805 --offline
```

## Milestone 11 — AI profile drafting skeleton

Goal: structured profile draft, no auto use.

Tasks:

1. Define IC profile schema.
2. Add `ff profile draft` that can create a placeholder draft from datasheet metadata.
3. Add optional AI provider interface.
4. Validate outputs strictly.

Acceptance:

```bash
ff profile draft TPS7A4700
ff profile show TPS7A4700
ff profile approve TPS7A4700
```

## Critical testing strategy

Every resolver must have deterministic tests.

Test cases:

- Exact MPN resolves exact footprint.
- 10uF +5V/GND cap resolves 0805 approved part.
- HV/EOM cap blocks.
- 1k between +12V/GND computes 144mW and chooses suitable power resistor.
- Locked component unchanged.
- Existing footprint not overwritten unless `--force`.
- DNP excluded from BOM.
- Inventory shortage computed correctly.

Use golden KiCad schematic fixtures. Keep fixtures small and understandable.
