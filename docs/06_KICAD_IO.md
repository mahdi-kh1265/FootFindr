# 06 — KiCad I/O Specification

## Goal

FootFindr must read and write KiCad schematic fields directly from the command line.

Primary file: `.kicad_sch`

KiCad schematics use S-expression text. FootFindr should edit symbol properties/fields while preserving everything else.

## What is written

At minimum:

```text
(property "Footprint" "Capacitor_SMD:C_0805_2012Metric")
```

Optional part fields:

```text
(property "InternalPN" "CAP-10U-16V-X7R-0805")
(property "MPN" "CL21A106KOQNNNE")
(property "Manufacturer" "Samsung")
(property "Package" "0805")
(property "VoltageRating" "16V")
(property "FootFindrStatus" "AUTO_APPLIED")
(property "FootFindrConfidence" "0.96")
(property "FootFindrReason" "+5V/GND cap; 2x voltage policy; approved 0805 part")
```

KiCad stores footprint references as `LibraryNickname:FootprintName`, not the footprint file contents. If FootFindr imports a custom `.kicad_mod`, it should copy it into a `.pretty` library and set `Footprint` to the corresponding ref.

## Reading strategy

Need two layers:

1. **Schematic field parser**
   - Read symbols, refs, values, existing fields, symbol library id, UUIDs.

2. **Connectivity graph**
   - MVP can use either parsed schematic wires/nets or `kicad-cli sch export netlist` if installed.
   - Prefer supporting `kicad-cli` for robust netlist when available; allow fallback.

## KiCad CLI integration

FootFindr does not need to run ERC for MVP. However, it may use `kicad-cli` for export tasks.

Potential calls:

```bash
kicad-cli sch export netlist --output board.net board.kicad_sch
kicad-cli sch export bom --output board_bom.csv board.kicad_sch
```

Do not require KiCad CLI for the simplest field-write path if direct parsing is enough.

## Writer requirements

- Preserve file as much as possible.
- Preserve UUIDs.
- Do not reorder symbols if possible.
- Edit only properties of target symbols.
- Create missing properties if absent.
- Avoid changing wire/geometry/labels.
- Make backup before apply.

## Backup and undo

On apply:

```text
board.kicad_sch.footfindr.bak
footfindr_decisions.json
```

Decision log should include enough old/new field values to undo.

Undo:

```bash
ff undo board.kicad_sch --decision-log footfindr_decisions.json
```

## Footprint library indexing

Scan `.pretty` directories and KiCad global/project footprint libraries.

Build:

```python
FootprintRecord(
    ref="Capacitor_SMD:C_0805_2012Metric",
    lib="Capacitor_SMD",
    name="C_0805_2012Metric",
    path=".../Capacitor_SMD.pretty/C_0805_2012Metric.kicad_mod",
    package_hint="0805",
    pad_count=2,
    verified=True,
)
```

For custom footprints:

```bash
ff lib import-footprint ./downloads/MyPart.kicad_mod --lib POSM
```

Behavior:

1. Copy `.kicad_mod` to configured `POSM.pretty/`.
2. Ensure KiCad project/global `fp-lib-table` contains POSM lib or warn.
3. Set footprint field as `POSM:MyPart`.

## Parsing challenges

KiCad S-expressions are nested. Use a robust parser, not regex for the whole file.

Possible approach:

- Use or write a minimal S-expression tokenizer/parser.
- Keep original text spans for property edits if possible.
- For MVP, parse into tree and serialize acceptable KiCad formatting.
- Golden-file tests are critical.

## Fields to normalize

FootFindr should tolerate common field names:

```text
MPN, Mfr Part Number, Manufacturer Part Number
Manufacturer, Mfr
DKPN, DigiKeyPN, Digi-Key Part Number
MouserPN, Mouser Part Number
LCSC, LCSC Part #, JLCPCB Part #
DNP, DoNotPopulate
```

But write canonical names from config.

## DNP handling

Components with DNP true should not be included in manufacturing BOM by default and may be skipped by resolve unless configured.

Truthy values:

```text
true, yes, y, 1, dnp, DNP
```

## Lock field

If `FootFindrLocked=true`, do not change unless `--force` or `--ignore-locks`.

## Multi-sheet/hierarchical projects

MVP can start with single schematic root but design for multiple files.

Need to support:

- Root `.kicad_sch`.
- Sheet paths.
- Repeated sheet references.
- Multiple symbol instances.

For first MVP, clearly document limitations and add tests as capability expands.
