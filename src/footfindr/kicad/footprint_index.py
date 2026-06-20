"""KiCad footprint discovery and indexing (M9.3).

Scans KiCad ``fp-lib-table`` files to discover footprint libraries, indexes
individual ``.kicad_mod`` files, and stores results in a SQLite database
for fast search/lookup.

Supports:
  - Auto-detection of global KiCad config paths (Windows, macOS, Linux)
  - Environment variables (KICAD8_FOOTPRINT_DIR, etc.)
  - Project-local fp-lib-table
  - User config override via ``ff config set kicad.global-fp-table``

Index storage: ``<project>/.footfindr/index/footprints.sqlite``
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import platform
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("footfindr.kicad.footprint_index")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FootprintRecord:
    """A single indexed KiCad footprint."""
    library_nickname: str       # e.g. "Capacitor_SMD"
    footprint_name: str         # e.g. "C_0603_1608Metric"
    kicad_id: str               # "Capacitor_SMD:C_0603_1608Metric"
    source_path: str            # Path to the .pretty directory
    scope: str                  # "builtin", "global", "project"
    package_tokens: list[str]   # ["0603", "1608"]
    pad_count: int | None = None
    body_dims: str | None = None  # "1.6x0.8mm"
    last_indexed: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "library_nickname": self.library_nickname,
            "footprint_name": self.footprint_name,
            "kicad_id": self.kicad_id,
            "source_path": self.source_path,
            "scope": self.scope,
            "package_tokens": self.package_tokens,
            "pad_count": self.pad_count,
            "body_dims": self.body_dims,
            "last_indexed": self.last_indexed,
        }


@dataclass
class ScanReport:
    """Result of a footprint scan operation."""
    project_fp_table: str       # "found" / "not found"
    global_fp_table: str        # "found <path>" / "not found"
    env_vars_found: list[str]
    libraries_indexed: list[str]
    total_footprints: int
    builtin_indexed: bool = False   # True if Capacitor_SMD + Resistor_SMD indexed
    resolved_footprint_dir: str = ""  # path used for ${KICADx_FOOTPRINT_DIR}
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# fp-lib-table parser
# ---------------------------------------------------------------------------

@dataclass
class FpLibEntry:
    """A single library entry from an fp-lib-table."""
    name: str
    type: str
    uri: str
    options: str = ""
    descr: str = ""


def parse_fp_lib_table(path: Path) -> list[FpLibEntry]:
    """Parse a KiCad fp-lib-table file (S-expression format).

    Format::

        (fp_lib_table
          (version 7)
          (lib (name "Capacitor_SMD")(type "KiCad")
               (uri "${KICAD8_FOOTPRINT_DIR}/Capacitor_SMD.pretty")
               (options "")(descr ""))
        )
    """
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning(f"Cannot read fp-lib-table {path}: {e}")
        return []

    entries: list[FpLibEntry] = []

    # Find all (lib ...) blocks
    lib_pattern = re.compile(
        r'\(lib\s+'
        r'\(name\s+"?([^")\s]+)"?\)'
        r'\s*\(type\s+"?([^")\s]+)"?\)'
        r'\s*\(uri\s+"?([^")\n]+?)"?\)'
        r'(?:\s*\(options\s+"?([^")\n]*?)"?\))?'
        r'(?:\s*\(descr\s+"?([^")\n]*?)"?\))?',
        re.DOTALL,
    )

    for m in lib_pattern.finditer(text):
        entries.append(FpLibEntry(
            name=m.group(1).strip(),
            type=m.group(2).strip(),
            uri=m.group(3).strip(),
            options=(m.group(4) or "").strip(),
            descr=(m.group(5) or "").strip(),
        ))

    logger.debug(f"Parsed {len(entries)} libraries from {path}")
    return entries


# ---------------------------------------------------------------------------
# KiCad config auto-detection
# ---------------------------------------------------------------------------

# Common KiCad install paths by platform
_KICAD_INSTALL_PATHS: dict[str, list[str]] = {
    "Windows": [
        r"C:\Program Files\KiCad\{ver}\share\kicad\footprints",
        r"C:\Program Files (x86)\KiCad\{ver}\share\kicad\footprints",
    ],
    "Darwin": [
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
        "/Applications/KiCad/{ver}/KiCad.app/Contents/SharedSupport/footprints",
    ],
    "Linux": [
        "/usr/share/kicad/footprints",
        "/usr/local/share/kicad/footprints",
        "/usr/share/kicad/{ver}/footprints",
    ],
}

_KICAD_VERSIONS = ["10.0", "9.0", "8.0", "7.0"]

# Well-known built-in libraries that MUST be indexed for passive assignment
_BUILTIN_LIBRARIES = {"Capacitor_SMD", "Resistor_SMD"}


def _get_kicad_env_vars() -> dict[str, str]:
    """Detect KICAD*_FOOTPRINT_DIR environment variables."""
    result: dict[str, str] = {}
    for key in (
        "KICAD10_FOOTPRINT_DIR",
        "KICAD9_FOOTPRINT_DIR",
        "KICAD8_FOOTPRINT_DIR",
        "KICAD7_FOOTPRINT_DIR",
        "KICAD_FOOTPRINT_DIR",
    ):
        val = os.environ.get(key)
        if val and Path(val).exists():
            result[key] = val
    return result


def discover_kicad_footprint_dirs() -> list[Path]:
    """Probe common KiCad install paths for footprint directories.

    Returns a list of existing directories that contain `Capacitor_SMD.pretty`,
    ordered newest version first.
    """
    system = platform.system()
    templates = _KICAD_INSTALL_PATHS.get(system, _KICAD_INSTALL_PATHS["Linux"])
    found: list[Path] = []

    for tmpl in templates:
        if "{ver}" in tmpl:
            for ver in _KICAD_VERSIONS:
                candidate = Path(tmpl.replace("{ver}", ver))
                if candidate.is_dir() and (candidate / "Capacitor_SMD.pretty").is_dir():
                    if candidate not in found:
                        found.append(candidate)
                        logger.debug(f"Found KiCad footprints at {candidate}")
        else:
            candidate = Path(tmpl)
            if candidate.is_dir() and (candidate / "Capacitor_SMD.pretty").is_dir():
                if candidate not in found:
                    found.append(candidate)
                    logger.debug(f"Found KiCad footprints at {candidate}")

    return found


def _build_env_overrides(
    env_vars: dict[str, str],
    config_footprint_dir: str | None = None,
) -> dict[str, str]:
    """Build a complete set of KiCad env var overrides.

    Priority:
      1. User config (``ff config set kicad.footprint-dir``).
      2. OS environment variables.
      3. Probed KiCad install paths.

    Maps discovered dirs to all known env var names so that
    `${KICAD9_FOOTPRINT_DIR}/Capacitor_SMD.pretty` resolves.
    """
    result = dict(env_vars)  # copy

    # If user configured a dir, use it for all generic vars
    if config_footprint_dir:
        p = Path(config_footprint_dir)
        if p.is_dir():
            for key in ("KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR",
                        "KICAD8_FOOTPRINT_DIR", "KICAD7_FOOTPRINT_DIR",
                        "KICAD_FOOTPRINT_DIR"):
                result.setdefault(key, str(p))
            return result

    # Check if we already have env vars that resolve
    all_vars_needed = [
        "KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR",
        "KICAD8_FOOTPRINT_DIR", "KICAD7_FOOTPRINT_DIR",
        "KICAD_FOOTPRINT_DIR",
    ]
    missing = [k for k in all_vars_needed if k not in result]

    if missing:
        # Probe install paths
        discovered = discover_kicad_footprint_dirs()
        if discovered:
            fp_dir = str(discovered[0])  # Use newest found
            for key in missing:
                result[key] = fp_dir
            logger.info(f"Auto-discovered KiCad footprints at {fp_dir}")

    return result


def _resolve_uri(uri: str, env_vars: dict[str, str] | None = None) -> Path | None:
    """Resolve a KiCad library URI with env var substitution.

    Example: ``${KICAD8_FOOTPRINT_DIR}/Capacitor_SMD.pretty``
    """
    resolved = uri
    # Substitute known env vars (including auto-discovered)
    env = env_vars or {}
    for key, val in env.items():
        resolved = resolved.replace(f"${{{key}}}", val)

    # Also try generic env var substitution from os.environ
    def _env_sub(m):
        var_name = m.group(1)
        return os.environ.get(var_name, m.group(0))

    resolved = re.sub(r'\$\{([A-Z_][A-Z0-9_]*)\}', _env_sub, resolved)

    # Check if any ${...} remain unresolved
    if '${' in resolved:
        return None

    p = Path(resolved)
    if p.exists():
        return p

    return None


def discover_global_fp_lib_tables() -> list[Path]:
    """Auto-detect global KiCad fp-lib-table locations.

    Checks platform-specific config directories for KiCad 7, 8, 9, 10.
    Returns found paths, ordered by KiCad version (newest first).
    """
    candidates: list[Path] = []
    system = platform.system()

    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            for ver in _KICAD_VERSIONS:
                candidates.append(Path(appdata) / "kicad" / ver / "fp-lib-table")
    elif system == "Darwin":
        home = Path.home()
        for ver in _KICAD_VERSIONS:
            candidates.append(home / "Library" / "Preferences" / "kicad" / ver / "fp-lib-table")
    else:  # Linux
        home = Path.home()
        for ver in _KICAD_VERSIONS:
            candidates.append(home / ".config" / "kicad" / ver / "fp-lib-table")

    return [p for p in candidates if p.exists()]


def discover_project_fp_lib_table(project_dir: Path) -> Path | None:
    """Find fp-lib-table in a KiCad project directory."""
    fp_table = project_dir / "fp-lib-table"
    return fp_table if fp_table.exists() else None


# ---------------------------------------------------------------------------
# Footprint file scanning
# ---------------------------------------------------------------------------

_PACKAGE_TOKEN_RE = re.compile(
    r'(\d{4})'  # imperial sizes like 0603, 0805
    r'|(\d{4}Metric)'  # 1608Metric
    r'|(DFN|QFN|SOIC|SOT|MSOP|TSSOP|SSOP|LQFP|TQFP|BGA|SOP|SOD|DPAK|D2PAK|SMB|SMA|SMC)'
    r'|(\d+x\d+(?:\.\d+)?mm)'  # body dims like 3x3mm
)


def _extract_package_tokens(footprint_name: str) -> list[str]:
    """Extract package-relevant tokens from a footprint name.

    Examples:
        "C_0603_1608Metric" -> ["0603", "1608Metric"]
        "QFN-32-1EP_5x5mm_P0.5mm" -> ["QFN", "32", "5x5mm"]
    """
    tokens: list[str] = []
    for m in _PACKAGE_TOKEN_RE.finditer(footprint_name):
        token = m.group(0)
        if token:
            tokens.append(token)

    # Also extract pin count from patterns like -32- or _32_
    pin_match = re.search(r'[-_](\d{1,3})[-_]', footprint_name)
    if pin_match:
        count = pin_match.group(1)
        if count not in tokens:
            tokens.append(count)

    return tokens


def _count_pads(kicad_mod_path: Path) -> int | None:
    """Count pads in a .kicad_mod file by counting (pad ...) entries."""
    try:
        text = kicad_mod_path.read_text(encoding="utf-8", errors="replace")
        # Count (pad ...) entries
        return len(re.findall(r'\(pad\s+', text))
    except OSError:
        return None


def _extract_body_dims(footprint_name: str) -> str | None:
    """Extract body dimensions from footprint name."""
    m = re.search(r'(\d+(?:\.\d+)?x\d+(?:\.\d+)?mm)', footprint_name)
    return m.group(1) if m else None


def scan_pretty_directory(
    pretty_dir: Path,
    library_nickname: str,
    scope: str,
    *,
    count_pads: bool = False,
) -> list[FootprintRecord]:
    """Scan a .pretty directory for .kicad_mod footprint files."""
    records: list[FootprintRecord] = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if not pretty_dir.is_dir():
        return records

    for mod_file in sorted(pretty_dir.glob("*.kicad_mod")):
        fp_name = mod_file.stem
        kicad_id = f"{library_nickname}:{fp_name}"
        tokens = _extract_package_tokens(fp_name)
        body_dims = _extract_body_dims(fp_name)

        pad_count = None
        if count_pads:
            pad_count = _count_pads(mod_file)

        records.append(FootprintRecord(
            library_nickname=library_nickname,
            footprint_name=fp_name,
            kicad_id=kicad_id,
            source_path=str(pretty_dir),
            scope=scope,
            package_tokens=tokens,
            pad_count=pad_count,
            body_dims=body_dims,
            last_indexed=now,
        ))

    return records


# ---------------------------------------------------------------------------
# SQLite Footprint Index
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS footprints (
    kicad_id TEXT PRIMARY KEY,
    library_nickname TEXT NOT NULL,
    footprint_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    scope TEXT NOT NULL,
    package_tokens TEXT,
    pad_count INTEGER,
    body_dims TEXT,
    last_indexed TEXT
);

CREATE INDEX IF NOT EXISTS idx_library ON footprints(library_nickname);
CREATE INDEX IF NOT EXISTS idx_scope ON footprints(scope);
"""

