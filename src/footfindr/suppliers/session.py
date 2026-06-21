"""Active supplier search session state manager.

Stores/loads the current search context at
``.footfindr/session/supplier_search.json``.  Enables dot-syntax
(``ff supplier show . 3``) across sequential CLI invocations.

Session state is project-local and does NOT affect the resolver or
schematic writes.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from footfindr.suppliers.models import PriceBreak, SupplierPart

logger = logging.getLogger("footfindr.suppliers.session")

# ---------------------------------------------------------------------------
# Field alias mapping — friendly names for group/filter/sort
# ---------------------------------------------------------------------------

_FIELD_ALIASES: dict[str, str] = {
    # package
    "pkg": "package",
    "pack": "package",
    "case": "package",
    "package": "package",
    # temperature
    "temp": "temperature_range",
    "temperature": "temperature_range",
    "operating-temperature": "temperature_range",
    "temperature_range": "temperature_range",
    # stock
    "stock": "stock",
    "qty": "stock",
    "quantity": "stock",
    # price
    "price": "price",
    "cost": "price",
    # lifecycle
    "lifecycle": "lifecycle",
    "status": "lifecycle",
    "stat": "lifecycle",
    # supplier
    "supplier": "supplier",
    "sup": "supplier",
    "vendor": "supplier",
    # manufacturer
    "manufacturer": "manufacturer",
    "mfr": "manufacturer",
    # packaging (reel/tape)
    "packaging": "packaging",
    "pkgtype": "packaging",
    "reel": "packaging",
    "tape": "packaging",
    "tube": "packaging",
    # mounting
    "mounting": "mounting_type",
    "mounting_type": "mounting_type",
    "mount": "mounting_type",
    # description
    "description": "description",
    "desc": "description",
    # mpn
    "mpn": "mpn",
    "part": "mpn",
    "partnumber": "mpn",
    # supplier_pn
    "supplier_pn": "supplier_pn",
    "sku": "supplier_pn",
    "spn": "supplier_pn",
    "supplier-part": "supplier_pn",
    # lead time
    "lead_time": "lead_time",
    "lead": "lead_time",
    # product url
    "product_url": "product_url",
    "url": "product_url",
    # datasheet
    "datasheet_url": "datasheet_url",
    "datasheet": "datasheet_url",
    "ds": "datasheet_url",
    # --- Constraint-oriented aliases (M8.7) ---
    # voltage
    "voltage": "voltage",
    "volt": "voltage",
    "v": "voltage",
    # capacitance
    "capacitance": "capacitance",
    "cap": "capacitance",
    # resistance
    "resistance": "resistance",
    "res": "resistance",
    # current
    "current": "current",
    "curr": "current",
    "i": "current",
    # frequency
    "frequency": "frequency",
    "freq": "frequency",
    # tolerance
    "tolerance": "tolerance",
    "tol": "tolerance",
    # dielectric
    "dielectric": "dielectric",
    "diel": "dielectric",
    # family (for IC part family matching)
    "family": "family",
    "fam": "family",
}

# Canonical fields with their display name, aliases, and whether they are always
# available (i.e. present on every SupplierPart).
_CANONICAL_FIELDS: list[tuple[str, str, list[str], bool]] = [
    # (canonical, display_name, aliases, always_available)
    ("mpn", "MPN", ["part", "partnumber"], True),
    ("supplier", "Supplier", ["vendor"], True),
    ("supplier_pn", "Supplier PN", ["sku", "supplier-part"], True),
    ("manufacturer", "Manufacturer", ["mfr"], True),
    ("description", "Description", ["desc"], True),
    ("package", "Package", ["pkg", "case"], False),
    ("stock", "Stock", ["qty", "quantity"], True),
    ("price", "Price", ["cost"], False),
    ("lifecycle", "Lifecycle", ["status"], False),
    ("temperature_range", "Temperature", ["temp", "temperature"], False),
    ("packaging", "Packaging", ["reel", "tape", "tube"], False),
    ("mounting_type", "Mounting", ["mounting", "mount"], False),
    ("lead_time", "Lead Time", ["lead"], False),
    ("datasheet_url", "Datasheet", ["datasheet"], False),
    ("product_url", "Product URL", ["url"], False),
]

# Default sort directions per field
_SORT_DEFAULTS: dict[str, bool] = {
    "stock": True,        # descending (high stock first)
    "price": False,       # ascending (cheap first)
    "package": False,     # ascending (alphabetical)
    "temperature_range": False,
    "lifecycle": False,
    "supplier": False,
    "manufacturer": False,
    "packaging": False,
    "mounting_type": False,
    "mpn": False,
}

# Named views: each maps to a list of columns to show
NAMED_VIEWS: dict[str, list[str]] = {
    "stock": ["mpn", "stock", "badges"],
    "package": ["mpn", "package", "supplier_device_package", "badges"],
    "price": ["mpn", "price", "moq", "stock"],
    "sourcing": ["mpn", "supplier_pn", "stock", "price", "lead_time"],
    "specs": ["mpn", "package", "temperature_range"],
}


def resolve_field_alias(name: str) -> str:
    """Resolve a user-typed field name to the canonical field name."""
    return _FIELD_ALIASES.get(name.lower().strip(), name.lower().strip())


def default_sort_descending(field_name: str) -> bool:
    """Return the default sort direction for a canonical field."""
    return _SORT_DEFAULTS.get(field_name, False)


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

def compute_relevance(part: SupplierPart, query: str) -> int:
    """Score how relevant a part is to the query.

    Returns:
        0 = exact MPN match
        1 = MPN starts with query
        2 = MPN contains query as meaningful token
        3 = description strongly matches query / parametric match
        4 = low relevance (query only in description/accessory context)
        5 = unrelated
    """
    q_upper = query.upper().strip()
    mpn_upper = (part.mpn or "").upper()

    # For parametric/keyword queries, MPN substring matching is meaningless.
    # Return a moderate relevance — actual filtering is done by constraints.
    if not is_mpn_like_query(query):
        return 3

    if mpn_upper == q_upper:
        return 0
    if mpn_upper.startswith(q_upper):
        return 1
    if q_upper in mpn_upper:
        return 2

    # Check description for strong match
    desc_upper = (part.description or "").upper()
    if q_upper in desc_upper:
        return 3

    # Check manufacturer MPN family pattern
    # e.g. query "AD9959" should match "AD9959BCPZ" but not "ZUSA-HT-3030"
    mfr = (part.manufacturer or "").upper()
    if q_upper in mfr:
        return 3

    return 5


def is_mpn_like_query(query: str) -> bool:
    """Heuristic: does the query look like a part number family?

    Part families typically have uppercase letters + digits, no spaces,
    and no natural-language words.
    """
    q = query.strip()
    # Short or empty
    if len(q) < 2:
        return False
    # Contains spaces → likely natural language
    if " " in q and not any(sep in q for sep in [",", ";", "/"]):
        return False
    # Looks like a part number: alpha + digits, or digits + alpha
    alphanumeric = re.sub(r"[^A-Za-z0-9]", "", q)
    has_letters = any(c.isalpha() for c in alphanumeric)
    has_digits = any(c.isdigit() for c in alphanumeric)
    if has_letters and has_digits:
        return True
    # Pure uppercase like "LDO" could be a category, not MPN
    if q.isupper() and len(q) <= 4 and not has_digits:
        return False
    # If it has special part-number chars
    if any(c in q for c in "#/-_"):
        return True
    return False


# ---------------------------------------------------------------------------
# Field discovery
# ---------------------------------------------------------------------------

@dataclass
class FieldInfo:
    """Information about a field available on the current result set."""
    canonical: str
    display_name: str
    aliases: list[str]
    coverage: str  # e.g. "4/4"
    is_attribute: bool = False  # True for dynamic attributes from supplier params


def discover_fields(results: list[SupplierPart]) -> list[FieldInfo]:
    """Discover available fields on the current result set.

    Returns both standard fields and dynamic attributes found in the
    parts' ``attributes`` dicts.
    """
    total = len(results)
    if total == 0:
        return []

    infos: list[FieldInfo] = []

    for canon, display, aliases, always in _CANONICAL_FIELDS:
        if always:
            infos.append(FieldInfo(canon, display, aliases, f"{total}/{total}"))
        else:
            count = 0
            for r in results:
                val = _get_field_value(r, canon)
                if val and val != "-":
                    count += 1
            infos.append(FieldInfo(canon, display, aliases, f"{count}/{total}"))

    # Dynamic attributes from supplier parameters
    attr_keys: dict[str, int] = {}
    for r in results:
        for k in r.attributes:
            if r.attributes[k] and r.attributes[k] != "-":
                attr_keys[k] = attr_keys.get(k, 0) + 1

    for key in sorted(attr_keys):
        # Skip keys already covered by canonical fields
        if key.lower().replace(" ", "_") in _FIELD_ALIASES:
            continue
        if key in ("Package / Case", "Mounting Type", "Operating Temperature",
                    "Supplier Device Package"):
            continue  # Already mapped to canonical fields
        infos.append(FieldInfo(
            canonical=key,
            display_name=key,
            aliases=[],
            coverage=f"{attr_keys[key]}/{total}",
            is_attribute=True,
        ))

    return infos


def _get_field_value(part: SupplierPart, canon: str) -> str:
    """Get a field value as string for coverage calculation."""
    if canon == "price":
        bp = part.best_price()
        return f"${bp:.4f}" if bp else ""
    if canon == "package":
        return part.package or part.supplier_device_package or part.attributes.get("Package / Case", "")
    if canon == "temperature_range":
        return part.temperature_range or part.attributes.get("Operating Temperature", "")
    if canon == "mounting_type":
        return part.mounting_type or part.attributes.get("Mounting Type", "")
    return str(getattr(part, canon, "") or "")


# ---------------------------------------------------------------------------
# Session data structures
# ---------------------------------------------------------------------------

@dataclass
class SearchFilter:
    """A single filter applied to the active result set."""
    field: str          # canonical field name
    op: str             # "contains", "eq", "gt", "lt", "gte", "lte"
    value: str          # user-supplied filter value


@dataclass
class SearchSession:
    """Full active supplier search state."""
    query: str
    suppliers: list[str]
    created_at: str
    last_updated: str

    # Results
    original_results: list[SupplierPart]  # never modified after search
    active_result_ids: list[str]  # result_id values, narrowed by filters

    # Interaction state
    filters: list[SearchFilter] = field(default_factory=list)
    sort_fields: list[str] = field(default_factory=list)
    sort_descending: bool = True
    selected_result_id: str | None = None
    quantity: int | None = None

    # Pagination (M9.3)
    page_size: int = 10
    current_page: int = 1
    provider_offsets: dict[str, int] = field(default_factory=dict)

    # Degraded pagination state (M9.3c)
    pagination_status: str = "normal"  # "normal", "degraded", or "unverified"
    provider_status: dict[str, dict] = field(default_factory=dict)

    # Ref context for expand (M9.3c expand)
    ref_name: str | None = None             # e.g. "C1"
    base_query_parts: list[str] = field(default_factory=list)  # e.g. ["4.7uF", "25V", "0603"]
    category: str | None = None             # e.g. "capacitor"

    # Expansion metadata (M9.3c expand)
    expanded: bool = False
    expansion_strategy: str | None = None
    expansion_queries_run: int = 0
    expansion_new_results: int = 0

    def get_active_results(self) -> list[SupplierPart]:
        """Return the filtered/sorted subset of results."""
        id_set = set(self.active_result_ids)
        active = [r for r in self.original_results if r.result_id in id_set]

        # Apply sorting
        if self.sort_fields:
            active = _sort_parts(active, self.sort_fields, self.sort_descending, self.quantity)

        return active

    def get_selected(self) -> SupplierPart | None:
        """Return the selected result, or None."""
        if not self.selected_result_id:
            return None
        for r in self.original_results:
            if r.result_id == self.selected_result_id:
                return r
        return None

    def get_by_index(self, index: int) -> SupplierPart | None:
        """Return a result by 1-based display index in active results."""
        active = self.get_active_results()
        if 1 <= index <= len(active):
            return active[index - 1]
        return None

    def result_by_id(self, result_id: str) -> SupplierPart | None:
        """Lookup any original result by its stable ID."""
        for r in self.original_results:
            if r.result_id == result_id:
                return r
        return None

    def get_status_line(self) -> str:
        """Return a compact status line showing current session state."""
        parts = [f"Search: {self.query}"]

        # Supplier info
        if len(self.suppliers) == 1:
            parts.append(f"supplier: {self.suppliers[0]}")
        elif self.suppliers:
            parts.append(f"suppliers: {','.join(self.suppliers)}")

        # Result count
        total = len(self.original_results)
        active = len(self.active_result_ids)
        if active != total:
            parts.append(f"results: {active}/{total}")
        else:
            parts.append(f"results: {total}")

        # Sort
        if self.sort_fields:
            direction = "desc" if self.sort_descending else "asc"
            parts.append(f"sort: {','.join(self.sort_fields)} {direction}")

        # Filters
        if self.filters:
            filter_descs = [f"{f.field} {f.op} '{f.value}'" for f in self.filters]
            parts.append(f"filters: {'; '.join(filter_descs)}")

        return " | ".join(parts)

    # --- Pagination methods (M9.3) ---

    def total_pages(self) -> int:
        """Total pages based on active results and page size."""
        total = len(self.get_active_results())
        return max(1, math.ceil(total / self.page_size))

    def get_page(self, page: int) -> list[SupplierPart]:
        """Return the subset of active results for a given page (1-based)."""
        active = self.get_active_results()
        page = max(1, min(page, self.total_pages()))
        start = (page - 1) * self.page_size
        end = start + self.page_size
        return active[start:end]

    def get_current_page(self) -> list[SupplierPart]:
        """Return results for the current page."""
        return self.get_page(self.current_page)

    def has_next_page(self) -> bool:
        """True if there are more results beyond the current page."""
        return self.current_page < self.total_pages()

    def has_prev_page(self) -> bool:
        """True if there is a previous page."""
        return self.current_page > 1

    def get_page_status_line(self) -> str:
        """Compact page status: 'Page: 1 | Showing: 1-10 of 20 fetched'."""
        active = self.get_active_results()
        total = len(active)
        if total == 0:
            return "No results"
        start = (self.current_page - 1) * self.page_size + 1
        end = min(self.current_page * self.page_size, total)
        return f"Page: {self.current_page}/{self.total_pages()} | Showing: {start}-{end} of {total} fetched"

    def is_single_supplier(self) -> bool:
        """True if all active results are from the same supplier."""
        active = self.get_active_results()
        if not active:
            return True
        return len(set(r.supplier for r in active)) == 1

    def is_single_manufacturer(self) -> bool:
        """True if all active results have the same manufacturer."""
        active = self.get_active_results()
        if not active:
            return True
        mfrs = set((r.manufacturer or "").strip() for r in active)
        return len(mfrs) <= 1


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def _sort_key(part: SupplierPart, fields: list[str], qty: int | None) -> tuple:
    """Build a sort key tuple for a part."""
    keys: list[Any] = []
    for f in fields:
        if f == "price":
            bp = part.best_price(qty)
            keys.append(bp if bp is not None else float("inf"))
        elif f == "stock":
            keys.append(part.stock if part.stock is not None else 0)
        elif f == "lifecycle":
            # active=0, nrnd=1, obsolete=2, unknown=3
            lc = (part.lifecycle or "").lower()
            order = {"active": 0, "nrnd": 1, "not recommended": 1,
                     "obsolete": 2, "discontinued": 2, "eol": 2}
            keys.append(order.get(lc, 3))
        else:
            # Check part attributes dict too
            val = getattr(part, f, None)
            if val is None:
                val = part.attributes.get(f, "")
            keys.append(str(val or "").lower())
    return tuple(keys)


def _sort_parts(
    parts: list[SupplierPart],
    fields: list[str],
    descending: bool,
    qty: int | None = None,
) -> list[SupplierPart]:
    """Sort parts by given fields."""
    return sorted(parts, key=lambda p: _sort_key(p, fields, qty), reverse=descending)


# ---------------------------------------------------------------------------
# Default interleaved sort for multi-supplier results
# ---------------------------------------------------------------------------

def default_interleave_sort(
    parts: list[SupplierPart],
    query: str,
    qty: int | None = None,
) -> list[SupplierPart]:
    """Sort multi-supplier results by relevance.

    Priority: MPN family relevance > active lifecycle > in stock > lower price > supplier.
    """
    q_upper = query.upper().strip()

    def sort_key(p: SupplierPart) -> tuple:
        # MPN relevance score (lower is better)
        mpn_score = compute_relevance(p, query)

        # Active lifecycle (lower is better)
        lc = (p.lifecycle or "").lower()
        lc_order = {"active": 0, "nrnd": 1, "not recommended": 1,
                     "obsolete": 2, "discontinued": 2}
        lc_score = lc_order.get(lc, 2)

        # In stock (lower is better: 0=in stock, 1=no stock)
        stock_score = 0 if (p.stock and p.stock > 0) else 1

        # Price (lower is better)
        bp = p.best_price(qty)
        price_score = bp if bp is not None else float("inf")

        # Supplier alphabetical
        supplier_score = p.supplier or ""

        return (mpn_score, lc_score, stock_score, price_score, supplier_score)

    return sorted(parts, key=sort_key)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _normalize_package(s: str) -> str:
    """Normalize a package string for fuzzy matching.

    ``DFN-10``, ``DFN10``, ``DFN 10``, ``10-DFN`` -> ``dfn10``
    """
    s = s.lower().strip()
    s = re.sub(r"[\s\-_]+", "", s)
    return s


def apply_filter(part: SupplierPart, filt: SearchFilter) -> bool:
    """Return True if the part passes the filter."""
    canon = filt.field
    val = filt.value

    # Get the field value from the part
    if canon == "price":
        raw = str(part.best_price() or "")
    elif canon in ("package", "pkg"):
        # Try package, then supplier_device_package, then attributes
        raw = part.package or part.supplier_device_package or part.attributes.get("Package / Case", "")
    elif canon == "temperature_range":
        raw = part.temperature_range or part.attributes.get("Operating Temperature", "")
    elif canon == "relevance":
        # Special: filter by relevance level
        # Value should reference session query, but we don't have it here
        # This is handled in the CLI layer instead
        return True
    else:
        raw = str(getattr(part, canon, "") or "")
        # Also check attributes
        if not raw and canon in (part.attributes or {}):
            raw = part.attributes[canon]

    # Operators
    if filt.op == "contains":
        if canon in ("package",):
            return _normalize_package(val) in _normalize_package(raw)
        return val.lower() in raw.lower()
    elif filt.op == "eq":
        return raw.lower() == val.lower()
    elif filt.op in ("gt", "gte", "lt", "lte"):
        try:
            raw_num = float(raw.replace(",", ""))
            val_num = float(val.replace(",", ""))
        except (ValueError, TypeError):
            return False
        if filt.op == "gt":
            return raw_num > val_num
        elif filt.op == "gte":
            return raw_num >= val_num
        elif filt.op == "lt":
            return raw_num < val_num
        elif filt.op == "lte":
            return raw_num <= val_num
    return True


def parse_filter_value(value_str: str) -> tuple[str, str]:
    """Parse a filter value like '>100' into (op, value).

    Returns (op, clean_value). Defaults to 'contains'.
    """
    s = value_str.strip()
    if s.startswith(">="):
        return "gte", s[2:].strip()
    elif s.startswith("<="):
        return "lte", s[2:].strip()
    elif s.startswith(">"):
        return "gt", s[1:].strip()
    elif s.startswith("<"):
        return "lt", s[1:].strip()
    elif s.startswith("="):
        return "eq", s[1:].strip()
    else:
        return "contains", s


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

class SessionManager:
    """Manage active supplier search session state."""

    def __init__(self, workspace: Path | None = None) -> None:
        from footfindr.config import get_workspace as _gw
        ws = Path(workspace) if workspace else _gw()
        self._session_dir = ws / "session"
        self._session_file = self._session_dir / "supplier_search.json"

    def save(self, session: SearchSession) -> None:
        """Save active search session to disk."""
        self._session_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "query": session.query,
            "suppliers": session.suppliers,
            "created_at": session.created_at,
            "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "filters": [
                {"field": f.field, "op": f.op, "value": f.value}
                for f in session.filters
            ],
            "sort_fields": session.sort_fields,
            "sort_descending": session.sort_descending,
            "selected_result_id": session.selected_result_id,
            "quantity": session.quantity,
            "active_result_ids": session.active_result_ids,
            "original_results": [_part_to_dict(p) for p in session.original_results],
            # Pagination (M9.3)
            "page_size": session.page_size,
            "current_page": session.current_page,
            "provider_offsets": session.provider_offsets,
            # Degraded pagination state (M9.3c)
            "pagination_status": session.pagination_status,
            "provider_status": session.provider_status,
            # Ref context for expand (M9.3c expand)
            "ref_name": session.ref_name,
            "base_query_parts": session.base_query_parts,
            "category": session.category,
            # Expansion metadata (M9.3c expand)
            "expanded": session.expanded,
            "expansion_strategy": session.expansion_strategy,
            "expansion_queries_run": session.expansion_queries_run,
            "expansion_new_results": session.expansion_new_results,
        }
        self._session_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def load(self) -> SearchSession | None:
        """Load active search session from disk. Returns None if none exists."""
        if not self._session_file.exists():
            return None
        try:
            data = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load session: {e}")
            return None

        original = [_dict_to_part(d) for d in data.get("original_results", [])]
        filters = [
            SearchFilter(field=f["field"], op=f["op"], value=f["value"])
            for f in data.get("filters", [])
        ]

        return SearchSession(
            query=data.get("query", ""),
            suppliers=data.get("suppliers", []),
            created_at=data.get("created_at", ""),
            last_updated=data.get("last_updated", ""),
            original_results=original,
            active_result_ids=data.get("active_result_ids", [r.result_id for r in original]),
            filters=filters,
            sort_fields=data.get("sort_fields", []),
            sort_descending=data.get("sort_descending", True),
            selected_result_id=data.get("selected_result_id"),
            quantity=data.get("quantity"),
            # Pagination (M9.3)
            page_size=data.get("page_size", 10),
            current_page=data.get("current_page", 1),
            provider_offsets=data.get("provider_offsets", {}),
            # Degraded pagination state (M9.3c)
            pagination_status=data.get("pagination_status", "normal"),
            provider_status=data.get("provider_status", {}),
            # Ref context for expand (M9.3c expand)
            ref_name=data.get("ref_name"),
            base_query_parts=data.get("base_query_parts", []),
            category=data.get("category"),
            # Expansion metadata (M9.3c expand)
            expanded=data.get("expanded", False),
            expansion_strategy=data.get("expansion_strategy"),
            expansion_queries_run=data.get("expansion_queries_run", 0),
            expansion_new_results=data.get("expansion_new_results", 0),
        )

    def clear(self) -> None:
        """Remove the active search session."""
        if self._session_file.exists():
            self._session_file.unlink()

    def require_session(self) -> SearchSession:
        """Load session or raise with helpful error."""
        session = self.load()
        if session is None:
            raise SessionError(
                "No active supplier search.\nRun:\n  ff supplier search <query>"
            )
        return session


class SessionError(Exception):
    """Raised when session operation fails."""


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _part_to_dict(p: SupplierPart) -> dict[str, Any]:
    """Serialize a SupplierPart to a JSON-safe dict."""
    return {
        "supplier": p.supplier,
        "supplier_pn": p.supplier_pn,
        "supplier_url": p.supplier_url,
        "mpn": p.mpn,
        "manufacturer": p.manufacturer,
        "description": p.description,
        "stock": p.stock,
        "price_breaks": [
            {"quantity": pb.quantity, "unit_price": pb.unit_price, "currency": pb.currency}
            for pb in p.price_breaks
        ],
        "currency": p.currency,
        "minimum_order_quantity": p.minimum_order_quantity,
        "packaging": p.packaging,
        "lead_time": p.lead_time,
        "datasheet_url": p.datasheet_url,
        "lifecycle": p.lifecycle,
        "last_checked": p.last_checked,
        "source": p.source,
        "category": p.category,
        "package": p.package,
        "product_url": p.product_url,
        "lcsc_pn": p.lcsc_pn,
        "jlc_category": p.jlc_category,
        "mounting_type": p.mounting_type,
        "temperature_range": p.temperature_range,
        "supplier_device_package": p.supplier_device_package,
        "product_status": p.product_status,
        "attributes": p.attributes,
        "badges": p.badges,
    }


def _dict_to_part(d: dict[str, Any]) -> SupplierPart:
    """Deserialize a dict to a SupplierPart."""
    pbs = [
        PriceBreak(
            quantity=pb.get("quantity", 1),
            unit_price=pb.get("unit_price", 0.0),
            currency=pb.get("currency", "USD"),
        )
        for pb in d.get("price_breaks", [])
    ]
    return SupplierPart(
        supplier=d.get("supplier", ""),
        supplier_pn=d.get("supplier_pn"),
        supplier_url=d.get("supplier_url"),
        mpn=d.get("mpn", ""),
        manufacturer=d.get("manufacturer"),
        description=d.get("description"),
        stock=d.get("stock"),
        price_breaks=pbs,
        currency=d.get("currency", "USD"),
        minimum_order_quantity=d.get("minimum_order_quantity"),
        packaging=d.get("packaging"),
        lead_time=d.get("lead_time"),
        datasheet_url=d.get("datasheet_url"),
        lifecycle=d.get("lifecycle"),
        last_checked=d.get("last_checked"),
        source=d.get("source", "cache"),
        category=d.get("category"),
        package=d.get("package"),
        product_url=d.get("product_url"),
        lcsc_pn=d.get("lcsc_pn"),
        jlc_category=d.get("jlc_category"),
        mounting_type=d.get("mounting_type"),
        temperature_range=d.get("temperature_range"),
        supplier_device_package=d.get("supplier_device_package"),
        product_status=d.get("product_status"),
        attributes=d.get("attributes", {}),
        badges=d.get("badges", []),
    )
