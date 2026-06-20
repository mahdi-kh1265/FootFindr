# 02 — CLI Specification

## Executable

Install both names if possible:

```bash
footfindr ...
ff ...
```

`ff` is the preferred everyday alias.

## CLI design style

Use `Typer` + `Rich`.

- `Typer`: clean nested commands, typed options, autocompletion.
- `Rich`: tables, panels, color, progress bars, markdown, diffs.

Use this format:

```bash
ff <domain> <action> <target...> [options]
```

But make the most common commands short:

```bash
ff res all -a
ff inv c 10u
ff bom fpga-lock -p jlcpcb
```

## Global options

All commands should support relevant global options:

```bash
--project <name>
--schematic <path>
--config <path>
--db <path>
--json
--quiet
--verbose
--no-color
--profile <name>
```

`--json` should output machine-readable JSON for scripting.

## Target grammar

Commands like `resolve`, `explain`, `lock`, `unlock`, etc. should accept targets.

Supported target forms:

```bash
ff resolve all
ff resolve missing
ff resolve caps
ff resolve resistors
ff resolve res
ff resolve ics
ff resolve C13
ff resolve C12 C15 R17 U3
ff resolve C12,C15,R17,U3
ff resolve "[C12, C15, R17, U3]"
ff resolve C10-C30
ff resolve risk
ff resolve review
```

Aliases:

```text
caps: c, cap, capacitors
resistors: r, res, resistor
ics: ic, u
missing: miss
all: all
```

Implementation detail: parse target list into a `ComponentSelector` object that resolves against the schematic graph.

## Command map

### Setup/config

```bash
ff init
ff doctor
ff config show
ff config set <key> <value>
```

`ff init` creates:

```text
footfindr.yaml
footfindr.sqlite
footfindr_data/
  datasheets/
  footprints/
  ic_profiles/
  vendor_models/
  reports/
  supplier_cache/
  projects/
```

`ff doctor` checks:

- Python package health.
- KiCad availability/path if configured.
- Schematic path if project selected.
- Footprint libraries.
- Config validity.
- DB migrations.
- Supplier credentials.
- Datasheet cache.
- Export profiles.

### Resolve/footprint application

Canonical:

```bash
ff resolve all
ff resolve all --apply
ff resolve all --apply --backup
ff resolve C13 --apply
ff resolve C12 C15 R17 U3 --apply
```

Aliases:

```bash
ff res all
ff res all -a
ff res C13 -a
```

Options:

```bash
--apply, -a                 write fields to schematic
--dry                       force dry-run
--backup                    create backup before editing
--force                     allow overwriting non-empty footprint fields
--min-confidence 0.92       auto-apply threshold
--write Footprint,MPN       fields to write
--no-mpn                    write only Footprint
--respect-locks / --ignore-locks
--report <path>
--decision-log <path>
```

Examples:

```bash
ff res missing -a
ff res caps -a
ff res C10-C40 --dry
ff res caps --value 10uF --rail +5V
ff res res --value 271
```

Expected behavior:

- Dry-run by default.
- `--apply` required for file edits.
- Backup created if `--backup`; consider making backups default for apply.
- Decision log always written on apply.
- Respect `FootFindrLocked=true` unless overridden.

### Explanation/debugging

```bash
ff explain C17
ff why C17
ff what-is C17
ff diff board.kicad_sch
ff undo board.kicad_sch
```

Aliases:

```bash
ff why C17
ff wi C17
```

`ff what-is` should print how FootFindr sees the component:

```text
C17
  type: capacitor
  value: 10uF
  nets: +5V, GND
  context: rail-to-ground capacitor
  rail voltage: 5.0V
  required voltage: >=10V
  proposed: CAP-10U-16V-X7R-0805
```

`ff why` should print why a previous decision was made.

### Locking/manual control

```bash
ff lock C17
ff unlock C17
ff dnp C17
ff populate C17
```

Fields:

```text
FootFindrLocked=true
DNP=true
DNPReason="optional filter cap"
```

### Inventory

Canonical:

```bash
ff inventory cap 10u
ff inventory res 271
ff inventory ic LMH6702
ff inventory check fpga-lock
ff inventory shortage fpga-lock
ff inventory reserve fpga-lock --builds 3
ff inventory release fpga-lock
ff inventory receive CAP-10U-16V-X7R-0805 --qty 500 --loc "Drawer C3"
ff inventory locate CAP-10U-16V-X7R-0805
```

Aliases:

```bash
ff inv c 10u
ff inv r 271
ff inv u LMH6702
ff inv 10u --cap
ff inv 271 --res
ff inv chk fpga-lock
ff inv short fpga-lock
```

Search filters:

```bash
ff inv c 10u --voltage-min 16V
ff inv c 10u --rail 5V
ff inv c 100n --package 0603
ff inv r 10k --tol 1% --package 0603
ff inv r 0.1 --power-min 0.5W
```

Inventory output should include:

- InternalPN
- MPN
- Manufacturer
- Value
- Package
- Footprint
- Ratings
- Quantity on hand
- Reserved quantity
- Available quantity
- Locations
- Supplier stock/price if requested
- Approved/deprecated status

### Supplier stock/cart

```bash
ff stock check CAP-10U-16V-X7R-0805
ff stock check fpga-lock
ff stock refresh fpga-lock
ff stock quote fpga-lock --builds 5
ff cart fpga-lock --supplier digikey
ff cart fpga-lock --supplier mouser
ff cart fpga-lock --supplier all
```

Aliases:

```bash
ff stk chk <target>
ff cart fpga-lock -s dk
ff cart fpga-lock -s mouser
```

DigiKey/Mouser auth:

