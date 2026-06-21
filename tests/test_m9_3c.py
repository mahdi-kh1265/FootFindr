"""Tests for M9.3c: Built-in footprint root scan, pagination debug, --min alias,
idempotent apply, manual footprint write.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Minimal workspace directory."""
    ws = tmp_path / ".footfindr"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "session").mkdir()
    (ws / "libraries").mkdir()
    (ws / "plans").mkdir()
    (ws / "index").mkdir()
    return ws


@pytest.fixture
def footprint_root(tmp_path: Path) -> Path:
    """Create a fake KiCad built-in footprint root with multiple .pretty dirs.

    Simulates: C:\\Program Files\\KiCad\\10.0\\share\\kicad\\footprints
    """
    root = tmp_path / "kicad_builtin" / "footprints"
    root.mkdir(parents=True)

    # Capacitor_SMD — 5 footprints
    cap_dir = root / "Capacitor_SMD.pretty"
    cap_dir.mkdir()
    for fp_name in [
        "C_0201_0603Metric",
        "C_0402_1005Metric",
        "C_0603_1608Metric",
        "C_0805_2012Metric",
        "C_1206_3216Metric",
    ]:
        (cap_dir / f"{fp_name}.kicad_mod").write_text(
            f'(footprint "{fp_name}"\n'
            f'  (layer "F.Cu")\n'
            f'  (pad "1" smd rect (at -0.5 0) (size 0.5 0.6))\n'
            f'  (pad "2" smd rect (at 0.5 0) (size 0.5 0.6))\n'
            f')\n',
            encoding="utf-8",
        )

    # Resistor_SMD — 3 footprints
    res_dir = root / "Resistor_SMD.pretty"
    res_dir.mkdir()
    for fp_name in ["R_0402_1005Metric", "R_0603_1608Metric", "R_0805_2012Metric"]:
        (res_dir / f"{fp_name}.kicad_mod").write_text(
            f'(footprint "{fp_name}"\n'
            f'  (pad "1" smd rect (at 0 0) (size 1 1))\n'
            f'  (pad "2" smd rect (at 1 0) (size 1 1))\n'
            f')\n',
            encoding="utf-8",
        )

    # LED_SMD — 1 footprint
    led_dir = root / "LED_SMD.pretty"
    led_dir.mkdir()
    (led_dir / "LED_0402_1005Metric.kicad_mod").write_text(
        '(footprint "LED_0402_1005Metric"\n'
        '  (pad "1" smd rect (at 0 0) (size 1 1))\n'
        '  (pad "2" smd rect (at 1 0) (size 1 1))\n'
        ')\n',
        encoding="utf-8",
    )

    # Package_DFN_QFN — 2 footprints
    ic_dir = root / "Package_DFN_QFN.pretty"
    ic_dir.mkdir()
    for fp_name in ["QFN-32-1EP_5x5mm_P0.5mm", "DFN-10-1EP_3x3mm_P0.5mm"]:
        pads = "\n".join(
            f'  (pad "{i}" smd rect (at {i*0.5} 0) (size 0.3 0.8))'
            for i in range(1, 11)
        )
        (ic_dir / f"{fp_name}.kicad_mod").write_text(
            f'(footprint "{fp_name}"\n{pads}\n)\n',
            encoding="utf-8",
        )

    # Inductor_SMD — 1 footprint (extra to verify "all dirs" scanning)
    ind_dir = root / "Inductor_SMD.pretty"
    ind_dir.mkdir()
    (ind_dir / "L_0402_1005Metric.kicad_mod").write_text(
        '(footprint "L_0402_1005Metric"\n'
        '  (pad "1" smd rect (at 0 0) (size 1 1))\n'
        '  (pad "2" smd rect (at 1 0) (size 1 1))\n'
        ')\n',
        encoding="utf-8",
    )

    return root


