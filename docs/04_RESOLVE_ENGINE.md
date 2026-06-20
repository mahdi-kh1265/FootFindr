# 04 — Resolve Engine Specification

## Purpose

The resolve engine is the heart of FootFindr. It selects real implementation parts and writes the corresponding KiCad `Footprint` fields.

It must be deterministic, explainable, and conservative.

## Inputs

- `ComponentContext`: symbol/value/fields/nets/connected IC pins.
- `ProjectPolicy`: rails, derating, package preferences, risk patterns.
- `ApprovedPartsDB`: local approved parts and exact footprint mappings.
- `FootprintIndex`: known KiCad footprint refs and optional metadata.
- `ICProfiles`: approved datasheet/eval-board profiles.
- `Inventory/Supplier` data: optional; should not be required for basic resolution.

## Outputs

`Decision` object:

```python
@dataclass
class Decision:
    ref: str
    status: DecisionStatus
    confidence: float
    selected_internal_pn: str | None
    selected_mpn: str | None
    selected_footprint: str | None
    fields_to_write: dict[str, str]
    reasons: list[str]
    warnings: list[str]
    errors: list[str]
    old_fields: dict[str, str]
    requirements: dict[str, Any]
    candidate_summary: list[dict[str, Any]]
```

## Resolver hierarchy

For each selected component, try resolvers in order:

1. **Lock/skip resolver**
   - If `FootFindrLocked=true`, status `UNCHANGED`.

2. **Exact InternalPN resolver**
   - If component field `InternalPN` exists, lookup exact approved part.
   - If footprint exists and matches, high-confidence auto.

3. **Exact MPN resolver**
   - If `MPN` exists, lookup exact part in approved DB.
   - Supplier lookups may enrich but should not be required.

4. **IC resolver**
   - If component category is IC and MPN/value matches approved IC profile, use package/footprint.

5. **Capacitor resolver**
   - Handles generic caps with known context.

6. **Resistor resolver**
   - Handles exact/simple/computable resistors.

7. **Package hint resolver**
   - If `PackageHint` or symbol naming like `C_0805` exists, map package to footprint.
   - Do this only if safe or user explicitly requests.

8. **Previous decision resolver**
   - Match prior approved decisions by signature.
   - Suggest; auto only when strict criteria pass.

9. **Review/block fallback**
   - Do not write.

## Component context

```python
@dataclass
class ComponentContext:
    ref: str
    value: str
    symbol: str
    fields: dict[str, str]
    footprint: str | None
    category: str | None
    pins: dict[str, str]  # pin_name_or_number -> net_name
    nets: set[str]
    connected_ic_pins: list[tuple[str, str, str]]  # (ref, mpn/value, pin)
    known_rails: list[RailContext]
    risk_flags: list[str]
```

## Rail detection

Rail definitions come from `footfindr.yaml`:

```yaml
rails:
  "+3V3": { voltage: 3.3 }
  "+5V": { voltage: 5.0 }
  "V_EOM": { voltage: 300, force_manual_review: true }
```

Rules:

- A cap connected to `GND` and one known rail is a rail-to-GND cap.
- A resistor between two known rails has computable worst-case DC power.
- Any component touching a rail marked `force_manual_review` is not auto-applied.

## Risk detection

Policy:

```yaml
high_risk_net_patterns:
  - HV
  - EOM
  - RF
  - CLK
  - GATE
  - SW
  - RESET
  - VPI
  - PIEZO
```

If a component touches high-risk nets:

- Exact approved InternalPN/MPN may still auto-apply if locked/approved.
- Generic passive selection should usually be blocked/review.

## Capacitor resolver

Supported MVP cases:

1. Exact InternalPN/MPN.
2. Rail-to-GND capacitor on known non-risk rail.
3. Capacitor attached to known IC pin with approved IC profile support recommendation.

Do not auto-resolve:

- RF matching/DC-blocking caps.
- HV/EOM caps.
- Timing/crystal caps unless exact approved part.
- Op-amp compensation/filter caps unless explicit profile/part.

### Capacitor requirements

From component:

- Capacitance from `Value` field.
- Existing package hint if any.

From rail:

- `rail_voltage`.
- `required_voltage = rail_voltage * voltage_derating`.

From IC profile:

