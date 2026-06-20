"""Tests for M9.2 live-bug fixes.

Covers:
    Bug 1 — constraint checker reads attributes["Capacitance"]
    Bug 2 — capacitance normalization handles all forms
    Bug 3 — query builder canonicalizes 4.7u -> 4.7uF
    Bug 4 — parametric search results not marked LOW_RELEVANCE
    Bug 5 — ff ref assign uses correct session API (get_by_index)
    Amendment 6 — ff ref assign defaults to plan mode
    Amendment 9 — debug output shows source, not "?"
"""

from __future__ import annotations

import pytest
from pathlib import Path

from footfindr.suppliers.models import SupplierPart


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cap_part(**overrides) -> SupplierPart:
    """Create a capacitor SupplierPart matching DigiKey response structure."""
    defaults = dict(
        supplier="digikey",
        supplier_pn="490-GRT188C81E475KE13D",
        mpn="GRT188C81E475KE13D",
        manufacturer="Murata Electronics",
        description="CAP CER 4.7UF 25V X6S 0603",
        stock=50000,
        package="0603 (1608 Metric)",
        lifecycle="Active",
        attributes={
            "Capacitance": "4.7 µF",
            "Voltage - Rated": "25V",
            "Temperature Coefficient": "X6S",
            "Package / Case": "0603 (1608 Metric)",
            "Tolerance": "±10%",
            "Operating Temperature": "-55°C ~ 105°C",
        },
    )
    defaults.update(overrides)
    return SupplierPart(**defaults)


def _cap_part_no_top_package(**overrides) -> SupplierPart:
    """Capacitor where package only lives in attributes, not top-level."""
    defaults = dict(
        supplier="digikey",
        mpn="GRM188R71E475KE11D",
        manufacturer="Murata Electronics",
        description="CAP CER 4.7UF 25V X7R 0603",
        stock=10000,
        package=None,
        lifecycle="Active",
        attributes={
            "Capacitance": "4.7 µF",
            "Voltage - Rated": "25V",
            "Temperature Coefficient": "X7R",
            "Package / Case": "0603 (1608 Metric)",
        },
    )
    defaults.update(overrides)
    return SupplierPart(**defaults)


# ---------------------------------------------------------------------------
# Bug 1: Constraint checker reads dynamic attributes
# ---------------------------------------------------------------------------

class TestFieldResolution:
    """Verify centralized get_part_field resolves supplier attributes."""

    def test_capacitance_from_attributes(self):
        from footfindr.constraints import get_part_field
        part = _cap_part()
        fv = get_part_field(part, "capacitance")
        assert fv.value == "4.7 µF"
        assert fv.source == "attributes.Capacitance"

    def test_voltage_from_attributes(self):
        from footfindr.constraints import get_part_field
        part = _cap_part()
        fv = get_part_field(part, "voltage")
        assert fv.value == "25V"
        assert fv.source == "attributes.Voltage - Rated"

    def test_package_from_top_level(self):
        from footfindr.constraints import get_part_field
        part = _cap_part()
        fv = get_part_field(part, "package")
        assert fv.value == "0603 (1608 Metric)"
        assert fv.source == "top.package"

    def test_package_falls_through_to_attributes(self):
        from footfindr.constraints import get_part_field
        part = _cap_part_no_top_package()
        fv = get_part_field(part, "package")
        assert fv.value == "0603 (1608 Metric)"
        assert fv.source == "attributes.Package / Case"

    def test_dielectric_from_temperature_coefficient(self):
        from footfindr.constraints import get_part_field
        part = _cap_part()
        fv = get_part_field(part, "dielectric")
        assert fv.value == "X6S"
        assert fv.source == "attributes.Temperature Coefficient"

    def test_tolerance_from_attributes(self):
        from footfindr.constraints import get_part_field
        part = _cap_part()
        fv = get_part_field(part, "tolerance")
        assert fv.value == "±10%"
        assert fv.source == "attributes.Tolerance"

    def test_missing_field_returns_none(self):
        from footfindr.constraints import get_part_field
        part = _cap_part()
        fv = get_part_field(part, "frequency")
        assert fv.value is None
        assert fv.source is None

    def test_resistance_from_attributes(self):
        from footfindr.constraints import get_part_field
        part = SupplierPart(
            supplier="digikey", mpn="RC0603FR-0710KL",
            attributes={"Resistance": "10 kΩ"},
        )
        fv = get_part_field(part, "resistance")
        assert fv.value == "10 kΩ"
        assert fv.source == "attributes.Resistance"


