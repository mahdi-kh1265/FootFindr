"""KiCad project discovery for FootFindr.

Finds KiCad projects by scanning directories for ``.kicad_pro`` /
``.kicad_sch`` files.  Detects hierarchical subsheets by parsing
``(sheet … (property "Sheetfile" "sub.kicad_sch"))`` references.

All operations are read-only and bounded (max depth / max results).

M9.1b: hardened — always recurses into child directories even when the
parent is itself a KiCad project, so nested projects are never missed.
Adds fuzzy/case-insensitive name matching and a ``diagnose()`` helper.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("footfindr.kicad.discovery")

# Limits to keep discovery fast
_MAX_DEPTH = 4
_MAX_PROJECTS = 100

_SKIP_DIRS = frozenset({
    "__pycache__", "node_modules", "venv", ".venv",
    "build", "dist", ".git", ".svn", ".hg",
    "Backup", "backups", "_autosave",
})


@dataclass
class KiCadProject:
    """A discovered KiCad project."""
    name: str                        # derived from .kicad_pro stem
    project_dir: Path                # containing directory
    pro_file: Path | None = None     # .kicad_pro
    sch_file: Path | None = None     # .kicad_sch (root)
    pcb_file: Path | None = None     # .kicad_pcb
    sym_lib_table: Path | None = None
    fp_lib_table: Path | None = None
    subsheets: list[Path] = field(default_factory=list)
    project_type: str = "full"       # "full" or "schematic-only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project_dir": str(self.project_dir),
            "pro_file": str(self.pro_file) if self.pro_file else None,
            "sch_file": str(self.sch_file) if self.sch_file else None,
            "pcb_file": str(self.pcb_file) if self.pcb_file else None,
            "sym_lib_table": str(self.sym_lib_table) if self.sym_lib_table else None,
            "fp_lib_table": str(self.fp_lib_table) if self.fp_lib_table else None,
            "subsheets": [str(s) for s in self.subsheets],
            "project_type": self.project_type,
        }


# ---------------------------------------------------------------------------
# Core discovery
# ---------------------------------------------------------------------------

def discover_kicad_project(directory: Path) -> KiCadProject | None:
    """Look for a KiCad project in a specific directory.

    Returns a project if either .kicad_pro or .kicad_sch is found.
    Directories with .kicad_sch but no .kicad_pro are returned as
    ``project_type='schematic-only'``.
    """
    directory = directory.resolve()
    if not directory.is_dir():
        return None

    pro_files = list(directory.glob("*.kicad_pro"))
    sch_files = list(directory.glob("*.kicad_sch"))

    if not pro_files and not sch_files:
        return None

    # Determine name and project type
    if pro_files:
        pro = pro_files[0]
        name = pro.stem
        project_type = "full"
    else:
        pro = None
        # Pick a .kicad_sch whose stem matches the folder name if possible
        folder_name = directory.name
        matching_sch = [s for s in sch_files if s.stem == folder_name]
        best_sch = matching_sch[0] if matching_sch else sch_files[0]
        name = best_sch.stem
        project_type = "schematic-only"

    # Find matching schematic file
    sch = directory / f"{name}.kicad_sch"
    if not sch.exists():
        sch = sch_files[0] if sch_files else None

    pcb = directory / f"{name}.kicad_pcb"
    sym_table = directory / "sym-lib-table"
    fp_table = directory / "fp-lib-table"

    # Detect subsheets
    subsheets: list[Path] = []
    if sch and sch.exists():
        subsheets = detect_subsheets(sch)

    return KiCadProject(
        name=name,
        project_dir=directory,
        pro_file=pro if pro and pro.exists() else None,
        sch_file=sch if sch and sch.exists() else None,
        pcb_file=pcb if pcb.exists() else None,
        sym_lib_table=sym_table if sym_table.exists() else None,
        fp_lib_table=fp_table if fp_table.exists() else None,
        subsheets=subsheets,
        project_type=project_type,
    )


def discover_kicad_projects(
    roots: list[Path],
    *,
    max_depth: int = _MAX_DEPTH,
    max_projects: int = _MAX_PROJECTS,
    include_sch_only: bool = True,
    explain: bool = False,
) -> list[KiCadProject]:
    """Search multiple roots for KiCad projects (bounded, fast).

    Always recurses into child directories even when the parent is
    itself a project, so nested projects are never missed.

    Args:
        roots: Directories to search.
        max_depth: Maximum recursion depth per root.
        max_projects: Stop after this many projects.
        include_sch_only: Include dirs with .kicad_sch but no .kicad_pro.
        explain: If True, log which roots are scanned.
    """
    found: list[KiCadProject] = []
    seen_dirs: set[str] = set()
    log: list[str] = [] if explain else None

    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            if explain:
                log.append(f"SKIP root (not a dir): {root}")
            continue

        if explain:
            log.append(f"SCAN root: {root}")

        _scan_dir(root, 0, max_depth, max_projects, found, seen_dirs,
                  include_sch_only, log)
        if len(found) >= max_projects:
            break

    if explain and log:
        for line in log:
            logger.info(line)

    return found


def _scan_dir(
    directory: Path,
    depth: int,
    max_depth: int,
    max_projects: int,
    found: list[KiCadProject],
    seen: set[str],
    include_sch_only: bool,
    log: list[str] | None,
) -> None:
    """Recursively scan for KiCad projects.

    CRITICAL: Even if this directory IS a project, still recurse into
    children so nested projects (like test-board inside Max_Board_2)
    are found.
    """
    if depth > max_depth or len(found) >= max_projects:
        return

    dir_key = str(directory.resolve())
    if dir_key in seen:
        return
    seen.add(dir_key)

    # Check if this directory is a KiCad project
    proj = discover_kicad_project(directory)
    if proj:
        if include_sch_only or proj.project_type == "full":
            found.append(proj)
            if log is not None:
                log.append(f"  FOUND: {proj.name} ({proj.project_type}) at {directory}")

    # ALWAYS recurse into children (even if we found a project here)
    try:
        for child in sorted(directory.iterdir()):
            if len(found) >= max_projects:
                return
            if not child.is_dir():
                continue
            # Skip hidden, build, and virtual env directories
            if child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            _scan_dir(child, depth + 1, max_depth, max_projects, found,
                      seen, include_sch_only, log)
    except PermissionError:
        if log is not None:
            log.append(f"  PERM DENIED: {directory}")


def find_nearest_kicad_project(start: Path | None = None) -> KiCadProject | None:
    """Walk up from ``start`` (default: cwd) looking for a KiCad project.

    Checks the start directory and up to 5 parent levels.
    """
    current = (start or Path.cwd()).resolve()

    for _ in range(6):  # current + 5 parents
        proj = discover_kicad_project(current)
        if proj:
            return proj
        parent = current.parent
        if parent == current:
            break
        current = parent

    return None


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Normalize a project name for fuzzy matching.

    Strips hyphens, underscores, spaces; lowercases.
    """
    return re.sub(r'[-_\s]+', '', name.lower())


