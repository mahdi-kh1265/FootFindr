"""Base protocol and result type for vendor CSV parsers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from footfindr.core.models import PartRecord


# ---------------------------------------------------------------------------
# Parse result — returned by every parser
# ---------------------------------------------------------------------------

@dataclass
class VendorParseResult:
    """Result of parsing a vendor CSV file.

    Every parser must return this structure so that ``packs.py`` can build
    the pack directory and normalization report without knowing any
    vendor-specific details.
    """

    records: list[PartRecord] = field(default_factory=list)

    # Row accounting
    raw_rows: int = 0
    imported_parts: int = 0
    skipped_rows: int = 0
    skip_reasons: list[str] = field(default_factory=list)

    # Distribution counters
    product_status_counts: Counter = field(default_factory=Counter)
    package_counts: Counter = field(default_factory=Counter)
    voltage_counts: Counter = field(default_factory=Counter)
    dielectric_counts: Counter = field(default_factory=Counter)
    unmapped_size_codes: set[str] = field(default_factory=set)
    unmapped_dimension_pairs: set[str] = field(default_factory=set)

    # Samples for the normalization report
    example_mpns: list[str] = field(default_factory=list)

    # Warnings (non-fatal)
    warnings: list[str] = field(default_factory=list)

    # Parser metadata
    parser_version: str = "1.0.0"

    def to_report_dict(self) -> dict[str, Any]:
        """Export as a dict for the ``normalization_report.yaml``."""
        return {
            "raw_rows": self.raw_rows,
            "imported_parts": self.imported_parts,
            "skipped_rows": self.skipped_rows,
            "product_status_counts": dict(self.product_status_counts),
            "package_counts": dict(self.package_counts.most_common()),
            "voltage_counts": dict(self.voltage_counts.most_common()),
            "dielectric_counts": dict(self.dielectric_counts.most_common()),
            "unmapped_size_codes": sorted(self.unmapped_size_codes),
            "unmapped_dimension_pairs": sorted(self.unmapped_dimension_pairs),
            "warnings": (self.warnings + self.skip_reasons)[:30],
            "examples": {
                "imported_mpns": self.example_mpns[:10],
                "skipped_rows": self.skip_reasons[:5],
            },
            "parser_version": self.parser_version,
        }


# ---------------------------------------------------------------------------
# Parser protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class VendorParser(Protocol):
    """Protocol that every vendor-specific CSV parser must satisfy.

    Implement this protocol, then call
    ``register_parser("my-vendor", MyVendorParser)`` at module level.
    """

    # --- Metadata that the pack builder reads ---
    vendor: str           # e.g. "Murata"
    series: str           # e.g. "GRM"
    category: str         # e.g. "capacitor"
    display_name: str     # e.g. "Murata GRM MLCC Library"
    pack_slug: str        # e.g. "footfindr-lib-murata-grm"

    def parse(
        self,
        source_path: "str | __import__('pathlib').Path",
        *,
        limit: int | None = None,
        source_file: str | None = None,
        source_pack: str | None = None,
    ) -> VendorParseResult:
        """Parse a vendor CSV and return a ``VendorParseResult``."""
        ...
