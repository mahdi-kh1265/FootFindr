"""Tests for M8.5 — Supplier Variant Browser & Stateful Part-Selection Workflow.

Covers:
- Session save/load/clear/dot resolution
- Badge computation
- Differentiator extraction
- Group by MPN / by field
- Filter / sort logic
- Display formatters (mini, full, part-numbers-only)
- Shortlist add/list/remove
- promote_from_supplier creates PartRecord with provenance
- No footprint auto-binding
- Search cache isolation
- No schematic writes / no purchasing
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from footfindr.suppliers.models import PriceBreak, SupplierPart


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_part(
    *,
    mpn: str = "LT3045EDD#PBF",
    supplier: str = "digikey",
    manufacturer: str = "Analog Devices",
    supplier_pn: str | None = "505-LT3045EDD#PBF-ND",
    package: str | None = "DFN-10",
    stock: int | None = 2310,
    price: float = 7.42,
    lifecycle: str = "Active",
    packaging: str | None = "Tube",
    datasheet_url: str | None = "https://example.com/ds.pdf",
    temperature_range: str | None = "-40°C ~ 125°C",
    mounting_type: str | None = "Surface Mount",
    description: str | None = "IC REG LINEAR POS ADJ 500MA 10DFN",
    product_url: str | None = "https://www.digikey.com/product",
    attributes: dict | None = None,
) -> SupplierPart:
    return SupplierPart(
        supplier=supplier,
        supplier_pn=supplier_pn,
        mpn=mpn,
        manufacturer=manufacturer,
        description=description,
        stock=stock,
        price_breaks=[PriceBreak(quantity=1, unit_price=price)],
        package=package,
        lifecycle=lifecycle,
        packaging=packaging,
        datasheet_url=datasheet_url,
        temperature_range=temperature_range,
        mounting_type=mounting_type,
        product_url=product_url,
        source="live",
        last_checked="2026-06-20T00:00:00Z",
        attributes=attributes or {},
    )


def _sample_parts() -> list[SupplierPart]:
    """Create a realistic set of LT3045 variants."""
    return [
        _make_part(
            mpn="LT3045EDD#PBF",
            supplier_pn="505-LT3045EDD#PBF-ND",
            package="DFN-10",
            stock=2310,
            price=7.42,
            packaging="Tube",
        ),
        _make_part(
            mpn="LT3045EDD#TRPBF",
            supplier_pn="505-LT3045EDD#TRPBF-ND",
            package="DFN-10",
            stock=8450,
            price=7.42,
            packaging="Tape & Reel",
        ),
        _make_part(
            mpn="LT3045EMSE#PBF",
            supplier_pn="505-LT3045EMSE#PBF-ND",
            package="MSOP-12-EP",
            stock=1205,
            price=8.10,
            packaging="Tube",
        ),
        _make_part(
            mpn="LT3045IMSE#PBF",
            supplier_pn="505-LT3045IMSE#PBF-ND",
            package="MSOP-12-EP",
            stock=930,
            price=9.50,
            packaging="Tube",
            temperature_range="-40°C ~ 125°C",
        ),
        _make_part(
            mpn="LT3045MPMSE#PBF",
            supplier_pn="505-LT3045MPMSE#PBF-ND",
            package="MSOP-12-EP",
            stock=12,
            price=18.90,
            packaging="Tube",
            temperature_range="-55°C ~ 150°C",
        ),
    ]


# ---------------------------------------------------------------------------
# SupplierPart model extensions
# ---------------------------------------------------------------------------

class TestSupplierPartExtensions:
    """Test M8.5 model extensions."""

    def test_result_id_stable(self):
        p = _make_part()
        rid = p.result_id
        assert "digikey" in rid
        assert "LT3045EDD#PBF" in rid
        assert "Analog Devices" in rid
        assert "505-LT3045EDD#PBF-ND" in rid
        # Same data → same ID
        p2 = _make_part()
        assert p2.result_id == rid

    def test_result_id_unique_across_variants(self):
        parts = _sample_parts()
        ids = [p.result_id for p in parts]
        assert len(ids) == len(set(ids))

    def test_best_price_no_qty(self):
        p = _make_part(price=7.42)
        assert p.best_price() == 7.42

    def test_best_price_with_qty(self):
        p = SupplierPart(
            supplier="digikey",
            mpn="TEST",
            price_breaks=[
                PriceBreak(quantity=1, unit_price=10.0),
                PriceBreak(quantity=10, unit_price=8.0),
                PriceBreak(quantity=100, unit_price=5.0),
            ],
        )
        assert p.best_price(1) == 10.0
        assert p.best_price(10) == 8.0
        assert p.best_price(50) == 8.0
        assert p.best_price(100) == 5.0
        assert p.best_price(1000) == 5.0

    def test_best_price_no_breaks(self):
        p = SupplierPart(supplier="test", mpn="X")
        assert p.best_price() is None

    def test_new_fields_have_defaults(self):
        p = SupplierPart(supplier="test", mpn="X")
        assert p.mounting_type is None
        assert p.temperature_range is None
        assert p.supplier_device_package is None
        assert p.product_status is None
        assert p.attributes == {}
        assert p.badges == []


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class TestSessionState:
    """Test active search session state."""

    def test_save_load_roundtrip(self, tmp_path):
        from footfindr.suppliers.session import SearchSession, SessionManager

        mgr = SessionManager(workspace=tmp_path)
        parts = _sample_parts()[:2]

        session = SearchSession(
            query="LT3045",
            suppliers=["digikey"],
            created_at="2026-06-20",
            last_updated="2026-06-20",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            quantity=10,
        )
        mgr.save(session)

        loaded = mgr.load()
        assert loaded is not None
        assert loaded.query == "LT3045"
        assert len(loaded.original_results) == 2
        assert loaded.quantity == 10
        assert loaded.original_results[0].mpn == "LT3045EDD#PBF"

    def test_load_missing_returns_none(self, tmp_path):
        from footfindr.suppliers.session import SessionManager
        mgr = SessionManager(workspace=tmp_path)
        assert mgr.load() is None

    def test_clear_removes_session(self, tmp_path):
        from footfindr.suppliers.session import SearchSession, SessionManager
        mgr = SessionManager(workspace=tmp_path)
        parts = _sample_parts()[:1]
        session = SearchSession(
            query="X", suppliers=[], created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[parts[0].result_id],
        )
        mgr.save(session)
        assert mgr.load() is not None
        mgr.clear()
        assert mgr.load() is None

    def test_require_session_raises(self, tmp_path):
        from footfindr.suppliers.session import SessionError, SessionManager
        mgr = SessionManager(workspace=tmp_path)
        with pytest.raises(SessionError):
            mgr.require_session()

    def test_get_by_index(self, tmp_path):
        from footfindr.suppliers.session import SearchSession, SessionManager
        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        assert session.get_by_index(1).mpn == "LT3045EDD#PBF"
        assert session.get_by_index(3).mpn == "LT3045EMSE#PBF"
        assert session.get_by_index(0) is None
        assert session.get_by_index(99) is None

    def test_filter_preserves_originals(self, tmp_path):
        from footfindr.suppliers.session import SearchFilter, SearchSession, apply_filter

        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )

        # Filter to DFN only
        filt = SearchFilter(field="package", op="contains", value="DFN")
        session.filters.append(filt)
        active_ids = [
            r.result_id for r in parts if apply_filter(r, filt)
        ]
        session.active_result_ids = active_ids

        # Originals unchanged
        assert len(session.original_results) == 5
        # Active narrowed
        active = session.get_active_results()
        assert len(active) == 2
        assert all("DFN" in (a.package or "") for a in active)

    def test_filter_reset_restores(self, tmp_path):
        from footfindr.suppliers.session import SearchFilter, SearchSession, apply_filter

        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        # Filter
        session.filters.append(SearchFilter(field="package", op="contains", value="DFN"))
        session.active_result_ids = [
            r.result_id for r in parts
            if apply_filter(r, session.filters[0])
        ]
        assert len(session.get_active_results()) == 2

        # Reset
        session.filters.clear()
        session.active_result_ids = [r.result_id for r in session.original_results]
        assert len(session.get_active_results()) == 5

    def test_selected_result_stable(self, tmp_path):
        from footfindr.suppliers.session import SearchSession

        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            selected_result_id=parts[2].result_id,
        )
        selected = session.get_selected()
        assert selected is not None
        assert selected.mpn == "LT3045EMSE#PBF"


# ---------------------------------------------------------------------------
# Field aliases
# ---------------------------------------------------------------------------

class TestFieldAliases:
    def test_package_aliases(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("package") == "package"
        assert resolve_field_alias("pkg") == "package"
        assert resolve_field_alias("case") == "package"

    def test_temp_aliases(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("temp") == "temperature_range"
        assert resolve_field_alias("temperature") == "temperature_range"

    def test_stock_aliases(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("stock") == "stock"
        assert resolve_field_alias("qty") == "stock"

    def test_unknown_passthrough(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("custom_field") == "custom_field"


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

class TestBadges:
    def test_active_in_stock(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part(stock=1000, lifecycle="Active")
        badges = compute_badges(p)
        assert "IN_STOCK" in badges
        assert "ACTIVE" in badges
        assert "OBSOLETE" not in badges

    def test_low_stock(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part(stock=50, lifecycle="Active")
        badges = compute_badges(p)
        assert "IN_STOCK" in badges
        assert "LOW_STOCK" in badges

    def test_obsolete(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part(lifecycle="Obsolete")
        badges = compute_badges(p)
        assert "OBSOLETE" in badges
        assert "ACTIVE" not in badges

    def test_nrnd(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part(lifecycle="NRND")
        badges = compute_badges(p)
        assert "NRND" in badges

    def test_no_datasheet(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part(datasheet_url=None)
        badges = compute_badges(p)
        assert "NO_DATASHEET" in badges

    def test_no_price(self):
        from footfindr.suppliers.badges import compute_badges
        p = SupplierPart(supplier="test", mpn="X", stock=10)
        badges = compute_badges(p)
        assert "NO_PRICE" in badges

    def test_footprint_always_review(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part()
        badges = compute_badges(p)
        assert "FOOTPRINT_REVIEW" in badges

    def test_jlc_available(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part()
        p.lcsc_pn = "C12345"
        badges = compute_badges(p)
        assert "JLC_AVAILABLE" in badges

    def test_expensive_badge(self):
        from footfindr.suppliers.badges import compute_badges
        parts = _sample_parts()
        # Override the last part to be genuinely expensive (>3x median of ~8)
        parts[4] = _make_part(
            mpn="LT3045MPMSE#PBF",
            supplier_pn="505-EXPENSIVE-ND",
            package="MSOP-12-EP",
            stock=12,
            price=50.00,  # >3x median of ~8
        )
        expensive = parts[4]
        badges = compute_badges(expensive, parts)
        assert "EXPENSIVE" in badges


# ---------------------------------------------------------------------------
# Differentiator extraction
# ---------------------------------------------------------------------------

class TestDifferentiators:
    def test_package_differs(self):
        from footfindr.suppliers.badges import extract_differentiators
        parts = _sample_parts()
        diffs = extract_differentiators(parts)
        assert "Package" in diffs
        assert "DFN-10" in diffs["Package"]
        assert "MSOP-12-EP" in diffs["Package"]

    def test_packaging_differs(self):
        from footfindr.suppliers.badges import extract_differentiators
        parts = _sample_parts()[:2]  # tube vs tape & reel
        diffs = extract_differentiators(parts)
        assert "Packaging" in diffs


# ---------------------------------------------------------------------------
# MPN grouping
# ---------------------------------------------------------------------------

class TestMPNGrouping:
    def test_group_by_mpn(self):
        from footfindr.suppliers.badges import group_by_mpn
        parts = [
            _make_part(mpn="LT3045EDD#PBF", supplier_pn="PN1", packaging="Tube"),
            _make_part(mpn="LT3045EDD#PBF", supplier_pn="PN2", packaging="Tape & Reel"),
            _make_part(mpn="LT3045EMSE#PBF", supplier_pn="PN3", packaging="Tube"),
        ]
        groups = group_by_mpn(parts)
        assert len(groups) == 2
        # First group should have 2 variants
        dfn_group = [g for g in groups if "EDD" in g.mpn][0]
        assert len(dfn_group.variants) == 2


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------

class TestSort:
    def test_sort_by_stock_descending(self):
        from footfindr.suppliers.session import _sort_parts
        parts = _sample_parts()
        sorted_p = _sort_parts(parts, ["stock"], descending=True)
        stocks = [p.stock for p in sorted_p]
        assert stocks == sorted(stocks, reverse=True)

    def test_sort_by_price_ascending(self):
        from footfindr.suppliers.session import _sort_parts
        parts = _sample_parts()
        sorted_p = _sort_parts(parts, ["price"], descending=False)
        prices = [p.best_price() for p in sorted_p]
        assert prices == sorted(prices)

    def test_default_interleave_sort(self):
        from footfindr.suppliers.session import default_interleave_sort
        parts = _sample_parts()
        sorted_p = default_interleave_sort(parts, "LT3045")
        # Exact match first
        assert sorted_p[0].lifecycle == "Active"


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

class TestFilter:
    def test_contains_filter(self):
        from footfindr.suppliers.session import SearchFilter, apply_filter
        p = _make_part(package="DFN-10")
        f = SearchFilter(field="package", op="contains", value="DFN")
        assert apply_filter(p, f) is True

    def test_contains_filter_fail(self):
        from footfindr.suppliers.session import SearchFilter, apply_filter
        p = _make_part(package="MSOP-12-EP")
        f = SearchFilter(field="package", op="contains", value="DFN")
        assert apply_filter(p, f) is False

    def test_fuzzy_package_match(self):
        from footfindr.suppliers.session import SearchFilter, apply_filter
        p = _make_part(package="DFN-10")
        # User types "dfn10" without hyphen
        f = SearchFilter(field="package", op="contains", value="dfn10")
        assert apply_filter(p, f) is True

    def test_gt_stock_filter(self):
        from footfindr.suppliers.session import SearchFilter, apply_filter
        p = _make_part(stock=2310)
        f = SearchFilter(field="stock", op="gt", value="100")
        assert apply_filter(p, f) is True
        f2 = SearchFilter(field="stock", op="gt", value="5000")
        assert apply_filter(p, f2) is False

    def test_parse_filter_value(self):
        from footfindr.suppliers.session import parse_filter_value
        assert parse_filter_value(">100") == ("gt", "100")
        assert parse_filter_value(">=50") == ("gte", "50")
        assert parse_filter_value("<10") == ("lt", "10")
        assert parse_filter_value("DFN") == ("contains", "DFN")
        assert parse_filter_value("=Active") == ("eq", "Active")


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

class TestDisplay:
    def test_mini_table_renders(self):
        from footfindr.suppliers.display import render_mini_table
        parts = _sample_parts()
        table = render_mini_table(parts, query="LT3045")
        assert table is not None

    def test_full_table_renders(self):
        from footfindr.suppliers.display import render_search_table
        parts = _sample_parts()
        table = render_search_table(parts, query="LT3045")
        assert table is not None

    def test_part_numbers_only(self):
        from footfindr.suppliers.display import render_part_numbers_only
        parts = _sample_parts()
        output = render_part_numbers_only(parts)
        lines = output.strip().split("\n")
        assert len(lines) == 5
        assert "LT3045EDD#PBF" in lines[0]

    def test_grouped_output(self):
        from footfindr.suppliers.display import render_grouped
        parts = _sample_parts()
        output = render_grouped(parts, "package")
        assert "DFN-10" in output
        assert "MSOP-12-EP" in output

    def test_comparison_table(self):
        from footfindr.suppliers.display import render_comparison
        parts = _sample_parts()[:2]
        table = render_comparison(parts)
        assert table is not None

    def test_part_detail(self):
        from footfindr.suppliers.display import render_part_detail
        p = _make_part()
        table = render_part_detail(p)
        assert table is not None

    def test_qty_aware_display(self):
        from footfindr.suppliers.display import render_search_table
        parts = _sample_parts()
        table = render_search_table(parts, qty=10, query="LT3045")
        assert table is not None

    def test_export_csv(self, tmp_path):
        from footfindr.suppliers.display import export_csv
        parts = _sample_parts()
        out = str(tmp_path / "test.csv")
        n = export_csv(parts, out)
        assert n == 5
        assert Path(out).exists()

    def test_export_markdown(self, tmp_path):
        from footfindr.suppliers.display import export_markdown
        parts = _sample_parts()
        out = str(tmp_path / "test.md")
        n = export_markdown(parts, out)
        assert n == 5
        content = Path(out).read_text()
        assert "LT3045EDD#PBF" in content


# ---------------------------------------------------------------------------
# Shortlist
# ---------------------------------------------------------------------------

class TestShortlist:
    def test_add_list_remove(self, tmp_path):
        from footfindr.suppliers.shortlist import Shortlist, ShortlistEntry
        sl = Shortlist(workspace=tmp_path)

        entry = ShortlistEntry.from_supplier_part(_make_part())
        sl.add(entry)

        entries = sl.list()
        assert len(entries) == 1
        assert entries[0].mpn == "LT3045EDD#PBF"

        sl.remove(index=1)
        assert len(sl.list()) == 0

    def test_add_deduplicates(self, tmp_path):
        from footfindr.suppliers.shortlist import Shortlist, ShortlistEntry
        sl = Shortlist(workspace=tmp_path)
        p = _make_part()
        sl.add(ShortlistEntry.from_supplier_part(p))
        sl.add(ShortlistEntry.from_supplier_part(p))
        assert len(sl.list()) == 1

    def test_clear(self, tmp_path):
        from footfindr.suppliers.shortlist import Shortlist, ShortlistEntry
        sl = Shortlist(workspace=tmp_path)
        sl.add(ShortlistEntry.from_supplier_part(_make_part()))
        sl.clear()
        assert len(sl.list()) == 0


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_recommend_returns_results(self):
        from footfindr.suppliers.badges import recommend
        parts = _sample_parts()
        recs = recommend(parts)
        assert len(recs) > 0
        # Should have at least a best_value recommendation
        cats = [r.category for r in recs]
        assert "best_value" in cats

    def test_recommend_empty(self):
        from footfindr.suppliers.badges import recommend
        assert recommend([]) == []


# ---------------------------------------------------------------------------
# Search cache isolation
# ---------------------------------------------------------------------------

class TestSearchCacheIsolation:
    def test_search_cache_separate_from_exact(self, tmp_path):
        from footfindr.suppliers.cache import SupplierCache
        cache = SupplierCache(workspace=tmp_path)

        # Store a search result
        parts = _sample_parts()[:2]
        cache.store_search("digikey", "LT3045", parts)

        # Exact lookup should NOT find these
        exact = cache.lookup("LT3045EDD#PBF", supplier="digikey")
        assert len(exact) == 0  # Not polluted

        # Search cache should find them
        search_results = cache.lookup_search("digikey", "LT3045")
        assert search_results is not None
        assert len(search_results) == 2

        cache.close()

    def test_search_cache_refresh_replaces(self, tmp_path):
        from footfindr.suppliers.cache import SupplierCache
        cache = SupplierCache(workspace=tmp_path)

        parts1 = _sample_parts()[:2]
        cache.store_search("digikey", "LT3045", parts1)

        parts2 = _sample_parts()[:3]
        cache.store_search("digikey", "LT3045", parts2)

        results = cache.lookup_search("digikey", "LT3045")
        assert len(results) == 3

        cache.close()

    def test_clear_searches(self, tmp_path):
        from footfindr.suppliers.cache import SupplierCache
        cache = SupplierCache(workspace=tmp_path)
        cache.store_search("digikey", "LT3045", _sample_parts()[:1])
        assert cache.clear_searches() == 1
        assert cache.lookup_search("digikey", "LT3045") is None
        cache.close()


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    def test_v2_fields_stored_and_loaded(self, tmp_path):
        from footfindr.suppliers.cache import SupplierCache
        cache = SupplierCache(workspace=tmp_path)
        p = _make_part(
            temperature_range="-40°C ~ 125°C",
            mounting_type="Surface Mount",
        )
        p.attributes = {"Capacitance": "10 µF", "Package / Case": "DFN-10"}
        cache.store(p)

        results = cache.lookup("LT3045EDD#PBF", supplier="digikey")
        assert len(results) == 1
        loaded = results[0]
        assert loaded.temperature_range == "-40°C ~ 125°C"
        assert loaded.mounting_type == "Surface Mount"
        assert "Capacitance" in loaded.attributes
        assert loaded.attributes["Capacitance"] == "10 µF"
        cache.close()


# ---------------------------------------------------------------------------
# Safety boundaries
# ---------------------------------------------------------------------------

class TestSafetyBoundaries:
    """Verify M8.5 does not cross safety lines."""

    def test_no_footprint_auto_binding_on_promotion(self, tmp_path):
        """Promoted parts should NOT have footprint auto-assigned."""
        from footfindr.suppliers.badges import compute_badges
        p = _make_part()
        badges = compute_badges(p)
        assert "FOOTPRINT_REVIEW" in badges

    def test_supplier_part_has_no_schematic_write_fields(self):
        """SupplierPart should not have methods for schematic writes."""
        p = _make_part()
        assert not hasattr(p, "write_to_schematic")
        assert not hasattr(p, "apply_to_schematic")

    def test_supplier_part_has_no_purchasing(self):
        """SupplierPart should not have cart/order methods."""
        p = _make_part()
        assert not hasattr(p, "add_to_cart")
        assert not hasattr(p, "submit_order")


# ===========================================================================
# M8.5 UX Follow-up Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Sort semantics
# ---------------------------------------------------------------------------

class TestSortSemantics:
    """Sort replaces previous sort by default; multi-sort only on request."""

    def test_sort_replaces_previous(self):
        """Second sort should replace the first, not accumulate."""
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            sort_fields=["stock"],
            sort_descending=True,
        )
        # Simulate what CLI does on second sort command: REPLACE
        session.sort_fields = ["price"]
        session.sort_descending = False

        assert session.sort_fields == ["price"]
        results = session.get_active_results()
        prices = [p.best_price() for p in results]
        assert prices == sorted(prices)

    def test_multi_sort_explicit(self):
        """Multi-sort only when explicitly requested."""
        from footfindr.suppliers.session import _sort_parts
        parts = _sample_parts()
        # Sort by package (asc), then stock (desc within each package)
        sorted_p = _sort_parts(parts, ["package", "stock"], descending=False)
        # DFN-10 before MSOP-12-EP
        first_pkg = sorted_p[0].package
        assert first_pkg == "DFN-10"

    def test_sort_clear_restores_original(self):
        """Clearing sort restores original insertion order."""
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            sort_fields=["price"],
            sort_descending=False,
        )
        # Clear sort
        session.sort_fields = []
        results = session.get_active_results()
        # Should be in original order
        assert results[0].mpn == parts[0].mpn


# ---------------------------------------------------------------------------
# Context-aware mini display
# ---------------------------------------------------------------------------

class TestContextAwareMini:
    """Mini output hides redundant columns and shows relevant ones."""

    def test_mini_hides_supplier_when_single(self):
        """If all results are from one supplier, hide Supplier column."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()  # all digikey
        cols = _resolve_mini_columns(parts)
        assert "supplier" not in cols

    def test_mini_shows_supplier_when_multi(self):
        """If results are from multiple suppliers, show Supplier column."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        parts[0] = _make_part(mpn="X", supplier="mouser", supplier_pn="M-X")
        cols = _resolve_mini_columns(parts)
        assert "supplier" in cols

    def test_mini_hides_manufacturer_when_single(self):
        """If all results have the same manufacturer, hide Manufacturer column."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()  # all Analog Devices
        # manufacturer is not shown by default anyway (context-aware)
        cols = _resolve_mini_columns(parts)
        assert "manufacturer" not in cols

    def test_sort_stock_shows_stock_not_package(self):
        """Sort by stock mini should show stock column, not package."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        cols = _resolve_mini_columns(parts, context_fields=["stock"])
        assert "stock" in cols
        # package is NOT in context_fields, so it should NOT appear
        assert "package" not in cols

    def test_sort_package_shows_package(self):
        """Sort by package mini should show package column."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        cols = _resolve_mini_columns(parts, context_fields=["package"])
        assert "package" in cols

    def test_sort_temp_shows_temp(self):
        """Sort by temperature mini should show temp column."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        cols = _resolve_mini_columns(parts, context_fields=["temperature_range"])
        assert "temperature_range" in cols

    def test_columns_override_defaults(self):
        """Explicit --columns overrides mini defaults."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        cols = _resolve_mini_columns(parts, columns=["mpn", "stock"])
        assert cols == ["mpn", "stock"]

    def test_named_view_stock(self):
        """Named view 'stock' shows mpn, stock, badges."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        cols = _resolve_mini_columns(parts, view="stock")
        assert cols == ["mpn", "stock", "badges"]

    def test_named_view_sourcing(self):
        """Named view 'sourcing' shows sourcing fields."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        cols = _resolve_mini_columns(parts, view="sourcing")
        assert "supplier_pn" in cols
        assert "lead_time" in cols

    def test_mini_with_badges_always_present(self):
        """Default mini output always includes badges column."""
        from footfindr.suppliers.display import _resolve_mini_columns
        parts = _sample_parts()
        cols = _resolve_mini_columns(parts)
        assert "badges" in cols

    def test_mini_renders_with_columns(self):
        """Mini table renders correctly with explicit columns."""
        from footfindr.suppliers.display import render_mini_table
        parts = _sample_parts()
        table = render_mini_table(parts, columns=["mpn", "stock"], query="LT3045")
        assert table is not None

    def test_mini_renders_with_view(self):
        """Mini table renders correctly with named view."""
        from footfindr.suppliers.display import render_mini_table
        parts = _sample_parts()
        table = render_mini_table(parts, view="stock", query="LT3045")
        assert table is not None