def match_project_name(query: str, projects: list[KiCadProject],
                       ) -> tuple[list[KiCadProject], list[KiCadProject]]:
    """Match a query against discovered projects.

    Returns (exact_matches, fuzzy_matches).
    Exact = case-insensitive name match.
    Fuzzy = normalized (no hyphens/underscores/spaces) match or substring.
    """
    exact: list[KiCadProject] = []
    fuzzy: list[KiCadProject] = []
    q_lower = query.lower()
    q_norm = _normalize_name(query)

    for p in projects:
        if p.name.lower() == q_lower:
            exact.append(p)
        elif _normalize_name(p.name) == q_norm:
            fuzzy.append(p)
        elif q_norm in _normalize_name(p.name):
            fuzzy.append(p)

    return exact, fuzzy


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

def diagnose_project(
    query: str,
    *,
    roots: list[Path] | None = None,
    workspace: Path | None = None,
    max_depth: int = 6,
) -> list[str]:
    """Diagnose why a project cannot be found.

    Returns a list of diagnostic lines (human-readable).
    """
    lines: list[str] = [f"Diagnosing KiCad project: {query}", ""]

    # 1. Check registered projects
    lines.append("1. Registered FootFindr projects:")
    try:
        from footfindr.project import ProjectManager
        pm = ProjectManager(workspace=workspace)
        all_projects = pm.list_all()
        if all_projects:
            match = [p for p in all_projects if p.name.lower() == query.lower()]
            if match:
                lines.append(f"   FOUND: {match[0].name} (registered)")
                lines.append(f"   schematic: {match[0].schematic}")
            else:
                lines.append(f"   checked {len(all_projects)} projects: no exact match")
                # Check fuzzy
                q_norm = _normalize_name(query)
                fuzzy_reg = [p for p in all_projects
                             if q_norm in _normalize_name(p.name)]
                if fuzzy_reg:
                    lines.append(f"   fuzzy matches: {', '.join(p.name for p in fuzzy_reg)}")
        else:
            lines.append("   no projects registered")
    except Exception as e:
        lines.append(f"   error: {e}")
    lines.append("")

    # 2. Check if query is a path
    p = Path(query)
    if p.exists():
        lines.append(f"2. Path exists: {p.resolve()}")
        if p.is_dir():
            proj = discover_kicad_project(p)
            if proj:
                lines.append(f"   FOUND project: {proj.name}")
                lines.append(f"   type: {proj.project_type}")
                if proj.sch_file:
                    lines.append(f"   schematic: {proj.sch_file}")
            else:
                lines.append("   no .kicad_pro or .kicad_sch in this directory")
        elif p.suffix in (".kicad_pro", ".kicad_sch"):
            proj = discover_kicad_project(p.parent)
            if proj:
                lines.append(f"   FOUND project: {proj.name}")
        lines.append("")
    elif query != ".":
        lines.append(f"2. Path '{query}': does not exist as a file/dir")
        lines.append("")

    # 3. Current directory + parents
    lines.append("3. Current directory / parents:")
    cwd = Path.cwd().resolve()
    nearest = find_nearest_kicad_project(cwd)
    lines.append(f"   cwd: {cwd}")
    if nearest:
        lines.append(f"   nearest project: {nearest.name} at {nearest.project_dir}")
    else:
        lines.append("   no KiCad project in current or parent directories")
    lines.append("")

    # 4. Configured roots
    search_roots = roots or get_default_search_roots()
    lines.append("4. Configured/default search roots:")
    for r in search_roots:
        lines.append(f"   - {r} (exists={r.is_dir()})")
    lines.append("")

    # 5. Full discovery
    lines.append(f"5. Full discovery (depth={max_depth}):")
    all_discovered = discover_kicad_projects(
        search_roots, max_depth=max_depth, max_projects=200)
    lines.append(f"   found {len(all_discovered)} total projects")

    exact, fuzzy = match_project_name(query, all_discovered)

    if exact:
        lines.append("")
        lines.append("   EXACT MATCH:")
        for m in exact:
            lines.append(f"     {m.name} ({m.project_type})")
            lines.append(f"       dir: {m.project_dir}")
            if m.pro_file:
                lines.append(f"       pro: {m.pro_file}")
            if m.sch_file:
                lines.append(f"       sch: {m.sch_file}")
    elif fuzzy:
        lines.append("")
        lines.append("   FUZZY MATCHES:")
        for m in fuzzy:
            lines.append(f"     {m.name} ({m.project_type})")
            lines.append(f"       dir: {m.project_dir}")
    else:
        lines.append(f"   no match for '{query}'")
        lines.append("")
        lines.append("   All discovered project names:")
        for d in all_discovered:
            lines.append(f"     {d.name}  ({d.project_dir})")

    lines.append("")

    # Result
    if exact:
        m = exact[0]
        lines.append("Result: FOUND")
        lines.append(f"  name: {m.name}")
        if m.pro_file:
            lines.append(f"  project: {m.pro_file}")
        if m.sch_file:
            lines.append(f"  schematic: {m.sch_file}")
        if m.pcb_file:
            lines.append(f"  board: {m.pcb_file}")
        lines.append("")
        lines.append("Suggested command:")
        if m.pro_file:
            lines.append(f'  ff project use "{m.pro_file}"')
        else:
            lines.append(f'  ff project use "{m.project_dir}"')
    elif fuzzy:
        lines.append("Result: POSSIBLE MATCHES (not exact)")
        for m in fuzzy:
            lines.append(f"  ff project use {m.name}")
    else:
        lines.append("Result: NOT FOUND")
        lines.append("")
        lines.append("Possible causes:")
        lines.append("  - folder is not under configured KiCad roots")
        lines.append("  - project has .kicad_sch but no .kicad_pro")
        lines.append("  - project name differs from folder name")
        lines.append("  - discovery depth too shallow")
        lines.append("  - OneDrive/Documents path not scanned")
        lines.append("")
        lines.append("Try:")
        lines.append(f'  ff config add kicad.root "<folder-containing-{query}>"')
        lines.append(f'  ff project discover --root "<path>" --depth 6')
        lines.append(f'  ff project use "<full-path-to-{query}.kicad_sch>"')

    return lines