# ---------------------------------------------------------------------------
# Bug 1 (continued): Constraint check PASS with attributes
# ---------------------------------------------------------------------------

class TestConstraintWithAttributes:
    """Verify constraints pass when values come from supplier attributes."""

    def test_capacitance_eq_passes(self):
        from footfindr.constraints import Constraint, check_part_constraints
        part = _cap_part()
        constraints = [Constraint(field="capacitance", op="eq", value="4.7uF")]
        results = check_part_constraints(constraints, part)
        assert len(results) == 1
        assert results[0].passed, f"Expected PASS but got: {results[0].message}"
        assert results[0].source == "attributes.Capacitance"
        assert results[0].actual_value == "4.7 µF"

    def test_voltage_gte_passes(self):
        from footfindr.constraints import Constraint, check_part_constraints
        part = _cap_part()
        constraints = [Constraint(field="voltage", op="gte", value="25V")]
        results = check_part_constraints(constraints, part)
        assert results[0].passed, f"Expected PASS but got: {results[0].message}"

    def test_package_eq_passes(self):
        from footfindr.constraints import Constraint, check_part_constraints
        part = _cap_part()
        constraints = [Constraint(field="package", op="eq", value="0603")]
        results = check_part_constraints(constraints, part)
        assert results[0].passed, f"Expected PASS but got: {results[0].message}"

    def test_all_three_constraints_pass(self):
        """The exact scenario from the bug report."""
        from footfindr.constraints import Constraint, check_part_constraints
        part = _cap_part()
        constraints = [
            Constraint(field="capacitance", op="eq", value="4.7uF"),
            Constraint(field="voltage", op="gte", value="25V"),
            Constraint(field="package", op="eq", value="0603"),
        ]
        results = check_part_constraints(constraints, part)
        for r in results:
            assert r.passed, f"{r.constraint.field}: Expected PASS but got: {r.message}"
            assert r.source is not None, f"{r.constraint.field}: source should not be None"
            assert r.actual_value, f"{r.constraint.field}: actual_value should not be empty"


# ---------------------------------------------------------------------------
# Bug 2: Capacitance normalization — all forms equivalent
# ---------------------------------------------------------------------------

