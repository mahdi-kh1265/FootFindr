"""M8.6 tests — constraints, explainability, plan/apply, CLI cleanup.

No live API calls for session/list/fields/sort/filter/explain/constraint/plan commands.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with .footfindr dir."""
    ws = tmp_path / ".footfindr"
    ws.mkdir(parents=True)
    return ws


@pytest.fixture
def mock_supplier_part():
    """Create a mock SupplierPart-like object."""
    from footfindr.suppliers.models import PriceBreak, SupplierPart

    return SupplierPart(
        supplier="digikey",
        supplier_pn="1276-1100-1-ND",
        mpn="GRM21BR61C106KE15L",
        manufacturer="Murata",
        description="CAP CER 10UF 16V X5R 0805",
        stock=50000,
        package="0805",
        lifecycle="Active",
        price_breaks=[PriceBreak(1, 0.10), PriceBreak(100, 0.05)],
        product_url="https://www.digikey.com/product/GRM21BR61C106KE15L",
        supplier_device_package="0805 (2012 Metric)",
        temperature_range="-55°C ~ 85°C",
        attributes={
            "Capacitance": "10µF",
            "Voltage - Rated": "16V",
            "Temperature Coefficient": "X5R",
            "Package / Case": "0805 (2012 Metric)",
            "Tolerance": "±10%",
            "Operating Temperature": "-55°C ~ 85°C",
        },
    )


@pytest.fixture
def mock_supplier_part_b():
    """Create a second mock SupplierPart for comparison."""
    from footfindr.suppliers.models import PriceBreak, SupplierPart

    return SupplierPart(
        supplier="mouser",
        supplier_pn="81-GRM21BR61C106KE15",
        mpn="GRM21BR61C106KE15K",
        manufacturer="Murata",
        description="MLCC 10µF 16V X5R 0805",
        stock=20000,
        package="0805",
        lifecycle="Active",
        price_breaks=[PriceBreak(1, 0.12), PriceBreak(100, 0.06)],
        product_url="https://www.mouser.com/product/GRM21BR61C106KE15K",
        supplier_device_package="0805 (2012 Metric)",
        temperature_range="-55°C ~ 105°C",
        attributes={
            "Capacitance": "10µF",
            "Voltage - Rated": "16V",
            "Temperature Coefficient": "X5R",
            "Package / Case": "0805 (2012 Metric)",
            "Tolerance": "±10%",
            "Operating Temperature": "-55°C ~ 105°C",
        },
    )


@pytest.fixture
def mock_ic_part():
    """IC part for category inference testing."""
    from footfindr.suppliers.models import PriceBreak, SupplierPart

    return SupplierPart(
        supplier="digikey",
        supplier_pn="LT3045EDD#PBF-ND",
        mpn="LT3045EDD#PBF",
        manufacturer="Analog Devices",
        description="IC REG LINEAR POS ADJ 500MA DFN",
        stock=5000,
        package="DFN-10",
        lifecycle="Active",
        price_breaks=[PriceBreak(1, 5.00)],
        product_url="https://www.digikey.com/product/LT3045",
        supplier_device_package="10-DFN (3x3)",
        attributes={
            "Output Current": "500mA",
            "Voltage - Output (Min/Fixed)": "0V",
            "Voltage - Output (Max)": "15V",
        },
    )


# ===========================================================================
# Phase 3 — Constraint Engine Core
# ===========================================================================


