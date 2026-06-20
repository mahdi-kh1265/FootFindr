"""Part promotion and lifecycle management.

Promotes raw/candidate parts into approved libraries, manages status
transitions (approve, deprecate, block), and binds footprints.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from footfindr.core.models import PartRecord, PartStatus
from footfindr.libraries.manager import LibraryManager


class PromotionError(Exception):
    """Raised when a promotion operation fails."""


def promote_part(
    mpn: str,
    target_library: str,
    manager: LibraryManager,
    *,
    internal_pn: Optional[str] = None,
) -> PartRecord:
    """Promote a raw/candidate part to an approved library.

    Searches all raw vendor libraries for the MPN, copies the part into the
    target approved library, sets status=approved, and optionally assigns
    a custom internal PN.
    """
    # Find the part in raw libraries
    source_part: Optional[PartRecord] = None
    source_lib_name: Optional[str] = None

    libs = manager.list_libraries()
    for lib in libs:
        if lib.kind.value in ("raw_vendor", "approved", "sub"):
            parts = manager.load_raw_library(lib.name)
            for p in parts:
                if p.mpn and p.mpn == mpn:
                    source_part = p
                    source_lib_name = lib.name
                    break
            if source_part:
                break

    # Also check the main approved parts
    if not source_part:
        all_approved = manager.load_approved_parts()
        for p in all_approved:
            if p.mpn and p.mpn == mpn:
                source_part = p
                source_lib_name = "approved_parts"
                break

    if not source_part:
        raise PromotionError(f"Part with MPN '{mpn}' not found in any library")

    # Create the promoted copy
    promoted = PartRecord(
        internal_pn=internal_pn or source_part.internal_pn,
        category=source_part.category,
        manufacturer=source_part.manufacturer,
        mpn=source_part.mpn,
        description=source_part.description,
        value=source_part.value,
        status=PartStatus.APPROVED,
        approved=True,
        specs=source_part.specs,
        package=source_part.package,
        footprint=source_part.footprint,
        supplier_pns=source_part.supplier_pns.copy(),
        source_library=target_library,
        # Carry provenance from raw part
        source_vendor=source_part.source_vendor or source_part.manufacturer,
        source_series=source_part.source_series,
        source_pack=source_part.source_pack,
        source_file=source_part.source_file,
        source_row=source_part.source_row,
        promoted_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        promoted_from=source_lib_name,
        notes=f"Promoted from {source_lib_name} on {datetime.date.today().isoformat()}",
    )

    # Load target library parts, add the promoted part, save
    target_parts_file = _get_library_parts_file(target_library, manager)
    existing = []
    if target_parts_file.exists():
        existing = manager._parse_approved_yaml(target_parts_file)

    # Check for duplicate internal_pn
    for p in existing:
        if p.internal_pn == promoted.internal_pn:
            raise PromotionError(
                f"Internal PN '{promoted.internal_pn}' already exists in '{target_library}'"
            )

    existing.append(promoted)
    manager.save_approved_parts(existing, target_parts_file)

    return promoted


def promote_from_supplier(
    part,
    target_library: str,
    manager: LibraryManager,
    *,
    internal_pn: str,
    for_ref: str | None = None,
) -> PartRecord:
    """Promote a SupplierPart from supplier search into an approved library.

    Creates an approved library entry with full supplier provenance.

    Safety:
    - No footprint auto-binding (set to None with confidence 'review').
    - No schematic writes.
    - No purchasing.
    - No automatic substitutions.
    """
    from footfindr.constraints import infer_category

    # Infer category from ref pattern or description
    cat_value, cat_confidence = infer_category(
        ref=for_ref,
        description=getattr(part, "description", None),
    )
    from footfindr.core.models import ComponentCategory
    try:
        category = ComponentCategory(cat_value)
    except ValueError:
        category = ComponentCategory.OTHER

    notes_parts = [
        f"Promoted from {part.supplier} supplier search on "
        f"{datetime.date.today().isoformat()}.",
        f"Supplier PN: {part.supplier_pn or 'N/A'}.",
        f"Source: {getattr(part, 'product_url', None) or 'N/A'}.",
        f"Footprint: review required.",
    ]
    if for_ref:
        notes_parts.insert(0, f"Selected for ref: {for_ref}.")
    if cat_confidence == "review":
        notes_parts.append("Category: needs review.")

    promoted = PartRecord(
        internal_pn=internal_pn,
        category=category,
        manufacturer=part.manufacturer,
        mpn=part.mpn,
        description=part.description,
        status=PartStatus.APPROVED,
        approved=True,
        package=part.package or getattr(part, 'supplier_device_package', None),
        footprint=None,  # No auto-binding — requires manual review
        supplier_pns={part.supplier: part.supplier_pn} if part.supplier_pn else {},
        source_library=target_library,
        source_vendor=part.manufacturer,
        promoted_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        promoted_from=f"supplier:{part.supplier}",
        notes=" ".join(notes_parts),
    )

    # Load target library parts, add promoted, save
    target_parts_file = _get_library_parts_file(target_library, manager)
    existing = []
    if target_parts_file.exists():
        existing = manager._parse_approved_yaml(target_parts_file)

    # Check for duplicate internal_pn
    for p in existing:
        if p.internal_pn == promoted.internal_pn:
            raise PromotionError(
                f"Internal PN '{promoted.internal_pn}' already exists in '{target_library}'"
            )

    existing.append(promoted)
    manager.save_approved_parts(existing, target_parts_file)

    return promoted


def promote_from_supplier_data(
    data: dict,
    target_library: str,
    manager: LibraryManager,
    *,
    internal_pn: str,
) -> PartRecord:
    """Promote from a plan's serialized data dict into an approved library.

    Used by PlanManager.apply() to execute planned promotions.
    """
    from footfindr.core.models import ComponentCategory

    try:
        category = ComponentCategory(data.get("category", "other"))
    except ValueError:
        category = ComponentCategory.OTHER

    promoted = PartRecord(
        internal_pn=internal_pn,
        category=category,
        manufacturer=data.get("manufacturer"),
        mpn=data.get("mpn"),
        description=data.get("description"),
        status=PartStatus.APPROVED,
        approved=True,
        package=data.get("package"),
        footprint=None,
        supplier_pns=data.get("supplier_pns", {}),
        source_library=target_library,
        source_vendor=data.get("manufacturer"),
        promoted_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        promoted_from=data.get("promoted_from", ""),
        notes=data.get("notes", ""),
    )

    target_parts_file = _get_library_parts_file(target_library, manager)
    existing = []
    if target_parts_file.exists():
        existing = manager._parse_approved_yaml(target_parts_file)

    for p in existing:
        if p.internal_pn == promoted.internal_pn:
            raise PromotionError(
                f"Internal PN '{promoted.internal_pn}' already exists in '{target_library}'"
            )

    existing.append(promoted)
    manager.save_approved_parts(existing, target_parts_file)

    return promoted



def approve_part(
    internal_pn: str,
    manager: LibraryManager,
) -> PartRecord:
    """Set a part's status to approved."""
    return _update_part_status(internal_pn, PartStatus.APPROVED, manager)


