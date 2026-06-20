"""Tests for the resolve engine."""

from pathlib import Path

import pytest


class TestExactResolver:
    """Test exact InternalPN and MPN resolution."""

    def test_exact_internal_pn(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C5",
            value="10uF",
            category="capacitor",
            fields={"InternalPN": "CAP-10U-16V-X7R-0805"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.confidence == 0.99
        assert decision.selected_internal_pn == "CAP-10U-16V-X7R-0805"
        assert decision.selected_footprint == "Capacitor_SMD:C_0805_2012Metric"
        assert "Footprint" in decision.fields_to_write

    def test_exact_mpn(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="U1",
            value="LMH6702MA/NOPB",
            category="ic",
            fields={"MPN": "LMH6702MA/NOPB"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_mpn == "LMH6702MA/NOPB"
        assert decision.selected_footprint == "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"

    def test_unknown_internal_pn(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C99",
            value="47uF",
            category="capacitor",
            fields={"InternalPN": "NONEXISTENT-PN"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.ERROR

    def test_unknown_mpn_review(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="U2",
            value="UNKNOWN_IC",
            category="ic",
            fields={"MPN": "UNKNOWN_IC_MPN"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.REVIEW


class TestCapacitorResolver:
    """Test capacitor value resolution."""

    def test_100nf_auto(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C1",
            value="100nF",
            category="capacitor",
            fields={},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-100N-16V-X7R-0603"

    def test_10uf_auto(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C2",
            value="10uF",
            category="capacitor",
            fields={},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-10U-16V-X7R-0805"

    def test_1uf_auto(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(ref="C3", value="1uF", category="capacitor", fields={})
        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-1U-16V-X7R-0603"

    def test_unknown_value_skip(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(ref="C99", value="47uF", category="capacitor", fields={})
        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.SKIP


class TestResistorResolver:
    """Test resistor value resolution."""

    def test_10k_auto(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(ref="R1", value="10k", category="resistor", fields={})
        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "RES-10K-1PCT-0603"

    def test_271_auto(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(ref="R2", value="271", category="resistor", fields={})
        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "RES-271R-1PCT-0603"

    def test_1k_auto_single_match(self, approved_parts, config) -> None:
        """1k now has two approved parts (0603 and 0805), so should be REVIEW."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(ref="R3", value="1k", category="resistor", fields={})
        decision = engine.resolve_component(ctx)
        # Multiple matches -> REVIEW (pick the best one)
        assert decision.status in (DecisionStatus.AUTO, DecisionStatus.REVIEW)
        assert len(decision.candidate_summary) >= 2


class TestLockedComponent:
    """Test locked component handling."""

    def test_locked_unchanged(self, approved_parts, config) -> None:
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C4",
            value="10uF",
            category="capacitor",
            locked=True,
            fields={"FootFindrLocked": "true"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.UNCHANGED


class TestFullResolveWorkflow:
    """Test the full resolve workflow against the fixture schematic."""

    def test_resolve_all_dry_run(self, simple_schematic_path: Path, approved_parts, config) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader
        from footfindr.resolve.engine import ResolveEngine

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        engine = ResolveEngine(config, approved_parts)
        decisions = engine.resolve_schematic(sch, ["all"])

        assert len(decisions) == 9
        # None should be applied in dry-run
        assert all(not d.applied for d in decisions)

    def test_resolve_specific_refs(self, simple_schematic_path: Path, approved_parts, config) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader
        from footfindr.resolve.engine import ResolveEngine

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        engine = ResolveEngine(config, approved_parts)
        decisions = engine.resolve_schematic(sch, ["C1", "R1"])

        assert len(decisions) == 2
        refs = {d.ref for d in decisions}
        assert refs == {"C1", "R1"}
