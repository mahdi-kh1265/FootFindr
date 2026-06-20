# 05 — Data Model

## Goals

FootFindr needs more than a `part -> footprint` table. It should model parts similarly to serious EDA/vendor libraries:

```text
Part identity
  -> physical variants
  -> electrical specs
  -> footprint assets
  -> supplier offers
  -> documents/datasheets
  -> simulation models
  -> inventory records
  -> approval/lifecycle status
```

## MVP data storage

Start with YAML for hand-editable examples plus SQLite for real use.

- `approved_parts.yaml`: simple bootstrap file.
- `footfindr.sqlite`: normalized database.
- `footfindr.yaml`: project/user config and policy.

Implement YAML import/export so early development is easy.

## Core entities

### Part

Represents a real manufacturer part or internal generic approved part.

Fields:

- `internal_pn`: POSM/FootFindr internal part number, e.g. `CAP-10U-16V-X7R-0805`.
- `category`: capacitor, resistor, inductor, ic, connector, etc.
- `manufacturer`
- `mpn`
- `description`
- `approved_status`: approved, candidate, deprecated, blocked.
- `lifecycle_status`: active, nrnd, obsolete, unknown.
- `notes`

### Electrical specs

For capacitors:

- capacitance
- voltage_rating
- dielectric
- tolerance
- temp_rating
- esr/esl optional
- dc_bias_curve optional/future

For resistors:

- resistance
- tolerance
- power_rating
- voltage_rating
- tempco
- technology
- pulse_rating optional

For inductors/ferrites future:

- inductance
- current_rms
- current_saturation
- dcr
- srf
- q
- impedance_at_frequency

### Physical variant

- package, e.g. `0603`, `0805`, `SOIC-8`, `QFN-20`.
- footprint ref, e.g. `Capacitor_SMD:C_0805_2012Metric`.
- body size.
- height.
- pad count.
- pitch.
- land-pattern source.

### Supplier offer

- supplier: DigiKey/Mouser/LCSC/etc.
- supplier_pn.
- stock.
- price breaks.
- packaging.
- MOQ.
- last_checked timestamp.

### Inventory record

- internal_pn.
- quantity_on_hand.
- quantity_reserved.
- location.
- lot.
- date_received.
- source.
- notes.

### Document

- datasheet path/url.
- app note path/url.
- eval board BOM path.
- source provider.
- hash.
- extracted JSON path.

### Simulation/model asset

Future RF/power extensions:

- model_type: SPICE, S-parameter, IBIS, Touchstone, vendor library.
- model_path.
- frequency range.
- validity notes.
- source/license.

## SQLite schema sketch

```sql
CREATE TABLE parts (
    internal_pn TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    manufacturer TEXT,
    mpn TEXT,
    description TEXT,
    approved_status TEXT NOT NULL DEFAULT 'candidate',
    lifecycle_status TEXT NOT NULL DEFAULT 'unknown',
    notes TEXT
);

CREATE TABLE specs (
    internal_pn TEXT PRIMARY KEY,
    value TEXT,
    capacitance TEXT,
    resistance TEXT,
    inductance TEXT,
    voltage_rating TEXT,
    current_rating TEXT,
    power_rating TEXT,
    tolerance TEXT,
    dielectric TEXT,
    tempco TEXT,
    dcr TEXT,
    esr TEXT,
    srf TEXT,
    q TEXT,
    FOREIGN KEY(internal_pn) REFERENCES parts(internal_pn)
);

CREATE TABLE physical_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_pn TEXT NOT NULL,
    package TEXT,
    footprint TEXT,
    body_size TEXT,
    height TEXT,
    pad_count INTEGER,
    pitch TEXT,
    land_pattern_source TEXT,
    verified INTEGER DEFAULT 0,
    FOREIGN KEY(internal_pn) REFERENCES parts(internal_pn)
);

CREATE TABLE supplier_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_pn TEXT NOT NULL,
    supplier TEXT NOT NULL,
    supplier_pn TEXT,
    stock INTEGER,
    price_json TEXT,
    packaging TEXT,
    moq INTEGER,
    last_checked TEXT,
    FOREIGN KEY(internal_pn) REFERENCES parts(internal_pn)
);

CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_pn TEXT NOT NULL,
    qty_on_hand INTEGER NOT NULL DEFAULT 0,
    qty_reserved INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    lot TEXT,
    date_received TEXT,
    source TEXT,
    notes TEXT,
    FOREIGN KEY(internal_pn) REFERENCES parts(internal_pn)
);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_pn TEXT,
    mpn TEXT,
    doc_type TEXT,
    local_path TEXT,
    url TEXT,
    sha256 TEXT,
    extracted_json_path TEXT,
    source TEXT,
    approved INTEGER DEFAULT 0
);

CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    schematic_path TEXT,
    ref TEXT,
    old_fields_json TEXT,
    new_fields_json TEXT,
    selected_internal_pn TEXT,
    selected_footprint TEXT,
    status TEXT,
    confidence REAL,
    reason_json TEXT,
    timestamp TEXT
);
```

## Pydantic models

Define strict models for all external/user inputs.

```python
class ApprovedPart(BaseModel):
    internal_pn: str
    category: Literal['capacitor', 'resistor', 'inductor', 'ic', 'connector', 'other']
    value: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    package: str | None = None
    footprint: str | None = None
    approved: bool = False
    specs: dict[str, str] = Field(default_factory=dict)
```

## InternalPN naming conventions

Suggested:

Capacitors:

```text
CAP-100N-16V-X7R-0603
CAP-1U-16V-X7R-0603
CAP-10U-16V-X7R-0805
CAP-22U-25V-X7R-1206
```

Resistors:

```text
RES-10K-1PCT-0603
RES-271R-1PCT-0603
RES-0R-JUMPER-0603
RSENSE-0R1-1PCT-1206-0P5W
```

ICs:

```text
IC-LMH6702MA-SOIC8
IC-TPS7A4700-QFN20
```

Connectors:

```text
CONN-SMA-EDGE-50OHM
```

## Unit handling

Use Pint for all numeric comparisons.

Must parse:

```text
10uF, 100nF, 4.7uF
271, 271R, 10k, 0.1R
16V, 0.25W, 500mA, 2A
0603, 0805 as packages, not numbers
```

Normalize values for matching, but preserve original display strings.

## Provenance

Every decision should store where requirements came from:

```json
{
  "capacitance": {"value": "10uF", "source": "KiCad Value field"},
  "rail_voltage": {"value": "5V", "source": "footfindr.yaml rails.+5V"},
  "voltage_min": {"value": "10V", "source": "2.0x capacitor derating policy"},
  "candidate": {"value": "CAP-10U-16V-X7R-0805", "source": "approved_parts.sqlite"}
}
```

## Decision signatures

For previous-decision matching, create signatures such as:

```json
{
  "category": "capacitor",
  "value_norm": "10uF",
  "nets_class": ["rail:+5V", "gnd"],
  "connected_ic_pin_role": null,
  "risk_flags": []
}
```

For resistor:

```json
{
  "category": "resistor",
  "value_norm": "10k",
  "nets_class": ["rail:+3V3", "gnd"],
  "power_known": true,
  "risk_flags": []
}
```

Use exact or similarity matching later.
