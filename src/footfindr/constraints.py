"""Constraint engine for FootFindr.

Project-local constraints stored in ``.footfindr/constraints.yaml``.
Constraints attach to specific schematic refs, groups, or patterns
and can be checked against supplier parts or library parts.

Priority: exact ref > group > pattern > project default.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("footfindr.constraints")


# ---------------------------------------------------------------------------
# Constraint field aliases — normalize user-typed short names
# ---------------------------------------------------------------------------

_CONSTRAINT_FIELD_ALIASES: dict[str, str] = {
    # voltage
    "voltage": "voltage",
    "volt": "voltage",
    "v": "voltage",
    "vmax": "voltage",
    "vmin": "voltage",
    # dielectric
    "dielectric": "dielectric",
    "diel": "dielectric",
    # package
    "package": "package",
    "pack": "package",
    "pkg": "package",
    "case": "package",
    # capacitance / value
    "capacitance": "capacitance",
    "cap": "capacitance",
    # resistance
    "resistance": "resistance",
    "res": "resistance",
    # tolerance
    "tolerance": "tolerance",
    "tol": "tolerance",
    # temperature
    "temperature": "temperature",
    "temp": "temperature",
    "temperature_range": "temperature",
    # family
    "family": "family",
    "fam": "family",
    # current
    "current": "current",
    "curr": "current",
    "i": "current",
    # output current
    "output_current": "output_current",
    # value
    "value": "value",
    "val": "value",
}


def normalize_constraint_field(field_name: str) -> str:
    """Normalize a constraint field name to its canonical form."""
    return _CONSTRAINT_FIELD_ALIASES.get(field_name.lower().strip(), field_name.lower().strip())


# ---------------------------------------------------------------------------
# Unit parsing — pragmatic, handles common EE units
# ---------------------------------------------------------------------------

_UNIT_MULTIPLIERS: dict[str, float] = {
    # SI prefix
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "\u00b5": 1e-6,  # µ
    "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
}

_VOLTAGE_RE = re.compile(r"([<>=!]*)?\s*([\d.]+)\s*([mkKM]?)\s*[Vv]?\s*$")
_CAPACITANCE_RE = re.compile(r"([<>=!]*)?\s*([\d.]+)\s*([pnuµmM]?)\s*[Ff]?\s*$")
_RESISTANCE_RE = re.compile(
    r"([<>=!]*)?\s*([\d.]+)\s*([mkKMG]?)\s*(?:[Oo]hm|[Rr\u03A9])?\s*$"
)
_CURRENT_RE = re.compile(r"([<>=!]*)?\s*([\d.]+)\s*([mkKM]?)\s*[Aa]?\s*$")
_TEMPERATURE_RE = re.compile(r"([<>=!]*)?\s*([-\d.]+)\s*°?\s*[Cc]?\s*$")
_PERCENT_RE = re.compile(r"([<>=!]*)?\s*([\d.]+)\s*%?\s*$")
_STOCK_RE = re.compile(r"([<>=!]*)?\s*([\d,]+)\s*$")


# EE shorthand pattern: 4u7 -> 4.7µ, 2k2 -> 2.2k, 0R1 -> 0.1Ω, 10n -> 10n
_SHORTHAND_RE = re.compile(
    r"^([\d]+)\s*([pnuµmkKMGRr])\s*([\d]+)?\s*[FfVvAaΩ]?\s*$"
)


def _parse_shorthand(s: str) -> str | None:
    """Parse EE shorthand notation into standard form.

    Examples:
        4u7  -> 4.7u
        2n2  -> 2.2n
        10n  -> 10n
        2k2  -> 2.2k
        4R7  -> 4.7
        0R1  -> 0.1
        100n -> 100n
    """
    s = s.strip()
    if not s:
        return None
    m = _SHORTHAND_RE.match(s)
    if not m:
        return None
    integer_part = m.group(1)
    prefix = m.group(2)
    decimal_part = m.group(3) or ""

    # R means the decimal point for resistance (4R7 = 4.7Ω)
    if prefix.upper() == "R":
        if decimal_part:
            return f"{integer_part}.{decimal_part}"
        else:
            return f"{integer_part}"

    if decimal_part:
        return f"{integer_part}.{decimal_part}{prefix}"
    else:
        return f"{integer_part}{prefix}"


def parse_numeric_value(s: str, domain: str = "") -> float | None:
    """Parse a string with optional units into a float.

    Domains: voltage, capacitance, resistance, current, temperature,
             percent, stock, price, generic.
    """
    s = s.strip()
    # Strip operator prefix
    s = re.sub(r"^[<>=!]+\s*", "", s)

    if not s:
        return None

    # Try EE shorthand first (4u7 -> 4.7u)
    expanded = _parse_shorthand(s)
    if expanded is not None:
        s = expanded

    patterns: list[tuple[str, re.Pattern]] = [
        ("voltage", _VOLTAGE_RE),
        ("capacitance", _CAPACITANCE_RE),
        ("resistance", _RESISTANCE_RE),
        ("current", _CURRENT_RE),
        ("temperature", _TEMPERATURE_RE),
        ("percent", _PERCENT_RE),
        ("stock", _STOCK_RE),
    ]

    # Try domain-specific first
    for d, pat in patterns:
        if domain and d != domain:
            continue
        m = pat.match(s)
        if m:
            num = float(m.group(2).replace(",", ""))
            prefix = m.group(3) if m.lastindex >= 3 else ""
            mult = _UNIT_MULTIPLIERS.get(prefix, 1.0) if prefix else 1.0
            return num * mult

    # Try all patterns if no domain match
    if domain:
        for _, pat in patterns:
            m = pat.match(s)
            if m:
                num = float(m.group(2).replace(",", ""))
                prefix = m.group(3) if m.lastindex >= 3 else ""
                mult = _UNIT_MULTIPLIERS.get(prefix, 1.0) if prefix else 1.0
                return num * mult

    # Last resort: try plain float
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _guess_domain(field_name: str) -> str:
    """Guess the unit domain from a constraint field name."""
    f = field_name.lower()
    if "voltage" in f or f in ("v", "vmax", "vmin"):
        return "voltage"
    if "capacit" in f or f in ("value",) or "cap" in f:
        return "capacitance"
    if "resist" in f or f in ("resistance",):
        return "resistance"
    if "current" in f:
        return "current"
    if "temp" in f:
        return "temperature"
    if "tolerance" in f or "tol" in f:
        return "percent"
    if "stock" in f:
        return "stock"
    if "price" in f or "cost" in f:
        return "price"
    return ""


def _parse_op_value(s: str) -> tuple[str, str]:
    """Parse an operator+value string like '>=25V' into (op, value)."""
    s = s.strip()
    if s.startswith(">="):
        return "gte", s[2:].strip()
    if s.startswith("<="):
        return "lte", s[2:].strip()
    if s.startswith("!="):
        return "ne", s[2:].strip()
    if s.startswith(">"):
        return "gt", s[1:].strip()
    if s.startswith("<"):
        return "lt", s[1:].strip()
    if s.startswith("="):
        return "eq", s[1:].strip()
    # Check for pipe-separated alternatives: "MSOP|DFN"
    if "|" in s:
        return "matches", s
    # Check for comma-separated in-list: "X7R,X5R,C0G"
    if "," in s:
        return "in", s
    # Default: eq for exact match
    return "eq", s


# ---------------------------------------------------------------------------
# Constraint data model
# ---------------------------------------------------------------------------

@dataclass
class Constraint:
    """A single constraint on a field."""
    field: str           # "voltage", "package", "dielectric", etc.
    op: str              # "gte", "lte", "gt", "lt", "eq", "ne",
                         # "contains", "not_contains", "matches", "in"
    value: str           # "25V", "X7R", "0805"
    reason: str | None = None  # human-readable rationale

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "field": self.field,
            "op": self.op,
            "value": self.value,
        }
        if self.reason:
            d["reason"] = self.reason
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Constraint:
        return cls(
            field=d["field"],
            op=d["op"],
            value=d["value"],
            reason=d.get("reason"),
        )

    @classmethod
    def from_field_value(cls, field_name: str, value_str: str, *, reason: str | None = None) -> Constraint:
        """Create a constraint from a CLI-style field=value input.

        Handles:
            voltage ">=25V"     -> Constraint(field="voltage", op="gte", value="25V")
            volt ">=25V"        -> Constraint(field="voltage", op="gte", value="25V")
            dielectric X7R      -> Constraint(field="dielectric", op="eq", value="X7R")
            package "MSOP|DFN"  -> Constraint(field="package", op="matches", value="MSOP|DFN")
            tolerance "<=1%"    -> Constraint(field="tolerance", op="lte", value="1%")
        """
        # Special soft-constraint fields
        if field_name in ("prefer_package", "avoid_package", "family", "suppliers", "reason"):
            return cls(field=field_name, op="eq", value=value_str, reason=reason)

        # Normalize field name aliases
        canonical = normalize_constraint_field(field_name)
        op, clean = _parse_op_value(value_str)
        return cls(field=canonical, op=op, value=clean, reason=reason)


@dataclass
class ConstraintResult:
    """Result of checking a single constraint against a part."""
    constraint: Constraint
    passed: bool
    actual_value: str
    message: str
    is_soft: bool = False  # True for prefer_/avoid_ constraints


# ---------------------------------------------------------------------------
# Constraint containers
# ---------------------------------------------------------------------------

@dataclass
class RefConstraints:
    """Constraints for a specific schematic reference."""
    ref: str
    constraints: list[Constraint] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for c in self.constraints:
            d[c.field] = c.value if c.op == "eq" else f"{_op_to_prefix(c.op)}{c.value}"
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class GroupConstraints:
    """Constraints shared by a group of refs."""
    name: str
    refs: list[str] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"refs": self.refs, "constraints": {}}
        for c in self.constraints:
            d["constraints"][c.field] = c.value if c.op == "eq" else f"{_op_to_prefix(c.op)}{c.value}"
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class PatternConstraints:
    """Constraints matching ref patterns like C*, R*."""
    pattern: str
    constraints: list[Constraint] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for c in self.constraints:
            d[c.field] = c.value if c.op == "eq" else f"{_op_to_prefix(c.op)}{c.value}"
        if self.reason:
            d["reason"] = self.reason
        return d

    def matches_ref(self, ref: str) -> bool:
        """Check if a ref matches this pattern."""
        return fnmatch.fnmatch(ref, self.pattern)


def _op_to_prefix(op: str) -> str:
    """Convert an operator to its prefix string."""
    return {
        "gte": ">=", "lte": "<=", "gt": ">", "lt": "<",
        "eq": "=", "ne": "!=",
    }.get(op, "")


# ---------------------------------------------------------------------------
# Constraint file model
# ---------------------------------------------------------------------------

@dataclass
class ConstraintFile:
    """Full constraint file contents."""
    version: int = 1
    refs: dict[str, RefConstraints] = field(default_factory=dict)
    groups: dict[str, GroupConstraints] = field(default_factory=dict)
    patterns: dict[str, PatternConstraints] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Constraint checking — normalization helpers
# ---------------------------------------------------------------------------

# Common imperial/metric package size mappings
_PACKAGE_SIZE_EQUIVALENTS: dict[str, set[str]] = {
    "0402": {"0402", "1005", "1005 metric"},
    "0603": {"0603", "1608", "1608 metric"},
    "0805": {"0805", "2012", "2012 metric"},
    "1206": {"1206", "3216", "3216 metric"},
    "1210": {"1210", "3225", "3225 metric"},
    "1812": {"1812", "4532", "4532 metric"},
    "2220": {"2220", "5750", "5750 metric"},
}


def _normalize_package(pkg: str) -> str:
    """Extract the core package size from strings like '0603 (1608 Metric)'."""
    if not pkg:
        return ""
    # Strip parenthetical metric equivalents
    s = re.sub(r"\s*\(.*?\)\s*", "", pkg).strip().lower()
    return s


def _package_matches(constraint_val: str, part_val: str) -> bool:
    """Check if a package constraint matches a part's package value.

    Handles:
        0603  matches  '0603 (1608 Metric)'
        0603  matches  '1608'
        0603  matches  '0603'
    """
    if not isinstance(constraint_val, str) or not isinstance(part_val, str):
        return False
    if not constraint_val or not part_val:
        return False

    cv = constraint_val.strip().lower()
    pv = part_val.strip().lower()

    # Direct substring match (covers '0603' in '0603 (1608 Metric)')
    if cv in pv:
        return True

    # Check equivalents
    pv_normalized = _normalize_package(part_val)
    for _imperial, equivalents in _PACKAGE_SIZE_EQUIVALENTS.items():
        if cv in equivalents and pv_normalized in equivalents:
            return True

    return False


def _dielectric_matches(constraint_val: str, part_val: str) -> bool:
    """Check if a dielectric constraint matches.

    Handles:
        X7R matches 'X7R'
        X7R matches 'X7R, X5R'  (if it contains X7R)
    """
    if not isinstance(constraint_val, str) or not isinstance(part_val, str):
        return False
    if not constraint_val or not part_val:
        return False
    cv = constraint_val.strip().upper()
    pv = part_val.strip().upper()
    return cv == pv or cv in pv


# ---------------------------------------------------------------------------
# Numeric equality for component value fields
# ---------------------------------------------------------------------------

_NUMERIC_EQ_FIELDS = {"capacitance", "resistance", "voltage", "current", "value"}


def _numeric_values_equal(constraint_val: str, part_val: str, domain: str = "") -> bool:
    """Compare two values numerically for equality.

    Handles unit variations:
        4.7uF == 4.7 µF == 4700nF
        10k == 10000 == 10kΩ

    Falls back to case-insensitive string equality if numeric parsing fails.
    """
    if not isinstance(constraint_val, str) or not isinstance(part_val, str):
        return False
    cv_num = parse_numeric_value(constraint_val, domain)
    pv_num = parse_numeric_value(part_val, domain)
    if cv_num is not None and pv_num is not None:
        # Use relative tolerance for floating point comparison
        if cv_num == 0 and pv_num == 0:
            return True
        if cv_num == 0 or pv_num == 0:
            return False
        return abs(cv_num - pv_num) / max(abs(cv_num), abs(pv_num)) < 0.01  # 1% tolerance
    # Fall back to string comparison
    return constraint_val.strip().lower() == part_val.strip().lower()


# ---------------------------------------------------------------------------
# Constraint checking
# ---------------------------------------------------------------------------

def check_constraint(constraint: Constraint, part_value: str) -> ConstraintResult:
    """Check a single constraint against a part's field value.

    Returns a ConstraintResult with pass/fail and actual value.
    """
    cv = constraint.value
    pv = part_value.strip() if part_value else ""
    is_soft = constraint.field.startswith("prefer_") or constraint.field.startswith("avoid_")

    # Handle special fields
    if constraint.field == "prefer_package":
        # Soft: passes always but warns if not matched
        passed = cv.lower() in pv.lower() if pv else False
        return ConstraintResult(
            constraint=constraint, passed=True, actual_value=pv,
            message=f"Preferred package '{cv}': {'matched' if passed else 'not matched, consider alternatives'}",
            is_soft=True,
        )

    if constraint.field == "avoid_package":
        avoided = cv.lower() not in pv.lower() if pv else True
        return ConstraintResult(
            constraint=constraint, passed=avoided, actual_value=pv,
            message=f"Avoid package '{cv}': {'avoided' if avoided else 'PRESENT, consider alternatives'}",
            is_soft=True,
        )

    if constraint.field == "family":
        # MPN family prefix match
        passed = pv.upper().startswith(cv.upper())
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"Family '{cv}': {'PASS' if passed else 'FAIL'}",
        )

    if constraint.field == "suppliers":
        allowed = [s.strip().lower() for s in cv.split(",")]
        passed = pv.lower() in allowed
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"Suppliers [{cv}]: {'PASS' if passed else 'FAIL'}",
        )

    if constraint.field == "reason":
        # Pure metadata, always passes
        return ConstraintResult(
            constraint=constraint, passed=True, actual_value="",
            message=f"Reason: {cv}",
            is_soft=True,
        )

    # Numeric comparison
    domain = _guess_domain(constraint.field)
    if constraint.op in ("gte", "lte", "gt", "lt"):
        cv_num = parse_numeric_value(cv, domain)
        pv_num = parse_numeric_value(pv, domain)

        if cv_num is None:
            return ConstraintResult(
                constraint=constraint, passed=True, actual_value=pv,
                message=f"Cannot parse constraint value '{cv}', skipping",
                is_soft=True,
            )
        if pv_num is None:
            return ConstraintResult(
                constraint=constraint, passed=False, actual_value=pv,
                message=f"{constraint.field} {_op_to_prefix(constraint.op)}{cv}: FAIL (no numeric value '{pv}')",
            )

        # Tolerance special case: <=1% means 1% or better (smaller number)
        if constraint.op == "gte":
            passed = pv_num >= cv_num
        elif constraint.op == "lte":
            passed = pv_num <= cv_num
        elif constraint.op == "gt":
            passed = pv_num > cv_num
        elif constraint.op == "lt":
            passed = pv_num < cv_num
        else:
            passed = False

        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"{constraint.field} {_op_to_prefix(constraint.op)}{cv}: {'PASS' if passed else 'FAIL'} (actual: {pv})",
        )

    # String operations
    if constraint.op == "eq":
        # Normalized matching for package and dielectric
        if constraint.field == "package":
            passed = _package_matches(cv, pv)
        elif constraint.field == "dielectric":
            passed = _dielectric_matches(cv, pv)
        elif constraint.field in _NUMERIC_EQ_FIELDS:
            passed = _numeric_values_equal(cv, pv, domain)
        else:
            passed = cv.lower() == pv.lower()
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"{constraint.field} = {cv}: {'PASS' if passed else 'FAIL'} (actual: {pv})",
        )

    if constraint.op == "ne":
        passed = cv.lower() != pv.lower()
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"{constraint.field} != {cv}: {'PASS' if passed else 'FAIL'} (actual: {pv})",
        )

    if constraint.op == "contains":
        passed = cv.lower() in pv.lower()
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"{constraint.field} contains '{cv}': {'PASS' if passed else 'FAIL'}",
        )

    if constraint.op == "not_contains":
        passed = cv.lower() not in pv.lower()
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"{constraint.field} not contains '{cv}': {'PASS' if passed else 'FAIL'}",
        )

    if constraint.op == "matches":
        # Pipe-separated alternatives: "MSOP|DFN"
        alternatives = [a.strip().lower() for a in cv.split("|")]
        passed = any(alt in pv.lower() for alt in alternatives)
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"{constraint.field} matches [{cv}]: {'PASS' if passed else 'FAIL'}",
        )

    if constraint.op == "in":
        # Comma-separated list: "X7R,X5R,C0G"
        allowed = [a.strip().lower() for a in cv.split(",")]
        passed = pv.lower() in allowed
        return ConstraintResult(
            constraint=constraint, passed=passed, actual_value=pv,
            message=f"{constraint.field} in [{cv}]: {'PASS' if passed else 'FAIL'}",
        )

    # Unknown op
    return ConstraintResult(
        constraint=constraint, passed=True, actual_value=pv,
        message=f"Unknown operator '{constraint.op}', skipping",
        is_soft=True,
    )


def _get_part_field(part, field_name: str) -> str:
    """Get a field value from a SupplierPart for constraint checking."""
    # Direct attribute
    val = getattr(part, field_name, None)
    if val is not None and val != "":
        return str(val)

    # Mapped fields
    field_map = {
        "voltage": lambda p: (
            getattr(p, "attributes", {}).get("Voltage - Rated", "")
            or getattr(p, "attributes", {}).get("Voltage Rating", "")
            or getattr(p, "attributes", {}).get("Voltage - Supply", "")
        ),
        "dielectric": lambda p: (
            getattr(p, "attributes", {}).get("Dielectric", "")
            or getattr(p, "attributes", {}).get("Temperature Coefficient", "")
        ),
        "value": lambda p: (
            getattr(p, "attributes", {}).get("Capacitance", "")
            or getattr(p, "attributes", {}).get("Resistance", "")
            or getattr(p, "attributes", {}).get("Inductance", "")
            or getattr(p, "description", "")
        ),
        "package": lambda p: (
            getattr(p, "package", "")
            or getattr(p, "supplier_device_package", "")
            or getattr(p, "attributes", {}).get("Package / Case", "")
        ),
        "tolerance": lambda p: (
            getattr(p, "attributes", {}).get("Tolerance", "")
        ),
        "family": lambda p: getattr(p, "mpn", ""),
        "suppliers": lambda p: getattr(p, "supplier", ""),
        "prefer_package": lambda p: (
            getattr(p, "package", "")
            or getattr(p, "supplier_device_package", "")
        ),
        "avoid_package": lambda p: (
            getattr(p, "package", "")
            or getattr(p, "supplier_device_package", "")
        ),
        "temperature": lambda p: (
            getattr(p, "temperature_range", "")
            or getattr(p, "attributes", {}).get("Operating Temperature", "")
        ),
        "temp": lambda p: (
            getattr(p, "temperature_range", "")
            or getattr(p, "attributes", {}).get("Operating Temperature", "")
        ),
        "output_current": lambda p: (
            getattr(p, "attributes", {}).get("Output Current", "")
            or getattr(p, "attributes", {}).get("Current - Output", "")
        ),
    }

    getter = field_map.get(field_name)
    if getter:
        return getter(part) or ""

    # Check attributes dict
    attrs = getattr(part, "attributes", {})
    if field_name in attrs:
        return attrs[field_name] or ""

    return ""


def check_part_constraints(
    constraints: list[Constraint],
    part,
) -> list[ConstraintResult]:
    """Check all constraints against a supplier part.

    Returns a list of ConstraintResult (one per constraint).
    """
    results = []
    for c in constraints:
        pv = _get_part_field(part, c.field)
        result = check_constraint(c, pv)
        results.append(result)
    return results


def apply_constraints_to_results(
    constraints: list[Constraint],
    parts: list,
) -> tuple[list, list[dict]]:
    """Filter supplier parts by constraints.

    Returns (passing_parts, constraint_summary_per_part).
    """
    passing = []
    summaries = []

    for part in parts:
        results = check_part_constraints(constraints, part)
        hard_pass = all(r.passed for r in results if not r.is_soft)

        summaries.append({
            "mpn": getattr(part, "mpn", ""),
            "passed": hard_pass,
            "results": results,
        })

        if hard_pass:
            passing.append(part)

    return passing, summaries


# ---------------------------------------------------------------------------
# Category inference from ref pattern
# ---------------------------------------------------------------------------

def infer_category(ref: str | None = None, description: str | None = None) -> tuple[str, str]:
    """Infer ComponentCategory from ref pattern or description.

    Returns (category_value, confidence).
    """
    from footfindr.core.models import ComponentCategory

    if ref:
        r = ref.upper().strip()
        if r.startswith("C"):
            return ComponentCategory.CAPACITOR.value, "high"
        if r.startswith("R"):
            return ComponentCategory.RESISTOR.value, "high"
        if r.startswith("L"):
            return ComponentCategory.INDUCTOR.value, "high"
        if r.startswith("U"):
            return ComponentCategory.IC.value, "high"
        if r.startswith("D"):
            # Could be diode or LED
            if description and "led" in description.lower():
                return ComponentCategory.LED.value, "medium"
            return ComponentCategory.DIODE.value, "medium"
        if r.startswith("Q"):
            return ComponentCategory.TRANSISTOR.value, "high"
        if r.startswith("J") or r.startswith("P"):
            return ComponentCategory.CONNECTOR.value, "medium"
        if r.startswith("Y") or r.startswith("X"):
            return ComponentCategory.CRYSTAL.value, "medium"

    if description:
        d = description.lower()
        if any(w in d for w in ("capacitor", "cap ", "mlcc")):
            return ComponentCategory.CAPACITOR.value, "medium"
        if any(w in d for w in ("resistor", "res ")):
            return ComponentCategory.RESISTOR.value, "medium"
        if any(w in d for w in ("inductor", "coil", "choke")):
            return ComponentCategory.INDUCTOR.value, "medium"
        if any(w in d for w in ("ic ", "regulator", "mcu", "adc", "dac", "amplifier", "op amp", "ldo")):
            return ComponentCategory.IC.value, "medium"

    return ComponentCategory.OTHER.value, "review"


# ---------------------------------------------------------------------------
# Constraint manager — YAML persistence
# ---------------------------------------------------------------------------

class ConstraintManager:
    """Manage project-local constraints."""

    def __init__(self, workspace: Path | None = None) -> None:
        from footfindr.config import get_workspace as _gw
        ws = Path(workspace) if workspace else _gw()
        self._file = ws / "constraints.yaml"

    def load(self) -> ConstraintFile:
        """Load constraints from YAML file."""
        if not self._file.exists():
            return ConstraintFile()

        try:
            data = yaml.safe_load(self._file.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Failed to load constraints: {e}")
            return ConstraintFile()

        if not data or not isinstance(data, dict):
            return ConstraintFile()

        cf = ConstraintFile(version=data.get("version", 1))

        # Parse refs
        for ref, fields in data.get("refs", {}).items():
            if not isinstance(fields, dict):
                continue
            reason = fields.pop("reason", None) if isinstance(fields.get("reason"), str) else None
            constraints = []
            for fname, fval in fields.items():
                if fname == "reason":
                    reason = fval
                    continue
                c = Constraint.from_field_value(fname, str(fval))
                constraints.append(c)
            cf.refs[ref] = RefConstraints(ref=ref, constraints=constraints, reason=reason)

        # Parse groups
        for gname, gdata in data.get("groups", {}).items():
            if not isinstance(gdata, dict):
                continue
            refs = gdata.get("refs", [])
            reason = gdata.get("reason")
            constraints = []
            for fname, fval in gdata.get("constraints", {}).items():
                c = Constraint.from_field_value(fname, str(fval))
                constraints.append(c)
            cf.groups[gname] = GroupConstraints(
                name=gname, refs=refs, constraints=constraints, reason=reason,
            )

        # Parse patterns
        for pattern, fields in data.get("patterns", {}).items():
            if not isinstance(fields, dict):
                continue
            reason = fields.get("reason")
            constraints = []
            for fname, fval in fields.items():
                if fname == "reason":
                    continue
                c = Constraint.from_field_value(fname, str(fval))
                constraints.append(c)
            cf.patterns[pattern] = PatternConstraints(
                pattern=pattern, constraints=constraints, reason=reason,
            )

        return cf

    def save(self, cf: ConstraintFile) -> None:
        """Save constraints to YAML file."""
        data: dict[str, Any] = {"version": cf.version}

        if cf.refs:
            data["refs"] = {}
            for ref, rc in cf.refs.items():
                data["refs"][ref] = rc.to_dict()

        if cf.groups:
            data["groups"] = {}
            for gname, gc in cf.groups.items():
                data["groups"][gname] = gc.to_dict()

        if cf.patterns:
            data["patterns"] = {}
            for pat, pc in cf.patterns.items():
                data["patterns"][pat] = pc.to_dict()

        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def get_constraints_for(self, ref: str) -> list[Constraint]:
        """Get merged constraints for a ref.

        Priority: exact ref > group > pattern.
        Later entries override earlier ones for same field.
        """
        cf = self.load()
        merged: dict[str, Constraint] = {}

        # 1. Pattern constraints (lowest priority)
        for pat, pc in cf.patterns.items():
            if pc.matches_ref(ref):
                for c in pc.constraints:
                    merged[c.field] = c

        # 2. Group constraints (middle priority)
        for gname, gc in cf.groups.items():
            if ref in gc.refs:
                for c in gc.constraints:
                    merged[c.field] = c

        # 3. Exact ref constraints (highest priority)
        if ref in cf.refs:
            for c in cf.refs[ref].constraints:
                merged[c.field] = c

        return list(merged.values())

    def set_constraint(self, ref: str, field_name: str, value: str, *, reason: str | None = None) -> None:
        """Set a constraint on a ref."""
        cf = self.load()
        c = Constraint.from_field_value(field_name, value, reason=reason)

        if ref not in cf.refs:
            cf.refs[ref] = RefConstraints(ref=ref)

        # Replace existing constraint for same field
        cf.refs[ref].constraints = [
            x for x in cf.refs[ref].constraints if x.field != c.field
        ]
        cf.refs[ref].constraints.append(c)
        self.save(cf)

    def remove_constraint(self, ref: str, field_name: str) -> bool:
        """Remove a constraint from a ref. Returns True if removed."""
        cf = self.load()
        if ref not in cf.refs:
            return False
        before = len(cf.refs[ref].constraints)
        cf.refs[ref].constraints = [
            c for c in cf.refs[ref].constraints if c.field != field_name
        ]
        if len(cf.refs[ref].constraints) < before:
            # Remove ref entirely if no constraints left
            if not cf.refs[ref].constraints and not cf.refs[ref].reason:
                del cf.refs[ref]
            self.save(cf)
            return True
        return False

    def clear_ref(self, ref: str) -> bool:
        """Remove all constraints for a ref."""
        cf = self.load()
        if ref in cf.refs:
            del cf.refs[ref]
            self.save(cf)
            return True
        return False

    def check_part(self, ref: str, part) -> list[ConstraintResult]:
        """Check a part against all constraints for a ref."""
        constraints = self.get_constraints_for(ref)
        return check_part_constraints(constraints, part)

    def build_search_query(self, ref: str, schematic_value: str | None = None) -> tuple[str, list[Constraint]]:
        """Build a high-specificity search query from constraints + schematic fields.

        For a capacitor with Value=0.1uF and constraints voltage>=25V, dielectric=X7R,
        package=0603, this generates:
            "0.1uF 25V X7R 0603 ceramic capacitor"

        Returns (query_string, constraints_used).
        """
        constraints = self.get_constraints_for(ref)
        if not constraints and not schematic_value:
            return "", []

        parts: list[str] = []
        _numeric_values_seen: list[float] = []  # for deduplication

        def _add_if_not_numeric_dup(val: str, field: str) -> None:
            """Add value to parts, deduplicating by numeric equivalence."""
            domain = _guess_domain(field)
            num = parse_numeric_value(val, domain)
            if num is not None:
                # Check if this numeric value already in parts
                for seen in _numeric_values_seen:
                    if abs(seen - num) / max(abs(num), 1e-30) < 0.01:  # 1% tolerance
                        return  # Skip — numeric duplicate
                _numeric_values_seen.append(num)
            elif val in parts:
                return  # exact string duplicate
            parts.append(val)

        # 1. Include schematic value field if available
        if schematic_value and schematic_value.strip():
            sv = schematic_value.strip()
            _add_if_not_numeric_dup(sv, "value")

        # 2. Include constraint values in priority order
        _QUERY_FIELDS = [
            ("capacitance", ("eq", "gte", "lte")),
            ("resistance", ("eq", "gte", "lte")),
            ("value", ("eq", "gte", "lte")),
            ("voltage", ("eq", "gte", "gt", "lte", "lt")),
            ("current", ("eq", "gte", "gt", "lte", "lt")),
            ("dielectric", ("eq", "in", "matches")),
            ("package", ("eq", "matches")),
            ("tolerance", ("eq", "lte")),
            ("family", ("eq",)),
            ("temperature", ("eq", "gte")),
        ]

        used_fields: set[str] = set()
        for field_name, allowed_ops in _QUERY_FIELDS:
            for c in constraints:
                if c.field == field_name and c.op in allowed_ops and field_name not in used_fields:
                    val = c.value.strip()
                    if val:
                        _add_if_not_numeric_dup(val, field_name)
                    used_fields.add(field_name)

        # 3. Add category hint with more specific terms
        cat, _ = infer_category(ref)
        _CATEGORY_SEARCH_TERMS = {
            "capacitor": "ceramic capacitor",
            "resistor": "resistor",
            "inductor": "inductor",
            "ic": "",  # ICs are too diverse for a generic term
            "diode": "diode",
            "led": "LED",
            "transistor": "transistor",
            "connector": "connector",
            "crystal": "crystal",
        }
        if cat and cat != "other":
            term = _CATEGORY_SEARCH_TERMS.get(cat, cat)
            if term:
                parts.append(term)

        # If still empty after all that, fall back to category
        if not parts and cat and cat != "other":
            parts.append(cat)

        return " ".join(parts), constraints

    def build_fallback_queries(self, ref: str, schematic_value: str | None = None) -> list[str]:
        """Build progressively broader fallback queries.

        Returns a list of query strings, from most specific to broadest.
        Each should be used as its own cache key.
        """
        primary, constraints = self.build_search_query(ref, schematic_value)
        if not primary:
            return []

        cat, _ = infer_category(ref)
        cat_term = {
            "capacitor": "ceramic capacitor",
            "resistor": "resistor",
            "inductor": "inductor",
            "diode": "diode",
            "transistor": "transistor",
        }.get(cat or "", "")

        # Collect value parts for fallback construction
        value_parts: list[str] = []
        if schematic_value and schematic_value.strip():
            value_parts.append(schematic_value.strip())

        voltage_val = None
        diel_val = None
        pkg_val = None
        for c in constraints:
            if c.field == "capacitance" and c.value.strip():
                if c.value.strip() not in value_parts:
                    value_parts.append(c.value.strip())
            elif c.field == "resistance" and c.value.strip():
                if c.value.strip() not in value_parts:
                    value_parts.append(c.value.strip())
            elif c.field == "voltage":
                voltage_val = c.value.strip()
            elif c.field == "dielectric":
                diel_val = c.value.strip()
            elif c.field == "package":
                pkg_val = c.value.strip()

        fallbacks: list[str] = []
        base = " ".join(value_parts) if value_parts else ""

        # Fallback 1: drop category term (keep all constraint values)
        fb1_parts = value_parts[:]
        if voltage_val:
            fb1_parts.append(voltage_val)
        if diel_val:
            fb1_parts.append(diel_val)
        if pkg_val:
            fb1_parts.append(pkg_val)
        fb1 = " ".join(fb1_parts)
        if fb1 and fb1 != primary:
            fallbacks.append(fb1)

        # Fallback 2: drop dielectric
        fb2_parts = value_parts[:]
        if voltage_val:
            fb2_parts.append(voltage_val)
        if pkg_val:
            fb2_parts.append(pkg_val)
        if cat_term:
            fb2_parts.append(cat_term.split()[-1])  # just "capacitor"
        fb2 = " ".join(fb2_parts)
        if fb2 and fb2 != primary and fb2 not in fallbacks:
            fallbacks.append(fb2)

        # Fallback 3: just value + package + category
        fb3_parts = value_parts[:]
        if pkg_val:
            fb3_parts.append(pkg_val)
        if cat_term:
            fb3_parts.append(cat_term.split()[-1])
        fb3 = " ".join(fb3_parts)
        if fb3 and fb3 != primary and fb3 not in fallbacks:
            fallbacks.append(fb3)

        return fallbacks

    # Group management

    def create_group(self, name: str, reason: str | None = None) -> None:
        cf = self.load()
        if name not in cf.groups:
            cf.groups[name] = GroupConstraints(name=name, reason=reason)
            self.save(cf)

    def add_to_group(self, name: str, refs: list[str]) -> None:
        cf = self.load()
        if name not in cf.groups:
            cf.groups[name] = GroupConstraints(name=name)
        for r in refs:
            if r not in cf.groups[name].refs:
                cf.groups[name].refs.append(r)
        self.save(cf)

    def set_group_constraint(self, name: str, field_name: str, value: str) -> None:
        cf = self.load()
        if name not in cf.groups:
            cf.groups[name] = GroupConstraints(name=name)
        c = Constraint.from_field_value(field_name, value)
        cf.groups[name].constraints = [
            x for x in cf.groups[name].constraints if x.field != c.field
        ]
        cf.groups[name].constraints.append(c)
        self.save(cf)
