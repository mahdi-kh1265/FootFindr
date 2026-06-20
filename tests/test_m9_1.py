"""M9.1 tests — KiCad project discovery, binding, inspect, safe write, config.

All tests are offline. Uses temporary directories with mock KiCad files.
"""

from __future__ import annotations

import hashlib
import json
import os
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: create mock KiCad project files
# ---------------------------------------------------------------------------

MINIMAL_KICAD_SCH = textwrap.dedent("""\
(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
  (lib_symbols)
  (symbol
    (lib_id "Device:R")
    (at 100 100 0)
    (uuid "11111111-2222-3333-4444-555555555555")
    (property "Reference" "R1" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "10k" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "MPN" "RC0603FR-0710KL" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Manufacturer" "Yageo" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
  )
  (symbol
    (lib_id "Device:C")
    (at 200 100 0)
    (uuid "22222222-3333-4444-5555-666666666666")
    (property "Reference" "C1" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "100nF" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
  )
)
""")

KICAD_SCH_WITH_SUBSHEET = textwrap.dedent("""\
(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
  (lib_symbols)
  (symbol
    (lib_id "Device:R")
    (at 100 100 0)
    (uuid "11111111-2222-3333-4444-555555555555")
    (property "Reference" "R1" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "10k" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
  )
  (sheet
    (at 50 50) (size 20 20)
    (uuid "33333333-4444-5555-6666-777777777777")
    (property "Sheetname" "Power" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Sheetfile" "power.kicad_sch" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
  )
)
""")

SUBSHEET_SCH = textwrap.dedent("""\
(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (uuid "88888888-9999-aaaa-bbbb-cccccccccccc")
  (lib_symbols)
  (symbol
    (lib_id "Device:C")
    (at 100 100 0)
    (uuid "44444444-5555-6666-7777-888888888888")
    (property "Reference" "C10" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "10uF" (at 0 0 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "Capacitor_SMD:C_0805_2012Metric" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
  )
)
""")


