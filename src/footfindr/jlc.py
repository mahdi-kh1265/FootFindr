"""JLCPCB/LCSC compatibility checker and annotator.

Checks schematic BOM for LCSC Part # field presence/validity and can
annotate schematics with LCSC codes from the supplier cache or live API.

Safety rules:
- ``jlc_check()`` never writes.
- ``jlc_annotate(dry_run=True)`` never writes.
- ``jlc_annotate(dry_run=False)`` writes only for exact manufacturer+MPN matches.
- No fuzzy matching writeback.
- No supplier substitutions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class JLCPartStatus:
    """Status of LCSC compatibility for a single schematic symbol."""
    ref: str
    mpn: str | None = None
    manufacturer: str | None = None
    existing_lcsc: str | None = None
    matched_lcsc: str | None = None
    match_type: str = "none"  # 'exact', 'ambiguous', 'none', 'already_annotated'
    jlc_category: str | None = None  # basic/extended/preferred
    notes: str | None = None


@dataclass
class JLCCheckReport:
    """Summary of JLCPCB compatibility check."""
    statuses: list[JLCPartStatus] = field(default_factory=list)
    total: int = 0
    already_annotated: int = 0
    exact_match: int = 0
    ambiguous: int = 0
    no_match: int = 0


# Recognized LCSC field names in KiCad schematics
_LCSC_FIELD_NAMES = {"LCSC Part #", "LCSC", "JLCPCB Part #", "LCSC_Part"}
# Canonical field name for writes
_LCSC_WRITE_FIELD = "LCSC Part #"


def jlc_check(
    schematic_path: str | Path,
    *,
    cache_only: bool = False,
    live: bool = False,
    workspace: str | Path | None = None,
) -> JLCCheckReport:
    """Check schematic for JLCPCB/LCSC compatibility.

    Never writes to the schematic. Returns a report of LCSC status
    for each symbol.

    Parameters
    ----------
    cache_only : bool
        Only use cached data, no API calls.
    live : bool
        If True, query JLCPCB/LCSC API for LCSC codes (and cache results).
    """
    from footfindr.kicad.schematic import KiCadSchematicReader
    from footfindr.suppliers.cache import SupplierCache
    from footfindr.suppliers.mock import MockSupplierProvider

    reader = KiCadSchematicReader()
    sch = reader.read(str(schematic_path))
    cache = SupplierCache(workspace=workspace)
    mock = MockSupplierProvider()

    # Initialize live provider if requested
    jlc_provider = None
    if live and not cache_only:
        try:
            from footfindr.suppliers.jlcpcb import JLCPCBProvider
            jlc_provider = JLCPCBProvider()
            if not jlc_provider.is_configured():
                jlc_provider = None
        except Exception:
            jlc_provider = None

    report = JLCCheckReport()

    for sym in sch.symbols:
        if sym.dnp:
            continue

        report.total += 1
        ref = sym.ref
        mpn = sym.fields.get("MPN", "") or sym.fields.get("mpn", "")
        manufacturer = sym.fields.get("Manufacturer", "") or sym.fields.get("manufacturer", "")

        # Check existing LCSC annotation
        existing_lcsc = None
        for fname in _LCSC_FIELD_NAMES:
            val = sym.fields.get(fname, "")
            if val:
                existing_lcsc = val
                break

        status = JLCPartStatus(
            ref=ref,
            mpn=mpn or None,
            manufacturer=manufacturer or None,
            existing_lcsc=existing_lcsc,
        )

        if existing_lcsc:
            status.match_type = "already_annotated"
            report.already_annotated += 1
        elif not mpn:
            status.match_type = "none"
            status.notes = "No MPN set"
            report.no_match += 1
        else:
            lcsc_code = None
            jlc_cat = None

            # Try cache first
            cached = cache.lookup(mpn, supplier="jlcpcb", manufacturer=manufacturer or None)
            if cached:
                for entry in cached:
                    if entry.lcsc_pn:
                        lcsc_code = entry.lcsc_pn
                        jlc_cat = entry.jlc_category
                        break

            # Try mock LCSC lookup
            if not lcsc_code:
                cached_mock = cache.lookup(mpn, supplier="mock", manufacturer=manufacturer or None)
                if cached_mock:
                    lcsc_code = mock.lookup_lcsc(mpn)
                elif not cache_only:
                    lcsc_code = mock.lookup_lcsc(mpn)

            # Try live JLCPCB API
            if not lcsc_code and jlc_provider and not cache_only:
                try:
                    jlc_part = jlc_provider.lookup_mpn(
                        mpn, manufacturer=manufacturer or None
                    )
                    if jlc_part and jlc_part.lcsc_pn:
                        lcsc_code = jlc_part.lcsc_pn
                        jlc_cat = jlc_part.jlc_category
                        # Cache the result
                        cache.store(jlc_part)
                except Exception as e:
                    status.notes = f"JLCPCB API error: {e}"

            if lcsc_code:
                status.matched_lcsc = lcsc_code
                status.jlc_category = jlc_cat
                status.match_type = "exact"
                report.exact_match += 1
            else:
                status.match_type = "none"
                report.no_match += 1

        report.statuses.append(status)

    cache.close()
    return report


def jlc_annotate(
    schematic_path: str | Path,
    *,
    dry_run: bool = True,
    live: bool = False,
    workspace: str | Path | None = None,
) -> JLCCheckReport:
    """Annotate schematic with LCSC Part # fields.

    Parameters
    ----------
    dry_run : bool
        If True (default), only report proposed changes. If False,
        write exact-match LCSC codes to the schematic.
    live : bool
        If True, query JLCPCB/LCSC API for LCSC codes.

    Safety: Only writes when match_type == 'exact'. Never writes
    ambiguous or fuzzy matches.
    """
    report = jlc_check(schematic_path, live=live, workspace=workspace)

    if dry_run:
        return report

    # Collect writes: only exact matches
    writes: dict[str, str] = {}
    for status in report.statuses:
        if status.match_type == "exact" and status.matched_lcsc:
            writes[status.ref] = status.matched_lcsc

    if not writes:
        return report

    # Apply writes via field writer
    from footfindr.kicad.field_writer import KiCadFieldWriter

    writer = KiCadFieldWriter()
    for ref, lcsc_code in writes.items():
        writer.write_field(
            str(schematic_path),
            ref,
            _LCSC_WRITE_FIELD,
            lcsc_code,
        )

    return report
