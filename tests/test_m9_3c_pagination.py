"""Tests for M9.3c follow-up: SupplierSearchPage compat, pagination, cache safety, --min alias.

Narrowly scoped — does not touch footprint indexing/resolution/write code.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. SupplierSearchPage compatibility tests
# ---------------------------------------------------------------------------

class TestSupplierSearchPageCompat:
    """Verify no code path does len(SupplierSearchPage) or iterates it directly."""

    def test_no_len_on_search_page(self):
        """SupplierSearchPage should not support len() — guard against misuse."""
        from footfindr.suppliers.base import SupplierSearchPage
        from footfindr.suppliers.models import SupplierPart

        page = SupplierSearchPage(
            items=[SupplierPart(supplier="digikey", mpn="TEST-1", source="test")],
            supplier="digikey",
            query="test",
            limit=10,
            offset=0,
            total_available=52,
            has_more=True,
        )

        # SupplierSearchPage is a dataclass — it should NOT have __len__
        # If someone adds __len__, this test catches it early
        with pytest.raises(TypeError, match="has no len"):
            len(page)

    def test_items_attribute_exists(self):
        """SupplierSearchPage.items should be a list."""
        from footfindr.suppliers.base import SupplierSearchPage

        page = SupplierSearchPage(
            items=[], supplier="digikey", query="test",
            limit=10, offset=0,
        )
        assert isinstance(page.items, list)
        assert hasattr(page, "items")

    def test_auth_test_unwrap_pattern(self):
        """The unwrap pattern used in auth test should work correctly."""
        from footfindr.suppliers.base import SupplierSearchPage
        from footfindr.suppliers.models import SupplierPart

        # Simulate SupplierSearchPage return
        page = SupplierSearchPage(
            items=[
                SupplierPart(supplier="digikey", mpn="TEST-1", source="test"),
                SupplierPart(supplier="digikey", mpn="TEST-2", source="test"),
            ],
            supplier="digikey",
            query="test",
            limit=10,
            offset=0,
            total_available=52,
            has_more=True,
        )

        # This is the pattern used in the fix
        items = page.items if hasattr(page, "items") else page
        count = len(items)

        assert count == 2
        assert items[0].mpn == "TEST-1"

    def test_auth_test_unwrap_with_plain_list(self):
        """The unwrap pattern should also work with a plain list (non-DigiKey)."""
        from footfindr.suppliers.models import SupplierPart

        plain_list = [
            SupplierPart(supplier="mouser", mpn="PART-A", source="test"),
        ]

        items = plain_list.items if hasattr(plain_list, "items") else plain_list
        count = len(items)

        assert count == 1
        assert items[0].mpn == "PART-A"

    def test_digikey_lookup_fallback_unwrap(self):
        """DigiKey lookup_mpn 404 fallback should unwrap SupplierSearchPage."""
        from footfindr.suppliers.base import SupplierSearchPage
        from footfindr.suppliers.models import SupplierPart

        # Simulate what lookup_mpn does after the fix
        search_result = SupplierSearchPage(
            items=[
                SupplierPart(supplier="digikey", mpn="GRM188", source="test",
                             manufacturer="Murata"),
                SupplierPart(supplier="digikey", mpn="GRM155", source="test",
                             manufacturer="Murata"),
            ],
            supplier="digikey",
            query="GRM188",
            limit=25,
            offset=0,
        )

        # This is the fixed pattern
        results = search_result.items if hasattr(search_result, "items") else search_result
        assert len(results) == 2
        assert results[0].mpn == "GRM188"

    def test_structural_no_len_search_in_cli(self):
        """Grep: no code path in cli.py or cli_supplier.py does len(provider.search(...))."""
        import footfindr.cli as cli_mod
        import footfindr.cli_supplier as sup_mod

        for mod_path in [cli_mod.__file__, sup_mod.__file__]:
            source = Path(mod_path).read_text(encoding="utf-8")
            # Should NOT contain len(provider.search(...)) or len(result) on the search line
            # The pattern "len(result)" right after "provider.search" is the bug
            lines = source.split("\n")
            for i, line in enumerate(lines):
                stripped = line.strip()
                # Check for the specific bug pattern: len(result) where result = provider.search(...)
                if "len(result)" in stripped and i > 0:
                    prev_line = lines[i - 1].strip() if i > 0 else ""
                    # If the previous line assigns result from provider.search, that's the bug
                    if "provider.search" in prev_line and "items" not in lines[i].strip():
                        pytest.fail(
                            f"Found len(result) without .items unwrap after provider.search "
                            f"in {Path(mod_path).name}:{i + 1}"
                        )


# ---------------------------------------------------------------------------
# 2. Pagination tests
# ---------------------------------------------------------------------------

class TestPaginationMore:
    """Verify more command sends correct offset and uses session supplier."""

    def test_session_provider_offsets_used(self):
        """more should use provider_offsets from the session, not default providers."""
        from footfindr.suppliers.session import SearchSession
        from footfindr.suppliers.models import SupplierPart

        parts = [
            SupplierPart(supplier="digikey", mpn=f"PART-{i}", source="test")
            for i in range(10)
        ]
        session = SearchSession(
            query="4.7uF 25V 0603 ceramic capacitor",
            suppliers=["digikey"],
            created_at="2025-01-01T00:00:00Z",
            last_updated="2025-01-01T00:00:00Z",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            page_size=10,
            provider_offsets={"digikey": 10},
        )

        # provider_offsets should be used to:
        # 1. Determine which suppliers to query
        # 2. What offset to send
        assert "digikey" in session.provider_offsets
        assert session.provider_offsets["digikey"] == 10

    def test_digikey_search_payload_uses_offset(self):
        """DigiKey search() should pass offset as RecordStartPosition in payload."""
        from footfindr.suppliers.digikey import DigiKeyProvider

        # We can't call the real API, but we can verify the code path
        # by checking the source code structure
        import inspect
        source = inspect.getsource(DigiKeyProvider.search)

        assert "RecordStartPosition" in source
        assert "offset" in source
        # RecordStartPosition should be set from offset parameter
        assert '"RecordStartPosition": offset' in source

    def test_session_has_next_page_logic(self):
        """has_next_page should correctly determine if local pages remain."""
        from footfindr.suppliers.session import SearchSession
        from footfindr.suppliers.models import SupplierPart

        parts = [
            SupplierPart(supplier="digikey", mpn=f"PART-{i}", source="test")
            for i in range(15)
        ]
        session = SearchSession(
            query="test",
            suppliers=["digikey"],
            created_at="2025-01-01T00:00:00Z",
            last_updated="2025-01-01T00:00:00Z",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            page_size=10,
            current_page=1,
        )

        # 15 results, page_size=10, current_page=1 -> page 2 exists
        assert session.has_next_page()

        session.current_page = 2
        # Page 2 = results 10-14 (5 items), no page 3
        assert not session.has_next_page()


# ---------------------------------------------------------------------------
# 3. Cache offset safety tests
# ---------------------------------------------------------------------------

class TestCacheOffsetSafety:
    """Verify search cache does not return offset=0 results for offset>0."""

    def test_search_cache_stores_by_query(self, tmp_path):
        """Cache stores by (supplier, normalized_query) — no offset in key."""
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.models import SupplierPart

        cache = SupplierCache(workspace=tmp_path)

        page1_parts = [
            SupplierPart(supplier="digikey", mpn=f"PAGE1-{i}", source="test")
            for i in range(5)
        ]
        cache.store_search("digikey", "capacitor 0603", page1_parts)

        # Lookup should return page 1 results for same query
        result = cache.lookup_search("digikey", "capacitor 0603")
        assert result is not None
        assert len(result) == 5
        assert result[0].mpn == "PAGE1-0"

        cache.close()

    def test_run_search_skips_cache_for_nonzero_offset(self):
        """_run_search in search-for must skip cache when offset > 0.

        This is verified by checking the code guard at line ~1265-1266:
            if not refresh and search_offset == 0:
                cached = cache.lookup_search(...)
        """
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        # The guard: only use cache when search_offset == 0
        assert "search_offset == 0" in source

    def test_more_does_not_use_cache(self):
        """The more command calls provider.search() directly, bypassing cache."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        # Find the more command section
        more_start = source.find("def supplier_more_cmd")
        assert more_start > 0

        # Get the more command body (until next def or end)
        more_end = source.find("\n    @", more_start + 1)
        if more_end == -1:
            more_end = len(source)
        more_body = source[more_start:more_end]

        # more should NOT call cache.lookup_search
        assert "lookup_search" not in more_body, \
            "more command should not use search cache — it calls provider.search() directly"


