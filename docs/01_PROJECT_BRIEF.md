# 01 — Project Brief: FootFindr

## Name

**FootFindr**

Executable alias: `ff`

Python package name: `footfindr`

Suggested tagline:

> FootFindr resolves KiCad schematic parts into approved footprints, part numbers, inventory decisions, BOMs, and supplier carts from the command line.

## Problem

Manual KiCad footprint assignment takes far too long on boards with many symbols. The user wants a CLI tool that can read a KiCad schematic, decide the appropriate footprint/part implementation for supported parts, and write the fields back into the schematic.

The user specifically wants:

- A very rich CLI.
- Short aliases for all common commands.
- `ff resolve all` to resolve/write fields for everything supported.
- `ff resolve C13` to resolve a single refdes.
- `ff resolve C12 C15 R17 U3` to resolve a list.
- Inventory browsing/search from CLI.
- Supplier stock checks via DigiKey/Mouser/Nexar/Octopart where appropriate.
- Datasheet lookup/extraction/profiles.
- Internal inventory tracking and part reservation.
- Custom BOM outputs, including POSM internal and JLCPCB-compatible BOMs.
- Cart generation for suppliers, especially DigiKey/Mouser.
- Build/project sessions such as `ff project start`, `ff build start`, `ff bom`, `ff cart`, `ff freeze`.
- Future extensions for RF (`ff rf`) and power-electronics libraries (`ff pwrlib`).
- A separate power calculator/checker namespace (`ff pwr`).

## Product definition

FootFindr is a **post-schematic implementation compiler**.

It starts from an existing schematic. It does not generate the schematic from natural language. It does not replace the human engineer. It compiles generic/partial schematic intent into concrete implementation parts.

The primary output is a modified KiCad schematic with fields updated:

- `Footprint`
- `MPN`
- `Manufacturer`
- `InternalPN`
- `DKPN`
- `MouserPN`
- `LCSC Part #`
- `Package`
- `VoltageRating`
- `PowerRating`
- `FootFindrStatus`
- `FootFindrReason`
- `FootFindrConfidence`

## What makes it different from simple scripts

A naive script maps `Device:C` to `C_0603`. That is not good enough.

FootFindr chooses an **implementation variant**, for example:

```text
C17 value = 10uF
nets = +5V, GND
policy = capacitor voltage rating >= 2x rail voltage
approved candidates = 10uF 10V 0603, 10uF 16V 0805, 10uF 25V 1206
selected implementation = CAP-10U-16V-X7R-0805
footprint = Capacitor_SMD:C_0805_2012Metric
```

The selected footprint is a consequence of the selected real part.

## Main personas

1. **User/Engineer**
   - Draws schematic in KiCad.
   - Runs `ff resolve all --apply`.
   - Reviews yellow/red items.
   - Generates BOM/cart/build pack.

2. **Internal POSM workflow**
   - Uses strict approved parts.
   - Uses internal inventory and locations.
   - Uses custom BOM formats.
   - Uses supplier/cart integration.

3. **Future open-source users**
   - Install package via PyPI.
   - Configure their own parts DB.
   - Use local KiCad projects.

## Project philosophy

```text
AI reads/extracts/suggests.
Math sizes.
Rules decide.
Verifier checks.
FootFindr writes.
Human reviews the scary parts.
```

## Source-of-truth hierarchy

When resolving a part, use this order:

1. `FootFindrLocked=true` -> do not change.
2. Exact `InternalPN` -> exact known approved part.
3. Exact `MPN` -> exact known approved part/footprint.
4. IC profile match -> exact IC footprint and support part rules.
5. Generic capacitor/resistor constraints -> approved part selector.
6. Package hint field -> footprint mapping if safe.
7. Similar previous approved decision -> suggestion only unless confidence high.
8. Unknown/risky -> review/block.

## Success criterion for MVP

A user with a KiCad schematic and a small approved parts YAML can run:

```bash
ff resolve all --apply --backup
```

Then, after opening KiCad, common capacitors, simple resistors, and exact ICs already have correct `Footprint` and part fields.

## Non-goals for MVP

- No full KiCad GUI.
- No complete ECAD database server.
- No direct automated purchasing/payment.
- No automated RF/HV/GaN selection.
- No training custom AI models.
- No licensing-unsafe bundling of vendor libraries.