# Full-text search on tokens would be ideal but simple LIKE queries
# are sufficient for M9.3 footprint counts.


class FootprintIndex:
    """SQLite-backed footprint index.

    Storage: ``<project>/.footfindr/index/footprints.sqlite``
    """

    def __init__(self, project_dir: Path | None = None) -> None:
        if project_dir is None:
            from footfindr.config import get_workspace
            ws = get_workspace()
            self._db_path = ws / "index" / "footprints.sqlite"
        else:
            ff_dir = project_dir / ".footfindr"
            self._db_path = ff_dir / "index" / "footprints.sqlite"

        self._conn: sqlite3.Connection | None = None

    def _ensure_db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def scan(
        self,
        fp_lib_tables: list[Path],
        *,
        project_dir: Path | None = None,
        config_overrides: dict[str, str] | None = None,
        reset: bool = False,
    ) -> ScanReport:
        """Full scan: parse fp-lib-tables, index all footprints.

        Returns a ScanReport with scan diagnostics.
        """
        conn = self._ensure_db()

        # Build resolved env vars with auto-discovery
        raw_env = _get_kicad_env_vars()
        config_fp_dir = None
        if config_overrides and "KICAD_FOOTPRINT_DIR" in config_overrides:
            config_fp_dir = config_overrides["KICAD_FOOTPRINT_DIR"]
        env_vars = _build_env_overrides(raw_env, config_fp_dir)
        if config_overrides:
            env_vars.update(config_overrides)

        # Track which dir we resolved for diagnostics
        resolved_fp_dir = ""
        for k in ("KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR",
                  "KICAD7_FOOTPRINT_DIR", "KICAD_FOOTPRINT_DIR"):
            if k in env_vars:
                resolved_fp_dir = env_vars[k]
                break

        # Detect sources
        project_fp_table_status = "not found"
        global_fp_table_status = "not found"
        env_found = [k for k in raw_env]  # Only actual OS env vars

        all_entries: list[tuple[FpLibEntry, str]] = []  # (entry, scope)
        errors: list[str] = []

        for fp_table_path in fp_lib_tables:
            entries = parse_fp_lib_table(fp_table_path)
            if not entries:
                errors.append(f"No libraries found in {fp_table_path}")
                continue

            # Determine scope
            if project_dir and fp_table_path.parent == project_dir:
                scope = "project"
                project_fp_table_status = f"found ({fp_table_path})"
            else:
                scope = "global"
                if global_fp_table_status == "not found":
                    global_fp_table_status = f"found ({fp_table_path})"

            for entry in entries:
                all_entries.append((entry, scope))

        # Clear old data and re-index
        if reset:
            conn.execute("DROP TABLE IF EXISTS footprints")
            conn.executescript(_SCHEMA)
        else:
            conn.execute("DELETE FROM footprints")

        libraries_indexed: list[str] = []
        total = 0

        for entry, scope in all_entries:
            resolved = _resolve_uri(entry.uri, env_vars)
            if resolved is None:
                errors.append(f"Cannot resolve: {entry.uri} (library: {entry.name})")
                continue

            records = scan_pretty_directory(resolved, entry.name, scope)
            if records:
                libraries_indexed.append(entry.name)
                total += len(records)
                self._insert_records(conn, records)

        conn.commit()

        builtin_ok = _BUILTIN_LIBRARIES.issubset(set(libraries_indexed))

        return ScanReport(
            project_fp_table=project_fp_table_status,
            global_fp_table=global_fp_table_status,
            env_vars_found=env_found,
            libraries_indexed=libraries_indexed,
            total_footprints=total,
            builtin_indexed=builtin_ok,
            resolved_footprint_dir=resolved_fp_dir,
            errors=errors,
        )

    def _insert_records(self, conn: sqlite3.Connection, records: list[FootprintRecord]) -> None:
        """Batch insert footprint records."""
        conn.executemany(
            """INSERT OR REPLACE INTO footprints
               (kicad_id, library_nickname, footprint_name, source_path,
                scope, package_tokens, pad_count, body_dims, last_indexed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r.kicad_id, r.library_nickname, r.footprint_name,
                    r.source_path, r.scope,
                    json.dumps(r.package_tokens), r.pad_count,
                    r.body_dims, r.last_indexed,
                )
                for r in records
            ],
        )

    def search(self, query: str) -> list[FootprintRecord]:
        """Search footprints by package token, name, or library.

        Supports queries like: "0603", "C_0603", "QFN-32", "Capacitor_SMD".
        """
        conn = self._ensure_db()
        # Search in kicad_id, footprint_name, library_nickname, and package_tokens
        like_query = f"%{query}%"
        rows = conn.execute(
            """SELECT kicad_id, library_nickname, footprint_name, source_path,
                      scope, package_tokens, pad_count, body_dims, last_indexed
               FROM footprints
               WHERE kicad_id LIKE ? OR footprint_name LIKE ?
                  OR library_nickname LIKE ? OR package_tokens LIKE ?
               ORDER BY library_nickname, footprint_name
               LIMIT 100""",
            (like_query, like_query, like_query, like_query),
        ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def get(self, kicad_id: str) -> FootprintRecord | None:
        """Get a specific footprint by its full KiCad ID."""
        conn = self._ensure_db()
        row = conn.execute(
            """SELECT kicad_id, library_nickname, footprint_name, source_path,
                      scope, package_tokens, pad_count, body_dims, last_indexed
               FROM footprints WHERE kicad_id = ?""",
            (kicad_id,),
        ).fetchone()

        return self._row_to_record(row) if row else None

    def list_all(self, scope: str | None = None) -> list[FootprintRecord]:
        """List all indexed footprints, optionally filtered by scope."""
        conn = self._ensure_db()
        if scope:
            rows = conn.execute(
                """SELECT kicad_id, library_nickname, footprint_name, source_path,
                          scope, package_tokens, pad_count, body_dims, last_indexed
                   FROM footprints WHERE scope = ?
                   ORDER BY library_nickname, footprint_name""",
                (scope,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT kicad_id, library_nickname, footprint_name, source_path,
                          scope, package_tokens, pad_count, body_dims, last_indexed
                   FROM footprints
                   ORDER BY library_nickname, footprint_name""",
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def count(self) -> int:
        """Total number of indexed footprints."""
        conn = self._ensure_db()
        row = conn.execute("SELECT COUNT(*) FROM footprints").fetchone()
        return row[0] if row else 0

    def list_libraries(self) -> list[str]:
        """List all indexed library nicknames."""
        conn = self._ensure_db()
        rows = conn.execute(
            "SELECT DISTINCT library_nickname FROM footprints ORDER BY library_nickname"
        ).fetchall()
        return [r[0] for r in rows]

    def has_builtin_libraries(self) -> bool:
        """Check if the essential built-in libraries are indexed.

        Returns True if both Capacitor_SMD and Resistor_SMD are present.
        """
        libs = set(self.list_libraries())
        return _BUILTIN_LIBRARIES.issubset(libs)

    @staticmethod
    def _row_to_record(row: tuple) -> FootprintRecord:
        """Convert a DB row to a FootprintRecord."""
        tokens = []
        if row[5]:
            try:
                tokens = json.loads(row[5])
            except (json.JSONDecodeError, TypeError):
                tokens = []

        return FootprintRecord(
            kicad_id=row[0],
            library_nickname=row[1],
            footprint_name=row[2],
            source_path=row[3],
            scope=row[4],
            package_tokens=tokens,
            pad_count=row[6],
            body_dims=row[7],
            last_indexed=row[8] or "",
        )


# ---------------------------------------------------------------------------
# High-level scan helper
# ---------------------------------------------------------------------------

def run_footprint_scan(
    project_dir: Path | None = None,
    *,
    config_overrides: dict[str, str] | None = None,
    reset: bool = False,
) -> tuple[FootprintIndex, ScanReport]:
    """Run a full footprint scan, combining project + global fp-lib-tables.

    Automatically discovers KiCad install paths when env vars are not set.
    Returns the index and a scan report.
    """
    fp_tables: list[Path] = []

    # 1. User config overrides
    overrides = dict(config_overrides or {})
    try:
        from footfindr.config import load_user_config
        cfg = load_user_config()
        user_fp_table = cfg.get("kicad", {}).get("global-fp-table")
        if user_fp_table:
            p = Path(user_fp_table)
            if p.exists():
                fp_tables.append(p)
            else:
                logger.warning(f"Configured fp-lib-table not found: {p}")

        user_fp_dir = cfg.get("kicad", {}).get("footprint-dir")
        if user_fp_dir:
            overrides["KICAD_FOOTPRINT_DIR"] = user_fp_dir
    except Exception:
        pass

    # 2. Project-local fp-lib-table
    if project_dir:
        proj_table = discover_project_fp_lib_table(project_dir)
        if proj_table:
            fp_tables.append(proj_table)

    # 3. Global fp-lib-tables (auto-detect)
    global_tables = discover_global_fp_lib_tables()
    for gt in global_tables:
        if gt not in fp_tables:
            fp_tables.append(gt)
            break  # Use first (newest version) found

    index = FootprintIndex(project_dir=project_dir)
    report = index.scan(
        fp_tables,
        project_dir=project_dir,
        config_overrides=overrides,
        reset=reset,
    )

    return index, report


def run_footprint_diagnose() -> dict[str, Any]:
    """Diagnose KiCad footprint configuration.

    Returns a dict with diagnostic information.
    """
    diag: dict[str, Any] = {}

    # KiCad versions detected
    global_tables = discover_global_fp_lib_tables()
    diag["global_fp_lib_tables"] = [str(p) for p in global_tables]

    # Env vars
    env_vars = _get_kicad_env_vars()
    diag["env_vars"] = env_vars

    # Discovered install paths
    discovered = discover_kicad_footprint_dirs()
    diag["discovered_footprint_dirs"] = [str(p) for p in discovered]

    # Built-in library check
    for d in discovered:
        cap_exists = (d / "Capacitor_SMD.pretty").is_dir()
        res_exists = (d / "Resistor_SMD.pretty").is_dir()
        diag["capacitor_smd_exists"] = cap_exists
        diag["resistor_smd_exists"] = res_exists
        diag["verified_footprint_dir"] = str(d)
        break
    else:
        diag["capacitor_smd_exists"] = False
        diag["resistor_smd_exists"] = False
        diag["verified_footprint_dir"] = None

    # User config
    try:
        from footfindr.config import load_user_config
        cfg = load_user_config()
        diag["config_footprint_dir"] = cfg.get("kicad", {}).get("footprint-dir")
        diag["config_fp_table"] = cfg.get("kicad", {}).get("global-fp-table")
    except Exception:
        diag["config_footprint_dir"] = None
        diag["config_fp_table"] = None

    # Suggested fix
    if discovered and not env_vars:
        diag["suggested_fix"] = f'ff config set kicad.footprint-dir "{discovered[0]}"'
    elif not discovered:
        diag["suggested_fix"] = (
            "Could not find KiCad footprint libraries. "
            "Set manually: ff config set kicad.footprint-dir <path>"
        )
    else:
        diag["suggested_fix"] = None

    return diag