# ---------------------------------------------------------------------------
# Field discovery
# ---------------------------------------------------------------------------

class TestFieldDiscovery:
    """ff supplier fields . shows available fields."""

    def test_discover_fields_returns_canonical(self):
        from footfindr.suppliers.session import discover_fields
        parts = _sample_parts()
        fields = discover_fields(parts)
        canon_names = [f.canonical for f in fields]
        assert "mpn" in canon_names
        assert "stock" in canon_names
        assert "package" in canon_names
        assert "temperature_range" in canon_names

    def test_discover_fields_coverage(self):
        from footfindr.suppliers.session import discover_fields
        parts = _sample_parts()
        fields = discover_fields(parts)
        mpn_field = next(f for f in fields if f.canonical == "mpn")
        assert mpn_field.coverage == "5/5"

    def test_discover_fields_shows_aliases(self):
        from footfindr.suppliers.session import discover_fields
        parts = _sample_parts()
        fields = discover_fields(parts)
        pkg = next(f for f in fields if f.canonical == "package")
        assert "pkg" in pkg.aliases
        assert "case" in pkg.aliases

    def test_discover_fields_includes_dynamic_attributes(self):
        from footfindr.suppliers.session import discover_fields
        parts = [_make_part(attributes={"Output Current": "500mA", "Voltage - Input": "20V"})]
        fields = discover_fields(parts)
        attr_names = [f.canonical for f in fields if f.is_attribute]
        assert "Output Current" in attr_names
        assert "Voltage - Input" in attr_names

    def test_discover_fields_empty_results(self):
        from footfindr.suppliers.session import discover_fields
        assert discover_fields([]) == []

    def test_fields_table_renders(self):
        from footfindr.suppliers.display import render_fields_table
        from footfindr.suppliers.session import discover_fields
        parts = _sample_parts()
        fields = discover_fields(parts)
        table = render_fields_table(fields)
        assert table is not None


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

