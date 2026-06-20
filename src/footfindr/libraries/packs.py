"""Vendor library pack management — build, validate, install, uninstall.

A vendor pack is a versioned, portable directory containing normalised part
data from a vendor catalog (e.g. Murata GRM MLCCs).

Pack lifecycle:
    1. User downloads/exports a vendor CSV.
    2. ``ff lib pack build`` normalises it into a pack directory.
    3. ``ff lib install`` copies/registers the pack locally.
    4. ``ff lib search`` finds raw parts in installed packs.
    5. ``ff lib promote`` moves selected parts into POSM approved.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from footfindr.core.models import PartRecord


def _sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    """Compute SHA256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Pack metadata model
# ---------------------------------------------------------------------------

@dataclass
class PackSource:
    """Source provenance for a vendor pack."""
    source_type: str = "unknown"       # manual_csv, fixture, api, etc.
    real_source: bool = False
    is_complete_catalog: bool = False
    source_file: str | None = None
    source_sha256: str | None = None
    source_url: str | None = None
    downloaded_at: str | None = None
    notes: str | None = None


@dataclass
class PackLicense:
    """License information for a vendor pack."""
    redistribution_status: str = "unknown"  # unknown, private_only, permitted, restricted
    notes: str | None = None


@dataclass
class PackCounts:
    """Import counts for a vendor pack."""
    raw_rows: int = 0
    imported_parts: int = 0
    skipped_rows: int = 0


@dataclass
class PackParser:
    """Parser metadata for a vendor pack."""
    name: str = ""          # e.g. "MurataGRMParser"
    slug: str = ""          # e.g. "murata-grm"
    version: str = "1.0.0"


@dataclass
class PackHashes:
    """SHA256 hashes for pack integrity verification."""
    source_csv: str | None = None
    normalized_yaml: str | None = None
    normalized_jsonl: str | None = None


