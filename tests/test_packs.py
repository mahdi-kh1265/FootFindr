"""Tests for vendor library pack infrastructure."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml


FIXTURES_DIR = Path(__file__).parent.parent / "examples"


# ---------------------------------------------------------------------------
# Murata parser tests (real CSV format)
# ---------------------------------------------------------------------------


class TestMurataParserRealFormat:
    """Test Murata parser against real CSV column names."""

    def test_voltage_normalization(self) -> None:
        from footfindr.libraries.murata import _normalize_voltage

        assert _normalize_voltage("16Vdc") == "16V"
        assert _normalize_voltage("6.3Vdc") == "6.3V"
        assert _normalize_voltage("1,000Vdc") == "1000V"
        assert _normalize_voltage("100Vdc") == "100V"
        assert _normalize_voltage("2.5Vdc") == "2.5V"
        assert _normalize_voltage("3,150Vdc") == "3150V"
        assert _normalize_voltage("") == ""

    def test_tolerance_normalization(self) -> None:
        from footfindr.libraries.murata import _normalize_tolerance

        assert _normalize_tolerance("±10%") == "10%"
        assert _normalize_tolerance("±0.5pF") == "0.5pF"
        assert _normalize_tolerance("±2%") == "2%"
        assert _normalize_tolerance("±0.25pF") == "0.25pF"
        assert _normalize_tolerance("") == ""

    def test_capacitance_normalization(self) -> None:
        from footfindr.libraries.murata import _normalize_capacitance

        assert _normalize_capacitance("100pF") == "100pF"
        assert _normalize_capacitance("10µF") == "10uF"
        assert _normalize_capacitance("4.7μF") == "4.7uF"

    def test_mpn_size_code_extraction(self) -> None:
        from footfindr.libraries.murata import _extract_package_from_mpn

        assert _extract_package_from_mpn("GRM155R71C104KA88D") == "0402"
        assert _extract_package_from_mpn("GRM188R71C104KA01D") == "0603"
        assert _extract_package_from_mpn("GRM21BR71C106KE15L") == "0805"
        assert _extract_package_from_mpn("GRM31CR71E106KA12L") == "1206"
        assert _extract_package_from_mpn("GRM32ER71C226ME15L") == "1210"
        assert _extract_package_from_mpn("GRM0115C1C100GE01") == "008004"

    def test_mpn_all_size_codes_mapped(self) -> None:
        """All 37 Murata size codes should have EIA mappings."""
        from footfindr.libraries.murata import MURATA_SIZE_CODE_TO_EIA

        assert len(MURATA_SIZE_CODE_TO_EIA) >= 37

    def test_parse_sample_fixture(self) -> None:
        """Sample fixture (old column names) should still parse."""
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(FIXTURES_DIR / "murata_grm_sample.csv")
        assert len(result.records) == 25
        assert all(r.status.value == "raw" for r in result.records)
        assert all(not r.approved for r in result.records)
        assert all(r.category.value == "capacitor" for r in result.records)

    def test_parse_sample_fixture_packages(self) -> None:
        """Sample fixture uses 'Size' column for package."""
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(FIXTURES_DIR / "murata_grm_sample.csv")
        packages = {r.package for r in result.records if r.package}
        assert "0402" in packages
        assert "0603" in packages
        assert "0805" in packages

    def test_provenance_fields_populated(self) -> None:
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(
            FIXTURES_DIR / "murata_grm_sample.csv",
            source_file="sample.csv",
            source_pack="test-pack",
        )
        r = result.records[0]
        assert r.source_vendor == "Murata"
        assert r.source_series == "GRM"
        assert r.source_pack == "test-pack"
        assert r.source_file == "sample.csv"
        assert r.source_row is not None
        assert r.source_library == "Murata-GRM"

    def test_stats_populated(self) -> None:
        from footfindr.libraries.murata import MurataGRMParser

        parser = MurataGRMParser()
        result = parser.parse(FIXTURES_DIR / "murata_grm_sample.csv")
        assert result.raw_rows == 25
        assert result.imported_parts == 25
        assert result.skipped_rows == 0
        assert len(result.package_counts) > 0
        assert len(result.voltage_counts) > 0


# ---------------------------------------------------------------------------
# Pack build tests
# ---------------------------------------------------------------------------


class TestPackBuild:
    """Test building vendor packs."""

    def test_build_fixture_pack(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        meta, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "test-pack",
            source_type="fixture",
            real_source=False,
        )

        assert meta.pack_name == "footfindr-lib-murata-grm"
        assert meta.vendor == "Murata"
        assert meta.series == "GRM"
        assert meta.source.source_type == "fixture"
        assert meta.source.real_source is False
        assert meta.source.is_complete_catalog is False
        assert meta.counts.imported_parts == 25
        assert meta.counts.skipped_rows == 0

    def test_build_real_source_pack(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        meta, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "real-pack",
            source_type="manual_csv",
            real_source=True,
        )

        assert meta.source.source_type == "manual_csv"
        assert meta.source.real_source is True
        assert meta.source.is_complete_catalog is True

    def test_pack_directory_structure(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "struct-pack",
            source_type="fixture",
        )

        assert (pack_dir / "footfindr_pack.yaml").exists()
        assert (pack_dir / "README.md").exists()
        assert (pack_dir / "LICENSE_NOTES.md").exists()
        assert (pack_dir / "source").is_dir()
        assert (pack_dir / "normalized" / "parts.yaml").exists()
        assert (pack_dir / "normalized" / "parts.jsonl").exists()
        assert (pack_dir / "manifests" / "normalization_report.yaml").exists()

    def test_pack_readme_fixture_warning(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "readme-pack",
            source_type="fixture",
        )

        readme = (pack_dir / "README.md").read_text(encoding="utf-8")
        assert "NOT a complete vendor catalog" in readme

    def test_normalization_report_contents(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "report-pack",
            source_type="fixture",
        )

        with open(pack_dir / "manifests" / "normalization_report.yaml") as f:
            report = yaml.safe_load(f)

        assert report["raw_rows"] == 25
        assert report["imported_parts"] == 25
        assert "package_counts" in report
        assert "voltage_counts" in report
        assert "dielectric_counts" in report

    def test_pack_has_sha256_hashes(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        meta, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "hash-pack",
            source_type="fixture",
        )

        # Source hash
        assert meta.source.source_sha256 is not None
        assert len(meta.source.source_sha256) == 64  # SHA256 hex

        # File hashes
        assert meta.hashes.source_csv is not None
        assert meta.hashes.normalized_yaml is not None
        assert meta.hashes.normalized_jsonl is not None
        assert len(meta.hashes.source_csv) == 64
        assert len(meta.hashes.normalized_yaml) == 64
        assert meta.hashes.source_csv == meta.source.source_sha256

    def test_pack_hashes_in_manifest(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "manifest-hash-pack",
            source_type="fixture",
        )

        with open(pack_dir / "footfindr_pack.yaml") as f:
            manifest = yaml.safe_load(f)

        assert "hashes" in manifest
        assert manifest["hashes"]["source_csv"] is not None
        assert manifest["hashes"]["normalized_yaml"] is not None
        assert manifest["hashes"]["normalized_jsonl"] is not None
        assert manifest["source"]["source_sha256"] is not None

    def test_pack_parser_metadata(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        meta, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "parser-meta-pack",
            source_type="fixture",
        )

        assert meta.parser.name == "MurataGRMParser"
        assert meta.parser.slug == "murata-grm"
        assert meta.parser.version == "1.0.0"

        # Also in YAML
        with open(pack_dir / "footfindr_pack.yaml") as f:
            manifest = yaml.safe_load(f)

        assert manifest["parser"]["name"] == "MurataGRMParser"
        assert manifest["parser"]["slug"] == "murata-grm"

    def test_hash_reproducibility(self, tmp_path: Path) -> None:
        """Same source CSV should produce the same source hash."""
        from footfindr.libraries.packs import build_pack

        meta1, _ = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "repro-1",
            source_type="fixture",
        )
        meta2, _ = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "repro-2",
            source_type="fixture",
        )

        assert meta1.source.source_sha256 == meta2.source.source_sha256
        assert meta1.hashes.source_csv == meta2.hashes.source_csv

    def test_normalization_report_has_sha256(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "report-sha-pack",
            source_type="fixture",
        )

        with open(pack_dir / "manifests" / "normalization_report.yaml") as f:
            report = yaml.safe_load(f)

        assert "source_sha256" in report
        assert len(report["source_sha256"]) == 64
        assert report["parser_name"] == "MurataGRMParser"
        assert report["parser_slug"] == "murata-grm"


# ---------------------------------------------------------------------------
# Pack validate tests
# ---------------------------------------------------------------------------


class TestPackValidate:

    def test_valid_pack(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack, validate_pack

        build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "valid-pack",
            source_type="fixture",
        )

        issues = validate_pack(tmp_path / "valid-pack")
        critical = [i for i in issues if "non-critical" not in i]
        assert len(critical) == 0

    def test_missing_manifest(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import validate_pack

        (tmp_path / "bad-pack").mkdir()
        issues = validate_pack(tmp_path / "bad-pack")
        assert any("Missing footfindr_pack.yaml" in i for i in issues)

    def test_missing_parts(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import validate_pack

        pack_dir = tmp_path / "no-parts-pack"
        pack_dir.mkdir()
        manifest = {
            "pack_name": "test",
            "display_name": "Test",
            "vendor": "Test",
        }
        with open(pack_dir / "footfindr_pack.yaml", "w") as f:
            yaml.dump(manifest, f)

        issues = validate_pack(pack_dir)
        assert any("Missing normalized/parts.yaml" in i for i in issues)

    def test_nonexistent_directory(self) -> None:
        from footfindr.libraries.packs import validate_pack

        issues = validate_pack("/nonexistent/path")
        assert len(issues) > 0


# ---------------------------------------------------------------------------
# Pack install / uninstall tests
# ---------------------------------------------------------------------------


class TestPackInstall:

    def test_install_pack(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack, install_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "install-pack",
            source_type="fixture",
        )

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        meta = install_pack(pack_dir, workspace=ws)

        assert meta.counts.imported_parts == 25
        assert (ws / "vendor_packs" / "footfindr-lib-murata-grm").exists()
        assert (ws / "vendor_packs" / "registry.yaml").exists()

    def test_installed_pack_searchable(self, tmp_path: Path) -> None:
        """Installed raw pack parts should be searchable."""
        from footfindr.libraries.packs import build_pack, install_pack
        from footfindr.libraries.promotion import search_all_parts
        from footfindr.libraries.manager import LibraryManager

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "search-pack",
            source_type="fixture",
        )

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        install_pack(pack_dir, workspace=ws)

        mgr = LibraryManager(workspace=ws)
        results = search_all_parts("100nF", mgr, raw_only=True)
        assert len(results) > 0
        assert all(not r.approved for r in results)

    def test_raw_pack_parts_not_in_resolver(self, tmp_path: Path) -> None:
        """Raw vendor pack parts must NOT be used by the resolver."""
        from footfindr.libraries.packs import build_pack, install_pack
        from footfindr.libraries.manager import LibraryManager

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "resolver-pack",
            source_type="fixture",
        )

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        install_pack(pack_dir, workspace=ws)

        # The resolver should only use approved parts
        mgr = LibraryManager(workspace=ws)
        approved = mgr.load_approved_parts()
        for part in approved:
            assert part.approved is True, f"Non-approved part in approved list: {part.internal_pn}"

    def test_uninstall_pack(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack, install_pack, uninstall_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "uninstall-pack",
            source_type="fixture",
        )

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        meta = install_pack(pack_dir, workspace=ws)

        lib_name = meta.display_name.replace(" ", "-")
        result = uninstall_pack(lib_name, workspace=ws)
        assert result is True
        assert not (ws / "vendor_packs" / "footfindr-lib-murata-grm").exists()

    def test_list_installed_packs(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import (
            build_pack, install_pack, list_installed_packs,
        )

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "list-pack",
            source_type="fixture",
        )

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        install_pack(pack_dir, workspace=ws)

        packs = list_installed_packs(workspace=ws)
        assert len(packs) == 1
        assert packs[0]["vendor"] == "Murata"

    def test_info_installed_pack(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack, install_pack, info_pack

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "info-pack",
            source_type="fixture",
        )

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        meta = install_pack(pack_dir, workspace=ws)
        lib_name = meta.display_name.replace(" ", "-")

        info = info_pack(lib_name, workspace=ws)
        assert info["vendor"] == "Murata"
        assert info["counts"]["imported_parts"] == 25
        assert any("fixture" in w.lower() or "sample" in w.lower()
                   for w in info.get("warnings", []))


# ---------------------------------------------------------------------------
# Promotion provenance tests
# ---------------------------------------------------------------------------


class TestPromotionProvenance:
    """Test that promotion preserves provenance from vendor packs."""

    def test_promote_preserves_provenance(self, tmp_path: Path) -> None:
        from footfindr.libraries.packs import build_pack, install_pack
        from footfindr.libraries.promotion import promote_part
        from footfindr.libraries.manager import LibraryManager

        _, pack_dir = build_pack(
            "murata-grm",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "promote-pack",
            source_type="fixture",
        )

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        install_pack(pack_dir, workspace=ws)

        mgr = LibraryManager(workspace=ws)

        # Create the target approved library
        mgr.create_library("POSM", "approved")

        # Get an MPN from the installed pack
        from footfindr.libraries.promotion import search_all_parts
        results = search_all_parts("100nF", mgr, raw_only=True)
        assert len(results) > 0
        mpn = results[0].mpn

        # Promote
        promoted = promote_part(
            mpn, "POSM", mgr,
            internal_pn="CAP-100N-16V-X7R-0402",
        )

        assert promoted.approved is True
        assert promoted.status.value == "approved"
        assert promoted.source_vendor == "Murata"
        assert promoted.source_series == "GRM"
        assert promoted.promoted_at is not None
        assert promoted.promoted_from is not None


# ---------------------------------------------------------------------------
# Provenance round-trip test
# ---------------------------------------------------------------------------


class TestProvenanceRoundTrip:
    """Test that provenance fields survive YAML round-trips."""

    def test_provenance_yaml_roundtrip(self) -> None:
        from footfindr.core.models import (
            PartRecord, ComponentCategory, PartStatus, ElectricalSpecs,
        )
        from footfindr.libraries.manager import LibraryManager

        original = PartRecord(
            internal_pn="TEST-1",
            category=ComponentCategory.CAPACITOR,
            manufacturer="TestCorp",
            mpn="TEST-MPN-123",
            value="100nF",
            status=PartStatus.APPROVED,
            approved=True,
            source_library="POSM",
            source_vendor="TestCorp",
            source_series="TST",
            source_pack="test-pack",
            source_file="test.csv",
            source_row=42,
            promoted_at="2024-01-01T00:00:00Z",
            promoted_from="TestLib-Raw",
        )

        # Serialize
        d = LibraryManager._record_to_dict(original)
        assert d["source_vendor"] == "TestCorp"
        assert d["source_series"] == "TST"
        assert d["source_pack"] == "test-pack"
        assert d["source_file"] == "test.csv"
        assert d["source_row"] == 42
        assert d["promoted_at"] == "2024-01-01T00:00:00Z"
        assert d["promoted_from"] == "TestLib-Raw"

        # Deserialize
        from footfindr.libraries.models import ApprovedPartSchema
        schema = ApprovedPartSchema(**d)
        record = LibraryManager._schema_to_record(schema)

        assert record.source_vendor == "TestCorp"
        assert record.source_series == "TST"
        assert record.source_pack == "test-pack"
        assert record.source_file == "test.csv"
        assert record.source_row == 42
        assert record.promoted_at == "2024-01-01T00:00:00Z"
        assert record.promoted_from == "TestLib-Raw"


# ---------------------------------------------------------------------------
# Parser registry tests
# ---------------------------------------------------------------------------


class TestParserRegistry:
    """Test vendor parser registry."""

    def test_list_parsers(self) -> None:
        from footfindr.libraries.vendor_parsers import list_parsers

        parsers = list_parsers()
        assert "murata-grm" in parsers
        assert "generic" in parsers
        assert "generic-csv" in parsers

    def test_get_murata_parser(self) -> None:
        from footfindr.libraries.vendor_parsers import get_parser

        parser = get_parser("murata-grm")
        assert parser.vendor == "Murata"
        assert parser.series == "GRM"
        assert parser.category == "capacitor"

    def test_get_generic_parser(self) -> None:
        from footfindr.libraries.vendor_parsers import get_parser

        parser = get_parser("generic")
        assert parser.vendor == "Generic"

    def test_unknown_parser_raises(self) -> None:
        from footfindr.libraries.vendor_parsers import get_parser

        with pytest.raises(ValueError, match="Unknown vendor type"):
            get_parser("nonexistent-vendor")

    def test_parser_conforms_to_protocol(self) -> None:
        from footfindr.libraries.vendor_parsers import get_parser
        from footfindr.libraries.vendor_parsers.base import VendorParser

        for slug in ("murata-grm", "generic"):
            parser = get_parser(slug)
            assert isinstance(parser, VendorParser)
            assert hasattr(parser, "vendor")
            assert hasattr(parser, "series")
            assert hasattr(parser, "category")
            assert hasattr(parser, "display_name")
            assert hasattr(parser, "pack_slug")
            assert hasattr(parser, "parse")


# ---------------------------------------------------------------------------
# Generic CSV parser tests
# ---------------------------------------------------------------------------


class TestGenericCSVParser:
    """Test the generic CSV parser."""

    def test_parse_murata_sample_as_generic(self) -> None:
        """Generic parser should handle the Murata fixture too."""
        from footfindr.libraries.vendor_parsers.generic_csv import GenericCSVParser

        parser = GenericCSVParser()
        result = parser.parse(FIXTURES_DIR / "murata_grm_sample.csv")
        assert result.imported_parts == 25
        assert result.skipped_rows == 0
        assert all(not r.approved for r in result.records)

    def test_generic_pack_build(self, tmp_path: Path) -> None:
        """Build a pack using the generic parser."""
        from footfindr.libraries.packs import build_pack

        meta, pack_dir = build_pack(
            "generic",
            FIXTURES_DIR / "murata_grm_sample.csv",
            tmp_path / "generic-pack",
            source_type="fixture",
        )

        assert meta.vendor == "Generic"
        assert meta.counts.imported_parts == 25
        assert (pack_dir / "footfindr_pack.yaml").exists()
        assert (pack_dir / "normalized" / "parts.yaml").exists()

    def test_generic_result_has_correct_type(self) -> None:
        from footfindr.libraries.vendor_parsers.generic_csv import GenericCSVParser
        from footfindr.libraries.vendor_parsers.base import VendorParseResult

        parser = GenericCSVParser()
        result = parser.parse(FIXTURES_DIR / "murata_grm_sample.csv")
        assert isinstance(result, VendorParseResult)
        assert result.parser_version == "1.0.0"
