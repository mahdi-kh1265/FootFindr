"""BOM profile loading, listing, and management."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import yaml

from footfindr.bom.models import BOMColumn, BOMProfile
from footfindr.config import get_workspace


# ---------------------------------------------------------------------------
# Profile search paths
# ---------------------------------------------------------------------------

_SHIPPED_PROFILES_DIR = Path(__file__).parent.parent.parent.parent / "schemas" / "bom_profiles"


def _user_profiles_dir(workspace: Optional[Path] = None) -> Path:
    ws = workspace or get_workspace()
    return ws / "bom_profiles"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_profile(name: str, *, workspace: Optional[Path] = None) -> BOMProfile:
    """Load a BOM profile by name.

    Search order:
    1. .footfindr/bom_profiles/<name>.yaml  (user overrides)
    2. schemas/bom_profiles/<name>.yaml     (shipped defaults)
    """
    # User overrides first
    user_dir = _user_profiles_dir(workspace)
    user_path = user_dir / f"{name}.yaml"
    if user_path.exists():
        return _parse_profile(user_path, name)

    # Shipped defaults
    shipped_path = _SHIPPED_PROFILES_DIR / f"{name}.yaml"
    if shipped_path.exists():
        return _parse_profile(shipped_path, name)

    raise FileNotFoundError(
        f"BOM profile '{name}' not found.\n"
        f"Searched: {user_path}, {shipped_path}\n"
        f"Available profiles: {', '.join(p.name for p in list_profiles(workspace=workspace))}"
    )


def _parse_profile(path: Path, name: str) -> BOMProfile:
    """Parse a YAML profile file."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    columns = []
    for col in raw.get("columns", []):
        if isinstance(col, dict):
            columns.append(BOMColumn(
                name=col.get("name", ""),
                source=col.get("source", ""),
                default=col.get("default", ""),
            ))
        elif isinstance(col, str):
            columns.append(BOMColumn(name=col, source=col.lower().replace(" ", "_")))

    return BOMProfile(
        name=name,
        description=raw.get("description", ""),
        columns=columns,
        group_by=raw.get("group_by", "internal_pn"),
        exclude_dnp=raw.get("exclude_dnp", True),
        warn_missing=raw.get("warn_missing", ["Footprint", "InternalPN"]),
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_profiles(*, workspace: Optional[Path] = None) -> list[BOMProfile]:
    """List all available BOM profiles."""
    seen: set[str] = set()
    profiles: list[BOMProfile] = []

    # User profiles first (overrides)
    user_dir = _user_profiles_dir(workspace)
    if user_dir.exists():
        for f in sorted(user_dir.glob("*.yaml")):
            name = f.stem
            if name not in seen:
                seen.add(name)
                profiles.append(_parse_profile(f, name))

    # Shipped defaults
    if _SHIPPED_PROFILES_DIR.exists():
        for f in sorted(_SHIPPED_PROFILES_DIR.glob("*.yaml")):
            name = f.stem
            if name not in seen:
                seen.add(name)
                profiles.append(_parse_profile(f, name))

    return profiles


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------

def create_profile(
    name: str,
    *,
    from_profile: Optional[str] = None,
    workspace: Optional[Path] = None,
) -> Path:
    """Create a new BOM profile, optionally copying from an existing one."""
    user_dir = _user_profiles_dir(workspace)
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"{name}.yaml"

    if dest.exists():
        raise FileExistsError(f"Profile '{name}' already exists at {dest}")

    if from_profile:
        source = load_profile(from_profile, workspace=workspace)
        # Find the source YAML file and copy it
        source_path = _SHIPPED_PROFILES_DIR / f"{from_profile}.yaml"
        if not source_path.exists():
            source_path = _user_profiles_dir(workspace) / f"{from_profile}.yaml"
        if source_path.exists():
            shutil.copy2(str(source_path), str(dest))
        else:
            # Generate from parsed profile
            _write_profile_yaml(dest, source)
    else:
        # Create minimal template
        template = BOMProfile(
            name=name,
            description=f"Custom BOM profile: {name}",
            columns=[
                BOMColumn(name="Quantity", source="quantity"),
                BOMColumn(name="References", source="references"),
                BOMColumn(name="Value", source="value"),
                BOMColumn(name="Footprint", source="footprint"),
            ],
        )
        _write_profile_yaml(dest, template)

    return dest


def validate_profile(name: str, *, workspace: Optional[Path] = None) -> list[str]:
    """Validate a BOM profile, returning a list of issues (empty = valid)."""
    issues: list[str] = []
    try:
        profile = load_profile(name, workspace=workspace)
    except FileNotFoundError as e:
        return [str(e)]

    if not profile.columns:
        issues.append("Profile has no columns defined")

    for col in profile.columns:
        if not col.name:
            issues.append("Column has empty name")
        if not col.source:
            issues.append(f"Column '{col.name}' has no source field")

    if profile.group_by not in ("internal_pn", "value_footprint", "value_footprint_mpn"):
        issues.append(f"Unknown group_by strategy: '{profile.group_by}'")

    return issues


def get_profile_path(name: str, *, workspace: Optional[Path] = None) -> Path | None:
    """Return the file path of a profile, or None if not found."""
    user_path = _user_profiles_dir(workspace) / f"{name}.yaml"
    if user_path.exists():
        return user_path
    shipped_path = _SHIPPED_PROFILES_DIR / f"{name}.yaml"
    if shipped_path.exists():
        return shipped_path
    return None


def _write_profile_yaml(path: Path, profile: BOMProfile) -> None:
    """Write a BOMProfile to a YAML file."""
    data = {
        "description": profile.description,
        "group_by": profile.group_by,
        "exclude_dnp": profile.exclude_dnp,
        "warn_missing": profile.warn_missing,
        "columns": [
            {"name": c.name, "source": c.source, "default": c.default}
            for c in profile.columns
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
