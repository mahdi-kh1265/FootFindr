"""Datasheet document model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatasheetRecord(BaseModel):
    """A datasheet record in the FootFindr document index."""
    mpn: str
    local_path: str | None = None
    url: str | None = None
    sha256: str | None = None
    extracted_text_path: str | None = None
    extracted_json_path: str | None = None
    source: str | None = None
    notes: str | None = None