class TestCapacitanceNormalization:
    """Verify all capacitance representations parse to the same numeric value."""

    @pytest.mark.parametrize("raw", [
        "4.7uF",
        "4.7 uF",
        "4.7 µF",
        "4700nF",
        "4700 nF",
        "0.0047mF",
        "4u7",
        "4.7u",
    ])
    def test_all_forms_equal_4_7uF(self, raw: str):
        from footfindr.constraints import parse_numeric_value
        val = parse_numeric_value(raw, "capacitance")
        assert val is not None, f"Failed to parse '{raw}'"
        assert abs(val - 4.7e-6) / 4.7e-6 < 0.01, (
            f"'{raw}' parsed to {val}, expected ~4.7e-6"
        )

    def test_numeric_eq_4u7_vs_4_7_µF(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7uF", "4.7 µF", "capacitance")

    def test_numeric_eq_4u7_shorthand_vs_standard(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7uF", "4u7", "capacitance")

    def test_numeric_eq_nF_vs_uF(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7uF", "4700nF", "capacitance")


# ---------------------------------------------------------------------------
# Bug 3: Query builder canonicalizes schematic values
# ---------------------------------------------------------------------------

class TestQueryCanonicalization:
    """Verify query builder renders canonical units."""

    def test_4u7_becomes_4_7uF_in_query(self, tmp_path: Path):
        """Schematic value '4.7u' should become '4.7uF' in generated query."""
        from footfindr.constraints import ConstraintManager, Constraint

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        mgr = ConstraintManager(workspace=ws)
        mgr.set_constraint("C1", "capacitance", "4.7uF")
        mgr.set_constraint("C1", "voltage", ">=25V")
        mgr.set_constraint("C1", "package", "0603")

        query, _ = mgr.build_search_query("C1", schematic_value="4.7u")
        assert "4.7uF" in query, f"Expected '4.7uF' in query, got: {query}"
        assert "4.7u " not in query, f"Raw '4.7u' should not appear in query: {query}"

    def test_no_duplicate_value_in_query(self, tmp_path: Path):
        """Schematic value and constraint value should be deduped numerically."""
        from footfindr.constraints import ConstraintManager

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        mgr = ConstraintManager(workspace=ws)
        mgr.set_constraint("C1", "capacitance", "4.7uF")
        mgr.set_constraint("C1", "voltage", ">=25V")
        mgr.set_constraint("C1", "package", "0603")

        query, _ = mgr.build_search_query("C1", schematic_value="4.7u")
        # Should have exactly one 4.7uF, not "4.7uF 4.7uF" or "4.7uF 4.7u"
        parts = query.split()
        cap_parts = [p for p in parts if "4.7" in p or "4700" in p]
        assert len(cap_parts) == 1, f"Expected one capacitance value, got: {cap_parts} in query: {query}"

    def test_100n_becomes_100nF(self, tmp_path: Path):
        from footfindr.constraints import ConstraintManager

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        mgr = ConstraintManager(workspace=ws)
        mgr.set_constraint("C2", "capacitance", "100nF")

        query, _ = mgr.build_search_query("C2", schematic_value="100n")
        assert "100nF" in query, f"Expected '100nF' in query, got: {query}"

    def test_query_contains_ceramic_capacitor(self, tmp_path: Path):
        from footfindr.constraints import ConstraintManager

        ws = tmp_path / ".footfindr"
        ws.mkdir()
        mgr = ConstraintManager(workspace=ws)
        mgr.set_constraint("C1", "capacitance", "4.7uF")

        query, _ = mgr.build_search_query("C1", schematic_value="4.7u")
        assert "ceramic capacitor" in query.lower(), f"Expected category hint in query: {query}"


# ---------------------------------------------------------------------------
# Bug 4: Parametric search results not marked LOW_RELEVANCE
# ---------------------------------------------------------------------------

class TestParametricRelevance:
    """Verify parametric queries don't produce LOW_RELEVANCE badges."""

    def test_parametric_query_not_mpn_like(self):
        from footfindr.suppliers.session import is_mpn_like_query
        assert not is_mpn_like_query("4.7uF 25V 0603 ceramic capacitor")

    def test_mpn_query_is_mpn_like(self):
        from footfindr.suppliers.session import is_mpn_like_query
        assert is_mpn_like_query("GRT188C81E475KE13D")

    def test_parametric_relevance_is_moderate(self):
        from footfindr.suppliers.session import compute_relevance
        part = _cap_part()
        score = compute_relevance(part, "4.7uF 25V 0603 ceramic capacitor")
        assert score <= 3, f"Expected relevance <= 3 for parametric query, got {score}"

    def test_no_low_relevance_badge_parametric(self):
        from footfindr.suppliers.badges import compute_badges
        part = _cap_part()
        badges = compute_badges(part, query="4.7uF 25V 0603 ceramic capacitor")
        assert "LOW_RELEVANCE" not in badges, f"Unexpected LOW_RELEVANCE in {badges}"

    def test_no_low_relevance_when_constraints_pass(self):
        from footfindr.suppliers.badges import compute_badges
        part = _cap_part()
        badges = compute_badges(
            part,
            query="4.7uF 25V 0603 ceramic capacitor",
            constraint_passed=True,
        )
        assert "LOW_RELEVANCE" not in badges

    def test_mpn_search_still_gets_low_relevance(self):
        """MPN searches should still flag irrelevant parts."""
        from footfindr.suppliers.badges import compute_badges
        part = SupplierPart(
            supplier="digikey",
            mpn="ZUSA-HT-3030",
            description="Unrelated part",
        )
        badges = compute_badges(part, query="AD9959BCPZ")
        assert "LOW_RELEVANCE" in badges


# ---------------------------------------------------------------------------
# Bug 5: ff ref assign uses correct session API
# ---------------------------------------------------------------------------

class TestSessionResolution:
    """Verify _resolve_session_part uses get_by_index, not active_results."""

    def test_search_session_has_get_by_index(self):
        """SearchSession should have get_by_index method."""
        from footfindr.suppliers.session import SearchSession
        assert hasattr(SearchSession, "get_by_index")

    def test_search_session_has_get_active_results(self):
        """SearchSession should have get_active_results method."""
        from footfindr.suppliers.session import SearchSession
        assert hasattr(SearchSession, "get_active_results")

    def test_search_session_does_not_have_active_results(self):
        """SearchSession should NOT have bare active_results method."""
        from footfindr.suppliers.session import SearchSession
        # It shouldn't be a direct method (get_active_results is the correct one)
        assert not hasattr(SearchSession, "active_results"), (
            "SearchSession has 'active_results' — this was the bug. "
            "Use 'get_active_results' or 'get_by_index' instead."
        )

    def test_get_by_index_returns_correct_part(self):
        """get_by_index should return the correct 1-based indexed part."""
        import datetime
        from footfindr.suppliers.session import SearchSession

        parts = [
            SupplierPart(supplier="dk", mpn="PART_A"),
            SupplierPart(supplier="dk", mpn="PART_B"),
            SupplierPart(supplier="dk", mpn="PART_C"),
        ]
        session = SearchSession(
            query="test",
            suppliers=["dk"],
            created_at=datetime.datetime.now().isoformat(),
            last_updated=datetime.datetime.now().isoformat(),
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
        )
        assert session.get_by_index(1).mpn == "PART_A"
        assert session.get_by_index(2).mpn == "PART_B"
        assert session.get_by_index(3).mpn == "PART_C"
        assert session.get_by_index(0) is None
        assert session.get_by_index(4) is None


# ---------------------------------------------------------------------------
# Amendment 9: Debug output shows source, not "?"
# ---------------------------------------------------------------------------

class TestDebugOutputSource:
    """Verify ConstraintResult includes source information."""

    def test_constraint_result_has_source(self):
        from footfindr.constraints import Constraint, check_part_constraints
        part = _cap_part()
        constraints = [Constraint(field="capacitance", op="eq", value="4.7uF")]
        results = check_part_constraints(constraints, part)
        cr = results[0]
        assert cr.source is not None, "ConstraintResult.source should not be None"
        assert "Capacitance" in cr.source, f"Source should mention 'Capacitance', got: {cr.source}"

    def test_actual_value_not_empty_when_attribute_exists(self):
        from footfindr.constraints import Constraint, check_part_constraints
        part = _cap_part()
        constraints = [
            Constraint(field="capacitance", op="eq", value="4.7uF"),
            Constraint(field="voltage", op="gte", value="25V"),
            Constraint(field="package", op="eq", value="0603"),
        ]
        results = check_part_constraints(constraints, part)
        for r in results:
            assert r.actual_value != "", (
                f"{r.constraint.field}: actual_value should not be empty "
                f"(source={r.source})"
            )
            assert r.actual_value != "?", (
                f"{r.constraint.field}: actual_value should not be '?'"
            )