# ---------------------------------------------------------------------------
# Subsheet detection
# ---------------------------------------------------------------------------

def detect_subsheets(sch_path: Path) -> list[Path]:
    """Parse a .kicad_sch for hierarchical sheet references.

    Looks for ``(sheet ... (property "Sheetfile" "subsheet.kicad_sch") ...)``
    patterns.  Returns absolute paths to referenced subsheets.
    """
    if not sch_path.exists():
        return []

    try:
        text = sch_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    pattern = re.compile(
        r'\(\s*property\s+"Sheetfile"\s+"([^"]+\.kicad_sch)"\s*',
        re.IGNORECASE,
    )

    parent_dir = sch_path.parent
    subsheets: list[Path] = []

    for match in pattern.finditer(text):
        sheet_name = match.group(1)
        sheet_path = parent_dir / sheet_name
        subsheets.append(sheet_path)

    return subsheets


# ---------------------------------------------------------------------------
# Default roots
# ---------------------------------------------------------------------------

def get_default_search_roots() -> list[Path]:
    """Get default search roots for KiCad project discovery.

    Includes current directory, common Windows locations,
    and configured roots.
    """
    roots: list[Path] = [Path.cwd()]

    home = Path.home()
    candidates = [
        home / "Documents" / "KiCad",
        home / "Documents",
        home / "OneDrive" / "Documents" / "KiCad",
        home / "OneDrive" / "Documents",
        home / "Downloads",
    ]

    for c in candidates:
        if c.is_dir():
            roots.append(c)

    # Configured roots (loaded from config)
    try:
        from footfindr.config import get_kicad_roots
        configured = get_kicad_roots()
        for r in configured:
            if r.is_dir() and r not in roots:
                roots.append(r)
    except Exception:
        pass

    return roots
