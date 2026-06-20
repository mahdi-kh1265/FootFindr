"""Deterministic badge computation and differentiator extraction.

Rule-based only — no AI.  Used by the supplier variant browser to
annotate search results with status badges and highlight what makes
variants different from each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from footfindr.suppliers.models import SupplierPart


# ---------------------------------------------------------------------------
# Badge definitions
# ---------------------------------------------------------------------------

BADGE_DEFS = {
    "IN_STOCK": "Part is in stock",
    "ACTIVE": "Active lifecycle",
    "LOW_STOCK": "Stock < 100 units",
    "NRND": "Not recommended for new designs",
    "OBSOLETE": "Obsolete / discontinued",
    "NO_DATASHEET": "No datasheet URL available",
    "NO_PRICE": "No pricing data available",
    "EXPENSIVE": "Price significantly above median",
    "JLC_AVAILABLE": "Available on JLCPCB/LCSC",
    "FOOTPRINT_REVIEW": "Footprint needs manual review",
    "LOW_RELEVANCE": "MPN doesn't match query family",
}


def compute_badges(
    part: SupplierPart,
    context: list[SupplierPart] | None = None,
    *,
    query: str | None = None,
    constraint_passed: bool | None = None,
) -> list[str]:
    """Compute badges for a supplier part.

    ``context`` is the full result set, used for relative comparisons
    (e.g. EXPENSIVE).
    ``query`` is the original search query, used for relevance scoring.
    ``constraint_passed`` if True, suppresses LOW_RELEVANCE badge.
    """
    badges: list[str] = []

    # Stock
    if part.stock is not None and part.stock > 0:
        badges.append("IN_STOCK")
        if part.stock < 100:
            badges.append("LOW_STOCK")

    # Lifecycle
    lc = (part.lifecycle or part.product_status or "").lower()
    if lc in ("active",):
        badges.append("ACTIVE")
    elif lc in ("nrnd", "not recommended", "not recommended for new designs"):
        badges.append("NRND")
    elif lc in ("obsolete", "discontinued", "eol", "end of life"):
        badges.append("OBSOLETE")

    # Datasheet
    if not part.datasheet_url:
        badges.append("NO_DATASHEET")

    # Price
    if not part.price_breaks:
        badges.append("NO_PRICE")
    elif context:
        # EXPENSIVE: price > 3x median of context
        prices = [p.best_price() for p in context if p.best_price() is not None]
        if prices:
            median = sorted(prices)[len(prices) // 2]
            bp = part.best_price()
            if bp is not None and median > 0 and bp > median * 3:
                badges.append("EXPENSIVE")

    # JLC
    if part.lcsc_pn:
        badges.append("JLC_AVAILABLE")

    # Relevance — skip for parametric queries and constraint-passing parts
    if query and constraint_passed is not True:
        from footfindr.suppliers.session import compute_relevance, is_mpn_like_query
        # Only apply MPN-based relevance for MPN-like queries
        if is_mpn_like_query(query):
            score = compute_relevance(part, query)
            if score >= 4:
                badges.append("LOW_RELEVANCE")

    # Footprint always needs review (no auto-binding)
    badges.append("FOOTPRINT_REVIEW")

    return badges


# ---------------------------------------------------------------------------
# Differentiator extraction
# ---------------------------------------------------------------------------

# Fields to check for differences
_DIFF_FIELDS = [
    ("package", "Package"),
    ("packaging", "Packaging"),
    ("temperature_range", "Temp Range"),
    ("lifecycle", "Lifecycle"),
    ("mounting_type", "Mounting"),
    ("supplier", "Supplier"),
]


def extract_differentiators(parts: list[SupplierPart]) -> dict[str, list[str]]:
    """Identify which fields differ across a set of parts.

    Returns ``{display_name: [unique_values]}`` for fields that vary.
    """
    diffs: dict[str, list[str]] = {}

    for attr, display in _DIFF_FIELDS:
        values: set[str] = set()
        for p in parts:
            v = _get_diff_value(p, attr)
            if v:
                values.add(v)
        if len(values) > 1:
            diffs[display] = sorted(values)

    # Also check attributes dict for common differentiating keys
    attr_keys: set[str] = set()
    for p in parts:
        attr_keys.update(p.attributes.keys())

    for key in sorted(attr_keys):
        vals: set[str] = set()
        for p in parts:
            v = p.attributes.get(key, "")
            if v and v != "-":
                vals.add(v)
        if len(vals) > 1 and key not in ("Features", "Ratings", "Lead Style",
                                          "Lead Spacing", "Height - Seated (Max)"):
            diffs[key] = sorted(vals)

    return diffs


def _get_diff_value(part: SupplierPart, attr: str) -> str:
    """Get a displayable value for a diff field."""
    val = getattr(part, attr, None)
    if val is None or (isinstance(val, str) and not val.strip()):
        return ""
    return str(val).strip()


# ---------------------------------------------------------------------------
# Group by manufacturer MPN (collapse supplier SKUs)
# ---------------------------------------------------------------------------

@dataclass
class MPNGroup:
    """A manufacturer MPN with all its supplier orderable variants."""
    mpn: str
    manufacturer: str | None
    variants: list[SupplierPart]

    @property
    def best_variant(self) -> SupplierPart:
        """Return the variant with highest stock."""
        return max(self.variants, key=lambda v: v.stock or 0)


def group_by_mpn(parts: list[SupplierPart]) -> list[MPNGroup]:
    """Group parts by manufacturer MPN, collecting supplier SKUs.

    Same MPN from same supplier but different packaging (Tube, Tape & Reel,
    Digi-Reel) are grouped together.
    """
    groups: dict[str, MPNGroup] = {}

    for p in parts:
        key = (p.mpn or "").upper()
        if key in groups:
            groups[key].variants.append(p)
        else:
            groups[key] = MPNGroup(
                mpn=p.mpn or "",
                manufacturer=p.manufacturer,
                variants=[p],
            )

    return list(groups.values())


# ---------------------------------------------------------------------------
# Deterministic recommendations
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    """A deterministic part recommendation."""
    mpn: str
    supplier: str
    reason: str
    category: str  # "prototype", "compact", "best_value"
    part: SupplierPart


def recommend(
    parts: list[SupplierPart],
    qty: int | None = None,
) -> list[Recommendation]:
    """Generate deterministic recommendations from a result set.

    Rules:
    - prefer active lifecycle
    - prefer in-stock
    - prefer datasheet available
    - prefer lower qty-aware price
    - for "prototype": prefer hand-reworkable package (SOIC, MSOP, QFP > DFN, QFN, BGA)
    - for "compact": prefer smallest package
    """
    if not parts:
        return []

    # Filter to active, in-stock parts with pricing
    viable = [
        p for p in parts
        if (p.lifecycle or "").lower() in ("active", "")
        and (p.stock is not None and p.stock > 0)
        and p.is_valid()
    ]

    if not viable:
        viable = [p for p in parts if p.is_valid()]

    if not viable:
        return []

    recs: list[Recommendation] = []

    # Group by MPN to avoid recommending same MPN twice
    mpn_groups = group_by_mpn(viable)

    # Best value: lowest price, active, in stock
    by_price = sorted(
        mpn_groups,
        key=lambda g: g.best_variant.best_price(qty) or float("inf"),
    )
    if by_price:
        bv = by_price[0].best_variant
        price_str = f"${bv.best_price(qty):.4f}" if bv.best_price(qty) else "?"
        recs.append(Recommendation(
            mpn=bv.mpn,
            supplier=bv.supplier,
            reason=f"Lowest price ({price_str}), active, in stock ({bv.stock:,} units)",
            category="best_value",
            part=bv,
        ))

    # Prototype: prefer hand-reworkable packages
    _reworkable = {"soic", "ssop", "tssop", "msop", "sop", "qfp", "lqfp", "tqfp", "pdip", "dip"}
    _compact = {"dfn", "qfn", "wlcsp", "bga", "csp", "son", "udfn"}

    for g in mpn_groups:
        pkg = (g.best_variant.package or g.best_variant.supplier_device_package or "").lower()
        pkg_base = pkg.split("-")[0].split("_")[0].strip()
        if pkg_base in _reworkable:
            bv = g.best_variant
            recs.append(Recommendation(
                mpn=bv.mpn,
                supplier=bv.supplier,
                reason=f"Hand-reworkable package ({bv.package or bv.supplier_device_package}), "
                       f"in stock ({bv.stock:,}), datasheet {'available' if bv.datasheet_url else 'missing'}",
                category="prototype",
                part=bv,
            ))
            break

    # Compact: prefer smallest/densest package
    for g in mpn_groups:
        pkg = (g.best_variant.package or g.best_variant.supplier_device_package or "").lower()
        pkg_base = pkg.split("-")[0].split("_")[0].strip()
        if pkg_base in _compact:
            bv = g.best_variant
            recs.append(Recommendation(
                mpn=bv.mpn,
                supplier=bv.supplier,
                reason=f"Compact package ({bv.package or bv.supplier_device_package}), "
                       f"smaller board area, in stock ({bv.stock:,})",
                category="compact",
                part=bv,
            ))
            break

    return recs
