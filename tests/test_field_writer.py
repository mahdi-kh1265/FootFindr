"""Tests for the KiCad field writer."""

from pathlib import Path

import pytest


class TestFieldWriter:
    """Test targeted text-level property writing."""

    def test_backup_created(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.field_writer import KiCadFieldWriter

        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {"C1": {"FootFindrStatus": "TEST"}},
            backup=True,
        )

        bak = simple_schematic_path.with_name(simple_schematic_path.name + ".footfindr.bak")
        assert bak.exists()

    def test_update_existing_field(self, simple_schematic_path: Path) -> None:
        """Updating an existing empty Footprint field should work."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        writer = KiCadFieldWriter()
        # C1 has empty Footprint — should be writeable without force
        writer.update_fields(
            simple_schematic_path,
            {"C1": {"Footprint": "Capacitor_SMD:C_0603_1608Metric"}},
            backup=True,
            force=False,
        )

        # Re-read and verify
        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.footprint == "Capacitor_SMD:C_0603_1608Metric"

    def test_no_overwrite_without_force(self, simple_schematic_path: Path) -> None:
        """Existing non-empty Footprint should NOT be overwritten without force."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        writer = KiCadFieldWriter()
        results = writer.update_fields(
            simple_schematic_path,
            {"R3": {"Footprint": "Resistor_SMD:R_0805_2012Metric"}},
            backup=True,
            force=False,
        )

        # R3 already has a footprint — it should be skipped, not error
        assert results.get("R3") is None

        # Verify original is preserved
        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        r3 = sch.symbol_by_ref("R3")
        assert r3 is not None
        assert r3.footprint == "Resistor_SMD:R_0603_1608Metric"

    def test_overwrite_with_force(self, simple_schematic_path: Path) -> None:
        """With force=True, existing footprint should be overwritten."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {"R3": {"Footprint": "Resistor_SMD:R_0805_2012Metric"}},
            backup=True,
            force=True,
        )

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        r3 = sch.symbol_by_ref("R3")
        assert r3 is not None
        assert r3.footprint == "Resistor_SMD:R_0805_2012Metric"

    def test_locked_component_blocked(self, simple_schematic_path: Path) -> None:
        """Locked components should not be written to."""
        from footfindr.kicad.field_writer import KiCadFieldWriter

        writer = KiCadFieldWriter()
        results = writer.update_fields(
            simple_schematic_path,
            {"C4": {"Footprint": "Capacitor_SMD:C_0805_2012Metric"}},
            backup=True,
        )

        assert results.get("C4") is not None  # Should have error message
        assert "locked" in results["C4"].lower()

    def test_missing_ref_error(self, simple_schematic_path: Path) -> None:
        """Writing to a non-existent ref should return an error."""
        from footfindr.kicad.field_writer import KiCadFieldWriter

        writer = KiCadFieldWriter()
        results = writer.update_fields(
            simple_schematic_path,
            {"MISSING99": {"Footprint": "test"}},
            backup=True,
        )

        assert results.get("MISSING99") is not None
        assert "not found" in results["MISSING99"].lower()

    def test_add_new_field(self, simple_schematic_path: Path) -> None:
        """Adding a new property that doesn't exist should insert it."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {"C1": {"MPN": "GRM188R71C104KA01D"}},
            backup=True,
        )

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.fields.get("MPN") == "GRM188R71C104KA01D"

    def test_file_integrity_after_write(self, simple_schematic_path: Path) -> None:
        """After writing, the file should still be parseable."""
        from footfindr.kicad.field_writer import KiCadFieldWriter
        from footfindr.kicad.schematic import KiCadSchematicReader

        writer = KiCadFieldWriter()
        writer.update_fields(
            simple_schematic_path,
            {
                "C1": {"Footprint": "Capacitor_SMD:C_0603_1608Metric", "InternalPN": "CAP-100N-16V-X7R-0603"},
                "R1": {"Footprint": "Resistor_SMD:R_0603_1608Metric"},
            },
            backup=True,
        )

        # Should still parse cleanly
        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        assert len(sch.symbols) == 9  # Same number of symbols
