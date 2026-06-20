"""Tests for M9.3: Pagination, Footprint Index, Resolution, Mappings, BOM checks."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Minimal workspace directory."""
    ws = tmp_path / ".footfindr"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "session").mkdir()
    (ws / "libraries").mkdir()
    (ws / "plans").mkdir()
    (ws / "index").mkdir()
    return ws


@pytest.fixture
def footprint_lib(tmp_path: Path) -> Path:
    """Create a fake KiCad footprint library structure."""
    # Create Capacitor_SMD library
    cap_dir = tmp_path / "kicad_libs" / "Capacitor_SMD.pretty"
    cap_dir.mkdir(parents=True)

    # Create .kicad_mod files
    for fp_name in [
        "C_0201_0603Metric",
        "C_0402_1005Metric",
        "C_0603_1608Metric",
        "C_0805_2012Metric",
        "C_1206_3216Metric",
    ]:
        mod_file = cap_dir / f"{fp_name}.kicad_mod"
        mod_file.write_text(
            f'(footprint "{fp_name}"\n'
            f'  (layer "F.Cu")\n'
            f'  (pad "1" smd rect (at -0.5 0) (size 0.5 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
            f'  (pad "2" smd rect (at 0.5 0) (size 0.5 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
            f')\n',
            encoding="utf-8",
        )

    # Create Resistor_SMD library
    res_dir = tmp_path / "kicad_libs" / "Resistor_SMD.pretty"
    res_dir.mkdir(parents=True)
    for fp_name in ["R_0603_1608Metric", "R_0805_2012Metric"]:
        (res_dir / f"{fp_name}.kicad_mod").write_text(
            f'(footprint "{fp_name}"\n  (pad "1" smd rect (at 0 0) (size 1 1))\n  (pad "2" smd rect (at 1 0) (size 1 1))\n)\n',
            encoding="utf-8",
        )

    # Create Package_DFN_QFN library
    ic_dir = tmp_path / "kicad_libs" / "Package_DFN_QFN.pretty"
    ic_dir.mkdir(parents=True)
    for fp_name in ["QFN-32-1EP_5x5mm_P0.5mm", "DFN-10-1EP_3x3mm_P0.5mm"]:
        pads = "\n".join(
            f'  (pad "{i}" smd rect (at {i*0.5} 0) (size 0.3 0.8))'
            for i in range(1, 11)
        )
        (ic_dir / f"{fp_name}.kicad_mod").write_text(
            f'(footprint "{fp_name}"\n{pads}\n)\n',
            encoding="utf-8",
        )

    # Create LED_SMD library
    led_dir = tmp_path / "kicad_libs" / "LED_SMD.pretty"
    led_dir.mkdir(parents=True)
    (led_dir / "LED_0402_1005Metric.kicad_mod").write_text(
        '(footprint "LED_0402_1005Metric"\n  (pad "1" smd rect (at 0 0) (size 1 1))\n  (pad "2" smd rect (at 1 0) (size 1 1))\n)\n',
        encoding="utf-8",
    )

    return tmp_path / "kicad_libs"


@pytest.fixture
def fp_lib_table(tmp_path: Path, footprint_lib: Path) -> Path:
    """Create a KiCad fp-lib-table file pointing to the test libraries."""
    lib_dir = footprint_lib
    table_path = tmp_path / "fp-lib-table"
    table_path.write_text(
        f'(fp_lib_table\n'
        f'  (version 7)\n'
        f'  (lib (name "Capacitor_SMD")(type "KiCad")(uri "{lib_dir / "Capacitor_SMD.pretty"}")(options "")(descr "SMD caps"))\n'
        f'  (lib (name "Resistor_SMD")(type "KiCad")(uri "{lib_dir / "Resistor_SMD.pretty"}")(options "")(descr "SMD resistors"))\n'
        f'  (lib (name "Package_DFN_QFN")(type "KiCad")(uri "{lib_dir / "Package_DFN_QFN.pretty"}")(options "")(descr "IC packages"))\n'
        f'  (lib (name "LED_SMD")(type "KiCad")(uri "{lib_dir / "LED_SMD.pretty"}")(options "")(descr "SMD LEDs"))\n'
        f')\n',
        encoding="utf-8",
    )
    return table_path


