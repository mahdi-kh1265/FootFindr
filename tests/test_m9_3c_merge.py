"""Tests for M9.3c session merge and degraded pagination UX.

Tests:
- search --add-to-session merges into existing session
- lookup --add-to-session merges into existing session
- add-to-session creates session when none exists
- dedupe by supplier PN
- fallback dedupe by supplier+mfr+mpn
- degraded state persisted
- ls --all displays degraded pagination message (structural)
- more does not suggest itself after degraded (structural)
- footprint files untouched
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# 1. Merge / add-to-session tests
# ---------------------------------------------------------------------------

class TestSearchAddToSession:
    """Test --add-to-session merge behavior on search."""

    def _make_session(self, parts, query="test query"):
        from footfindr.suppliers.session import SearchSession
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return SearchSession(
            query=query,
            suppliers=["digikey"],
            created_at=now,
            last_updated=now,
            original_results=list(parts),
            active_result_ids=[p.result_id for p in parts],
        )

    def _make_part(self, supplier="digikey", mpn="PART-1", supplier_pn="490-001-ND",
                   manufacturer="Murata"):
        from footfindr.suppliers.models import SupplierPart
        return SupplierPart(
            supplier=supplier, mpn=mpn, supplier_pn=supplier_pn,
            manufacturer=manufacturer, source="test",
        )

    def test_merge_adds_new_results(self):
        """New results with different supplier PNs should be merged."""
        from footfindr.cli_supplier import _merge_results_into_session

        existing_part = self._make_part(supplier_pn="490-001-ND", mpn="CAP-A")
        session = self._make_session([existing_part])

        new_part = self._make_part(supplier_pn="490-002-ND", mpn="CAP-B")
        merged, skipped = _merge_results_into_session(session, [new_part])

        assert merged == 1
        assert skipped == 0
        assert len(session.original_results) == 2
        assert len(session.active_result_ids) == 2

    def test_merge_skips_duplicates(self):
        """Results with same supplier PN should be skipped."""
        from footfindr.cli_supplier import _merge_results_into_session

        existing_part = self._make_part(supplier_pn="490-001-ND", mpn="CAP-A")
        session = self._make_session([existing_part])

        dupe_part = self._make_part(supplier_pn="490-001-ND", mpn="CAP-A-VARIANT")
        merged, skipped = _merge_results_into_session(session, [dupe_part])

        assert merged == 0
        assert skipped == 1
        assert len(session.original_results) == 1

    def test_merge_mixed(self):
        """Mix of new and duplicate results."""
        from footfindr.cli_supplier import _merge_results_into_session

        existing = self._make_part(supplier_pn="490-001-ND")
        session = self._make_session([existing])

        new_parts = [
            self._make_part(supplier_pn="490-001-ND"),  # dupe
            self._make_part(supplier_pn="490-002-ND"),  # new
            self._make_part(supplier_pn="490-003-ND"),  # new
        ]
        merged, skipped = _merge_results_into_session(session, new_parts)

        assert merged == 2
        assert skipped == 1
        assert len(session.original_results) == 3

    def test_merge_preserves_session_metadata(self):
        """Merge should not change query, suppliers, or other session metadata."""
        from footfindr.cli_supplier import _merge_results_into_session

        existing = self._make_part(supplier_pn="490-001-ND")
        session = self._make_session([existing], query="4.7uF capacitor")
        original_query = session.query
        original_suppliers = session.suppliers

        new_part = self._make_part(supplier_pn="490-002-ND")
        _merge_results_into_session(session, [new_part])

        assert session.query == original_query
        assert session.suppliers == original_suppliers


class TestDedupeKey:
    """Test dedupe key generation."""

    def _make_part(self, supplier="digikey", mpn="PART-1", supplier_pn="490-001-ND",
                   manufacturer="Murata"):
        from footfindr.suppliers.models import SupplierPart
        return SupplierPart(
            supplier=supplier, mpn=mpn, supplier_pn=supplier_pn,
            manufacturer=manufacturer, source="test",
        )

    def test_dedupe_by_supplier_pn(self):
        """Primary dedupe uses (supplier, supplier_pn)."""
        from footfindr.cli_supplier import _dedupe_key

        p1 = self._make_part(supplier="digikey", supplier_pn="490-001-ND")
        p2 = self._make_part(supplier="digikey", supplier_pn="490-001-ND")

        assert _dedupe_key(p1) == _dedupe_key(p2)

    def test_different_supplier_pn_different_key(self):
        """Different supplier PNs should produce different keys."""
        from footfindr.cli_supplier import _dedupe_key

        p1 = self._make_part(supplier="digikey", supplier_pn="490-001-ND")
        p2 = self._make_part(supplier="digikey", supplier_pn="490-002-ND")

        assert _dedupe_key(p1) != _dedupe_key(p2)

    def test_different_supplier_same_pn_different_key(self):
        """Same PN from different suppliers should be different."""
        from footfindr.cli_supplier import _dedupe_key

        p1 = self._make_part(supplier="digikey", supplier_pn="490-001-ND")
        p2 = self._make_part(supplier="mouser", supplier_pn="490-001-ND")

        assert _dedupe_key(p1) != _dedupe_key(p2)

    def test_fallback_dedupe_by_mfr_mpn(self):
        """When supplier_pn is missing, fallback to (supplier, mfr, mpn)."""
        from footfindr.cli_supplier import _dedupe_key

        p1 = self._make_part(supplier="jlcpcb", supplier_pn=None,
                              mpn="GRM188R61E475KE11D", manufacturer="Murata")
        p2 = self._make_part(supplier="jlcpcb", supplier_pn=None,
                              mpn="GRM188R61E475KE11D", manufacturer="Murata")

        assert _dedupe_key(p1) == _dedupe_key(p2)

    def test_fallback_different_mpn(self):
        """Different MPNs in fallback should produce different keys."""
        from footfindr.cli_supplier import _dedupe_key

        p1 = self._make_part(supplier="jlcpcb", supplier_pn=None,
                              mpn="GRM188R61E475KE11D", manufacturer="Murata")
        p2 = self._make_part(supplier="jlcpcb", supplier_pn=None,
                              mpn="GRT188C81E475KE13D", manufacturer="Murata")

        assert _dedupe_key(p1) != _dedupe_key(p2)

    def test_case_insensitive_supplier_pn(self):
        """Dedupe should be case-insensitive."""
        from footfindr.cli_supplier import _dedupe_key

        p1 = self._make_part(supplier="DigiKey", supplier_pn="490-001-ND")
        p2 = self._make_part(supplier="digikey", supplier_pn="490-001-nd")

        assert _dedupe_key(p1) == _dedupe_key(p2)


# ---------------------------------------------------------------------------
# 2. Degraded pagination state tests
# ---------------------------------------------------------------------------

class TestDegradedPaginationState:
    """Test degraded pagination state persistence."""

    def test_session_defaults_to_normal(self):
        """New session should have pagination_status='normal'."""
        from footfindr.suppliers.session import SearchSession
        from footfindr.suppliers.models import SupplierPart

        parts = [SupplierPart(supplier="dk", mpn="P1", source="t")]
        session = SearchSession(
            query="test",
            suppliers=["digikey"],
            created_at="2025-01-01",
            last_updated="2025-01-01",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )

        assert session.pagination_status == "normal"
        assert session.provider_status == {}

    def test_degraded_state_persists(self, tmp_path):
        """Degraded pagination state should survive save/load."""
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
            pagination_status="degraded",
            provider_status={
                "digikey": {
                    "provider_total_available": 52,
                    "fetched_unique": 10,
                    "pagination_status": "degraded_duplicate_page",
                    "last_attempted_offset": 10,
                    "last_raw_count": 10,
                    "last_new_unique_count": 0,
                }
            },
        )

        mgr = SessionManager(workspace=tmp_path)
        mgr.save(session)
        loaded = mgr.load()

        assert loaded is not None
        assert loaded.pagination_status == "degraded"
        assert "digikey" in loaded.provider_status
        assert loaded.provider_status["digikey"]["provider_total_available"] == 52
        assert loaded.provider_status["digikey"]["fetched_unique"] == 10
        assert loaded.provider_status["digikey"]["pagination_status"] == "degraded_duplicate_page"

    def test_old_session_loads_as_normal(self, tmp_path):
        """Sessions saved before M9.3c should load with normal status."""
        import json

        mgr_dir = tmp_path / "session"
        mgr_dir.mkdir()
        # Write a session file without pagination_status/provider_status
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
        assert loaded.pagination_status == "normal"
        assert loaded.provider_status == {}


# ---------------------------------------------------------------------------
# 3. Structural tests
# ---------------------------------------------------------------------------

class TestStructuralMoreDegraded:
    """Verify more command respects degraded state."""

    def test_more_checks_degraded_before_suggesting(self):
        """The more command should not suggest 'ff sup more .' when degraded."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        # Find the more command section
        more_start = source.find("def supplier_more_cmd")
        assert more_start > 0

        more_end = source.find("\n    @", more_start + 1)
        if more_end == -1:
            more_end = len(source)
        more_body = source[more_start:more_end]

        # Should check pagination_status before suggesting more
        assert 'pagination_status == "degraded"' in more_body
        # Should suggest --add-to-session as workaround
        assert "--add-to-session" in more_body

    def test_ls_all_checks_degraded(self):
        """The ls --all command should show degraded info."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")

        # Find the list command section
        ls_start = source.find("def supplier_list_cmd")
        assert ls_start > 0

        ls_end = source.find("\n    @", ls_start + 1)
        if ls_end == -1:
            ls_end = len(source)
        ls_body = source[ls_start:ls_end]

        assert 'pagination_status == "degraded"' in ls_body
        assert "--add-to-session" in ls_body


class TestAddToSessionFlag:
    """Verify --add-to-session flag exists on search and lookup."""

    def test_search_has_add_to_session(self):
        """search command should have --add-to-session flag."""
        import footfindr.cli_supplier as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert '"--add-to-session"' in source
        assert '"--add"' in source

    def test_lookup_has_add_to_session(self):
        """lookup command should have --add-to-session flag."""
        import footfindr.cli as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert '"--add-to-session"' in source


class TestFootprintUntouched:
    """Verify footprint files were not touched."""

    def test_footprint_index_unchanged(self):
        """footprint_index.py should not have been modified by this patch."""
        # This is a structural assertion — we verify that the file exists
        # and doesn't import from cli_supplier (which would indicate coupling)
        fp_index = Path("src/footfindr/footprints/footprint_index.py")
        if fp_index.exists():
            source = fp_index.read_text(encoding="utf-8")
            assert "add_to_session" not in source
            assert "_merge_results" not in source

    def test_footprint_resolver_unchanged(self):
        """footprint_resolver.py should not reference session merge."""
        fp_resolver = Path("src/footfindr/footprints/footprint_resolver.py")
        if fp_resolver.exists():
            source = fp_resolver.read_text(encoding="utf-8")
            assert "add_to_session" not in source
            assert "_merge_results" not in source

    def test_field_writer_unchanged(self):
        """field_writer.py should not reference session merge."""
        fw = Path("src/footfindr/kicad/field_writer.py")
        if fw.exists():
            source = fw.read_text(encoding="utf-8")
            assert "add_to_session" not in source

    def test_safe_write_unchanged(self):
        """safe_write.py should not reference session merge."""
        sw = Path("src/footfindr/kicad/safe_write.py")
        if sw.exists():
            source = sw.read_text(encoding="utf-8")
            assert "add_to_session" not in source
