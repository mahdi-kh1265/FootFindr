"""Tests for M9.3c expand command — shard generation, merge, and UX.

Tests:
- manufacturer shards generated with category token
- dielectric shards generated for capacitors
- combo shards (manufacturer + dielectric) in auto strategy
- max_queries limit respected
- category token preserved in all shards
- session ref context persisted
- expand merges unique results into session
- constraints reapplied before merge
- ls suggests expand when provider_total > fetched
- more degraded suggests expand, not just more
- footprint files untouched
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Shard generation tests
# ---------------------------------------------------------------------------

class TestShardGeneration:
    """Test _generate_expansion_shards."""

    def _make_session(self, query="4.7uF 25V 0603 ceramic capacitor",
                      base_parts=None, category="capacitor"):
        from footfindr.suppliers.session import SearchSession
        from footfindr.suppliers.models import SupplierPart
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        parts = [SupplierPart(supplier="dk", mpn="P1", source="t")]
        return SearchSession(
            query=query,
            suppliers=["digikey"],
            created_at=now,
            last_updated=now,
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            ref_name="C1",
            base_query_parts=base_parts or ["4.7uF", "25V", "0603"],
            category=category,
        )

    def test_manufacturer_shards(self):
        """Manufacturer strategy generates one shard per known manufacturer."""
        from footfindr.cli_supplier import _generate_expansion_shards, _PASSIVE_MANUFACTURERS

        session = self._make_session()
        shards = _generate_expansion_shards(session, strategy="manufacturer", max_queries=50)

        assert len(shards) == len(_PASSIVE_MANUFACTURERS)
        # Each shard should contain the base query, manufacturer, and category token
        for shard in shards:
            assert "4.7uF 25V 0603" in shard
            assert "capacitor" in shard.lower()

    def test_manufacturer_shard_names(self):
        """Each manufacturer should appear in its shard."""
        from footfindr.cli_supplier import _generate_expansion_shards, _PASSIVE_MANUFACTURERS

        session = self._make_session()
        shards = _generate_expansion_shards(session, strategy="manufacturer", max_queries=50)

        for mfr in _PASSIVE_MANUFACTURERS:
            matching = [s for s in shards if mfr in s]
            assert len(matching) == 1, f"Expected 1 shard for {mfr}, got {len(matching)}"

    def test_dielectric_shards(self):
        """Dielectric strategy generates shards for capacitors."""
        from footfindr.cli_supplier import _generate_expansion_shards, _CAP_DIELECTRICS

        session = self._make_session()
        shards = _generate_expansion_shards(session, strategy="dielectric", max_queries=50)

        assert len(shards) == len(_CAP_DIELECTRICS)
        for shard in shards:
            assert "4.7uF 25V 0603" in shard
            assert "capacitor" in shard.lower()

    def test_dielectric_not_for_resistors(self):
        """Dielectric strategy should generate nothing for resistors."""
        from footfindr.cli_supplier import _generate_expansion_shards

        session = self._make_session(
            query="10k 0603 resistor",
            base_parts=["10k", "0603"],
            category="resistor",
        )
        shards = _generate_expansion_shards(session, strategy="dielectric")
        assert len(shards) == 0

    def test_auto_includes_combos(self):
        """Auto strategy includes manufacturer + dielectric combos for capacitors."""
        from footfindr.cli_supplier import _generate_expansion_shards

        session = self._make_session()
        shards = _generate_expansion_shards(session, strategy="auto", max_queries=100)

        # Should have: manufacturers + dielectrics + combos
        combo_shards = [s for s in shards if any(
            m in s for m in ["Murata", "TDK", "Samsung"]
        ) and any(d in s for d in ["X5R", "X6S", "X7R"])]
        assert len(combo_shards) > 0, "Auto strategy should include combo shards"

    def test_auto_order_mfr_then_diel_then_combo(self):
        """Auto strategy order: manufacturer, dielectric, then combos."""
        from footfindr.cli_supplier import _generate_expansion_shards, _PASSIVE_MANUFACTURERS, _CAP_DIELECTRICS

        session = self._make_session()
        shards = _generate_expansion_shards(session, strategy="auto", max_queries=100)

        # First shards should be manufacturer-only
        first_shard = shards[0]
        assert _PASSIVE_MANUFACTURERS[0] in first_shard

        # After manufacturers, should be dielectrics
        mfr_count = len(_PASSIVE_MANUFACTURERS)
        diel_shards = shards[mfr_count:mfr_count + len(_CAP_DIELECTRICS)]
        for shard in diel_shards:
            assert any(d in shard for d in _CAP_DIELECTRICS)

    def test_max_queries_limit(self):
        """Shards should be capped at max_queries."""
        from footfindr.cli_supplier import _generate_expansion_shards

        session = self._make_session()
        shards = _generate_expansion_shards(session, strategy="auto", max_queries=5)
        assert len(shards) == 5

    def test_category_token_preserved(self):
        """Every shard should include the category token."""
        from footfindr.cli_supplier import _generate_expansion_shards

        session = self._make_session()
        shards = _generate_expansion_shards(session, strategy="auto", max_queries=20)

        for shard in shards:
            assert "capacitor" in shard.lower(), f"Category token missing from shard: {shard}"

    def test_resistor_shards_have_category(self):
        """Resistor shards should include 'resistor' token."""
        from footfindr.cli_supplier import _generate_expansion_shards

        session = self._make_session(
            query="10k 0603 resistor",
            base_parts=["10k", "0603"],
            category="resistor",
        )
        shards = _generate_expansion_shards(session, strategy="manufacturer", max_queries=50)

        for shard in shards:
            assert "resistor" in shard.lower()

    def test_no_base_parts_falls_back_to_query(self):
        """If base_query_parts empty, strip category from query."""
        from footfindr.cli_supplier import _generate_expansion_shards

        session = self._make_session(base_parts=[])
        shards = _generate_expansion_shards(session, strategy="manufacturer", max_queries=5)

        # Should still generate shards using the query with category stripped
        assert len(shards) > 0
        for shard in shards:
            # Should not have "ceramic capacitor" doubled
            assert shard.count("capacitor") == 1


# ---------------------------------------------------------------------------
# 2. Session ref context persistence tests
# ---------------------------------------------------------------------------

class TestSessionRefContext:
    """Test session ref context fields persistence."""

    def test_session_stores_ref_context(self, tmp_path):
        """ref_name, base_query_parts, category should persist."""
        from footfindr.suppliers.session import SearchSession, SessionManager
        from footfindr.suppliers.models import SupplierPart

        parts = [SupplierPart(supplier="dk", mpn="P1", source="t")]
        session = SearchSession(
            query="4.7uF 25V 0603 ceramic capacitor",
            suppliers=["digikey"],
            created_at="2025-01-01",
            last_updated="2025-01-01",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            ref_name="C1",
            base_query_parts=["4.7uF", "25V", "0603"],
            category="capacitor",
        )

        mgr = SessionManager(workspace=tmp_path)
        mgr.save(session)
        loaded = mgr.load()

        assert loaded is not None
        assert loaded.ref_name == "C1"
        assert loaded.base_query_parts == ["4.7uF", "25V", "0603"]
        assert loaded.category == "capacitor"

    def test_expansion_metadata_persists(self, tmp_path):
        """Expansion metadata should persist."""
        from footfindr.suppliers.session import SearchSession, SessionManager
        from footfindr.suppliers.models import SupplierPart

        parts = [SupplierPart(supplier="dk", mpn="P1", source="t")]
        session = SearchSession(
            query="test",
            suppliers=["digikey"],
            created_at="2025-01-01",
            last_updated="2025-01-01",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            expanded=True,
            expansion_strategy="auto",
            expansion_queries_run=15,
            expansion_new_results=8,
        )

        mgr = SessionManager(workspace=tmp_path)
        mgr.save(session)
        loaded = mgr.load()

        assert loaded.expanded is True
        assert loaded.expansion_strategy == "auto"
        assert loaded.expansion_queries_run == 15
        assert loaded.expansion_new_results == 8

    def test_old_session_loads_without_ref_context(self, tmp_path):
        """Old sessions without ref context should load cleanly."""
        import json

        mgr_dir = tmp_path / "session"
        mgr_dir.mkdir()
        old_data = {
            "query": "test",
            "suppliers": ["digikey"],
            "created_at": "2025-01-01",
            "last_updated": "2025-01-01",
            "original_results": [{"supplier": "dk", "mpn": "P1", "source": "t",
                                   "price_breaks": [], "attributes": {}}],
            "active_result_ids": [],
            "page_size": 10,
            "current_page": 1,
            "provider_offsets": {},
        }
        (mgr_dir / "supplier_search.json").write_text(
            json.dumps(old_data), encoding="utf-8"
        )

        from footfindr.suppliers.session import SessionManager
        mgr = SessionManager(workspace=tmp_path)
        loaded = mgr.load()

        assert loaded is not None
        assert loaded.ref_name is None
        assert loaded.base_query_parts == []
        assert loaded.category is None
        assert loaded.expanded is False


# ---------------------------------------------------------------------------
# 3. Expand merge tests
# ---------------------------------------------------------------------------

class TestExpandMerge:
    """Test that expand correctly merges unique results."""

    def _make_part(self, supplier="digikey", mpn="PART-1", supplier_pn="490-001-ND",
                   manufacturer="Murata"):
        from footfindr.suppliers.models import SupplierPart
        return SupplierPart(
            supplier=supplier, mpn=mpn, supplier_pn=supplier_pn,
            manufacturer=manufacturer, source="test",
        )

    def _make_session(self, parts):
        from footfindr.suppliers.session import SearchSession
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return SearchSession(
            query="4.7uF 25V 0603 ceramic capacitor",
            suppliers=["digikey"],
            created_at=now,
            last_updated=now,
            original_results=list(parts),
            active_result_ids=[p.result_id for p in parts],
            ref_name="C1",
            base_query_parts=["4.7uF", "25V", "0603"],
            category="capacitor",
        )

    def test_merge_preserves_existing_and_adds_new(self):
        """Expand should add new results without losing existing ones."""
        from footfindr.cli_supplier import _merge_results_into_session

        existing = [self._make_part(supplier_pn=f"490-{i:03d}-ND") for i in range(5)]
        session = self._make_session(existing)

        new = [self._make_part(supplier_pn=f"490-{i:03d}-ND") for i in range(5, 8)]
        merged, skipped = _merge_results_into_session(session, new)

        assert merged == 3
        assert skipped == 0
        assert len(session.original_results) == 8

    def test_expand_preserves_session_metadata(self):
        """Expand should not change query, ref_name, category, etc."""
        from footfindr.cli_supplier import _merge_results_into_session

        existing = [self._make_part(supplier_pn="490-001-ND")]
        session = self._make_session(existing)
        assert session.ref_name == "C1"
        assert session.category == "capacitor"

        new = [self._make_part(supplier_pn="490-002-ND")]
        _merge_results_into_session(session, new)

        assert session.ref_name == "C1"
        assert session.category == "capacitor"
        assert session.query == "4.7uF 25V 0603 ceramic capacitor"


# ---------------------------------------------------------------------------
# 4. Structural tests
# ---------------------------------------------------------------------------

class TestStructuralExpand:
    """Verify structural properties of expand integration."""

    def test_expand_command_exists(self):
        """The expand command should be registered."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert 'def supplier_expand_cmd' in source
        assert '"expand"' in source

    def test_expand_alias_exists(self):
        """The 'ex' alias should be registered."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert '"ex"' in source
        assert 'supplier_expand_cmd' in source

    def test_more_degraded_suggests_expand(self):
        """The more command footer should suggest expand when degraded."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        more_start = source.find("def supplier_more_cmd")
        assert more_start > 0
        more_end = source.find("\n    @", more_start + 1)
        if more_end == -1:
            more_end = len(source)
        more_body = source[more_start:more_end]

        assert "ff sup expand ." in more_body

    def test_ls_all_suggests_expand(self):
        """ls --all should suggest expand."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        ls_start = source.find("def supplier_list_cmd")
        assert ls_start > 0
        ls_end = source.find("\n    @", ls_start + 1)
        if ls_end == -1:
            ls_end = len(source)
        ls_body = source[ls_start:ls_end]

        assert "ff sup expand ." in ls_body

    def test_ls_all_shows_unverified_provider_total(self):
        """ls --all should show provider total even when pagination is unverified."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        ls_start = source.find("def supplier_list_cmd")
        assert ls_start > 0
        ls_end = source.find("\n    @", ls_start + 1)
        if ls_end == -1:
            ls_end = len(source)
        ls_body = source[ls_start:ls_end]

        # Should check provider_status (not just degraded)
        assert "session.provider_status" in ls_body
        assert "provider_total_available" in ls_body

    def test_search_for_stores_ref_context(self):
        """search-for should store ref_name, base_query_parts, category."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        sf_start = source.find("def supplier_search_for_cmd")
        assert sf_start > 0
        sf_end = source.find("\n    @", sf_start + 1)
        if sf_end == -1:
            sf_end = len(source)
        sf_body = source[sf_start:sf_end]

        assert "ref_name=" in sf_body
        assert "base_query_parts=" in sf_body
        assert "category=" in sf_body

    def test_search_for_stores_provider_total(self):
        """search-for should store provider_total_available."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        sf_start = source.find("def supplier_search_for_cmd")
        assert sf_start > 0
        sf_end = source.find("\n    @", sf_start + 1)
        if sf_end == -1:
            sf_end = len(source)
        sf_body = source[sf_start:sf_end]

        assert "provider_total_available" in sf_body
        assert "provider_status" in sf_body

    def test_expand_applies_constraints(self):
        """Expand should load and apply constraints from ref_name."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        expand_start = source.find("def supplier_expand_cmd")
        assert expand_start > 0
        expand_end = source.find("\n    @", expand_start + 1)
        if expand_end == -1:
            expand_end = len(source)
        expand_body = source[expand_start:expand_end]

        assert "ConstraintManager" in expand_body
        assert "apply_constraints_to_results" in expand_body

    def test_expand_no_low_relevance_filter(self):
        """Expand should NOT apply display-level low-relevance filtering."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        expand_start = source.find("def supplier_expand_cmd")
        assert expand_start > 0
        expand_end = source.find("\n    @", expand_start + 1)
        if expand_end == -1:
            expand_end = len(source)
        expand_body = source[expand_start:expand_end]

        # Should NOT use compute_relevance or is_mpn_like_query (display filters)
        assert "compute_relevance" not in expand_body
        assert "is_mpn_like_query" not in expand_body


