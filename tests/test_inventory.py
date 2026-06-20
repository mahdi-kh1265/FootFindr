"""Tests for local inventory management."""

from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def inv_workspace(tmp_path: Path) -> Path:
    """Create temp workspace for inventory tests."""
    ws = tmp_path / ".footfindr"
    ws.mkdir()
    return ws


class TestInventoryManager:
    """Test InventoryManager operations."""

    def test_receive_creates_entry(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        entry = mgr.receive("CAP-100N-16V-X7R-0603", 50, location="Drawer A1")

        assert entry.internal_pn == "CAP-100N-16V-X7R-0603"
        assert entry.qty_on_hand == 50
        assert entry.location == "Drawer A1"
        assert entry.last_updated != ""

    def test_receive_adds_to_existing(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        mgr.receive("CAP-100N-16V-X7R-0603", 50, location="Drawer A1")
        entry = mgr.receive("CAP-100N-16V-X7R-0603", 30)

        assert entry.qty_on_hand == 80
        assert entry.location == "Drawer A1"  # Location preserved

    def test_receive_updates_location(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        mgr.receive("CAP-100N-16V-X7R-0603", 50, location="Drawer A1")
        entry = mgr.receive("CAP-100N-16V-X7R-0603", 10, location="Drawer B2")

        assert entry.location == "Drawer B2"

    def test_locate_finds_entry(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        mgr.receive("RES-10K-1PCT-0603", 100, location="Drawer C3")

        entry = mgr.locate("RES-10K-1PCT-0603")
        assert entry is not None
        assert entry.qty_on_hand == 100
        assert entry.location == "Drawer C3"

    def test_locate_returns_none_for_missing(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        assert mgr.locate("NONEXISTENT-PART") is None

    def test_check_identifies_shortages(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        mgr.receive("CAP-100N-16V-X7R-0603", 10)
        mgr.receive("RES-10K-1PCT-0603", 50)

        requirements = {
            "CAP-100N-16V-X7R-0603": 5,  # Need 5 per build
            "RES-10K-1PCT-0603": 20,      # Need 20 per build
            "IC-MISSING": 1,              # Not in inventory
        }

        results = mgr.check(requirements, builds=2)
        assert len(results) == 3

        # CAP: need 10, have 10 -> no shortage
        cap = next(r for r in results if r.internal_pn == "CAP-100N-16V-X7R-0603")
        assert cap.required == 10
        assert cap.on_hand == 10
        assert cap.shortage == 0

        # RES: need 40, have 50 -> no shortage
        res = next(r for r in results if r.internal_pn == "RES-10K-1PCT-0603")
        assert res.required == 40
        assert res.on_hand == 50
        assert res.shortage == 0

        # IC: need 2, have 0 -> shortage 2
        ic = next(r for r in results if r.internal_pn == "IC-MISSING")
        assert ic.required == 2
        assert ic.on_hand == 0
        assert ic.shortage == 2

    def test_shortage_returns_only_shortages(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        mgr.receive("CAP-100N-16V-X7R-0603", 100)

        requirements = {
            "CAP-100N-16V-X7R-0603": 10,
            "RES-MISSING": 5,
        }

        shortages = mgr.shortage(requirements, builds=3)
        assert len(shortages) == 1
        assert shortages[0].internal_pn == "RES-MISSING"
        assert shortages[0].shortage == 15

    def test_check_multi_build(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        mgr.receive("CAP-10U-16V-X7R-0805", 100, location="Drawer C3")

        requirements = {"CAP-10U-16V-X7R-0805": 15}

        # 3 builds = 45 needed, have 100
        results = mgr.check(requirements, builds=3)
        assert results[0].shortage == 0

        # 10 builds = 150 needed, have 100
        results = mgr.check(requirements, builds=10)
        assert results[0].shortage == 50

    def test_persistence_across_instances(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr1 = InventoryManager(workspace=inv_workspace)
        mgr1.receive("CAP-100N-16V-X7R-0603", 42, location="Box 7")

        mgr2 = InventoryManager(workspace=inv_workspace)
        entry = mgr2.locate("CAP-100N-16V-X7R-0603")
        assert entry is not None
        assert entry.qty_on_hand == 42
        assert entry.location == "Box 7"

    def test_get_all(self, inv_workspace):
        from footfindr.inventory.manager import InventoryManager

        mgr = InventoryManager(workspace=inv_workspace)
        mgr.receive("PART-A", 10)
        mgr.receive("PART-B", 20)
        mgr.receive("PART-C", 30)

        all_entries = mgr.get_all()
        assert len(all_entries) == 3
        pns = {e.internal_pn for e in all_entries}
        assert pns == {"PART-A", "PART-B", "PART-C"}
