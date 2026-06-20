"""Inventory data models.

Future: unit can be each, reel, strip, length_mm, grams, etc.
For now, all quantities are integer counts (each).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class InventoryEntry(BaseModel):
    """A single inventory record."""
    internal_pn: str
    qty_on_hand: int = 0
    qty_reserved: int = 0
    location: str = ""
    notes: str = ""
    last_updated: str = ""
    # TODO: Future - add unit field (each, reel, strip, length_mm, grams)


class InventoryFile(BaseModel):
    """Top-level schema for .footfindr/inventory.yaml."""
    entries: list[InventoryEntry] = Field(default_factory=list)