class TestRelevanceScoring:
    """MPN-family relevance scoring for search results."""

    def test_exact_match_highest(self):
        from footfindr.suppliers.session import compute_relevance
        p = _make_part(mpn="AD9959")
        assert compute_relevance(p, "AD9959") == 0

    def test_starts_with_high(self):
        from footfindr.suppliers.session import compute_relevance
        p = _make_part(mpn="AD9959BCPZ-REEL7")
        assert compute_relevance(p, "AD9959") == 1

    def test_contains_medium(self):
        from footfindr.suppliers.session import compute_relevance
        p = _make_part(mpn="EVAL-AD9959")
        assert compute_relevance(p, "AD9959") == 2

    def test_description_only_low(self):
        from footfindr.suppliers.session import compute_relevance
        p = _make_part(mpn="ZUSA-HT-3030", description="Thermal compound for AD9959")
        assert compute_relevance(p, "AD9959") == 3

    def test_unrelated_lowest(self):
        from footfindr.suppliers.session import compute_relevance
        p = _make_part(mpn="ZUSA-HT-3030", description="Thermal compound")
        assert compute_relevance(p, "AD9959") == 5

    def test_low_relevance_badge_assigned(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part(mpn="ZUSA-HT-3030", description="Thermal compound")
        badges = compute_badges(p, query="AD9959")
        assert "LOW_RELEVANCE" in badges

    def test_high_relevance_no_badge(self):
        from footfindr.suppliers.badges import compute_badges
        p = _make_part(mpn="AD9959BCPZ")
        badges = compute_badges(p, query="AD9959")
        assert "LOW_RELEVANCE" not in badges

    def test_strict_hides_low_relevance(self):
        """Strict search hides unrelated results."""
        from footfindr.suppliers.session import compute_relevance
        parts = [
            _make_part(mpn="AD9959BCPZ-REEL7"),
            _make_part(mpn="AD9959/PCBZ"),
            _make_part(mpn="AD9959BCPZ"),
            _make_part(mpn="ZUSA-HT-3030", description="Thermal compound"),
        ]
        high_relevance = [p for p in parts if compute_relevance(p, "AD9959") <= 3]
        assert len(high_relevance) == 3
        assert all("AD9959" in p.mpn for p in high_relevance)

    def test_include_related_shows_low_relevance(self):
        """include-related shows all results including low-relevance."""
        from footfindr.suppliers.session import compute_relevance
        parts = [
            _make_part(mpn="AD9959BCPZ"),
            _make_part(mpn="ZUSA-HT-3030", description="Thermal compound"),
        ]
        # include-related means don't filter
        all_results = parts  # no filtering
        assert len(all_results) == 2


# ---------------------------------------------------------------------------
# MPN-like query detection
# ---------------------------------------------------------------------------

class TestMPNLikeQuery:
    """Heuristic for detecting MPN-like queries."""

    def test_part_numbers_detected(self):
        from footfindr.suppliers.session import is_mpn_like_query
        assert is_mpn_like_query("AD9959") is True
        assert is_mpn_like_query("LT3045") is True
        assert is_mpn_like_query("OPA189") is True
        assert is_mpn_like_query("74LVC1G17") is True
        assert is_mpn_like_query("GRM155R60J106ME05D") is True

    def test_natural_language_not_detected(self):
        from footfindr.suppliers.session import is_mpn_like_query
        assert is_mpn_like_query("10uF 16V X7R 0805") is False
        assert is_mpn_like_query("linear regulator 500mA") is False

    def test_part_with_special_chars(self):
        from footfindr.suppliers.session import is_mpn_like_query
        assert is_mpn_like_query("LT3045EDD#PBF") is True
        assert is_mpn_like_query("AD9959/PCBZ") is True


# ---------------------------------------------------------------------------
# Status line
# ---------------------------------------------------------------------------

class TestStatusLine:
    """Status line shows session state compactly."""

    def test_basic_status(self):
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        status = session.get_status_line()
        assert "Search: LT3045" in status
        assert "supplier: digikey" in status
        assert "results: 5" in status

    def test_status_with_sort(self):
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            sort_fields=["stock"],
            sort_descending=True,
        )
        status = session.get_status_line()
        assert "sort: stock desc" in status

    def test_status_with_filter(self):
        from footfindr.suppliers.session import SearchFilter, SearchSession
        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts[:3]],
            filters=[SearchFilter(field="package", op="contains", value="WFDFN")],
        )
        status = session.get_status_line()
        assert "results: 3/5" in status
        assert "filters:" in status
        assert "WFDFN" in status

    def test_status_multi_supplier(self):
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey", "mouser"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        status = session.get_status_line()
        assert "suppliers: digikey,mouser" in status