- Recommended value/package/voltage policy.

From policy:

- Preferred dielectrics.
- Preferred packages.
- Stock thresholds.

Example:

```text
C17 = 10uF, nets +5V/GND
rail_voltage = 5V
voltage_derating = 2.0
required_voltage = 10V
preferred packages for 10uF on 5V = [0805, 1206]
```

Candidate filter:

- category == capacitor
- capacitance matches requested tolerance/policy
- voltage_rating >= required_voltage
- approved == true unless user allows supplier candidates
- package in policy allowed list
- footprint exists
- not deprecated

Scoring:

- Approved internal part: +large
- Smallest package that passes: +medium
- Higher voltage margin: +small
- Stock available: +medium
- Same as previous approved decision: +medium
- Missing DC-bias model/unknown effective cap: warning/lower confidence for high-value MLCCs

## Resistor resolver

Supported MVP cases:

1. Exact InternalPN/MPN.
2. Explicit `PowerMin` field.
3. Explicit `CurrentRMS` field.
4. Resistor between known DC rails.
5. Boring default signal resistor if policy allows and not high-risk.

Do not auto-resolve:

- Current sense without current/power.
- Gate resistors on fast switching nets.
- RF terminations/attenuators.
- Snubbers.
- Power path resistors.
- High-voltage bleeders without explicit stress/power.

### Resistor power math

If `CurrentRMS` known:

```text
P = I_rms^2 * R
```

If voltage across resistor known:

```text
P = V^2 / R
```

If `PowerMin` exists, use it directly.

Apply derating:

```text
required_rating = P / power_derating
```

For example, if derating factor is 0.5 and actual power is 0.144 W, required rated power is 0.288 W.

Candidate filter:

- resistance matches.
- tolerance meets policy/field.
- power_rating >= required_rating.
- voltage_rating passes if known.
- package/footprint exists.
- approved.

## IC resolver

Supported MVP:

- Exact MPN -> approved part -> footprint.
- Package/name verification.
- Optional pad count verification against footprint index.

Fields:

- `MPN`
- `Package`
- `InternalPN`

If MPN has multiple packages, require package field or exact approved MPN/variant.

## Exact MPN resolver

Highest-trust normal path.

If part exists in DB:

- Use exact stored footprint.
- Write fields.
- Verify footprint exists.
- Status AUTO_APPLY unless high-risk policy says exact is still review.

If part not in DB:

- Try supplier/datasheet lookup only if configured.
- Do not auto-apply unapproved supplier result unless `--allow-unapproved`.

## Package hint resolver

Package hints may be from:

- `PackageHint` field.
- Symbol name: `POSM:C_0603`, `POSM:R_0805`.
- Value string containing package convention.

This can map:

```yaml
package_footprints:
  capacitor:
    "0603": "Capacitor_SMD:C_0603_1608Metric"
    "0805": "Capacitor_SMD:C_0805_2012Metric"
  resistor:
    "0603": "Resistor_SMD:R_0603_1608Metric"
```

Use as fallback; still validate against risk and fields.

## Field writing

For AUTO_APPLY decisions, write:

- `Footprint`
- `InternalPN`
- `MPN`
- `Manufacturer`
- `Package`
- `VoltageRating` or `PowerRating` where relevant
- `FootFindrStatus=AUTO_APPLIED`
- `FootFindrConfidence=<score>`
- `FootFindrReason=<short reason>`

Do not write huge explanations into schematic fields. Store full details in JSON/HTML report.

## Decision examples

### C17 10uF on +5V/GND

```text
AUTO_APPLY
Footprint = Capacitor_SMD:C_0805_2012Metric
InternalPN = CAP-10U-16V-X7R-0805
Reason = +5V/GND cap, 2x voltage rule, approved 0805 16V X7R part.
```

### R12 1k between +12V/GND

```text
Compute P = 12^2/1000 = 144mW.
With 0.5 derating, require >=288mW rating.
If approved 0805/1206 exists, select. Otherwise review.
```

### C31 1uF on V_EOM/GND

```text
BLOCKED
Reason = high-voltage/EOM net; generic MLCC selection not allowed.
```

### U3 LMH6702MA/NOPB

```text
AUTO_APPLY if MPN exists in DB with SOIC-8 footprint.
```
