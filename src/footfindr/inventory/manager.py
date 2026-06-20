"""Local inventory management for FootFindr.

Tracks on-hand quantities of parts in .footfindr/inventory.yaml.
No supplier API connections -- purely local storage.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from footfindr.config import get_workspace
from footfindr.inventory.models import InventoryEntry, InventoryFile


@dataclass
class ShortageItem:
    """A single shortage report item."""
    internal_pn: str
    required: int
    on_hand: int
    shortage: int
    location: str = ""


class InventoryManager:
    """Manages local part inventory stored in .footfindr/inventory.yaml."""

    def __init__(self, workspace: Optional[str | Path] = None) -> None:
        self._workspace = Path(workspace) if workspace else get_workspace()
        self._path = self._workspace / "inventory.yaml"

    # ---- CRUD ----

    def receive(
        self,
        internal_pn: str,
        qty: int,
        *,
        location: str = "",
        notes: str = "",
    ) -> InventoryEntry:
        """Add stock for a part. Creates entry if it doesn't exist."""
        inv = self._load()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        entry = self._find_entry(inv, internal_pn)
        if entry:
            entry.qty_on_hand += qty
            if location:
                entry.location = location
            if notes:
                entry.notes = notes
            entry.last_updated = now
        else:
            entry = InventoryEntry(
                internal_pn=internal_pn,
                qty_on_hand=qty,
                location=location,
                notes=notes,
                last_updated=now,
            )
            inv.entries.append(entry)

        self._save(inv)
        return entry

    def locate(self, internal_pn: str) -> InventoryEntry | None:
        """Find where a part is stored."""
        inv = self._load()
        return self._find_entry(inv, internal_pn)

    def get_all(self) -> list[InventoryEntry]:
        """Return all inventory entries."""
        return self._load().entries

    def check(
        self,
        bom_requirements: dict[str, int],
        builds: int = 1,
    ) -> list[ShortageItem]:
        """Compare BOM requirements against inventory.

        Parameters
        ----------
        bom_requirements : dict
            Mapping of internal_pn -> quantity per build.
        builds : int
            Number of builds to check.

        Returns
        -------
        list of ShortageItem
            All items, including those with sufficient stock.
        """
        inv = self._load()
        results: list[ShortageItem] = []

        for ipn, qty_per_build in sorted(bom_requirements.items()):
            required = qty_per_build * builds
            entry = self._find_entry(inv, ipn)
            on_hand = entry.qty_on_hand if entry else 0
            shortage = max(0, required - on_hand)
            results.append(ShortageItem(
                internal_pn=ipn,
                required=required,
                on_hand=on_hand,
                shortage=shortage,
                location=entry.location if entry else "",
            ))

        return results

    def shortage(
        self,
        bom_requirements: dict[str, int],
        builds: int = 1,
    ) -> list[ShortageItem]:
        """Return only items with insufficient stock."""
        all_items = self.check(bom_requirements, builds)
        return [item for item in all_items if item.shortage > 0]

    # ---- Persistence ----

    def _load(self) -> InventoryFile:
        if not self._path.exists():
            return InventoryFile()
        with open(self._path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return InventoryFile.model_validate(raw)

    def _save(self, data: InventoryFile) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            yaml.dump(
                data.model_dump(),
                fh, default_flow_style=False, sort_keys=False,
            )

    @staticmethod
    def _find_entry(inv: InventoryFile, internal_pn: str) -> InventoryEntry | None:
        for e in inv.entries:
            if e.internal_pn == internal_pn:
                return e
        return None
