"""BOM generation engine.

Reads a schematic, looks up resolved/approved parts, groups rows by
the profile's grouping strategy, and exports CSV or Rich table output.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Optional

from footfindr.bom.models import BOMProfile, BOMReport, BOMRow
from footfindr.bom.profiles import load_profile
from footfindr.kicad.schematic import KiCadSchematicReader
from footfindr.libraries.manager import LibraryManager


def generate_bom(
    schematic_path: str | Path,
    profile_name: str = "posm",
    *,
    workspace: Optional[str | Path] = None,
) -> BOMReport:
    """Generate a BOM from a schematic using the specified profile.

    Reads the schematic, matches against approved parts, groups rows
    per the profile's strategy, and returns a BOMReport.
    """
    profile = load_profile(profile_name)
    reader = KiCadSchematicReader()
    sch = reader.read(str(schematic_path))
    mgr = LibraryManager(workspace=workspace)
    approved = mgr.load_approved_parts()

    # Build lookup tables
    by_ipn: dict[str, object] = {}
    by_mpn: dict[str, object] = {}
    for p in approved:
        by_ipn[p.internal_pn] = p
        if p.mpn and p.mpn != "TBD":
            by_mpn[p.mpn] = p

    warnings: list[str] = []
    # Group key -> BOMRow
    groups: dict[str, BOMRow] = {}

    for sym in sch.symbols:
        # Respect DNP exclusion
        if profile.exclude_dnp and sym.dnp:
            continue

        ref = sym.ref
        value = sym.value or ""
        footprint = sym.footprint or ""
        fields = sym.fields

        # Try to find the matching approved part
        ipn = fields.get("InternalPN", "")
        mpn = fields.get("MPN", "") or fields.get("mpn", "")
        manufacturer = fields.get("Manufacturer", "")
        package = fields.get("Package", "")
        voltage_rating = fields.get("VoltageRating", "")
        power_rating = fields.get("PowerRating", "")
        tolerance = fields.get("Tolerance", "")
        dielectric = fields.get("Dielectric", "")
        lcsc = fields.get("LCSC Part #", "") or fields.get("LCSC", "")
        notes = fields.get("Notes", "")

        # Enrich from approved part if we have an IPN match
        part = None
        if ipn and ipn in by_ipn:
            part = by_ipn[ipn]
        elif mpn and mpn in by_mpn:
            part = by_mpn[mpn]

        if part:
            if not manufacturer:
                manufacturer = part.manufacturer or ""
            if not footprint:
                footprint = part.footprint or ""
            if not package:
                package = part.package or ""
            if not voltage_rating:
                voltage_rating = part.specs.voltage_rating or ""
            if not power_rating:
                power_rating = part.specs.power_rating or ""
            if not tolerance:
                tolerance = part.specs.tolerance or ""
            if not dielectric:
                dielectric = part.specs.dielectric or ""
            if not mpn:
                mpn = part.mpn or ""
            if not ipn:
                ipn = part.internal_pn or ""
            # LCSC from supplier_pns
            if not lcsc and hasattr(part, "supplier_pns"):
                lcsc = part.supplier_pns.get("lcsc", "")

        # Determine group key
        if profile.group_by == "internal_pn" and ipn:
            key = ipn
        else:
            key = f"{value}|{footprint}|{mpn}"

        if key not in groups:
            groups[key] = BOMRow(
                quantity=0,
                references=[],
                value=value,
                internal_pn=ipn,
                mpn=mpn,
                manufacturer=manufacturer,
                footprint=footprint,
                package=package,
                voltage_rating=voltage_rating,
                power_rating=power_rating,
                tolerance=tolerance,
                dielectric=dielectric,
                notes=notes,
                lcsc_pn=lcsc,
            )

        groups[key].quantity += 1
        groups[key].references.append(ref)

        # Track warnings
        if "InternalPN" in profile.warn_missing and not ipn:
            warnings.append(f"{ref}: missing InternalPN")
        if "Footprint" in profile.warn_missing and not footprint:
            warnings.append(f"{ref}: missing Footprint")
        if "MPN" in profile.warn_missing and not mpn:
            warnings.append(f"{ref}: missing MPN")

    # LCSC-specific warning for JLCPCB profiles
    if profile_name.startswith("jlcpcb"):
        parts_without_lcsc = [
            row for row in groups.values()
            if not row.lcsc_pn
        ]
        if parts_without_lcsc:
            refs = []
            for row in parts_without_lcsc:
                refs.extend(row.references)
            suffix = "..." if len(refs) > 5 else ""
            ref_str = ", ".join(refs[:5]) + suffix
            warnings.append(
                f"Missing LCSC Part # for {len(parts_without_lcsc)} row(s) "
                f"({ref_str}).\n"
                f"Run:\n"
                f"  ff jlc check\n"
                f"  ff jlc annotate --dry-run"
            )

    # POSM-specific warnings
    if profile_name == "posm":
        missing_ipn = [row for row in groups.values() if not row.internal_pn]
        missing_mpn = [row for row in groups.values() if not row.mpn]
        missing_mfr = [row for row in groups.values() if not row.manufacturer]
        missing_fp = [row for row in groups.values() if not row.footprint]

        if missing_ipn:
            refs = []
            for row in missing_ipn:
                refs.extend(row.references)
            warnings.append(
                f"{len(missing_ipn)} row(s) missing InternalPN "
                f"({', '.join(refs[:5])}{'...' if len(refs) > 5 else ''}). "
                f"Run: ff resolve <schematic> all"
            )
        if missing_mpn:
            refs = []
            for row in missing_mpn:
                refs.extend(row.references)
            warnings.append(
                f"{len(missing_mpn)} row(s) missing MPN "
                f"({', '.join(refs[:5])}{'...' if len(refs) > 5 else ''})"
            )
        if missing_mfr:
            refs = []
            for row in missing_mfr:
                refs.extend(row.references)
            warnings.append(
                f"{len(missing_mfr)} row(s) missing Manufacturer "
                f"({', '.join(refs[:5])}{'...' if len(refs) > 5 else ''})"
            )
        if missing_fp:
            refs = []
            for row in missing_fp:
                refs.extend(row.references)
            warnings.append(
                f"{len(missing_fp)} row(s) missing Footprint "
                f"({', '.join(refs[:5])}{'...' if len(refs) > 5 else ''}). "
                f"Run: ff resolve <schematic> all --apply"
            )

    # Sort by first reference
    rows = sorted(groups.values(), key=lambda r: r.references[0] if r.references else "")

    return BOMReport(
        rows=rows,
        warnings=warnings,
        profile_name=profile_name,
        schematic_path=str(schematic_path),
        total_parts=sum(r.quantity for r in rows),
        total_unique=len(rows),
    )


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

def export_bom_csv(
    report: BOMReport,
    output_path: str | Path,
    profile_name: str = "posm",
) -> Path:
    """Export a BOM report to CSV using the profile's column configuration."""
    profile = load_profile(profile_name)
    path = Path(output_path)

    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)

        # Header
        headers = [col.name for col in profile.columns]
        writer.writerow(headers)

        # Rows
        for row in report.rows:
            csv_row = []
            for col in profile.columns:
                val = _get_row_field(row, col.source, col.default)
                csv_row.append(val)
            writer.writerow(csv_row)

    return path


def _get_row_field(row: BOMRow, source: str, default: str = "") -> str:
    """Extract a field value from a BOMRow by source name."""
    source_lower = source.lower().replace(" ", "_")
    mapping = {
        "quantity": str(row.quantity),
        "references": ", ".join(sorted(row.references)),
        "designator": ", ".join(sorted(row.references)),
        "value": row.value,
        "comment": row.value,
        "internal_pn": row.internal_pn,
        "internalpn": row.internal_pn,
        "mpn": row.mpn,
        "manufacturer": row.manufacturer,
        "footprint": row.footprint,
        "package": row.package or row.footprint,
        "voltage_rating": row.voltage_rating,
        "voltagerating": row.voltage_rating,
        "power_rating": row.power_rating,
        "powerrating": row.power_rating,
        "tolerance": row.tolerance,
        "dielectric": row.dielectric,
        "notes": row.notes,
        "lcsc_part_#": row.lcsc_pn,
        "lcsc_pn": row.lcsc_pn,
    }
    return mapping.get(source_lower, default)
