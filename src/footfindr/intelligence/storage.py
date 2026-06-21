"""SuggestionRecord persistence layer (M9.4).

Stores and retrieves ``SuggestionRecord`` objects as JSON files under
the project-local workspace:

    ``.footfindr/intelligence/suggestions/<ref>.json``

Storage is tied to the active project because suggestions depend on
schematic context, rails, project policy, and board constraints.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from footfindr.intelligence.models import SuggestionRecord

logger = logging.getLogger("footfindr.intelligence.storage")


class SuggestionStore:
    """Persist and retrieve SuggestionRecords."""

    def __init__(self, workspace: Path | None = None) -> None:
        if workspace is None:
            from footfindr.config import get_workspace
            workspace = get_workspace()
        self._dir = workspace / "intelligence" / "suggestions"

    def _path_for(self, ref: str) -> Path:
        """Return the JSON file path for a given ref."""
        safe = ref.replace("/", "_").replace("\\", "_")
        return self._dir / f"{safe}.json"

    def save(self, record: SuggestionRecord) -> Path:
        """Save a SuggestionRecord to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(record.ref)
        data = record.to_dict()
        path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        logger.debug(f"Saved suggestion for {record.ref} -> {path}")
        return path

    def load(self, ref: str) -> SuggestionRecord | None:
        """Load a stored SuggestionRecord. Returns None if not found."""
        path = self._path_for(ref)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SuggestionRecord.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"Failed to load suggestion for {ref}: {e}")
            return None

    def exists(self, ref: str) -> bool:
        """Check if a suggestion exists for the given ref."""
        return self._path_for(ref).exists()

    def delete(self, ref: str) -> bool:
        """Delete a stored suggestion. Returns True if deleted."""
        path = self._path_for(ref)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_refs(self) -> list[str]:
        """List all refs with stored suggestions."""
        if not self._dir.exists():
            return []
        return [
            p.stem for p in self._dir.glob("*.json")
        ]


# ---------------------------------------------------------------------------
# Rail override persistence
# ---------------------------------------------------------------------------

class RailOverrideStore:
    """Persist user rail voltage overrides and aliases.

    Stored at ``.footfindr/intelligence/rails.yaml``.
    """

    def __init__(self, workspace: Path | None = None) -> None:
        if workspace is None:
            from footfindr.config import get_workspace
            workspace = get_workspace()
        self._path = workspace / "intelligence" / "rails.yaml"

    def load(self) -> dict[str, Any]:
        """Load rail overrides. Returns empty dict if none exist."""
        if not self._path.exists():
            return {"overrides": {}, "aliases": {}}
        try:
            import yaml
            data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"overrides": {}, "aliases": {}}
            data.setdefault("overrides", {})
            data.setdefault("aliases", {})
            return data
        except Exception as e:
            logger.warning(f"Failed to load rail overrides: {e}")
            return {"overrides": {}, "aliases": {}}

    def save(self, data: dict[str, Any]) -> None:
        """Save rail overrides to disk."""
        import yaml
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def set_voltage(self, net: str, voltage: float) -> None:
        """Set a user-defined voltage for a net."""
        data = self.load()
        data["overrides"][net] = {
            "voltage": voltage,
            "source": "user",
        }
        self.save(data)

    def set_alias(self, net: str, target: str) -> None:
        """Set a net alias (e.g. VDDA -> +3V3)."""
        data = self.load()
        data["aliases"][net] = target
        self.save(data)

    def get_overrides(self) -> dict[str, dict[str, Any]]:
        """Get all user-defined voltage overrides."""
        return self.load().get("overrides", {})

    def get_aliases(self) -> dict[str, str]:
        """Get all net aliases."""
        return self.load().get("aliases", {})