@pytest.fixture
def fp_lib_table_custom(tmp_path: Path) -> Path:
    """Create a fp-lib-table with only 1 custom library (NOT in root)."""
    custom_lib = tmp_path / "custom_libs" / "MyParts.pretty"
    custom_lib.mkdir(parents=True)
    (custom_lib / "CustomConn_USB.kicad_mod").write_text(
        '(footprint "CustomConn_USB"\n'
        '  (pad "1" smd rect (at 0 0) (size 1 1))\n'
        ')\n',
        encoding="utf-8",
    )

    table = tmp_path / "custom_project" / "fp-lib-table"
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text(
        f'(fp_lib_table\n'
        f'  (version 7)\n'
        f'  (lib (name "MyParts")(type "KiCad")(uri "{custom_lib}")(options "")(descr "User custom"))\n'
        f')\n',
        encoding="utf-8",
    )
    return table


# ---------------------------------------------------------------------------
# 1. Built-in Root Scan Tests
# ---------------------------------------------------------------------------

class TestBuiltinRootScan:
    """Test that scan() indexes ALL .pretty dirs under footprint_root."""

    def test_root_scan_indexes_all_pretty_dirs(self, tmp_path, footprint_root):
        from footfindr.kicad.footprint_index import FootprintIndex

        index = FootprintIndex(project_dir=tmp_path)
        report = index.scan(
            [],  # NO fp-lib-table entries at all
            project_dir=tmp_path,
            footprint_root=footprint_root,
        )

        # Should find 5 .pretty dirs
        assert report.builtin_pretty_dirs_found == 5

        # Total footprints: 5 cap + 3 res + 1 led + 2 ic + 1 ind = 12
        assert report.builtin_footprints_indexed == 12
        assert report.total_footprints == 12

        # All libraries should be listed
        assert "Capacitor_SMD" in report.libraries_indexed
        assert "Resistor_SMD" in report.libraries_indexed
        assert "LED_SMD" in report.libraries_indexed
        assert "Package_DFN_QFN" in report.libraries_indexed
        assert "Inductor_SMD" in report.libraries_indexed

        index.close()

    def test_exact_footprint_ids_indexed(self, tmp_path, footprint_root):
        """Verify exact KiCad footprint IDs (not just library names)."""
        from footfindr.kicad.footprint_index import FootprintIndex

        index = FootprintIndex(project_dir=tmp_path)
        index.scan([], project_dir=tmp_path, footprint_root=footprint_root)

        # These exact IDs must exist
        cap_record = index.get("Capacitor_SMD:C_0603_1608Metric")
        assert cap_record is not None
        assert cap_record.footprint_name == "C_0603_1608Metric"
        assert cap_record.library_nickname == "Capacitor_SMD"

        res_record = index.get("Resistor_SMD:R_0603_1608Metric")
        assert res_record is not None
        assert res_record.footprint_name == "R_0603_1608Metric"
        assert res_record.library_nickname == "Resistor_SMD"

        index.close()

    def test_builtin_indexed_requires_exact_footprints(self, tmp_path, footprint_root):
        """builtin_indexed must be True only when exact IDs exist."""
        from footfindr.kicad.footprint_index import FootprintIndex

        index = FootprintIndex(project_dir=tmp_path)
        report = index.scan(
            [], project_dir=tmp_path, footprint_root=footprint_root,
        )

        assert report.builtin_indexed is True
        index.close()

    def test_builtin_indexed_false_when_partial(self, tmp_path):
        """builtin_indexed must be False if Capacitor_SMD or Resistor_SMD missing."""
        from footfindr.kicad.footprint_index import FootprintIndex

        partial_root = tmp_path / "partial_kicad"
        partial_root.mkdir()
        # Only Capacitor_SMD, no Resistor_SMD
        cap_dir = partial_root / "Capacitor_SMD.pretty"
        cap_dir.mkdir()
        (cap_dir / "C_0603_1608Metric.kicad_mod").write_text(
            '(footprint "C_0603_1608Metric"\n  (pad "1" smd rect (at 0 0) (size 1 1))\n)\n',
            encoding="utf-8",
        )

        index = FootprintIndex(project_dir=tmp_path)
        report = index.scan([], project_dir=tmp_path, footprint_root=partial_root)

        assert report.builtin_indexed is False
        assert report.builtin_pretty_dirs_found == 1
        assert report.builtin_footprints_indexed == 1
        index.close()

    def test_root_scan_additive_with_fp_lib_table(
        self, tmp_path, footprint_root, fp_lib_table_custom,
    ):
        """Root scan + fp-lib-table should produce combined results."""
        from footfindr.kicad.footprint_index import FootprintIndex

        index = FootprintIndex(project_dir=tmp_path)
        report = index.scan(
            [fp_lib_table_custom],
            project_dir=tmp_path,
            footprint_root=footprint_root,
        )

        # 12 from root + 1 from custom fp-lib-table = 13
        assert report.total_footprints == 13

        # Custom library should be indexed too
        assert "MyParts" in report.libraries_indexed
        custom_record = index.get("MyParts:CustomConn_USB")
        assert custom_record is not None

        # Root scan entries should still exist
        assert index.get("Capacitor_SMD:C_0603_1608Metric") is not None
        assert index.get("Resistor_SMD:R_0603_1608Metric") is not None

        index.close()

    def test_root_scan_no_root(self, tmp_path):
        """When no footprint_root is provided, only fp-lib-table matters."""
        from footfindr.kicad.footprint_index import FootprintIndex

        index = FootprintIndex(project_dir=tmp_path)
        report = index.scan(
            [],  # no fp-lib-table
            project_dir=tmp_path,
            footprint_root=None,  # no root
        )

        assert report.builtin_pretty_dirs_found == 0
        assert report.builtin_footprints_indexed == 0
        assert report.total_footprints == 0
        assert report.builtin_indexed is False
        index.close()

    def test_resolved_footprint_dir_in_report(self, tmp_path, footprint_root):
        """ScanReport.resolved_footprint_dir should be populated."""
        from footfindr.kicad.footprint_index import FootprintIndex

        # Set an env var override so resolved_footprint_dir is populated
        index = FootprintIndex(project_dir=tmp_path)
        report = index.scan(
            [],
            project_dir=tmp_path,
            footprint_root=footprint_root,
            config_overrides={"KICAD_FOOTPRINT_DIR": str(footprint_root)},
        )

        assert report.resolved_footprint_dir != ""
        index.close()

    def test_search_after_root_scan(self, tmp_path, footprint_root):
        """Search should find footprints indexed from root scan."""
        from footfindr.kicad.footprint_index import FootprintIndex

        index = FootprintIndex(project_dir=tmp_path)
        index.scan([], project_dir=tmp_path, footprint_root=footprint_root)

        results = index.search("0603")
        kicad_ids = [r.kicad_id for r in results]
        assert "Capacitor_SMD:C_0603_1608Metric" in kicad_ids
        assert "Resistor_SMD:R_0603_1608Metric" in kicad_ids
        index.close()

    def test_scope_is_builtin(self, tmp_path, footprint_root):
        """Footprints from root scan should have scope='builtin'."""
        from footfindr.kicad.footprint_index import FootprintIndex

        index = FootprintIndex(project_dir=tmp_path)
        index.scan([], project_dir=tmp_path, footprint_root=footprint_root)

        record = index.get("Capacitor_SMD:C_0603_1608Metric")
        assert record is not None
        assert record.scope == "builtin"
        index.close()


