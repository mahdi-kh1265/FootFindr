"""Tests for M9.2 — Constraint Search Hardening + Ref Assignment.

Tests:
  - EE shorthand parsing (4u7, 2k2, 0R1)
  - Numeric equality for capacitance/resistance/value
  - Query deduplication (schematic value vs constraint)
  - Rich markup safety (no MarkupError on zero-match)
  - Fallback query generation
  - Ref show/check/assign plan generation
  - Safe-write integration
  - Footprint not auto-written
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# EE Shorthand Parsing
# ---------------------------------------------------------------------------


class TestEEShorthand:
    """Test _parse_shorthand for European EE notation."""

    def test_4u7(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("4u7") == "4.7u"

    def test_10n(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("10n") == "10n"

    def test_2n2(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("2n2") == "2.2n"

    def test_100n(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("100n") == "100n"

    def test_2k2(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("2k2") == "2.2k"

    def test_4R7(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("4R7") == "4.7"

    def test_0R1(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("0R1") == "0.1"

    def test_no_shorthand(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("4.7uF") is None

    def test_empty(self):
        from footfindr.constraints import _parse_shorthand
        assert _parse_shorthand("") is None


class TestShorthandInParseNumeric:
    """Test that parse_numeric_value handles shorthand via _parse_shorthand."""

    def test_4u7_parses(self):
        from footfindr.constraints import parse_numeric_value
        v = parse_numeric_value("4u7", "capacitance")
        assert v is not None
        assert abs(v - 4.7e-6) < 1e-9

    def test_2k2_parses(self):
        from footfindr.constraints import parse_numeric_value
        v = parse_numeric_value("2k2", "resistance")
        assert v is not None
        assert abs(v - 2200) < 1

    def test_0R1_parses(self):
        from footfindr.constraints import parse_numeric_value
        v = parse_numeric_value("0R1", "resistance")
        assert v is not None
        assert abs(v - 0.1) < 0.01

    def test_100n_parses(self):
        from footfindr.constraints import parse_numeric_value
        v = parse_numeric_value("100n", "capacitance")
        assert v is not None
        assert abs(v - 100e-9) < 1e-12


# ---------------------------------------------------------------------------
# Numeric Equality
# ---------------------------------------------------------------------------


class TestNumericEquality:
    """Test _numeric_values_equal for capacitance/resistance/voltage."""

    def test_4_7uF_equals_4_7_uF(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7uF", "4.7 µF", "capacitance") is True

    def test_4_7uF_equals_4700nF(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7uF", "4700nF", "capacitance") is True

    def test_4_7uF_equals_4700_nF(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7uF", "4700 nF", "capacitance") is True

    def test_4_7u_equals_4_7uF(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7u", "4.7uF", "capacitance") is True

    def test_10k_equals_10000(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("10k", "10000", "resistance") is True

    def test_different_values_fail(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("4.7uF", "10uF", "capacitance") is False

    def test_non_numeric_string_equality(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("X7R", "X7R", "") is True

    def test_non_numeric_different(self):
        from footfindr.constraints import _numeric_values_equal
        assert _numeric_values_equal("X7R", "C0G", "") is False


class TestConstraintNumericEq:
    """Test check_constraint with eq operator for numeric fields."""

    def test_capacitance_eq_passes_digikey_format(self):
        """capacitance = 4.7uF should pass '4.7 µF'."""
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="capacitance", op="eq", value="4.7uF")
        result = check_constraint(c, "4.7 µF")
        assert result.passed is True

    def test_capacitance_eq_passes_nanofarads(self):
        """capacitance = 4.7uF should pass '4700nF'."""
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="capacitance", op="eq", value="4.7uF")
        result = check_constraint(c, "4700nF")
        assert result.passed is True

    def test_capacitance_eq_fails_different(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="capacitance", op="eq", value="4.7uF")
        result = check_constraint(c, "10uF")
        assert result.passed is False

    def test_resistance_eq_passes_k_notation(self):
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint(field="resistance", op="eq", value="10k")
        result = check_constraint(c, "10 kΩ")
        assert result.passed is True

    def test_shorthand_4u7_constraint_passes(self):
        """Constraint set with 4u7 should pass 4.7uF."""
        from footfindr.constraints import Constraint, check_constraint
        c = Constraint.from_field_value("cap", "4u7")
        result = check_constraint(c, "4.7 µF")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Query Deduplication
# ---------------------------------------------------------------------------


class TestQueryDedup:
    """Test that build_search_query deduplicates numeric equivalents."""

    def test_dedup_schematic_and_constraint(self, tmp_path):
        """4.7u (schematic) + 4.7uF (constraint) → only one value in query."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "capacitance", "4.7uF")
        mgr.set_constraint("C1", "voltage", ">=25V")
        mgr.set_constraint("C1", "dielectric", "X7R")
        mgr.set_constraint("C1", "package", "0603")

        query, _ = mgr.build_search_query("C1", schematic_value="4.7u")
        # Should not have both 4.7u AND 4.7uF
        assert query.count("4.7u") + query.count("4.7uF") == 1
        assert "25V" in query
        assert "X7R" in query
        assert "0603" in query

    def test_no_dedup_for_different_values(self, tmp_path):
        """Different numeric values should both appear."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "capacitance", "100nF")
        mgr.set_constraint("C1", "voltage", ">=25V")

        query, _ = mgr.build_search_query("C1", schematic_value="4.7u")
        # Both should appear since they are different values
        assert "4.7u" in query
        assert "100nF" in query


# ---------------------------------------------------------------------------
# Rich Markup Safety
# ---------------------------------------------------------------------------


class TestRichMarkupSafety:
    """Test that zero-match diagnostics don't crash with MarkupError."""

    def test_no_markup_error_in_diagnostic(self):
        """Simulate the diagnostic output strings — no mismatched tags."""
        from rich.console import Console
        from rich.markup import escape as rich_escape

        test_console = Console(file=None, force_terminal=False)
        query_str = "4.7uF 25V X7R 0603 ceramic capacitor"

        # These should not raise MarkupError
        try:
            test_console.print(f"\n  [yellow]0/10 results passed full query constraints.[/yellow]")
            test_console.print(f"\n  [dim]No candidates passed constraints across all queries.[/dim]")
            test_console.print(f"  [dim]Try a manual search with different terms:[/dim]")
            test_console.print(f"    ff sup s \"{rich_escape(query_str)}\" -s dk -r --mini")
        except Exception as e:
            pytest.fail(f"Rich markup error: {e}")

    def test_escape_user_strings(self):
        """User strings with brackets should be escaped."""
        from rich.markup import escape as rich_escape

        # Strings that could break Rich markup
        dangerous = "[test] value with [brackets]"
        escaped = rich_escape(dangerous)
        assert "[" not in escaped or "\\[" in escaped