```bash
ff auth digikey login
ff auth digikey status
ff auth digikey logout
ff auth mouser set-key
ff auth mouser status
```

Cart output modes:

```bash
ff cart fpga-lock -s digikey --csv
ff cart fpga-lock -s mouser --push
ff cart fpga-lock -s all --shortages-only
ff cart fpga-lock -s digikey --quote
```

Actual ordering/submission should be separated:

```bash
ff order submit fpga-lock --supplier digikey
```

Do not hide actual order/payment behavior under a simple `cart` command.

### Project/build lifecycle

Project = design identity. Build = actual run/quantity/revision.

```bash
ff project start fpga-lock --schematic fpga_lock.kicad_sch
ff project status fpga-lock
ff project end fpga-lock
ff project freeze fpga-lock
```

Aliases:

```bash
ff proj start fpga-lock -s fpga_lock.kicad_sch
ff p start fpga-lock -s fpga_lock.kicad_sch
```

Build session:

```bash
ff build start fpga-lock --qty 5 --rev A
ff build status
ff build reserve
ff build bom
ff build cart
ff build end
```

The build session should snapshot:

- Project name.
- Schematic path.
- Quantity.
- Revision.
- Decision log.
- BOM output.
- Inventory reservation.
- Cart files.
- Freeze manifest.

### BOM/export

```bash
ff bom fpga-lock
ff bom fpga-lock --profile posm
ff bom fpga-lock --profile jlcpcb
ff bom fpga-lock --profile digikey
ff bom fpga-lock --profile mouser
ff bom fpga-lock --profile assembly
ff bom board.kicad_sch --csv bom.csv
```

Aliases:

```bash
ff bom fpga-lock -p posm
ff bom fpga-lock -p jlcpcb
```

Formats:

```bash
--csv
--xlsx
--json
--html
```

Grouping options:

```bash
--group-by InternalPN
--group-by MPN
--group-by Value,Footprint
--exclude-dnp
--include-dnp
--variant assembled
```

### Fabrication/assembly package

```bash
ff fab fpga-lock --target jlcpcb
ff fab fpga-lock --target posm
ff cpl fpga-lock --profile jlcpcb
```

`ff fab --target jlcpcb` should eventually generate:

```text
production/
  gerbers.zip
  bom_jlcpcb.csv
  positions_jlcpcb.csv
  assembly_notes.md
  unmatched_lcsc_parts.csv
  footfindr_report.html
```

MVP can generate BOM profile only; CPL/Gerber integration can come later.

### Part database

```bash
ff part search "10uF 16V X7R 0805"
ff part show CAP-10U-16V-X7R-0805
ff part add
ff part approve CAP-10U-16V-X7R-0805
ff part deprecate CAP-10U-10V-X5R-0603
ff part alt CAP-10U-16V-X7R-0805
ff part bind-footprint CAP-10U-16V-X7R-0805 Capacitor_SMD:C_0805_2012Metric
```

Aliases:

```bash
ff psearch "10uF 16V"
ff alt C17
```

Supplier ingestion:

```bash
ff part ingest digikey CL21A106KOQNNNE
ff part ingest mouser 187-CL21A106KOQNNNE
ff part ingest nexar LMH6702MA/NOPB
```

### Datasheets/profiles

```bash
ff datasheet fetch LMH6702MA/NOPB
ff datasheet add LMH6702 ./LMH6702.pdf
ff datasheet open LMH6702
ff datasheet extract LMH6702
ff datasheet ask LMH6702 "what decoupling caps are recommended?"
```

Aliases:

```bash
ff ds fetch LMH6702
ff ds add LMH6702 ./LMH6702.pdf
```

Profiles:

```bash
ff profile draft LMH6702
ff profile show LMH6702
ff profile approve LMH6702
ff profile check LMH6702
```

Need to distinguish:

- `ic-profile`: datasheet/eval-board IC support profile.
- `export-profile`: BOM/cart/fab format profile.
- `policy`: resolver policy profile.

Consider commands:

```bash
ff ic-profile show TPS7A4700
ff export-profile list
ff policy use posm
```

### Power calculator/checker: `ff pwr`

`ff pwr` is for calculations/checks through parts/nets.

```bash
ff pwr res R17 --current 1.5A
ff pwr res 271 --voltage 12V
ff pwr cap C17 --rail 5V
ff pwr rail +5V
ff pwr budget fpga-lock
```

Alias:

```bash
ff pwr r R17 --i 1.5A
ff pwr c 10u --rail 5V
```

This can write constraints back:

```bash
ff pwr res R17 --current 2A --write
ff resolve R17 --apply
```

### Power electronics library: `ff pwrlib`

Separate from `ff pwr`. This is for power-electronics part libraries/templates.

```bash
ff pwrlib buck --vin 12V --vout 5V --iout 2A
ff pwrlib boost --vin 5V --vout 24V --iout 200mA
ff pwrlib ldo --vin 5V --vout 3.3V --iout 500mA
ff pwrlib fet --vds 100V --id 5A --package qfn
ff pwrlib driver --type gan --vbus 300V
ff pwrlib sense --current 2A --drop 50mV
```

This is not MVP core; design extension hooks.

### RF extension: `ff rf`

Separate future extension.

```bash
ff rf index-models ./vendor_models
ff rf search-inductor 10nH --freq 780MHz
ff rf search-cap 1pF --freq 780MHz
ff rf q 10nH --mpn 0402HP-10NX
ff rf sparam show 0402HP-10NX
ff rf match --z0 50 --freq 780MHz
ff rf export-sparams board.kicad_sch
```

Core should expose plugin architecture so `pip install footfindr-rf` adds `ff rf`.