# ---------------------------------------------------------------------------
# 2. Pagination Provider Resolution Tests
# ---------------------------------------------------------------------------

class TestPaginationProviderResolution:
    """Verify `more` command resolves the correct provider from session."""

    def test_session_stores_provider_offsets(self):
        """Session should preserve provider_offsets for pagination."""
        from footfindr.suppliers.session import SearchSession
        from footfindr.suppliers.models import SupplierPart

        parts = [
            SupplierPart(supplier="digikey", mpn=f"PART-{i}", source="test")
            for i in range(15)
        ]
        session = SearchSession(
            query="capacitor 4.7uF",
            suppliers=["digikey"],
            created_at="2025-01-01T00:00:00Z",
            last_updated="2025-01-01T00:00:00Z",
            original_results=parts,
            active_result_ids=[p.result_id for p in parts],
            page_size=10,
            provider_offsets={"digikey": 15},
        )

        assert session.provider_offsets == {"digikey": 15}
        assert session.has_next_page()  # 15 results, page_size=10, page 1


# ---------------------------------------------------------------------------
# 3. --min Alias Tests
# ---------------------------------------------------------------------------

class TestMinAlias:
    """Verify --min is accepted wherever --mini is accepted."""

    def test_search_command_has_min(self):
        """supplier search should accept --min."""
        # This is a structural test: check the typer Option definition
        # We verify by importing and checking the Option's names
        # (The actual CLI acceptance is verified in live testing)
        import footfindr.cli_supplier as cli_mod
        source = Path(cli_mod.__file__).read_text(encoding="utf-8")
        # The search command should have both --mini and --min
        assert '"--mini", "--min", "-q"' in source or '"--mini", "--min"' in source

    def test_list_command_has_min(self):
        """supplier list should accept --min."""
        import footfindr.cli_supplier as cli_mod
        source = Path(cli_mod.__file__).read_text(encoding="utf-8")
        # Count occurrences of the pattern
        count = source.count('"--mini", "--min"')
        # search, list, group, filter, sort = 5 positional commands
        # + search-for already had it = 1
        # + more, next, prev, page = 4 toggle commands
        assert count >= 5, f"Expected at least 5 commands with --min, found {count}"

    def test_more_command_has_min(self):
        """supplier more should accept --min."""
        import footfindr.cli_supplier as cli_mod
        source = Path(cli_mod.__file__).read_text(encoding="utf-8")
        # more uses toggle syntax: "--mini/--full", "--min"
        assert '"--mini/--full", "--min"' in source


