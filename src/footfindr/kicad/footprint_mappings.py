"""YAML-backed footprint mapping database (M9.3).

Persistent user-defined bindings between components and KiCad footprints.
Supports binding by ref, MPN, IPN, and category+package.

Storage: ``<project>/.footfindr/libraries/footprint_mappings.yaml``

Precedence for lookup:
  ref > MPN > IPN > category+package
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("footfindr.kicad.footprint_mappings")


@dataclass
class FootprintMapping:
    """A single footprint binding."""
    footprint: str
    scope: str = "project"       # "project", "posm", "global"
    confidence: str = "manual"   # "exact", "manual", "heuristic"
    reason: str = ""
    created_at: str = ""


class FootprintMappings:
    """YAML-backed footprint mapping database.

    Structure::

        mappings:
          ref:
            C1:
              footprint: "Capacitor_SMD:C_0603_1608Metric"
              scope: "project"
              confidence: "manual"
              reason: "User binding"
              created_at: "2025-01-01T00:00:00Z"
          mpn:
            GRT188C81E475KE13D:
              footprint: "Capacitor_SMD:C_0603_1608Metric"
              ...
          ipn:
            CAP-4U7-25V-X6S-0603:
              footprint: "Capacitor_SMD:C_0603_1608Metric"
              ...
          package:
            capacitor:
              "0603":
                footprint: "Capacitor_SMD:C_0603_1608Metric"
                ...
    """

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from footfindr.config import get_workspace
            ws = get_workspace()
            self._path = ws / "libraries" / "footprint_mappings.yaml"
        else:
            self._path = path

        self._data: dict[str, Any] = {"mappings": {
            "ref": {},
            "mpn": {},
            "ipn": {},
            "package": {},
        }}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def load(self) -> None:
        """Load mappings from disk."""
        self._loaded = True
        if not self._path.exists():
            return

        try:
            text = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if isinstance(data, dict) and "mappings" in data:
                self._data = data
            else:
                logger.warning(f"Invalid mappings file format: {self._path}")
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Failed to load mappings from {self._path}: {e}")

    def save(self) -> None:
        """Save mappings to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            yaml.dump(self._data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    # --- Bind methods ---

    def bind_ref(self, ref: str, footprint: str, *, scope: str = "project", reason: str = "") -> None:
        """Bind a specific ref to a footprint."""
        self._ensure_loaded()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._data["mappings"].setdefault("ref", {})[ref] = {
            "footprint": footprint,
            "scope": scope,
            "confidence": "manual",
            "reason": reason or f"Manual binding for {ref}",
            "created_at": now,
        }
        self.save()

    def bind_mpn(self, mpn: str, footprint: str, *, scope: str = "project", reason: str = "") -> None:
        """Bind an MPN to a footprint."""
        self._ensure_loaded()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._data["mappings"].setdefault("mpn", {})[mpn] = {
            "footprint": footprint,
            "scope": scope,
            "confidence": "manual",
            "reason": reason or f"Manual binding for MPN {mpn}",
            "created_at": now,
        }
        self.save()

    def bind_ipn(self, ipn: str, footprint: str, *, scope: str = "project", reason: str = "") -> None:
        """Bind an IPN to a footprint."""
        self._ensure_loaded()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._data["mappings"].setdefault("ipn", {})[ipn] = {
            "footprint": footprint,
            "scope": scope,
            "confidence": "manual",
            "reason": reason or f"Manual binding for IPN {ipn}",
            "created_at": now,
        }
        self.save()

    def bind_package(
        self, category: str, package: str, footprint: str,
        *, scope: str = "project", reason: str = "",
    ) -> None:
        """Bind a category+package combination to a footprint."""
        self._ensure_loaded()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pkg_map = self._data["mappings"].setdefault("package", {})
        cat_map = pkg_map.setdefault(category, {})
        cat_map[package] = {
            "footprint": footprint,
            "scope": scope,
            "confidence": "manual",
            "reason": reason or f"Manual binding for {category} {package}",
            "created_at": now,
        }
        self.save()

    # --- Lookup ---

    def lookup(
        self,
        ref: str | None = None,
        mpn: str | None = None,
        ipn: str | None = None,
        category: str | None = None,
        package: str | None = None,
    ) -> FootprintMapping | None:
        """Look up footprint mapping with precedence: ref > MPN > IPN > category+package."""
        self._ensure_loaded()
        mappings = self._data.get("mappings", {})

        # 1. Ref binding
        if ref:
            entry = mappings.get("ref", {}).get(ref)
            if entry:
                return self._entry_to_mapping(entry)

        # 2. MPN binding
        if mpn:
            entry = mappings.get("mpn", {}).get(mpn)
            if entry:
                return self._entry_to_mapping(entry)

        # 3. IPN binding
        if ipn:
            entry = mappings.get("ipn", {}).get(ipn)
            if entry:
                return self._entry_to_mapping(entry)

        # 4. Category+package binding
        if category and package:
            cat_map = mappings.get("package", {}).get(category, {})
            entry = cat_map.get(package)
            if entry:
                return self._entry_to_mapping(entry)

        return None

    def list_all(self) -> dict[str, list[dict[str, Any]]]:
        """List all mappings grouped by type."""
        self._ensure_loaded()
        mappings = self._data.get("mappings", {})
        result: dict[str, list[dict[str, Any]]] = {}

        for binding_type in ("ref", "mpn", "ipn"):
            items = mappings.get(binding_type, {})
            if items:
                result[binding_type] = [
                    {"key": k, **v} for k, v in items.items()
                ]

        # Package bindings (nested)
        pkg_map = mappings.get("package", {})
        if pkg_map:
            pkg_list = []
            for category, pkgs in pkg_map.items():
                for package, entry in pkgs.items():
                    pkg_list.append({
                        "key": f"{category}:{package}",
                        "category": category,
                        "package": package,
                        **entry,
                    })
            if pkg_list:
                result["package"] = pkg_list

        return result

    @staticmethod
    def _entry_to_mapping(entry: dict[str, Any]) -> FootprintMapping:
        """Convert a YAML dict entry to a FootprintMapping."""
        return FootprintMapping(
            footprint=entry.get("footprint", ""),
            scope=entry.get("scope", "project"),
            confidence=entry.get("confidence", "manual"),
            reason=entry.get("reason", ""),
            created_at=entry.get("created_at", ""),
        )
