"""Datasheet index — tracks local PDF files and their extracted data."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from footfindr.config import get_workspace
from footfindr.datasheets.models import DatasheetRecord


class DatasheetIndex:
    """Manages the local datasheet index."""

    def __init__(self, workspace: Optional[str | Path] = None) -> None:
        self._workspace = Path(workspace) if workspace else get_workspace()
        self._index_path = self._workspace / "datasheets" / "index.yaml"
        self._cache: dict[str, DatasheetRecord] | None = None

    def add(self, mpn: str, pdf_path: str | Path, *, notes: str | None = None) -> DatasheetRecord:
        """Register a local PDF file for an MPN."""
        records = self._load()
        record = DatasheetRecord(
            mpn=mpn,
            local_path=str(Path(pdf_path).resolve()),
            notes=notes,
        )
        records[mpn] = record
        self._save(records)
        return record

    def get(self, mpn: str) -> Optional[DatasheetRecord]:
        """Get a datasheet record by MPN."""
        records = self._load()
        return records.get(mpn)

    def list_all(self) -> list[DatasheetRecord]:
        """List all indexed datasheets."""
        return list(self._load().values())

    def update_extracted(self, mpn: str, text_path: str, json_path: str | None = None) -> None:
        """Update extraction results for a datasheet."""
        records = self._load()
        if mpn in records:
            records[mpn].extracted_text_path = text_path
            if json_path:
                records[mpn].extracted_json_path = json_path
            self._save(records)

    def _load(self) -> dict[str, DatasheetRecord]:
        """Load the index from disk."""
        if self._cache is not None:
            return self._cache
        if not self._index_path.exists():
            self._cache = {}
            return self._cache

        with open(self._index_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

        records = {}
        for mpn, data in raw.get("datasheets", {}).items():
            records[mpn] = DatasheetRecord.model_validate({"mpn": mpn, **data})
        self._cache = records
        return records

    def _save(self, records: dict[str, DatasheetRecord]) -> None:
        """Persist the index to disk."""
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"datasheets": {}}
        for mpn, rec in records.items():
            d = rec.model_dump(exclude_none=True)
            d.pop("mpn", None)
            data["datasheets"][mpn] = d

        with open(self._index_path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
        self._cache = records