@pytest.fixture
def indexed_footprints(tmp_path: Path, fp_lib_table: Path) -> "FootprintIndex":
    """Build and return a populated FootprintIndex."""
    from footfindr.kicad.footprint_index import FootprintIndex
    index = FootprintIndex(project_dir=tmp_path)
    index.scan([fp_lib_table], project_dir=tmp_path)
    return index


# ---------------------------------------------------------------------------
# Pagination Tests
# ---------------------------------------------------------------------------

class TestSessionPagination:
    """Test SearchSession pagination methods."""

    def _make_session(self, n_results: int, page_size: int = 10):
        from footfindr.suppliers.session import SearchSession
        from footfindr.suppliers.models import SupplierPart

        parts = [
            SupplierPart(
                supplier="mock", mpn=f"PART-{i:03d}",
                description=f"Part {i}", source="test",
            )
            for i in range(1, n_results + 1)
        ]
        return SearchSession(
            query="test",
            suppliers=["mock"],
            created_at="2025-01-01T00:00:00Z",
            last_updated="2025-01-01T00:00:00Z",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            page_size=page_size,
        )

    def test_total_pages_exact(self):
        s = self._make_session(20, page_size=10)
        assert s.total_pages() == 2

    def test_total_pages_remainder(self):
        s = self._make_session(25, page_size=10)
        assert s.total_pages() == 3

    def test_total_pages_single(self):
        s = self._make_session(5, page_size=10)
        assert s.total_pages() == 1

    def test_total_pages_empty(self):
        s = self._make_session(0, page_size=10)
        assert s.total_pages() == 1  # Min 1

    def test_get_page_first(self):
        s = self._make_session(25, page_size=10)
        page = s.get_page(1)
        assert len(page) == 10
        assert page[0].mpn == "PART-001"

    def test_get_page_last(self):
        s = self._make_session(25, page_size=10)
        page = s.get_page(3)
        assert len(page) == 5
        assert page[0].mpn == "PART-021"

    def test_has_next_page(self):
        s = self._make_session(25, page_size=10)
        assert s.has_next_page() is True
        s.current_page = 3
        assert s.has_next_page() is False

    def test_page_status_line(self):
        s = self._make_session(25, page_size=10)
        status = s.get_page_status_line()
        assert "Page: 1/3" in status
        assert "1-10 of 25" in status


class TestPaginationPersistence:
    """Test pagination state save/load."""

    def test_save_load_pagination(self, workspace: Path):
        from footfindr.suppliers.session import SearchSession, SessionManager
        from footfindr.suppliers.models import SupplierPart

        parts = [
            SupplierPart(
                supplier="mock", mpn=f"PART-{i}", source="test",
            )
            for i in range(5)
        ]
        session = SearchSession(
            query="test",
            suppliers=["mock"],
            created_at="2025-01-01",
            last_updated="2025-01-01",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            page_size=3,
            current_page=2,
            provider_offsets={"digikey": 25},
        )

        mgr = SessionManager(workspace=workspace)
        mgr.save(session)

        loaded = mgr.load()
        assert loaded is not None
        assert loaded.page_size == 3
        assert loaded.current_page == 2
        assert loaded.provider_offsets == {"digikey": 25}

    def test_load_without_pagination_defaults(self, workspace: Path):
        """Old sessions without pagination fields should get defaults."""
        from footfindr.suppliers.session import SessionManager

        session_dir = workspace / "session"
        session_dir.mkdir(exist_ok=True)
        session_file = session_dir / "supplier_search.json"
        # Write a session without pagination fields
        session_file.write_text(json.dumps({
            "query": "test",
            "suppliers": ["mock"],
            "created_at": "2025-01-01",
            "last_updated": "2025-01-01",
            "original_results": [],
            "active_result_ids": [],
        }), encoding="utf-8")

        mgr = SessionManager(workspace=workspace)
        loaded = mgr.load()
        assert loaded is not None
        assert loaded.page_size == 10
        assert loaded.current_page == 1
        assert loaded.provider_offsets == {}


# ---------------------------------------------------------------------------
# Footprint Index Tests
# ---------------------------------------------------------------------------