def _create_kicad_project(base: Path, name: str, *,
                           with_pro: bool = True,
                           with_sch: bool = True,
                           with_pcb: bool = False,
                           sch_content: str = MINIMAL_KICAD_SCH,
                           with_subsheet: bool = False) -> Path:
    """Create a mock KiCad project directory."""
    proj_dir = base / name
    proj_dir.mkdir(parents=True, exist_ok=True)

    if with_pro:
        (proj_dir / f"{name}.kicad_pro").write_text("{}", encoding="utf-8")
    if with_sch:
        content = KICAD_SCH_WITH_SUBSHEET if with_subsheet else sch_content
        (proj_dir / f"{name}.kicad_sch").write_text(content, encoding="utf-8")
    if with_pcb:
        (proj_dir / f"{name}.kicad_pcb").write_text("(kicad_pcb)", encoding="utf-8")
    if with_subsheet:
        (proj_dir / "power.kicad_sch").write_text(SUBSHEET_SCH, encoding="utf-8")

    return proj_dir


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Test KiCad project discovery."""

    def test_discover_in_directory(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "test-board", with_pro=True, with_pcb=True)
        proj = discover_kicad_project(tmp_path / "test-board")
        assert proj is not None
        assert proj.name == "test-board"
        assert proj.pro_file is not None
        assert proj.sch_file is not None
        assert proj.pcb_file is not None

    def test_discover_sch_only(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "sch-only", with_pro=False)
        proj = discover_kicad_project(tmp_path / "sch-only")
        assert proj is not None
        assert proj.name == "sch-only"
        assert proj.pro_file is None
        assert proj.sch_file is not None

    def test_discover_empty_dir(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        empty = tmp_path / "empty"
        empty.mkdir()
        assert discover_kicad_project(empty) is None

    def test_discover_multiple_roots(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        _create_kicad_project(root1, "proj-a")
        _create_kicad_project(root2, "proj-b")
        projects = discover_kicad_projects([root1, root2])
        names = [p.name for p in projects]
        assert "proj-a" in names
        assert "proj-b" in names

    def test_discover_max_projects(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        for i in range(5):
            _create_kicad_project(tmp_path, f"proj-{i}")
        projects = discover_kicad_projects([tmp_path], max_projects=3)
        assert len(projects) <= 3

    def test_discover_max_depth(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        # Create a deep project
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        _create_kicad_project(deep, "deep-proj")
        # max_depth=3 should not find it (5 levels deep)
        projects = discover_kicad_projects([tmp_path], max_depth=3)
        names = [p.name for p in projects]
        assert "deep-proj" not in names

    def test_discover_skips_hidden(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        hidden = tmp_path / ".hidden"
        _create_kicad_project(hidden, "hidden-proj")
        projects = discover_kicad_projects([tmp_path])
        names = [p.name for p in projects]
        assert "hidden-proj" not in names

    def test_find_nearest(self, tmp_path):
        from footfindr.kicad.discovery import find_nearest_kicad_project
        _create_kicad_project(tmp_path, "my-proj")
        proj_dir = tmp_path / "my-proj"
        sub = proj_dir / "sub" / "deep"
        sub.mkdir(parents=True)
        # From deep inside, should find parent project
        proj = find_nearest_kicad_project(sub)
        assert proj is not None
        assert proj.name == "my-proj"

    def test_find_nearest_none(self, tmp_path):
        from footfindr.kicad.discovery import find_nearest_kicad_project
        empty = tmp_path / "empty"
        empty.mkdir()
        assert find_nearest_kicad_project(empty) is None

    def test_to_dict(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "dict-test", with_pcb=True)
        proj = discover_kicad_project(tmp_path / "dict-test")
        d = proj.to_dict()
        j = json.dumps(d)
        parsed = json.loads(j)
        assert parsed["name"] == "dict-test"
        assert parsed["pro_file"] is not None


# ---------------------------------------------------------------------------
# Subsheet detection tests
# ---------------------------------------------------------------------------


class TestSubsheetDetection:
    """Test hierarchical sheet detection."""

    def test_detect_subsheets(self, tmp_path):
        from footfindr.kicad.discovery import detect_subsheets
        _create_kicad_project(tmp_path, "hier-proj", with_subsheet=True)
        sch = tmp_path / "hier-proj" / "hier-proj.kicad_sch"
        subsheets = detect_subsheets(sch)
        assert len(subsheets) == 1
        assert subsheets[0].name == "power.kicad_sch"

    def test_detect_no_subsheets(self, tmp_path):
        from footfindr.kicad.discovery import detect_subsheets
        _create_kicad_project(tmp_path, "flat-proj")
        sch = tmp_path / "flat-proj" / "flat-proj.kicad_sch"
        subsheets = detect_subsheets(sch)
        assert len(subsheets) == 0

    def test_detect_missing_file(self, tmp_path):
        from footfindr.kicad.discovery import detect_subsheets
        assert detect_subsheets(tmp_path / "nonexistent.kicad_sch") == []

    def test_subsheet_in_discovery(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "hier2", with_subsheet=True)
        proj = discover_kicad_project(tmp_path / "hier2")
        assert len(proj.subsheets) == 1


# ---------------------------------------------------------------------------
# Inspect tests
# ---------------------------------------------------------------------------


class TestInspect:
    """Test schematic inspection."""

    def test_inspect_basic(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        _create_kicad_project(tmp_path, "insp-proj")
        sch = tmp_path / "insp-proj" / "insp-proj.kicad_sch"
        result = inspect_schematic(str(sch))
        assert result.symbol_count == 2
        assert "R1" in result.refs
        assert "C1" in result.refs
        assert result.has_mpn == 1  # only R1 has MPN
        assert result.has_manufacturer == 1
        assert result.has_footprint == 1  # R1 has footprint, C1 has empty
        assert result.writable is True
        assert result.is_complete is True

    def test_inspect_with_subsheets(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        _create_kicad_project(tmp_path, "hier-insp", with_subsheet=True)
        sch = tmp_path / "hier-insp" / "hier-insp.kicad_sch"
        result = inspect_schematic(str(sch))
        # Should include root R1 + subsheet C10
        assert "R1" in result.refs
        assert "C10" in result.refs
        assert result.sheets_parsed == 2
        assert result.is_complete is True

    def test_inspect_missing_subsheet(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        _create_kicad_project(tmp_path, "miss-sub", with_subsheet=True)
        # Delete the subsheet file
        (tmp_path / "miss-sub" / "power.kicad_sch").unlink()
        sch = tmp_path / "miss-sub" / "miss-sub.kicad_sch"
        result = inspect_schematic(str(sch))
        assert result.is_complete is False
        assert len(result.subsheets_missing) == 1
        assert any("missing" in w.lower() for w in result.parse_warnings)

    def test_inspect_nonexistent(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        result = inspect_schematic(str(tmp_path / "nope.kicad_sch"))
        assert result.is_complete is False
        assert len(result.parse_warnings) > 0

    def test_inspect_to_dict_json(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        _create_kicad_project(tmp_path, "json-insp")
        sch = tmp_path / "json-insp" / "json-insp.kicad_sch"
        result = inspect_schematic(str(sch))
        d = result.to_dict()
        j = json.dumps(d)
        parsed = json.loads(j)
        assert parsed["symbol_count"] == 2
        assert parsed["is_complete"] is True

    def test_inspect_field_names(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        _create_kicad_project(tmp_path, "fields-insp")
        sch = tmp_path / "fields-insp" / "fields-insp.kicad_sch"
        result = inspect_schematic(str(sch))
        assert "MPN" in result.field_names
        assert "Manufacturer" in result.field_names

    def test_inspect_values_summary(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        _create_kicad_project(tmp_path, "vals-insp")
        sch = tmp_path / "vals-insp" / "vals-insp.kicad_sch"
        result = inspect_schematic(str(sch))
        assert "10k" in result.values_summary
        assert "100nF" in result.values_summary


# ---------------------------------------------------------------------------
# Safe write tests
# ---------------------------------------------------------------------------


class TestSafeWrite:
    """Test safe schematic write model."""

    def test_snapshot(self, tmp_path):
        from footfindr.kicad.safe_write import snapshot_schematic
        _create_kicad_project(tmp_path, "snap-proj")
        sch = tmp_path / "snap-proj" / "snap-proj.kicad_sch"
        snap = snapshot_schematic(sch)
        assert snap.sha256 is not None
        assert snap.size > 0
        assert snap.taken_at is not None
        assert snap.path == str(sch.resolve())

    def test_verify_unchanged(self, tmp_path):
        from footfindr.kicad.safe_write import snapshot_schematic, verify_unchanged
        _create_kicad_project(tmp_path, "verify-proj")
        sch = tmp_path / "verify-proj" / "verify-proj.kicad_sch"
        snap = snapshot_schematic(sch)
        assert verify_unchanged(snap) is True

    def test_verify_changed(self, tmp_path):
        from footfindr.kicad.safe_write import snapshot_schematic, verify_unchanged
        _create_kicad_project(tmp_path, "change-proj")
        sch = tmp_path / "change-proj" / "change-proj.kicad_sch"
        snap = snapshot_schematic(sch)
        # Modify the file
        sch.write_text(sch.read_text() + "\n; modified", encoding="utf-8")
        assert verify_unchanged(snap) is False

    def test_snapshot_to_dict(self, tmp_path):
        from footfindr.kicad.safe_write import snapshot_schematic, SchematicSnapshot
        _create_kicad_project(tmp_path, "dict-snap")
        sch = tmp_path / "dict-snap" / "dict-snap.kicad_sch"
        snap = snapshot_schematic(sch)
        d = snap.to_dict()
        j = json.dumps(d)
        parsed = json.loads(j)
        assert parsed["sha256"] == snap.sha256
        # Roundtrip
        snap2 = SchematicSnapshot.from_dict(parsed)
        assert snap2.sha256 == snap.sha256

    def test_create_backup(self, tmp_path):
        from footfindr.kicad.safe_write import create_backup
        _create_kicad_project(tmp_path, "bak-proj")
        sch = tmp_path / "bak-proj" / "bak-proj.kicad_sch"
        bak = create_backup(sch)
        assert bak.exists()
        assert ".footfindr.bak" in bak.name
        assert bak.read_text(encoding="utf-8") == sch.read_text(encoding="utf-8")

    def test_safe_write_blocks_changed(self, tmp_path):
        from footfindr.kicad.safe_write import (
            snapshot_schematic, safe_write, SchematicChangedError)
        _create_kicad_project(tmp_path, "block-proj")
        sch = tmp_path / "block-proj" / "block-proj.kicad_sch"
        snap = snapshot_schematic(sch)
        # Modify file after snapshot
        sch.write_text(sch.read_text() + "\n; changed", encoding="utf-8")
        with pytest.raises(SchematicChangedError):
            safe_write(sch, {"R1": {"Value": "20k"}}, snapshot=snap)

    def test_safe_write_creates_backup(self, tmp_path):
        from footfindr.kicad.safe_write import safe_write
        _create_kicad_project(tmp_path, "bak-write")
        sch = tmp_path / "bak-write" / "bak-write.kicad_sch"
        initial_files = set((tmp_path / "bak-write").iterdir())
        safe_write(sch, {"R1": {"Value": "20k"}}, backup=True)
        new_files = set((tmp_path / "bak-write").iterdir()) - initial_files
        bak_files = [f for f in new_files if ".footfindr.bak" in f.name]
        assert len(bak_files) >= 1

    def test_safe_write_validates_parse(self, tmp_path):
        from footfindr.kicad.safe_write import safe_write
        _create_kicad_project(tmp_path, "valid-proj")
        sch = tmp_path / "valid-proj" / "valid-proj.kicad_sch"
        # Write should succeed and file should still be parsable
        results = safe_write(sch, {"R1": {"Value": "22k"}}, backup=False)
        assert results["R1"] is None  # success
        # Verify the file is still parsable
        from footfindr.kicad.schematic import KiCadSchematicReader
        reader = KiCadSchematicReader()
        parsed = reader.read(str(sch))
        r1 = parsed.symbol_by_ref("R1")
        assert r1.value == "22k"

    def test_safe_write_no_write_without_plan(self, tmp_path):
        """safe_write with empty updates should not modify file."""
        from footfindr.kicad.safe_write import safe_write
        _create_kicad_project(tmp_path, "no-write")
        sch = tmp_path / "no-write" / "no-write.kicad_sch"
        original = sch.read_text(encoding="utf-8")
        safe_write(sch, {}, backup=False)
        assert sch.read_text(encoding="utf-8") == original

    def test_snapshot_nonexistent(self, tmp_path):
        from footfindr.kicad.safe_write import snapshot_schematic
        with pytest.raises(FileNotFoundError):
            snapshot_schematic(tmp_path / "nope.kicad_sch")


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    """Test user-level config system."""

    def test_load_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("footfindr.config.get_user_config_path",
                           lambda: tmp_path / "config.yaml")
        from footfindr.config import load_user_config
        assert load_user_config() == {}

    def test_save_and_load(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        monkeypatch.setattr("footfindr.config.get_user_config_path",
                           lambda: cfg_path)
        from footfindr.config import save_user_config, load_user_config
        save_user_config({"kicad": {"roots": ["/path/a"]}})
        loaded = load_user_config()
        assert loaded["kicad"]["roots"] == ["/path/a"]

    def test_add_kicad_root(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        monkeypatch.setattr("footfindr.config.get_user_config_path",
                           lambda: cfg_path)
        monkeypatch.setattr("footfindr.config.get_user_config_dir",
                           lambda: tmp_path)
        from footfindr.config import add_kicad_root, get_kicad_roots
        add_kicad_root(tmp_path / "kicad1")
        add_kicad_root(tmp_path / "kicad2")
        roots = get_kicad_roots()
        assert len(roots) == 2

    def test_add_kicad_root_no_duplicates(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        monkeypatch.setattr("footfindr.config.get_user_config_path",
                           lambda: cfg_path)
        monkeypatch.setattr("footfindr.config.get_user_config_dir",
                           lambda: tmp_path)
        from footfindr.config import add_kicad_root, get_kicad_roots
        add_kicad_root(tmp_path / "kicad1")
        add_kicad_root(tmp_path / "kicad1")
        roots = get_kicad_roots()
        assert len(roots) == 1

    def test_set_kicad_root(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        monkeypatch.setattr("footfindr.config.get_user_config_path",
                           lambda: cfg_path)
        monkeypatch.setattr("footfindr.config.get_user_config_dir",
                           lambda: tmp_path)
        from footfindr.config import set_kicad_root, get_kicad_roots
        set_kicad_root(tmp_path / "root1")
        roots = get_kicad_roots()
        assert len(roots) == 1

    def test_get_config_value(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        monkeypatch.setattr("footfindr.config.get_user_config_path",
                           lambda: cfg_path)
        monkeypatch.setattr("footfindr.config.get_user_config_dir",
                           lambda: tmp_path)
        from footfindr.config import (
            set_user_config_value, get_user_config_value)
        set_user_config_value("kicad.root", "/my/path")
        val = get_user_config_value("kicad.roots")
        assert val == ["/my/path"]

    def test_get_nonexistent_key(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        monkeypatch.setattr("footfindr.config.get_user_config_path",
                           lambda: cfg_path)
        from footfindr.config import get_user_config_value
        assert get_user_config_value("nonexistent.key") is None


# ---------------------------------------------------------------------------
# Project manager tests
# ---------------------------------------------------------------------------


class TestProjectManager:
    """Test enhanced project manager."""

    def test_use_smart_dot(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "dot-test", with_pro=True, with_pcb=True)
        proj_dir = tmp_path / "dot-test"

        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        with patch("footfindr.kicad.discovery.find_nearest_kicad_project") as mock_fn:
            from footfindr.kicad.discovery import discover_kicad_project
            mock_fn.return_value = discover_kicad_project(proj_dir)
            meta, msg = pm.use_smart(".")
            assert meta.name == "dot-test"
            assert "Active project: dot-test" in msg

    def test_use_smart_path(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "path-test")
        proj_dir = tmp_path / "path-test"

        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        meta, msg = pm.use_smart(str(proj_dir))
        assert meta.name == "path-test"
        assert meta.schematic is not None

    def test_use_smart_kicad_pro(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "pro-test", with_pro=True)

        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        pro_file = tmp_path / "pro-test" / "pro-test.kicad_pro"
        meta, msg = pm.use_smart(str(pro_file))
        assert meta.name == "pro-test"

    def test_use_smart_kicad_sch(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "sch-test")

        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        sch_file = tmp_path / "sch-test" / "sch-test.kicad_sch"
        meta, msg = pm.use_smart(str(sch_file))
        assert meta.name == "sch-test"

    def test_use_smart_registered_name(self, tmp_path):
        from footfindr.project import ProjectManager
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        pm.start("existing", "some.kicad_sch")
        meta, msg = pm.use_smart("existing")
        assert meta.name == "existing"
        assert "Active project: existing" in msg

    def test_use_smart_not_found(self, tmp_path):
        from footfindr.project import ProjectManager
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        with patch("footfindr.kicad.discovery.get_default_search_roots",
                   return_value=[tmp_path]):
            with pytest.raises(ValueError, match="No KiCad project"):
                pm.use_smart("nonexistent")

    def test_use_smart_ambiguous(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "test-board")
        _create_kicad_project(tmp_path, "test-supply")

        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        with patch("footfindr.kicad.discovery.get_default_search_roots",
                   return_value=[tmp_path]):
            with pytest.raises(ValueError, match="Multiple"):
                pm.use_smart("test")

    def test_clear(self, tmp_path):
        from footfindr.project import ProjectManager
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        pm.start("clear-test", "some.kicad_sch")
        pm.use("clear-test")
        assert pm.get_active_name() == "clear-test"
        pm.clear()
        assert pm.get_active_name() is None

    def test_auto_register_stores_paths(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "reg-test",
                              with_pro=True, with_pcb=True)
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        meta, _ = pm.use_smart(str(tmp_path / "reg-test"))
        assert meta.kicad_pro is not None
        assert meta.kicad_pcb is not None
        assert meta.project_dir is not None

    def test_auto_register_updates_existing(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "update-test",
                              with_pro=True, with_pcb=True)
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        pm.start("update-test", "")  # create empty
        meta, _ = pm.use_smart(str(tmp_path / "update-test"))
        assert meta.schematic != ""  # should be updated

    def test_discover(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "disc-a")
        _create_kicad_project(tmp_path, "disc-b")
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        projects = pm.discover(roots=[tmp_path])
        names = [p.name for p in projects]
        assert "disc-a" in names
        assert "disc-b" in names

    def test_metadata_serialization(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "serial-test",
                              with_pro=True, with_pcb=True)
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        meta, _ = pm.use_smart(str(tmp_path / "serial-test"))
        # Reload
        reloaded = pm.status("serial-test")
        assert reloaded.kicad_pro == meta.kicad_pro
        assert reloaded.kicad_pcb == meta.kicad_pcb
        assert reloaded.project_dir == meta.project_dir


# ---------------------------------------------------------------------------
# Resolve schematic path tests
# ---------------------------------------------------------------------------


class TestResolveSchematic:
    """Test enhanced schematic resolution with nearest-project fallback."""

    def test_explicit_path(self, tmp_path):
        from footfindr.project import resolve_schematic_path
        _create_kicad_project(tmp_path, "expl-proj")
        sch = tmp_path / "expl-proj" / "expl-proj.kicad_sch"
        result = resolve_schematic_path(str(sch), workspace=str(tmp_path / ".ff"))
        assert result == str(sch)

    def test_active_project(self, tmp_path):
        from footfindr.project import ProjectManager, resolve_schematic_path
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        pm.start("act-proj", "my.kicad_sch")
        pm.use("act-proj")
        result = resolve_schematic_path(
            None, workspace=str(tmp_path / ".footfindr"))
        assert result == "my.kicad_sch"

    def test_nearest_fallback(self, tmp_path):
        from footfindr.project import resolve_schematic_path
        _create_kicad_project(tmp_path, "near-proj")
        sch = tmp_path / "near-proj" / "near-proj.kicad_sch"

        with patch("footfindr.kicad.discovery.find_nearest_kicad_project") as mock:
            from footfindr.kicad.discovery import KiCadProject
            mock.return_value = KiCadProject(
                name="near-proj",
                project_dir=tmp_path / "near-proj",
                sch_file=sch,
            )
            result = resolve_schematic_path(
                None, workspace=str(tmp_path / ".footfindr"))
            assert result == str(sch)

    def test_nothing_found_raises(self, tmp_path):
        from footfindr.project import resolve_schematic_path
        with patch("footfindr.kicad.discovery.find_nearest_kicad_project",
                   return_value=None):
            with pytest.raises(ValueError, match="No active project"):
                resolve_schematic_path(
                    None, workspace=str(tmp_path / ".footfindr"))


# ---------------------------------------------------------------------------
# CLI registration tests
# ---------------------------------------------------------------------------


class TestCLIRegistration:
    """Test that M9.1 commands are registered."""

    def test_cli_imports(self):
        from footfindr.cli import app
        assert app is not None

    def test_discovery_imports(self):
        from footfindr.kicad.discovery import (
            KiCadProject, discover_kicad_project,
            discover_kicad_projects, find_nearest_kicad_project,
            detect_subsheets,
        )
        assert KiCadProject is not None

    def test_inspect_imports(self):
        from footfindr.kicad.inspect import (
            SchematicInspection, inspect_schematic,
        )
        assert SchematicInspection is not None

    def test_safe_write_imports(self):
        from footfindr.kicad.safe_write import (
            SchematicSnapshot, snapshot_schematic,
            verify_unchanged, safe_write, create_backup,
            SchematicChangedError, SchematicWriteError,
        )
        assert SchematicSnapshot is not None

    def test_config_imports(self):
        from footfindr.config import (
            load_user_config, save_user_config,
            get_kicad_roots, add_kicad_root,
            get_user_config_value, set_user_config_value,
        )
        assert load_user_config is not None

    def test_cli_config_imports(self):
        from footfindr.cli_config import config_app
        assert config_app is not None


# ---------------------------------------------------------------------------
# JSON output tests
# ---------------------------------------------------------------------------


class TestJSONOutput:
    """Test JSON outputs are valid."""

    def test_kicad_project_json(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "json-proj", with_pcb=True)
        proj = discover_kicad_project(tmp_path / "json-proj")
        j = json.dumps(proj.to_dict())
        parsed = json.loads(j)
        assert parsed["name"] == "json-proj"

    def test_inspection_json(self, tmp_path):
        from footfindr.kicad.inspect import inspect_schematic
        _create_kicad_project(tmp_path, "json-insp")
        sch = tmp_path / "json-insp" / "json-insp.kicad_sch"
        result = inspect_schematic(str(sch))
        j = json.dumps(result.to_dict())
        parsed = json.loads(j)
        assert parsed["symbol_count"] == 2

    def test_snapshot_json(self, tmp_path):
        from footfindr.kicad.safe_write import snapshot_schematic
        _create_kicad_project(tmp_path, "json-snap")
        sch = tmp_path / "json-snap" / "json-snap.kicad_sch"
        snap = snapshot_schematic(sch)
        j = json.dumps(snap.to_dict())
        parsed = json.loads(j)
        assert "sha256" in parsed


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Verify M8/M8.5/M8.6/M8.7/M9 features still work."""

    def test_supplier_alias(self):
        from footfindr.suppliers.registry import SupplierRegistry
        assert SupplierRegistry.normalize_name("dk") == "digikey"

    def test_field_alias(self):
        from footfindr.suppliers.session import resolve_field_alias
        assert resolve_field_alias("pack") == "package"

    def test_profile_available(self):
        from footfindr.assembly_profiles import get_profile
        p = get_profile("prototype")
        assert p is not None

    def test_project_issue_model(self):
        from footfindr.bom.models import ProjectIssue
        issue = ProjectIssue(
            severity="FAIL", code="TEST", ref="C1",
            field="MPN", message="test",
        )
        d = issue.to_dict()
        assert d["severity"] == "FAIL"


