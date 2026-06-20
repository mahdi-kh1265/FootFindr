"""SQLite-based part index for fast parametric search.

The index at ``.footfindr/index/parts.sqlite`` is a rebuildable acceleration
layer derived from installed vendor packs, approved libraries, and raw
library YAML files.  It is **not** the source of truth — the normalized
YAML/JSONL pack files and library metadata remain authoritative.

Use ``ff lib index rebuild`` to (re)build from source-of-truth files.
"""

from __future__ import annotations

import datetime
import math
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from footfindr import __version__
from footfindr.core.models import (
    ComponentCategory,
    ElectricalSpecs,
    PartRecord,
    PartStatus,
)
from footfindr.core.units import (
    parse_capacitance,
    parse_resistance,
    parse_voltage,
    parse_power,
)

_SCHEMA_VERSION = "1"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_name TEXT NOT NULL,
    source_pack TEXT,
    status TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    manufacturer TEXT,
    mpn TEXT,
    internal_pn TEXT NOT NULL,
    category TEXT NOT NULL,
    value TEXT,
    description TEXT,
    capacitance_f REAL,
    resistance_ohm REAL,
    voltage_v REAL,
    power_w REAL,
    dielectric TEXT,
    tolerance TEXT,
    package TEXT,
    footprint TEXT,
    source_vendor TEXT,
    source_series TEXT,
    source_file TEXT,
    source_row INTEGER
);