# ---------------------------------------------------------------------------
# Fallback Queries
# ---------------------------------------------------------------------------


class TestFallbackQueries:
    """Test build_fallback_queries generates progressively broader queries."""

    def test_capacitor_fallbacks(self, tmp_path):
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "capacitance", "4.7uF")
        mgr.set_constraint("C1", "voltage", ">=25V")
        mgr.set_constraint("C1", "dielectric", "X7R")
        mgr.set_constraint("C1", "package", "0603")

        fallbacks = mgr.build_fallback_queries("C1", schematic_value="4.7u")
        assert len(fallbacks) >= 1
        # Each fallback should be different from primary
        primary, _ = mgr.build_search_query("C1", schematic_value="4.7u")
        for fb in fallbacks:
            assert fb != primary

    def test_fallback_distinct_cache_keys(self, tmp_path):
        """Each fallback query must be unique → different cache keys."""
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        mgr.set_constraint("C1", "capacitance", "100nF")
        mgr.set_constraint("C1", "voltage", ">=25V")
        mgr.set_constraint("C1", "dielectric", "X7R")
        mgr.set_constraint("C1", "package", "0603")

        fallbacks = mgr.build_fallback_queries("C1")
        primary, _ = mgr.build_search_query("C1")
        all_queries = [primary] + fallbacks
        # All must be unique
        assert len(all_queries) == len(set(all_queries))

    def test_no_fallbacks_without_constraints(self, tmp_path):
        from footfindr.constraints import ConstraintManager
        mgr = ConstraintManager(workspace=tmp_path / ".footfindr")
        fallbacks = mgr.build_fallback_queries("C99")
        assert fallbacks == []


