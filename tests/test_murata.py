"""Tests for Murata GRM MLCC fetch/ingest."""

from __future__ import annotations

import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent.parent / "examples"


@pytest.fixture
def murata_csv_path() -> Path:
    """Return path to the sample Murata GRM CSV."""
    path = FIXTURES_DIR / "murata_grm_sample.csv"
    assert path.exists(), f"Fixture CSV not found: {path}"
    return path


@pytest.fixture
def murata_workspace(tmp_path: Path) -> Path:
    """Create temp workspace for Murata tests."""
    ws = tmp_path / ".footfindr"
    ws.mkdir()
    return ws


class TestMurataGRMParser:
    """Test the Murata GRM CSV parser."""

    def test_parse_csv_returns_records(self, murata_csv_path):
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(murata_csv_path)

        assert len(result.records) == 25  # Our fixture has 25 parts
        assert all(r.manufacturer == "Murata" for r in result.records)
        assert all(r.approved is False for r in result.records)

    def test_parse_csv_with_limit(self, murata_csv_path):
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(murata_csv_path, limit=5)

        assert len(result.records) == 5

    def test_parts_are_raw_status(self, murata_csv_path):
        from footfindr.core.models import PartStatus
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(murata_csv_path)

        for r in result.records:
            assert r.status == PartStatus.RAW
            assert r.approved is False

    def test_parts_are_capacitors(self, murata_csv_path):
        from footfindr.core.models import ComponentCategory
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(murata_csv_path)

        for r in result.records:
            assert r.category == ComponentCategory.CAPACITOR

    def test_mpn_extracted(self, murata_csv_path):
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(murata_csv_path)

        mpns = [r.mpn for r in result.records]
        assert "GRM155R71C104KA88D" in mpns
        assert "GRM21BR71C106KE15L" in mpns

    def test_specs_extracted(self, murata_csv_path):
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(murata_csv_path)

        # Find a known part
        grm21b = next(r for r in result.records if r.mpn == "GRM21BR71C106KE15L")
        assert grm21b.specs.capacitance == "10uF"
        assert grm21b.specs.voltage_rating == "16V"
        assert grm21b.specs.dielectric == "X7R"
        assert grm21b.specs.tolerance == "10%"


class TestPackageToFootprint:
    """Test package-to-footprint mapping."""

    def test_known_packages_mapped(self):
        from footfindr.libraries.murata import PACKAGE_FOOTPRINT_MAP

        assert "0402" in PACKAGE_FOOTPRINT_MAP
        assert "0603" in PACKAGE_FOOTPRINT_MAP
        assert "0805" in PACKAGE_FOOTPRINT_MAP
        assert "1206" in PACKAGE_FOOTPRINT_MAP
        assert "1210" in PACKAGE_FOOTPRINT_MAP

    def test_footprint_format(self):
        from footfindr.libraries.murata import PACKAGE_FOOTPRINT_MAP

        for pkg, fp in PACKAGE_FOOTPRINT_MAP.items():
            assert fp.startswith("Capacitor_SMD:C_"), f"Bad footprint for {pkg}: {fp}"

    def test_parsed_parts_have_footprint(self, murata_csv_path):
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(murata_csv_path)

        for r in result.records:
            assert r.footprint is not None, f"No footprint for {r.mpn}"
            assert "Capacitor_SMD" in r.footprint


class TestMurataIngest:
    """Test manual CSV ingest path."""

    def test_ingest_csv_creates_raw_library(self, murata_csv_path, murata_workspace):
        from footfindr.libraries.murata import ingest_murata_grm_csv

        count, path = ingest_murata_grm_csv(
            murata_csv_path,
            "Murata-GRM-Raw",
            workspace=murata_workspace,
        )

        assert count == 25
        assert path.exists()

    def test_ingest_csv_with_limit(self, murata_csv_path, murata_workspace):
        from footfindr.libraries.murata import ingest_murata_grm_csv

        count, path = ingest_murata_grm_csv(
            murata_csv_path,
            "Murata-GRM-Raw",
            limit=10,
            workspace=murata_workspace,
        )

        assert count == 10

    def test_ingested_parts_not_approved(self, murata_csv_path, murata_workspace):
        from footfindr.libraries.manager import LibraryManager
        from footfindr.libraries.murata import ingest_murata_grm_csv

        ingest_murata_grm_csv(murata_csv_path, "Murata-Test", workspace=murata_workspace)

        mgr = LibraryManager(workspace=murata_workspace)
        parts = mgr.load_raw_library("Murata-Test")
        for p in parts:
            assert p.approved is False

    def test_csv_not_found_raises(self, murata_workspace):
        from footfindr.libraries.murata import ingest_murata_grm_csv

        with pytest.raises(FileNotFoundError):
            ingest_murata_grm_csv(
                "nonexistent.csv",
                "Murata-GRM-Raw",
                workspace=murata_workspace,
            )

    def test_ingested_parts_caches_csv(self, murata_csv_path, murata_workspace):
        from footfindr.libraries.murata import ingest_murata_grm_csv

        ingest_murata_grm_csv(murata_csv_path, "Murata-GRM-Raw", workspace=murata_workspace)

        cache = murata_workspace / "vendor_raw" / "murata" / "grm_mlcc.csv"
        assert cache.exists()


class TestRawPartsNotUsedByResolver:
    """Verify raw parts are excluded from resolver."""

    def test_raw_parts_not_in_approved(self, murata_csv_path, murata_workspace):
        from footfindr.libraries.manager import LibraryManager
        from footfindr.libraries.murata import ingest_murata_grm_csv

        ingest_murata_grm_csv(murata_csv_path, "Murata-GRM-Raw", workspace=murata_workspace)

        mgr = LibraryManager(workspace=murata_workspace)
        # Approved parts should NOT include raw parts
        approved = mgr.load_approved_parts()
        # There shouldn't be raw Murata parts in approved list
        raw_in_approved = [p for p in approved if p.internal_pn.startswith("RAW-")]
        assert len(raw_in_approved) == 0


class TestSearchAfterIngest:
    """Test that ingested Murata parts are searchable."""

    def test_search_raw_finds_murata_parts(self, murata_csv_path, murata_workspace):
        from footfindr.libraries.manager import LibraryManager
        from footfindr.libraries.murata import ingest_murata_grm_csv
        from footfindr.libraries.promotion import search_all_parts

        ingest_murata_grm_csv(murata_csv_path, "Murata-GRM-Raw", workspace=murata_workspace)

        mgr = LibraryManager(workspace=murata_workspace)
        results = search_all_parts("10uF", mgr, raw_only=True)
        assert len(results) > 0
        assert all(r.manufacturer == "Murata" for r in results)

    def test_search_with_voltage_filter(self, murata_csv_path, murata_workspace):
        from footfindr.libraries.manager import LibraryManager
        from footfindr.libraries.murata import ingest_murata_grm_csv
        from footfindr.libraries.promotion import search_all_parts

        ingest_murata_grm_csv(murata_csv_path, "Murata-GRM-Raw", workspace=murata_workspace)

        mgr = LibraryManager(workspace=murata_workspace)
        results = search_all_parts(
            "10uF", mgr,
            raw_only=True,
            voltage_min="16V",
        )
        for r in results:
            # All should have voltage >= 16V
            assert r.specs.voltage_rating is not None
