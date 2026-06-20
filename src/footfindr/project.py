"""Project context management for FootFindr.

Tracks project metadata (name, schematic, library, timestamps) so users
can run ``ff resolve all`` without specifying the schematic every time.

Storage layout:
  .footfindr/projects/<name>/project.yaml  -- per-project metadata
  .footfindr/state.yaml                    -- active project / active library

M9.1: Enhanced with KiCad discovery, smart ``use`` (accepts `.`, names,
paths), auto-registration, and nearest-project fallback.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from footfindr.config import get_workspace

logger = logging.getLogger("footfindr.project")


@dataclass
class ProjectMetadata:
    """Metadata for a FootFindr project."""
    name: str
    schematic: str
    active_library: str | None = None
    created: str | None = None
    last_resolve: str | None = None
    last_decision_log: str | None = None
    build_quantity: int | None = None
    status: str = "active"
    # M9.1 additions
    kicad_pro: str | None = None
    kicad_pcb: str | None = None
    project_dir: str | None = None


@dataclass
class GlobalState:
    """Global state persisted in .footfindr/state.yaml."""
    active_project: str | None = None
    active_library: str | None = None


class ProjectManager:
    """Manages FootFindr project lifecycle."""

    def __init__(self, workspace: Optional[str | Path] = None) -> None:
        self._workspace = Path(workspace) if workspace else get_workspace()
        self._projects_dir = self._workspace / "projects"
        self._state_path = self._workspace / "state.yaml"

    @property
    def workspace(self) -> Path:
        return self._workspace

    # ---- State ----

    def _load_state(self) -> GlobalState:
        if not self._state_path.exists():
            return GlobalState()
        with open(self._state_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return GlobalState(
            active_project=raw.get("active_project"),
            active_library=raw.get("active_library"),
        )

    def _save_state(self, state: GlobalState) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as fh:
            yaml.dump(
                {"active_project": state.active_project, "active_library": state.active_library},
                fh, default_flow_style=False, sort_keys=False,
            )

    # ---- Project CRUD ----

    def start(
        self,
        name: str,
        schematic: str,
        *,
        library: str | None = None,
        build_quantity: int | None = None,
    ) -> ProjectMetadata:
        """Create and register a new project."""
        proj_dir = self._projects_dir / name
        if proj_dir.exists():
            raise ValueError(f"Project '{name}' already exists")

        proj_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        meta = ProjectMetadata(
            name=name,
            schematic=schematic,
            active_library=library,
            created=now,
            build_quantity=build_quantity,
            status="active",
        )
        self._save_project(meta)
        return meta

    def status(self, name: str) -> ProjectMetadata:
        """Get the status of a project."""
        meta = self._load_project(name)
        if meta is None:
            raise ValueError(f"Project '{name}' not found")
        return meta

    def use(self, name: str) -> None:
        """Set the active project.

        Original behavior: requires the project to already be registered.
        """
        meta = self._load_project(name)
        if meta is None:
            raise ValueError(f"Project '{name}' not found")
        state = self._load_state()
        state.active_project = name
        self._save_state(state)

    def use_smart(self, name_or_path: str) -> tuple[ProjectMetadata, str]:
        """Smart project use: accept '.', name, directory, .kicad_pro, .kicad_sch.

        Resolution order:
          1. "." → nearest KiCad project from cwd
          2. Existing registered project name (case-insensitive)
          3. Filesystem path (dir, .kicad_pro, .kicad_sch)
          4. Discovered project by name (case/hyphen/underscore insensitive)
          5. Fuzzy substring match → show choices
          6. Not found → clear diagnostic

        Returns (metadata, message_for_user).
        Raises ValueError with helpful instructions if nothing resolves.
        """
        from footfindr.kicad.discovery import (
            discover_kicad_project,
            discover_kicad_projects,
            find_nearest_kicad_project,
            get_default_search_roots,
            match_project_name,
        )

        # 1. "." → nearest KiCad project from cwd
        if name_or_path == ".":
            proj = find_nearest_kicad_project()
            if proj:
                meta = self._auto_register(proj)
                state = self._load_state()
                state.active_project = meta.name
                self._save_state(state)
                return meta, self._format_use_success(proj)
            raise ValueError(
                "No KiCad project found in current or parent directories.\n\n"
                "Try:\n"
                "  ff project discover --all\n"
                "  ff project use <path-to-.kicad_pro>\n"
                "  ff config add kicad.root <folder-containing-KiCad-projects>"
            )

        # 2. Existing registered project name (case-insensitive)
        existing = self._load_project(name_or_path)
        if not existing:
            # Try case-insensitive match against registered projects
            for proj_meta in self.list_all():
                if proj_meta.name.lower() == name_or_path.lower():
                    existing = proj_meta
                    break
        if existing:
            state = self._load_state()
            state.active_project = existing.name
            self._save_state(state)
            return existing, self._format_use_success_meta(existing)

        # 3. Filesystem path
        p = Path(name_or_path)
        if p.exists():
            if p.is_dir():
                proj = discover_kicad_project(p)
                if proj:
                    meta = self._auto_register(proj)
                    state = self._load_state()
                    state.active_project = meta.name
                    self._save_state(state)
                    return meta, self._format_use_success(proj)
                raise ValueError(
                    f"Directory exists but no KiCad files found:\n"
                    f"  {p.resolve()}\n\n"
                    f"No .kicad_pro or .kicad_sch in this directory."
                )
            elif p.suffix in (".kicad_pro", ".kicad_sch"):
                proj = discover_kicad_project(p.parent)
                if proj:
                    meta = self._auto_register(proj)
                    state = self._load_state()
                    state.active_project = meta.name
                    self._save_state(state)
                    return meta, self._format_use_success(proj)
                raise ValueError(
                    f"File exists but could not parse KiCad project:\n"
                    f"  {p.resolve()}"
                )

        # 4. Discovered project by name (case/hyphen/underscore insensitive)
        roots = get_default_search_roots()
        discovered = discover_kicad_projects(roots)

        exact, fuzzy = match_project_name(name_or_path, discovered)

        if len(exact) == 1:
            meta = self._auto_register(exact[0])
            state = self._load_state()
            state.active_project = meta.name
            self._save_state(state)
            return meta, self._format_use_success(exact[0])

        if len(exact) > 1:
            lines = [f'Multiple exact matches for "{name_or_path}":\n']
            for i, d in enumerate(exact, 1):
                lines.append(f"  {i}  {d.name}  ({d.project_dir})")
            lines.append("\nRun:")
            for d in exact:
                lines.append(f'  ff project use "{d.project_dir}"')
            raise ValueError("\n".join(lines))

        # 5. Fuzzy match (normalized match or substring)
        if len(fuzzy) == 1:
            meta = self._auto_register(fuzzy[0])
            state = self._load_state()
            state.active_project = meta.name
            self._save_state(state)
            return meta, self._format_use_success(fuzzy[0])

        if len(fuzzy) > 1:
            lines = [f'Multiple KiCad projects match "{name_or_path}":\n']
            for i, d in enumerate(fuzzy[:10], 1):
                lines.append(f"  {i}  {d.name}  ({d.project_dir})")
            lines.append("\nRun:")
            for d in fuzzy[:5]:
                lines.append(f"  ff project use {d.name}")
            raise ValueError("\n".join(lines))

        # 6. Nothing found — clear diagnostic
        raise ValueError(
            f'No KiCad project named "{name_or_path}" found.\n\n'
            f"Searched {len(roots)} roots, found {len(discovered)} projects, "
            f"none matched.\n\n"
            "Try:\n"
            f"  ff project diagnose {name_or_path}\n"
            "  ff project discover --all\n"
            "  ff project use <full-path-to-project>\n"
            "  ff config add kicad.root <folder-containing-KiCad-projects>"
        )

    def _auto_register(self, kicad_proj) -> ProjectMetadata:
        """Auto-register a discovered KiCad project."""
        existing = self._load_project(kicad_proj.name)
        if existing:
            # Update paths if needed
            if kicad_proj.sch_file and not existing.schematic:
                existing.schematic = str(kicad_proj.sch_file)
            if kicad_proj.pro_file:
                existing.kicad_pro = str(kicad_proj.pro_file)
            if kicad_proj.pcb_file:
                existing.kicad_pcb = str(kicad_proj.pcb_file)
            existing.project_dir = str(kicad_proj.project_dir)
            self._save_project(existing)
            return existing

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        meta = ProjectMetadata(
            name=kicad_proj.name,
            schematic=str(kicad_proj.sch_file) if kicad_proj.sch_file else "",
            created=now,
            status="active",
            kicad_pro=str(kicad_proj.pro_file) if kicad_proj.pro_file else None,
            kicad_pcb=str(kicad_proj.pcb_file) if kicad_proj.pcb_file else None,
            project_dir=str(kicad_proj.project_dir),
        )
        self._save_project(meta)
        return meta

    @staticmethod
    def _format_registration_msg(kicad_proj) -> str:
        lines = [f"Auto-registered KiCad project '{kicad_proj.name}'"]
        if kicad_proj.pro_file:
            lines.append(f"  project:   {kicad_proj.pro_file}")
        if kicad_proj.sch_file:
            lines.append(f"  schematic: {kicad_proj.sch_file}")
        if kicad_proj.pcb_file:
            lines.append(f"  board:     {kicad_proj.pcb_file}")
        if kicad_proj.subsheets:
            lines.append(f"  subsheets: {len(kicad_proj.subsheets)} detected")
        return "\n".join(lines)

    @staticmethod
    def _format_use_success(kicad_proj) -> str:
        """Format a rich success message after project use."""
        lines = [f"Active project: {kicad_proj.name}"]
        lines.append("")
        lines.append("KiCad:")
        if kicad_proj.pro_file:
            lines.append(f"  project:   {kicad_proj.pro_file}")
        else:
            lines.append(f"  project:   (none — schematic-only)")
        if kicad_proj.sch_file:
            lines.append(f"  schematic: {kicad_proj.sch_file}")
        if kicad_proj.pcb_file:
            lines.append(f"  board:     {kicad_proj.pcb_file}")
        if kicad_proj.subsheets:
            lines.append(f"  subsheets: {len(kicad_proj.subsheets)} detected")
        ptype = getattr(kicad_proj, "project_type", "full")
        if ptype == "schematic-only":
            lines.append(f"  type:      schematic-only")
        lines.append("")
        lines.append("Next:")
        lines.append("  ff project status")
        lines.append("  ff proj review")
        return "\n".join(lines)

    @staticmethod
    def _format_use_success_meta(meta) -> str:
        """Format success message from ProjectMetadata."""
        lines = [f"Active project: {meta.name}"]
        lines.append("")
        lines.append("KiCad:")
        if meta.kicad_pro:
            lines.append(f"  project:   {meta.kicad_pro}")
        if meta.schematic:
            lines.append(f"  schematic: {meta.schematic}")
        if meta.kicad_pcb:
            lines.append(f"  board:     {meta.kicad_pcb}")
        lines.append("")
        lines.append("Next:")
        lines.append("  ff project status")
        lines.append("  ff proj review")
        return "\n".join(lines)

    def discover(
        self,
        roots: list[Path] | None = None,
        *,
        max_depth: int | None = None,
        include_sch_only: bool = True,
        explain: bool = False,
    ) -> list:
        """Discover KiCad projects from configured roots."""
        from footfindr.kicad.discovery import (
            discover_kicad_projects,
            get_default_search_roots,
        )
        search_roots = roots or get_default_search_roots()
        kwargs = {"include_sch_only": include_sch_only, "explain": explain}
        if max_depth is not None:
            kwargs["max_depth"] = max_depth
        return discover_kicad_projects(search_roots, **kwargs)

    def clear(self) -> None:
        """Clear the active project."""
        state = self._load_state()
        state.active_project = None
        self._save_state(state)

    def list_all(self) -> list[ProjectMetadata]:
        """List all registered projects."""
        if not self._projects_dir.exists():
            return []
        result = []
        for d in sorted(self._projects_dir.iterdir()):
            if d.is_dir():
                meta = self._load_project(d.name)
                if meta:
                    result.append(meta)
        return result

    def end(self, name: str) -> None:
        """Mark a project as ended (archive, don't delete)."""
        meta = self._load_project(name)
        if meta is None:
            raise ValueError(f"Project '{name}' not found")
        meta.status = "ended"
        self._save_project(meta)

        # If this was the active project, clear it
        state = self._load_state()
        if state.active_project == name:
            state.active_project = None
            self._save_state(state)

    def get_active(self) -> ProjectMetadata | None:
        """Return the active project, or None."""
        state = self._load_state()
        if not state.active_project:
            return None
        return self._load_project(state.active_project)

    def get_active_name(self) -> str | None:
        """Return the active project name, or None."""
        state = self._load_state()
        return state.active_project

    def update_last_resolve(
        self,
        name: str,
        decision_log: str | None = None,
    ) -> None:
        """Update the last_resolve timestamp and decision log path."""
        meta = self._load_project(name)
        if meta is None:
            return
        meta.last_resolve = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if decision_log:
            meta.last_decision_log = decision_log
        self._save_project(meta)

    # ---- Persistence ----

    def _project_path(self, name: str) -> Path:
        return self._projects_dir / name / "project.yaml"

    def _load_project(self, name: str) -> ProjectMetadata | None:
        path = self._project_path(name)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return ProjectMetadata(
            name=raw.get("name", name),
            schematic=raw.get("schematic", ""),
            active_library=raw.get("active_library"),
            created=raw.get("created"),
            last_resolve=raw.get("last_resolve"),
            last_decision_log=raw.get("last_decision_log"),
            build_quantity=raw.get("build_quantity"),
            status=raw.get("status", "active"),
            kicad_pro=raw.get("kicad_pro"),
            kicad_pcb=raw.get("kicad_pcb"),
            project_dir=raw.get("project_dir"),
        )

    def _save_project(self, meta: ProjectMetadata) -> None:
        path = self._project_path(meta.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "name": meta.name,
            "schematic": meta.schematic,
            "active_library": meta.active_library,
            "created": meta.created,
            "last_resolve": meta.last_resolve,
            "last_decision_log": meta.last_decision_log,
            "build_quantity": meta.build_quantity,
            "status": meta.status,
            "kicad_pro": meta.kicad_pro,
            "kicad_pcb": meta.kicad_pcb,
            "project_dir": meta.project_dir,
        }
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)