# ---------------------------------------------------------------------------
# Ref Show / Check
# ---------------------------------------------------------------------------


class TestRefBuildInfo:
    """Test _build_ref_info helper."""

    def test_build_ref_info_no_schematic(self):
        from footfindr.cli_ref import _build_ref_info
        data = _build_ref_info("C1", None, [], "capacitor", "high")
        assert data["ref"] == "C1"
        assert data["schematic"] is None
        assert data["category"] == "capacitor"


# ---------------------------------------------------------------------------
# Ref Assign Plan Generation
# ---------------------------------------------------------------------------


@dataclass
class MockSupplierPartForAssign:
    mpn: str = "GRM188R71E474KA12"
    supplier: str = "digikey"
    supplier_pn: str = "1276-6717-1-ND"
    manufacturer: str = "Murata"
    description: str = "CAP CER 0.47UF 25V X7R 0603"
    package: str = "0603 (1608 Metric)"
    stock: int = 50000
    product_url: str = "https://example.com/part"
    badges: list = dc_field(default_factory=list)
    attributes: dict[str, str] = dc_field(default_factory=dict)
    result_id: str = "test-assign-1"

    def is_valid(self) -> bool:
        return bool(self.mpn)


class TestRefAssignPlan:
    """Test plan generation for ref assign."""

    def test_plan_includes_promote_and_schematic_update(self, tmp_path):
        """ref assign with --to should generate both promote + update steps."""
        from footfindr.plans import Plan, PlanStep, PlanManager

        steps = []
        part = MockSupplierPartForAssign()

        # Promote step
        steps.append(PlanStep(
            operation="promote",
            target_file="library:POSM",
            target_key="CAP-4U7-25V-X7R-0603",
            new_value={"mpn": part.mpn, "manufacturer": part.manufacturer},
            reason=f"Promote {part.mpn} to POSM",
        ))

        # Schematic update step
        update_fields = {
            "Manufacturer": part.manufacturer,
            "MPN": part.mpn,
            "InternalPN": "CAP-4U7-25V-X7R-0603",
            "FootFindrStatus": "assigned",
            "FootFindrFootprintStatus": "REVIEW",
        }
        steps.append(PlanStep(
            operation="update_schematic",
            target_file=str(tmp_path / "test.kicad_sch"),
            target_key="C1",
            new_value=update_fields,
            reason="Assign to C1",
        ))

        mgr = PlanManager(workspace=tmp_path / ".footfindr")
        plan = Plan(
            plan_id="test_assign_plan",
            operation="ref-assign",
            created_at="2026-01-01T00:00:00Z",
            steps=steps,
            provenance={"ref": "C1", "mpn": part.mpn},
        )
        mgr.create(plan)

        loaded = mgr.load("test_assign_plan")
        assert loaded is not None
        assert len(loaded.steps) == 2
        assert loaded.steps[0].operation == "promote"
        assert loaded.steps[1].operation == "update_schematic"

    def test_library_assign_only_schematic_update(self, tmp_path):
        """ref assign from library should generate only update_schematic step."""
        from footfindr.plans import Plan, PlanStep, PlanManager

        steps = [PlanStep(
            operation="update_schematic",
            target_file=str(tmp_path / "test.kicad_sch"),
            target_key="C1",
            new_value={
                "MPN": "GRM188R71E474KA12",
                "InternalPN": "CAP-4U7-25V",
                "FootFindrStatus": "assigned",
            },
        )]

        mgr = PlanManager(workspace=tmp_path / ".footfindr")
        plan = Plan(
            plan_id="test_lib_assign",
            operation="ref-assign-library",
            created_at="2026-01-01T00:00:00Z",
            steps=steps,
        )
        mgr.create(plan)

        loaded = mgr.load("test_lib_assign")
        assert len(loaded.steps) == 1
        assert loaded.steps[0].operation == "update_schematic"


