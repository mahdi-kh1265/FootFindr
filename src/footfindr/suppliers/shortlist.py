"""Supplier part shortlist manager.

JSON-backed shortlist at ``.footfindr/session/shortlist.json``.
Project-local if inside a FootFindr project, otherwise workspace-local.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footfindr.suppliers.models import SupplierPart

logger = logging.getLogger("footfindr.suppliers.shortlist")


@dataclass
class ShortlistEntry:
    """A part on the supplier shortlist."""
    mpn: str
    supplier: str
    supplier_pn: str | None = None
    manufacturer: str | None = None
    description: str | None = None
    package: str | None = None
    added_at: str | None = None
    project: str | None = None
    notes: str | None = None
    result_id: str | None = None  # Stable identity key

    def to_dict(self) -> dict[str, Any]:
        return {
            "mpn": self.mpn,
            "supplier": self.supplier,
            "supplier_pn": self.supplier_pn,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "package": self.package,
            "added_at": self.added_at,
            "project": self.project,
            "notes": self.notes,
            "result_id": self.result_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShortlistEntry:
        return cls(
            mpn=d.get("mpn", ""),
            supplier=d.get("supplier", ""),
            supplier_pn=d.get("supplier_pn"),
            manufacturer=d.get("manufacturer"),
            description=d.get("description"),
            package=d.get("package"),
            added_at=d.get("added_at"),
            project=d.get("project"),
            notes=d.get("notes"),
            result_id=d.get("result_id"),
        )

    @classmethod
    def from_supplier_part(
        cls,
        part: SupplierPart,
        *,
        project: str | None = None,
        notes: str | None = None,
    ) -> ShortlistEntry:
        return cls(
            mpn=part.mpn,
            supplier=part.supplier,
            supplier_pn=part.supplier_pn,
            manufacturer=part.manufacturer,
            description=part.description,
            package=part.package or part.supplier_device_package,
            added_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            project=project,
            notes=notes,
            result_id=part.result_id,
        )


class Shortlist:
    """Manage the supplier part shortlist."""

    def __init__(self, workspace: Path | None = None) -> None:
        from footfindr.config import get_workspace as _gw
        ws = Path(workspace) if workspace else _gw()
        self._session_dir = ws / "session"
        self._file = self._session_dir / "shortlist.json"

    def _load(self) -> list[ShortlistEntry]:
        if not self._file.exists():
            return []
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return [ShortlistEntry.from_dict(d) for d in data]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load shortlist: {e}")
            return []

    def _save(self, entries: list[ShortlistEntry]) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        data = [e.to_dict() for e in entries]
        self._file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def add(self, entry: ShortlistEntry) -> None:
        """Add an entry. Deduplicates by result_id."""
        entries = self._load()
        # Deduplicate
        if entry.result_id:
            entries = [e for e in entries if e.result_id != entry.result_id]
        entries.append(entry)
        self._save(entries)

    def remove(self, *, index: int | None = None, result_id: str | None = None) -> bool:
        """Remove by 1-based index or result_id. Returns True if removed."""
        entries = self._load()
        if index is not None:
            if 1 <= index <= len(entries):
                entries.pop(index - 1)
                self._save(entries)
                return True
            return False
        if result_id is not None:
            new = [e for e in entries if e.result_id != result_id]
            if len(new) < len(entries):
                self._save(new)
                return True
            return False
        return False

    def list(self) -> list[ShortlistEntry]:
        """Return all shortlist entries."""
        return self._load()

    def clear(self) -> None:
        """Remove all entries."""
        if self._file.exists():
            self._file.unlink()