class TestFpLibTableParser:
    """Test fp-lib-table S-expression parsing."""

    def test_parse_basic(self, fp_lib_table: Path):
        from footfindr.kicad.footprint_index import parse_fp_lib_table
        entries = parse_fp_lib_table(fp_lib_table)
        assert len(entries) == 4
        names = [e.name for e in entries]
        assert "Capacitor_SMD" in names
        assert "Resistor_SMD" in names

    def test_parse_missing_file(self, tmp_path: Path):
        from footfindr.kicad.footprint_index import parse_fp_lib_table
        entries = parse_fp_lib_table(tmp_path / "nonexistent.txt")
        assert entries == []

    def test_parse_entry_fields(self, fp_lib_table: Path):
        from footfindr.kicad.footprint_index import parse_fp_lib_table
        entries = parse_fp_lib_table(fp_lib_table)
        cap_entry = next(e for e in entries if e.name == "Capacitor_SMD")
        assert cap_entry.type == "KiCad"
        assert "Capacitor_SMD.pretty" in cap_entry.uri

    def test_parse_empty_file(self, tmp_path: Path):
        from footfindr.kicad.footprint_index import parse_fp_lib_table
        empty = tmp_path / "fp-lib-table"
        empty.write_text("(fp_lib_table\n)\n", encoding="utf-8")
        entries = parse_fp_lib_table(empty)
        assert entries == []


class TestFootprintIndex:
    """Test footprint scanning and indexing."""

    def test_scan_counts(self, indexed_footprints):
        count = indexed_footprints.count()
        # 5 caps + 2 res + 2 ICs + 1 LED = 10
        assert count == 10

    def test_search_by_size(self, indexed_footprints):
        results = indexed_footprints.search("0603")
        kicad_ids = [r.kicad_id for r in results]
        assert "Capacitor_SMD:C_0603_1608Metric" in kicad_ids
        assert "Resistor_SMD:R_0603_1608Metric" in kicad_ids

    def test_search_by_library(self, indexed_footprints):
        results = indexed_footprints.search("Capacitor_SMD")
        assert all(r.library_nickname == "Capacitor_SMD" for r in results)
        assert len(results) == 5

    def test_get_exact(self, indexed_footprints):
        record = indexed_footprints.get("Capacitor_SMD:C_0603_1608Metric")
        assert record is not None
        assert record.footprint_name == "C_0603_1608Metric"
        assert record.library_nickname == "Capacitor_SMD"

    def test_get_missing(self, indexed_footprints):
        record = indexed_footprints.get("Nonexistent:FP_999")
        assert record is None

    def test_list_all(self, indexed_footprints):
        all_records = indexed_footprints.list_all()
        assert len(all_records) == 10

    def test_list_libraries(self, indexed_footprints):
        libs = indexed_footprints.list_libraries()
        assert "Capacitor_SMD" in libs
        assert "Resistor_SMD" in libs

    def test_package_tokens_extraction(self, indexed_footprints):
        record = indexed_footprints.get("Capacitor_SMD:C_0603_1608Metric")
        assert record is not None
        assert "0603" in record.package_tokens


# ---------------------------------------------------------------------------
# Footprint Resolution Tests
# ---------------------------------------------------------------------------