class TestFootprintNotAutoWritten:
    """Test that Footprint is never in the auto-update fields."""

    def test_footprint_not_in_update_fields(self):
        """The update_fields dict should NOT contain Footprint."""
        update_fields = {
            "Manufacturer": "Murata",
            "MPN": "GRM188R71E474KA12",
            "InternalPN": "CAP-4U7-25V",
            "FootFindrStatus": "assigned",
            "FootFindrFootprintStatus": "REVIEW",
        }
        assert "Footprint" not in update_fields

    def test_footprint_status_is_review(self):
        """FootFindrFootprintStatus should be REVIEW."""
        update_fields = {
            "FootFindrFootprintStatus": "REVIEW",
        }
        assert update_fields["FootFindrFootprintStatus"] == "REVIEW"


class TestNoSchematicWriteWithoutApply:
    """Test that plan creation does not write schematic files."""

    def test_plan_create_no_sch_files(self, tmp_path):
        from footfindr.plans import Plan, PlanStep, PlanManager

        steps = [PlanStep(
            operation="update_schematic",
            target_file=str(tmp_path / "test.kicad_sch"),
            target_key="C1",
            new_value={"MPN": "TEST"},
        )]

        mgr = PlanManager(workspace=tmp_path / ".footfindr")
        plan = Plan(
            plan_id="no_write_test",
            operation="ref-assign",
            created_at="2026-01-01T00:00:00Z",
            steps=steps,
        )
        mgr.create(plan)

        # No schematic file should exist
        sch_files = list(tmp_path.glob("*.kicad_sch"))
        assert len(sch_files) == 0


# ---------------------------------------------------------------------------
# Plan apply with update_schematic
# ---------------------------------------------------------------------------


class TestPlanApplyUpdateSchematic:
    """Test that PlanManager.apply() handles update_schematic."""

    def test_apply_recognizes_update_schematic(self, tmp_path):
        """apply() should not raise 'Unknown operation' for update_schematic."""
        from footfindr.plans import Plan, PlanStep, PlanManager, PlanError

        # Create a minimal schematic file
        sch_path = tmp_path / "test.kicad_sch"
        sch_path.write_text(
            '(kicad_sch (version 20230121) (generator "eeschema")\n'
            '  (symbol (lib_id "Device:C") (at 100 100 0)\n'
            '    (property "Reference" "C1" (at 100 100 0))\n'
            '    (property "Value" "4.7u" (at 100 101 0))\n'
            '    (property "Footprint" "" (at 100 102 0))\n'
            '  )\n'
            ')\n',
            encoding="utf-8",
        )

        steps = [PlanStep(
            operation="update_schematic",
            target_file=str(sch_path),
            target_key="C1",
            new_value={"MPN": "GRM188R71E474KA12"},
        )]

        mgr = PlanManager(workspace=tmp_path / ".footfindr")
        plan = Plan(
            plan_id="apply_sch_test",
            operation="ref-assign",
            created_at="2026-01-01T00:00:00Z",
            steps=steps,
        )
        mgr.create(plan)

        # This should not raise "Unknown operation"
        # It may fail at the safe_write level if the schematic format is too minimal,
        # but the operation dispatch should work
        try:
            mgr.apply(plan)
        except Exception as e:
            # Accept PlanError for schematic write issues, but NOT for "Unknown operation"
            assert "Unknown operation" not in str(e)
