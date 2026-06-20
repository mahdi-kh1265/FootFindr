"""Tests for SQLite part index.

Verifies index build, parametric search, rebuild, removal, and info.
Confirms that the index is an acceleration layer and does not modify
source YAML/pack files.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from footfindr.core.models import (
    ComponentCategory,
    ElectricalSpecs,
    PartRecord,
    PartStatus,
)
from footfindr.db.index import PartIndex


def _make_parts() -> list[PartRecord]:
    """Create test parts."""
    return [
        PartRecord(
            internal_pn="CAP-100N-16V-X7R-0402",
            category=ComponentCategory.CAPACITOR,
            value="100nF",
            manufacturer="Murata",
            mpn="GRM155R71C104KA88",
            package="0402",
            specs=ElectricalSpecs(
                capacitance="100nF",
                voltage_rating="16V",
                dielectric="X7R",
                tolerance="±10%",
            ),
            status=PartStatus.RAW,
            approved=False,
        ),
        PartRecord(
            internal_pn="CAP-10U-16V-X7R-0805",
            category=ComponentCategory.CAPACITOR,
            value="10µF",
            manufacturer="Murata",
            mpn="GRM21BB31C106KE15",
            package="0805",
            specs=ElectricalSpecs(
                capacitance="10µF",
                voltage_rating="16V",
                dielectric="X7R",
            ),
            status=PartStatus.RAW,
            approved=False,
        ),
        PartRecord(
            internal_pn="CAP-100P-50V-C0G-0402",
            category=ComponentCategory.CAPACITOR,
            value="100pF",
            manufacturer="Murata",
            mpn="GRM1555C1H101JA01",
            package="0402",
            specs=ElectricalSpecs(
                capacitance="100pF",
                voltage_rating="50V",
                dielectric="C0G",
            ),
            status=PartStatus.RAW,
            approved=False,
        ),
        PartRecord(
            internal_pn="RES-10K-0402",
            category=ComponentCategory.RESISTOR,
            value="10k",
            manufacturer="Yageo",
            mpn="RC0402FR-0710KL",
            package="0402",
            specs=ElectricalSpecs(
                resistance="10k",
            ),
            status=PartStatus.RAW,
            approved=False,
        ),
        PartRecord(
            internal_pn="RES-4K7-0402",
            category=ComponentCategory.RESISTOR,
            value="4k7",
            manufacturer="Yageo",
            mpn="RC0402FR-074K7L",
            package="0402",
            specs=ElectricalSpecs(
                resistance="4k7",
            ),
            status=PartStatus.RAW,
            approved=False,
        ),
    ]


class TestPartIndex:
    def test_add_and_search(self, tmp_path: Path):
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        parts = _make_parts()
        count = idx.add_library("test-lib", parts)
        assert count == 5

        # Search by capacitance
        results = idx.search("100n", category="capacitor")
        assert len(results) >= 1
        assert any(r.internal_pn == "CAP-100N-16V-X7R-0402" for r in results)

        idx.close()

    def test_search_equivalence(self, tmp_path: Path):
        """100n and 0.1u should return the same results."""
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        idx.add_library("test-lib", _make_parts())

        r1 = idx.search("100n", category="capacitor")
        r2 = idx.search("0.1u", category="capacitor")

        ipns1 = {r.internal_pn for r in r1}
        ipns2 = {r.internal_pn for r in r2}
        assert ipns1 == ipns2

        idx.close()

    def test_search_resistance(self, tmp_path: Path):
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        idx.add_library("test-lib", _make_parts())

        r1 = idx.search("4k7", category="resistor")
        r2 = idx.search("4700", category="resistor")
        r3 = idx.search("4.7k", category="resistor")

        ipns1 = {r.internal_pn for r in r1}
        ipns2 = {r.internal_pn for r in r2}
        ipns3 = {r.internal_pn for r in r3}
        assert ipns1 == ipns2 == ipns3
        assert "RES-4K7-0402" in ipns1

        idx.close()

    def test_search_with_filters(self, tmp_path: Path):
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        idx.add_library("test-lib", _make_parts())

        # Search with voltage min
        results = idx.search("10u", category="capacitor", voltage_min="16V")
        assert len(results) >= 1

        # Search with dielectric
        results = idx.search("100p", category="capacitor", dielectric="C0G")
        assert len(results) >= 1
        assert results[0].internal_pn == "CAP-100P-50V-C0G-0402"

        idx.close()

    def test_search_dielectric_c0g_np0(self, tmp_path: Path):
        """c0g should match C0G (case-insensitive)."""
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        idx.add_library("test-lib", _make_parts())

        results = idx.search("100p", category="capacitor", dielectric="c0g")
        assert len(results) >= 1

        idx.close()

    def test_remove_library(self, tmp_path: Path):
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        idx.add_library("test-lib", _make_parts())
        assert idx.has_library("test-lib")

        removed = idx.remove_library("test-lib")
        assert removed == 5
        assert not idx.has_library("test-lib")

        idx.close()

    def test_info(self, tmp_path: Path):
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        idx.add_library("test-lib", _make_parts())

        info = idx.info()
        assert info.total_parts == 5
        assert "test-lib" in info.libraries
        assert info.schema_version == "1"

        idx.close()

    def test_has_any_parts(self, tmp_path: Path):
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        assert not idx.has_any_parts()

        idx.add_library("test-lib", _make_parts())
        assert idx.has_any_parts()

        idx.close()

    def test_index_does_not_modify_source(self, tmp_path: Path):
        """Rebuilding the index must not create or modify source files."""
        ws = tmp_path / ".footfindr"
        ws.mkdir()

        # Create a simple library YAML file
        raw_dir = ws / "raw"
        raw_dir.mkdir()
        lib_yaml = raw_dir / "test.yaml"
        lib_yaml.write_text("[]")
        original_content = lib_yaml.read_text()

        idx = PartIndex(workspace=ws)
        idx.add_library("test-lib", _make_parts())
        idx.close()

        # Verify source was not modified
        assert lib_yaml.read_text() == original_content

    def test_vendor_filter(self, tmp_path: Path):
        idx = PartIndex(workspace=tmp_path / ".footfindr")
        idx.add_library("test-lib", _make_parts())

        results = idx.search("", vendor="Murata")
        assert all(r.manufacturer and "Murata" in r.manufacturer for r in results)

        results = idx.search("", vendor="Yageo")
        assert all(r.manufacturer and "Yageo" in r.manufacturer for r in results)

        idx.close()
