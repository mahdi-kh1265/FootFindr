"""Tests for project context management."""

from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def proj_workspace(tmp_path: Path) -> Path:
    """Create a temp workspace for project tests."""
    ws = tmp_path / ".footfindr"
    ws.mkdir()
    return ws


class TestProjectManager:
    """Test ProjectManager lifecycle operations."""

    def test_start_creates_project(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        meta = pm.start("test-board", "board.kicad_sch")

        assert meta.name == "test-board"
        assert meta.schematic == "board.kicad_sch"
        assert meta.status == "active"
        assert meta.created is not None

    def test_start_duplicate_raises(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("test-board", "board.kicad_sch")

        with pytest.raises(ValueError, match="already exists"):
            pm.start("test-board", "other.kicad_sch")

    def test_status_returns_metadata(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("fpga-lock", "fpga.kicad_sch", build_quantity=5)

        meta = pm.status("fpga-lock")
        assert meta.name == "fpga-lock"
        assert meta.schematic == "fpga.kicad_sch"
        assert meta.build_quantity == 5

    def test_status_missing_raises(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        with pytest.raises(ValueError, match="not found"):
            pm.status("nonexistent")

    def test_use_sets_active(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("board-a", "a.kicad_sch")
        pm.start("board-b", "b.kicad_sch")

        pm.use("board-b")
        assert pm.get_active_name() == "board-b"

        active = pm.get_active()
        assert active is not None
        assert active.schematic == "b.kicad_sch"

    def test_use_missing_raises(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        with pytest.raises(ValueError, match="not found"):
            pm.use("nonexistent")

    def test_list_all_returns_projects(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("alpha", "alpha.kicad_sch")
        pm.start("beta", "beta.kicad_sch")

        projects = pm.list_all()
        assert len(projects) == 2
        names = {p.name for p in projects}
        assert names == {"alpha", "beta"}

    def test_end_archives_project(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("temp-board", "temp.kicad_sch")
        pm.use("temp-board")
        assert pm.get_active_name() == "temp-board"

        pm.end("temp-board")
        meta = pm.status("temp-board")
        assert meta.status == "ended"
        # Active project should be cleared
        assert pm.get_active_name() is None

    def test_get_active_returns_none_when_no_active(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        assert pm.get_active() is None
        assert pm.get_active_name() is None

    def test_update_last_resolve(self, proj_workspace):
        from footfindr.project import ProjectManager

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("tracked", "tracked.kicad_sch")
        pm.update_last_resolve("tracked", decision_log="log.md")

        meta = pm.status("tracked")
        assert meta.last_resolve is not None
        assert meta.last_decision_log == "log.md"


class TestResolveSchematicPath:
    """Test the resolve_schematic_path helper."""

    def test_explicit_path_returns_directly(self, proj_workspace, simple_schematic_path):
        from footfindr.project import resolve_schematic_path

        result = resolve_schematic_path(str(simple_schematic_path), workspace=proj_workspace)
        assert result == str(simple_schematic_path)

    def test_kicad_sch_suffix_returns_directly(self, proj_workspace):
        from footfindr.project import resolve_schematic_path

        result = resolve_schematic_path("nonexistent.kicad_sch", workspace=proj_workspace)
        assert result == "nonexistent.kicad_sch"

    def test_active_project_used_when_no_explicit(self, proj_workspace):
        from footfindr.project import ProjectManager, resolve_schematic_path

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("proj1", "my_board.kicad_sch")
        pm.use("proj1")

        result = resolve_schematic_path(workspace=proj_workspace)
        assert result == "my_board.kicad_sch"

    def test_error_when_no_schematic_no_project(self, proj_workspace):
        from unittest.mock import patch
        from footfindr.project import resolve_schematic_path

        with patch("footfindr.kicad.discovery.find_nearest_kicad_project",
                   return_value=None):
            with pytest.raises(ValueError, match="No active project"):
                resolve_schematic_path(workspace=proj_workspace)

    def test_explicit_overrides_active_project(self, proj_workspace, simple_schematic_path):
        from footfindr.project import ProjectManager, resolve_schematic_path

        pm = ProjectManager(workspace=proj_workspace)
        pm.start("proj1", "project_board.kicad_sch")
        pm.use("proj1")

        result = resolve_schematic_path(str(simple_schematic_path), workspace=proj_workspace)
        assert result == str(simple_schematic_path)
