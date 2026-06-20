"""Raw vendor CSV ingestion for FootFindr.

Imports vendor capacitor/resistor catalogs from CSV files, auto-detects
or maps column names, and stores the result as a raw vendor library.
Ingested parts are marked ``status=raw, library_kind=raw_vendor``.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from footfindr.core.models import (
    ComponentCategory,
    ElectricalSpecs,
    PartRecord,
    PartStatus,
)
from footfindr.libraries.manager import LibraryManager


# ---------------------------------------------------------------------------
# Column name auto-detection
# ---------------------------------------------------------------------------

# Canonical field -> list of common CSV header names (case-insensitive)
_COLUMN_ALIASES: dict[str, list[str]] = {
    "manufacturer": [
        "manufacturer", "mfr", "mfg", "brand", "vendor",
    ],
    "mpn": [
        "mpn", "mfr part number", "manufacturer part number", "part number",
        "mfr_pn", "part_number", "pn",
    ],
    "category": [
        "category", "type", "component type", "part type",
    ],
    "capacitance": [
        "capacitance", "cap", "capacitance (f)", "capacitance_value",
        "nominal capacitance",
    ],
    "resistance": [
        "resistance", "res", "resistance (ohm)", "resistance_value",
        "nominal resistance",
    ],
    "voltage_rating": [
        "voltage rating", "voltage_rating", "rated voltage", "vmax",
        "voltage", "voltage (v)",
    ],
    "power_rating": [
        "power rating", "power_rating", "rated power", "power (w)",
        "power",
    ],
    "current_rating": [
        "current rating", "current_rating", "rated current", "imax",
    ],
    "tolerance": [
        "tolerance", "tol", "tolerance (%)",
    ],
    "dielectric": [
        "dielectric", "tc", "temperature coefficient", "dielectric type",
        "class",
    ],
    "package": [
        "package", "case", "case/package", "case size", "package size",
        "size", "footprint", "package_code",
    ],
    "footprint": [
        "kicad footprint", "footprint_ref", "fp",
    ],
    "datasheet_url": [
        "datasheet", "datasheet url", "datasheet_url", "ds_url",
    ],
    "description": [
        "description", "desc", "product description",
    ],
}

# Preset column maps for known vendors
VENDOR_PRESETS: dict[str, dict[str, str]] = {
    "murata": {
        "Part Number": "mpn",
        "Capacitance": "capacitance",
        "Rated Voltage": "voltage_rating",
        "Temperature Characteristic": "dielectric",
        "Tolerance": "tolerance",
        "Size": "package",
    },
    "tdk": {
        "Part No.": "mpn",
        "Capacitance": "capacitance",
        "Rated Voltage": "voltage_rating",
        "Temperature Characteristics": "dielectric",
        "Capacitance Tolerance": "tolerance",
        "Size (mm)": "package",
    },
    "kemet": {
        "Part Number": "mpn",
        "Capacitance": "capacitance",
        "Voltage": "voltage_rating",
        "Dielectric": "dielectric",
        "Tolerance": "tolerance",
        "Case Size": "package",
    },
}


def _auto_detect_columns(headers: list[str]) -> dict[str, str]:
    """Auto-detect column mapping from CSV headers.

    Returns ``{csv_header: canonical_field_name}``.
    """
    mapping: dict[str, str] = {}
    for header in headers:
        h_lower = header.strip().lower()
        for canonical, aliases in _COLUMN_ALIASES.items():
            if h_lower in aliases:
                mapping[header] = canonical
                break
    return mapping


# ---------------------------------------------------------------------------
# Ingestor
# ---------------------------------------------------------------------------

class CsvIngestor:
    """Ingests a CSV file into FootFindr PartRecord objects."""

    def __init__(
        self,
        *,
        vendor: Optional[str] = None,
        column_map: Optional[dict[str, str]] = None,
        default_category: str = "capacitor",
        default_manufacturer: Optional[str] = None,
    ) -> None:
        self._vendor = vendor
        self._column_map = column_map
        self._default_category = default_category
        self._default_manufacturer = default_manufacturer or (vendor or "").title()

    def ingest(self, csv_path: str | Path) -> list[PartRecord]:
        """Read a CSV file and return a list of raw PartRecords."""
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return []

            headers = list(reader.fieldnames)

            # Determine column mapping
            if self._column_map:
                col_map = self._column_map
            elif self._vendor and self._vendor.lower() in VENDOR_PRESETS:
                col_map = VENDOR_PRESETS[self._vendor.lower()]
            else:
                col_map = _auto_detect_columns(headers)

            records: list[PartRecord] = []
            for row_num, row in enumerate(reader, start=2):
                record = self._row_to_record(row, col_map, row_num)
                if record:
                    records.append(record)

        return records

    def _row_to_record(
        self,
        row: dict[str, str],
        col_map: dict[str, str],
        row_num: int,
    ) -> Optional[PartRecord]:
        """Convert a single CSV row to a PartRecord."""
        mapped: dict[str, str] = {}
        for csv_col, canonical in col_map.items():
            val = row.get(csv_col, "").strip()
            if val:
                mapped[canonical] = val

        # Must have at least an MPN
        mpn = mapped.get("mpn")
        if not mpn:
            return None

        category_str = mapped.get("category", self._default_category)
        try:
            cat = ComponentCategory(category_str.lower())
        except ValueError:
            cat = ComponentCategory.OTHER

        manufacturer = mapped.get("manufacturer", self._default_manufacturer)

        # Build internal PN from MPN
        internal_pn = f"RAW-{mpn}"

        specs = ElectricalSpecs(
            capacitance=mapped.get("capacitance"),
            resistance=mapped.get("resistance"),
            voltage_rating=mapped.get("voltage_rating"),
            power_rating=mapped.get("power_rating"),
            current_rating=mapped.get("current_rating"),
            tolerance=mapped.get("tolerance"),
            dielectric=mapped.get("dielectric"),
        )

        return PartRecord(
            internal_pn=internal_pn,
            category=cat,
            manufacturer=manufacturer,
            mpn=mpn,
            description=mapped.get("description"),
            value=mapped.get("capacitance") or mapped.get("resistance"),
            status=PartStatus.RAW,
            approved=False,
            specs=specs,
            package=mapped.get("package"),
            footprint=mapped.get("footprint"),
        )


def ingest_csv(
    csv_path: str | Path,
    library_name: str,
    manager: LibraryManager,
    *,
    vendor: Optional[str] = None,
    column_map: Optional[dict[str, str]] = None,
    default_category: str = "capacitor",
) -> tuple[int, Path]:
    """High-level ingest: read CSV, store as raw vendor library, return count and path."""
    ingestor = CsvIngestor(
        vendor=vendor,
        column_map=column_map,
        default_category=default_category,
        default_manufacturer=vendor,
    )
    records = ingestor.ingest(csv_path)
    path = manager.save_raw_library(library_name, records)
    return len(records), path