# ---------------------------------------------------------------------------
# M9.1b hardening tests
# ---------------------------------------------------------------------------


class TestNameMatching:
    """Test case-insensitive and hyphen/underscore-insensitive matching."""

    def test_exact_case_insensitive(self):
        from footfindr.kicad.discovery import KiCadProject, match_project_name
        projects = [
            KiCadProject(name="test-board", project_dir=Path("/a")),
            KiCadProject(name="other", project_dir=Path("/b")),
        ]
        exact, fuzzy = match_project_name("Test-Board", projects)
        assert len(exact) == 1
        assert exact[0].name == "test-board"

    def test_fuzzy_underscore_hyphen(self):
        from footfindr.kicad.discovery import KiCadProject, match_project_name
        projects = [
            KiCadProject(name="test-board", project_dir=Path("/a")),
        ]
        exact, fuzzy = match_project_name("test_board", projects)
        assert len(exact) == 0
        assert len(fuzzy) == 1
        assert fuzzy[0].name == "test-board"

    def test_fuzzy_nospaces(self):
        from footfindr.kicad.discovery import KiCadProject, match_project_name
        projects = [
            KiCadProject(name="test-board", project_dir=Path("/a")),
        ]
        exact, fuzzy = match_project_name("testboard", projects)
        assert len(fuzzy) == 1

    def test_no_match(self):
        from footfindr.kicad.discovery import KiCadProject, match_project_name
        projects = [
            KiCadProject(name="test-board", project_dir=Path("/a")),
        ]
        exact, fuzzy = match_project_name("totally-different", projects)
        assert len(exact) == 0
        assert len(fuzzy) == 0

    def test_substring_match(self):
        from footfindr.kicad.discovery import KiCadProject, match_project_name
        projects = [
            KiCadProject(name="test-board-v2", project_dir=Path("/a")),
            KiCadProject(name="other", project_dir=Path("/b")),
        ]
        exact, fuzzy = match_project_name("test", projects)
        assert len(exact) == 0
        assert len(fuzzy) == 1  # substring match