def deprecate_part(
    internal_pn: str,
    manager: LibraryManager,
) -> PartRecord:
    """Set a part's status to deprecated."""
    return _update_part_status(internal_pn, PartStatus.DEPRECATED, manager)


def block_part(
    internal_pn: str,
    manager: LibraryManager,
) -> PartRecord:
    """Set a part's status to blocked."""
    return _update_part_status(internal_pn, PartStatus.BLOCKED, manager)


def bind_footprint(
    internal_pn: str,
    footprint_ref: str,
    manager: LibraryManager,
) -> PartRecord:
    """Bind a footprint reference to a part."""
    parts = manager.load_approved_parts()
    for p in parts:
        if p.internal_pn == internal_pn:
            p.footprint = footprint_ref
            # Re-save — find which file this part came from
            # For MVP, save back to the default approved parts file
            _save_all_approved(parts, manager)
            return p
    raise PromotionError(f"Part '{internal_pn}' not found")


def find_part(
    identifier: str,
    manager: LibraryManager,
) -> Optional[PartRecord]:
    """Find a part by internal_pn or MPN."""
    parts = manager.load_approved_parts()
    for p in parts:
        if p.internal_pn == identifier:
            return p
        if p.mpn and p.mpn == identifier:
            return p
    # Also search raw libraries
    libs = manager.list_libraries()
    for lib in libs:
        if lib.kind.value == "raw_vendor":
            raw_parts = manager.load_raw_library(lib.name)
            for p in raw_parts:
                if p.internal_pn == identifier:
                    return p
                if p.mpn and p.mpn == identifier:
                    return p
    return None


def search_parts(
    query: str,
    manager: LibraryManager,
) -> list[PartRecord]:
    """Search parts by a text query across multiple fields."""
    parts = manager.load_approved_parts()
    q = query.lower()
    tokens = q.split()
    results = []

    for p in parts:
        searchable = " ".join(filter(None, [
            p.internal_pn,
            p.mpn,
            p.manufacturer,
            p.value,
            p.package,
            p.specs.capacitance,
            p.specs.resistance,
            p.specs.voltage_rating,
            p.specs.dielectric,
            p.description,
            p.category.value,
        ])).lower()

        if all(tok in searchable for tok in tokens):
            results.append(p)

    return results


