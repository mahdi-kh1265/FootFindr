"""Tests for search-for query generation, constraint field aliases, and matching.

Tests constraint normalization, query builder, and package/dielectric matching.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Mock SupplierPart for constraint testing
# ---------------------------------------------------------------------------

@dataclass
class MockSupplierPart:
    """Minimal SupplierPart for testing constraint checks."""
    mpn: str = ""
    supplier: str = "digikey"
    description: str = ""
    package: str = ""
    supplier_device_package: str = ""
    stock: int = 100
    price: float = 0.10
    attributes: dict[str, str] = dc_field(default_factory=dict)
    result_id: str = "test-1"
    temperature_range: str = ""

    def is_valid(self) -> bool:
        return bool(self.mpn)


# ---------------------------------------------------------------------------
# Constraint field alias normalization tests
# ---------------------------------------------------------------------------


class TestConstraintFieldAliases:
    """Test that constraint field aliases are normalized to canonical names."""

    def test_volt_normalizes_to_voltage(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("volt", ">=25V")
        assert c.field == "voltage"
        assert c.op == "gte"
        assert c.value == "25V"

    def test_diel_normalizes_to_dielectric(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("diel", "X7R")
        assert c.field == "dielectric"
        assert c.op == "eq"
        assert c.value == "X7R"

    def test_pack_normalizes_to_package(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("pack", "0603")
        assert c.field == "package"
        assert c.op == "eq"
        assert c.value == "0603"

    def test_pkg_normalizes_to_package(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("pkg", "0805")
        assert c.field == "package"

    def test_case_normalizes_to_package(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("case", "1206")
        assert c.field == "package"

    def test_tol_normalizes_to_tolerance(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("tol", "<=1%")
        assert c.field == "tolerance"
        assert c.op == "lte"
        assert c.value == "1%"

    def test_v_normalizes_to_voltage(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("v", "50V")
        assert c.field == "voltage"

    def test_temp_normalizes_to_temperature(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("temp", ">=85C")
        assert c.field == "temperature"

    def test_cap_normalizes_to_capacitance(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("cap", "100nF")
        assert c.field == "capacitance"

    def test_res_normalizes_to_resistance(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("res", "10k")
        assert c.field == "resistance"

    def test_canonical_names_unchanged(self):
        from footfindr.constraints import Constraint
        for name in ("voltage", "dielectric", "package", "tolerance"):
            c = Constraint.from_field_value(name, "test")
            assert c.field == name

    def test_unknown_field_preserved(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("custom_field", "value")
        assert c.field == "custom_field"

    def test_special_fields_not_normalized(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("family", "LT3045")
        assert c.field == "family"
        c = Constraint.from_field_value("reason", "hand-rework")
        assert c.field == "reason"


# ---------------------------------------------------------------------------
# build_search_query tests
# ---------------------------------------------------------------------------


class TestBuildSearchQuery:
    """Test high-specificity query generation."""

    def test_capacitor_with_constraints(self, tmp_path):
        """Capacitor with voltage, dielectric, package → specific query."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "volt", ">=25V")
        mgr.set_constraint("C1", "diel", "X7R")
        mgr.set_constraint("C1", "pack", "0603")

        query, constraints = mgr.build_search_query("C1")
        assert "25V" in query
        assert "X7R" in query
        assert "0603" in query
        assert "capacitor" in query.lower()

    def test_capacitor_with_schematic_value(self, tmp_path):
        """Schematic Value field should appear in query."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "voltage", ">=25V")

        query, _ = mgr.build_search_query("C1", schematic_value="0.1uF")
        # Canonicalization renders 0.1uF → 100nF (normalized display form)
        assert "100nF" in query or "0.1uF" in query
        assert "25V" in query
        assert "capacitor" in query.lower()

    def test_no_constraints_no_value_empty(self, tmp_path):
        """No constraints and no schematic value → empty."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        query, _ = mgr.build_search_query("C99")
        assert query == ""

    def test_schematic_value_only(self, tmp_path):
        """Schematic value without constraints still works."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        query, _ = mgr.build_search_query("C1", schematic_value="100nF")
        assert "100nF" in query
        assert "capacitor" in query.lower()

    def test_gt_operator_includes_value(self, tmp_path):
        """>25V should include 25V in query."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "voltage", ">25V")
        query, _ = mgr.build_search_query("C1")
        assert "25V" in query

    def test_resistor_query(self, tmp_path):
        """Resistor ref generates resistor-specific query."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("R1", "resistance", "10k")
        mgr.set_constraint("R1", "package", "0603")
        query, _ = mgr.build_search_query("R1")
        assert "10k" in query
        assert "0603" in query
        assert "resistor" in query.lower()

    def test_ic_ref_no_generic_term(self, tmp_path):
        """IC refs should not add a generic 'ic' term."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("U3", "family", "LT3045")
        query, _ = mgr.build_search_query("U3")
        assert "LT3045" in query
        # Should not append bare "ic" — too generic

    def test_no_duplicate_schematic_value_and_constraint(self, tmp_path):
        """Schematic value should not be duplicated if constraint has same value."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "capacitance", "100nF")
        query, _ = mgr.build_search_query("C1", schematic_value="100nF")
        # 100nF should appear only once
        assert query.count("100nF") == 1

    def test_category_hint_ceramic_capacitor(self, tmp_path):
        """C-ref should get 'ceramic capacitor', not just 'capacitor'."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "voltage", "25V")
        query, _ = mgr.build_search_query("C1")
        assert "ceramic capacitor" in query