class TestNestedProjectDiscovery:
    """Test that projects nested inside other projects are found."""

    def test_nested_project_found(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        # Parent is a KiCad project
        _create_kicad_project(tmp_path, "parent-board")
        # Child is also a KiCad project
        _create_kicad_project(tmp_path / "parent-board", "child-board")
        projects = discover_kicad_projects([tmp_path])
        names = [p.name for p in projects]
        assert "parent-board" in names
        assert "child-board" in names

    def test_deeply_nested_within_depth(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        _create_kicad_project(tmp_path, "top")
        _create_kicad_project(tmp_path / "top" / "sub1", "deep")
        projects = discover_kicad_projects([tmp_path], max_depth=4)
        names = [p.name for p in projects]
        assert "top" in names
        assert "deep" in names

    def test_sibling_and_nested(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        _create_kicad_project(tmp_path, "alpha")
        _create_kicad_project(tmp_path, "beta")
        _create_kicad_project(tmp_path / "alpha", "alpha-child")
        projects = discover_kicad_projects([tmp_path])
        names = [p.name for p in projects]
        assert "alpha" in names
        assert "beta" in names
        assert "alpha-child" in names


class TestSchematicOnlyProject:
    """Test schematic-only project discovery."""

    def test_sch_only_type(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "sch-only-proj", with_pro=False)
        proj = discover_kicad_project(tmp_path / "sch-only-proj")
        assert proj is not None
        assert proj.project_type == "schematic-only"
        assert proj.pro_file is None
        assert proj.sch_file is not None

    def test_full_type(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "full-proj", with_pro=True)
        proj = discover_kicad_project(tmp_path / "full-proj")
        assert proj.project_type == "full"

    def test_sch_only_in_discovery(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        _create_kicad_project(tmp_path, "sch-disc", with_pro=False)
        projects = discover_kicad_projects([tmp_path], include_sch_only=True)
        assert any(p.name == "sch-disc" for p in projects)

    def test_pro_only_filter(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        _create_kicad_project(tmp_path, "sch-only2", with_pro=False)
        _create_kicad_project(tmp_path, "full-proj2", with_pro=True)
        projects = discover_kicad_projects([tmp_path], include_sch_only=False)
        names = [p.name for p in projects]
        assert "sch-only2" not in names
        assert "full-proj2" in names


class TestDiscoverFlags:
    """Test discover command flags."""

    def test_discover_root(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        _create_kicad_project(tmp_path / "sub", "proj-r")
        projects = discover_kicad_projects([tmp_path / "sub"])
        assert any(p.name == "proj-r" for p in projects)

    def test_discover_depth(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        _create_kicad_project(deep, "deep-proj")
        # Shallow
        shallow = discover_kicad_projects([tmp_path], max_depth=2)
        assert not any(p.name == "deep-proj" for p in shallow)
        # Deep
        found = discover_kicad_projects([tmp_path], max_depth=6)
        assert any(p.name == "deep-proj" for p in found)

    def test_discover_no_early_stop(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_projects
        # Create many projects
        for i in range(10):
            _create_kicad_project(tmp_path, f"proj-{i}")
        projects = discover_kicad_projects([tmp_path], max_projects=100)
        assert len(projects) == 10


class TestDiagnose:
    """Test diagnose function."""

    def test_diagnose_found(self, tmp_path):
        from footfindr.kicad.discovery import diagnose_project
        _create_kicad_project(tmp_path, "test-board")
        lines = diagnose_project("test-board", roots=[tmp_path],
                                 workspace=tmp_path / ".footfindr")
        text = "\n".join(lines)
        assert "FOUND" in text
        assert "test-board" in text

    def test_diagnose_not_found(self, tmp_path):
        from footfindr.kicad.discovery import diagnose_project
        lines = diagnose_project("nonexistent", roots=[tmp_path],
                                 workspace=tmp_path / ".footfindr")
        text = "\n".join(lines)
        assert "NOT FOUND" in text

    def test_diagnose_fuzzy(self, tmp_path):
        from footfindr.kicad.discovery import diagnose_project
        _create_kicad_project(tmp_path, "test-board")
        lines = diagnose_project("test_board", roots=[tmp_path],
                                 workspace=tmp_path / ".footfindr")
        text = "\n".join(lines)
        # Should find via fuzzy
        assert "FUZZY" in text or "FOUND" in text


class TestProjectUseNeverSilent:
    """Every path through use_smart must produce output."""

    def test_success_has_output(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "output-test")
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        meta, msg = pm.use_smart(str(tmp_path / "output-test"))
        assert len(msg) > 0
        assert "Active project" in msg

    def test_not_found_has_output(self, tmp_path):
        from footfindr.project import ProjectManager
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        with patch("footfindr.kicad.discovery.get_default_search_roots",
                   return_value=[tmp_path]):
            with pytest.raises(ValueError) as exc_info:
                pm.use_smart("nope")
            assert len(str(exc_info.value)) > 10

    def test_ambiguous_has_output(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "test-a")
        _create_kicad_project(tmp_path, "test-b")
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        with patch("footfindr.kicad.discovery.get_default_search_roots",
                   return_value=[tmp_path]):
            with pytest.raises(ValueError) as exc_info:
                pm.use_smart("test")
            msg = str(exc_info.value)
            assert "Multiple" in msg
            assert "test-a" in msg or "test-b" in msg

    def test_empty_dir_path_has_error(self, tmp_path):
        from footfindr.project import ProjectManager
        empty = tmp_path / "empty-dir"
        empty.mkdir()
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        with pytest.raises(ValueError, match="no KiCad files"):
            pm.use_smart(str(empty))


class TestCaseInsensitiveUse:
    """Test that use_smart is case-insensitive for registered projects."""

    def test_use_registered_case_insensitive(self, tmp_path):
        from footfindr.project import ProjectManager
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        pm.start("My-Project", "my.kicad_sch")
        meta, msg = pm.use_smart("my-project")
        assert meta.name == "My-Project"

    def test_use_discovered_case_insensitive(self, tmp_path):
        from footfindr.project import ProjectManager
        _create_kicad_project(tmp_path, "CamelCase")
        pm = ProjectManager(workspace=tmp_path / ".footfindr")
        with patch("footfindr.kicad.discovery.get_default_search_roots",
                   return_value=[tmp_path]):
            meta, msg = pm.use_smart("camelcase")
            assert meta.name == "CamelCase"


class TestProjectTypeInToDict:
    """Test project_type in to_dict output."""

    def test_full_project_type(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "full-td", with_pro=True)
        proj = discover_kicad_project(tmp_path / "full-td")
        d = proj.to_dict()
        assert d["project_type"] == "full"

    def test_sch_only_project_type(self, tmp_path):
        from footfindr.kicad.discovery import discover_kicad_project
        _create_kicad_project(tmp_path, "sch-td", with_pro=False)
        proj = discover_kicad_project(tmp_path / "sch-td")
        d = proj.to_dict()
        assert d["project_type"] == "schematic-only"


class TestNewImports:
    """Test M9.1b additions are importable."""

    def test_match_project_name(self):
        from footfindr.kicad.discovery import match_project_name
        assert match_project_name is not None

    def test_diagnose_project(self):
        from footfindr.kicad.discovery import diagnose_project
        assert diagnose_project is not None