def search_all_parts(
    query: str,
    manager: LibraryManager,
    *,
    category: str | None = None,
    approved_only: bool = False,
    raw_only: bool = False,
    vendor: str | None = None,
    package: str | None = None,
    voltage_min: str | None = None,
    dielectric: str | None = None,
) -> list[PartRecord]:
    """Search approved AND raw libraries with optional filters.

    Supports equivalent-value parametric search: ``100n``, ``0.1u``, and
    ``0.10uF`` all match the same capacitance.  ``4k7``, ``4700``, and
    ``4.7k`` all match the same resistance.

    Parameters
    ----------
    query : str
        Text query or value query (e.g. '10u', '100n', '4k7').
    category : str, optional
        Filter by component category (e.g. 'capacitor', 'resistor').
    approved_only : bool
        Show only approved parts.
    raw_only : bool
        Show only raw/unapproved parts.
    vendor : str, optional
        Filter by manufacturer name (case-insensitive substring).
    package : str, optional
        Filter by package code (exact, case-insensitive).
    voltage_min : str, optional
        Filter by minimum voltage rating (e.g. '16V').
    dielectric : str, optional
        Filter by dielectric type (e.g. 'C0G', 'X7R').
    """
    # Try index first
    try:
        from footfindr.db.index import PartIndex
        idx = PartIndex(workspace=manager._workspace)
        if idx.has_any_parts():
            return idx.search(
                query,
                category=category,
                approved_only=approved_only,
                raw_only=raw_only,
                vendor=vendor,
                package=package,
                voltage_min=voltage_min,
                dielectric=dielectric,
            )
    except Exception:
        pass  # Fall back to in-memory search

    return _search_in_memory(
        query, manager,
        category=category,
        approved_only=approved_only,
        raw_only=raw_only,
        vendor=vendor,
        package=package,
        voltage_min=voltage_min,
        dielectric=dielectric,
    )


def _normalize_query_value(
    query: str,
    category_hint: str | None = None,
) -> tuple[float | None, str]:
    """Try to parse a search query as a normalized SI value.

    Returns ``(si_value, domain)`` where *domain* is ``'capacitance'``,
    ``'resistance'``, or ``'unknown'``.  Returns ``(None, 'unknown')`` if
    the query cannot be parsed as a numeric value.
    """
    from footfindr.core.units import parse_capacitance, parse_resistance

    q = query.strip()
    if not q:
        return None, "unknown"

    # Category hint narrows interpretation
    cat = (category_hint or "").lower()

    # Try capacitance first if category suggests it
    if cat.startswith("cap") or cat in ("c",):
        val = parse_capacitance(q)
        if val is not None and val > 0:
            return val, "capacitance"

    # Try resistance if category suggests it
    if cat.startswith("res") or cat in ("r",):
        val = parse_resistance(q)
        if val is not None and val >= 0:
            return val, "resistance"

    # No category hint — try both
    cap_val = parse_capacitance(q)
    res_val = parse_resistance(q)

    # Heuristics: if query ends with F/f/pF/nF/uF → capacitance
    q_upper = q.upper()
    if q_upper.endswith("F") or q_upper.endswith("PF") or q_upper.endswith("NF") or q_upper.endswith("UF"):
        if cap_val is not None and cap_val > 0:
            return cap_val, "capacitance"

    # If query contains R/Ω/k/K/M as multiplier → resistance
    if any(c in q for c in ("R", "Ω", "k", "K")) and q_upper[-1:] != "F":
        if res_val is not None and res_val >= 0:
            return res_val, "resistance"

    # Embedded multiplier hints (4u7 → cap, 4k7 → res)
    import re
    if re.match(r"^\d+[uUnNpP]\d+$", q):
        if cap_val is not None and cap_val > 0:
            return cap_val, "capacitance"
    if re.match(r"^\d+[kKMmRr]\d+$", q):
        if res_val is not None and res_val >= 0:
            return res_val, "resistance"

    # Try cap then res as fallback
    if cap_val is not None and cap_val > 0:
        return cap_val, "capacitance"
    if res_val is not None and res_val >= 0:
        return res_val, "resistance"

    return None, "unknown"


def _value_matches(
    query_si: float,
    domain: str,
    part: PartRecord,
) -> bool:
    """Check if a part's value matches the query's normalized SI value.

    Uses tight tolerance for exact nominal matching.
    """
    import math
    from footfindr.core.units import parse_capacitance, parse_resistance

    if domain == "capacitance":
        raw = part.specs.capacitance or part.value or ""
        part_val = parse_capacitance(raw)
    elif domain == "resistance":
        raw = part.specs.resistance or part.value or ""
        part_val = parse_resistance(raw)
    else:
        return False

    if part_val is None:
        return False

    return math.isclose(query_si, part_val, rel_tol=1e-9, abs_tol=1e-18)


