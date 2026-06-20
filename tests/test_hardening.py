"""Phase B: Hardened field writer tests + Phase D: Capacitor constraint tests."""

from pathlib import Path

import pytest


# ========================================================================
# Phase B: Field writer hardening
# ========================================================================


class TestWriterHardening:
    """Additional writer safety tests (Phase B items 1-7)."""

    def test_c1_c10_isolation(self, simple_schematic_path: Path) -> None:
        """B5: Updating C1 must not touch C10 (similar names)."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        # First, insert a C10 symbol into the schematic
        text = simple_schematic_path.read_text(encoding="utf-8")
        c10_block = '''
  (symbol (lib_id "Device:C") (at 200 80 0) (unit 1)
    (in_bom yes) (on_board yes) (dnp no)
    (uuid "10101010-1010-1010-1010-101010101010")
    (property "Reference" "C10" (at 201.27 77.46 0)
      (effects (font (size 1.27 1.27)) (justify left))
    )
    (property "Value" "4.7uF" (at 201.27 82.54 0)
      (effects (font (size 1.27 1.27)) (justify left))
    )
    (property "Footprint" "" (at 200 80 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "~" (at 200 80 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (pin "1" (uuid "c10-pin1-uuid"))
    (pin "2" (uuid "c10-pin2-uuid"))
  )
'''
        # Insert before the closing paren
        text = text.rstrip()
        if text.endswith(")"):
            text = text[:-1] + c10_block + "\n)"
        simple_schematic_path.write_text(text, encoding="utf-8")

        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {"C1": {"Footprint": "Capacitor_SMD:C_0603_1608Metric"}},
            backup=True,
        )

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.footprint == "Capacitor_SMD:C_0603_1608Metric"

        c10 = sch.symbol_by_ref("C10")
        assert c10 is not None
        assert c10.footprint is None  # C10 must be untouched

    def test_existing_fields_preserved(self, simple_schematic_path: Path) -> None:
        """B4: Updating Footprint should not clobber existing MPN/InternalPN."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        # C5 has InternalPN already
        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {"C5": {"Footprint": "Capacitor_SMD:C_0805_2012Metric"}},
            backup=True,
        )

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        c5 = sch.symbol_by_ref("C5")
        assert c5 is not None
        assert c5.footprint == "Capacitor_SMD:C_0805_2012Metric"
        assert c5.fields.get("InternalPN") == "CAP-10U-16V-X7R-0805"

    def test_multi_field_update_selective(self, simple_schematic_path: Path) -> None:
        """B4: Writer should update only intended fields when given multiple."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {"U1": {"Footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Manufacturer": "TI"}},
            backup=True,
        )

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        u1 = sch.symbol_by_ref("U1")
        assert u1 is not None
        assert u1.footprint == "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
        assert u1.fields.get("Manufacturer") == "TI"
        # Original MPN field must be preserved
        assert u1.fields.get("MPN") == "LMH6702MA/NOPB"

    def test_missing_footprint_property_insert(self, simple_schematic_path: Path) -> None:
        """B3: If symbol has empty Footprint property, writer inserts value cleanly."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        # R1 has empty Footprint
        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {"R1": {"Footprint": "Resistor_SMD:R_0603_1608Metric"}},
            backup=True,
        )

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        r1 = sch.symbol_by_ref("R1")
        assert r1 is not None
        assert r1.footprint == "Resistor_SMD:R_0603_1608Metric"

    def test_unknown_ref_produces_error(self, simple_schematic_path: Path) -> None:
        """B6: Unknown reference produces ERROR, no file corruption."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        original_text = simple_schematic_path.read_text(encoding="utf-8")

        writer = KiCadFieldWriter()
        results = writer.update_fields(
            simple_schematic_path,
            {"ZZZZZ99": {"Footprint": "test"}},
            backup=True,
        )

        # Should report error
        assert "ZZZZZ99" in results
        assert results["ZZZZZ99"] is not None
        assert "not found" in results["ZZZZZ99"].lower()

        # File should be unchanged (no partial writes)
        after_text = simple_schematic_path.read_text(encoding="utf-8")
        assert after_text == original_text

    def test_atomic_multi_ref_write(self, simple_schematic_path: Path) -> None:
        """B7: Writing multiple refs should be atomic enough that a partial
        failure doesn't corrupt the file."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        writer = KiCadFieldWriter()
        results = writer.update_fields(
            simple_schematic_path,
            {
                "C1": {"Footprint": "Capacitor_SMD:C_0603_1608Metric"},
                "C2": {"Footprint": "Capacitor_SMD:C_0805_2012Metric"},
                "NONEXISTENT": {"Footprint": "should_fail"},
            },
            backup=True,
        )

        # NONEXISTENT should fail
        assert "NONEXISTENT" in results
        assert results["NONEXISTENT"] is not None

        # But C1 and C2 should still be written (writer processes valid refs)
        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.footprint == "Capacitor_SMD:C_0603_1608Metric"

    def test_resolve_apply_respects_force(self, simple_schematic_path: Path) -> None:
        """B1: ff resolve --apply must not overwrite non-empty Footprint without --force."""
        from footfindr.resolve.engine import apply_decisions
        from footfindr.core.models import Decision, DecisionStatus
        from footfindr.kicad.schematic import KiCadSchematicReader

        # R3 has existing footprint
        d = Decision(
            ref="R3",
            status=DecisionStatus.AUTO,
            confidence=0.99,
            selected_footprint="Resistor_SMD:R_0805_2012Metric",
            fields_to_write={"Footprint": "Resistor_SMD:R_0805_2012Metric"},
            component_value="1k",
            component_category="resistor",
        )

        # Without force -- should NOT overwrite
        results = apply_decisions(simple_schematic_path, [d], force=False)

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        r3 = sch.symbol_by_ref("R3")
        assert r3 is not None
        assert r3.footprint == "Resistor_SMD:R_0603_1608Metric"  # Original preserved

    def test_locked_component_no_resolve_apply(self, simple_schematic_path: Path) -> None:
        """B2: Locked component should not be modified by resolve --apply."""
        from footfindr.resolve.engine import run_resolve
        from footfindr.kicad.schematic import KiCadSchematicReader

        decisions = run_resolve(
            simple_schematic_path,
            ["C4"],
            apply=True,
            force=False,
        )

        assert len(decisions) == 1
        assert decisions[0].status.value == "UNCHANGED"
        assert not decisions[0].applied

        # Verify file is untouched for C4
        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        c4 = sch.symbol_by_ref("C4")
        assert c4 is not None
        assert c4.footprint is None  # Still empty