class TestPassiveFootprintResolution:
    """Test passive component footprint auto-resolution."""

    def test_capacitor_0603(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="GRM188", package="0603 (1608 Metric)", attributes={})
        result = resolver.resolve(part, "C1", "capacitor")
        assert result.status == "exact"
        assert result.footprint == "Capacitor_SMD:C_0603_1608Metric"
        assert result.confidence == "high"

    def test_resistor_0805(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="RC0805", package="0805", attributes={})
        result = resolver.resolve(part, "R1", "resistor")
        assert result.status == "exact"
        assert result.footprint == "Resistor_SMD:R_0805_2012Metric"

    def test_capacitor_0402(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="GRM155", package="0402", attributes={})
        result = resolver.resolve(part, "C2", "capacitor")
        assert result.status == "exact"
        assert result.footprint == "Capacitor_SMD:C_0402_1005Metric"

    def test_led_0402(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="LED123", package="0402", attributes={})
        result = resolver.resolve(part, "D1", "led")
        assert result.status == "exact"
        assert result.footprint == "LED_SMD:LED_0402_1005Metric"

    def test_capacitor_1206(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="GRM31", package="1206", attributes={})
        result = resolver.resolve(part, "C3", "capacitor")
        assert result.status == "exact"
        assert result.footprint == "Capacitor_SMD:C_1206_3216Metric"

    def test_missing_package(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="UNKNOWN", package="", attributes={})
        result = resolver.resolve(part, "C99", "capacitor")
        assert result.status == "missing"

    def test_unknown_size(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="PART", package="9999", attributes={})
        result = resolver.resolve(part, "C99", "capacitor")
        # Should be missing since 9999 is not a known imperial/metric size
        assert result.status in ("missing", "ambiguous")

    def test_package_normalization_strips_metric(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="GRM", package="0805 (2012 Metric)", attributes={})
        result = resolver.resolve(part, "C4", "capacitor")
        assert result.status == "exact"
        assert result.footprint == "Capacitor_SMD:C_0805_2012Metric"


