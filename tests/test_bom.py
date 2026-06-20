"""Tests for BOM generation and profile management."""

from __future__ import annotations

import csv
import pytest
from pathlib import Path

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


@pytest.fixture
def bom_workspace(tmp_path: Path) -> Path:
    """Create temp workspace for BOM tests."""
    ws = tmp_path / ".footfindr"
    ws.mkdir()
    return ws


class TestBOMProfiles:
    """Test BOM profile loading and management."""

    def test_list_shipped_profiles(self):
        from footfindr.bom.profiles import list_profiles

        profiles = list_profiles()
        names = {p.name for p in profiles}
        assert "posm" in names
        assert "jlcpcb" in names
        assert "jlcpcb_minimal" in names

    def test_load_posm_profile(self):
        from footfindr.bom.profiles import load_profile

        prof = load_profile("posm")
        assert prof.name == "posm"
        assert len(prof.columns) == 13
        assert prof.group_by == "internal_pn"
        assert prof.exclude_dnp is True

        col_names = [c.name for c in prof.columns]
        assert "Quantity" in col_names
        assert "References" in col_names
        assert "InternalPN" in col_names
        assert "Footprint" in col_names
        assert "Dielectric" in col_names

    def test_load_jlcpcb_profile(self):
        from footfindr.bom.profiles import load_profile

        prof = load_profile("jlcpcb")
        assert prof.name == "jlcpcb"
        col_names = [c.name for c in prof.columns]
        assert "Designator" in col_names
        assert "LCSC Part #" in col_names
        assert "Quantity" in col_names
        assert "Value" in col_names

    def test_load_jlcpcb_minimal_profile(self):
        from footfindr.bom.profiles import load_profile

        prof = load_profile("jlcpcb_minimal")
        col_names = [c.name for c in prof.columns]
        assert "Comment" in col_names
        assert "Designator" in col_names
        assert "Footprint" in col_names

    def test_load_missing_profile_raises(self):
        from footfindr.bom.profiles import load_profile

        with pytest.raises(FileNotFoundError):
            load_profile("nonexistent_profile")

    def test_create_profile(self, bom_workspace):
        from footfindr.bom.profiles import create_profile, load_profile

        path = create_profile("custom-bom", from_profile="posm", workspace=bom_workspace)
        assert path.exists()

        prof = load_profile("custom-bom", workspace=bom_workspace)
        assert prof.name == "custom-bom"

    def test_create_duplicate_raises(self, bom_workspace):
        from footfindr.bom.profiles import create_profile

        create_profile("dup-test", workspace=bom_workspace)
        with pytest.raises(FileExistsError):
            create_profile("dup-test", workspace=bom_workspace)

    def test_validate_valid_profile(self):
        from footfindr.bom.profiles import validate_profile

        issues = validate_profile("posm")
        assert issues == []

    def test_validate_missing_profile(self):
        from footfindr.bom.profiles import validate_profile

        issues = validate_profile("nonexistent")
        assert len(issues) > 0


class TestBOMGeneration:
    """Test BOM generation from schematic."""

    def test_generate_bom_posm(self, simple_schematic_path):
        from footfindr.bom.generator import generate_bom

        report = generate_bom(simple_schematic_path, "posm")

        assert report.profile_name == "posm"
        assert report.total_parts > 0
        assert report.total_unique > 0
        assert len(report.rows) > 0

    def test_generate_bom_jlcpcb(self, simple_schematic_path):
        from footfindr.bom.generator import generate_bom

        report = generate_bom(simple_schematic_path, "jlcpcb")

        assert report.profile_name == "jlcpcb"
        assert report.total_parts > 0
        # JLCPCB should warn about missing LCSC Part #
        lcsc_warnings = [w for w in report.warnings if "LCSC" in w]
        assert len(lcsc_warnings) > 0

    def test_generate_bom_jlcpcb_minimal(self, simple_schematic_path):
        from footfindr.bom.generator import generate_bom

        report = generate_bom(simple_schematic_path, "jlcpcb_minimal")
        assert report.total_parts > 0

    def test_bom_rows_have_references(self, simple_schematic_path):
        from footfindr.bom.generator import generate_bom

        report = generate_bom(simple_schematic_path, "posm")

        for row in report.rows:
            assert len(row.references) > 0
            assert row.quantity == len(row.references)

    def test_export_csv_posm(self, simple_schematic_path, tmp_path):
        from footfindr.bom.generator import export_bom_csv, generate_bom

        report = generate_bom(simple_schematic_path, "posm")
        csv_path = tmp_path / "bom_posm.csv"
        result = export_bom_csv(report, csv_path, "posm")

        assert result.exists()

        # Read and verify CSV structure
        with open(result, "r", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            headers = next(reader)
            assert "Quantity" in headers
            assert "References" in headers
            assert "InternalPN" in headers
            assert "Footprint" in headers
            assert "Dielectric" in headers

            rows = list(reader)
            assert len(rows) > 0

    def test_export_csv_jlcpcb(self, simple_schematic_path, tmp_path):
        from footfindr.bom.generator import export_bom_csv, generate_bom

        report = generate_bom(simple_schematic_path, "jlcpcb")
        csv_path = tmp_path / "bom_jlcpcb.csv"
        result = export_bom_csv(report, csv_path, "jlcpcb")

        assert result.exists()

        with open(result, "r", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            headers = next(reader)
            assert "Designator" in headers
            assert "LCSC Part #" in headers