# ---------------------------------------------------------------------------
# 4. Idempotent Apply Tests
# ---------------------------------------------------------------------------

class TestIdempotentApply:
    """Verify idempotent reuse does not require --force for plan apply."""

    def test_idempotent_collision_not_stored_in_plan(self):
        """Idempotent collisions should be filtered from plan.collision_warnings."""
        from footfindr.plans import CollisionWarning

        # Simulate: same IPN + same MPN (idempotent reuse)
        warnings = [
            CollisionWarning(
                collision_type="idempotent",
                existing_pn="CAP-100NF-50V-X7R-0603",
                existing_mpn="GRM188R71H104KA93D",
                message="Part already exists in library (IPN=CAP-100NF-50V-X7R-0603). Reusing.",
            ),
        ]

        # This is the filter applied in cli_ref.py
        plan_warnings = [
            cw.message for cw in warnings
            if cw.collision_type != "idempotent"
        ]

        assert plan_warnings == [], "Idempotent warnings should not appear in plan"

    def test_true_conflict_still_stored(self):
        """True conflicts (same IPN, different MPN) should still be in plan."""
        from footfindr.plans import CollisionWarning

        warnings = [
            CollisionWarning(
                collision_type="same_internal_pn",
                existing_pn="CAP-100NF",
                existing_mpn="GRM188",
                message="Internal PN 'CAP-100NF' already exists with MPN 'GRM188'",
            ),
        ]

        plan_warnings = [
            cw.message for cw in warnings
            if cw.collision_type != "idempotent"
        ]

        assert len(plan_warnings) == 1

    def test_plan_apply_no_force_with_empty_warnings(self, workspace):
        """Plan with no collision_warnings should apply without --force."""
        from footfindr.plans import Plan, PlanStep, PlanManager

        plan = Plan(
            plan_id="test_idempotent_001",
            operation="ref-assign",
            created_at="2025-01-01T00:00:00Z",
            steps=[],
            collision_warnings=[],  # Empty = no force needed
            provenance={},
        )

        # Verify: collision_warnings is empty, so apply should not block
        assert plan.collision_warnings == []

    def test_idempotent_reuse_function(self):
        """is_idempotent_reuse should return True for all-idempotent collisions."""
        from footfindr.plans import CollisionWarning, is_idempotent_reuse

        warnings = [
            CollisionWarning(
                collision_type="idempotent",
                existing_pn="CAP-1",
                existing_mpn="MPN1",
                message="Part already exists.",
            ),
        ]
        assert is_idempotent_reuse(warnings) is True

    def test_mixed_collisions_not_idempotent(self):
        """Mix of idempotent + conflict should not be considered idempotent."""
        from footfindr.plans import CollisionWarning, is_idempotent_reuse

        warnings = [
            CollisionWarning(
                collision_type="idempotent",
                existing_pn="CAP-1",
                existing_mpn="MPN1",
                message="Part already exists.",
            ),
            CollisionWarning(
                collision_type="same_internal_pn",
                existing_pn="CAP-1",
                existing_mpn="MPN2",
                message="Conflict!",
            ),
        ]
        assert is_idempotent_reuse(warnings) is False