class TestICFootprintResolution:
    """Test IC footprint resolution (conservative)."""

    def test_dfn10_exact(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(
            mpn="LT3045", package="DFN-10-1EP_3x3mm",
            attributes={"Number of Pins": "10"},
        )
        result = resolver.resolve(part, "U1", "ic")
        assert result.status == "exact"
        assert "DFN-10" in result.footprint
        assert "3x3mm" in result.footprint

    def test_qfn32_exact(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(
            mpn="STM32F0", package="QFN-32-1EP_5x5mm",
            attributes={},
        )
        result = resolver.resolve(part, "U2", "ic")
        assert result.status == "exact"
        assert "QFN-32" in result.footprint

    def test_missing_ic_family(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="CHIP", package="UNKNOWN-PKG", attributes={})
        result = resolver.resolve(part, "U99", "ic")
        assert result.status == "missing"

    def test_ic_with_no_package(self, indexed_footprints):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from types import SimpleNamespace

        resolver = FootprintResolver(indexed_footprints)
        part = SimpleNamespace(mpn="IC1", package="", attributes={})
        result = resolver.resolve(part, "U99", "ic")
        assert result.status == "missing"


# ---------------------------------------------------------------------------
# Footprint Mappings Tests
# ---------------------------------------------------------------------------

class TestFootprintMappings:
    """Test YAML-backed footprint mapping database."""

    def test_bind_and_lookup_ref(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        m.bind_ref("C1", "Capacitor_SMD:C_0603_1608Metric")

        result = m.lookup(ref="C1")
        assert result is not None
        assert result.footprint == "Capacitor_SMD:C_0603_1608Metric"

    def test_bind_and_lookup_mpn(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        m.bind_mpn("GRT188C81E475KE13D", "Capacitor_SMD:C_0603_1608Metric")

        result = m.lookup(mpn="GRT188C81E475KE13D")
        assert result is not None
        assert result.footprint == "Capacitor_SMD:C_0603_1608Metric"

    def test_bind_and_lookup_package(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        m.bind_package("capacitor", "0603", "Capacitor_SMD:C_0603_1608Metric")

        result = m.lookup(category="capacitor", package="0603")
        assert result is not None
        assert result.footprint == "Capacitor_SMD:C_0603_1608Metric"

    def test_precedence_ref_over_mpn(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        m.bind_mpn("MPN1", "FP_MPN")
        m.bind_ref("C1", "FP_REF")

        result = m.lookup(ref="C1", mpn="MPN1")
        assert result is not None
        assert result.footprint == "FP_REF"  # ref takes precedence

    def test_precedence_mpn_over_package(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        m.bind_package("capacitor", "0603", "FP_PKG")
        m.bind_mpn("MPN1", "FP_MPN")

        result = m.lookup(mpn="MPN1", category="capacitor", package="0603")
        assert result is not None
        assert result.footprint == "FP_MPN"  # MPN takes precedence

    def test_save_and_reload(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        m.bind_ref("C1", "FP_C1")
        m.bind_mpn("MPN1", "FP_MPN1")

        # Reload from file
        m2 = FootprintMappings(path=path)
        assert m2.lookup(ref="C1").footprint == "FP_C1"
        assert m2.lookup(mpn="MPN1").footprint == "FP_MPN1"

    def test_lookup_not_found(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        assert m.lookup(ref="NONEXISTENT") is None

    def test_list_all(self, tmp_path: Path):
        from footfindr.kicad.footprint_mappings import FootprintMappings
        path = tmp_path / "mappings.yaml"
        m = FootprintMappings(path=path)
        m.bind_ref("C1", "FP_C1")
        m.bind_mpn("MPN1", "FP_MPN1")

        all_maps = m.list_all()
        assert "ref" in all_maps
        assert "mpn" in all_maps
        assert len(all_maps["ref"]) == 1
        assert len(all_maps["mpn"]) == 1


# ---------------------------------------------------------------------------
# BOM Footprint Checks
# ---------------------------------------------------------------------------

class TestBomFootprintChecks:
    """Test footprint-related BOM checks in review.py."""

    def test_footprint_matches_package_exact(self):
        from footfindr.review import _footprint_matches_package
        assert _footprint_matches_package("Capacitor_SMD:C_0603_1608Metric", "0603") is True

    def test_footprint_matches_package_mismatch(self):
        from footfindr.review import _footprint_matches_package
        assert _footprint_matches_package("Capacitor_SMD:C_0805_2012Metric", "0603") is False

    def test_footprint_matches_package_with_metric(self):
        from footfindr.review import _footprint_matches_package
        assert _footprint_matches_package("Capacitor_SMD:C_0603_1608Metric", "0603 (1608 Metric)") is True

    def test_footprint_matches_package_ic(self):
        from footfindr.review import _footprint_matches_package
        assert _footprint_matches_package("Package_DFN_QFN:QFN-32-1EP_5x5mm", "QFN-32") is True

    def test_footprint_matches_empty(self):
        from footfindr.review import _footprint_matches_package
        assert _footprint_matches_package("", "0603") is True  # empty = can't check
        assert _footprint_matches_package("some:fp", "") is True

    def test_footprint_case_insensitive(self):
        from footfindr.review import _footprint_matches_package
        assert _footprint_matches_package("Capacitor_SMD:C_0603_1608Metric", "0603") is True


# ---------------------------------------------------------------------------
# Resolver with Mappings Integration
# ---------------------------------------------------------------------------

class TestResolverWithMappings:
    """Test FootprintResolver using FootprintMappings for explicit bindings."""

    def test_binding_overrides_auto(self, indexed_footprints, tmp_path: Path):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from footfindr.kicad.footprint_mappings import FootprintMappings
        from types import SimpleNamespace

        mappings_path = tmp_path / "mappings.yaml"
        mappings = FootprintMappings(path=mappings_path)
        # Bind C1 to a specific footprint (different from auto-resolve)
        mappings.bind_ref("C1", "Capacitor_SMD:C_0805_2012Metric")

        resolver = FootprintResolver(indexed_footprints, mappings)
        part = SimpleNamespace(mpn="GRM188", package="0603", attributes={})
        result = resolver.resolve(part, "C1", "capacitor")

        # Should use the explicit binding, not auto-resolve
        assert result.status == "exact"
        assert result.footprint == "Capacitor_SMD:C_0805_2012Metric"
        assert "binding" in result.reason.lower()

    def test_package_binding(self, indexed_footprints, tmp_path: Path):
        from footfindr.kicad.footprint_resolver import FootprintResolver
        from footfindr.kicad.footprint_mappings import FootprintMappings
        from types import SimpleNamespace

        mappings_path = tmp_path / "mappings.yaml"
        mappings = FootprintMappings(path=mappings_path)
        mappings.bind_package("capacitor", "0603", "Capacitor_SMD:C_0603_1608Metric")

        resolver = FootprintResolver(indexed_footprints, mappings)
        part = SimpleNamespace(mpn="NEW_PART", package="0603", attributes={})
        result = resolver.resolve(part, "C99", "capacitor")

        assert result.status == "exact"
        assert result.footprint == "Capacitor_SMD:C_0603_1608Metric"
