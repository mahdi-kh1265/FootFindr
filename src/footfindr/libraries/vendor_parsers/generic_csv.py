"""Generic CSV vendor parser.

A universal fallback parser that handles common column names across
arbitrary vendor CSV files.  Not perfect, but gives a baseline for
any vendor whose CSV is reasonably structured.
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
# Column aliases — covers the most common vendor CSV headers
# ---------------------------------------------------------------------------

_COLUMN_ALIASES: dict[str, str] = {
    # MPN
    "part number": "mpn", "part_number": "mpn", "partnumber": "mpn",
    "mpn": "mpn", "mfr part number": "mpn", "mfr_part_number": "mpn",
    "manufacturer part number": "mpn", "pn": "mpn", "model": "mpn",
    # Manufacturer
    "manufacturer": "manufacturer", "mfr": "manufacturer",
    "brand": "manufacturer", "vendor": "manufacturer",
    # Category
    "category": "category", "type": "category",
    "component type": "category", "part type": "category",
    # Capacitance
    "capacitance": "capacitance", "cap": "capacitance",
    "capacitance (f)": "capacitance", "cap value": "capacitance",
    # Resistance
    "resistance": "resistance", "res": "resistance",
    "resistance (ohm)": "resistance", "res value": "resistance",
    # Inductance
    "inductance": "inductance", "ind": "inductance",
    # Voltage
    "rated voltage": "voltage_rating", "rated voltage dc": "voltage_rating",
    "voltage rating": "voltage_rating", "voltage": "voltage_rating",
    "max voltage": "voltage_rating",
    # Tolerance
    "tolerance": "tolerance", "tolerance of capacitance": "tolerance",
    "tol": "tolerance", "tolerance (%)": "tolerance",
    # Dielectric / temp characteristic
    "dielectric": "dielectric", "temperature characteristic": "dielectric",
    "temperature characteristics": "dielectric",
    "temp characteristic": "dielectric", "tc": "dielectric",
    # Package / size
    "package": "package", "size": "package", "case size": "package",
    "case/package": "package", "footprint": "package",
    "package size": "package", "body size": "package",
    # Dimensions
    "length": "length", "length (mm)": "length",
    "width": "width", "width (mm)": "width",
    "height": "height", "height (mm)": "height",
    "thickness": "height",
    # Description
    "description": "description", "desc": "description",
    # Datasheet
    "datasheet": "datasheet_url", "datasheet url": "datasheet_url",
    "datasheet_url": "datasheet_url",
    # Status
    "status": "product_status", "product status": "product_status",
    "lifecycle": "product_status",
}


_PACKAGE_FOOTPRINT_MAP: dict[str, str] = {
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

_CATEGORY_MAP: dict[str, ComponentCategory] = {
    "capacitor": ComponentCategory.CAPACITOR,
    "cap": ComponentCategory.CAPACITOR,
    "mlcc": ComponentCategory.CAPACITOR,
    "resistor": ComponentCategory.RESISTOR,
    "res": ComponentCategory.RESISTOR,
    "inductor": ComponentCategory.INDUCTOR,
    "ind": ComponentCategory.INDUCTOR,
    "ic": ComponentCategory.IC,
    "connector": ComponentCategory.CONNECTOR,
    "diode": ComponentCategory.DIODE,
    "led": ComponentCategory.LED,
}


def _normalize_col(raw: str) -> str:
    return _COLUMN_ALIASES.get(raw.strip().lower(), raw.strip().lower())


def _clean_voltage(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"[Vv][Dd][Cc]$", "V", s)
    s = s.replace(",", "")
    if s and not s.upper().endswith("V"):
        s += "V"
    return s


def _clean_tolerance(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    for ch in ("±", "Â±", "Â", "\ufffd", "�"):
        s = s.replace(ch, "")
    return s.lstrip("±").strip()


def _clean_capacitance(raw: str) -> str:
    if not raw:
        return ""
    return raw.strip().replace("µ", "u").replace("μ", "u").replace("\u03bc", "u")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class GenericCSVParser:
    """Generic CSV parser.

    Best-effort column detection and value normalization.
    Works with any reasonably structured CSV that has at minimum
    a part-number column.
    """

    vendor = "Generic"
    series = ""
    category = "other"
    display_name = "Generic CSV Library"
    pack_slug = "footfindr-lib-generic"

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
        result = VendorParseResult(parser_version="1.0.0")

        # Try encodings
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(path, "r", encoding=enc, newline="") as fh:
                    reader = csv.DictReader(fh)
                    if reader.fieldnames is None:
                        return result
                    col_map = {raw: _normalize_col(raw) for raw in reader.fieldnames}

                    for row_num, row in enumerate(reader, start=2):
                        if limit and len(result.records) >= limit:
                            break
                        result.raw_rows += 1

                        mapped: dict[str, str] = {}
                        for raw_col, val in row.items():
                            canonical = col_map.get(raw_col, raw_col)
                            if val and val.strip():
                                mapped[canonical] = val.strip()

                        rec = self._row_to_record(mapped, row_num, fname, source_pack, now)
                        if rec:
                            result.records.append(rec)
                            result.imported_parts += 1
                            self._collect_stats(result, rec, mapped)
                        else:
                            result.skipped_rows += 1
                            mpn = mapped.get("mpn", "(no MPN)")
                            result.skip_reasons.append(f"Row {row_num}: skipped ({mpn})")
                break  # success
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"Could not decode {path} with utf-8-sig, utf-8, or latin-1")

        return result

    def _row_to_record(
        self,
        mapped: dict[str, str],
        row_num: int,
        source_file: str,
        source_pack: str | None,
        now: str,
    ) -> PartRecord | None:
        mpn = mapped.get("mpn")
        if not mpn:
            return None
        mpn = mpn.strip()
        if not mpn:
            return None

        manufacturer = mapped.get("manufacturer", "")
        package = mapped.get("package", "")
        footprint = _PACKAGE_FOOTPRINT_MAP.get(package) if package else None

        cat_str = mapped.get("category", self.category).lower()
        cat = _CATEGORY_MAP.get(cat_str, ComponentCategory.OTHER)

        capacitance = _clean_capacitance(mapped.get("capacitance", ""))
        resistance = mapped.get("resistance", "")
        inductance = mapped.get("inductance", "")
        voltage = _clean_voltage(mapped.get("voltage_rating", ""))
        tolerance = _clean_tolerance(mapped.get("tolerance", ""))
        dielectric = mapped.get("dielectric", "")

        # Pick a value string
        value = capacitance or resistance or inductance or ""

        specs = ElectricalSpecs(
            capacitance=capacitance or None,
            resistance=resistance or None,
            inductance=inductance or None,
            voltage_rating=voltage or None,
            tolerance=tolerance or None,
            dielectric=dielectric or None,
        )

        desc_parts = [manufacturer or "Generic", mpn]
        if value:
            desc_parts.append(value)
        if voltage:
            desc_parts.append(voltage)

        return PartRecord(
            internal_pn=f"RAW-{mpn}",
            category=cat,
            manufacturer=manufacturer or None,
            mpn=mpn,
            description=" ".join(desc_parts),
            value=value or None,
            status=PartStatus.RAW,
            approved=False,
            specs=specs,
            package=package or None,
            footprint=footprint,
            source_library=f"Generic-{source_file}",
            source_vendor=manufacturer or None,
            source_pack=source_pack,
            source_file=source_file,
            source_row=row_num,
            notes=f"Imported from generic CSV at {now}",
        )

    @staticmethod
    def _collect_stats(result: VendorParseResult, rec: PartRecord, mapped: dict) -> None:
        if rec.package:
            result.package_counts[rec.package] += 1
        if rec.specs.voltage_rating:
            result.voltage_counts[rec.specs.voltage_rating] += 1
        if rec.specs.dielectric:
            result.dielectric_counts[rec.specs.dielectric] += 1
        ps = mapped.get("product_status", "")
        if ps:
            result.product_status_counts[ps] += 1
        if len(result.example_mpns) < 10:
            result.example_mpns.append(rec.mpn or "")


# Self-register under multiple slugs
register_parser("generic", GenericCSVParser)
register_parser("generic-csv", GenericCSVParser)
