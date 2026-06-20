# 09 — Extensions: `ff pwr`, `ff pwrlib`, `ff rf`

## Why extensions

Core FootFindr should stay reliable and focused:

- Exact ICs.
- Common capacitors.
- Simple/computable resistors.
- BOM/inventory/cart.

Power electronics and RF are much deeper design spaces. They should be supported through namespaces/extensions rather than bloating core.

## `ff pwr`: power calculator/checker

Purpose:

- Calculate/check power through specific parts.
- Write explicit constraints such as `PowerMin`, `CurrentRMS`, `CurrentPeak` into schematic fields.
- Feed resolver.

Examples:

```bash
ff pwr res R17 --current 1.5A
ff pwr res 271 --voltage 12V
ff pwr cap C17 --rail 5V
ff pwr rail +5V
ff pwr budget fpga-lock
```

Useful behavior:

```bash
ff pwr res R17 --current 2A --write
ff resolve R17 --apply
```

This first writes `CurrentRMS=2A` or `PowerMin=<computed>` to R17, then the resolver can choose a package/MPN.

## `ff pwrlib`: power electronics library/templates

Purpose:

- Search/organize parts for power electronics.
- Support buck/boost/LDO/flyback/FET/diode/sense-resistor/GaN-driver workflows.
- Use datasheet/eval-board profiles.

Commands:

```bash
ff pwrlib buck --vin 12V --vout 5V --iout 2A
ff pwrlib boost --vin 5V --vout 24V --iout 200mA
ff pwrlib ldo --vin 5V --vout 3.3V --iout 500mA
ff pwrlib fet --vds 100V --id 5A --package qfn
ff pwrlib driver --type gan --vbus 300V
ff pwrlib sense --current 2A --drop 50mV
```

Potential outputs:

- Candidate ICs.
- Recommended external passives.
- Required voltage/current/power constraints.
- Eval-board part recommendations.
- Thermal warnings.
- Footprint/MPN candidates.

MVP: leave as extension skeleton.

## `ff rf`: RF extension

Purpose:

- RF/microwave components need frequency-aware behavior.
- Footprint/package selection may depend on parasitics, Q, SRF, S-parameters, model validity, and layout.

Commands:

```bash
ff rf index-models ./vendor_models
ff rf search-inductor 10nH --freq 780MHz
ff rf search-cap 1pF --freq 780MHz
ff rf q 10nH --mpn 0402HP-10NX
ff rf sparam show 0402HP-10NX
ff rf match --z0 50 --freq 780MHz
ff rf export-sparams board.kicad_sch
```

RF extension should index local vendor libraries:

- Coilcraft S-parameters/SPICE.
- Murata ADS/S-parameter libraries.
- TDK libraries.
- Modelithics if licensed.

Do not redistribute vendor libraries. Provide importers/indexers only.

## Vendor library model

Serious tools like ADS/Genesys organize parts with:

- Manufacturer.
- MPN/series/value.
- Package.
- Electrical metadata.
- Simulation assets: SPICE, Touchstone/S-parameters, IBIS, scalable models.
- Layout footprint/land pattern.
- Datasheet/app notes.
- Frequency/validity limits.
- License/source metadata.

FootFindr should mirror this locally:

```text
VendorModelIndex
  manufacturer
  mpn
  series
  value
  package
  model_type
  model_path
  frequency_min
  frequency_max
  source
  license_notes
```

## Relationship to core resolver

Core resolver can use extensions as knowledge providers later:

- `ff rf` can provide approved RF implementation variants.
- `ff pwrlib` can provide power components/templates.
- Core still controls field writing and decision logs.

## Safety

RF/HV/GaN/power-switching parts should default to review/block unless exact approved parts are used. Do not auto-resolve from generic values alone.
