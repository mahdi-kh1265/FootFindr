"""SQLite-based supplier cache.

Stores supplier lookup results locally at
``.footfindr/supplier_cache/cache.sqlite``.  The cache is independent of
the part index and is never wiped by ``ff lib index rebuild``.

Uniqueness is enforced on ``(manufacturer, mpn, supplier)`` to avoid
collisions from inconsistent MPN formatting across suppliers.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from footfindr import __version__
from footfindr.suppliers.models import CacheEntry, PriceBreak, SupplierPart

logger = logging.getLogger("footfindr.suppliers.cache")

_SCHEMA_VERSION = "2"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS supplier_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer TEXT,
    mpn TEXT NOT NULL,
    supplier TEXT NOT NULL,
    supplier_pn TEXT,
    supplier_url TEXT,
    description TEXT,
    stock INTEGER,
    price_breaks TEXT,
    currency TEXT DEFAULT 'USD',
    minimum_order_quantity INTEGER,
    packaging TEXT,
    lead_time TEXT,
    datasheet_url TEXT,
    lifecycle TEXT,
    last_checked TEXT,
    source TEXT DEFAULT 'live',
    product_url TEXT,
    lcsc_pn TEXT,
    jlc_category TEXT,
    mounting_type TEXT,
    temperature_range TEXT,
    supplier_device_package TEXT,
    product_status TEXT,
    attributes_json TEXT,
    UNIQUE(manufacturer, mpn, supplier)
);

CREATE TABLE IF NOT EXISTS supplier_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    refreshed_at TEXT,
    result_count INTEGER,
    result_json TEXT,
    source TEXT DEFAULT 'live',
    UNIQUE(supplier, normalized_query)
);

CREATE INDEX IF NOT EXISTS idx_sp_mpn ON supplier_parts(mpn);
CREATE INDEX IF NOT EXISTS idx_sp_supplier ON supplier_parts(supplier);
CREATE INDEX IF NOT EXISTS idx_sp_mfr ON supplier_parts(manufacturer);
CREATE INDEX IF NOT EXISTS idx_sp_lcsc ON supplier_parts(lcsc_pn);
CREATE INDEX IF NOT EXISTS idx_ss_query ON supplier_searches(normalized_query);
"""

# Schema v1 -> v2 migration: add new columns
_MIGRATE_V1_TO_V2 = [
    "ALTER TABLE supplier_parts ADD COLUMN mounting_type TEXT",
    "ALTER TABLE supplier_parts ADD COLUMN temperature_range TEXT",
    "ALTER TABLE supplier_parts ADD COLUMN supplier_device_package TEXT",
    "ALTER TABLE supplier_parts ADD COLUMN product_status TEXT",
    "ALTER TABLE supplier_parts ADD COLUMN attributes_json TEXT",
]


@dataclass
class CacheInfo:
    """Summary statistics for the supplier cache."""
    total_entries: int
    suppliers: list[str]
    db_size_bytes: int
    last_updated: str | None
    schema_version: str


