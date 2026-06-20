"""Pydantic models for the FootFindr library system.

These are the serialisation/validation models used for YAML/JSON persistence.
The core data model lives in ``footfindr.core.models`` — these Pydantic models
map to/from those dataclasses for I/O.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SupplierPNs(BaseModel):
    """Supplier part number references."""
    dkpn: str | None = None
    mouser_pn: str | None = None
    lcsc_pn: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class ApprovedPartSchema(BaseModel):
    """Pydantic schema for a single approved part in YAML."""
    internal_pn: str
    category: str
    value: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    description: str | None = None

    # Electrical specs (flat for YAML readability)
    capacitance: str | None = None
    resistance: str | None = None
    inductance: str | None = None
    voltage_rating: str | None = None
    current_rating: str | None = None
    power_rating: str | None = None
    tolerance: str | None = None
    dielectric: str | None = None
    tempco: str | None = None

    # Physical
    package: str | None = None
    footprint: str | None = None

    # Supplier
    supplier_pns: SupplierPNs | None = None

    # Status
    approved: bool = True
    status: str = "approved"
    lifecycle: str = "unknown"

    # Library provenance
    library: str | None = None
    source_library: str | None = None
    source_vendor: str | None = None
    source_series: str | None = None
    source_pack: str | None = None
    source_file: str | None = None
    source_row: int | None = None
    promoted_at: str | None = None
    promoted_from: str | None = None

    # Docs
    datasheet_url: str | None = None
    datasheet_path: str | None = None

    notes: str | None = None

    model_config = {"extra": "allow", "coerce_numbers_to_str": True}


class ApprovedPartsFile(BaseModel):
    """Top-level schema for an ``approved_parts.yaml`` file."""
    parts: list[ApprovedPartSchema] = Field(default_factory=list)


class LibraryMetadataSchema(BaseModel):
    """Pydantic schema for library metadata stored in ``libraries.yaml``."""
    name: str
    kind: str  # master, sub, raw_vendor, approved, project
    parent: str | None = None
    description: str | None = None
    active: bool = False
    parts_file: str | None = None
    created: str | None = None
    modified: str | None = None


class LibrariesFile(BaseModel):
    """Top-level schema for ``.footfindr/libraries.yaml``."""
    libraries: list[LibraryMetadataSchema] = Field(default_factory=list)
    active_library: str | None = None
