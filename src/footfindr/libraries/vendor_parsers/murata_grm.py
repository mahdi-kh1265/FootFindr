"""Murata GRM MLCC capacitor parser.

Handles both the real Murata product-search CSV export (35 columns, 9k+ parts)
and the simplified sample fixture (10 columns).

Vendor-specific quirks handled:
  - Column name mapping (real vs fixture)
  - MPN size-code → EIA package decoding (37 codes)
  - ``Vdc`` voltage suffix cleanup
  - ``µ`` / ``μ`` capacitance normalization
  - ``±`` / mojibake tolerance cleanup
  - Comma-separated thousands in voltage (``1,000Vdc``)
"""

from __future__ import annotations

import csv
import datetime
import re
from pathlib import Path

from footfindr.core.models import (
    ComponentCategory,
    ElectricalSpecs,
    PartRecord,
    PartStatus,
)
from footfindr.libraries.vendor_parsers import register_parser
from footfindr.libraries.vendor_parsers.base import VendorParseResult


# ---------------------------------------------------------------------------
# Package mapping: EIA imperial → KiCad footprint
# ---------------------------------------------------------------------------

PACKAGE_FOOTPRINT_MAP: dict[str, str] = {
    "0201": "Capacitor_SMD:C_0201_0603Metric",
    "0402": "Capacitor_SMD:C_0402_1005Metric",
    "0603": "Capacitor_SMD:C_0603_1608Metric",
    "0805": "Capacitor_SMD:C_0805_2012Metric",
    "1206": "Capacitor_SMD:C_1206_3216Metric",
    "1210": "Capacitor_SMD:C_1210_3225Metric",
    "1808": "Capacitor_SMD:C_1808_4520Metric",
    "1812": "Capacitor_SMD:C_1812_4532Metric",
    "2220": "Capacitor_SMD:C_2220_5650Metric",
}

# ---------------------------------------------------------------------------
# Murata MPN size code → EIA package
# ---------------------------------------------------------------------------

MURATA_SIZE_CODE_TO_EIA: dict[str, str] = {
    # 008004 class (0.25 × 0.125 mm)
    "011": "008004",
    # 01005 class (0.4 × 0.2 mm)
    "022": "01005", "02Y": "01005",
    # 0201 class (0.6 × 0.3 mm)
    "031": "0201", "032": "0201", "033": "0201", "035": "0201", "MDX": "0201",
    # 0402 class (1.0 × 0.5 mm)
    "152": "0402", "153": "0402", "155": "0402", "158": "0402",
    # 0603 class (1.6 × 0.8 mm)
    "185": "0603", "186": "0603", "187": "0603", "188": "0603",
    "JN6": "0603", "JN7": "0603",
    # 0805 class (2.0 × 1.25 mm)
    "216": "0805", "219": "0805", "21A": "0805", "21B": "0805",
    # 1206 class (3.2 × 1.6 mm)
    "319": "1206", "31A": "1206", "31B": "1206", "31C": "1206", "31M": "1206",
    # 1210 class (3.2 × 2.5 mm)
    "32A": "1210", "32B": "1210", "32D": "1210", "32E": "1210", "32Q": "1210",
    # 1808 class (4.5 × 2.0 mm)
    "42A": "1808",
    # 1812 class (4.5 × 3.2 mm)
    "43D": "1812", "43Q": "1812",
    # 2220 class (5.7 × 5.0 mm)
    "55D": "2220", "55Q": "2220",
}

# Fixture "Size" column to EIA
FIXTURE_SIZE_TO_EIA: dict[str, str] = {
    "0201": "0201", "0402": "0402", "0603": "0603", "0805": "0805",
    "1206": "1206", "1210": "1210", "1812": "1812", "2220": "2220",
    "1.0x0.5": "0402", "1.6x0.8": "0603", "2.0x1.25": "0805",
    "3.2x1.6": "1206", "3.2x2.5": "1210", "4.5x3.2": "1812",
}

# ---------------------------------------------------------------------------
# Column aliases  (real CSV name → canonical)
# ---------------------------------------------------------------------------

_COLUMN_ALIASES: dict[str, str] = {
    "part number": "mpn", "part_number": "mpn", "partnumber": "mpn",
    "product status": "product_status", "productstatus": "product_status",
    "product_status": "product_status",
    "capacitance": "capacitance",
    "tolerance of capacitance": "tolerance", "tolerance": "tolerance",
    "rated voltage dc": "voltage_rating", "rated voltage": "voltage_rating",
    "rated_voltage": "voltage_rating",
    "temperature characteristics": "dielectric",
    "temperature characteristic": "dielectric",
    "temperature_characteristic": "dielectric",
    "size": "size",
    "length": "length", "length (mm)": "length", "length_mm": "length",
    "width": "width", "width (mm)": "width", "width_mm": "width",
    "thickness": "height", "thickness(max.)": "height_max",
    "height": "height", "height (mm)": "height", "height_mm": "height",
    "operating temperature": "operating_temp",
    "characteristic": "characteristic",
    "equivalent series resistance (esr)": "esr", "dc resistance": "dcr",
    "datasheet url": "datasheet_url",
    "specific applications": "applications",
    "end of production": "end_of_production",
    "last time order due": "last_time_order",
    "product name": "product_name",
}


def _normalize_column_name(raw: str) -> str:
    return _COLUMN_ALIASES.get(raw.strip().lower(), raw.strip().lower())


# ---------------------------------------------------------------------------
# Value normalizers
# ---------------------------------------------------------------------------