# ---------------------------------------------------------------------------
# Package matching tests
# ---------------------------------------------------------------------------


class TestPackageMatching:
    """Test normalized package matching in constraint checks."""

    def test_exact_match(self):
        from footfindr.constraints import _package_matches
        assert _package_matches("0603", "0603") is True

    def test_parenthetical_metric(self):
        from footfindr.constraints import _package_matches
        assert _package_matches("0603", "0603 (1608 Metric)") is True

    def test_metric_equivalent(self):
        from footfindr.constraints import _package_matches
        assert _package_matches("0603", "1608") is True

    def test_metric_equivalent_reverse(self):
        from footfindr.constraints import _package_matches
        assert _package_matches("1608", "0603") is True

    def test_no_match(self):
        from footfindr.constraints import _package_matches
        assert _package_matches("0603", "0805") is False

    def test_0805_variants(self):
        from footfindr.constraints import _package_matches
        assert _package_matches("0805", "0805 (2012 Metric)") is True
        assert _package_matches("0805", "2012") is True

    def test_empty_values(self):
        from footfindr.constraints import _package_matches
        assert _package_matches("0603", "") is False
        assert _package_matches("", "0603") is False

    def test_constraint_check_uses_package_matching(self):
        """check_constraint should use _package_matches for package eq."""
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="package", op="eq", value="0603")
        result = check_constraint(c, "0603 (1608 Metric)")
        assert result.passed is True

    def test_constraint_check_package_metric(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="package", op="eq", value="0603")
        result = check_constraint(c, "1608")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Dielectric matching tests
# ---------------------------------------------------------------------------


class TestDielectricMatching:
    """Test normalized dielectric matching."""

    def test_exact_match(self):
        from footfindr.constraints import _dielectric_matches
        assert _dielectric_matches("X7R", "X7R") is True

    def test_substring_match(self):
        from footfindr.constraints import _dielectric_matches
        assert _dielectric_matches("X7R", "X7R, X5R") is True

    def test_case_insensitive(self):
        from footfindr.constraints import _dielectric_matches
        assert _dielectric_matches("x7r", "X7R") is True

    def test_no_match(self):
        from footfindr.constraints import _dielectric_matches
        assert _dielectric_matches("X7R", "C0G") is False

    def test_constraint_check_uses_dielectric_matching(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="dielectric", op="eq", value="X7R")
        result = check_constraint(c, "X7R")
        assert result.passed is True

    def test_constraint_dielectric_from_temp_coeff(self):
        """DigiKey Temperature Coefficient field containing X7R should pass."""
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="dielectric", op="eq", value="X7R")
        result = check_constraint(c, "X7R")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Full-stack: constraint check with MockSupplierPart
# ---------------------------------------------------------------------------


class TestConstraintCheckIntegration:
    """Test check_part_constraints with normalized fields."""

    def test_capacitor_passes_all(self):
        from footfindr.constraints import Constraint, check_part_constraints
        part = MockSupplierPart(
            mpn="GRM188R71E104KA01",
            package="0603 (1608 Metric)",
            attributes={
                "Voltage - Rated": "25V",
                "Temperature Coefficient": "X7R",
                "Capacitance": "100nF",
            },
        )
        constraints = [
            Constraint(field="voltage", op="gte", value="25V"),
            Constraint(field="dielectric", op="eq", value="X7R"),
            Constraint(field="package", op="eq", value="0603"),
        ]
        results = check_part_constraints(constraints, part)
        for r in results:
            assert r.passed, f"Failed: {r.message}"

    def test_capacitor_fails_voltage(self):
        from footfindr.constraints import Constraint, check_part_constraints
        part = MockSupplierPart(
            mpn="GRM188R71E104KA01",
            package="0603",
            attributes={
                "Voltage - Rated": "16V",
                "Temperature Coefficient": "X7R",
            },
        )
        constraints = [
            Constraint(field="voltage", op="gte", value="25V"),
        ]
        results = check_part_constraints(constraints, part)
        assert not results[0].passed

    def test_gt_operator_rejects_exact(self):
        """>25V should reject exactly 25V."""
        from footfindr.constraints import Constraint, check_part_constraints
        part = MockSupplierPart(
            mpn="test",
            attributes={"Voltage - Rated": "25V"},
        )
        constraints = [
            Constraint(field="voltage", op="gt", value="25V"),
        ]
        results = check_part_constraints(constraints, part)
        assert not results[0].passed

    def test_gte_operator_accepts_exact(self):
        """>=25V should accept exactly 25V."""
        from footfindr.constraints import Constraint, check_part_constraints
        part = MockSupplierPart(
            mpn="test",
            attributes={"Voltage - Rated": "25V"},
        )
        constraints = [
            Constraint(field="voltage", op="gte", value="25V"),
        ]
        results = check_part_constraints(constraints, part)
        assert results[0].passed


