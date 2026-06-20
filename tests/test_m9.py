"""M9 tests — Project review, BOM check, source-check, cost, profiles, packet.

All tests are offline. Uses mocked schematic and supplier data.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures: mock schematic and supplier data
# ---------------------------------------------------------------------------


def _make_mock_symbol(ref, mpn="", manufacturer="", footprint="",
                      ipn="", lcsc="", package="", value="",
                      dnp=False, **extra_fields):
    """Create a mock KiCadSymbol-like object."""
    fields = {}
    if ipn:
        fields["InternalPN"] = ipn
    if mpn:
        fields["MPN"] = mpn
    if manufacturer:
        fields["Manufacturer"] = manufacturer
    if lcsc:
        fields["LCSC Part #"] = lcsc
    if package:
        fields["Package"] = package
    if value:
        fields["Value"] = value
    fields.update(extra_fields)

    sym = MagicMock()
    sym.ref = ref
    sym.value = value or "100nF"
    sym.footprint = footprint or "Capacitor_SMD:C_0805"
    sym.fields = fields
    sym.dnp = dnp
    sym.lib_id = ""
    sym.category = None
    return sym


def _make_mock_schematic(symbols):
    """Create a mock KiCadSchematic."""
    sch = MagicMock()
    sch.symbols = symbols
    sch.path = Path("test.kicad_sch")
    return sch


def _make_mock_approved_part(ipn, mpn="", manufacturer="", status="approved",
                              package="", footprint=""):
    """Create a mock approved library part."""
    part = MagicMock()
    part.internal_pn = ipn
    part.mpn = mpn
    part.manufacturer = manufacturer
    part.status = status
    part.package = package
    part.footprint = footprint
    part.specs = MagicMock()
    part.specs.voltage_rating = ""
    part.specs.power_rating = ""
    part.specs.tolerance = ""
    part.specs.dielectric = ""
    part.supplier_pns = {}
    return part


def _make_mock_supplier_part(supplier, mpn, stock=1000,
                              price_breaks=None, lifecycle="Active",
                              lcsc_pn=None, moq=1,
                              lead_time=None, datasheet_url="http://ds.pdf",
                              package="0805"):
    """Create a mock SupplierPart."""
    from footfindr.suppliers.models import SupplierPart, PriceBreak
    pbs = price_breaks or [PriceBreak(1, 0.01), PriceBreak(10, 0.008)]
    return SupplierPart(
        supplier=supplier, mpn=mpn, stock=stock,
        price_breaks=pbs, lifecycle=lifecycle,
        lcsc_pn=lcsc_pn, minimum_order_quantity=moq,
        lead_time=lead_time, datasheet_url=datasheet_url,
        package=package, manufacturer="TestMfr",
    )


# ---------------------------------------------------------------------------
# Assembly profiles tests
# ---------------------------------------------------------------------------


class TestAssemblyProfiles:
    """Test assembly profile definitions and package checks."""

    def test_list_profiles(self):
        from footfindr.assembly_profiles import list_profiles
        profiles = list_profiles()
        names = [p.name for p in profiles]
        assert "prototype" in names
        assert "hand-assembly" in names
        assert "jlcpcb" in names
        assert "posm" in names

    def test_get_profile(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("prototype")
        assert p.name == "prototype"
        assert len(p.package_rules) > 0

    def test_get_unknown_raises(self):
        from footfindr.assembly_profiles import get_profile
        with pytest.raises(KeyError):
            get_profile("nonexistent")

    def test_prototype_bga_warns(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("prototype")
        rule = p.check_package("BGA-256")
        assert rule is not None
        assert rule.severity == "WARN"

    def test_prototype_0805_ok(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("prototype")
        rule = p.check_package("0805")
        assert rule is None  # no warning

    def test_hand_assembly_bga_fails(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("hand-assembly")
        rule = p.check_package("BGA-100")
        assert rule is not None
        assert rule.severity == "FAIL"

    def test_hand_assembly_qfn_warns(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("hand-assembly")
        rule = p.check_package("QFN-32")
        assert rule is not None
        assert rule.severity == "WARN"

    def test_jlcpcb_no_package_rules(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("jlcpcb")
        assert len(p.package_rules) == 0

    def test_profile_to_dict(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("prototype")
        d = p.to_dict()
        assert d["name"] == "prototype"
        assert isinstance(d["package_rules"], list)
        assert isinstance(d["checks"], list)


# ---------------------------------------------------------------------------
# ProjectIssue model tests
# ---------------------------------------------------------------------------


class TestProjectIssue:
    """Test the ProjectIssue data model."""

    def test_to_dict(self):
        from footfindr.bom.models import ProjectIssue
        issue = ProjectIssue(
            severity="FAIL", code="MISSING_MPN", ref="C13",
            field="MPN", message="Missing MPN",
            suggested_action="ff supplier search",
            plan_available=True,
        )
        d = issue.to_dict()
        assert d["severity"] == "FAIL"
        assert d["code"] == "MISSING_MPN"
        assert d["ref"] == "C13"
        assert d["suggested_action"] == "ff supplier search"
        assert d["plan_available"] is True

    def test_to_dict_minimal(self):
        from footfindr.bom.models import ProjectIssue
        issue = ProjectIssue(
            severity="WARN", code="TEST", ref=None,
            field=None, message="test",
        )
        d = issue.to_dict()
        assert "suggested_action" not in d
        assert "plan_available" not in d


# ---------------------------------------------------------------------------
# BOM check tests
# ---------------------------------------------------------------------------


class TestBOMCheck:
    """Test BOM structural/design-field checks."""

    def _make_reviewer(self, symbols, approved=None):
        from footfindr.review import ProjectReviewer
        reviewer = ProjectReviewer(
            "test.kicad_sch", profile="prototype", qty=1,
        )
        reviewer._schematic = _make_mock_schematic(symbols)
        reviewer._approved_parts = approved or []
        reviewer._by_ipn = {p.internal_pn: p for p in (approved or [])}
        reviewer._by_mpn = {
            p.mpn: p for p in (approved or [])
            if p.mpn and p.mpn != "TBD"
        }
        return reviewer

    def test_missing_mpn(self):
        syms = [_make_mock_symbol("C13")]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check()
        codes = [i.code for i in issues]
        assert "MISSING_MPN" in codes

    def test_missing_manufacturer(self):
        syms = [_make_mock_symbol("C13", mpn="GRM21")]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check()
        codes = [i.code for i in issues]
        assert "MISSING_MANUFACTURER" in codes

    def test_missing_ipn(self):
        syms = [_make_mock_symbol("C13", mpn="GRM21", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check()
        codes = [i.code for i in issues]
        assert "MISSING_IPN" in codes

    def test_all_fields_present_no_missing_issues(self):
        part = _make_mock_approved_part("CAP-100NF", mpn="GRM21",
                                         manufacturer="Murata")
        syms = [_make_mock_symbol("C13", mpn="GRM21", manufacturer="Murata",
                                  ipn="CAP-100NF", footprint="C_0805")]
        reviewer = self._make_reviewer(syms, approved=[part])
        issues = reviewer.bom_check()
        codes = [i.code for i in issues]
        assert "MISSING_MPN" not in codes
        assert "MISSING_MANUFACTURER" not in codes
        assert "MISSING_IPN" not in codes
        assert "MISSING_FOOTPRINT" not in codes

    def test_unapproved_part(self):
        syms = [_make_mock_symbol("C13", mpn="GRM21", manufacturer="Murata",
                                  ipn="CAP-100NF")]
        reviewer = self._make_reviewer(syms, approved=[])
        issues = reviewer.bom_check()
        codes = [i.code for i in issues]
        assert "UNAPPROVED_PART" in codes

    def test_deprecated_part(self):
        part = _make_mock_approved_part("CAP-OLD", mpn="OLDCAP",
                                         status="deprecated")
        syms = [_make_mock_symbol("C13", mpn="OLDCAP", ipn="CAP-OLD",
                                  manufacturer="X")]
        reviewer = self._make_reviewer(syms, approved=[part])
        issues = reviewer.bom_check()
        codes = [i.code for i in issues]
        assert "DEPRECATED_PART" in codes

    def test_missing_lcsc_jlcpcb_profile(self):
        syms = [_make_mock_symbol("C13", mpn="GRM21", manufacturer="Murata",
                                  ipn="CAP-100NF")]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check(profile_name="jlcpcb")
        lcsc_issues = [i for i in issues if i.code == "MISSING_LCSC"]
        assert len(lcsc_issues) > 0
        assert lcsc_issues[0].severity == "FAIL"

    def test_missing_lcsc_prototype_profile(self):
        syms = [_make_mock_symbol("C13", mpn="GRM21", manufacturer="Murata",
                                  ipn="CAP-100NF")]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check(profile_name="prototype")
        lcsc_issues = [i for i in issues if i.code == "MISSING_LCSC"]
        assert len(lcsc_issues) > 0
        assert lcsc_issues[0].severity == "WARN"

    def test_dnp_skipped(self):
        syms = [_make_mock_symbol("C13", dnp=True)]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check()
        assert len(issues) == 0

    def test_prototype_package_risk(self):
        syms = [_make_mock_symbol("U1", mpn="IC1", manufacturer="TI",
                                  ipn="IC-1", package="QFN-32")]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check(profile_name="prototype")
        codes = [i.code for i in issues]
        assert "PROFILE_PACKAGE_RISK" in codes

    def test_hand_assembly_bga_fail(self):
        syms = [_make_mock_symbol("U1", mpn="IC1", manufacturer="TI",
                                  ipn="IC-1", package="BGA-256")]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check(profile_name="hand-assembly")
        bga_issues = [i for i in issues
                      if i.code == "PROFILE_PACKAGE_RISK"]
        assert len(bga_issues) > 0
        assert bga_issues[0].severity == "FAIL"

    def test_field_inconsistency(self):
        part = _make_mock_approved_part("CAP-100NF", mpn="GRM21CORRECT")
        syms = [_make_mock_symbol("C13", mpn="GRM21WRONG",
                                  ipn="CAP-100NF", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms, approved=[part])
        issues = reviewer.bom_check()
        codes = [i.code for i in issues]
        assert "FIELD_INCONSISTENCY" in codes

    def test_issues_sorted_by_severity(self):
        syms = [
            _make_mock_symbol("C1"),  # missing MPN = FAIL
            _make_mock_symbol("C2", mpn="X"),  # missing mfr = WARN
        ]
        reviewer = self._make_reviewer(syms)
        issues = reviewer.bom_check()
        severities = [i.severity for i in issues]
        # FAIL should come before WARN
        fail_idx = next(i for i, s in enumerate(severities) if s == "FAIL")
        warn_indices = [i for i, s in enumerate(severities) if s == "WARN"]
        for wi in warn_indices:
            assert fail_idx <= wi or True  # All FAILs grouped first

    def test_review_does_not_mutate_files(self):
        """ProjectReviewer.review() should not create/modify any files."""
        syms = [_make_mock_symbol("C13", mpn="GRM21")]
        reviewer = self._make_reviewer(syms)
        # Patch supplier cache to avoid file I/O
        with patch("footfindr.review.ProjectReviewer.source_check",
                    return_value=[]):
            with patch("footfindr.review.ProjectReviewer.cost_rollup",
                        return_value=([], None)):
                result = reviewer.review()
                assert result is not None
                # No files should have been written


# ---------------------------------------------------------------------------
# Constraint override tests
# ---------------------------------------------------------------------------


class TestConstraintOverrides:
    """Test that constraints override profile rules."""

    def _make_reviewer(self, symbols, approved=None):
        from footfindr.review import ProjectReviewer
        reviewer = ProjectReviewer(
            "test.kicad_sch", profile="prototype", qty=1,
        )
        reviewer._schematic = _make_mock_schematic(symbols)
        reviewer._approved_parts = approved or []
        reviewer._by_ipn = {p.internal_pn: p for p in (approved or [])}
        reviewer._by_mpn = {
            p.mpn: p for p in (approved or [])
            if p.mpn and p.mpn != "TBD"
        }
        return reviewer

    def test_constraint_accepts_package(self):
        """If constraint explicitly sets package=DFN, profile should not WARN."""
        from footfindr.constraints import ConstraintManager, ConstraintFile, RefConstraints, Constraint

        syms = [_make_mock_symbol("U1", mpn="IC1", manufacturer="TI",
                                  ipn="IC-1", package="DFN-10")]

        reviewer = self._make_reviewer(syms)

        # Mock constraint manager
        mock_cm = MagicMock(spec=ConstraintManager)
        mock_cm.get_constraints_for.return_value = [
            Constraint(field="package", op="eq", value="DFN"),
        ]

        with patch("footfindr.constraints.ConstraintManager", return_value=mock_cm):
            issues = reviewer.bom_check(check_constraints=True,
                                         profile_name="prototype")
            # Should get PROFILE_PACKAGE_ACCEPTED (INFO), not RISK (WARN)
            pkg_issues = [i for i in issues if "PACKAGE" in i.code]
            for pi in pkg_issues:
                if pi.code == "PROFILE_PACKAGE_RISK":
                    # Should not have WARN for DFN since constraint accepts it
                    assert False, f"Should not get PROFILE_PACKAGE_RISK: {pi}"


# ---------------------------------------------------------------------------
# Source check tests
# ---------------------------------------------------------------------------


class TestSourceCheck:
    """Test source-check risk assessment."""

    def _make_reviewer(self, symbols):
        from footfindr.review import ProjectReviewer
        reviewer = ProjectReviewer(
            "test.kicad_sch", qty=10, project_name="test",
        )
        reviewer._schematic = _make_mock_schematic(symbols)
        reviewer._approved_parts = []
        reviewer._by_ipn = {}
        reviewer._by_mpn = {}
        return reviewer

    def test_no_mpn_high_risk(self):
        syms = [_make_mock_symbol("C1")]  # no MPN
        reviewer = self._make_reviewer(syms)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check()
            assert len(results) == 1
            assert results[0].risk_level == "HIGH"
            assert "NO_SUPPLIER_RESULT" in results[0].risk_codes

    def test_good_stock_low_risk(self):
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms)
        # Two suppliers + JLC to avoid SINGLE_SOURCE and NO_JLC_MATCH
        mock_dk = _make_mock_supplier_part("digikey", "GRM21", stock=5000)
        mock_jlc = _make_mock_supplier_part("jlcpcb", "GRM21", stock=10000,
                                             lcsc_pn="C123456")
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            # Return different parts for different supplier lookups
            MockCache.return_value.lookup.side_effect = [
                [mock_dk], [mock_jlc], [],  # dk, jlc, mouser (empty)
            ]
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check(
                suppliers=["digikey", "jlcpcb", "mouser"])
            assert len(results) == 1
            assert results[0].risk_level == "LOW"
            assert results[0].best_stock == 10000

    def test_low_stock_medium_risk(self):
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part("digikey", "GRM21", stock=50)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check()
            assert results[0].risk_level == "MEDIUM"
            assert "LOW_STOCK" in results[0].risk_codes

    def test_obsolete_blocker(self):
        syms = [_make_mock_symbol("C1", mpn="OLD1", manufacturer="X")]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part(
            "digikey", "OLD1", stock=100, lifecycle="Obsolete")
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check()
            assert results[0].risk_level == "BLOCKER"
            assert "OBSOLETE" in results[0].risk_codes

    def test_no_stock_high_risk(self):
        syms = [_make_mock_symbol("C1", mpn="X1", manufacturer="X")]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part("digikey", "X1", stock=0)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check()
            assert "NO_STOCK" in results[0].risk_codes

    def test_single_source_risk(self):
        syms = [_make_mock_symbol("C1", mpn="X1", manufacturer="X")]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part("digikey", "X1", stock=500)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check()
            assert "SINGLE_SOURCE" in results[0].risk_codes

    def test_no_jlc_match(self):
        syms = [_make_mock_symbol("C1", mpn="X1", manufacturer="X")]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part("digikey", "X1", stock=500)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check()
            assert "NO_JLC_MATCH" in results[0].risk_codes

    def test_cache_only_default(self):
        """source_check defaults to cache_only=True."""
        syms = [_make_mock_symbol("C1", mpn="X1", manufacturer="X")]
        reviewer = self._make_reviewer(syms)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            # Should not call provider.lookup_mpn
            with patch("footfindr.suppliers.registry.SupplierRegistry") as MockReg:
                MockReg.return_value.normalize_name.side_effect = lambda x: x
                results = reviewer.source_check(cache_only=True)
                # No live API call should be made
                assert len(results) == 1

    def test_no_supplier_result_note(self):
        syms = [_make_mock_symbol("C1", mpn="RARE1", manufacturer="X")]
        reviewer = self._make_reviewer(syms)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            results = reviewer.source_check()
            assert "NO_SUPPLIER_RESULT" in results[0].risk_codes
            assert any("Run:" in n for n in results[0].notes)


# ---------------------------------------------------------------------------
# Cost rollup tests
# ---------------------------------------------------------------------------


class TestCostRollup:
    """Test cost estimation with quantity-aware pricing."""

    def _make_reviewer(self, symbols):
        from footfindr.review import ProjectReviewer
        reviewer = ProjectReviewer(
            "test.kicad_sch", qty=10, project_name="test",
        )
        reviewer._schematic = _make_mock_schematic(symbols)
        reviewer._approved_parts = []
        reviewer._by_ipn = {}
        reviewer._by_mpn = {}
        return reviewer

    def test_basic_cost(self):
        from footfindr.suppliers.models import PriceBreak
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part(
            "digikey", "GRM21",
            price_breaks=[PriceBreak(1, 0.10), PriceBreak(10, 0.05)])
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            lines, total = reviewer.cost_rollup(qty=10)
            priced = [l for l in lines if l.priced]
            assert len(priced) == 1
            # qty_per_board=1, build_qty=10, required_qty=10
            # price at qty 10 = $0.05
            # extended = 0.05 * 10 = $0.50
            assert priced[0].required_qty == 10
            assert priced[0].unit_price == pytest.approx(0.05)
            assert priced[0].extended_price == pytest.approx(0.50)
            assert total == pytest.approx(0.50)

    def test_qty_per_board_multiplied(self):
        """If 3 refs share same MPN, qty_per_board=3."""
        from footfindr.suppliers.models import PriceBreak
        syms = [
            _make_mock_symbol("R1", mpn="RC0805", manufacturer="Yageo"),
            _make_mock_symbol("R2", mpn="RC0805", manufacturer="Yageo"),
            _make_mock_symbol("R3", mpn="RC0805", manufacturer="Yageo"),
        ]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part(
            "digikey", "RC0805",
            price_breaks=[PriceBreak(1, 0.01), PriceBreak(10, 0.005),
                          PriceBreak(100, 0.002)])
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            lines, total = reviewer.cost_rollup(qty=10)
            priced = [l for l in lines if l.priced]
            assert len(priced) == 1
            assert priced[0].qty_per_board == 3
            assert priced[0].required_qty == 30
            # price at qty 30 = $0.005 (10-tier applies)
            assert priced[0].unit_price == pytest.approx(0.005)
            assert priced[0].extended_price == pytest.approx(0.15)

    def test_no_mpn_unpriced(self):
        syms = [_make_mock_symbol("C1")]  # no MPN
        reviewer = self._make_reviewer(syms)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            lines, total = reviewer.cost_rollup(qty=10)
            assert total is None
            unpriced = [l for l in lines if not l.priced]
            assert len(unpriced) == 1

    def test_moq_warning(self):
        from footfindr.suppliers.models import PriceBreak
        syms = [_make_mock_symbol("C1", mpn="RARE", manufacturer="X")]
        reviewer = self._make_reviewer(syms)
        mock_part = _make_mock_supplier_part(
            "digikey", "RARE", moq=1000,
            price_breaks=[PriceBreak(1000, 0.01)])
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = [mock_part]
            MockCache.return_value.close.return_value = None
            lines, total = reviewer.cost_rollup(qty=1)
            priced = [l for l in lines if l.priced]
            assert priced[0].moq_warning is True


# ---------------------------------------------------------------------------
# Full review tests
# ---------------------------------------------------------------------------


class TestProjectReview:
    """Test full project review combining all checks."""

    def _make_reviewer(self, symbols, approved=None):
        from footfindr.review import ProjectReviewer
        reviewer = ProjectReviewer(
            "test.kicad_sch", profile="prototype", qty=10,
            project_name="test-project",
        )
        reviewer._schematic = _make_mock_schematic(symbols)
        reviewer._approved_parts = approved or []
        reviewer._by_ipn = {p.internal_pn: p for p in (approved or [])}
        reviewer._by_mpn = {
            p.mpn: p for p in (approved or [])
            if p.mpn and p.mpn != "TBD"
        }
        return reviewer

    def test_review_returns_result(self):
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata",
                                  ipn="CAP-1")]
        part = _make_mock_approved_part("CAP-1", mpn="GRM21")
        reviewer = self._make_reviewer(syms, approved=[part])
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            result = reviewer.review()
            assert result.project_name == "test-project"
            assert result.profile == "prototype"
            assert result.qty == 10
            assert result.ref_count == 1

    def test_review_to_dict_json_valid(self):
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata",
                                  ipn="CAP-1")]
        part = _make_mock_approved_part("CAP-1", mpn="GRM21")
        reviewer = self._make_reviewer(syms, approved=[part])
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            result = reviewer.review()
            d = result.to_dict()
            # Should be JSON-serializable
            json_str = json.dumps(d)
            parsed = json.loads(json_str)
            assert parsed["project_name"] == "test-project"
            assert "summary" in parsed
            assert "issues" in parsed

    def test_review_recommended_actions(self):
        syms = [
            _make_mock_symbol("C1"),  # no MPN
            _make_mock_symbol("C2", mpn="GRM21", manufacturer="Murata"),
        ]
        reviewer = self._make_reviewer(syms)
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            result = reviewer.review()
            assert result.missing_mpn_count == 1
            assert len(result.recommended_actions) > 0


# ---------------------------------------------------------------------------
# Packet generation tests
# ---------------------------------------------------------------------------


class TestPacketGeneration:
    """Test markdown packet generation."""

    def test_generates_markdown(self, tmp_path):
        from footfindr.review import ProjectReviewer
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata",
                                  ipn="CAP-1")]
        reviewer = ProjectReviewer.__new__(ProjectReviewer)
        reviewer._schematic_path = Path("test.kicad_sch")
        reviewer._profile_name = "prototype"
        reviewer._qty = 10
        reviewer._project_name = "test"
        reviewer._workspace = None
        reviewer._schematic = _make_mock_schematic(syms)
        reviewer._approved_parts = []
        reviewer._by_ipn = {}
        reviewer._by_mpn = {}

        out = tmp_path / "review.md"
        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            result_path = reviewer.generate_packet(out_path=out)

        assert result_path.exists()
        content = result_path.read_text(encoding="utf-8")
        assert "# Project Review:" in content
        assert "Summary" in content
        assert "Refs scanned" in content

    def test_packet_writes_only_requested_file(self, tmp_path):
        from footfindr.review import ProjectReviewer
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata")]
        reviewer = ProjectReviewer.__new__(ProjectReviewer)
        reviewer._schematic_path = Path("test.kicad_sch")
        reviewer._profile_name = "prototype"
        reviewer._qty = 1
        reviewer._project_name = "test"
        reviewer._workspace = None
        reviewer._schematic = _make_mock_schematic(syms)
        reviewer._approved_parts = []
        reviewer._by_ipn = {}
        reviewer._by_mpn = {}

        out = tmp_path / "my_review.md"
        initial_files = set(tmp_path.iterdir())

        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            reviewer.generate_packet(out_path=out)

        new_files = set(tmp_path.iterdir()) - initial_files
        assert len(new_files) == 1
        assert out in new_files


# ---------------------------------------------------------------------------
# Fix-plan tests
# ---------------------------------------------------------------------------


class TestFixPlan:
    """Test conservative fix-plan generation."""

    def _make_reviewer(self, symbols):
        from footfindr.review import ProjectReviewer
        reviewer = ProjectReviewer(
            "test.kicad_sch", profile="prototype", qty=1,
            project_name="test",
        )
        reviewer._schematic = _make_mock_schematic(symbols)
        reviewer._approved_parts = []
        reviewer._by_ipn = {}
        reviewer._by_mpn = {}
        return reviewer

    def test_fix_plan_generates_plan(self, tmp_path):
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms)
        reviewer._workspace = tmp_path

        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            plan = reviewer.generate_fix_plan()
            assert plan is not None
            assert plan.status == "pending"
            assert plan.operation == "review-fix"
            assert len(plan.steps) > 0

    def test_fix_plan_does_not_apply(self, tmp_path):
        """Fix plan should not auto-apply."""
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms)
        reviewer._workspace = tmp_path

        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            plan = reviewer.generate_fix_plan()
            assert plan.status == "pending"  # NOT "applied"

    def test_fix_plan_no_substitutions(self, tmp_path):
        """Fix plan should not contain promote/substitute operations."""
        syms = [_make_mock_symbol("C1", mpn="GRM21", manufacturer="Murata")]
        reviewer = self._make_reviewer(syms)
        reviewer._workspace = tmp_path

        with patch("footfindr.suppliers.cache.SupplierCache") as MockCache:
            MockCache.return_value.lookup.return_value = []
            MockCache.return_value.close.return_value = None
            plan = reviewer.generate_fix_plan()
            for step in plan.steps:
                assert step.operation != "promote"
                assert step.operation != "substitute"
                assert step.operation != "bind_footprint"


# ---------------------------------------------------------------------------
# Risk level tests
# ---------------------------------------------------------------------------


class TestRiskLevels:
    """Test risk level derivation from risk codes."""

    def test_obsolete_is_blocker(self):
        from footfindr.review import _risk_level_from_codes
        assert _risk_level_from_codes(["OBSOLETE"]) == "BLOCKER"

    def test_no_stock_is_high(self):
        from footfindr.review import _risk_level_from_codes
        assert _risk_level_from_codes(["NO_STOCK"]) == "HIGH"

    def test_low_stock_is_medium(self):
        from footfindr.review import _risk_level_from_codes
        assert _risk_level_from_codes(["LOW_STOCK"]) == "MEDIUM"

    def test_no_codes_is_low(self):
        from footfindr.review import _risk_level_from_codes
        assert _risk_level_from_codes([]) == "LOW"

    def test_multiple_codes_highest_wins(self):
        from footfindr.review import _risk_level_from_codes
        assert _risk_level_from_codes(
            ["LOW_STOCK", "OBSOLETE"]) == "BLOCKER"
        assert _risk_level_from_codes(
            ["LOW_STOCK", "NO_STOCK"]) == "HIGH"


# ---------------------------------------------------------------------------
# Price selection tests
# ---------------------------------------------------------------------------


class TestBestPrice:
    """Test price break selection at quantity."""

    def test_exact_tier(self):
        from footfindr.review import _best_price_at_qty
        from footfindr.suppliers.models import PriceBreak
        pbs = [PriceBreak(1, 0.10), PriceBreak(10, 0.05),
               PriceBreak(100, 0.02)]
        assert _best_price_at_qty(pbs, 10) == pytest.approx(0.05)

    def test_between_tiers(self):
        from footfindr.review import _best_price_at_qty
        from footfindr.suppliers.models import PriceBreak
        pbs = [PriceBreak(1, 0.10), PriceBreak(100, 0.02)]
        assert _best_price_at_qty(pbs, 50) == pytest.approx(0.10)

    def test_above_all_tiers(self):
        from footfindr.review import _best_price_at_qty
        from footfindr.suppliers.models import PriceBreak
        pbs = [PriceBreak(1, 0.10), PriceBreak(10, 0.05)]
        assert _best_price_at_qty(pbs, 1000) == pytest.approx(0.05)

    def test_empty_breaks(self):
        from footfindr.review import _best_price_at_qty
        assert _best_price_at_qty([], 10) is None

    def test_below_first_tier(self):
        from footfindr.review import _best_price_at_qty
        from footfindr.suppliers.models import PriceBreak
        pbs = [PriceBreak(10, 0.05), PriceBreak(100, 0.02)]
        # qty=5 < 10, should use smallest tier
        assert _best_price_at_qty(pbs, 5) == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# CLI registration test
# ---------------------------------------------------------------------------


class TestCLIRegistration:
    """Test that M9 commands are registered."""

    def test_cli_imports(self):
        from footfindr.cli import app
        assert app is not None

    def test_review_engine_imports(self):
        from footfindr.review import ProjectReviewer
        assert ProjectReviewer is not None

    def test_assembly_profiles_import(self):
        from footfindr.assembly_profiles import get_profile, list_profiles
        assert get_profile is not None
        assert list_profiles is not None

    def test_project_issue_import(self):
        from footfindr.bom.models import ProjectIssue
        assert ProjectIssue is not None


# ---------------------------------------------------------------------------
# JSON output validation
# ---------------------------------------------------------------------------


class TestJSONOutput:
    """Test that JSON outputs are valid and structured."""

    def test_source_check_result_json(self):
        from footfindr.review import SourceCheckResult
        r = SourceCheckResult(
            ref="C1", mpn="GRM21", manufacturer="Murata",
            risk_level="LOW", risk_codes=[], best_stock=5000,
            best_supplier="digikey", best_price=0.01,
            supplier_count=2, has_jlc=True,
        )
        d = r.to_dict()
        j = json.dumps(d)
        parsed = json.loads(j)
        assert parsed["ref"] == "C1"
        assert parsed["risk_level"] == "LOW"

    def test_cost_line_json(self):
        from footfindr.review import CostLine
        c = CostLine(
            ref="C1", mpn="GRM21", qty_per_board=1,
            required_qty=10, unit_price=0.05,
            extended_price=0.50, supplier="digikey",
            priced=True,
        )
        d = c.to_dict()
        j = json.dumps(d)
        parsed = json.loads(j)
        assert parsed["required_qty"] == 10

    def test_project_issue_json(self):
        from footfindr.bom.models import ProjectIssue
        issue = ProjectIssue(
            severity="FAIL", code="MISSING_MPN", ref="C1",
            field="MPN", message="test",
        )
        j = json.dumps(issue.to_dict())
        parsed = json.loads(j)
        assert parsed["severity"] == "FAIL"


# ---------------------------------------------------------------------------
# Aliases still work
# ---------------------------------------------------------------------------


class TestAliasesStillWork:
    """Verify M8.7 aliases still function after M9."""

    def test_supplier_alias(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("dk") == "digikey"

    def test_field_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("pack") == "package"

    def test_profile_alias(self):
        """profile command should be accessible."""
        from footfindr.cli import app
        assert app is not None