def _normalize_voltage(raw: str) -> str:
    """``16Vdc`` → ``16V``, ``1,000Vdc`` → ``1000V``."""
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"[Vv][Dd][Cc]$", "V", s)
    s = re.sub(r"[Vv][Aa][Cc]$", "V", s)
    s = s.replace(",", "")
    if s and not s.upper().endswith("V"):
        s += "V"
    return s


def _normalize_tolerance(raw: str) -> str:
    """``±10%`` → ``10%``, ``±0.5pF`` → ``0.5pF``; handles mojibake."""
    if not raw:
        return ""
    s = raw.strip()
    for ch in ("±", "Â±", "Â", "\ufffd", "�"):
        s = s.replace(ch, "")
    s = s.lstrip("±").strip()
    return s


def _normalize_capacitance(raw: str) -> str:
    """Normalize µ variants to ``u``."""
    if not raw:
        return ""
    s = raw.strip()
    s = s.replace("µ", "u").replace("μ", "u").replace("\u03bc", "u")
    return s


def _extract_package_from_mpn(mpn: str) -> str | None:
    """GRM MPNs encode body size at chars 3-5."""
    if not mpn or len(mpn) < 6 or not mpn.upper().startswith("GRM"):
        return None
    return MURATA_SIZE_CODE_TO_EIA.get(mpn[3:6])


def _package_to_footprint(package: str | None) -> str | None:
    if package and package in PACKAGE_FOOTPRINT_MAP:
        return PACKAGE_FOOTPRINT_MAP[package]
    return None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class MurataGRMParser:
    """Murata GRM MLCC CSV parser.

    Conforms to the ``VendorParser`` protocol.
    """

    vendor = "Murata"
    series = "GRM"
    category = "capacitor"
    display_name = "Murata GRM MLCC Library"
    pack_slug = "footfindr-lib-murata-grm"

    def parse(
        self,
        source_path: str | Path,
        *,
        limit: int | None = None,
        source_file: str | None = None,
        source_pack: str | None = None,
    ) -> VendorParseResult:
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        fname = source_file or path.name
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        source_library = f"{self.vendor}-{self.series}"
        result = VendorParseResult(parser_version="1.0.0")

        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return result

            col_map = {raw: _normalize_column_name(raw) for raw in reader.fieldnames}

            for row_num, row in enumerate(reader, start=2):
                if limit and len(result.records) >= limit:
                    break
                result.raw_rows += 1

                mapped: dict[str, str] = {}
                for raw_col, val in row.items():
                    canonical = col_map.get(raw_col, raw_col)
                    if val and val.strip():
                        mapped[canonical] = val.strip()

                rec = self._row_to_record(mapped, row_num, fname, source_library, source_pack, now)
                if rec:
                    result.records.append(rec)
                    result.imported_parts += 1
                    self._collect_stats(result, rec, mapped)
                else:
                    result.skipped_rows += 1
                    mpn = mapped.get("mpn", "(no MPN)")
                    result.skip_reasons.append(f"Row {row_num}: skipped ({mpn})")

        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(
        mapped: dict[str, str],
        row_num: int,
        source_file: str,
        source_library: str,
        source_pack: str | None,
        now: str,
    ) -> PartRecord | None:
        mpn = mapped.get("mpn")
        if not mpn:
            return None
        mpn = mpn.rstrip("#").strip()
        if not mpn:
            return None

        # --- Package ---
        package = _extract_package_from_mpn(mpn)
        if not package:
            size_str = mapped.get("size", "")
            if size_str:
                package = FIXTURE_SIZE_TO_EIA.get(size_str.strip())

        footprint = _package_to_footprint(package)

        # --- Normalize values ---
        capacitance = _normalize_capacitance(mapped.get("capacitance", ""))
        voltage = _normalize_voltage(mapped.get("voltage_rating", ""))
        tolerance = _normalize_tolerance(mapped.get("tolerance", ""))
        dielectric = mapped.get("dielectric", "")

        specs = ElectricalSpecs(
            capacitance=capacitance or None,
            voltage_rating=voltage or None,
            tolerance=tolerance or None,
            dielectric=dielectric or None,
            esr=mapped.get("esr") or None,
            dcr=mapped.get("dcr") or None,
        )

        desc_parts = ["Murata GRM MLCC"]
        if capacitance:
            desc_parts.append(capacitance)
        if voltage:
            desc_parts.append(voltage)
        if dielectric:
            desc_parts.append(dielectric)

        return PartRecord(
            internal_pn=f"RAW-{mpn}",
            category=ComponentCategory.CAPACITOR,
            manufacturer="Murata",
            mpn=mpn,
            description=" ".join(desc_parts),
            value=capacitance or None,
            status=PartStatus.RAW,
            approved=False,
            specs=specs,
            package=package,
            footprint=footprint,
            source_library=source_library,
            source_vendor="Murata",
            source_series="GRM",
            source_pack=source_pack,
            source_file=source_file,
            source_row=row_num,
            notes=f"Imported from Murata GRM CSV at {now}",
        )

    @staticmethod
    def _collect_stats(result: VendorParseResult, rec: PartRecord, mapped: dict) -> None:
        if rec.package:
            result.package_counts[rec.package] += 1
        else:
            mpn = rec.mpn or ""
            code = mpn[3:6] if len(mpn) >= 6 else "???"
            result.unmapped_size_codes.add(code)

        if rec.specs.voltage_rating:
            result.voltage_counts[rec.specs.voltage_rating] += 1
        if rec.specs.dielectric:
            result.dielectric_counts[rec.specs.dielectric] += 1
        ps = mapped.get("product_status", "")
        if ps:
            result.product_status_counts[ps] += 1
        if len(result.example_mpns) < 10:
            result.example_mpns.append(rec.mpn or "")


# Self-register
register_parser("murata-grm", MurataGRMParser)