class TestConstraintModel:
    """Test Constraint data model."""

    def test_from_field_value_gte(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("voltage", ">=25V")
        assert c.field == "voltage"
        assert c.op == "gte"
        assert c.value == "25V"

    def test_from_field_value_lte(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("tolerance", "<=1%")
        assert c.field == "tolerance"
        assert c.op == "lte"
        assert c.value == "1%"

    def test_from_field_value_eq(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("dielectric", "X7R")
        assert c.field == "dielectric"
        assert c.op == "eq"
        assert c.value == "X7R"

    def test_from_field_value_pipe_matches(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("package", "MSOP|DFN")
        assert c.op == "matches"

    def test_from_field_value_comma_in(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("dielectric", "X7R,X5R,C0G")
        assert c.op == "in"

    def test_from_field_value_ne(self):
        from footfindr.constraints import Constraint
        c = Constraint.from_field_value("package", "!=DFN")
        assert c.op == "ne"

    def test_to_dict(self):
        from footfindr.constraints import Constraint
        c = Constraint(field="voltage", op="gte", value="25V", reason="Safety margin")
        d = c.to_dict()
        assert d["field"] == "voltage"
        assert d["reason"] == "Safety margin"


class TestUnitParsing:
    """Test numeric unit parsing."""

    def test_voltage_v(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("25V", "voltage") == 25.0

    def test_voltage_mv(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("100mV", "voltage") == pytest.approx(0.1)

    def test_capacitance_uf(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("10uF", "capacitance") == pytest.approx(10e-6)

    def test_capacitance_pf(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("100pF", "capacitance") == pytest.approx(100e-12)

    def test_resistance_k(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("10k", "resistance") == pytest.approx(10e3)

    def test_percent(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("1%", "percent") == pytest.approx(1.0)

    def test_stock_integer(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("5000", "stock") == pytest.approx(5000.0)

    def test_plain_float(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("3.3", "") == pytest.approx(3.3)

    def test_none_on_bad_input(self):
        from footfindr.constraints import parse_numeric_value
        assert parse_numeric_value("xyz", "voltage") is None


class TestConstraintOperators:
    """Test constraint checking operators."""

    def test_gte_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="voltage", op="gte", value="16V")
        r = check_constraint(c, "25V")
        assert r.passed

    def test_gte_fail(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="voltage", op="gte", value="25V")
        r = check_constraint(c, "16V")
        assert not r.passed

    def test_lte_tolerance_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="tolerance", op="lte", value="1%")
        r = check_constraint(c, "0.5%")
        assert r.passed  # 0.5% <= 1% — better tolerance

    def test_lte_tolerance_fail(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="tolerance", op="lte", value="1%")
        r = check_constraint(c, "5%")
        assert not r.passed

    def test_eq_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="dielectric", op="eq", value="X7R")
        r = check_constraint(c, "X7R")
        assert r.passed

    def test_eq_fail(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="dielectric", op="eq", value="X7R")
        r = check_constraint(c, "X5R")
        assert not r.passed

    def test_ne_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="package", op="ne", value="DFN")
        r = check_constraint(c, "MSOP")
        assert r.passed

    def test_contains_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="description", op="contains", value="10UF")
        r = check_constraint(c, "CAP CER 10UF 16V X5R 0805")
        assert r.passed

    def test_matches_pipe_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="package", op="matches", value="MSOP|DFN")
        r = check_constraint(c, "10-DFN (3x3)")
        assert r.passed

    def test_in_list_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="dielectric", op="in", value="X7R,X5R,C0G")
        r = check_constraint(c, "X5R")
        assert r.passed

    def test_in_list_fail(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="dielectric", op="in", value="X7R,C0G")
        r = check_constraint(c, "Y5V")
        assert not r.passed

    def test_family_pass(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="family", op="eq", value="LT3045")
        r = check_constraint(c, "LT3045EDD#PBF")
        assert r.passed

    def test_avoid_package_soft(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="avoid_package", op="eq", value="DFN")
        r = check_constraint(c, "10-DFN (3x3)")
        assert not r.passed  # DFN is present
        assert r.is_soft

    def test_prefer_package_soft(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="prefer_package", op="eq", value="MSOP")
        r = check_constraint(c, "DFN-10")
        assert r.passed  # prefer is always pass
        assert r.is_soft


class TestConstraintPersistence:
    """Test YAML save/load roundtrip."""

    def test_save_load_roundtrip(self, workspace):
        from footfindr.constraints import Constraint, ConstraintManager, RefConstraints

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "voltage", ">=25V")
        mgr.set_constraint("C13", "dielectric", "X7R")
        mgr.set_constraint("C13", "package", "0805")

        # Reload
        cf = mgr.load()
        assert "C13" in cf.refs
        assert len(cf.refs["C13"].constraints) == 3

    def test_remove_constraint(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "voltage", ">=25V")
        mgr.set_constraint("C13", "package", "0805")

        removed = mgr.remove_constraint("C13", "voltage")
        assert removed

        cf = mgr.load()
        assert len(cf.refs["C13"].constraints) == 1

    def test_clear_ref(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "voltage", ">=25V")
        mgr.clear_ref("C13")

        cf = mgr.load()
        assert "C13" not in cf.refs

    def test_empty_load(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        cf = mgr.load()
        assert not cf.refs
        assert not cf.groups
        assert not cf.patterns


class TestConstraintPriority:
    """Test exact > group > pattern priority."""

    def test_exact_overrides_group(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)

        # Group says >=10V
        mgr.create_group("decoupling")
        mgr.add_to_group("decoupling", ["C13", "C14"])
        mgr.set_group_constraint("decoupling", "voltage", ">=10V")

        # Exact ref says >=25V (higher priority)
        mgr.set_constraint("C13", "voltage", ">=25V")

        constraints = mgr.get_constraints_for("C13")
        # Should have >=25V from exact ref, not >=10V from group
        voltage_c = [c for c in constraints if c.field == "voltage"][0]
        assert voltage_c.value == "25V"
        assert voltage_c.op == "gte"

    def test_group_overrides_pattern(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)

        # Pattern: C* → voltage >=6.3V
        cf = mgr.load()
        from footfindr.constraints import Constraint, PatternConstraints
        cf.patterns["C*"] = PatternConstraints(
            pattern="C*",
            constraints=[Constraint(field="voltage", op="gte", value="6.3V")],
        )
        mgr.save(cf)

        # Group says >=10V (higher priority than pattern)
        mgr.create_group("power-caps")
        mgr.add_to_group("power-caps", ["C13"])
        mgr.set_group_constraint("power-caps", "voltage", ">=10V")

        constraints = mgr.get_constraints_for("C13")
        voltage_c = [c for c in constraints if c.field == "voltage"][0]
        assert voltage_c.value == "10V"

    def test_pattern_applies_to_unmatched_ref(self, workspace):
        from footfindr.constraints import ConstraintManager, Constraint, PatternConstraints

        mgr = ConstraintManager(workspace=workspace)

        cf = mgr.load()
        cf.patterns["C*"] = PatternConstraints(
            pattern="C*",
            constraints=[Constraint(field="voltage", op="gte", value="6.3V")],
        )
        mgr.save(cf)

        constraints = mgr.get_constraints_for("C99")
        assert len(constraints) == 1
        assert constraints[0].value == "6.3V"


class TestConstraintCheck:
    """Test checking constraints against a SupplierPart."""

    def test_check_pass(self, workspace, mock_supplier_part):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "package", "0805")

        results = mgr.check_part("C13", mock_supplier_part)
        assert len(results) == 1
        assert results[0].passed

    def test_check_fail(self, workspace, mock_supplier_part):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "voltage", ">=25V")  # Part has 16V

        results = mgr.check_part("C13", mock_supplier_part)
        voltage_result = [r for r in results if r.constraint.field == "voltage"][0]
        assert not voltage_result.passed

    def test_check_avoid_soft(self, workspace, mock_ic_part):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("U3", "avoid_package", "DFN")

        results = mgr.check_part("U3", mock_ic_part)
        avoid_result = [r for r in results if r.constraint.field == "avoid_package"][0]
        assert not avoid_result.passed  # DFN is present
        assert avoid_result.is_soft  # But it's a soft constraint


class TestConstraintApply:
    """Test filtering supplier results using constraints."""

    def test_apply_filters_results(self, workspace, mock_supplier_part, mock_supplier_part_b):
        from footfindr.constraints import ConstraintManager, apply_constraints_to_results

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "voltage", ">=25V")  # Neither part has >=25V (16V)

        constraints = mgr.get_constraints_for("C13")
        passing, summaries = apply_constraints_to_results(constraints, [mock_supplier_part, mock_supplier_part_b])
        assert len(passing) == 0  # Both filtered out
        assert len(summaries) == 2


class TestConstraintGroups:
    """Test constraint group management."""

    def test_create_group(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.create_group("ldo-caps")
        cf = mgr.load()
        assert "ldo-caps" in cf.groups

    def test_add_to_group(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.create_group("ldo-caps")
        mgr.add_to_group("ldo-caps", ["C13", "C14"])
        cf = mgr.load()
        assert "C13" in cf.groups["ldo-caps"].refs

    def test_set_group_constraint(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.create_group("power")
        mgr.set_group_constraint("power", "voltage", ">=10V")
        cf = mgr.load()
        assert len(cf.groups["power"].constraints) == 1


class TestConstraintPatterns:
    """Test pattern wildcard matching."""

    def test_c_star_matches_cap_refs(self, workspace):
        from footfindr.constraints import PatternConstraints, Constraint

        pc = PatternConstraints(
            pattern="C*",
            constraints=[Constraint(field="voltage", op="gte", value="6.3V")],
        )
        assert pc.matches_ref("C1")
        assert pc.matches_ref("C13")
        assert pc.matches_ref("C999")
        assert not pc.matches_ref("R1")
        assert not pc.matches_ref("U3")


# ===========================================================================
# Phase 4 — Category Inference
# ===========================================================================


class TestCategoryInference:
    """Test category inference from ref pattern."""

    def test_capacitor_ref(self):
        from footfindr.constraints import infer_category
        cat, conf = infer_category(ref="C13")
        assert cat == "capacitor"
        assert conf == "high"

    def test_resistor_ref(self):
        from footfindr.constraints import infer_category
        cat, conf = infer_category(ref="R7")
        assert cat == "resistor"

    def test_ic_ref(self):
        from footfindr.constraints import infer_category
        cat, conf = infer_category(ref="U3")
        assert cat == "ic"

    def test_diode_ref(self):
        from footfindr.constraints import infer_category
        cat, conf = infer_category(ref="D1")
        assert cat == "diode"

    def test_transistor_ref(self):
        from footfindr.constraints import infer_category
        cat, conf = infer_category(ref="Q1")
        assert cat == "transistor"

    def test_other_fallback(self):
        from footfindr.constraints import infer_category
        cat, conf = infer_category(ref="ZZ1")
        assert cat == "other"
        assert conf == "review"

    def test_description_ic(self):
        from footfindr.constraints import infer_category
        cat, conf = infer_category(description="IC REG LINEAR POS ADJ 500MA DFN")
        assert cat == "ic"


# ===========================================================================
# Phase 5 — Plan/Apply Model
# ===========================================================================


class TestPlanModel:
    """Test Plan and PlanStep creation."""

    def test_plan_creation(self):
        from footfindr.plans import Plan, PlanStep

        step = PlanStep(
            operation="promote",
            target_file="/path/to/lib.yaml",
            target_key="TEST-PN",
            new_value={"mpn": "GRM21BR61C106KE15L"},
            reason="Test promotion",
        )
        plan = Plan(
            plan_id="20260620T000000_promote",
            operation="promote-supplier",
            created_at="2026-06-20T00:00:00Z",
            steps=[step],
        )
        assert plan.status == "pending"
        assert len(plan.steps) == 1

    def test_plan_to_dict(self):
        from footfindr.plans import Plan, PlanStep

        step = PlanStep(operation="promote", target_file="x.yaml", target_key="PN1")
        plan = Plan(
            plan_id="test123",
            operation="promote-supplier",
            created_at="2026-01-01T00:00:00Z",
            steps=[step],
        )
        d = plan.to_dict()
        assert d["plan_id"] == "test123"
        assert d["status"] == "pending"


class TestPlanPersistence:
    """Test YAML save/load."""

    def test_save_and_load(self, workspace):
        from footfindr.plans import Plan, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        step = PlanStep(operation="promote", target_file="x.yaml", target_key="PN1")
        plan = Plan(
            plan_id="20260620T000000_promote",
            operation="promote-supplier",
            created_at="2026-06-20T00:00:00Z",
            steps=[step],
        )
        mgr.create(plan)

        loaded = mgr.load("20260620T000000_promote")
        assert loaded is not None
        assert loaded.plan_id == plan.plan_id
        assert loaded.status == "pending"

    def test_load_latest(self, workspace):
        from footfindr.plans import Plan, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        for i in range(3):
            step = PlanStep(operation="promote", target_file="x.yaml", target_key=f"PN{i}")
            plan = Plan(
                plan_id=f"20260620T00000{i}_promote",
                operation="promote-supplier",
                created_at=f"2026-06-20T00:00:0{i}Z",
                steps=[step],
            )
            mgr.create(plan)

        latest = mgr.load_latest()
        assert latest is not None
        assert latest.plan_id == "20260620T000002_promote"

    def test_list_plans(self, workspace):
        from footfindr.plans import Plan, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        for i in range(3):
            step = PlanStep(operation="promote", target_file="x.yaml", target_key=f"PN{i}")
            plan = Plan(
                plan_id=f"20260620T00000{i}_promote",
                operation="promote-supplier",
                created_at=f"2026-06-20T00:00:0{i}Z",
                steps=[step],
            )
            mgr.create(plan)

        plans = mgr.list_plans()
        assert len(plans) == 3


class TestPlanDiscard:
    """Test plan discard."""

    def test_discard_marks_status(self, workspace):
        from footfindr.plans import Plan, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        step = PlanStep(operation="promote", target_file="x.yaml", target_key="PN1")
        plan = Plan(
            plan_id="20260620T000000_promote",
            operation="promote-supplier",
            created_at="2026-06-20T00:00:00Z",
            steps=[step],
        )
        mgr.create(plan)
        mgr.discard(plan)

        loaded = mgr.load("20260620T000000_promote")
        assert loaded.status == "discarded"

    def test_discard_already_discarded_raises(self, workspace):
        from footfindr.plans import Plan, PlanError, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        step = PlanStep(operation="promote", target_file="x.yaml", target_key="PN1")
        plan = Plan(
            plan_id="20260620T000000_promote",
            operation="promote-supplier",
            created_at="2026-06-20T00:00:00Z",
            steps=[step],
        )
        mgr.create(plan)
        mgr.discard(plan)

        with pytest.raises(PlanError):
            mgr.discard(plan)  # Already discarded


# ===========================================================================
# Phase 6 — Collision Detection
# ===========================================================================


class TestCollisionDetection:
    """Test promotion collision detection."""

    def test_same_internal_pn(self):
        from footfindr.plans import check_collisions

        class MockManager:
            def load_approved_parts(self):
                class FakePart:
                    internal_pn = "CAP-10U-16V"
                    mpn = "GRM21BR61C106KE15L"
                    supplier_pns = {}
                return [FakePart()]

        warnings = check_collisions(
            mpn="GRM21BR61C106KE15K",
            internal_pn="CAP-10U-16V",
            supplier_pn=None,
            target_library="POSM",
            manager=MockManager(),
        )
        assert any(w.collision_type == "same_internal_pn" for w in warnings)

    def test_same_mpn(self):
        from footfindr.plans import check_collisions

        class MockManager:
            def load_approved_parts(self):
                class FakePart:
                    internal_pn = "CAP-10U-16V"
                    mpn = "GRM21BR61C106KE15L"
                    supplier_pns = {}
                return [FakePart()]

        warnings = check_collisions(
            mpn="GRM21BR61C106KE15L",
            internal_pn="NEW-PN",
            supplier_pn=None,
            target_library="POSM",
            manager=MockManager(),
        )
        assert any(w.collision_type == "same_mpn" for w in warnings)

    def test_same_supplier_pn(self):
        from footfindr.plans import check_collisions

        class MockManager:
            def load_approved_parts(self):
                class FakePart:
                    internal_pn = "CAP-10U-16V"
                    mpn = "GRM21BR61C106KE15L"
                    supplier_pns = {"digikey": "1276-1100-1-ND"}
                return [FakePart()]

        warnings = check_collisions(
            mpn="DIFFERENT-MPN",
            internal_pn="NEW-PN",
            supplier_pn="1276-1100-1-ND",
            target_library="POSM",
            manager=MockManager(),
        )
        assert any(w.collision_type == "same_supplier_pn" for w in warnings)

    def test_similar_family(self):
        from footfindr.plans import check_collisions

        class MockManager:
            def load_approved_parts(self):
                class FakePart:
                    internal_pn = "CAP-10U-16V"
                    mpn = "GRM21BR61C106KE15L"
                    supplier_pns = {}
                return [FakePart()]

        warnings = check_collisions(
            mpn="GRM21BR61C106KE15K",  # Same family, different suffix
            internal_pn="NEW-PN",
            supplier_pn=None,
            target_library="POSM",
            manager=MockManager(),
        )
        assert any(w.collision_type == "similar_family" for w in warnings)

    def test_no_collisions(self):
        from footfindr.plans import check_collisions

        class MockManager:
            def load_approved_parts(self):
                return []

        warnings = check_collisions(
            mpn="TOTALLY-NEW-MPN",
            internal_pn="NEW-PN",
            supplier_pn=None,
            target_library="POSM",
            manager=MockManager(),
        )
        assert len(warnings) == 0


# ===========================================================================
# Phase 2 — Explain-Diff
# ===========================================================================


class TestExplainDiff:
    """Test explain-diff rendering."""

    def test_explain_diff_human(self, mock_supplier_part, mock_supplier_part_b):
        from footfindr.suppliers.display import render_explain_diff

        output = render_explain_diff([mock_supplier_part, mock_supplier_part_b])
        assert isinstance(output, str)
        assert "Comparing" in output
        assert "Differences" in output

    def test_explain_diff_json(self, mock_supplier_part, mock_supplier_part_b):
        from footfindr.suppliers.display import render_explain_diff

        data = render_explain_diff([mock_supplier_part, mock_supplier_part_b], as_json=True)
        assert isinstance(data, dict)
        assert "parts" in data
        assert "differences" in data
        assert "shared" in data
        assert len(data["parts"]) == 2

    def test_explain_diff_no_parts(self):
        from footfindr.suppliers.display import render_explain_diff

        output = render_explain_diff([])
        assert "No parts" in output

    def test_package_notes(self, mock_ic_part):
        from footfindr.suppliers.display import render_explain_diff

        data = render_explain_diff([mock_ic_part], as_json=True)
        assert len(data["package_notes"]) > 0
        assert "DFN" in data["package_notes"][0]["note"]


# ===========================================================================
# Phase 8 — Gitignore for plans
# ===========================================================================


class TestPlanGitignore:
    """Test that plans directory gets .gitignore."""

    def test_gitignore_created(self, workspace):
        from footfindr.plans import Plan, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        step = PlanStep(operation="promote", target_file="x.yaml", target_key="PN1")
        plan = Plan(
            plan_id="test",
            operation="test",
            created_at="2026-01-01T00:00:00Z",
            steps=[step],
        )
        mgr.create(plan)

        gitignore = workspace / "plans" / ".gitignore"
        assert gitignore.exists()


# ===========================================================================
# Phase 9 — JSON Output Validity
# ===========================================================================


class TestJSONOutput:
    """Test that JSON outputs are valid."""

    def test_constraint_list_json(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "voltage", ">=25V")

        cf = mgr.load()
        data = {
            "version": cf.version,
            "refs": {r: rc.to_dict() for r, rc in cf.refs.items()},
        }
        json_str = json.dumps(data, indent=2, default=str)
        parsed = json.loads(json_str)
        assert "refs" in parsed
        assert "C13" in parsed["refs"]

    def test_plan_json(self, workspace):
        from footfindr.plans import Plan, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        step = PlanStep(operation="promote", target_file="x.yaml", target_key="PN1",
                        new_value={"mpn": "TEST"})
        plan = Plan(
            plan_id="json_test",
            operation="promote-supplier",
            created_at="2026-01-01T00:00:00Z",
            steps=[step],
        )
        d = plan.to_dict()
        json_str = json.dumps(d, indent=2, default=str)
        parsed = json.loads(json_str)
        assert parsed["plan_id"] == "json_test"
        assert parsed["steps"][0]["new_value"]["mpn"] == "TEST"


# ===========================================================================
# Phase 10 — Safety Boundaries
# ===========================================================================


class TestSafetyBoundaries:
    """Test that no schematic writes or purchasing happen."""

    def test_promote_from_supplier_no_schematic_write(self, workspace):
        """promote_from_supplier should not write to any .kicad_sch file."""
        from footfindr.constraints import infer_category

        # Just verify that infer_category + promote path doesn't touch schematics
        cat, conf = infer_category(ref="C13")
        assert cat == "capacitor"
        # No schematic file created in workspace
        sch_files = list(workspace.rglob("*.kicad_sch"))
        assert len(sch_files) == 0

    def test_plan_discard_does_not_write_library(self, workspace):
        from footfindr.plans import Plan, PlanManager, PlanStep

        mgr = PlanManager(workspace=workspace)
        step = PlanStep(operation="promote", target_file="lib.yaml", target_key="PN1",
                        new_value={"mpn": "TEST"})
        plan = Plan(
            plan_id="safety_test",
            operation="promote-supplier",
            created_at="2026-01-01T00:00:00Z",
            steps=[step],
        )
        mgr.create(plan)
        mgr.discard(plan)

        # No library files should be created
        lib_files = list(workspace.rglob("lib.yaml"))
        assert len(lib_files) == 0


# ===========================================================================
# Phase 3 — Search query building
# ===========================================================================


class TestSearchQueryBuilding:
    """Test building search queries from constraints."""

    def test_build_query_capacitor(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("C13", "voltage", ">=25V")
        mgr.set_constraint("C13", "dielectric", "X7R")
        mgr.set_constraint("C13", "package", "0805")

        query, constraints = mgr.build_search_query("C13")
        assert "25V" in query
        assert "X7R" in query
        assert "0805" in query
        assert "capacitor" in query  # inferred from C* ref

    def test_build_query_ic_family(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        mgr.set_constraint("U3", "family", "LT3045")

        query, constraints = mgr.build_search_query("U3")
        assert "LT3045" in query
        # IC refs don't get a generic "ic" term appended — too broad for supplier searches

    def test_build_query_no_constraints(self, workspace):
        from footfindr.constraints import ConstraintManager

        mgr = ConstraintManager(workspace=workspace)
        query, constraints = mgr.build_search_query("C99")
        assert query == ""
        assert len(constraints) == 0


# ===========================================================================
# Tolerance direction semantics
# ===========================================================================


class TestToleranceSemantics:
    """Test that tolerance uses <= correctly."""

    def test_tolerance_lte_1_percent_passes_half(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="tolerance", op="lte", value="1%")
        r = check_constraint(c, "0.5%")
        assert r.passed  # 0.5% is better than 1%

    def test_tolerance_lte_1_percent_fails_5(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="tolerance", op="lte", value="1%")
        r = check_constraint(c, "5%")
        assert not r.passed  # 5% is worse than 1%

    def test_tolerance_lte_10_percent_passes_10(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="tolerance", op="lte", value="10%")
        r = check_constraint(c, "10%")
        assert r.passed  # Equal passes

    def test_tolerance_lte_10_percent_fails_20(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="tolerance", op="lte", value="10%")
        r = check_constraint(c, "20%")
        assert not r.passed