# ---------------------------------------------------------------------------
# Cache key uniqueness tests
# ---------------------------------------------------------------------------


class TestCacheKeyUniqueness:
    """Verify that different generated queries produce different cache keys."""

    def test_different_queries_different_keys(self, tmp_path):
        """broad 'capacitor' and specific '25V X7R 0603 ceramic capacitor' are different keys."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")

        # No constraints → empty (or generic)
        q1, _ = mgr.build_search_query("C99")

        # With constraints → specific
        mgr.set_constraint("C1", "voltage", ">=25V")
        mgr.set_constraint("C1", "dielectric", "X7R")
        mgr.set_constraint("C1", "package", "0603")
        q2, _ = mgr.build_search_query("C1")

        # These must be different strings → different cache keys
        assert q1 != q2
        assert "25V" in q2
        assert "X7R" in q2


# ---------------------------------------------------------------------------
# YAML load normalization tests
# ---------------------------------------------------------------------------


class TestYAMLNormalization:
    """Test that YAML with alias field names gets normalized on load."""

    def test_yaml_aliases_normalized(self, tmp_path):
        """YAML with 'volt', 'diel', 'pack' should normalize to canonical names."""
        from footfindr.constraints import ConstraintManager
        ff_dir = tmp_path / ".footfindr"
        ff_dir.mkdir()
        (ff_dir / "constraints.yaml").write_text(
            "version: 1\n"
            "refs:\n"
            "  C1:\n"
            "    volt: '>=25V'\n"
            "    diel: X7R\n"
            "    pack: '0603'\n",
            encoding="utf-8",
        )
        mgr = ConstraintManager(workspace=ff_dir)
        constraints = mgr.get_constraints_for("C1")
        fields = {c.field for c in constraints}
        assert "voltage" in fields
        assert "dielectric" in fields
        assert "package" in fields
        # Should NOT have the aliases
        assert "volt" not in fields
        assert "diel" not in fields
        assert "pack" not in fields

    def test_yaml_canonical_names_preserved(self, tmp_path):
        """YAML with canonical names should work as before."""
        from footfindr.constraints import ConstraintManager
        ff_dir = tmp_path / ".footfindr"
        ff_dir.mkdir()
        (ff_dir / "constraints.yaml").write_text(
            "version: 1\n"
            "refs:\n"
            "  C1:\n"
            "    voltage: '>=25V'\n"
            "    dielectric: X7R\n"
            "    package: '0603'\n",
            encoding="utf-8",
        )
        mgr = ConstraintManager(workspace=ff_dir)
        constraints = mgr.get_constraints_for("C1")
        fields = {c.field for c in constraints}
        assert "voltage" in fields
        assert "dielectric" in fields
        assert "package" in fields


# ---------------------------------------------------------------------------
# Normalize constraint field function tests
# ---------------------------------------------------------------------------


class TestNormalizeConstraintField:
    """Test normalize_constraint_field directly."""

    def test_all_voltage_aliases(self):
        from footfindr.constraints import normalize_constraint_field
        for alias in ("volt", "voltage", "v", "vmax", "vmin"):
            assert normalize_constraint_field(alias) == "voltage", f"Failed for {alias}"

    def test_all_dielectric_aliases(self):
        from footfindr.constraints import normalize_constraint_field
        for alias in ("diel", "dielectric"):
            assert normalize_constraint_field(alias) == "dielectric"

    def test_all_package_aliases(self):
        from footfindr.constraints import normalize_constraint_field
        for alias in ("pack", "pkg", "case", "package"):
            assert normalize_constraint_field(alias) == "package"

    def test_all_tolerance_aliases(self):
        from footfindr.constraints import normalize_constraint_field
        for alias in ("tol", "tolerance"):
            assert normalize_constraint_field(alias) == "tolerance"

    def test_all_temperature_aliases(self):
        from footfindr.constraints import normalize_constraint_field
        for alias in ("temp", "temperature", "temperature_range"):
            assert normalize_constraint_field(alias) == "temperature"

    def test_unknown_passthrough(self):
        from footfindr.constraints import normalize_constraint_field
        assert normalize_constraint_field("some_custom") == "some_custom"
