# 08 — AI and Datasheets

## AI philosophy

AI should not be the final authority. AI should help extract, draft, explain, and review.

```text
AI reads/extracts/suggests.
Math sizes.
Rules decide.
Verifier checks.
FootFindr writes.
Human reviews the scary parts.
```

## Good AI jobs

1. Read datasheet PDFs and draft IC profiles.
2. Read eval-board BOMs/reference schematics and extract support parts.
3. Normalize messy supplier parameter text into strict schemas.
4. Explain why a component was flagged.
5. Suggest alternates, but only as suggestions.
6. Answer datasheet questions through local PDF retrieval.

## Bad AI jobs

1. Silently choose HV/RF/GaN/power footprints.
2. Directly edit KiCad files.
3. Invent specs without source.
4. Override deterministic rules.
5. Make safety-critical decisions without review.

## Datasheet cache

Directory:

```text
footfindr_data/datasheets/
  LMH6702MA/
    datasheet.pdf
    extracted.json
    source.yaml
  TPS7A4700/
    datasheet.pdf
    extracted.json
```

Each datasheet record should include:

- MPN.
- Manufacturer.
- URL/source.
- Local path.
- SHA256.
- Download timestamp.
- Extraction status.
- Human approval status.

## Datasheet commands

```bash
ff ds fetch LMH6702MA/NOPB
ff ds add LMH6702 ./LMH6702.pdf
ff ds extract LMH6702
ff ds ask LMH6702 "what decoupling caps are recommended?"
```

## IC profile format

IC profiles are structured, reviewed datasheet/eval-board knowledge.

Example:

```yaml
ic: TPS7A4700
aliases:
  - TPS7A4700RGW
manufacturer: Texas Instruments
profile_status: approved
package:
  footprint: Package_DFN_QFN:Texas_S-PVQFN-N20_EP3.5x3.5mm
pins:
  IN:
    type: power_input
    voltage_policy: connected_rail
    support_parts:
      - kind: capacitor
        role: input_bypass
        value: 10uF
        voltage_policy: 2x_pin_voltage
        preferred_packages: [0805, 1206]
  OUT:
    type: power_output
    support_parts:
      - kind: capacitor
        role: output_bypass
        value: 10uF
        voltage_policy: 2x_output_voltage
        preferred_packages: [0805, 1206]
  EN:
    type: logic_input
    common_support:
      - kind: resistor
        role: pullup_or_pulldown
        preferred_package: 0603
sources:
  - type: datasheet
    path: footfindr_data/datasheets/TPS7A4700/datasheet.pdf
    notes: "Extracted and human-approved."
```

## AI profile drafting flow

```bash
ff profile draft TPS7A4700
```

Algorithm:

1. Find local datasheet or fetch if configured.
2. Extract text/tables with PyMuPDF or similar.
3. Retrieve relevant chunks: pin table, package table, typical application, layout guidelines, external component recommendations.
4. Ask AI to produce strict JSON matching `ICProfileDraft` schema.
5. Validate schema with Pydantic.
6. Save draft as `ic_profiles/TPS7A4700.draft.yaml`.
7. User reviews/edits.
8. `ff profile approve TPS7A4700` marks profile approved.

Only approved profiles can drive auto-apply decisions by default.

## Eval-board ingestion

Commands:

```bash
ff profile draft TPS7A4700 --eval-bom TPS7A4700EVM_BOM.csv --eval-sch TPS7A4700EVM.pdf
```

Goal:

- Learn common support component values/packages from reference design.
- Never blindly copy; produce draft profile.

Example extracted support rules:

```yaml
support_parts_from_eval_board:
  IN:
    capacitors:
      - value: 10uF
        package: 0805
  OUT:
    capacitors:
      - value: 10uF
        package: 0805
  EN:
    resistors:
      - value: 10k
        package: 0603
```

## AI output validation

AI must output:

- JSON/YAML with strict schema.
- Source page or quote snippet for each nontrivial claim where possible.
- Confidence per extracted field.
- `human_approved=false` by default.

Reject if:

- Invalid units.
- Missing source.
- Package/pin counts conflict.
- Values are nonsensical.

## Datasheet Q&A

`ff ds ask` should answer questions from local datasheet text, not the general internet.

Example:

```bash
ff ds ask LMH6702 "what supply decoupling is recommended?"
```

Output should include source page/chunk references where available.

## Supplier datasheet sources

Providers:

- Nexar/Octopart for datasheet URLs and broad part metadata.
- DigiKey for product details and datasheet links.
- Mouser for product details and datasheet links.
- Manual PDF drop-in.

Always cache downloaded datasheets locally.
