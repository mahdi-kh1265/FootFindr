"""Library manager — CRUD for FootFindr libraries and approved parts loading.

Manages library metadata (master, sub, raw_vendor, approved, project) stored
in ``.footfindr/libraries.yaml`` and loads part data from YAML files.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

import yaml

from footfindr.config import get_workspace
from footfindr.core.models import (
    ComponentCategory,
    ElectricalSpecs,
    LibraryKind,
    LibraryMetadata,
    PartRecord,
    PartStatus,
)
from footfindr.libraries.models import (
    ApprovedPartSchema,
    ApprovedPartsFile,
    LibrariesFile,
    LibraryMetadataSchema,
)


class LibraryManager:
    """Manages FootFindr libraries and approved part loading."""

    def __init__(self, workspace: Optional[str | Path] = None) -> None:
        self._workspace = Path(workspace) if workspace else get_workspace()
        self._libraries_path = self._workspace / "libraries.yaml"
        self._raw_dir = self._workspace / "raw"
        self._approved_dir = self._workspace / "approved"

    @property
    def workspace(self) -> Path:
        return self._workspace

    # ----- Library CRUD -----

    def create_library(
        self,
        name: str,
        kind: str,
        *,
        parent: Optional[str] = None,
        description: Optional[str] = None,
    ) -> LibraryMetadata:
        """Create a new library."""
        self._workspace.mkdir(parents=True, exist_ok=True)

        libs = self._load_libraries_file()
        # Check for duplicate
        for lib in libs.libraries:
            if lib.name == name:
                raise ValueError(f"Library '{name}' already exists")

        # Validate parent for sub-libraries
        if kind == "sub" and parent:
            parent_exists = any(l.name == parent for l in libs.libraries)
            if not parent_exists:
                raise ValueError(f"Parent library '{parent}' not found")

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        meta = LibraryMetadataSchema(
            name=name,
            kind=kind,
            parent=parent,
            description=description,
            created=now,
            modified=now,
        )
        libs.libraries.append(meta)
        self._save_libraries_file(libs)

        return LibraryMetadata(
            name=name,
            kind=LibraryKind(kind),
            parent=parent,
            description=description,
            created=now,
            modified=now,
        )

    def list_libraries(self) -> list[LibraryMetadata]:
        """List all registered libraries."""
        libs = self._load_libraries_file()
        result = []
        for lib in libs.libraries:
            result.append(LibraryMetadata(
                name=lib.name,
                kind=LibraryKind(lib.kind),
                parent=lib.parent,
                description=lib.description,
                active=lib.name == libs.active_library,
                parts_file=lib.parts_file,
                created=lib.created,
                modified=lib.modified,
            ))
        return result

    def get_tree(self) -> dict:
        """Return a nested dict representing the library hierarchy."""
        libs = self.list_libraries()
        by_name = {l.name: l for l in libs}
        tree: dict = {}

        # Build tree: masters at root, subs nested under parents
        for lib in libs:
            if not lib.parent or lib.parent not in by_name:
                tree[lib.name] = {
                    "kind": lib.kind,
                    "active": lib.active,
                    "children": {},
                }

        for lib in libs:
            if lib.parent and lib.parent in tree:
                tree[lib.parent]["children"][lib.name] = {
                    "kind": lib.kind,
                    "active": lib.active,
                    "children": {},
                }

        return tree

    def set_active(self, name: str) -> None:
        """Set the active library."""
        libs = self._load_libraries_file()
        found = any(l.name == name for l in libs.libraries)
        if not found:
            raise ValueError(f"Library '{name}' not found")
        libs.active_library = name
        self._save_libraries_file(libs)

    def get_active_name(self) -> Optional[str]:
        """Return the name of the active library, or None."""
        libs = self._load_libraries_file()
        return libs.active_library

    # ----- Part loading -----

    def load_approved_parts(
        self,
        path: Optional[str | Path] = None,
    ) -> list[PartRecord]:
        """Load approved parts from YAML files.

        If no path is given, loads from ``schemas/approved_parts.yaml`` and
        all approved-type library parts files registered in ``libraries.yaml``.
        """
        all_parts: list[PartRecord] = []

        if path is not None:
            return self._parse_approved_yaml(Path(path))

        # Load from default schema files
        candidates = [
            Path("schemas/approved_parts.yaml"),
            Path("schemas/approved_parts.example.yaml"),
        ]
        for c in candidates:
            if c.exists():
                all_parts.extend(self._parse_approved_yaml(c))
                break

        # Also load from all approved-type library files
        libs = self._load_libraries_file()
        for lib in libs.libraries:
            if lib.kind == "approved" and lib.parts_file:
                p = Path(lib.parts_file)
                if p.exists():
                    all_parts.extend(self._parse_approved_yaml(p))

        # Deduplicate by internal_pn (keep first occurrence)
        seen: set[str] = set()
        deduped: list[PartRecord] = []
        for part in all_parts:
            if part.internal_pn not in seen:
                seen.add(part.internal_pn)
                deduped.append(part)

        return deduped

    def _parse_approved_yaml(self, path: Path) -> list[PartRecord]:
        """Parse an approved_parts YAML file into PartRecord objects."""
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

        parts_list = raw.get("parts", [])
        result: list[PartRecord] = []

        for item in parts_list:
            schema = ApprovedPartSchema.model_validate(item)
            record = self._schema_to_record(schema)
            result.append(record)

        return result

    def save_approved_parts(self, parts: list[PartRecord], path: str | Path) -> None:
        """Save parts to a YAML file."""
        items = []
        for p in parts:
            item = self._record_to_dict(p)
            items.append(item)

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump({"parts": items}, fh, default_flow_style=False, sort_keys=False)

    # ----- Conversion helpers -----

    @staticmethod
    def _schema_to_record(schema: ApprovedPartSchema) -> PartRecord:
        """Convert a Pydantic schema to a PartRecord."""
        cat = ComponentCategory.OTHER
        try:
            cat = ComponentCategory(schema.category.lower())
        except ValueError:
            pass

        status = PartStatus.APPROVED
        try:
            status = PartStatus(schema.status.lower())
        except ValueError:
            pass

        specs = ElectricalSpecs(
            capacitance=schema.capacitance,
            resistance=schema.resistance,
            inductance=schema.inductance,
            voltage_rating=schema.voltage_rating,
            current_rating=schema.current_rating,
            power_rating=schema.power_rating,
            tolerance=schema.tolerance,
            dielectric=schema.dielectric,
            tempco=schema.tempco,
        )

        supplier_pns: dict[str, str] = {}
        if schema.supplier_pns:
            if schema.supplier_pns.dkpn:
                supplier_pns["digikey"] = schema.supplier_pns.dkpn
            if schema.supplier_pns.mouser_pn:
                supplier_pns["mouser"] = schema.supplier_pns.mouser_pn
            if schema.supplier_pns.lcsc_pn:
                supplier_pns["lcsc"] = schema.supplier_pns.lcsc_pn
            supplier_pns.update(schema.supplier_pns.extra)

        return PartRecord(
            internal_pn=schema.internal_pn,
            category=cat,
            manufacturer=schema.manufacturer,
            mpn=schema.mpn,
            description=schema.description,
            value=schema.value,
            status=status,
            approved=schema.approved,
            specs=specs,
            package=schema.package,
            footprint=schema.footprint,
            supplier_pns=supplier_pns,
            source_library=schema.library or schema.source_library,
            source_vendor=schema.source_vendor,
            source_series=schema.source_series,
            source_pack=schema.source_pack,
            source_file=schema.source_file,
            source_row=schema.source_row,
            promoted_at=schema.promoted_at,
            promoted_from=schema.promoted_from,
            notes=schema.notes,
        )

    @staticmethod
    def _record_to_dict(p: PartRecord) -> dict:
        """Convert a PartRecord to a plain dict for YAML serialisation."""
        d: dict = {
            "internal_pn": p.internal_pn,
            "category": p.category.value,
        }
        if p.value:
            d["value"] = p.value
        if p.manufacturer:
            d["manufacturer"] = p.manufacturer
        if p.mpn:
            d["mpn"] = p.mpn
        if p.description:
            d["description"] = p.description
        if p.specs.capacitance:
            d["capacitance"] = p.specs.capacitance
        if p.specs.resistance:
            d["resistance"] = p.specs.resistance
        if p.specs.voltage_rating:
            d["voltage_rating"] = p.specs.voltage_rating
        if p.specs.power_rating:
            d["power_rating"] = p.specs.power_rating
        if p.specs.current_rating:
            d["current_rating"] = p.specs.current_rating
        if p.specs.tolerance:
            d["tolerance"] = p.specs.tolerance
        if p.specs.dielectric:
            d["dielectric"] = p.specs.dielectric
        if p.package:
            d["package"] = p.package
        if p.footprint:
            d["footprint"] = p.footprint
        d["approved"] = p.approved
        d["status"] = p.status.value
        if p.source_library:
            d["library"] = p.source_library
        if p.source_vendor:
            d["source_vendor"] = p.source_vendor
        if p.source_series:
            d["source_series"] = p.source_series
        if p.source_pack:
            d["source_pack"] = p.source_pack
        if p.source_file:
            d["source_file"] = p.source_file
        if p.source_row is not None:
            d["source_row"] = p.source_row
        if p.promoted_at:
            d["promoted_at"] = p.promoted_at
        if p.promoted_from:
            d["promoted_from"] = p.promoted_from
        if p.notes:
            d["notes"] = p.notes
        return d

    # ----- Raw library storage for ingest -----

    def save_raw_library(self, name: str, parts: list[PartRecord]) -> Path:
        """Save parts to a raw vendor library YAML file."""
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace(" ", "_").lower()
        path = self._raw_dir / f"{safe_name}.yaml"
        self.save_approved_parts(parts, path)

        # Register library if not already registered
        libs = self._load_libraries_file()
        if not any(l.name == name for l in libs.libraries):
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            libs.libraries.append(LibraryMetadataSchema(
                name=name,
                kind="raw_vendor",
                parts_file=str(path),
                created=now,
                modified=now,
            ))
            self._save_libraries_file(libs)

        return path

    def load_raw_library(self, name: str) -> list[PartRecord]:
        """Load parts from a raw vendor library."""
        libs = self._load_libraries_file()
        for lib in libs.libraries:
            if lib.name == name and lib.parts_file:
                p = Path(lib.parts_file)
                if p.exists():
                    return self._parse_approved_yaml(p)
        return []

    # ----- Persistence helpers -----

    def _load_libraries_file(self) -> LibrariesFile:
        """Load or create the libraries.yaml file."""
        if not self._libraries_path.exists():
            return LibrariesFile()
        with open(self._libraries_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return LibrariesFile.model_validate(raw)

    def _save_libraries_file(self, data: LibrariesFile) -> None:
        """Save the libraries.yaml file."""
        self._workspace.mkdir(parents=True, exist_ok=True)
        with open(self._libraries_path, "w", encoding="utf-8") as fh:
            yaml.dump(
                data.model_dump(exclude_none=True),
                fh,
                default_flow_style=False,
                sort_keys=False,
            )
