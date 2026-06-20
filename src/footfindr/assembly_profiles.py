"""Assembly profile definitions for project review.

Deterministic rules for evaluating BOM buildability against
specific assembly contexts (prototype, hand-assembly, JLCPCB, POSM).

Profiles provide base rules; constraints always override.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PackageRule:
    """A single package pattern rule."""
    pattern: str          # e.g. "BGA", "QFN", "0201"
    severity: str         # "INFO", "WARN", "FAIL"
    reason: str           # why this pattern is flagged


@dataclass
class AssemblyProfile:
    """Defines assembly-context rules for project review."""
    name: str
    description: str
    package_rules: list[PackageRule] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)  # enabled check codes

    def check_package(self, package_str: str) -> PackageRule | None:
        """Check if a package string triggers any rule.

        Returns the highest-severity matching rule, or None.
        """
        if not package_str:
            return None
        pkg = package_str.upper()
        matches: list[PackageRule] = []
        for rule in self.package_rules:
            if rule.pattern.upper() in pkg:
                matches.append(rule)
        if not matches:
            return None
        # Return highest severity
        severity_order = {"FAIL": 3, "WARN": 2, "INFO": 1}
        return max(matches, key=lambda r: severity_order.get(r.severity, 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "package_rules": [
                {"pattern": r.pattern, "severity": r.severity, "reason": r.reason}
                for r in self.package_rules
            ],
            "checks": self.checks,
        }


# ---------------------------------------------------------------------------
# Risk thresholds (centralized for future configurability)
# ---------------------------------------------------------------------------

RISK_THRESHOLDS = {
    "low_stock": 100,
    "long_lead_time_days": 14,
    "high_moq_multiplier": 2,
}


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

BUILTIN_PROFILES: dict[str, AssemblyProfile] = {
    "prototype": AssemblyProfile(
        name="prototype",
        description="Prototype-friendly: prefer inspectable, reworkable packages",
        package_rules=[
            PackageRule("BGA", "WARN",
                        "BGA requires reflow and X-ray inspection; "
                        "difficult to prototype and rework."),
            PackageRule("QFN", "WARN",
                        "QFN has hidden pads; harder to inspect/rework "
                        "than MSOP/SOIC."),
            PackageRule("DFN", "WARN",
                        "DFN has hidden pads; harder to inspect/rework "
                        "than MSOP/SOIC."),
            PackageRule("0201", "WARN",
                        "0201 passives are very small; consider 0402+ "
                        "for prototype."),
            PackageRule("0402", "INFO",
                        "0402 passives are small; 0603+ is easier for "
                        "prototype hand-rework."),
        ],
        checks=[
            "obsolete", "nrnd", "low_stock", "single_source",
            "missing_datasheet", "no_approved_record",
        ],
    ),
    "hand-assembly": AssemblyProfile(
        name="hand-assembly",
        description="Hand soldering: fail on BGA, warn on QFN/DFN/0201",
        package_rules=[
            PackageRule("BGA", "FAIL",
                        "BGA cannot be hand-soldered. Requires reflow "
                        "oven and X-ray."),
            PackageRule("QFN", "WARN",
                        "QFN has hidden ground/thermal pads; requires "
                        "hot air or reflow for reliable soldering."),
            PackageRule("DFN", "WARN",
                        "DFN has hidden pads; requires hot air or "
                        "reflow for reliable soldering."),
            PackageRule("0201", "FAIL",
                        "0201 (0.6mm × 0.3mm) is too small for reliable "
                        "hand soldering."),
            PackageRule("0402", "WARN",
                        "0402 (1.0mm × 0.5mm) is very small; use 0603+ "
                        "if possible for hand soldering."),
            PackageRule("EXPOSED", "WARN",
                        "Exposed pad requires reflow or hot air; "
                        "difficult with soldering iron alone."),
        ],
        checks=["package_hand_solder"],
    ),
    "jlcpcb": AssemblyProfile(
        name="jlcpcb",
        description="JLCPCB assembly: check LCSC codes, basic/extended",
        package_rules=[],
        checks=[
            "lcsc_exists", "jlc_exact_match", "jlc_category",
            "package_supported",
        ],
    ),
    "posm": AssemblyProfile(
        name="posm",
        description="POSM defaults: approved library, multi-source, "
                    "active lifecycle, datasheet required",
        package_rules=[],
        checks=[
            "approved_library", "multi_source", "active_lifecycle",
            "has_datasheet", "prototype_friendly_default", "constraints",
        ],
    ),
}


def get_profile(name: str) -> AssemblyProfile:
    """Get an assembly profile by name.

    Raises KeyError if not found.
    """
    if name in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[name]
    raise KeyError(
        f"Assembly profile '{name}' not found. "
        f"Available: {', '.join(BUILTIN_PROFILES)}"
    )


def list_profiles() -> list[AssemblyProfile]:
    """List all available assembly profiles."""
    return list(BUILTIN_PROFILES.values())
