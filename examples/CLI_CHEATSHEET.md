# FootFindr CLI Cheat Sheet

## Resolve footprints

```bash
ff res all -a
ff res missing -a
ff res C13 -a
ff res C12 C15 R17 U3 -a
ff res caps --value 10uF --rail +5V -a
ff why C17
ff undo board.kicad_sch
```

## Inventory

```bash
ff inv c 10u
ff inv c 10u --voltage-min 16V
ff inv r 271
ff inv check fpga-lock --builds 3
ff inv reserve fpga-lock --builds 3
ff inv receive CAP-10U-16V-X7R-0805 --qty 500 --loc "Drawer C3"
```

## Project/build

```bash
ff proj start fpga-lock -s fpga_lock.kicad_sch
ff build start fpga-lock --qty 5 --rev A
ff build reserve
ff build bom
ff build cart
ff build end
```

## BOM/cart

```bash
ff bom fpga-lock -p posm --csv
ff bom fpga-lock -p jlcpcb --csv
ff cart fpga-lock -s digikey --shortages-only
ff cart fpga-lock -s mouser --csv
```

## Datasheets/profiles

```bash
ff ds fetch LMH6702MA/NOPB
ff ds add LMH6702 ./LMH6702.pdf
ff profile draft LMH6702
ff profile approve LMH6702
```

## Power/RF future

```bash
ff pwr r R17 --i 1.5A --write
ff pwrlib buck --vin 12V --vout 5V --iout 2A
ff rf search-inductor 10nH --freq 780MHz
```