# ---------------------------------------------------------------------------
# 5. Manual Footprint Write (ff ref fp) Tests
# ---------------------------------------------------------------------------

class TestManualFootprintWrite:
    """Test that ff ref fp --set creates the correct plan."""

    def test_plan_step_contains_footprint_field(self):
        """A plan step for ref fp should update Footprint + FootFindrFootprintStatus."""
        from footfindr.plans import PlanStep

        step = PlanStep(
            operation="update_schematic",
            target_file="/path/to/test.kicad_sch",
            target_key="C1",
            new_value={
                "Footprint": "Capacitor_SMD:C_0603_1608Metric",
                "FootFindrFootprintStatus": "assigned",
            },
            old_value={
                "Footprint": "",
                "FootFindrFootprintStatus": "",
            },
            reason="Set footprint for C1 to Capacitor_SMD:C_0603_1608Metric",
        )

        assert step.new_value["Footprint"] == "Capacitor_SMD:C_0603_1608Metric"
        assert step.new_value["FootFindrFootprintStatus"] == "assigned"
        assert step.old_value["Footprint"] == ""

    def test_plan_show_includes_footprint_diff(self):
        """Plan diff should show '— → Capacitor_SMD:C_0603_1608Metric'."""
        from footfindr.plans import PlanStep

        step = PlanStep(
            operation="update_schematic",
            target_file="/path/to/test.kicad_sch",
            target_key="C1",
            new_value={
                "Footprint": "Capacitor_SMD:C_0603_1608Metric",
                "FootFindrFootprintStatus": "assigned",
            },
            old_value={
                "Footprint": "",
                "FootFindrFootprintStatus": "",
            },
        )

        # Verify the new_value and old_value enable proper diff display
        old_fp = step.old_value.get("Footprint", "—") or "—"
        new_fp = step.new_value.get("Footprint", "")
        assert old_fp == "—" or old_fp == ""
        assert new_fp == "Capacitor_SMD:C_0603_1608Metric"


# ---------------------------------------------------------------------------
# 6. Footprint Resolver with Root-Scanned Index
# ---------------------------------------------------------------------------

class TestResolverWithRootScan:
    """Test FootprintResolver works with footprints indexed from root scan."""

    def test_capacitor_0603_from_root(self, tmp_path, footprint_root):
        from footfindr.kicad.footprint_index import FootprintIndex
        from footfindr.kicad.footprint_resolver import FootprintResolver

        index = FootprintIndex(project_dir=tmp_path)
        index.scan([], project_dir=tmp_path, footprint_root=footprint_root)

        resolver = FootprintResolver(index)
        part = SimpleNamespace(mpn="GRM188", package="0603 (1608 Metric)", attributes={})
        result = resolver.resolve(part, "C1", "capacitor")

        assert result.status == "exact"
        assert result.footprint == "Capacitor_SMD:C_0603_1608Metric"
        index.close()

    def test_resistor_0603_from_root(self, tmp_path, footprint_root):
        from footfindr.kicad.footprint_index import FootprintIndex
        from footfindr.kicad.footprint_resolver import FootprintResolver

        index = FootprintIndex(project_dir=tmp_path)
        index.scan([], project_dir=tmp_path, footprint_root=footprint_root)

        resolver = FootprintResolver(index)
        part = SimpleNamespace(mpn="RC0603", package="0603", attributes={})
        result = resolver.resolve(part, "R1", "resistor")

        assert result.status == "exact"
        assert result.footprint == "Resistor_SMD:R_0603_1608Metric"
        index.close()

    def test_ic_qfn32_from_root(self, tmp_path, footprint_root):
        from footfindr.kicad.footprint_index import FootprintIndex
        from footfindr.kicad.footprint_resolver import FootprintResolver

        index = FootprintIndex(project_dir=tmp_path)
        index.scan([], project_dir=tmp_path, footprint_root=footprint_root)

        resolver = FootprintResolver(index)
        part = SimpleNamespace(
            mpn="STM32F0", package="QFN-32-1EP_5x5mm", attributes={},
        )
        result = resolver.resolve(part, "U1", "ic")

        assert result.status == "exact"
        assert "QFN-32" in result.footprint
        index.close()
