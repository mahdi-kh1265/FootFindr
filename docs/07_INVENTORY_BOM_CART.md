# 07 — Inventory, BOM, Cart, Project/Build System

## Inventory system

FootFindr should eventually be a lightweight local inventory system. It does not have to replace Part-DB/InvenTree but should work independently at first.

### Inventory records

Track:

- InternalPN
- quantity on hand
- quantity reserved
- available quantity
- location
- lot
- source
- date received
- notes

Commands:

```bash
ff inv c 10u
ff inv r 271
ff inv check fpga-lock --builds 3
ff inv shortage fpga-lock --builds 3
ff inv reserve fpga-lock --builds 3
ff inv release fpga-lock
ff inv receive CAP-10U-16V-X7R-0805 --qty 500 --loc "Drawer C3"
ff inv locate CAP-10U-16V-X7R-0805
```

### Inventory check algorithm

1. Compile BOM from schematic/project.
2. Group by InternalPN or MPN.
3. Multiply quantities by build count.
4. Check local inventory available = on_hand - reserved.
5. Produce OK/SHORT/UNKNOWN rows.
6. Supplier quote only for shortages if requested.

## Project versus build

Separate project/design from actual build.

Project:

```bash
ff project start fpga-lock --schematic fpga_lock.kicad_sch
ff project status fpga-lock
ff project freeze fpga-lock
ff project end fpga-lock
```

Build:

```bash
ff build start fpga-lock --qty 5 --rev A
ff build reserve
ff build bom
ff build cart
ff build end
```

Project folder:

```text
footfindr_data/projects/fpga-lock/
  project.yaml
  builds/
    revA_2026-06-19_qty5/
      build.yaml
      decisions.json
      bom_posm.csv
      bom_jlcpcb.csv
      cart_digikey.csv
      inventory_reservation.json
      manifest.json
      reports/
```

## BOM profiles

FootFindr must support multiple BOM profiles.

Examples:

```bash
ff bom fpga-lock --profile posm
ff bom fpga-lock --profile jlcpcb
ff bom fpga-lock --profile digikey
ff bom fpga-lock --profile mouser
ff bom fpga-lock --profile assembly
```

Profiles define:

- Output columns.
- Grouping rules.
- DNP behavior.
- Preferred supplier part number field.
- Header renaming.
- Output format.

### POSM internal BOM

Columns:

- Quantity
- References
- Value
- InternalPN
- MPN
- Manufacturer
- Footprint
- Package
- DKPN
- MouserPN
- LCSC Part #
- InventoryAvailable
- NeedToBuy
- UnitPrice
- ExtendedPrice
- Notes

### JLCPCB BOM

JLCPCB-friendly columns should include at least:

- Comment
- Designator
- Footprint
- LCSC Part #

Consider also:

- Quantity
- Manufacturer Part Number
- Manufacturer
- Description

JLCPCB assembly also needs a CPL/position file. MVP can focus on BOM profile; future `ff cpl`/`ff fab` can produce CPL and Gerbers.

### DigiKey/Mouser BOM/cart formats

Use supplier-specific part numbers:

DigiKey cart CSV columns likely:

- DigiKey Part Number
- Quantity
- Customer Reference

Mouser cart/API payload:

- Mouser Part Number
- Quantity
- Customer Part Number/reference

Exact formats should be provider-specific and verified in implementation.

## Cart generation

Commands:

```bash
ff cart fpga-lock --supplier digikey --csv
ff cart fpga-lock --supplier mouser --csv
ff cart fpga-lock --supplier all --shortages-only
ff cart fpga-lock --supplier digikey --quote
ff cart fpga-lock --supplier mouser --push
```

Always support CSV fallback. API push/auth can come later.

Cart algorithm:

1. Compile BOM.
2. Apply build quantity.
3. Subtract local inventory if `--shortages-only`.
4. Select supplier offer by policy:
   - preferred supplier
   - available stock
   - lowest price
   - approved distributor
5. Generate cart file/API payload.
6. Record cart in build folder.

## Auth

Commands:

```bash
ff auth digikey login
ff auth digikey status
ff auth digikey logout
ff auth mouser set-key
ff auth mouser status
```

Do not store secrets in plain project files. Use OS keyring if practical; fallback to env vars.

Environment variables:

```text
DIGIKEY_CLIENT_ID
DIGIKEY_CLIENT_SECRET
DIGIKEY_REFRESH_TOKEN
MOUSER_SEARCH_API_KEY
MOUSER_CART_API_KEY
NEXAR_CLIENT_ID
NEXAR_CLIENT_SECRET
```

## Freeze/manifest

`ff freeze` creates a reproducibility snapshot:

```bash
ff project freeze fpga-lock
```

Manifest contents:

- FootFindr version.
- KiCad version if known.
- Schematic hash.
- Board hash if included.
- Rules/config hash.
- Parts DB hash.
- Footprint library refs/hashes.
- Resolved BOM with exact InternalPN/MPN/footprints.
- Decision log.
- Supplier cache timestamp.
- Datasheet/profile versions.

## Fab pack

Future command:

```bash
ff fab fpga-lock --target jlcpcb
```

Output:

```text
production/
  gerbers.zip
  bom_jlcpcb.csv
  positions_jlcpcb.csv
  assembly_notes.md
  unmatched_lcsc_parts.csv
  footfindr_report.html
  manifest.json
```

MVP can create BOMs first; full fabrication outputs can come after KiCad board integration.