CREATE INDEX IF NOT EXISTS idx_parts_category ON parts(category);
CREATE INDEX IF NOT EXISTS idx_parts_mpn ON parts(mpn);
CREATE INDEX IF NOT EXISTS idx_parts_value ON parts(value);
CREATE INDEX IF NOT EXISTS idx_parts_cap ON parts(capacitance_f);
CREATE INDEX IF NOT EXISTS idx_parts_res ON parts(resistance_ohm);
CREATE INDEX IF NOT EXISTS idx_parts_pkg ON parts(package);
CREATE INDEX IF NOT EXISTS idx_parts_lib ON parts(library_name);
CREATE INDEX IF NOT EXISTS idx_parts_approved ON parts(approved);
CREATE INDEX IF NOT EXISTS idx_parts_dielectric ON parts(dielectric);
"""


@dataclass
class IndexInfo:
    """Summary statistics for the part index."""
    total_parts: int
    libraries: list[str]
    db_size_bytes: int
    last_rebuilt: str | None
    schema_version: str


class PartIndex:
    """SQLite-backed part index for fast parametric search."""

    def __init__(self, *, workspace: str | Path | None = None) -> None:
        from footfindr.config import get_workspace as _gw
        ws = Path(workspace) if workspace else _gw()
        self._index_dir = ws / "index"
        self._db_path = self._index_dir / "parts.sqlite"
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._index_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_CREATE_SQL)
            # Ensure metadata
            cur = self._conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            )
            row = cur.fetchone()
            if row is None:
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                self._conn.executemany(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                    [
                        ("schema_version", _SCHEMA_VERSION),
                        ("created_at", now),
                        ("last_rebuilt", now),
                        ("footfindr_version", __version__),
                    ],
                )
                self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ----- Query helpers -----

    def has_any_parts(self) -> bool:
        """Check if the index has any parts (fast check)."""
        if not self._db_path.exists():
            return False
        conn = self._connect()
        cur = conn.execute("SELECT 1 FROM parts LIMIT 1")
        return cur.fetchone() is not None

    def has_library(self, name: str) -> bool:
        """Check if a library is in the index."""
        conn = self._connect()
        cur = conn.execute(
            "SELECT 1 FROM parts WHERE library_name=? LIMIT 1", (name,)
        )
        return cur.fetchone() is not None

    # ----- Indexing -----

    def add_library(self, name: str, parts: list[PartRecord]) -> int:
        """Index parts from a library. Returns count of indexed parts."""
        conn = self._connect()
        # Remove existing entries for this library
        conn.execute("DELETE FROM parts WHERE library_name=?", (name,))

        rows = []
        for p in parts:
            cap_f = _safe_parse(parse_capacitance, p.specs.capacitance or p.value or "")
            res_ohm = _safe_parse(parse_resistance, p.specs.resistance or "")
            volt_v = _safe_parse(parse_voltage, p.specs.voltage_rating or "")
            pwr_w = _safe_parse(parse_power, p.specs.power_rating or "")

            rows.append((
                name,
                p.source_pack,
                p.status.value if hasattr(p.status, "value") else str(p.status),
                1 if p.approved else 0,
                p.manufacturer,
                p.mpn,
                p.internal_pn,
                p.category.value if hasattr(p.category, "value") else str(p.category),
                p.value,
                p.description,
                cap_f,
                res_ohm,
                volt_v,
                pwr_w,
                p.specs.dielectric,
                p.specs.tolerance,
                p.package,
                p.footprint,
                p.source_vendor,
                p.source_series,
                p.source_file,
                p.source_row,
            ))

        conn.executemany(
            """INSERT INTO parts (
                library_name, source_pack, status, approved,
                manufacturer, mpn, internal_pn, category,
                value, description,
                capacitance_f, resistance_ohm, voltage_v, power_w,
                dielectric, tolerance, package, footprint,
                source_vendor, source_series, source_file, source_row
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        return len(rows)

    def remove_library(self, name: str) -> int:
        """Remove all indexed parts for a library. Returns count removed."""
        conn = self._connect()
        cur = conn.execute("DELETE FROM parts WHERE library_name=?", (name,))
        conn.commit()
        return cur.rowcount

    def rebuild(self, manager: "LibraryManager") -> int:
        """Drop and rebuild the entire index from source-of-truth files."""
        conn = self._connect()
        conn.execute("DELETE FROM parts")
        conn.commit()

        total = 0
        libs = manager.list_libraries()
        for lib in libs:
            if lib.kind.value == "raw_vendor":
                parts = manager.load_raw_library(lib.name)
                total += self.add_library(lib.name, parts)
            elif lib.kind.value == "approved" and lib.parts_file:
                parts = manager._parse_approved_yaml(Path(lib.parts_file))
                total += self.add_library(lib.name, parts)

        # Also index from schemas/approved_parts.yaml
        from footfindr.libraries.manager import LibraryManager as LM
        approved = manager.load_approved_parts()
        if approved:
            total += self.add_library("__approved__", approved)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            [
                ("last_rebuilt", now),
                ("footfindr_version", __version__),
            ],
        )
        conn.commit()
        return total

    # ----- Search -----

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        approved_only: bool = False,
        raw_only: bool = False,
        vendor: str | None = None,
        package: str | None = None,
        voltage_min: str | None = None,
        dielectric: str | None = None,
    ) -> list[PartRecord]:
        """Parametric search using the SQLite index."""
        from footfindr.libraries.promotion import (
            _normalize_query_value,
            _dielectric_matches,
        )

        conn = self._connect()
        clauses: list[str] = []
        params: list = []

        # Category filter
        if category:
            cat_lower = category.lower()
            clauses.append("(category = ? OR category LIKE ?)")
            params.extend([cat_lower, f"{cat_lower}%"])

        # Approved / raw filter
        if approved_only:
            clauses.append("approved = 1")
        if raw_only:
            clauses.append("approved = 0")

        # Vendor filter
        if vendor:
            clauses.append("manufacturer LIKE ?")
            params.append(f"%{vendor}%")

        # Package filter
        if package:
            clauses.append("LOWER(package) = ?")
            params.append(package.lower())

        # Voltage min filter
        if voltage_min:
            v = _safe_parse(parse_voltage, voltage_min)
            if v is not None:
                clauses.append("(voltage_v IS NOT NULL AND voltage_v >= ?)")
                params.append(v)

        # Dielectric filter (case-insensitive)
        if dielectric:
            from footfindr.libraries.promotion import _DIELECTRIC_ALIASES
            d_lower = dielectric.lower()
            aliases = _DIELECTRIC_ALIASES.get(d_lower, set())
            all_matches = {d_lower} | aliases
            placeholders = ",".join("?" * len(all_matches))
            clauses.append(f"LOWER(dielectric) IN ({placeholders})")
            params.extend(list(all_matches))

        # Parametric value matching
        query_si, domain = _normalize_query_value(query, category_hint=category)

        if query_si is not None:
            # Use tight range for matching (rel_tol=1e-6 for floating point SQL)
            # Slightly wider than in-memory to account for float storage
            low = query_si * (1 - 1e-6)
            high = query_si * (1 + 1e-6)
            if query_si == 0:
                low, high = -1e-18, 1e-18

            if domain == "capacitance":
                clauses.append("(capacitance_f BETWEEN ? AND ?)")
                params.extend([low, high])
            elif domain == "resistance":
                clauses.append("(resistance_ohm BETWEEN ? AND ?)")
                params.extend([low, high])
        elif query.strip():
            # Text fallback: search across text fields
            tokens = query.lower().split()
            for tok in tokens:
                clauses.append(
                    "(LOWER(COALESCE(internal_pn,'') || ' ' || COALESCE(mpn,'') || ' ' || "
                    "COALESCE(manufacturer,'') || ' ' || COALESCE(value,'') || ' ' || "
                    "COALESCE(package,'') || ' ' || COALESCE(dielectric,'') || ' ' || "
                    "COALESCE(description,'')) LIKE ?)"
                )
                params.append(f"%{tok}%")

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM parts WHERE {where} LIMIT 500"

        cur = conn.execute(sql, params)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

        return [_row_to_part(dict(zip(columns, row))) for row in rows]

    # ----- Info -----

    def info(self) -> IndexInfo:
        """Return index summary info."""
        conn = self._connect()

        cur = conn.execute("SELECT COUNT(*) FROM parts")
        total = cur.fetchone()[0]

        cur = conn.execute("SELECT DISTINCT library_name FROM parts")
        libs = [r[0] for r in cur.fetchall()]

        db_size = self._db_path.stat().st_size if self._db_path.exists() else 0

        cur = conn.execute("SELECT value FROM metadata WHERE key='last_rebuilt'")
        row = cur.fetchone()
        last_rebuilt = row[0] if row else None

        cur = conn.execute("SELECT value FROM metadata WHERE key='schema_version'")
        row = cur.fetchone()
        schema_ver = row[0] if row else "unknown"

        return IndexInfo(
            total_parts=total,
            libraries=libs,
            db_size_bytes=db_size,
            last_rebuilt=last_rebuilt,
            schema_version=schema_ver,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_parse(parser, raw: str) -> float | None:
    """Safely parse a value, returning None on failure."""
    if not raw:
        return None
    try:
        return parser(raw)
    except (ValueError, TypeError):
        return None


def _row_to_part(row: dict) -> PartRecord:
    """Convert a SQLite row dict to a PartRecord."""
    cat_str = row.get("category", "other")
    try:
        category = ComponentCategory(cat_str)
    except ValueError:
        category = ComponentCategory.OTHER

    status_str = row.get("status", "raw")
    try:
        status = PartStatus(status_str)
    except ValueError:
        status = PartStatus.RAW

    return PartRecord(
        internal_pn=row.get("internal_pn", ""),
        category=category,
        manufacturer=row.get("manufacturer"),
        mpn=row.get("mpn"),
        description=row.get("description"),
        value=row.get("value"),
        status=status,
        approved=bool(row.get("approved", 0)),
        specs=ElectricalSpecs(
            capacitance=row.get("value") if cat_str == "capacitor" else None,
            resistance=row.get("value") if cat_str == "resistor" else None,
            voltage_rating=f"{row['voltage_v']}V" if row.get("voltage_v") else None,
            power_rating=f"{row['power_w']}W" if row.get("power_w") else None,
            tolerance=row.get("tolerance"),
            dielectric=row.get("dielectric"),
        ),
        package=row.get("package"),
        footprint=row.get("footprint"),
        source_library=row.get("library_name"),
        source_vendor=row.get("source_vendor"),
        source_series=row.get("source_series"),
        source_pack=row.get("source_pack"),
        source_file=row.get("source_file"),
        source_row=row.get("source_row"),
    )
