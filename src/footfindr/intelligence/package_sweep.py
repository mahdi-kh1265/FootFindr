"""Capacitor package sweep with evidence model (M9.4B).

For each candidate package size, queries supplier data and builds a
``PackageEvidence`` record with:
    raw_count, parsed_count, viable_count, active_count, in_stock_count,
    manufacturer_count, manufacturer_entropy, median_price, price_quantiles,
    lifecycle_distribution, reject_reasons, first_raw_mpns

Includes package/attribute normalization:
    "0603 (1608 Metric)" → "0603"
    "4.7 µF" → 4.7e-6 F

Voltage handling:
    V_required = ceil_standard(k_derate * V_rail)
    Filter: V_rated >= V_required (not V_rated == V_rail)

Standard voltage ratings: [4, 6.3, 10, 16, 25, 35, 50, 63, 100]
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from dataclasses import dataclass
from typing import Any

from footfindr.intelligence.models import PackageEvidence, PackageScore

logger = logging.getLogger("footfindr.intelligence.package_sweep")


# ---------------------------------------------------------------------------
# Standard MLCC package dimensions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackageDims:
    """Dimensions for a standard passive package size."""
    length_mm: float
    width_mm: float
    height_mm: float

    @property
    def area_mm2(self) -> float:
        return self.length_mm * self.width_mm


PACKAGE_DIMS: dict[str, PackageDims] = {
    "0201": PackageDims(length_mm=0.60, width_mm=0.30, height_mm=0.30),
    "0402": PackageDims(length_mm=1.00, width_mm=0.50, height_mm=0.50),
    "0603": PackageDims(length_mm=1.60, width_mm=0.80, height_mm=0.80),
    "0805": PackageDims(length_mm=2.00, width_mm=1.25, height_mm=1.25),
    "1206": PackageDims(length_mm=3.20, width_mm=1.60, height_mm=1.60),
    "1210": PackageDims(length_mm=3.20, width_mm=2.50, height_mm=2.50),
    "1812": PackageDims(length_mm=4.50, width_mm=3.20, height_mm=2.50),
    "2220": PackageDims(length_mm=5.70, width_mm=5.00, height_mm=2.50),
}

DEFAULT_CAP_PACKAGES = ["0201", "0402", "0603", "0805", "1206"]

_ACCEPTABLE_LIFECYCLE = frozenset({
    "active", "new", "nrnd", "not recommended for new designs",
    None, "",
})


# ---------------------------------------------------------------------------
# Package name normalization
# ---------------------------------------------------------------------------

PACKAGE_ALIASES: dict[str, str] = {
    "1608 Metric": "0603", "1608": "0603",
    "0603 (1608 Metric)": "0603",
    "2012 Metric": "0805", "2012": "0805",
    "0805 (2012 Metric)": "0805",
    "0402 (1005 Metric)": "0402", "1005 Metric": "0402", "1005": "0402",
    "0201 (0603 Metric)": "0201", "0603 Metric": "0201",
    "1206 (3216 Metric)": "1206", "3216 Metric": "1206", "3216": "1206",
    "1210 (3225 Metric)": "1210", "3225 Metric": "1210", "3225": "1210",
    "1812 (4532 Metric)": "1812", "4532 Metric": "1812", "4532": "1812",
    "2220 (5750 Metric)": "2220", "5750 Metric": "2220", "5750": "2220",
}

# Regex to extract EIA code from various formats
_PKG_EXTRACT = re.compile(r"\b(0201|0402|0603|0805|1206|1210|1812|2220)\b")


def normalize_package(raw: str) -> str | None:
    """Normalize a package string to a standard EIA code.

    Returns None if unrecognized.
    """
    if not raw:
        return None
    raw_stripped = raw.strip()

    # Direct match
    if raw_stripped in PACKAGE_DIMS:
        return raw_stripped

    # Alias lookup
    if raw_stripped in PACKAGE_ALIASES:
        return PACKAGE_ALIASES[raw_stripped]

    # Case-insensitive alias
    for alias, normalized in PACKAGE_ALIASES.items():
        if raw_stripped.lower() == alias.lower():
            return normalized

    # Regex extraction
    m = _PKG_EXTRACT.search(raw_stripped)
    if m:
        return m.group(1)

    return None


# ---------------------------------------------------------------------------
# Standard voltage ratings for derating
# ---------------------------------------------------------------------------

STANDARD_VOLTAGE_RATINGS = [4, 6.3, 10, 16, 25, 35, 50, 63, 100, 200, 250, 500]


def ceil_standard_voltage(voltage: float) -> float:
    """Round up to the nearest standard capacitor voltage rating.

    V_required = ceil_standard(k_derate * V_rail)
    """
    for sv in STANDARD_VOLTAGE_RATINGS:
        if sv >= voltage:
            return float(sv)
    return voltage


def compute_required_voltage(
    rail_voltage: float,
    k_derate: float = 2.0,
) -> float:
    """Compute required capacitor voltage rating from rail voltage.

    V_required = ceil_standard(k_derate * V_rail)
    Default: 2.0x derating (50% derating rule).
    """
    return ceil_standard_voltage(k_derate * rail_voltage)


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

_CAP_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(pF|nF|uF|µF|μF|mF)\b",
    re.IGNORECASE,
)

_VOLTAGE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*V\b",
    re.IGNORECASE,
)


def normalize_capacitance(raw: str) -> float | None:
    """Parse capacitance from a supplier description.

    Handles: "4.7 µF", "4700 nF", "4.7 UF", "100pF".
    Returns value in Farads or None.
    """
    from footfindr.core.units import parse_capacitance
    m = _CAP_PATTERN.search(raw)
    if m:
        val_str = m.group(1) + m.group(2).replace("µ", "u").replace("μ", "u")
        return parse_capacitance(val_str)
    return None


def normalize_voltage(raw: str) -> float | None:
    """Parse voltage rating from a supplier description."""
    m = _VOLTAGE_PATTERN.search(raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Main sweep function
# ---------------------------------------------------------------------------

def package_sweep(
    value: str,
    voltage: str | None = None,
    *,
    packages: list[str] | None = None,
    use_cache: bool = True,
    use_live: bool = False,
    supplier: str | None = None,
) -> tuple[list[PackageScore], str]:
    """Run a package sweep for a capacitor value.

    Returns (list of PackageScore with evidence, data_source string).
    """
    from footfindr.core.units import parse_capacitance, parse_voltage

    target_packages = packages or DEFAULT_CAP_PACKAGES
    cap_farads = parse_capacitance(value)
    voltage_v = parse_voltage(voltage) if voltage else None

    scores: list[PackageScore] = []
    data_source = "cache"

    for pkg in target_packages:
        dims = PACKAGE_DIMS.get(pkg)
        if not dims:
            logger.warning(f"Unknown package size: {pkg}")
            continue

        # Build search query
        query = _build_search_query(value, voltage, pkg)

        # Get supplier results
        results, source = _get_supplier_results(
            query, pkg,
            use_cache=use_cache,
            use_live=use_live,
            supplier=supplier,
        )

        if source == "live":
            data_source = "live" if data_source != "mixed" else "mixed"
        elif source == "mock":
            data_source = "mock" if data_source == "cache" else "mixed"

        # Build evidence record with per-result classification
        ev = _build_package_evidence(results, cap_farads, voltage_v, pkg, query)

        # Build PackageScore from evidence
        score = _evidence_to_score(pkg, dims, ev)
        scores.append(score)

    # Sort by score descending
    scores.sort(key=lambda s: s.score, reverse=True)

    return scores, data_source


def _build_search_query(
    value: str,
    voltage: str | None,
    package: str,
) -> str:
    """Build a supplier search query string."""
    parts = [value]
    if voltage:
        parts.append(voltage)
    parts.append(package)
    parts.append("capacitor")
    return " ".join(parts)


def _get_supplier_results(
    query: str,
    package: str,
    *,
    use_cache: bool = True,
    use_live: bool = False,
    supplier: str | None = None,
) -> tuple[list[Any], str]:
    """Get supplier search results (cached, live, or mock)."""
    if use_cache:
        cached = _try_cache_lookup(query)
        if cached is not None:
            return cached, "cache"

    if use_live:
        live = _try_live_query(query, supplier=supplier)
        if live is not None:
            return live, "live"

    return [], "cache"


def _try_cache_lookup(query: str) -> list[Any] | None:
    """Try to find cached supplier results."""
    try:
        from footfindr.suppliers.cache import SupplierCache
        cache = SupplierCache()
        from footfindr.suppliers.registry import SupplierRegistry
        reg = SupplierRegistry()
        all_results: list[Any] = []
        for provider in reg.get_all():
            cached = cache.lookup_search(provider.name, query)
            if cached:
                all_results.extend(cached)
        cache.close()
        if all_results:
            return all_results
    except Exception as e:
        logger.debug(f"Cache lookup failed: {e}")
    return None


def _try_live_query(
    query: str,
    supplier: str | None = None,
) -> list[Any] | None:
    """Try a live supplier query, caching results."""
    try:
        from footfindr.suppliers.registry import SupplierRegistry
        from footfindr.suppliers.cache import SupplierCache

        reg = SupplierRegistry()
        cache = SupplierCache()

        if supplier:
            provider = reg.get(supplier)
            if provider and provider.is_configured():
                result = provider.search(query, limit=50)
                items = result.items if hasattr(result, "items") else result
                valid = [r for r in items if r.is_valid()]
                if valid:
                    cache.store_search(provider.name, query, valid)
                cache.close()
                return valid
        else:
            providers = reg.get_configured_live()
            all_results = []
            for p in providers:
                try:
                    result = p.search(query, limit=50)
                    items = result.items if hasattr(result, "items") else result
                    valid = [r for r in items if r.is_valid()]
                    if valid:
                        cache.store_search(p.name, query, valid)
                    all_results.extend(valid)
                except Exception as e:
                    logger.debug(f"Live query to {p.name} failed: {e}")
            cache.close()
            if all_results:
                return all_results
    except Exception as e:
        logger.debug(f"Live query failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Capacitance parsing cascade (M9.5)
# ---------------------------------------------------------------------------

# EIA capacitance code pattern: 3-digit code where first 2 digits are
# significand and third is exponent (number of zeros in picofarads).
# E.g., 475 = 47 × 10^5 pF = 4.7µF,  104 = 10 × 10^4 pF = 100nF
_EIA_CAP_CODE = re.compile(r"(\d)(\d)(\d)")

# Common MLCC MPN patterns that contain EIA capacitance codes.
# Known positions by manufacturer:
#   Murata GRM:  GRM188R61C475KA73  →  position [12:15] = "475"
#   Samsung CL:  CL10B104KB8NNNC    →  position [5:8]   = "104"
#   TDK C:       C1608X5R1E104K080  →  position [11:14]  = "104"
#   Yageo CC:    CC0402KRX7R7BB104  →  position [14:17]  = "104"
# Rather than hard-coding positions, we search for the pattern.
_MPN_CAP_SEARCH = re.compile(
    r"(?:^.{4,}?)"           # skip at least 4 chars of prefix
    r"(\d{3})"               # 3-digit EIA code
    r"(?=[A-Z])",            # followed by a letter (capacitor tolerance/packaging)
    re.IGNORECASE,
)


def _parse_supplier_attribute_capacitance(part) -> float | None:
    """Stage 1: Parse capacitance from structured supplier attribute."""
    from footfindr.core.units import parse_capacitance

    # Direct attribute
    cap_attr = getattr(part, "capacitance", None)
    if cap_attr is not None:
        if isinstance(cap_attr, (int, float)):
            return float(cap_attr)
        if isinstance(cap_attr, str) and cap_attr.strip():
            return parse_capacitance(cap_attr)

    # Parameters dict
    params = getattr(part, "parameters", None)
    if isinstance(params, dict):
        for key in ("capacitance", "Capacitance", "cap", "Cap"):
            val = params.get(key)
            if val is not None:
                if isinstance(val, (int, float)):
                    return float(val)
                if isinstance(val, str) and val.strip():
                    return parse_capacitance(val)

    return None


def _parse_eia_capacitance_from_mpn(mpn: str) -> float | None:
    """Stage 3: Extract EIA capacitance code from MPN.

    EIA code: 3-digit code where first 2 digits = significand,
    third digit = exponent (number of trailing zeros in pF).
    E.g., 475 = 47 × 10^5 pF = 4.7µF

    Returns value in Farads or None.
    """
    if not mpn or len(mpn) < 7:
        return None

    m = _MPN_CAP_SEARCH.search(mpn)
    if not m:
        return None

    code = m.group(1)
    sig1, sig2, exp = int(code[0]), int(code[1]), int(code[2])
    significand = sig1 * 10 + sig2
    if significand == 0:
        return None

    # Result in picofarads, then convert to farads
    pf = significand * (10 ** exp)
    farads = pf * 1e-12

    # Sanity check: capacitors range from ~0.1pF to ~10mF
    if farads < 0.1e-12 or farads > 10e-3:
        return None

    return farads


def parse_capacitance_cascade(part) -> tuple[float | None, str]:
    """Three-stage capacitance parsing cascade.

    Returns:
        (capacitance_in_farads, source_name)
        source_name is one of: "supplier_attribute", "description", "eia_mpn", "none"
    """
    # Stage 1: Supplier attribute
    cap = _parse_supplier_attribute_capacitance(part)
    if cap is not None:
        return cap, "supplier_attribute"

    # Stage 2: Description regex
    desc = getattr(part, "description", "") or ""
    cap = normalize_capacitance(desc)
    if cap is not None:
        return cap, "description"

    # Stage 3: EIA code from MPN
    mpn = getattr(part, "mpn", "") or ""
    cap = _parse_eia_capacitance_from_mpn(mpn)
    if cap is not None:
        return cap, "eia_mpn"

    return None, "none"


# ---------------------------------------------------------------------------
# Evidence building with three-bucket classification (M9.5)
# ---------------------------------------------------------------------------

def _build_package_evidence(
    results: list[Any],
    cap_farads: float | None,
    voltage_v: float | None,
    target_package: str,
    query: str,
) -> PackageEvidence:
    """Classify every result into three buckets and build detailed evidence.

    Buckets:
        verified_viable:    All parseable attributes match requirements.
        definitive_reject:  At least one attribute parsed AND mismatches.
        unverified:         Insufficient parsed data to determine viability.

    Only verified_viable parts count toward viable_count, active_count,
    in_stock_count, manufacturer_count, and pricing.
    """
    reject_reasons: dict[str, int] = {}
    viable: list[Any] = []
    active: list[Any] = []
    in_stock: list[Any] = []
    manufacturers: dict[str, int] = {}
    prices: list[float] = []
    lifecycle_dist: dict[str, int] = {}
    first_mpns: list[str] = []
    parsed_count = 0
    fields_present = 0
    fields_total = 0
    unverified_count = 0
    definitive_reject_count = 0

    for part in results:
        mpn = getattr(part, "mpn", "") or ""
        if len(first_mpns) < 5:
            first_mpns.append(mpn)

        # Parse fields from part
        desc = getattr(part, "description", "") or ""
        part_pkg_raw = getattr(part, "package", "") or ""
        part_pkg = normalize_package(part_pkg_raw) or normalize_package(desc) or ""

        # Capacitance cascade
        part_cap, cap_source = parse_capacitance_cascade(part)

        part_voltage = normalize_voltage(desc)
        lifecycle = (getattr(part, "lifecycle", "") or "").strip().lower()
        stock = getattr(part, "stock", 0) or 0
        mfr = (getattr(part, "manufacturer", "") or "").strip().upper()

        # Track attribute completeness
        fields_total += 5
        if part_pkg: fields_present += 1
        if part_cap is not None: fields_present += 1
        if part_voltage is not None: fields_present += 1
        if lifecycle: fields_present += 1
        if mfr: fields_present += 1

        # Lifecycle distribution
        lc_key = lifecycle if lifecycle else "unknown"
        lifecycle_dist[lc_key] = lifecycle_dist.get(lc_key, 0) + 1

        parsed_count += 1

        # --- Three-bucket classification ---
        bucket = "verified_viable"
        bucket_reject: list[str] = []
        bucket_unverified: list[str] = []

        # Check 1: Package match
        if part_pkg and part_pkg != target_package:
            if target_package.lower() not in part_pkg_raw.lower() and target_package not in desc:
                bucket = "definitive_reject"
                bucket_reject.append("wrong_package")

        # Check 2: Capacitance match
        if bucket != "definitive_reject":
            if cap_farads is not None:
                if part_cap is not None:
                    if not _cap_compatible(cap_farads, part_cap):
                        bucket = "definitive_reject"
                        bucket_reject.append("capacitance_mismatch")
                else:
                    # Capacitance unknown → unverified (NOT viable)
                    if bucket == "verified_viable":
                        bucket = "unverified"
                    bucket_unverified.append(f"capacitance_unknown (source={cap_source})")

        # Check 3: Voltage match
        if bucket not in ("definitive_reject",):
            if voltage_v is not None:
                if part_voltage is not None:
                    if part_voltage < voltage_v:
                        bucket = "definitive_reject"
                        bucket_reject.append("low_voltage")
                else:
                    # Voltage unknown but we need it → unverified
                    if bucket == "verified_viable":
                        bucket = "unverified"
                    bucket_unverified.append("voltage_unknown")

        # Check 4: Lifecycle
        if bucket not in ("definitive_reject",):
            if lifecycle in ("obsolete", "discontinued", "eol"):
                bucket = "definitive_reject"
                bucket_reject.append("lifecycle")

        # --- Record into appropriate bucket ---
        if bucket == "definitive_reject":
            definitive_reject_count += 1
            for reason in bucket_reject:
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

        elif bucket == "unverified":
            unverified_count += 1
            reject_reasons["unverified"] = reject_reasons.get("unverified", 0) + 1
            # Retained for debug/evidence but NOT counted as viable

        else:
            # verified_viable
            viable.append(part)

            if lifecycle in ("active", "new", "") or lifecycle is None:
                active.append(part)

            if stock > 0:
                in_stock.append(part)

            if mfr:
                manufacturers[mfr] = manufacturers.get(mfr, 0) + 1

            # Price
            price = _get_unit_price(part)
            if price is not None and price > 0:
                prices.append(price)

    # Manufacturer entropy (viable set only)
    mfr_entropy = 0.0
    total_mfr_parts = sum(manufacturers.values())
    if total_mfr_parts > 0 and len(manufacturers) > 1:
        for count in manufacturers.values():
            p = count / total_mfr_parts
            if p > 0:
                mfr_entropy -= p * math.log(p)

    # Price quantiles (viable set only)
    price_quantiles: dict[str, float] = {}
    if prices:
        prices.sort()
        n = len(prices)
        price_quantiles["Q25"] = round(prices[max(0, n // 4 - 1)], 4)
        price_quantiles["Q50"] = round(statistics.median(prices), 4)
        price_quantiles["Q75"] = round(prices[min(n - 1, 3 * n // 4)], 4)

    attr_completeness = fields_present / fields_total if fields_total > 0 else 0.0

    if len(results) == 0:
        reject_reasons["supplier_empty"] = 1

    return PackageEvidence(
        package=target_package,
        raw_count=len(results),
        parsed_count=parsed_count,
        viable_count=len(viable),
        active_count=len(active),
        in_stock_count=len(in_stock),
        total_stock=sum(getattr(p, "stock", 0) or 0 for p in in_stock),
        manufacturer_count=len(manufacturers),
        manufacturer_entropy=round(mfr_entropy, 4),
        median_price=round(statistics.median(prices), 4) if prices else None,
        price_quantiles=price_quantiles,
        lifecycle_distribution=lifecycle_dist,
        attribute_completeness=round(attr_completeness, 4),
        reject_reasons=reject_reasons,
        first_raw_mpns=first_mpns,
        query_strings=[query],
        unverified_count=unverified_count,
        definitive_reject_count=definitive_reject_count,
    )


def _cap_compatible(target: float, actual: float) -> bool:
    """Check if actual capacitance is compatible with target."""
    if target == 0 or actual == 0:
        return False
    ratio = actual / target
    return 0.5 <= ratio <= 2.0


def _evidence_to_score(
    package: str,
    dims: PackageDims,
    ev: PackageEvidence,
) -> PackageScore:
    """Convert PackageEvidence to a PackageScore."""
    # Simple viability score (used as fallback — the full utility model
    # in scoring.py replaces this for final ranking)
    score = 0.0
    if ev.viable_count > 0:
        score += min(1.0, ev.viable_count / 10.0) * 0.4
        score += min(1.0, ev.manufacturer_count / 5.0) * 0.2
        score += min(1.0, ev.in_stock_count / 5.0) * 0.2
        if ev.median_price is not None:
            score += max(0, 1.0 - ev.median_price / 1.0) * 0.2
        else:
            score += 0.1

    evidence_text = [
        f"Package {package}: {ev.viable_count} viable, {ev.active_count} active, "
        f"{ev.in_stock_count} in stock, {ev.total_stock} total units, "
        f"{ev.manufacturer_count} manufacturers",
    ]
    if ev.median_price is not None:
        evidence_text.append(f"Median unit price: ${ev.median_price:.4f}")
    else:
        evidence_text.append("No pricing data available")

    if ev.reject_reasons:
        reasons = ", ".join(f"{k}={v}" for k, v in ev.reject_reasons.items())
        evidence_text.append(f"Reject reasons: {reasons}")

    return PackageScore(
        package=package,
        viable_count=ev.viable_count,
        active_count=ev.active_count,
        in_stock_count=ev.in_stock_count,
        total_stock=ev.total_stock,
        manufacturer_count=ev.manufacturer_count,
        median_price=ev.median_price,
        length_mm=dims.length_mm,
        width_mm=dims.width_mm,
        area_mm2=dims.area_mm2,
        height_mm=dims.height_mm,
        score=round(score, 4),
        evidence=evidence_text,
        package_evidence=ev,
    )


def _get_unit_price(part: Any, qty: int = 1) -> float | None:
    """Extract unit price from a supplier part."""
    price_breaks = getattr(part, "price_breaks", None)
    if price_breaks:
        if isinstance(price_breaks, list) and len(price_breaks) > 0:
            pb = price_breaks[0]
            if isinstance(pb, dict):
                return pb.get("unit_price")
            return getattr(pb, "unit_price", None)

    unit_price = getattr(part, "unit_price", None)
    return unit_price