def resolve_schematic_path(
    explicit: str | None = None,
    *,
    workspace: str | Path | None = None,
) -> str:
    """Resolve a schematic path from explicit arg or active project.

    Priority:
      1. Explicit path (if it's a valid file or has .kicad_sch suffix)
      2. Active project's schematic
      3. Nearest KiCad project in cwd/parent directory
      4. Error with helpful message

    Returns the resolved schematic path string.
    Raises ValueError if nothing resolves.
    """
    if explicit:
        p = Path(explicit)
        # If it looks like a file path (exists, or has .kicad_sch suffix)
        if p.exists() or explicit.endswith(".kicad_sch"):
            return explicit

    # Try active project
    mgr = ProjectManager(workspace=workspace)
    active = mgr.get_active()
    if active and active.schematic:
        return active.schematic

    # Try nearest KiCad project in cwd/parent
    from footfindr.kicad.discovery import find_nearest_kicad_project
    import sys
    nearest = find_nearest_kicad_project()
    if nearest and nearest.sch_file:
        # Print auto-resolution message to stderr so it's visible
        print(f"Using nearest KiCad project: {nearest.name}", file=sys.stderr)
        return str(nearest.sch_file)

    # If explicit was given but didn't look like a file, return it anyway
    if explicit:
        return explicit

    raise ValueError(
        "No active project and no KiCad project found nearby.\n\n"
        "Try:\n"
        "  ff project use .\n"
        "  ff project discover --all\n"
        "  ff config add kicad.root <folder>"
    )
