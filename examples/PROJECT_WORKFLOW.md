# Example FootFindr Workflow

## Start project

```bash
ff proj start fpga-lock -s fpga_lock.kicad_sch
ff doctor
```

## Normalize fields

```bash
ff fields normalize fpga_lock.kicad_sch --apply --backup
```

## Resolve footprints and parts

```bash
ff res all --apply --backup
ff audit fpga-lock
```

Review yellow/red items in report. For a special resistor, write explicit current:

```bash
ff pwr r R17 --i 1.5A --write
ff res R17 --apply
```

## Check inventory

```bash
ff inv check fpga-lock --builds 3
ff inv shortage fpga-lock --builds 3
ff inv reserve fpga-lock --builds 3
```

## Generate BOMs

```bash
ff bom fpga-lock -p posm --xlsx
ff bom fpga-lock -p jlcpcb --csv
```

## Generate carts

```bash
ff cart fpga-lock -s digikey --shortages-only --csv
ff cart fpga-lock -s mouser --shortages-only --csv
```

## Freeze build

```bash
ff build start fpga-lock --qty 3 --rev A
ff build bom
ff build cart
ff project freeze fpga-lock
ff build end
```
