"""Murata GRM MLCC capacitor library — compatibility shim.

The actual parser logic now lives in ``footfindr.libraries.vendor_parsers.murata_grm``.
This module re-exports the key symbols so existing code that does
``from footfindr.libraries.murata import MurataGRMParser`` continues to work.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Re-export the parser and helpers from vendor_parsers
from footfindr.libraries.vendor_parsers.murata_grm import (  # noqa: F401
    MurataGRMParser,
    MURATA_SIZE_CODE_TO_EIA,
    PACKAGE_FOOTPRINT_MAP,
    FIXTURE_SIZE_TO_EIA,
    _normalize_voltage,
    _normalize_tolerance,
    _normalize_capacitance,
    _extract_package_from_mpn,
    _package_to_footprint,
)


# Keep NormalizationStats as a thin wrapper for backward compat
class NormalizationStats:
    """Backward-compatible stats wrapper.

    New code should use ``VendorParseResult`` directly.
    """

    def __init__(self) -> None:
        from collections import Counter
        self.raw_rows: int = 0
        self.imported_parts: int = 0
        self.skipped_rows: int = 0
        self.skip_reasons: list[str] = []
        self.product_status_counts: Counter = Counter()
        self.package_counts: Counter = Counter()
        self.voltage_counts: Counter = Counter()
        self.dielectric_counts: Counter = Counter()
        self.unmapped_size_codes: set[str] = set()
        self.example_mpns: list[str] = []

    def to_dict(self) -> dict:
        return {
            "raw_rows": self.raw_rows,
            "imported_parts": self.imported_parts,
            "skipped_rows": self.skipped_rows,
            "product_status_counts": dict(self.product_status_counts),
            "package_counts": dict(self.package_counts.most_common()),
            "voltage_counts": dict(self.voltage_counts.most_common()),
            "dielectric_counts": dict(self.dielectric_counts.most_common()),
            "unmapped_size_codes": sorted(self.unmapped_size_codes),
            "warnings": self.skip_reasons[:20],
            "examples": {
                "imported_mpns": self.example_mpns[:10],
                "skipped_rows": self.skip_reasons[:5],
            },
        }


# ---------------------------------------------------------------------------
# Legacy convenience functions
# ---------------------------------------------------------------------------

_DEFAULT_MURATA_URL = (
    "https://www.murata.com/api/pcsearch/search?1702702022"
)


def fetch_murata_grm(
    *,
    library_name: str = "Murata-GRM-Raw",
    limit: int | None = None,
    force_refresh: bool = False,
    cache_only: bool = False,
    url: str | None = None,
    workspace: str | Path | None = None,
) -> tuple[int, Path, list[str]]:
    """Fetch Murata GRM MLCC data (best-effort, secondary workflow).

    The primary workflow is offline vendor packs::

        ff lib pack build murata-grm <csv> --out <dir>
        ff lib install <dir>
    """
    from footfindr.config import get_workspace as _gw
    from footfindr.libraries.manager import LibraryManager

    ws = Path(workspace) if workspace else _gw()
    vendor_dir = ws / "vendor_raw" / "murata"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    cache_path = vendor_dir / "grm_mlcc.csv"

    warnings: list[str] = []

    if cache_only and cache_path.exists():
        return _ingest_cached(cache_path, library_name, limit, ws)

    if cache_only and not cache_path.exists():
        raise FileNotFoundError(
            "No cached Murata GRM data found.\n"
            "Recommended workflow:\n"
            "  1. Download the Murata GRM CSV from murata.com\n"
            "  2. Build a FootFindr pack:\n"
            "     ff lib pack build murata-grm <csv> --out ./footfindr-lib-murata-grm --real-source\n"
            "  3. Install it:\n"
            "     ff lib install ./footfindr-lib-murata-grm"
        )

    if cache_path.exists() and not force_refresh:
        count, path = _ingest_cached(cache_path, library_name, limit, ws)[:2]
        warnings.append("Using cached data. Use --force-refresh to re-download.")
        return count, path, warnings

    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError

    target_url = url or _DEFAULT_MURATA_URL
    try:
        req = Request(target_url, headers={"User-Agent": "FootFindr/0.1"})
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
        cache_path.write_bytes(data)
        warnings.append(f"Downloaded {len(data)} bytes from Murata")
    except (URLError, HTTPError, OSError, TimeoutError) as exc:
        error_msg = str(exc)
        manual_instructions = (
            f"Live Murata fetch failed: {error_msg}\n\n"
            "Recommended workflow:\n"
            "  1. Download/export the official Murata GRM CSV manually.\n"
            "  2. Build a FootFindr pack:\n"
            "     ff lib pack build murata-grm ./murata_grm.csv --out ./footfindr-lib-murata-grm --real-source\n"
            "  3. Install it:\n"
            "     ff lib install ./footfindr-lib-murata-grm"
        )
        if cache_path.exists():
            warnings.append(f"Download failed ({error_msg}); using cached data.")
            count, path = _ingest_cached(cache_path, library_name, limit, ws)[:2]
            return count, path, warnings
        raise RuntimeError(manual_instructions) from exc

    return _ingest_cached(cache_path, library_name, limit, ws)


def _ingest_cached(
    cache_path: Path,
    library_name: str,
    limit: int | None,
    workspace: Path,
) -> tuple[int, Path, list[str]]:
    from footfindr.libraries.manager import LibraryManager

    parser = MurataGRMParser()
    result = parser.parse(cache_path, limit=limit)
    mgr = LibraryManager(workspace=workspace)
    path = mgr.save_raw_library(library_name, result.records)
    return len(result.records), path, []


def ingest_murata_grm_csv(
    csv_path: str | Path,
    library_name: str = "Murata-GRM-Raw",
    *,
    limit: int | None = None,
    workspace: str | Path | None = None,
) -> tuple[int, Path]:
    """Ingest a manually downloaded Murata GRM CSV file."""
    from footfindr.config import get_workspace as _gw
    from footfindr.libraries.manager import LibraryManager

    ws = Path(workspace) if workspace else _gw()
    vendor_dir = ws / "vendor_raw" / "murata"
    vendor_dir.mkdir(parents=True, exist_ok=True)

    src = Path(csv_path)
    if not src.exists():
        raise FileNotFoundError(f"CSV file not found: {src}")
    cache_dest = vendor_dir / "grm_mlcc.csv"
    shutil.copy2(str(src), str(cache_dest))

    parser = MurataGRMParser()
    result = parser.parse(src, limit=limit)
    mgr = LibraryManager(workspace=workspace)
    path = mgr.save_raw_library(library_name, result.records)
    return len(result.records), path