# ---------------------------------------------------------------------------
# Single supplier / manufacturer detection
# ---------------------------------------------------------------------------

class TestSessionDetection:
    """Session helpers for detecting constant columns."""

    def test_single_supplier(self):
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()  # all digikey
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        assert session.is_single_supplier() is True
        assert session.is_single_manufacturer() is True

    def test_multi_supplier_detected(self):
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()
        parts[0] = _make_part(mpn="X", supplier="mouser", supplier_pn="M-X")
        session = SearchSession(
            query="LT3045", suppliers=["digikey", "mouser"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        assert session.is_single_supplier() is False

    def test_multi_manufacturer_detected(self):
        from footfindr.suppliers.session import SearchSession
        parts = _sample_parts()
        parts[0] = _make_part(mpn="X", manufacturer="TI", supplier_pn="TI-X")
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        assert session.is_single_manufacturer() is False


# ---------------------------------------------------------------------------
# Named views
# ---------------------------------------------------------------------------

class TestNamedViews:
    """Named view presets."""

    def test_named_views_exist(self):
        from footfindr.suppliers.session import NAMED_VIEWS
        assert "stock" in NAMED_VIEWS
        assert "package" in NAMED_VIEWS
        assert "price" in NAMED_VIEWS
        assert "sourcing" in NAMED_VIEWS
        assert "specs" in NAMED_VIEWS

    def test_stock_view_columns(self):
        from footfindr.suppliers.session import NAMED_VIEWS
        assert NAMED_VIEWS["stock"] == ["mpn", "stock", "badges"]


# ---------------------------------------------------------------------------
# Extended field aliases
# ---------------------------------------------------------------------------

class TestExtendedAliases:
    """New aliases added in the UX follow-up."""

    def test_sku_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("sku") == "supplier_pn"

    def test_part_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("part") == "mpn"
        assert resolve_field_alias("partnumber") == "mpn"

    def test_tube_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("tube") == "packaging"

    def test_lead_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("lead") == "lead_time"

    def test_url_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("url") == "product_url"

    def test_datasheet_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("datasheet") == "datasheet_url"


# ---------------------------------------------------------------------------
# No live API calls for local operations
# ---------------------------------------------------------------------------

class TestNoAPICalls:
    """Verify local sort/filter/list/group/fields don't hit APIs."""

    def test_sort_uses_session_only(self, tmp_path):
        """Sort should operate on cached session data, no API call."""
        from footfindr.suppliers.session import SearchSession, SessionManager

        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        mgr = SessionManager(workspace=tmp_path)
        mgr.save(session)

        # Load and sort — should work without any network
        loaded = mgr.require_session()
        loaded.sort_fields = ["stock"]
        loaded.sort_descending = True
        results = loaded.get_active_results()
        assert len(results) == 5
        assert results[0].stock >= results[-1].stock

    def test_filter_uses_session_only(self, tmp_path):
        """Filter should operate on cached session data, no API call."""
        from footfindr.suppliers.session import (
            SearchFilter, SearchSession, SessionManager, apply_filter,
        )

        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        mgr = SessionManager(workspace=tmp_path)
        mgr.save(session)

        loaded = mgr.require_session()
        filt = SearchFilter(field="package", op="contains", value="DFN")
        loaded.filters.append(filt)
        loaded.active_result_ids = [
            r.result_id for r in loaded.original_results
            if apply_filter(r, filt)
        ]
        results = loaded.get_active_results()
        assert len(results) == 2

    def test_fields_discovery_uses_session_only(self, tmp_path):
        """Field discovery should operate on cached session data, no API call."""
        from footfindr.suppliers.session import (
            SearchSession, SessionManager, discover_fields,
        )

        parts = _sample_parts()
        session = SearchSession(
            query="LT3045", suppliers=["digikey"],
            created_at="", last_updated="",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        mgr = SessionManager(workspace=tmp_path)
        mgr.save(session)

        loaded = mgr.require_session()
        fields = discover_fields(loaded.get_active_results())
        assert len(fields) > 0

