"""Tests for the library manager."""

from pathlib import Path

import pytest


class TestLibraryManager:
    """Test library CRUD operations."""

    def test_create_library(self, workspace: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager(workspace=workspace)
        lib = mgr.create_library("test-master", "master")
        assert lib.name == "test-master"
        assert lib.kind.value == "master"

    def test_create_duplicate_fails(self, workspace: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager(workspace=workspace)
        mgr.create_library("test-lib", "master")

        with pytest.raises(ValueError, match="already exists"):
            mgr.create_library("test-lib", "master")

    def test_list_libraries(self, workspace: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager(workspace=workspace)
        mgr.create_library("lib-a", "master")
        mgr.create_library("lib-b", "approved")

        libs = mgr.list_libraries()
        assert len(libs) == 2
        names = {l.name for l in libs}
        assert names == {"lib-a", "lib-b"}

    def test_set_active(self, workspace: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager(workspace=workspace)
        mgr.create_library("active-lib", "approved")
        mgr.set_active("active-lib")

        assert mgr.get_active_name() == "active-lib"

    def test_set_active_nonexistent_fails(self, workspace: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager(workspace=workspace)
        with pytest.raises(ValueError, match="not found"):
            mgr.set_active("nonexistent")

    def test_tree_hierarchy(self, workspace: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager(workspace=workspace)
        mgr.create_library("parent", "master")
        mgr.create_library("child", "sub", parent="parent")

        tree = mgr.get_tree()
        assert "parent" in tree
        assert "child" in tree["parent"]["children"]


class TestApprovedPartsLoading:
    """Test loading approved parts from YAML."""

    def test_load_approved_parts(self, approved_parts_path: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager()
        parts = mgr._parse_approved_yaml(approved_parts_path)
        assert len(parts) > 0

        # Check that the seed data has expected parts
        pns = {p.internal_pn for p in parts}
        assert "CAP-100N-16V-X7R-0603" in pns
        assert "CAP-10U-16V-X7R-0805" in pns
        assert "RES-10K-1PCT-0603" in pns

    def test_parts_have_footprints(self, approved_parts_path: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager()
        parts = mgr._parse_approved_yaml(approved_parts_path)

        for p in parts:
            if p.approved:
                assert p.footprint, f"Approved part {p.internal_pn} is missing footprint"

    def test_parts_have_categories(self, approved_parts_path: Path) -> None:
        from footfindr.libraries.manager import LibraryManager

        mgr = LibraryManager()
        parts = mgr._parse_approved_yaml(approved_parts_path)

        for p in parts:
            assert p.category is not None
            assert p.category.value in (
                "capacitor", "resistor", "inductor", "ic", "connector",
                "diode", "transistor", "crystal", "led", "other",
            )