class SupplierCache:
    """SQLite-backed supplier cache."""

    def __init__(self, *, workspace: str | Path | None = None) -> None:
        from footfindr.config import get_workspace as _gw
        ws = Path(workspace) if workspace else _gw()
        self._cache_dir = ws / "supplier_cache"
        self._db_path = self._cache_dir / "cache.sqlite"
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_CREATE_SQL)
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
                        ("last_updated", now),
                        ("footfindr_version", __version__),
                    ],
                )
                self._conn.commit()
            elif row[0] == "1":
                self._migrate_v1_to_v2()
        return self._conn

    def _migrate_v1_to_v2(self) -> None:
        """Migrate schema v1 -> v2: add M8.5 columns."""
        conn = self._conn
        assert conn is not None
        for sql in _MIGRATE_V1_TO_V2:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # Column already exists
        # Create supplier_searches if missing
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS supplier_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier TEXT NOT NULL,
                query TEXT NOT NULL,
                normalized_query TEXT NOT NULL,
                refreshed_at TEXT,
                result_count INTEGER,
                result_json TEXT,
                source TEXT DEFAULT 'live',
                UNIQUE(supplier, normalized_query)
            );
            CREATE INDEX IF NOT EXISTS idx_ss_query ON supplier_searches(normalized_query);
        """)
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
            (_SCHEMA_VERSION,),
        )
        conn.commit()
        logger.info("Migrated supplier cache schema v1 -> v2")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _normalize_manufacturer(mfr: str | None) -> str | None:
        """Basic manufacturer normalization."""
        if not mfr:
            return None
        # Strip common suffixes
        s = mfr.strip()
        for suffix in (" Electronics", " Manufacturing", " Inc.", " Inc", " Co.", " Ltd.", " Ltd"):
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
        return s

    @staticmethod
    def _normalize_mpn(mpn: str) -> str:
        """Normalize MPN to uppercase, stripped."""
        return mpn.strip().upper()

    def store(self, part: SupplierPart) -> None:
        """Upsert a supplier part into the cache.

        Rejects empty/invalid parts (no MPN, no supplier_pn).
        """
        if not part.is_valid():
            logger.warning(
                f"[cache] Refusing to store invalid/empty supplier part "
                f"(supplier={part.supplier}, mpn={part.mpn!r})"
            )
            return

        conn = self._connect()
        mfr = self._normalize_manufacturer(part.manufacturer)
        mpn = self._normalize_mpn(part.mpn)

        price_json = None
        if part.price_breaks:
            price_json = json.dumps(
                [{"qty": pb.quantity, "price": pb.unit_price, "currency": pb.currency}
                 for pb in part.price_breaks]
            )

        attrs_json = None
        if part.attributes:
            attrs_json = json.dumps(part.attributes)

        conn.execute(
            """INSERT OR REPLACE INTO supplier_parts (
                manufacturer, mpn, supplier, supplier_pn, supplier_url,
                description, stock, price_breaks, currency, minimum_order_quantity,
                packaging, lead_time, datasheet_url, lifecycle,
                last_checked, source, product_url, lcsc_pn, jlc_category,
                mounting_type, temperature_range, supplier_device_package,
                product_status, attributes_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mfr, mpn, part.supplier, part.supplier_pn, part.supplier_url,
                part.description, part.stock, price_json, part.currency,
                part.minimum_order_quantity,
                part.packaging, part.lead_time, part.datasheet_url, part.lifecycle,
                part.last_checked, part.source,
                part.product_url, part.lcsc_pn, part.jlc_category,
                part.mounting_type, part.temperature_range,
                part.supplier_device_package, part.product_status,
                attrs_json,
            ),
        )
        # Update last_updated
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('last_updated', ?)",
            (now,),
        )
        conn.commit()

    def lookup(
        self,
        mpn: str,
        *,
        supplier: str | None = None,
        manufacturer: str | None = None,
    ) -> list[SupplierPart]:
        """Look up cached entries by MPN."""
        conn = self._connect()
        norm_mpn = self._normalize_mpn(mpn)

        clauses = ["mpn = ?"]
        params: list = [norm_mpn]

        if supplier:
            clauses.append("supplier = ?")
            params.append(supplier)
        if manufacturer:
            norm_mfr = self._normalize_manufacturer(manufacturer)
            clauses.append("manufacturer = ?")
            params.append(norm_mfr)

        where = " AND ".join(clauses)
        cur = conn.execute(
            f"SELECT * FROM supplier_parts WHERE {where}", params
        )
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()

        results = []
        for row in rows:
            d = dict(zip(columns, row))
            pbs = []
            if d.get("price_breaks"):
                try:
                    for pb_dict in json.loads(d["price_breaks"]):
                        pbs.append(PriceBreak(
                            quantity=pb_dict.get("qty", 1),
                            unit_price=pb_dict.get("price", 0.0),
                            currency=pb_dict.get("currency", "USD"),
                        ))
                except (json.JSONDecodeError, TypeError):
                    pass

            # Parse attributes JSON
            attrs = {}
            attrs_raw = d.get("attributes_json")
            if attrs_raw:
                try:
                    attrs = json.loads(attrs_raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            results.append(SupplierPart(
                supplier=d.get("supplier", ""),
                supplier_pn=d.get("supplier_pn"),
                supplier_url=d.get("supplier_url"),
                mpn=d.get("mpn", ""),
                manufacturer=d.get("manufacturer"),
                description=d.get("description"),
                stock=d.get("stock"),
                price_breaks=pbs,
                currency=d.get("currency", "USD"),
                minimum_order_quantity=d.get("minimum_order_quantity"),
                packaging=d.get("packaging"),
                lead_time=d.get("lead_time"),
                datasheet_url=d.get("datasheet_url"),
                lifecycle=d.get("lifecycle"),
                last_checked=d.get("last_checked"),
                source=d.get("source", "cache"),
                product_url=d.get("product_url"),
                lcsc_pn=d.get("lcsc_pn"),
                jlc_category=d.get("jlc_category"),
                mounting_type=d.get("mounting_type"),
                temperature_range=d.get("temperature_range"),
                supplier_device_package=d.get("supplier_device_package"),
                product_status=d.get("product_status"),
                attributes=attrs,
            ))

        return results

    def clear(
        self,
        *,
        supplier: str | None = None,
        mpn: str | None = None,
    ) -> int:
        """Clear cache entries.

        Filter by supplier and/or mpn. If neither, clears all.
        """
        conn = self._connect()
        clauses: list[str] = []
        params: list[str] = []

        if supplier:
            clauses.append("supplier = ?")
            params.append(supplier)
        if mpn:
            clauses.append("mpn = ?")
            params.append(self._normalize_mpn(mpn))

        if clauses:
            where = " AND ".join(clauses)
            cur = conn.execute(
                f"DELETE FROM supplier_parts WHERE {where}", params
            )
        else:
            cur = conn.execute("DELETE FROM supplier_parts")
        conn.commit()
        return cur.rowcount

    def info(self) -> CacheInfo:
        """Return cache summary."""
        conn = self._connect()

        cur = conn.execute("SELECT COUNT(*) FROM supplier_parts")
        total = cur.fetchone()[0]

        cur = conn.execute("SELECT DISTINCT supplier FROM supplier_parts")
        suppliers = [r[0] for r in cur.fetchall()]

        db_size = self._db_path.stat().st_size if self._db_path.exists() else 0

        cur = conn.execute("SELECT value FROM metadata WHERE key='last_updated'")
        row = cur.fetchone()
        last_updated = row[0] if row else None

        cur = conn.execute("SELECT value FROM metadata WHERE key='schema_version'")
        row = cur.fetchone()
        schema_ver = row[0] if row else "unknown"

        return CacheInfo(
            total_entries=total,
            suppliers=suppliers,
            db_size_bytes=db_size,
            last_updated=last_updated,
            schema_version=schema_ver,
        )

    # ------------------------------------------------------------------
    # Search result cache (separate from exact part lookup cache)
    # ------------------------------------------------------------------

    def store_search(
        self,
        supplier: str,
        query: str,
        results: list[SupplierPart],
    ) -> None:
        """Cache a search result set. Does NOT pollute the exact part cache."""
        from footfindr.suppliers.session import _part_to_dict
        conn = self._connect()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result_json = json.dumps([_part_to_dict(r) for r in results])
        conn.execute(
            """INSERT OR REPLACE INTO supplier_searches
               (supplier, query, normalized_query, refreshed_at, result_count, result_json, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (supplier, query, query.strip().upper(), now, len(results), result_json, "live"),
        )
        conn.commit()

    def lookup_search(
        self,
        supplier: str,
        query: str,
    ) -> list[SupplierPart] | None:
        """Look up cached search results. Returns None if not found."""
        from footfindr.suppliers.session import _dict_to_part
        conn = self._connect()
        cur = conn.execute(
            "SELECT result_json, refreshed_at FROM supplier_searches WHERE supplier=? AND normalized_query=?",
            (supplier, query.strip().upper()),
        )
        row = cur.fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[0])
            return [_dict_to_part(d) for d in data]
        except (json.JSONDecodeError, TypeError):
            return None

    def clear_searches(self, *, supplier: str | None = None) -> int:
        """Clear search cache entries."""
        conn = self._connect()
        if supplier:
            cur = conn.execute("DELETE FROM supplier_searches WHERE supplier=?", (supplier,))
        else:
            cur = conn.execute("DELETE FROM supplier_searches")
        conn.commit()
        return cur.rowcount