# Dielectric alias mapping (common equivalents)
_DIELECTRIC_ALIASES: dict[str, set[str]] = {
    "c0g": {"c0g", "np0"},
    "np0": {"c0g", "np0"},
}


def _dielectric_matches(filter_val: str, part_val: str | None) -> bool:
    """Check if part dielectric matches the filter, with alias support."""
    if not part_val:
        return False
    f_lower = filter_val.lower()
    p_lower = part_val.lower()
    if f_lower == p_lower:
        return True
    aliases = _DIELECTRIC_ALIASES.get(f_lower, set())
    return p_lower in aliases


def _search_in_memory(
    query: str,
    manager: LibraryManager,
    *,
    category: str | None = None,
    approved_only: bool = False,
    raw_only: bool = False,
    vendor: str | None = None,
    package: str | None = None,
    voltage_min: str | None = None,
    dielectric: str | None = None,
) -> list[PartRecord]:
    """In-memory search fallback when no SQLite index is available."""
    from footfindr.core.units import parse_voltage

    all_parts: list[PartRecord] = []

    # Approved parts
    if not raw_only:
        all_parts.extend(manager.load_approved_parts())

    # Raw libraries
    if not approved_only:
        libs = manager.list_libraries()
        for lib in libs:
            if lib.kind.value == "raw_vendor":
                raw_parts = manager.load_raw_library(lib.name)
                all_parts.extend(raw_parts)

    # Try parametric value matching first
    query_si, domain = _normalize_query_value(query, category_hint=category)
    use_parametric = query_si is not None

    # Text search tokens (fallback)
    q_lower = query.lower()
    tokens = q_lower.split() if q_lower else []

    results: list[PartRecord] = []
    for p in all_parts:
        # Value matching: parametric if available, else text
        if use_parametric:
            if not _value_matches(query_si, domain, p):
                continue
        elif tokens:
            searchable = " ".join(filter(None, [
                p.internal_pn,
                p.mpn,
                p.manufacturer,
                p.value,
                p.package,
                p.specs.capacitance,
                p.specs.resistance,
                p.specs.voltage_rating,
                p.specs.dielectric,
                p.specs.tolerance,
                p.description,
                p.category.value,
                p.source_library,
            ])).lower()
            if not all(tok in searchable for tok in tokens):
                continue

        # Category filter (support abbreviations: cap, res, ind, etc.)
        if category:
            cat_lower = category.lower()
            cat_value = p.category.value
            if cat_value != cat_lower and not cat_value.startswith(cat_lower):
                continue

        # Vendor filter
        if vendor and (not p.manufacturer or vendor.lower() not in p.manufacturer.lower()):
            continue

        # Package filter
        if package and (not p.package or p.package.lower() != package.lower()):
            continue

        # Voltage min filter
        if voltage_min:
            try:
                required_v = parse_voltage(voltage_min)
                part_v = parse_voltage(p.specs.voltage_rating or "0V")
                if part_v < required_v:
                    continue
            except (ValueError, TypeError):
                pass

        # Dielectric filter
        if dielectric and not _dielectric_matches(dielectric, p.specs.dielectric):
            continue

        results.append(p)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _update_part_status(
    internal_pn: str,
    new_status: PartStatus,
    manager: LibraryManager,
) -> PartRecord:
    """Update a part's status in the approved parts file."""
    parts = manager.load_approved_parts()
    for p in parts:
        if p.internal_pn == internal_pn:
            p.status = new_status
            p.approved = new_status == PartStatus.APPROVED
            _save_all_approved(parts, manager)
            return p
    raise PromotionError(f"Part '{internal_pn}' not found")


def _save_all_approved(parts: list[PartRecord], manager: LibraryManager) -> None:
    """Save approved parts back to the default file."""
    candidates = [
        Path("schemas/approved_parts.yaml"),
        Path("schemas/approved_parts.example.yaml"),
    ]
    for c in candidates:
        if c.exists():
            manager.save_approved_parts(parts, c)
            return
    manager.save_approved_parts(parts, candidates[0])


def _get_library_parts_file(library_name: str, manager: LibraryManager) -> Path:
    """Get the parts file path for a library."""
    libs = manager.list_libraries()
    for lib in libs:
        if lib.name == library_name and lib.parts_file:
            return Path(lib.parts_file)

    # Create a default path
    safe_name = library_name.replace(" ", "_").lower()
    path = manager.workspace / "approved" / f"{safe_name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    # Update the library metadata with the parts file path
    libs_file = manager._load_libraries_file()
    for lib in libs_file.libraries:
        if lib.name == library_name:
            lib.parts_file = str(path)
            manager._save_libraries_file(libs_file)
            break

    return path