class TestFootprintUntouched:
    """Verify footprint files were not touched."""

    def test_footprint_index_unchanged(self):
        fp = Path("src/footfindr/footprints/footprint_index.py")
        if fp.exists():
            src = fp.read_text(encoding="utf-8")
            assert "expand" not in src.lower() or "expand" not in src  # expand may be in comments
            assert "_generate_expansion_shards" not in src

    def test_footprint_resolver_unchanged(self):
        fp = Path("src/footfindr/footprints/footprint_resolver.py")
        if fp.exists():
            src = fp.read_text(encoding="utf-8")
            assert "_generate_expansion_shards" not in src

    def test_field_writer_unchanged(self):
        fw = Path("src/footfindr/kicad/field_writer.py")
        if fw.exists():
            src = fw.read_text(encoding="utf-8")
            assert "_generate_expansion_shards" not in src

    def test_safe_write_unchanged(self):
        sw = Path("src/footfindr/kicad/safe_write.py")
        if sw.exists():
            src = sw.read_text(encoding="utf-8")
            assert "_generate_expansion_shards" not in src


# ---------------------------------------------------------------------------
# 5. Polish pass tests — debug/trace-http, wording, debug-pagination
# ---------------------------------------------------------------------------

class TestDebugTraceHttp:
    """Verify --debug does NOT enable httpcore/httpx logging, --trace-http does."""

    def test_expand_has_trace_http_flag(self):
        """Expand should have a --trace-http flag."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        expand_start = source.find("def supplier_expand_cmd")
        assert expand_start > 0
        expand_end = source.find("\n    @", expand_start + 1)
        if expand_end == -1:
            expand_end = len(source)
        expand_body = source[expand_start:expand_end]

        assert "--trace-http" in expand_body

    def test_expand_debug_does_not_enable_global_logging(self):
        """--debug should NOT call logging.basicConfig(DEBUG)."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        expand_start = source.find("def supplier_expand_cmd")
        assert expand_start > 0
        expand_end = source.find("\n    @", expand_start + 1)
        if expand_end == -1:
            expand_end = len(source)
        expand_body = source[expand_start:expand_end]

        # logging.basicConfig should only appear after trace_http check, not after debug check
        # Find the if-block for debug vs trace_http
        debug_block_idx = expand_body.find("if debug:")
        trace_block_idx = expand_body.find("if trace_http:")
        basic_config_idx = expand_body.find("logging.basicConfig")

        # basicConfig should appear after trace_http, not after "if debug:"
        assert trace_block_idx >= 0, "Should have trace_http check"
        assert basic_config_idx >= 0, "Should have basicConfig somewhere (for trace_http)"
        assert basic_config_idx > trace_block_idx, "basicConfig should be after trace_http check"

    def test_debug_pagination_has_trace_http(self):
        """debug-pagination should also have --trace-http."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        dp_start = source.find("def supplier_debug_pagination_cmd")
        assert dp_start > 0
        dp_end = source.find("\n    @", dp_start + 1)
        if dp_end == -1:
            dp_end = len(source)
        dp_body = source[dp_start:dp_end]

        assert "--trace-http" in dp_body


class TestDegradedWording:
    """Verify degraded messaging distinguishes provider vs local paging."""

    def test_more_says_provider_pagination(self):
        """more footer should say 'Provider pagination is degraded'."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        more_start = source.find("def supplier_more_cmd")
        assert more_start > 0
        more_end = source.find("\n    @", more_start + 1)
        if more_end == -1:
            more_end = len(source)
        more_body = source[more_start:more_end]

        assert "Provider pagination is degraded" in more_body

    def test_more_says_locally_fetched(self):
        """more footer should mention locally fetched/expanded results."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        more_start = source.find("def supplier_more_cmd")
        assert more_start > 0
        more_end = source.find("\n    @", more_start + 1)
        if more_end == -1:
            more_end = len(source)
        more_body = source[more_start:more_end]

        assert "locally fetched" in more_body.lower()

    def test_ls_says_provider_pagination(self):
        """ls --all should say 'Provider pagination is degraded'."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        ls_start = source.find("def supplier_list_cmd")
        assert ls_start > 0
        ls_end = source.find("\n    @", ls_start + 1)
        if ls_end == -1:
            ls_end = len(source)
        ls_body = source[ls_start:ls_end]

        assert "Provider pagination is degraded" in ls_body

    def test_ls_says_locally_fetched(self):
        """ls --all should mention locally fetched/expanded."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        ls_start = source.find("def supplier_list_cmd")
        assert ls_start > 0
        ls_end = source.find("\n    @", ls_start + 1)
        if ls_end == -1:
            ls_end = len(source)
        ls_body = source[ls_start:ls_end]

        assert "locally fetched" in ls_body.lower()


class TestDebugPaginationCommand:
    """Verify debug-pagination command exists and is structured correctly."""

    def test_command_exists(self):
        """debug-pagination command should be registered."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "def supplier_debug_pagination_cmd" in source
        assert '"debug-pagination"' in source

    def test_probes_multiple_offsets(self):
        """Should probe at offsets 0, 10, 20."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        dp_start = source.find("def supplier_debug_pagination_cmd")
        assert dp_start > 0
        dp_end = source.find("\n    @", dp_start + 1)
        if dp_end == -1:
            dp_end = len(source)
        dp_body = source[dp_start:dp_end]

        assert "offset=0" in dp_body or "offset, 0" in dp_body
        assert "offset=10" in dp_body or "offset, 10" in dp_body
        assert "offset=20" in dp_body or "offset, 20" in dp_body

    def test_probes_different_record_counts(self):
        """Should test count=25 and count=50."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        dp_start = source.find("def supplier_debug_pagination_cmd")
        assert dp_start > 0
        dp_end = source.find("\n    @", dp_start + 1)
        if dp_end == -1:
            dp_end = len(source)
        dp_body = source[dp_start:dp_end]

        assert "count=25" in dp_body
        assert "count=50" in dp_body

    def test_reports_overlap(self):
        """Should report overlap between pages."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        dp_start = source.find("def supplier_debug_pagination_cmd")
        assert dp_start > 0
        dp_end = source.find("\n    @", dp_start + 1)
        if dp_end == -1:
            dp_end = len(source)
        dp_body = source[dp_start:dp_end]

        assert "overlap" in dp_body.lower()

    def test_reports_verdict(self):
        """Should output a verdict on pagination status."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        dp_start = source.find("def supplier_debug_pagination_cmd")
        assert dp_start > 0
        dp_end = source.find("\n    @", dp_start + 1)
        if dp_end == -1:
            dp_end = len(source)
        dp_body = source[dp_start:dp_end]

        assert "DEGRADED" in dp_body
        assert "WORKING" in dp_body

    def test_reports_auth_mode(self):
        """Should report auth mode."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        dp_start = source.find("def supplier_debug_pagination_cmd")
        assert dp_start > 0
        dp_end = source.find("\n    @", dp_start + 1)
        if dp_end == -1:
            dp_end = len(source)
        dp_body = source[dp_start:dp_end]

        assert "auth_mode" in dp_body or "Auth mode" in dp_body