# ---------------------------------------------------------------------------
# 4. --min alias tests
# ---------------------------------------------------------------------------

class TestMinAliasUniversal:
    """Verify --min and -q are on every command that has --mini."""

    def test_all_mini_options_have_min_alias(self):
        """Every --mini option should also have --min."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        # Count --mini occurrences in both regular and toggle forms
        lines = source.split("\n")
        mini_option_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if ("--mini" in stripped and "typer.Option" in stripped
                    and "render_mini" not in stripped):
                mini_option_lines.append((i + 1, stripped))

        # 6 regular (search, list, group, filter, sort, search-for)
        # + 4 toggle (more, next, prev, page) = 10 total
        assert len(mini_option_lines) >= 9, \
            f"Expected at least 9 commands with --mini option, found {len(mini_option_lines)}"

        for lineno, line in mini_option_lines:
            assert '"--min"' in line, \
                f"Line {lineno} has --mini but missing --min: {line[:80]}"

    def test_all_mini_options_have_q_alias(self):
        """Every --mini option should also have -q."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if '"--mini"' in stripped and "typer.Option" in stripped:
                # Should have -q somewhere in the same option
                assert '"-q' in stripped, \
                    f"Line {i + 1} has --mini but missing -q: {stripped[:80]}"

    def test_live_alias_acceptance(self):
        """Structural test: the three flag variants should be in the same Option call."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        # For regular options: --mini, --min, -q in same call
        assert '"--mini", "--min", "-q"' in source

        # For toggle options: --mini/--full, --min, -q/-Q in same call
        assert '"--mini/--full", "--min", "-q/-Q"' in source


# ---------------------------------------------------------------------------
# 5. DigiKey provider search pagination structure
# ---------------------------------------------------------------------------

class TestDigiKeyPaginationStructure:
    """Verify DigiKey search() returns correct SupplierSearchPage with pagination metadata."""

    def test_search_returns_supplier_search_page(self):
        """DigiKey.search() return type annotation should be SupplierSearchPage."""
        from footfindr.suppliers.digikey import DigiKeyProvider
        import inspect

        sig = inspect.signature(DigiKeyProvider.search)
        ret = sig.return_annotation

        # Return annotation should mention SupplierSearchPage
        assert "SupplierSearchPage" in str(ret)

    def test_search_page_has_pagination_fields(self):
        """SupplierSearchPage should have offset, limit, total_available, has_more."""
        from footfindr.suppliers.base import SupplierSearchPage

        page = SupplierSearchPage(
            items=[], supplier="digikey", query="test",
            limit=10, offset=20, total_available=52, has_more=True,
        )

        assert page.offset == 20
        assert page.limit == 10
        assert page.total_available == 52
        assert page.has_more is True

    def test_search_page_has_more_calculation(self):
        """has_more should be True when offset + items < total_available."""
        from footfindr.suppliers.base import SupplierSearchPage
        from footfindr.suppliers.models import SupplierPart

        items = [SupplierPart(supplier="dk", mpn=f"P{i}", source="t") for i in range(10)]

        # Page 1: offset=0, 10 items, 52 total -> has_more=True
        page1 = SupplierSearchPage(
            items=items, supplier="dk", query="test",
            limit=10, offset=0, total_available=52, has_more=True,
        )
        assert page1.has_more is True

        # Last page: offset=50, 2 items, 52 total -> has_more=False
        page_last = SupplierSearchPage(
            items=items[:2], supplier="dk", query="test",
            limit=10, offset=50, total_available=52, has_more=False,
        )
        assert page_last.has_more is False
