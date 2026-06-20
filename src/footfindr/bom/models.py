"""BOM data models and profile definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BOMColumn:
    """A single column in a BOM profile."""
    name: str           # Output column header
    source: str         # Source field path (e.g. "internal_pn", "specs.voltage_rating")
    default: str = ""   # Default if source field is missing


@dataclass
class BOMProfile:
    """A configurable BOM export profile."""
    name: str
    description: str = ""
    columns: list[BOMColumn] = field(default_factory=list)
    group_by: str = "internal_pn"  # "internal_pn" or "value_footprint"
    exclude_dnp: bool = True
    warn_missing: list[str] = field(default_factory=lambda: ["Footprint", "InternalPN"])


@dataclass
class BOMRow:
    """A single row in a generated BOM."""
    quantity: int = 0
    references: list[str] = field(default_factory=list)
    value: str = ""
    internal_pn: str = ""
    mpn: str = ""
    manufacturer: str = ""
    footprint: str = ""
    package: str = ""
    voltage_rating: str = ""
    power_rating: str = ""
    tolerance: str = ""
    dielectric: str = ""
    notes: str = ""
    lcsc_pn: str = ""


@dataclass
class BOMReport:
    """Generated BOM report."""
    rows: list[BOMRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    profile_name: str = ""
    schematic_path: str = ""
    total_parts: int = 0
    total_unique: int = 0


@dataclass
class ProjectIssue:
    """A single issue found during project review or BOM check."""
    severity: str           # "INFO", "WARN", "FAIL", "BLOCKER"
    code: str               # e.g. "MISSING_MPN", "CONSTRAINT_PACKAGE"
    ref: str | None         # schematic ref or None for project-level
    field: str | None       # relevant field name
    message: str            # human-readable explanation
    suggested_action: str | None = None  # e.g. "ff supplier search ..."
    plan_available: bool = False  # can --fix-plan generate a fix?

    def to_dict(self) -> dict:
        d = {
            "severity": self.severity,
            "code": self.code,
            "ref": self.ref,
            "field": self.field,
            "message": self.message,
        }
        if self.suggested_action:
            d["suggested_action"] = self.suggested_action
        if self.plan_available:
            d["plan_available"] = True
        return d

