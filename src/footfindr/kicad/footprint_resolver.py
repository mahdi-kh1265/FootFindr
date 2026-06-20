"""Footprint resolution from supplier/library part metadata (M9.3).

Maps supplier part metadata (package, category, attributes) to KiCad
footprint IDs using the footprint index, mapping database, and heuristics.

Resolution pipeline:
  1. Check explicit bindings (ref, MPN, IPN, category+package)
  2. Passive auto-resolve (category + imperial size → KiCad name)
  3. IC heuristic (package family + pin count + dims)
  4. Return ambiguous/missing

Safety:
  - Passives: exact package/category mapping → safe to write
  - ICs: only write if package family + pin count + exposed-pad + dims match exactly
  - If ambiguous: never write, list candidates
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from footfindr.kicad.footprint_index import FootprintIndex
    from footfindr.kicad.footprint_mappings import FootprintMappings
    from footfindr.suppliers.models import SupplierPart

logger = logging.getLogger("footfindr.kicad.footprint_resolver")


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass
class FootprintResolution:
    """Result of footprint resolution for a part.

    Possible status values:
      - "exact": single match found, safe to write
      - "ambiguous": multiple candidates, user must choose
      - "missing": index healthy but no matching footprint
      - "index_incomplete": built-in KiCad libs not indexed; can't search
      - "conflict": existing schematic footprint differs from resolved
      - "review": needs manual review
    """
    status: str
    footprint: str | None = None
    candidates: list[str] = field(default_factory=list)
    confidence: str = "low"   # "high", "medium", "low"
    reason: str = ""


# ---------------------------------------------------------------------------
# Imperial ↔ Metric mapping (passives)
# ---------------------------------------------------------------------------

_IMPERIAL_TO_METRIC: dict[str, str] = {
    "0201": "0603",
    "0402": "1005",
    "0603": "1608",
    "0805": "2012",
    "1206": "3216",
    "1210": "3225",
    "1812": "4532",
    "2010": "5025",
    "2512": "6332",
}

_METRIC_TO_IMPERIAL: dict[str, str] = {v: k for k, v in _IMPERIAL_TO_METRIC.items()}

# Category → KiCad library prefix
_PASSIVE_CATEGORY_MAP: dict[str, str] = {
    "capacitor": "Capacitor_SMD",
    "resistor": "Resistor_SMD",
    "inductor": "Inductor_SMD",
    "led": "LED_SMD",
}

# Category → KiCad footprint prefix
_PASSIVE_FP_PREFIX: dict[str, str] = {
    "capacitor": "C",
    "resistor": "R",
    "inductor": "L",
    "led": "LED",
}

# Package families for ICs
_IC_FAMILIES = {
    "DFN", "QFN", "SOIC", "SOT", "MSOP", "TSSOP", "SSOP",
    "LQFP", "TQFP", "BGA", "SOP", "SOD", "DPAK", "D2PAK",
    "WSON", "SON", "VSON", "UDFN", "WDFN",
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class FootprintResolver:
    """Map supplier/library part metadata to KiCad footprints.

    Uses a combination of:
      - Explicit user bindings (FootprintMappings)
      - Passive auto-resolution (category + imperial size)
      - IC heuristic (family + pin count + dims)
      - FootprintIndex search
    """

    def __init__(
        self,
        index: FootprintIndex,
        mappings: FootprintMappings | None = None,
    ) -> None:
        self._index = index
        self._mappings = mappings

    def resolve(
        self,
        part: SupplierPart,
        ref: str,
        category: str,
    ) -> FootprintResolution:
        """Full resolution pipeline for a supplier part.

        Args:
            part: Supplier part with package, attributes, etc.
            ref: Schematic reference designator (e.g. C1, U3).
            category: Component category (capacitor, resistor, ic, etc.).

        Returns:
            FootprintResolution with status, footprint, candidates, confidence.
        """
        # 0. Index health check — if built-in libs not indexed for passives,
        #    return INDEX_INCOMPLETE instead of false MISSING.
        if category in _PASSIVE_CATEGORY_MAP and not self._index.has_builtin_libraries():
            return FootprintResolution(
                status="index_incomplete",
                confidence="low",
                reason=(
                    f"Built-in KiCad footprints not indexed. "
                    f"Capacitor_SMD and Resistor_SMD are required for passive resolution.\n"
                    f"Run: ff fp scan --reset   (or: ff fp repair --apply)"
                ),
            )

        # 1. Check explicit bindings
        if self._mappings:
            mpn = getattr(part, "mpn", None)
            ipn = None  # IPN not available on SupplierPart
            package = self._normalize_package(getattr(part, "package", "") or "")
            mapping = self._mappings.lookup(
                ref=ref, mpn=mpn, ipn=ipn,
                category=category, package=package,
            )
            if mapping:
                return FootprintResolution(
                    status="exact",
                    footprint=mapping.footprint,
                    confidence="high",
                    reason=f"Explicit binding ({mapping.scope}): {mapping.reason}",
                )

        # 2. Passive auto-resolve
        if category in _PASSIVE_CATEGORY_MAP:
            result = self._resolve_passive(part, category)
            if result.status == "exact":
                return result

        # 3. IC heuristic
        if category in ("ic", "other"):
            result = self._resolve_ic(part)
            if result.status in ("exact", "ambiguous"):
                return result

        # 4. General search fallback
        package = self._normalize_package(getattr(part, "package", "") or "")
        if package:
            candidates = self._search_by_package(package, category)
            if len(candidates) == 1:
                return FootprintResolution(
                    status="exact",
                    footprint=candidates[0],
                    candidates=candidates,
                    confidence="medium",
                    reason=f"Single match for package '{package}'",
                )
            elif candidates:
                return FootprintResolution(
                    status="ambiguous",
                    candidates=candidates,
                    confidence="low",
                    reason=f"{len(candidates)} candidates for package '{package}'",
                )

        return FootprintResolution(
            status="missing",
            confidence="low",
            reason=f"No footprint found for {category} with package '{package}'",
        )

    def _resolve_passive(
        self, part: SupplierPart, category: str,
    ) -> FootprintResolution:
        """Resolve footprint for passive components (capacitor, resistor, etc.).

        Strategy:
          1. Extract imperial package size from part.package
          2. Build expected KiCad footprint name: e.g. C_0603_1608Metric
          3. Verify it exists in the index
        """
        package_raw = getattr(part, "package", "") or ""
        package = self._normalize_package(package_raw)

        if not package:
            return FootprintResolution(
                status="missing",
                reason=f"No package information for {category}",
            )

        imperial = self._to_imperial(package)
        metric = _IMPERIAL_TO_METRIC.get(imperial, "")

        if not imperial or not metric:
            return FootprintResolution(
                status="missing",
                reason=f"Cannot map package '{package}' to imperial/metric",
            )

        lib_prefix = _PASSIVE_CATEGORY_MAP.get(category, "")
        fp_prefix = _PASSIVE_FP_PREFIX.get(category, "")

        if not lib_prefix or not fp_prefix:
            return FootprintResolution(
                status="missing",
                reason=f"Unknown passive category: {category}",
            )

        # Build expected KiCad footprint name
        expected_name = f"{fp_prefix}_{imperial}_{metric}Metric"
        expected_id = f"{lib_prefix}:{expected_name}"

        # Check index
        record = self._index.get(expected_id)
        if record:
            return FootprintResolution(
                status="exact",
                footprint=expected_id,
                candidates=[expected_id],
                confidence="high",
                reason=f"Passive auto-resolve: {category} {imperial} → {expected_id}",
            )

        # Try searching for the imperial size in the appropriate library
        candidates = self._search_by_package(imperial, category)
        if len(candidates) == 1:
            return FootprintResolution(
                status="exact",
                footprint=candidates[0],
                candidates=candidates,
                confidence="high",
                reason=f"Passive search: {category} {imperial}",
            )
        elif candidates:
            return FootprintResolution(
                status="ambiguous",
                candidates=candidates,
                confidence="medium",
                reason=f"{len(candidates)} candidates for {category} {imperial}",
            )

        return FootprintResolution(
            status="missing",
            reason=f"Footprint {expected_id} not in index. Run: ff fp scan",
        )

    def _resolve_ic(self, part: SupplierPart) -> FootprintResolution:
        """Resolve footprint for IC components.

        Conservative: only resolve if package family, pin count, and
        body dimensions yield exactly one candidate.
        """
        package_raw = getattr(part, "package", "") or ""
        attrs = getattr(part, "attributes", {}) or {}

        # Parse package text for family + pin count
        family, pin_count, has_ep, body_dims = self._parse_ic_package(package_raw, attrs)

        if not family:
            return FootprintResolution(
                status="missing",
                reason=f"Cannot determine IC package family from '{package_raw}'",
            )

        # Build search query
        search_terms = [family]
        if pin_count:
            search_terms.append(str(pin_count))

        # Search index
        candidates: list[str] = []
        for term in search_terms:
            results = self._index.search(term)
            if not results:
                continue

            for r in results:
                fp_name = r.footprint_name
                # Check family match
                if family.upper() not in fp_name.upper():
                    continue
                # Check pin count match
                if pin_count:
                    fp_pin = self._extract_pin_count(fp_name)
                    if fp_pin and fp_pin != pin_count:
                        continue
                # Check EP match
                if has_ep and "1EP" not in fp_name and "EP" not in fp_name:
                    continue
                if not has_ep and "1EP" in fp_name:
                    continue
                # Check body dims if available
                if body_dims:
                    if body_dims not in fp_name:
                        continue

                if r.kicad_id not in candidates:
                    candidates.append(r.kicad_id)

        if len(candidates) == 1:
            return FootprintResolution(
                status="exact",
                footprint=candidates[0],
                candidates=candidates,
                confidence="high",
                reason=f"IC exact: {family}-{pin_count or '?'} → {candidates[0]}",
            )
        elif candidates:
            return FootprintResolution(
                status="ambiguous",
                candidates=candidates[:10],  # Cap at 10
                confidence="low",
                reason=(
                    f"{len(candidates)} candidates for {family}-{pin_count or '?'}. "
                    f"Use: ff fp bind {family} <kicad_id>"
                ),
            )

        return FootprintResolution(
            status="missing",
            reason=f"No footprint found for IC family {family}-{pin_count or '?'}",
        )

    def _parse_ic_package(
        self,
        package_text: str,
        attributes: dict[str, str],
    ) -> tuple[str | None, int | None, bool, str | None]:
        """Parse IC package info from text and attributes.

        Returns (family, pin_count, has_exposed_pad, body_dims).
        """
        text = package_text.strip()

        # Try to extract family
        family = None
        for fam in sorted(_IC_FAMILIES, key=len, reverse=True):
            if fam.upper() in text.upper():
                family = fam
                break

        # Pin count
        pin_count = None
        # Patterns: "DFN-10", "QFN-32-1EP", "SOIC-8", "SOT-23-5"
        pin_match = re.search(r'[-_](\d{1,3})(?:[-_]|$)', text)
        if pin_match:
            pin_count = int(pin_match.group(1))

        # Also check attributes
        if not pin_count:
            for attr_key in ("Number of Pins", "Pin Count", "Pins"):
                val = attributes.get(attr_key)
                if val:
                    try:
                        pin_count = int(re.search(r'\d+', val).group())
                    except (ValueError, AttributeError):
                        pass

        # Exposed pad
        has_ep = bool(re.search(r'EP|Exposed\s*Pad', text, re.IGNORECASE))

        # Body dims
        body_dims = None
        dims_match = re.search(r'(\d+(?:\.\d+)?x\d+(?:\.\d+)?mm)', text)
        if dims_match:
            body_dims = dims_match.group(1)

        return family, pin_count, has_ep, body_dims

    @staticmethod
    def _extract_pin_count(footprint_name: str) -> int | None:
        """Extract pin count from a footprint name like QFN-32-1EP_5x5mm."""
        m = re.search(r'[-_](\d{1,3})[-_]', footprint_name)
        if m:
            return int(m.group(1))
        return None

    def _search_by_package(self, package: str, category: str) -> list[str]:
        """Search footprint index for a package in the appropriate library."""
        results = self._index.search(package)
        if not results:
            return []

        # Filter by category-appropriate library
        lib_prefix = _PASSIVE_CATEGORY_MAP.get(category)
        if lib_prefix:
            filtered = [r.kicad_id for r in results if r.library_nickname == lib_prefix]
            if filtered:
                return filtered

        return [r.kicad_id for r in results]

    @staticmethod
    def _normalize_package(raw: str) -> str:
        """Normalize package text.

        "0603 (1608 Metric)" → "0603"
        "0805" → "0805"
        "QFN-32" → "QFN-32"
        """
        if not raw:
            return ""
        # Strip metric annotation in parentheses
        m = re.match(r'^(\S+)\s*\(', raw)
        if m:
            return m.group(1).strip()
        return raw.strip()

    @staticmethod
    def _to_imperial(package: str) -> str:
        """Convert a package token to imperial if it's metric, or return as-is.

        "0603" → "0603" (already imperial — prioritize imperial interpretation)
        "1608" → "0603" (metric → imperial)

        Note: Some codes like 0603 are ambiguous (valid imperial AND metric).
        We prioritize imperial since supplier data almost always uses imperial.
        """
        # Check if it's a known imperial size FIRST (priority)
        if package in _IMPERIAL_TO_METRIC:
            return package
        # Check if it's a known metric size
        if package in _METRIC_TO_IMPERIAL:
            return _METRIC_TO_IMPERIAL[package]
        # Try to extract 4-digit codes
        m = re.match(r'^(\d{4})$', package)
        if m:
            return m.group(1)
        return package
