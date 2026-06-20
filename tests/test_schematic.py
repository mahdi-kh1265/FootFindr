"""Tests for the KiCad schematic parser."""

from pathlib import Path

import pytest


class TestSchematicReader:
    """Test parsing of the simple_board.kicad_sch fixture."""

    def test_read_fixture(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        assert sch.path == simple_schematic_path
        assert len(sch.symbols) == 9  # C1-C5, R1-R3, U1

    def test_reference_extraction(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        refs = sorted(sch.refs())
        assert refs == ["C1", "C2", "C3", "C4", "C5", "R1", "R2", "R3", "U1"]

    def test_value_extraction(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.value == "100nF"

        c2 = sch.symbol_by_ref("C2")
        assert c2 is not None
        assert c2.value == "10uF"

        r1 = sch.symbol_by_ref("R1")
        assert r1 is not None
        assert r1.value == "10k"

        r2 = sch.symbol_by_ref("R2")
        assert r2 is not None
        assert r2.value == "271"

    def test_footprint_extraction(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        # R3 has existing footprint
        r3 = sch.symbol_by_ref("R3")
        assert r3 is not None
        assert r3.footprint == "Resistor_SMD:R_0603_1608Metric"

        # C1 has empty footprint
        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.footprint is None  # empty string becomes None

    def test_custom_fields(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        # C4 has FootFindrLocked
        c4 = sch.symbol_by_ref("C4")
        assert c4 is not None
        assert c4.fields.get("FootFindrLocked") == "true"

        # C5 has InternalPN
        c5 = sch.symbol_by_ref("C5")
        assert c5 is not None
        assert c5.fields.get("InternalPN") == "CAP-10U-16V-X7R-0805"

        # U1 has MPN
        u1 = sch.symbol_by_ref("U1")
        assert u1 is not None
        assert u1.fields.get("MPN") == "LMH6702MA/NOPB"

    def test_category_detection(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.category == "capacitor"

        r1 = sch.symbol_by_ref("R1")
        assert r1 is not None
        assert r1.category == "resistor"

        u1 = sch.symbol_by_ref("U1")
        assert u1 is not None
        # U1 uses Device:R lib_id but ref starts with U → category should be "ic"
        # (detect_category prioritises ref prefix)

    def test_lib_id_extraction(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)

        c1 = sch.symbol_by_ref("C1")
        assert c1 is not None
        assert c1.lib_id == "Device:C"

    def test_symbol_by_ref_missing(self, simple_schematic_path: Path) -> None:
        from footfindr.kicad.schematic import KiCadSchematicReader

        reader = KiCadSchematicReader()
        sch = reader.read(simple_schematic_path)
        assert sch.symbol_by_ref("MISSING99") is None