# ========================================================================
# Phase D: Capacitor voltage/package/dielectric/tolerance constraints
# ========================================================================


class TestCapacitorConstraints:
    """Test capacitor resolution with explicit constraint fields."""

    def test_10uf_with_voltage_min_16v(self, approved_parts, config) -> None:
        """10uF with VoltageMin=16V should select the 16V part."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C2",
            value="10uF",
            category="capacitor",
            fields={"VoltageMin": "16V"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-10U-16V-X7R-0805"

    def test_10uf_with_package_hint_0805(self, approved_parts, config) -> None:
        """10uF with PackageHint=0805 should select the 0805 part."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C2",
            value="10uF",
            category="capacitor",
            fields={"PackageHint": "0805"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-10U-16V-X7R-0805"

    def test_10uf_with_incompatible_voltage_100v(self, approved_parts, config) -> None:
        """10uF with VoltageMin=100V should not auto-select (no 100V part)."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C2",
            value="10uF",
            category="capacitor",
            fields={"VoltageMin": "100V"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.REVIEW
        assert any("constraint" in r.lower() or "100V" in r for r in decision.reasons)

    def test_100nf_no_constraints_still_works(self, approved_parts, config) -> None:
        """100nF without extra fields should still resolve as before."""
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

    def test_10uf_with_dielectric_x7r(self, approved_parts, config) -> None:
        """10uF with Dielectric=X7R should match X7R parts."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C2",
            value="10uF",
            category="capacitor",
            fields={"Dielectric": "X7R"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-10U-16V-X7R-0805"

    def test_10uf_with_dielectric_c0g_no_match(self, approved_parts, config) -> None:
        """10uF with Dielectric=C0G should not match (no C0G 10uF in library)."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C2",
            value="10uF",
            category="capacitor",
            fields={"Dielectric": "C0G"},
        )

        decision = engine.resolve_component(ctx)
        # Should be REVIEW since base candidates exist but constraint fails
        assert decision.status == DecisionStatus.REVIEW

    def test_combined_constraints_narrow_to_one(self, approved_parts, config) -> None:
        """10uF with VoltageMin=16V + PackageHint=0805 + Dielectric=X7R should select exactly one."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C2",
            value="10uF",
            category="capacitor",
            fields={
                "VoltageMin": "16V",
                "PackageHint": "0805",
                "Dielectric": "X7R",
            },
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-10U-16V-X7R-0805"

    def test_tolerance_filter(self, approved_parts, config) -> None:
        """100nF with Tolerance=10% should match the 10% part."""
        from footfindr.core.models import ComponentContext, DecisionStatus
        from footfindr.resolve.engine import ResolveEngine

        engine = ResolveEngine(config, approved_parts)

        ctx = ComponentContext(
            ref="C1",
            value="100nF",
            category="capacitor",
            fields={"Tolerance": "10%"},
        )

        decision = engine.resolve_component(ctx)
        assert decision.status == DecisionStatus.AUTO
        assert decision.selected_internal_pn == "CAP-100N-16V-X7R-0603"