@dataclass
class PackMetadata:
    """Metadata for a FootFindr vendor library pack (footfindr_pack.yaml)."""
    pack_name: str
    display_name: str
    vendor: str
    series: str = ""
    category: str = "capacitor"
    kind: str = "raw_vendor"
    version: str = "1.0.0"
    generated_at: str = ""
    footfindr_min_version: str = "0.1.0"
    source: PackSource = field(default_factory=PackSource)
    counts: PackCounts = field(default_factory=PackCounts)
    parser: PackParser = field(default_factory=PackParser)
    hashes: PackHashes = field(default_factory=PackHashes)
    license: PackLicense = field(default_factory=PackLicense)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML."""
        return {
            "pack_name": self.pack_name,
            "display_name": self.display_name,
            "vendor": self.vendor,
            "series": self.series,
            "category": self.category,
            "kind": self.kind,
            "version": self.version,
            "generated_at": self.generated_at,
            "footfindr_min_version": self.footfindr_min_version,
            "source": {
                "source_type": self.source.source_type,
                "real_source": self.source.real_source,
                "is_complete_catalog": self.source.is_complete_catalog,
                "source_file": self.source.source_file,
                "source_sha256": self.source.source_sha256,
                "source_url": self.source.source_url,
                "downloaded_at": self.source.downloaded_at,
                "notes": self.source.notes,
            },
            "counts": {
                "raw_rows": self.counts.raw_rows,
                "imported_parts": self.counts.imported_parts,
                "skipped_rows": self.counts.skipped_rows,
            },
            "parser": {
                "name": self.parser.name,
                "slug": self.parser.slug,
                "version": self.parser.version,
            },
            "hashes": {
                "source_csv": self.hashes.source_csv,
                "normalized_yaml": self.hashes.normalized_yaml,
                "normalized_jsonl": self.hashes.normalized_jsonl,
            },
            "license": {
                "redistribution_status": self.license.redistribution_status,
                "notes": self.license.notes,
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackMetadata:
        """Deserialize from a dict."""
        src = d.get("source", {})
        lic = d.get("license", {})
        counts = d.get("counts", {})
        return cls(
            pack_name=d.get("pack_name", ""),
            display_name=d.get("display_name", ""),
            vendor=d.get("vendor", ""),
            series=d.get("series", ""),
            category=d.get("category", "capacitor"),
            kind=d.get("kind", "raw_vendor"),
            version=d.get("version", "1.0.0"),
            generated_at=d.get("generated_at", ""),
            footfindr_min_version=d.get("footfindr_min_version", "0.1.0"),
            source=PackSource(
                source_type=src.get("source_type", "unknown"),
                real_source=src.get("real_source", False),
                is_complete_catalog=src.get("is_complete_catalog", False),
                source_file=src.get("source_file"),
                source_sha256=src.get("source_sha256"),
                source_url=src.get("source_url"),
                downloaded_at=src.get("downloaded_at"),
                notes=src.get("notes"),
            ),
            counts=PackCounts(
                raw_rows=counts.get("raw_rows", 0),
                imported_parts=counts.get("imported_parts", 0),
                skipped_rows=counts.get("skipped_rows", 0),
            ),
            parser=PackParser(
                name=d.get("parser", {}).get("name", ""),
                slug=d.get("parser", {}).get("slug", ""),
                version=d.get("parser", {}).get("version", "1.0.0"),
            ),
            hashes=PackHashes(
                source_csv=d.get("hashes", {}).get("source_csv"),
                normalized_yaml=d.get("hashes", {}).get("normalized_yaml"),
                normalized_jsonl=d.get("hashes", {}).get("normalized_jsonl"),
            ),
            license=PackLicense(
                redistribution_status=lic.get("redistribution_status", "unknown"),
                notes=lic.get("notes"),
            ),
        )


# ---------------------------------------------------------------------------
# Build a pack
# ---------------------------------------------------------------------------

def build_pack(
    vendor_type: str,
    csv_path: str | Path,
    output_dir: str | Path,
    *,
    source_type: str = "manual_csv",
    real_source: bool = False,
    source_url: str | None = None,
    limit: int | None = None,
) -> tuple[PackMetadata, Path]:
    """Build a vendor library pack from a source CSV.

    Parameters
    ----------
    vendor_type : str
        Parser slug (e.g. ``"murata-grm"``, ``"generic"``).
        See ``vendor_parsers.list_parsers()`` for available parsers.
    csv_path : str | Path
        Path to the source CSV file.
    output_dir : str | Path
        Directory to create the pack in.
    source_type : str
        How the source was obtained: ``manual_csv``, ``fixture``, ``api``.
    real_source : bool
        True if this is a real vendor catalog (not a fixture).
    source_url : str | None
        URL where the source was downloaded from.
    limit : int | None
        Maximum number of parts to import (for testing).

    Returns
    -------
    tuple[PackMetadata, Path]
        The pack metadata and the pack directory path.
    """
    from footfindr.libraries.vendor_parsers import get_parser

    csv_path = Path(csv_path)
    output_dir = Path(output_dir)

    if not csv_path.exists():
        raise FileNotFoundError(f"Source CSV not found: {csv_path}")

    # Look up and invoke the parser
    parser = get_parser(vendor_type)
    pack_name = parser.pack_slug
    result = parser.parse(
        csv_path,
        limit=limit,
        source_file=csv_path.name,
        source_pack=pack_name,
    )
    records = result.records

    # Create pack directory structure
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "source").mkdir(exist_ok=True)
    (output_dir / "normalized").mkdir(exist_ok=True)
    (output_dir / "manifests").mkdir(exist_ok=True)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Compute source CSV hash
    source_sha256 = _sha256_file(csv_path)

    # Build metadata from parser + result
    meta = PackMetadata(
        pack_name=pack_name,
        display_name=parser.display_name,
        vendor=parser.vendor,
        series=parser.series,
        category=parser.category,
        kind="raw_vendor",
        version="1.0.0",
        generated_at=now,
        source=PackSource(
            source_type=source_type,
            real_source=real_source,
            is_complete_catalog=real_source and source_type != "fixture",
            source_file=csv_path.name,
            source_sha256=source_sha256,
            source_url=source_url,
            downloaded_at=now,
            notes=None,
        ),
        counts=PackCounts(
            raw_rows=result.raw_rows,
            imported_parts=result.imported_parts,
            skipped_rows=result.skipped_rows,
        ),
        parser=PackParser(
            name=type(parser).__name__,
            slug=vendor_type,
            version=result.parser_version,
        ),
        license=PackLicense(
            redistribution_status="unknown",
            notes="Verify vendor licensing before redistribution.",
        ),
    )

    # Copy source CSV
    shutil.copy2(str(csv_path), str(output_dir / "source" / csv_path.name))

    # Write normalized parts.yaml
    from footfindr.libraries.manager import LibraryManager
    parts_data = {"parts": [LibraryManager._record_to_dict(r) for r in records]}
    with open(output_dir / "normalized" / "parts.yaml", "w", encoding="utf-8") as f:
        yaml.dump(parts_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Write normalized parts.jsonl
    with open(output_dir / "normalized" / "parts.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(LibraryManager._record_to_dict(r), ensure_ascii=False) + "\n")

    # Compute hashes of normalized outputs
    meta.hashes = PackHashes(
        source_csv=source_sha256,
        normalized_yaml=_sha256_file(output_dir / "normalized" / "parts.yaml"),
        normalized_jsonl=_sha256_file(output_dir / "normalized" / "parts.jsonl"),
    )

    # Write footfindr_pack.yaml (with hashes)
    with open(output_dir / "footfindr_pack.yaml", "w", encoding="utf-8") as f:
        yaml.dump(meta.to_dict(), f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Write normalization report
    report = result.to_report_dict()
    report["generated_at"] = now
    report["source_file"] = csv_path.name
    report["source_type"] = source_type
    report["real_source"] = real_source
    report["source_sha256"] = source_sha256
    report["parser_name"] = type(parser).__name__
    report["parser_slug"] = vendor_type
    with open(output_dir / "manifests" / "normalization_report.yaml", "w", encoding="utf-8") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Generate README
    _generate_readme(output_dir, meta, result)

    # Generate LICENSE_NOTES
    _generate_license_notes(output_dir, meta)

    return meta, output_dir


# ---------------------------------------------------------------------------
# Validate a pack
# ---------------------------------------------------------------------------

def validate_pack(pack_dir: str | Path) -> list[str]:
    """Validate a vendor library pack directory.

    Returns a list of issues (empty = valid).
    """
    pack_dir = Path(pack_dir)
    issues: list[str] = []

    if not pack_dir.exists():
        return [f"Pack directory does not exist: {pack_dir}"]

    # Check footfindr_pack.yaml
    manifest = pack_dir / "footfindr_pack.yaml"
    if not manifest.exists():
        issues.append("Missing footfindr_pack.yaml")
        return issues

    try:
        with open(manifest, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        meta = PackMetadata.from_dict(data)
    except Exception as e:
        issues.append(f"Invalid footfindr_pack.yaml: {e}")
        return issues

    if not meta.pack_name:
        issues.append("pack_name is empty")
    if not meta.vendor:
        issues.append("vendor is empty")
    if not meta.display_name:
        issues.append("display_name is empty")

    # Check normalized parts
    parts_yaml = pack_dir / "normalized" / "parts.yaml"
    if not parts_yaml.exists():
        issues.append("Missing normalized/parts.yaml")
    else:
        try:
            with open(parts_yaml, "r", encoding="utf-8") as f:
                parts_data = yaml.safe_load(f) or {}
            parts_list = parts_data.get("parts", [])
            if not parts_list:
                issues.append("normalized/parts.yaml contains no parts")
            # Verify count matches metadata
            if meta.counts.imported_parts and len(parts_list) != meta.counts.imported_parts:
                issues.append(
                    f"Part count mismatch: metadata says {meta.counts.imported_parts}, "
                    f"parts.yaml has {len(parts_list)}"
                )
        except Exception as e:
            issues.append(f"Invalid normalized/parts.yaml: {e}")

    # Check source directory
    source_dir = pack_dir / "source"
    if not source_dir.exists():
        issues.append("Missing source/ directory")

    # Check normalization report
    report = pack_dir / "manifests" / "normalization_report.yaml"
    if not report.exists():
        issues.append("Missing manifests/normalization_report.yaml (non-critical)")

    # Check README
    readme = pack_dir / "README.md"
    if not readme.exists():
        issues.append("Missing README.md (non-critical)")

    return issues


# ---------------------------------------------------------------------------
# Install / uninstall a pack
# ---------------------------------------------------------------------------

def install_pack(
    pack_dir: str | Path,
    *,
    workspace: str | Path | None = None,
) -> PackMetadata:
    """Install a vendor library pack into the local workspace.

    Copies the pack to ``.footfindr/vendor_packs/<pack_name>/`` and registers
    it as a raw vendor library.

    Returns the pack metadata.
    """
    from footfindr.config import get_workspace as _gw
    from footfindr.libraries.manager import LibraryManager

    pack_dir = Path(pack_dir)
    ws = Path(workspace) if workspace else _gw()

    # Validate first
    issues = validate_pack(pack_dir)
    critical = [i for i in issues if "non-critical" not in i]
    if critical:
        raise ValueError(
            f"Pack validation failed:\n" + "\n".join(f"  - {i}" for i in critical)
        )

    # Load metadata
    with open(pack_dir / "footfindr_pack.yaml", "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    meta = PackMetadata.from_dict(data)

    # Copy pack to workspace
    dest = ws / "vendor_packs" / meta.pack_name
    if dest.exists():
        shutil.rmtree(str(dest))
    shutil.copytree(str(pack_dir), str(dest))

    # Load the normalized parts
    parts_yaml = dest / "normalized" / "parts.yaml"
    with open(parts_yaml, "r", encoding="utf-8") as f:
        parts_data = yaml.safe_load(f) or {}

    parts_list = parts_data.get("parts", [])
    from footfindr.libraries.models import ApprovedPartSchema
    records: list[PartRecord] = []
    for p in parts_list:
        schema = ApprovedPartSchema(**p)
        records.append(LibraryManager._schema_to_record(schema))

    # Register as a raw vendor library
    mgr = LibraryManager(workspace=ws)
    lib_name = meta.display_name.replace(" ", "-")
    mgr.save_raw_library(lib_name, records)

    # Store which pack this library came from
    pack_registry = ws / "vendor_packs" / "registry.yaml"
    registry: dict[str, Any] = {}
    if pack_registry.exists():
        with open(pack_registry, "r", encoding="utf-8") as f:
            registry = yaml.safe_load(f) or {}

    registry[lib_name] = {
        "pack_name": meta.pack_name,
        "pack_dir": str(dest),
        "vendor": meta.vendor,
        "series": meta.series,
        "category": meta.category,
        "version": meta.version,
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_type": meta.source.source_type,
        "real_source": meta.source.real_source,
        "is_complete_catalog": meta.source.is_complete_catalog,
        "part_count": meta.counts.imported_parts,
    }

    with open(pack_registry, "w", encoding="utf-8") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)

    return meta


def uninstall_pack(
    library_name: str,
    *,
    workspace: str | Path | None = None,
) -> bool:
    """Uninstall a vendor library pack.

    Removes the pack directory and its raw library registration.
    Returns True if successfully removed.
    """
    from footfindr.config import get_workspace as _gw
    from footfindr.libraries.manager import LibraryManager

    ws = Path(workspace) if workspace else _gw()

    # Find the pack in registry
    pack_registry = ws / "vendor_packs" / "registry.yaml"
    if not pack_registry.exists():
        raise FileNotFoundError(f"No vendor packs installed (no registry found)")

    with open(pack_registry, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f) or {}

    if library_name not in registry:
        raise KeyError(f"Vendor pack '{library_name}' not found in registry")

    entry = registry[library_name]
    pack_dir = Path(entry.get("pack_dir", ""))

    # Remove pack directory
    if pack_dir.exists():
        shutil.rmtree(str(pack_dir))

    # Remove raw library file
    mgr = LibraryManager(workspace=ws)
    safe_name = library_name.replace(" ", "_").lower()
    raw_path = ws / "raw" / f"{safe_name}.yaml"
    if raw_path.exists():
        raw_path.unlink()

    # Update registry
    del registry[library_name]
    with open(pack_registry, "w", encoding="utf-8") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)

    return True


# ---------------------------------------------------------------------------
# Query installed packs
# ---------------------------------------------------------------------------

def list_installed_packs(
    *,
    workspace: str | Path | None = None,
) -> list[dict[str, Any]]:
    """List all installed vendor packs."""
    from footfindr.config import get_workspace as _gw
    ws = Path(workspace) if workspace else _gw()

    pack_registry = ws / "vendor_packs" / "registry.yaml"
    if not pack_registry.exists():
        return []

    with open(pack_registry, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f) or {}

    result = []
    for lib_name, entry in registry.items():
        entry["library_name"] = lib_name
        result.append(entry)

    return result


def info_pack(
    pack_dir_or_name: str | Path,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Get detailed info about a pack (from directory or installed name).

    Returns a dict with metadata, counts, source, warnings.
    """
    from footfindr.config import get_workspace as _gw
    ws = Path(workspace) if workspace else _gw()

    pack_dir = Path(pack_dir_or_name)

    # If it looks like an installed pack name (not a path), look up in registry
    if not pack_dir.exists():
        pack_registry = ws / "vendor_packs" / "registry.yaml"
        if pack_registry.exists():
            with open(pack_registry, "r", encoding="utf-8") as f:
                registry = yaml.safe_load(f) or {}
            # Search by library name or pack name
            for lib_name, entry in registry.items():
                if lib_name == pack_dir_or_name or entry.get("pack_name") == str(pack_dir_or_name):
                    pack_dir = Path(entry["pack_dir"])
                    break

    if not pack_dir.exists():
        raise FileNotFoundError(f"Pack not found: {pack_dir_or_name}")

    manifest = pack_dir / "footfindr_pack.yaml"
    if not manifest.exists():
        raise FileNotFoundError(f"Not a valid pack directory (no footfindr_pack.yaml): {pack_dir}")

    with open(manifest, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    meta = PackMetadata.from_dict(data)

    info: dict[str, Any] = meta.to_dict()
    info["pack_dir"] = str(pack_dir)

    # Validation status
    issues = validate_pack(pack_dir)
    info["validation_issues"] = issues
    info["valid"] = len([i for i in issues if "non-critical" not in i]) == 0

    # Warnings
    warnings = []
    if not meta.source.real_source:
        warnings.append(
            "WARNING: This library was built from a fixture/sample file "
            "and is not a complete vendor catalog."
        )
    if meta.license.redistribution_status == "unknown":
        warnings.append(
            "WARNING: Redistribution status is unknown. "
            "Verify vendor licensing before sharing this pack."
        )
    info["warnings"] = warnings

    # Load normalization report if available
    report_path = pack_dir / "manifests" / "normalization_report.yaml"
    if report_path.exists():
        with open(report_path, "r", encoding="utf-8") as f:
            info["normalization_report"] = yaml.safe_load(f) or {}

    return info


# ---------------------------------------------------------------------------
# README and LICENSE generation
# ---------------------------------------------------------------------------

def _generate_readme(
    pack_dir: Path,
    meta: PackMetadata,
    stats: Any,
) -> None:
    """Generate a README.md for the pack."""
    is_fixture = meta.source.source_type == "fixture"
    fixture_warn = (
        "\n> ⚠️ **This pack was built from a fixture/sample file "
        "and is NOT a complete vendor catalog.**\n"
        if is_fixture else ""
    )

    content = f"""# {meta.display_name}

This pack contains normalized {meta.vendor} {meta.series} part data for FootFindr.
{fixture_warn}
## Status

- **Kind**: raw vendor library
- **Not POSM-approved** by default
- Parts require promotion before auto-resolve

## Source

| Field | Value |
|-------|-------|
| Source type | {meta.source.source_type} |
| Real source | {meta.source.real_source} |
| Complete catalog | {meta.source.is_complete_catalog} |
| Source file | {meta.source.source_file or 'N/A'} |
| Generated | {meta.generated_at} |
| Imported parts | {meta.counts.imported_parts} |
| Skipped rows | {meta.counts.skipped_rows} |

## Usage

```bash
# Install this pack
ff lib install .

# Search for parts
ff lib search cap 10u --raw --vendor {meta.vendor}

# Promote a part to POSM approved
ff lib promote <MPN> --to POSM --as CAP-10U-16V-X7R-0805
```

## License

Redistribution status: **{meta.license.redistribution_status}**

{meta.license.notes or 'Verify vendor licensing before redistribution.'}
"""

    (pack_dir / "README.md").write_text(content, encoding="utf-8")


def _generate_license_notes(pack_dir: Path, meta: PackMetadata) -> None:
    """Generate LICENSE_NOTES.md for the pack."""
    content = f"""# License Notes — {meta.display_name}

## Redistribution Status: {meta.license.redistribution_status}

This vendor library pack contains part data from **{meta.vendor}**.

### Important

- This data was obtained from the vendor's public product catalog.
- Redistribution rights have NOT been verified.
- Do NOT redistribute this pack publicly without verifying the vendor's
  data licensing terms.
- For private/internal use within your organization, this is generally
  acceptable under fair-use for engineering reference purposes.

### For Public Distribution

Before publishing this pack (e.g. on GitHub, npm, etc.):

1. Check the vendor's terms of use for their product catalog data.
2. Contact the vendor if terms are unclear.
3. Update `footfindr_pack.yaml` with the correct `redistribution_status`.
"""

    (pack_dir / "LICENSE_NOTES.md").write_text(content, encoding="utf-8")
