"""FootFindr CLI -- Typer + Rich command-line interface.

Entry points: ``ff`` and ``footfindr`` (both registered in pyproject.toml).
All common commands have short aliases.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from footfindr import __version__

app = typer.Typer(
    help="FootFindr -- KiCad footprint resolver, part intelligence, and build pipeline CLI.",
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# Schematic resolution helper
# ---------------------------------------------------------------------------

def _resolve_schematic(explicit: str | None) -> str:
    """Resolve schematic path from explicit arg or active project."""
    from footfindr.project import resolve_schematic_path

    try:
        return resolve_schematic_path(explicit)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Global callback (version, etc.)
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        console.print(f"FootFindr {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

@app.command()
def scan(
    schematic: Optional[str] = typer.Argument(None, help="Path to .kicad_sch file (uses active project if omitted)."),
) -> None:
    """Scan a KiCad schematic and display component summary."""
    from footfindr.kicad.schematic import KiCadSchematicReader
    from footfindr.reports.terminal import print_scan_summary

    sch_path = _resolve_schematic(schematic)
    reader = KiCadSchematicReader()
    sch = reader.read(sch_path)
    print_scan_summary(sch.symbols)


# ---------------------------------------------------------------------------
# resolve / res
# ---------------------------------------------------------------------------

def _do_resolve(
    schematic: str | None,
    targets: str = "all",
    apply: bool = False,
    force: bool = False,
    backup: bool = True,
    min_confidence: float = 0.92,
    decision_log: str | None = None,
) -> None:
    """Shared implementation for resolve and res commands."""
    from footfindr.reports.decision_log import write_decision_log
    from footfindr.reports.terminal import print_resolve_summary
    from footfindr.resolve.engine import run_resolve

    # Disambiguate: if first arg looks like a file path, it's the schematic
    # and targets defaults to "all"
    actual_schematic = schematic
    actual_targets = targets

    if schematic and (Path(schematic).exists() or schematic.endswith(".kicad_sch")):
        actual_schematic = schematic
    elif schematic and not Path(schematic).exists() and not schematic.endswith(".kicad_sch"):
        # First arg is a ref/target, not a schematic
        actual_targets = schematic
        if targets != "all":
            actual_targets = f"{schematic},{targets}"
        actual_schematic = None

    sch_path = _resolve_schematic(actual_schematic)

    # Parse targets: "all" or "C1,R1,R2" or "C1 R1 R2"
    if actual_targets.lower() == "all":
        target_list = ["all"]
    else:
        target_list = [t.strip() for t in actual_targets.replace(",", " ").split() if t.strip()]

    decisions = run_resolve(
        sch_path,
        target_list,
        apply=apply,
        force=force,
        backup=backup,
        min_confidence=min_confidence,
    )

    print_resolve_summary(decisions, applied=apply)

    log_path = None
    if apply or decision_log:
        log_path = write_decision_log(
            decisions,
            schematic_path=sch_path,
            output_path=decision_log,
        )
        console.print(f"\n  Decision log: [cyan]{log_path}[/cyan]")

    # Update active project timestamps
    try:
        from footfindr.project import ProjectManager
        pm = ProjectManager()
        active = pm.get_active()
        if active and apply:
            pm.update_last_resolve(active.name, decision_log=str(log_path) if log_path else None)
    except Exception:
        pass


@app.command()
def resolve(
    schematic: Optional[str] = typer.Argument(None, help="Schematic path or ref target (uses active project if omitted)."),
    targets: str = typer.Argument("all", help="Refs to resolve: 'all' or comma-separated list (e.g. C1,R1,R2)."),
    apply: bool = typer.Option(False, "--apply", "-a", help="Write fields to schematic."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing non-empty Footprint."),
    backup: bool = typer.Option(True, "--backup/--no-backup", help="Create backup before apply."),
    min_confidence: float = typer.Option(0.92, "--min-confidence", help="Auto-apply threshold."),
    decision_log: Optional[str] = typer.Option(None, "--decision-log", help="Decision log path."),
) -> None:
    """Resolve components.  Use 'all' to resolve everything, or list specific refs."""
    _do_resolve(schematic, targets, apply, force, backup, min_confidence, decision_log)


@app.command(name="res", hidden=True)
def res(
    schematic: Optional[str] = typer.Argument(None, help="Schematic path or ref target."),
    targets: str = typer.Argument("all", help="Refs to resolve."),
    apply: bool = typer.Option(False, "--apply", "-a", help="Write fields."),
    force: bool = typer.Option(False, "--force"),
    backup: bool = typer.Option(True, "--backup/--no-backup"),
    min_confidence: float = typer.Option(0.92, "--min-confidence"),
    decision_log: Optional[str] = typer.Option(None, "--decision-log"),
) -> None:
    """Alias for 'resolve'."""
    _do_resolve(schematic, targets, apply, force, backup, min_confidence, decision_log)


# ---------------------------------------------------------------------------
# explain / why
# ---------------------------------------------------------------------------

@app.command()
def explain(
    schematic_or_ref: str = typer.Argument(..., help="Schematic path or reference designator."),
    ref: Optional[str] = typer.Argument(None, help="Reference designator (if schematic given first)."),
) -> None:
    """Explain how FootFindr sees a component and what it would decide."""
    from footfindr.reports.terminal import print_component_explanation
    from footfindr.resolve.engine import run_resolve

    # Disambiguate: ff explain C1 vs ff explain schematic.kicad_sch C1
    if ref:
        sch_path = _resolve_schematic(schematic_or_ref)
        target_ref = ref
    else:
        if Path(schematic_or_ref).exists() or schematic_or_ref.endswith(".kicad_sch"):
            console.print("[red]Usage: ff explain [schematic] <ref>[/red]")
            raise typer.Exit(1)
        sch_path = _resolve_schematic(None)
        target_ref = schematic_or_ref

    decisions = run_resolve(sch_path, [target_ref])
    if decisions:
        print_component_explanation(decisions[0])
    else:
        console.print(f"[red]Component {target_ref} not found.[/red]")


# Register alias
app.command(name="why", hidden=True)(explain)


# ---------------------------------------------------------------------------
# project / proj / p
# ---------------------------------------------------------------------------

proj_app = typer.Typer(help="Project context commands.")
app.add_typer(proj_app, name="project")
app.add_typer(proj_app, name="proj", hidden=True)
app.add_typer(proj_app, name="p", hidden=True)

# ---------------------------------------------------------------------------
# ref / r — schematic reference management (M9.2)
# ---------------------------------------------------------------------------

ref_app = typer.Typer(help="Schematic reference management commands.")
app.add_typer(ref_app, name="ref")
app.add_typer(ref_app, name="r", hidden=True)

from footfindr.cli_ref import register_ref_commands
register_ref_commands(ref_app)

# M9.1: Aliases for inspect
# (proj_app.command aliases handled below)


@proj_app.command("start")
def proj_start(
    name: str = typer.Argument(..., help="Project name."),
    schematic: str = typer.Option(..., "--schematic", "-s", help="Path to .kicad_sch file."),
    library: Optional[str] = typer.Option(None, "--library", "-l", help="Active library name."),
    build_qty: Optional[int] = typer.Option(None, "--build-qty", help="Build quantity."),
) -> None:
    """Create and register a new project."""
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    try:
        meta = pm.start(name, schematic, library=library, build_quantity=build_qty)
        console.print(f"[green]Project '{meta.name}' started.[/green]")
        console.print(f"  Schematic: {meta.schematic}")
        console.print(f"  Run: ff project use {name}")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@proj_app.command("status")
def proj_status(
    name: Optional[str] = typer.Argument(None, help="Project name (uses active if omitted)."),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Show project status with KiCad connection report."""
    import json as _json
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    if not name:
        meta = pm.get_active()
        if not meta:
            console.print("[yellow]No active project. Run: ff project use . or ff project use <name>[/yellow]")
            raise typer.Exit(1)
    else:
        try:
            meta = pm.status(name)
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    active_name = pm.get_active_name()
    is_active = active_name == meta.name

    # KiCad file existence checks
    sch_path = Path(meta.schematic) if meta.schematic else None
    pro_path = Path(meta.kicad_pro) if meta.kicad_pro else None
    pcb_path = Path(meta.kicad_pcb) if meta.kicad_pcb else None
    proj_dir = Path(meta.project_dir) if meta.project_dir else None

    sch_exists = sch_path.exists() if sch_path else False
    pro_exists = pro_path.exists() if pro_path else False
    pcb_exists = pcb_path.exists() if pcb_path else False

    # Try schematic parse
    sym_count = 0
    ref_count = 0
    subsheet_count = 0
    sch_parsable = False
    is_complete = True
    sch_writable = False
    sch_modified = None
    if sch_exists:
        try:
            from footfindr.kicad.inspect import inspect_schematic
            inspection = inspect_schematic(str(sch_path))
            sym_count = inspection.symbol_count
            ref_count = len(inspection.refs)
            subsheet_count = len(inspection.subsheets_detected)
            sch_parsable = True
            is_complete = inspection.is_complete
            sch_writable = inspection.writable
            sch_modified = inspection.last_modified
        except Exception:
            sch_parsable = False

    # Check workspace files
    ws = pm.workspace
    has_constraints = (ws / "constraints.yaml").exists()
    has_cache = (ws / "supplier_cache.db").exists()

    if as_json:
        data = {
            "name": meta.name, "status": meta.status,
            "active": is_active, "schematic": meta.schematic,
            "kicad_pro": meta.kicad_pro, "kicad_pcb": meta.kicad_pcb,
            "project_dir": meta.project_dir,
            "sch_exists": sch_exists, "pro_exists": pro_exists,
            "pcb_exists": pcb_exists, "sch_parsable": sch_parsable,
            "symbol_count": sym_count, "ref_count": ref_count,
            "subsheet_count": subsheet_count, "is_complete": is_complete,
            "writable": sch_writable, "last_modified": sch_modified,
            "has_constraints": has_constraints, "has_cache": has_cache,
            "library": meta.active_library, "created": meta.created,
            "build_quantity": meta.build_quantity,
        }
        console.print_json(_json.dumps(data, indent=2))
        return

    console.print(f"\n[bold cyan]Active FootFindr project: {meta.name}[/bold cyan]")
    console.print()

    # KiCad section
    console.print("[bold]KiCad:[/bold]")
    _ok = "[green]found[/green]"
    _miss = "[red]missing[/red]"

    if pro_path:
        console.print(f"  project file:  {pro_path.name}  {_ok if pro_exists else _miss}")
    else:
        console.print(f"  project file:  {_miss}")

    if sch_path:
        parsable_str = ", parsable" if sch_parsable else ", [red]parse error[/red]"
        console.print(f"  schematic:     {sch_path.name}  {_ok if sch_exists else _miss}{parsable_str if sch_exists else ''}")
    else:
        console.print(f"  schematic:     {_miss}")

    if pcb_path:
        console.print(f"  board:         {pcb_path.name}  {_ok if pcb_exists else _miss}")

    # Check sym-lib-table / fp-lib-table
    if proj_dir:
        sym_table = Path(proj_dir) / "sym-lib-table"
        fp_table = Path(proj_dir) / "fp-lib-table"
        if sym_table.exists():
            console.print(f"  sym-lib-table  {_ok}")
        if fp_table.exists():
            console.print(f"  fp-lib-table   {_ok}")

    console.print()

    # Schematic section
    if sch_parsable:
        console.print("[bold]Schematic:[/bold]")
        console.print(f"  symbols:       {sym_count}")
        console.print(f"  refs:          {ref_count}")
        console.print(f"  subsheets:     {subsheet_count}")
        completeness = "complete" if is_complete else "[yellow]root-only (subsheets incomplete)[/yellow]"
        console.print(f"  review scope:  {completeness}")
        console.print(f"  writable:      {'yes' if sch_writable else 'no'}")
        if sch_modified:
            console.print(f"  last modified: {sch_modified}")
        console.print()

    # FootFindr section
    console.print("[bold]FootFindr:[/bold]")
    console.print(f"  constraints:   {'found' if has_constraints else 'not found'}")
    console.print(f"  supplier cache: {'found' if has_cache else 'not found'}")
    console.print(f"  library:       {meta.active_library or '(default)'}")
    console.print(f"  build qty:     {meta.build_quantity or '--'}")
    console.print()

    # Next commands
    console.print("[bold]Next:[/bold]")
    console.print("  ff proj review")
    console.print("  ff bom check")
    console.print("  ff proj packet --out review.md")
    console.print()


@proj_app.command("use")
def proj_use(
    name: str = typer.Argument(..., help="Project name, path, or '.' for current directory."),
) -> None:
    """Set the active project. Accepts '.', name, directory, or .kicad_pro/.kicad_sch path."""
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    try:
        meta, msg = pm.use_smart(name)
        console.print(f"[green]{msg}[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@proj_app.command("discover")
def proj_discover(
    root: Optional[str] = typer.Option(None, "--root", "-r", help="Search a specific root directory."),
    depth: int = typer.Option(4, "--depth", "-d", help="Max scan depth."),
    all_roots: bool = typer.Option(False, "--all", "-a", help="Search all roots without early stopping."),
    explain: bool = typer.Option(False, "--explain", "-e", help="Explain which roots were scanned."),
    include_sch_only: bool = typer.Option(True, "--include-sch-only/--pro-only", help="Include schematic-only projects."),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Discover KiCad projects in configured locations."""
    import json as _json
    from footfindr.project import ProjectManager

    pm = ProjectManager()

    roots_list = None
    if root:
        roots_list = [Path(root)]
    elif all_roots:
        from footfindr.kicad.discovery import get_default_search_roots
        roots_list = get_default_search_roots()

    if all_roots:
        depth = max(depth, 6)

    projects = pm.discover(
        roots=roots_list,
        max_depth=depth,
        include_sch_only=include_sch_only,
        explain=explain,
    )

    if explain:
        from footfindr.kicad.discovery import get_default_search_roots
        search_roots = roots_list or get_default_search_roots()
        console.print("\n[bold]Search roots:[/bold]")
        for r in search_roots:
            console.print(f"  {'[Y]' if r.is_dir() else '[N]'} {r}")
        console.print(f"\n[dim]Depth: {depth}, Include schematic-only: {include_sch_only}[/dim]\n")

    if as_json:
        console.print_json(_json.dumps(
            {"projects": [p.to_dict() for p in projects]}, indent=2))
        return

    if not projects:
        console.print("[yellow]No KiCad projects found.[/yellow]")
        console.print("\nTry:")
        console.print("  ff project discover --all --depth 6")
        console.print("  ff config add kicad.root <folder-containing-KiCad-projects>")
        console.print("  ff project use <path-to-project>")
        return

    console.print(f"\n[bold cyan]Found KiCad projects: {len(projects)}[/bold cyan]\n")
    for i, p in enumerate(projects, 1):
        type_tag = f" [dim]({p.project_type})[/dim]" if p.project_type == "schematic-only" else ""
        console.print(f"[bold]{i}  {p.name}[/bold]{type_tag}")
        if p.pro_file:
            console.print(f"   project:   {p.pro_file}")
        if p.sch_file:
            console.print(f"   schematic: {p.sch_file}")
        if p.pcb_file:
            console.print(f"   board:     {p.pcb_file}")
        if p.subsheets:
            console.print(f"   subsheets: {len(p.subsheets)}")
        console.print()


@proj_app.command("diagnose")
def proj_diagnose(
    query: str = typer.Argument(..., help="Project name, path, or '.' to diagnose."),
    depth: int = typer.Option(6, "--depth", "-d", help="Max discovery depth."),
) -> None:
    """Diagnose why a project cannot be found. Shows every resolution step."""
    from footfindr.kicad.discovery import diagnose_project

    lines = diagnose_project(query, max_depth=depth)
    for line in lines:
        # Colorize key results
        if line.startswith("Result: FOUND"):
            console.print(f"[bold green]{line}[/bold green]")
        elif line.startswith("Result: NOT FOUND"):
            console.print(f"[bold red]{line}[/bold red]")
        elif line.startswith("Result: POSSIBLE"):
            console.print(f"[bold yellow]{line}[/bold yellow]")
        elif "FOUND" in line and "EXACT" not in line and "Result" not in line:
            console.print(f"[green]{line}[/green]")
        elif "FUZZY" in line:
            console.print(f"[yellow]{line}[/yellow]")
        elif line.startswith("Suggested command:") or line.startswith("Try:"):
            console.print(f"[bold]{line}[/bold]")
        else:
            console.print(line)


# Alias: proj diag
@proj_app.command("diag", hidden=True)
def proj_diag(
    query: str = typer.Argument(..., help="Project name, path, or '.' to diagnose."),
    depth: int = typer.Option(6, "--depth", "-d"),
) -> None:
    """Alias for diagnose."""
    proj_diagnose(query, depth)


@proj_app.command("clear")
def proj_clear() -> None:
    """Clear the active project."""
    from footfindr.project import ProjectManager
    pm = ProjectManager()
    pm.clear()
    console.print("[yellow]Active project cleared.[/yellow]")


@proj_app.command("inspect-kicad")
def proj_inspect_kicad(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Inspect KiCad schematic: refs, fields, subsheets, parse status."""
    import json as _json
    from footfindr.kicad.inspect import inspect_schematic

    try:
        sch_path = _resolve_schematic(None)
    except (ValueError, SystemExit):
        console.print("[red]No schematic found. Run: ff project use .[/red]")
        raise typer.Exit(1)

    result = inspect_schematic(sch_path)

    if as_json:
        console.print_json(_json.dumps(result.to_dict(), indent=2))
        return

    console.print(f"\n[bold cyan]KiCad Schematic Inspection[/bold cyan]")
    console.print(f"  Path: [dim]{result.path}[/dim]")
    console.print()

    console.print("[bold]Summary:[/bold]")
    console.print(f"  Symbols:       {result.symbol_count}")
    console.print(f"  With MPN:      {result.has_mpn}")
    console.print(f"  With Mfr:      {result.has_manufacturer}")
    console.print(f"  With IPN:      {result.has_internal_pn}")
    console.print(f"  With Footprint:{result.has_footprint}")
    console.print(f"  Sheets parsed: {result.sheets_parsed}")
    console.print(f"  Writable:      {'yes' if result.writable else 'no'}")
    if result.last_modified:
        console.print(f"  Last modified: {result.last_modified}")
    console.print()

    if result.subsheets_detected:
        console.print(f"[bold]Subsheets: {len(result.subsheets_detected)}[/bold]")
        for ss in result.subsheets_exist:
            console.print(f"  [green]✓[/green] {Path(ss).name}")
        for ss in result.subsheets_missing:
            console.print(f"  [red]✗[/red] {Path(ss).name} (missing)")
        for ss in result.subsheets_parse_failed:
            console.print(f"  [yellow]![/yellow] {Path(ss).name} (parse failed)")
        if not result.is_complete:
            console.print("  [yellow]Review is INCOMPLETE: not all subsheets parsed.[/yellow]")
        console.print()

    if result.field_names:
        console.print(f"[bold]Fields found:[/bold] {', '.join(result.field_names[:20])}")
        console.print()

    if result.parse_warnings:
        console.print("[bold yellow]Warnings:[/bold yellow]")
        for w in result.parse_warnings:
            console.print(f"  [yellow]{w}[/yellow]")
        console.print()

    # Top values
    if result.values_summary:
        top_vals = sorted(result.values_summary.items(), key=lambda x: -x[1])[:10]
        console.print("[bold]Top values:[/bold]")
        for v, cnt in top_vals:
            console.print(f"  {v}: {cnt}")
        console.print()


@proj_app.command("list")
def proj_list() -> None:
    """List all registered projects."""
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    projects = pm.list_all()
    active_name = pm.get_active_name()

    if not projects:
        console.print("[yellow]No projects registered. Run: ff project use . or ff project start <name> --schematic <path>[/yellow]")
        return

    table = Table(title="Projects", show_lines=True, title_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Schematic")
    table.add_column("Active")
    table.add_column("Last Resolve")

    for p in projects:
        table.add_row(
            p.name,
            p.status,
            p.schematic,
            "[green]Y[/green]" if p.name == active_name else "",
            p.last_resolve or "--",
        )
    console.print(table)


@proj_app.command("end")
def proj_end(
    name: str = typer.Argument(..., help="Project name to end."),
) -> None:
    """Mark a project as ended (archived)."""
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    try:
        pm.end(name)
        console.print(f"[yellow]Project '{name}' ended/archived.[/yellow]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

# M9.1: Register config app
from footfindr.cli_config import config_app as _config_app
app.add_typer(_config_app, name="config")
app.add_typer(_config_app, name="cfg", hidden=True)


# ---------------------------------------------------------------------------
# lib / library
# ---------------------------------------------------------------------------

lib_app = typer.Typer(help="Library management commands.")
app.add_typer(lib_app, name="lib")
app.add_typer(lib_app, name="library", hidden=True)


@lib_app.command("new")
def lib_new(
    name: str = typer.Argument(..., help="Library name."),
    master: bool = typer.Option(False, "--master", help="Create as master library."),
    sub: bool = typer.Option(False, "--sub", help="Create as sub-library."),
    parent: Optional[str] = typer.Option(None, "--parent", help="Parent library name."),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Workspace directory."),
) -> None:
    """Create a new library."""
    from footfindr.libraries.manager import LibraryManager

    kind = "master" if master else ("sub" if sub else "approved")
    mgr = LibraryManager(workspace=workspace)

    try:
        meta = mgr.create_library(name, kind, parent=parent)
        console.print(f"[green]Created library '{meta.name}' (kind={kind})[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@lib_app.command("list")
def lib_list(
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """List all registered libraries."""
    from footfindr.libraries.manager import LibraryManager

    mgr = LibraryManager(workspace=workspace)
    libs = mgr.list_libraries()

    if not libs:
        console.print("[yellow]No libraries registered.[/yellow]")
        return

    table = Table(title="Libraries", show_lines=True, title_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Parent")
    table.add_column("Active")

    for lib in libs:
        table.add_row(
            lib.name,
            lib.kind.value,
            lib.parent or "--",
            "Y" if lib.active else "",
        )

    console.print(table)


@lib_app.command("tree")
def lib_tree(
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Show library hierarchy tree."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.reports.terminal import print_library_tree

    mgr = LibraryManager(workspace=workspace)
    tree = mgr.get_tree()
    print_library_tree(tree)


@lib_app.command("use")
def lib_use(
    name: str = typer.Argument(..., help="Library name to activate."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Set the active library."""
    from footfindr.libraries.manager import LibraryManager

    mgr = LibraryManager(workspace=workspace)
    try:
        mgr.set_active(name)
        console.print(f"[green]Active library set to '{name}'[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@lib_app.command("search")
def lib_search(
    category: str = typer.Argument(..., help="Component category (cap, res, ic, ...)."),
    query: str = typer.Argument("", help="Search query (e.g. '10u', '100n', '4k7')."),
    approved: bool = typer.Option(False, "--approved", help="Show only approved parts."),
    raw: bool = typer.Option(False, "--raw", help="Show only raw/unapproved parts."),
    vendor: Optional[str] = typer.Option(None, "--vendor", help="Filter by manufacturer."),
    package: Optional[str] = typer.Option(None, "--package", help="Filter by package code."),
    voltage_min: Optional[str] = typer.Option(None, "--voltage-min", help="Minimum voltage rating."),
    dielectric: Optional[str] = typer.Option(None, "--dielectric", help="Filter by dielectric (C0G, X7R, etc.)."),
) -> None:
    """Search parts across approved and raw libraries.

    Supports equivalent-value search: 100n, 0.1u, and 0.10uF match the same
    capacitance. 4k7, 4700, and 4.7k match the same resistance.
    """
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import search_all_parts

    # Normalize category
    cat_map = {"cap": "capacitor", "res": "resistor", "c": "capacitor", "r": "resistor"}
    cat = cat_map.get(category.lower(), category.lower())

    mgr = LibraryManager()
    results = search_all_parts(
        query, mgr,
        category=cat,
        approved_only=approved,
        raw_only=raw,
        vendor=vendor,
        package=package,
        voltage_min=voltage_min,
        dielectric=dielectric,
    )

    _print_search_results(results)


def _print_search_results(results: list) -> None:
    """Print library search results as a Rich table."""
    if not results:
        console.print("[yellow]No parts found.[/yellow]")
        return

    table = Table(title=f"Search Results ({len(results)} parts)", show_lines=True, title_style="bold cyan")
    table.add_column("MPN", max_width=30)
    table.add_column("Value")
    table.add_column("Voltage")
    table.add_column("Dielectric")
    table.add_column("Tolerance")
    table.add_column("Package")
    table.add_column("Footprint", max_width=35)
    table.add_column("Library", max_width=20)
    table.add_column("Pack", max_width=25)
    table.add_column("Status")
    table.add_column("Approved")

    for p in results:
        status_str = p.status.value if hasattr(p.status, "value") else str(p.status)
        approved_str = "[green]Y[/green]" if p.approved else "[dim]N[/dim]"
        table.add_row(
            p.mpn or "--",
            p.value or "--",
            p.specs.voltage_rating or "--",
            p.specs.dielectric or "--",
            p.specs.tolerance or "--",
            p.package or "--",
            p.footprint or "--",
            p.source_library or "--",
            p.source_pack or "--",
            status_str,
            approved_str,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# lib fetch
# ---------------------------------------------------------------------------

@lib_app.command("fetch")
def lib_fetch(
    source: str = typer.Argument(..., help="Vendor source (e.g. 'murata-grm')."),
    name: str = typer.Option("Murata-GRM-Raw", "--as", help="Library name for fetched data."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max parts to import."),
    force_refresh: bool = typer.Option(False, "--force-refresh", help="Force re-download."),
    cache_only: bool = typer.Option(False, "--cache-only", help="Use cached data only."),
    url: Optional[str] = typer.Option(None, "--url", help="Custom download URL."),
) -> None:
    """Fetch vendor part data from the internet."""
    if source.lower() in ("murata-grm", "murata_grm", "murata"):
        from footfindr.libraries.murata import fetch_murata_grm

        try:
            count, path, warnings = fetch_murata_grm(
                library_name=name,
                limit=limit,
                force_refresh=force_refresh,
                cache_only=cache_only,
                url=url,
            )
            for w in warnings:
                console.print(f"[yellow]  {w}[/yellow]")
            console.print(f"[green]Fetched {count} Murata GRM parts into '{name}' -> {path}[/green]")
        except (RuntimeError, FileNotFoundError) as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    else:
        console.print(f"[red]Unknown fetch source: '{source}'. Supported: murata-grm[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# lib index
# ---------------------------------------------------------------------------

index_app = typer.Typer(help="Part index management commands.")
lib_app.add_typer(index_app, name="index")


@index_app.command("rebuild")
def lib_index_rebuild() -> None:
    """Rebuild the SQLite part index from source-of-truth files."""
    from footfindr.db.index import PartIndex
    from footfindr.libraries.manager import LibraryManager

    mgr = LibraryManager()
    idx = PartIndex()
    console.print("[bold]Rebuilding part index...[/bold]")
    total = idx.rebuild(mgr)
    info = idx.info()
    idx.close()
    console.print(f"[green]Index rebuilt: {total} parts from {len(info.libraries)} libraries[/green]")
    db_kb = info.db_size_bytes / 1024
    console.print(f"  DB size: {db_kb:.0f} KB")
    console.print(f"  Path: {idx._db_path}")


@index_app.command("info")
def lib_index_info() -> None:
    """Show part index statistics."""
    from footfindr.db.index import PartIndex

    idx = PartIndex()
    if not idx._db_path.exists():
        console.print("[yellow]No index found. Run: ff lib index rebuild[/yellow]")
        raise typer.Exit(1)

    info = idx.info()
    idx.close()

    table = Table(title="Part Index Info", show_lines=True, title_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Total parts", str(info.total_parts))
    table.add_row("Libraries", ", ".join(info.libraries) if info.libraries else "none")
    table.add_row("DB size", f"{info.db_size_bytes / 1024:.0f} KB")
    table.add_row("Last rebuilt", info.last_rebuilt or "never")
    table.add_row("Schema version", info.schema_version)
    table.add_row("Path", str(idx._db_path))

    console.print(table)


# ---------------------------------------------------------------------------
# lib ingest
# ---------------------------------------------------------------------------

ingest_app = typer.Typer(help="Ingest raw vendor data into a library.")
lib_app.add_typer(ingest_app, name="ingest")


@ingest_app.command("generic-csv")
def ingest_generic_csv(
    csv_path: str = typer.Argument(..., help="Path to CSV file."),
    name: str = typer.Option(..., "--as", help="Library name for ingested data."),
    category: str = typer.Option("capacitor", "--category", help="Default component category."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Ingest a generic vendor CSV file."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.vendor_ingest import ingest_csv

    mgr = LibraryManager(workspace=workspace)
    count, path = ingest_csv(csv_path, name, mgr, default_category=category)
    console.print(f"[green]Ingested {count} parts into '{name}' -> {path}[/green]")


@ingest_app.command("murata-grm")
def ingest_murata_grm(
    csv_path: str = typer.Argument(..., help="Path to Murata GRM CSV file."),
    name: str = typer.Option("Murata-GRM-Raw", "--as", help="Library name."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max parts to import."),
) -> None:
    """Ingest a manually downloaded Murata GRM MLCC CSV."""
    from footfindr.libraries.murata import ingest_murata_grm_csv

    try:
        count, path = ingest_murata_grm_csv(csv_path, name, limit=limit)
        console.print(f"[green]Ingested {count} Murata GRM parts into '{name}' -> {path}[/green]")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@ingest_app.command("murata")
def ingest_murata(
    csv_path: str = typer.Argument(..., help="Path to Murata CSV."),
    name: str = typer.Option(..., "--as", help="Library name."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Ingest a Murata capacitor catalog CSV (generic format)."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.vendor_ingest import ingest_csv

    mgr = LibraryManager(workspace=workspace)
    count, path = ingest_csv(csv_path, name, mgr, vendor="murata")
    console.print(f"[green]Ingested {count} Murata parts into '{name}' -> {path}[/green]")


@ingest_app.command("tdk")
def ingest_tdk(
    csv_path: str = typer.Argument(..., help="Path to TDK CSV."),
    name: str = typer.Option(..., "--as", help="Library name."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Ingest a TDK capacitor catalog CSV."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.vendor_ingest import ingest_csv

    mgr = LibraryManager(workspace=workspace)
    count, path = ingest_csv(csv_path, name, mgr, vendor="tdk")
    console.print(f"[green]Ingested {count} TDK parts into '{name}' -> {path}[/green]")


@ingest_app.command("kemet")
def ingest_kemet(
    csv_path: str = typer.Argument(..., help="Path to KEMET CSV."),
    name: str = typer.Option(..., "--as", help="Library name."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Ingest a KEMET capacitor catalog CSV."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.vendor_ingest import ingest_csv

    mgr = LibraryManager(workspace=workspace)
    count, path = ingest_csv(csv_path, name, mgr, vendor="kemet")
    console.print(f"[green]Ingested {count} KEMET parts into '{name}' -> {path}[/green]")


# ---------------------------------------------------------------------------
# lib promote
# ---------------------------------------------------------------------------

@lib_app.command("promote")
def lib_promote(
    mpn: str = typer.Argument(..., help="MPN of part to promote."),
    to: str = typer.Option(..., "--to", help="Target approved library name."),
    internal_pn: Optional[str] = typer.Option(None, "--as", help="Custom internal PN."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Promote a raw/candidate part to an approved library."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import promote_part

    mgr = LibraryManager(workspace=workspace)
    try:
        part = promote_part(mpn, to, mgr, internal_pn=internal_pn)
        console.print(
            f"[green]Promoted '{mpn}' -> {part.internal_pn} in '{to}' "
            f"(status={part.status.value})[/green]"
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

# ---------------------------------------------------------------------------
# lib pack (sub-group)
# ---------------------------------------------------------------------------

pack_app = typer.Typer(help="Vendor library pack commands.")
lib_app.add_typer(pack_app, name="pack")


@pack_app.command("build")
def pack_build(
    vendor_type: str = typer.Argument(..., help="Vendor/parser type (e.g. 'murata-grm', 'generic')."),
    csv_path: str = typer.Argument(..., help="Path to source CSV file."),
    out: str = typer.Option(..., "--out", help="Output pack directory."),
    source_type: str = typer.Option("manual_csv", "--source-type", help="Source type: manual_csv, fixture, api."),
    real_source: bool = typer.Option(False, "--real-source", help="Mark as a real/complete vendor catalog."),
    source_url: Optional[str] = typer.Option(None, "--source-url", help="Source download URL."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max parts to import."),
) -> None:
    """Build a vendor library pack from a source CSV."""
    from footfindr.libraries.packs import build_pack

    try:
        meta, pack_dir = build_pack(
            vendor_type,
            csv_path,
            out,
            source_type=source_type,
            real_source=real_source,
            source_url=source_url,
            limit=limit,
        )
        console.print(f"[green]Built vendor pack: {meta.display_name}[/green]")
        console.print(f"  Pack dir: {pack_dir}")
        console.print(f"  Parts imported: {meta.counts.imported_parts}")
        console.print(f"  Rows skipped: {meta.counts.skipped_rows}")
        console.print(f"  Source type: {meta.source.source_type}")
        console.print(f"  Real source: {meta.source.real_source}")
        if not meta.source.real_source:
            console.print("[yellow]  WARNING: Built from fixture/sample - not a complete vendor catalog.[/yellow]")
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@pack_app.command("validate")
def pack_validate(
    pack_dir: str = typer.Argument(..., help="Path to pack directory."),
) -> None:
    """Validate a vendor library pack."""
    from footfindr.libraries.packs import validate_pack

    issues = validate_pack(pack_dir)
    if not issues:
        console.print("[green]Pack is valid.[/green]")
    else:
        critical = [i for i in issues if "non-critical" not in i]
        warnings = [i for i in issues if "non-critical" in i]
        for i in critical:
            console.print(f"[red]  X {i}[/red]")
        for i in warnings:
            console.print(f"[yellow]  ⚠ {i}[/yellow]")
        if critical:
            console.print("[red]Pack validation failed.[/red]")
            raise typer.Exit(1)
        else:
            console.print("[green]Pack is valid (with minor warnings).[/green]")


@pack_app.command("info")
def pack_info_cmd(
    pack_dir: str = typer.Argument(..., help="Path to pack directory."),
) -> None:
    """Show info about a vendor library pack (from directory)."""
    from footfindr.libraries.packs import info_pack

    try:
        info = info_pack(pack_dir)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Pack: {info.get('display_name', '?')}", show_lines=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    table.add_row("Pack name", info.get("pack_name", ""))
    table.add_row("Vendor", info.get("vendor", ""))
    table.add_row("Series", info.get("series", ""))
    table.add_row("Version", info.get("version", ""))
    table.add_row("Category", info.get("category", ""))
    table.add_row("Generated at", info.get("generated_at", ""))

    src = info.get("source", {})
    table.add_row("Source type", src.get("source_type", ""))
    table.add_row("Real source", str(src.get("real_source", False)))
    table.add_row("Complete catalog", str(src.get("is_complete_catalog", False)))
    table.add_row("Source file", src.get("source_file", "") or "")

    counts = info.get("counts", {})
    table.add_row("Raw rows", str(counts.get("raw_rows", 0)))
    table.add_row("Imported parts", str(counts.get("imported_parts", 0)))
    table.add_row("Skipped rows", str(counts.get("skipped_rows", 0)))

    lic = info.get("license", {})
    table.add_row("Redistribution", lic.get("redistribution_status", "unknown"))

    console.print(table)

    # Print warnings
    for w in info.get("warnings", []):
        console.print(f"[yellow]  {w}[/yellow]")


@pack_app.command("list")
def pack_list_cmd() -> None:
    """List all installed vendor packs."""
    from footfindr.libraries.packs import list_installed_packs

    packs = list_installed_packs()
    if not packs:
        console.print("[dim]No vendor packs installed.[/dim]")
        console.print("Build and install a pack:")
        console.print("  ff lib pack build murata-grm <csv> --out <dir>")
        console.print("  ff lib install <dir>")
        return

    table = Table(title="Installed Vendor Packs", show_lines=True)
    table.add_column("Library", style="cyan")
    table.add_column("Vendor")
    table.add_column("Series")
    table.add_column("Parts", justify="right")
    table.add_column("Source")
    table.add_column("Real?")
    table.add_column("Installed")

    for p in packs:
        table.add_row(
            p.get("library_name", ""),
            p.get("vendor", ""),
            p.get("series", ""),
            str(p.get("part_count", "?")),
            p.get("source_type", ""),
            "Yes" if p.get("real_source") else "No",
            p.get("installed_at", "")[:10] if p.get("installed_at") else "",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# lib install / uninstall / update / info
# ---------------------------------------------------------------------------

@lib_app.command("install")
def lib_install(
    pack_dir: str = typer.Argument(..., help="Path to vendor library pack directory."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Install a vendor library pack into the local workspace."""
    from footfindr.libraries.packs import install_pack

    try:
        meta = install_pack(pack_dir, workspace=workspace)
        console.print(f"[green]Installed '{meta.display_name}'[/green]")
        console.print(f"  Pack: {meta.pack_name}")
        console.print(f"  Parts: {meta.counts.imported_parts}")
        console.print(f"  Vendor: {meta.vendor}")
        if not meta.source.real_source:
            console.print("[yellow]  WARNING: This library was built from a fixture/sample file "
                         "and is not a complete vendor catalog.[/yellow]")
        console.print()
        console.print("Search installed parts:")
        console.print(f"  ff lib search cap <value> --raw --vendor {meta.vendor}")
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@lib_app.command("uninstall")
def lib_uninstall(
    library_name: str = typer.Argument(..., help="Name of installed library to remove."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Uninstall a vendor library pack."""
    from footfindr.libraries.packs import uninstall_pack

    try:
        uninstall_pack(library_name, workspace=workspace)
        console.print(f"[green]Uninstalled '{library_name}'[/green]")
    except (FileNotFoundError, KeyError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@lib_app.command("update")
def lib_update(
    library_name: str = typer.Argument(..., help="Name of installed library to update."),
    new_pack_dir: str = typer.Argument(..., help="Path to new pack directory."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Update an installed vendor library pack with a new version."""
    from footfindr.libraries.packs import uninstall_pack, install_pack

    try:
        # Uninstall old
        try:
            uninstall_pack(library_name, workspace=workspace)
            console.print(f"  Removed old '{library_name}'")
        except (FileNotFoundError, KeyError):
            console.print(f"  [dim]'{library_name}' was not previously installed[/dim]")

        # Install new
        meta = install_pack(new_pack_dir, workspace=workspace)
        console.print(f"[green]Updated '{library_name}' -> {meta.display_name} v{meta.version}[/green]")
        console.print(f"  Parts: {meta.counts.imported_parts}")
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@lib_app.command("info")
def lib_info(
    name: str = typer.Argument(..., help="Library or pack name."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Show detailed info about a library or installed pack."""
    from footfindr.libraries.packs import info_pack, list_installed_packs

    # Try as installed pack first
    packs = list_installed_packs(workspace=workspace)
    for p in packs:
        if p.get("library_name") == name or p.get("pack_name") == name:
            try:
                info = info_pack(name, workspace=workspace)
                table = Table(title=f"Library: {name}", show_lines=True)
                table.add_column("Field", style="cyan")
                table.add_column("Value")

                table.add_row("Pack name", info.get("pack_name", ""))
                table.add_row("Display name", info.get("display_name", ""))
                table.add_row("Vendor", info.get("vendor", ""))
                table.add_row("Series", info.get("series", ""))
                table.add_row("Category", info.get("category", ""))
                table.add_row("Kind", info.get("kind", ""))
                table.add_row("Version", info.get("version", ""))
                table.add_row("Generated", info.get("generated_at", ""))

                src = info.get("source", {})
                table.add_row("Source type", src.get("source_type", ""))
                table.add_row("Real source", str(src.get("real_source", False)))
                table.add_row("Complete catalog", str(src.get("is_complete_catalog", False)))
                table.add_row("Source file", src.get("source_file", ""))
                table.add_row("Source SHA256", src.get("source_sha256", "") or "not computed")

                counts = info.get("counts", {})
                table.add_row("Raw rows", str(counts.get("raw_rows", 0)))
                table.add_row("Imported parts", str(counts.get("imported_parts", 0)))
                table.add_row("Skipped rows", str(counts.get("skipped_rows", 0)))

                parser_info = info.get("parser", {})
                if parser_info and parser_info.get("name"):
                    table.add_row("Parser", f"{parser_info.get('name', '')} ({parser_info.get('slug', '')})")
                    table.add_row("Parser version", parser_info.get("version", ""))

                hashes = info.get("hashes", {})
                if hashes:
                    if hashes.get("source_csv"):
                        table.add_row("Hash: source CSV", hashes["source_csv"][:16] + "...")
                    if hashes.get("normalized_yaml"):
                        table.add_row("Hash: parts.yaml", hashes["normalized_yaml"][:16] + "...")
                    if hashes.get("normalized_jsonl"):
                        table.add_row("Hash: parts.jsonl", hashes["normalized_jsonl"][:16] + "...")

                lic = info.get("license", {})
                table.add_row("Redistribution", lic.get("redistribution_status", "unknown"))

                console.print(table)

                for w in info.get("warnings", []):
                    console.print(f"[yellow]  {w}[/yellow]")

                return
            except FileNotFoundError:
                pass

    # Fall back to basic library listing
    from footfindr.libraries.manager import LibraryManager
    mgr = LibraryManager(workspace=workspace)
    libs = mgr.list_libraries()
    for lib in libs:
        if lib.name == name:
            console.print(f"Library: {lib.name}")
            console.print(f"  Kind: {lib.kind.value}")
            console.print(f"  Active: {lib.active}")
            if lib.parts_file:
                console.print(f"  Parts file: {lib.parts_file}")
            return

    console.print(f"[red]Library '{name}' not found.[/red]")
    raise typer.Exit(1)


@lib_app.command("export")
def lib_export(
    name: str = typer.Argument(..., help="Library name to export."),
    out: str = typer.Option(..., "--out", help="Output YAML file path."),
    summary: bool = typer.Option(False, "--summary", help="Export summary only (not full part list)."),
    workspace: Optional[str] = typer.Option(None, "--workspace"),
) -> None:
    """Export a library (approved or raw) to a YAML file."""
    import yaml as _yaml
    from footfindr.libraries.manager import LibraryManager

    mgr = LibraryManager(workspace=workspace)
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Check approved libraries
    approved = mgr.load_approved_parts()
    matching_approved = [p for p in approved if p.source_library == name]

    # Check raw libraries
    raw_records = []
    try:
        raw_records = mgr.load_raw_library(name)
    except FileNotFoundError:
        pass

    if not matching_approved and not raw_records:
        # Try with hyphenated name
        safe_name = name.replace(" ", "-")
        try:
            raw_records = mgr.load_raw_library(safe_name)
        except FileNotFoundError:
            pass

    records = matching_approved or raw_records
    if not records:
        console.print(f"[red]Library '{name}' not found or empty.[/red]")
        raise typer.Exit(1)

    kind = "approved" if matching_approved else "raw"

    if summary:
        data = {
            "library": name,
            "kind": kind,
            "part_count": len(records),
            "manufacturers": list({r.manufacturer or "unknown" for r in records}),
            "categories": list({r.category.value for r in records}),
            "packages": sorted({r.package for r in records if r.package}),
            "sample_parts": [
                {
                    "internal_pn": r.internal_pn,
                    "mpn": r.mpn,
                    "value": r.value,
                    "package": r.package,
                    "footprint": r.footprint,
                }
                for r in records[:10]
            ],
        }
    else:
        data = {
            "library": name,
            "kind": kind,
            "part_count": len(records),
            "parts": [LibraryManager._record_to_dict(r) for r in records],
        }

    with open(out_path, "w", encoding="utf-8") as f:
        _yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    console.print(f"[green]Exported {len(records)} parts ({kind}) to {out_path}[/green]")
    if summary:
        console.print("  (summary mode - full part list not included)")



# ---------------------------------------------------------------------------
# part
# ---------------------------------------------------------------------------

part_app = typer.Typer(help="Part database commands.")
app.add_typer(part_app, name="part")


@part_app.command("search")
def part_search(
    query: str = typer.Argument(..., help="Search query (e.g. '10uF 16V X7R 0805')."),
) -> None:
    """Search approved parts."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import search_parts
    from footfindr.reports.terminal import print_part_search_results

    mgr = LibraryManager()
    results = search_parts(query, mgr)
    print_part_search_results(results)


@part_app.command("show")
def part_show(
    identifier: str = typer.Argument(..., help="InternalPN or MPN."),
) -> None:
    """Show detailed info for a part."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import find_part
    from footfindr.reports.terminal import print_part_detail

    mgr = LibraryManager()
    part = find_part(identifier, mgr)
    if part:
        print_part_detail(part)
    else:
        console.print(f"[red]Part '{identifier}' not found.[/red]")


@part_app.command("approve")
def part_approve(
    internal_pn: str = typer.Argument(..., help="InternalPN to approve."),
) -> None:
    """Set a part's status to approved."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import approve_part

    mgr = LibraryManager()
    try:
        part = approve_part(internal_pn, mgr)
        console.print(f"[green]Part '{part.internal_pn}' approved.[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@part_app.command("deprecate")
def part_deprecate(
    internal_pn: str = typer.Argument(..., help="InternalPN to deprecate."),
) -> None:
    """Set a part's status to deprecated."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import deprecate_part

    mgr = LibraryManager()
    try:
        part = deprecate_part(internal_pn, mgr)
        console.print(f"[yellow]Part '{part.internal_pn}' deprecated.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@part_app.command("bind-footprint")
def part_bind_fp(
    internal_pn: str = typer.Argument(..., help="InternalPN."),
    footprint: str = typer.Argument(..., help="KiCad footprint ref (e.g. Capacitor_SMD:C_0805_2012Metric)."),
) -> None:
    """Bind a footprint reference to a part."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import bind_footprint

    mgr = LibraryManager()
    try:
        part = bind_footprint(internal_pn, footprint, mgr)
        console.print(f"[green]Bound '{part.internal_pn}' -> {part.footprint}[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ---------------------------------------------------------------------------
# datasheet / ds
# ---------------------------------------------------------------------------

ds_app = typer.Typer(help="Datasheet management commands.")
app.add_typer(ds_app, name="datasheet")
app.add_typer(ds_app, name="ds", hidden=True)


@ds_app.command("add")
def ds_add(
    mpn: str = typer.Argument(..., help="MPN to associate the datasheet with."),
    pdf_path: str = typer.Argument(..., help="Path to the PDF file."),
) -> None:
    """Register a local PDF datasheet."""
    from footfindr.datasheets.index import DatasheetIndex

    idx = DatasheetIndex()
    record = idx.add(mpn, pdf_path)
    console.print(f"[green]Added datasheet for '{mpn}' -> {record.local_path}[/green]")


@ds_app.command("extract")
def ds_extract(
    mpn: str = typer.Argument(..., help="MPN to extract text from."),
) -> None:
    """Extract text from a registered datasheet PDF."""
    from footfindr.datasheets.extractor import extract_datasheet
    from footfindr.datasheets.index import DatasheetIndex

    idx = DatasheetIndex()
    text_path, json_path = extract_datasheet(mpn, idx)
    if text_path:
        console.print(f"[green]Extracted text -> {text_path}[/green]")
        if json_path:
            console.print(f"[green]Metadata -> {json_path}[/green]")
    else:
        console.print(f"[red]No datasheet found for '{mpn}'.[/red]")


# ---------------------------------------------------------------------------
# profile / prof
# ---------------------------------------------------------------------------

prof_app = typer.Typer(help="IC profile management commands.")
app.add_typer(prof_app, name="profile")
app.add_typer(prof_app, name="prof", hidden=True)


@prof_app.command("draft")
def profile_draft(
    mpn: str = typer.Argument(..., help="MPN to draft a profile for."),
    mock: bool = typer.Option(False, "--mock", help="Use MockAIProvider for demonstration."),
) -> None:
    """Create a draft IC profile (AI-assisted or skeleton)."""
    from footfindr.ai.profile_drafter import ProfileDrafter
    from footfindr.config import get_workspace

    if mock:
        from footfindr.ai.provider import MockAIProvider
        drafter = ProfileDrafter(provider=MockAIProvider())
        profile = drafter.draft(mpn, datasheet_text="mock datasheet")
    else:
        drafter = ProfileDrafter()
        profile = drafter.draft(mpn)

    ws = get_workspace()
    path = drafter.save_draft(profile, ws / "profiles")
    console.print(f"[green]Draft profile created -> {path}[/green]")
    console.print("[yellow]WARNING: human_approved=false -- review before use.[/yellow]")


# ---------------------------------------------------------------------------
# inventory / inv
# ---------------------------------------------------------------------------

inv_app = typer.Typer(help="Inventory management and search commands.")
app.add_typer(inv_app, name="inventory")
app.add_typer(inv_app, name="inv", hidden=True)


@inv_app.command("cap")
def inv_cap(
    value: str = typer.Argument(..., help="Capacitance value (e.g. 10u)."),
    voltage_min: Optional[str] = typer.Option(None, "--voltage-min"),
    package: Optional[str] = typer.Option(None, "--package"),
) -> None:
    """Search for capacitors in library."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import search_all_parts

    mgr = LibraryManager()
    results = search_all_parts(
        value, mgr,
        category="capacitor",
        package=package,
        voltage_min=voltage_min,
    )
    _print_search_results(results)
    console.print("[dim]  (Local stock tracking: not implemented yet)[/dim]")


# Alias: ff inv c
@inv_app.command("c", hidden=True)
def inv_c(
    value: str = typer.Argument(...),
    voltage_min: Optional[str] = typer.Option(None, "--voltage-min"),
    package: Optional[str] = typer.Option(None, "--package"),
) -> None:
    """Alias for 'inv cap'."""
    inv_cap(value, voltage_min=voltage_min, package=package)


@inv_app.command("res")
def inv_res(
    value: str = typer.Argument(..., help="Resistance value (e.g. 10k)."),
    package: Optional[str] = typer.Option(None, "--package"),
) -> None:
    """Search for resistors in library."""
    from footfindr.libraries.manager import LibraryManager
    from footfindr.libraries.promotion import search_all_parts

    mgr = LibraryManager()
    results = search_all_parts(
        value, mgr,
        category="resistor",
        package=package,
    )
    _print_search_results(results)
    console.print("[dim]  (Local stock tracking: not implemented yet)[/dim]")


# Alias: ff inv r
@inv_app.command("r", hidden=True)
def inv_r(
    value: str = typer.Argument(...),
    package: Optional[str] = typer.Option(None, "--package"),
) -> None:
    """Alias for 'inv res'."""
    inv_res(value, package=package)


@inv_app.command("receive")
def inv_receive(
    internal_pn: str = typer.Argument(..., help="InternalPN of part to receive."),
    qty: int = typer.Option(..., "--qty", help="Quantity to add."),
    location: str = typer.Option("", "--location", help="Storage location."),
    notes: str = typer.Option("", "--notes", help="Notes."),
) -> None:
    """Receive stock into local inventory."""
    from footfindr.inventory.manager import InventoryManager

    mgr = InventoryManager()
    entry = mgr.receive(internal_pn, qty, location=location, notes=notes)
    console.print(
        f"[green]Received {qty}x {internal_pn}. "
        f"On hand: {entry.qty_on_hand} @ {entry.location or '(no location)'}[/green]"
    )


@inv_app.command("locate")
def inv_locate(
    internal_pn: str = typer.Argument(..., help="InternalPN to locate."),
) -> None:
    """Show where a part is stored."""
    from footfindr.inventory.manager import InventoryManager

    mgr = InventoryManager()
    entry = mgr.locate(internal_pn)
    if entry:
        console.print(f"  {entry.internal_pn}: {entry.qty_on_hand} on hand @ {entry.location or '(no location)'}")
    else:
        console.print(f"[yellow]No inventory record for '{internal_pn}'.[/yellow]")


@inv_app.command("check")
def inv_check(
    project: Optional[str] = typer.Argument(None, help="Project name (uses active if omitted)."),
    builds: int = typer.Option(1, "--builds", "-b", help="Number of builds to check."),
) -> None:
    """Compare BOM requirements against local inventory."""
    from footfindr.bom.generator import generate_bom
    from footfindr.inventory.manager import InventoryManager
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    if project:
        try:
            meta = pm.status(project)
        except ValueError:
            console.print(f"[red]Project '{project}' not found.[/red]")
            raise typer.Exit(1)
    else:
        meta = pm.get_active()
        if not meta:
            console.print("[red]No project specified and no active project.[/red]")
            raise typer.Exit(1)

    # Generate BOM to get requirements
    report = generate_bom(meta.schematic, "posm")

    # Build requirements dict: internal_pn -> qty
    requirements: dict[str, int] = {}
    for row in report.rows:
        key = row.internal_pn or f"UNRESOLVED-{row.value}"
        requirements[key] = row.quantity

    inv = InventoryManager()
    results = inv.check(requirements, builds)

    table = Table(
        title=f"Inventory Check: {meta.name} x {builds} build(s)",
        show_lines=True, title_style="bold cyan",
    )
    table.add_column("InternalPN", style="bold")
    table.add_column("Required", justify="right")
    table.add_column("On Hand", justify="right")
    table.add_column("Shortage", justify="right")
    table.add_column("Location")

    for item in results:
        shortage_style = "[red]" if item.shortage > 0 else "[green]"
        table.add_row(
            item.internal_pn,
            str(item.required),
            str(item.on_hand),
            f"{shortage_style}{item.shortage}[/{shortage_style[1:]}",
            item.location or "--",
        )

    console.print(table)

    shortages = [i for i in results if i.shortage > 0]
    if shortages:
        console.print(f"\n  [red]{len(shortages)} item(s) short for {builds} build(s).[/red]")
    else:
        console.print(f"\n  [green]All parts sufficient for {builds} build(s).[/green]")


@inv_app.command("shortage")
def inv_shortage(
    project: Optional[str] = typer.Argument(None, help="Project name (uses active if omitted)."),
    builds: int = typer.Option(1, "--builds", "-b", help="Number of builds."),
) -> None:
    """Show only items with insufficient stock."""
    from footfindr.bom.generator import generate_bom
    from footfindr.inventory.manager import InventoryManager
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    if project:
        meta = pm.status(project)
    else:
        meta = pm.get_active()
        if not meta:
            console.print("[red]No project specified and no active project.[/red]")
            raise typer.Exit(1)

    report = generate_bom(meta.schematic, "posm")
    requirements: dict[str, int] = {}
    for row in report.rows:
        key = row.internal_pn or f"UNRESOLVED-{row.value}"
        requirements[key] = row.quantity

    inv = InventoryManager()
    shortages = inv.shortage(requirements, builds)

    if not shortages:
        console.print(f"[green]No shortages for {builds} build(s).[/green]")
        return

    table = Table(title=f"Shortages: {meta.name} x {builds}", show_lines=True, title_style="bold red")
    table.add_column("InternalPN", style="bold")
    table.add_column("Required", justify="right")
    table.add_column("On Hand", justify="right")
    table.add_column("Shortage", justify="right", style="red")

    for item in shortages:
        table.add_row(item.internal_pn, str(item.required), str(item.on_hand), str(item.shortage))

    console.print(table)


# ---------------------------------------------------------------------------
# bom
# ---------------------------------------------------------------------------

bom_app = typer.Typer(help="BOM generation and profile management.")
app.add_typer(bom_app, name="bom")

bom_profile_app = typer.Typer(help="BOM profile management.")
bom_app.add_typer(bom_profile_app, name="profile")


@bom_app.command("generate")
def bom_generate(
    project: Optional[str] = typer.Argument(None, help="Project name (uses active if omitted)."),
    profile: str = typer.Option("posm", "--profile", "-p", help="BOM profile name."),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output CSV path."),
) -> None:
    """Generate a BOM for a project."""
    from footfindr.bom.generator import export_bom_csv, generate_bom
    from footfindr.project import ProjectManager

    pm = ProjectManager()
    if project:
        # Check if it's a project name or schematic path
        if Path(project).exists() and project.endswith(".kicad_sch"):
            sch_path = project
        else:
            try:
                meta = pm.status(project)
                sch_path = meta.schematic
            except ValueError:
                sch_path = project  # Try as path
    else:
        meta = pm.get_active()
        if not meta:
            console.print("[red]No project specified and no active project.[/red]")
            raise typer.Exit(1)
        sch_path = meta.schematic

    report = generate_bom(sch_path, profile)

    # Print warnings
    for w in report.warnings:
        console.print(f"[yellow]  WARNING: {w}[/yellow]")

    if out:
        csv_path = export_bom_csv(report, out, profile)
        console.print(f"\n[green]BOM written to {csv_path} ({report.total_unique} lines, {report.total_parts} parts)[/green]")
    else:
        # Print Rich table
        _print_bom_table(report, profile)
        console.print(f"\n  [dim]To save as CSV: ff bom generate --profile {profile} --out bom_{profile}.csv[/dim]")


@bom_app.callback(invoke_without_command=True)
def bom_default(
    ctx: typer.Context,
    profile: str = typer.Option("posm", "--profile", "-p", help="BOM profile."),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output CSV."),
) -> None:
    """Generate BOM using active project (shorthand)."""
    if ctx.invoked_subcommand is not None:
        return
    bom_generate(project=None, profile=profile, out=out)


def _print_bom_table(report, profile_name: str) -> None:
    """Print BOM as Rich table."""
    from footfindr.bom.profiles import load_profile

    prof = load_profile(profile_name)

    table = Table(
        title=f"BOM ({profile_name}) - {report.total_parts} parts, {report.total_unique} unique",
        show_lines=True, title_style="bold cyan",
    )

    from footfindr.bom.generator import _get_row_field

    for col in prof.columns:
        table.add_column(col.name)

    for row in report.rows:
        csv_row = []
        for col in prof.columns:
            csv_row.append(_get_row_field(row, col.source, col.default))
        table.add_row(*csv_row)

    console.print(table)


@bom_profile_app.command("list")
def bom_profile_list() -> None:
    """List available BOM profiles."""
    from footfindr.bom.profiles import list_profiles

    profiles = list_profiles()
    if not profiles:
        console.print("[yellow]No BOM profiles found.[/yellow]")
        return

    table = Table(title="BOM Profiles", show_lines=True, title_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Columns")
    table.add_column("Group By")

    for p in profiles:
        table.add_row(
            p.name,
            p.description,
            str(len(p.columns)),
            p.group_by,
        )
    console.print(table)


@bom_profile_app.command("show")
def bom_profile_show(
    name: str = typer.Argument(..., help="Profile name."),
) -> None:
    """Show a BOM profile's configuration."""
    from footfindr.bom.profiles import load_profile

    try:
        prof = load_profile(name)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]BOM Profile: {prof.name}[/bold cyan]")
    console.print(f"  Description: {prof.description}")
    console.print(f"  Group by: {prof.group_by}")
    console.print(f"  Exclude DNP: {prof.exclude_dnp}")
    console.print(f"  Warn missing: {', '.join(prof.warn_missing)}")
    console.print(f"\n  Columns ({len(prof.columns)}):")
    for col in prof.columns:
        console.print(f"    {col.name} <- {col.source}")


@bom_profile_app.command("new")
def bom_profile_new(
    name: str = typer.Argument(..., help="New profile name."),
    from_profile: Optional[str] = typer.Option(None, "--from", help="Copy from existing profile."),
) -> None:
    """Create a new BOM profile."""
    from footfindr.bom.profiles import create_profile

    try:
        path = create_profile(name, from_profile=from_profile)
        console.print(f"[green]Created BOM profile '{name}' -> {path}[/green]")
    except FileExistsError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@bom_profile_app.command("validate")
def bom_profile_validate(
    name: str = typer.Argument(..., help="Profile name to validate."),
) -> None:
    """Validate a BOM profile."""
    from footfindr.bom.profiles import validate_profile

    issues = validate_profile(name)
    if not issues:
        console.print(f"[green]Profile '{name}' is valid.[/green]")
    else:
        console.print(f"[red]Profile '{name}' has {len(issues)} issue(s):[/red]")
        for issue in issues:
            console.print(f"  - {issue}")


@bom_profile_app.command("edit")
def bom_profile_edit(
    name: str = typer.Argument(..., help="Profile name to edit."),
) -> None:
    """Open a BOM profile in your editor."""
    from footfindr.bom.profiles import get_profile_path

    path = get_profile_path(name)
    if not path:
        console.print(f"[red]Profile '{name}' not found.[/red]")
        raise typer.Exit(1)

    editor = os.environ.get("EDITOR")
    if editor:
        os.system(f"{editor} {path}")
    else:
        console.print(f"Profile path: [cyan]{path}[/cyan]")
        console.print("[dim]Set $EDITOR to open automatically, or edit the file manually.[/dim]")


# ---------------------------------------------------------------------------
# init / doctor
# ---------------------------------------------------------------------------

@app.command()
def init(
    path: str = typer.Argument(".", help="Directory to initialize."),
) -> None:
    """Initialize a FootFindr workspace."""
    from footfindr.config import get_workspace

    ws = get_workspace(project_dir=path)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "raw").mkdir(exist_ok=True)
    (ws / "approved").mkdir(exist_ok=True)
    (ws / "profiles").mkdir(exist_ok=True)
    (ws / "datasheets").mkdir(exist_ok=True)
    (ws / "reports").mkdir(exist_ok=True)
    (ws / "projects").mkdir(exist_ok=True)
    (ws / "bom_profiles").mkdir(exist_ok=True)
    console.print(f"[green]Initialized FootFindr workspace at {ws}[/green]")


@app.command()
def doctor() -> None:
    """Check local FootFindr/KiCad configuration health."""
    import shutil

    console.print("[bold]FootFindr Doctor[/bold]\n")
    console.print(f"  Version: {__version__}")
    console.print(f"  Python:  {sys.version.split()[0]}")

    # Check KiCad CLI
    kicad_cli = shutil.which("kicad-cli")
    if kicad_cli:
        console.print(f"  KiCad CLI: [green]{kicad_cli}[/green]")
    else:
        console.print("  KiCad CLI: [yellow]not found[/yellow]")

    # Check config
    from footfindr.config import load_config
    cfg = load_config()
    console.print(f"  Config version: {cfg.version}")
    if cfg.schematic:
        sch_exists = Path(cfg.schematic).exists()
        status = "[green]found[/green]" if sch_exists else "[red]not found[/red]"
        console.print(f"  Schematic: {cfg.schematic} ({status})")

    # Check active project
    from footfindr.project import ProjectManager
    pm = ProjectManager()
    active = pm.get_active()
    if active:
        console.print(f"  Active project: [green]{active.name}[/green]")
    else:
        console.print("  Active project: [dim]none[/dim]")

    console.print("\n  [green]Basic checks passed.[/green]")


# ---------------------------------------------------------------------------
# footprint / fp — KiCad footprint discovery and mapping (M9.3)
# ---------------------------------------------------------------------------

fp_app = typer.Typer(help="KiCad footprint discovery, mapping, and binding commands.")
app.add_typer(fp_app, name="fp")
app.add_typer(fp_app, name="footprint", hidden=True)

from footfindr.cli_footprint import register_footprint_commands as _reg_fp
_reg_fp(fp_app)


# ---------------------------------------------------------------------------
# datasheet stubs (M9.3 — hooks for M10)
# ---------------------------------------------------------------------------

datasheet_app = typer.Typer(help="Datasheet management (preview — M10).")
app.add_typer(datasheet_app, name="datasheet")
app.add_typer(datasheet_app, name="ds", hidden=True)


@datasheet_app.command("attach")
def datasheet_attach_cmd(
    ref: str = typer.Argument(..., help="Schematic reference (e.g. U1)."),
    pdf_path: str = typer.Argument(..., help="Path to datasheet PDF."),
) -> None:
    """Attach a datasheet to a schematic reference. (Not yet implemented.)"""
    console.print(f"[yellow]Datasheet attach is not yet implemented (planned for M10).[/yellow]")
    console.print(f"  ref: {ref}")
    console.print(f"  pdf: {pdf_path}")
    console.print(f"\nThis will store the datasheet reference for automated footprint/package extraction.")


# ---------------------------------------------------------------------------
# supplier commands
# ---------------------------------------------------------------------------

supplier_app = typer.Typer(help="Supplier lookup, cache, and provider management.")
app.add_typer(supplier_app, name="supplier")
app.add_typer(supplier_app, name="supp", hidden=True)
app.add_typer(supplier_app, name="sup", hidden=True)


@supplier_app.command("providers")
def supplier_providers() -> None:
    """List all registered supplier providers with capability matrix."""
    from footfindr.suppliers.registry import SupplierRegistry

    reg = SupplierRegistry()
    table = Table(title="Supplier Providers", show_lines=True, title_style="bold cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Configured")
    table.add_column("Live")
    table.add_column("Sandbox")
    table.add_column("Lookup")
    table.add_column("Search")
    table.add_column("Stock")
    table.add_column("Datasheet")
    table.add_column("Lifecycle")
    table.add_column("Cart")
    table.add_column("Order")
    table.add_column("Status")

    for p in reg.list_providers():
        configured = "[green]yes[/green]" if p.configured else "[red]no[/red]"
        live = "[green]yes[/green]" if p.live_implemented else "[dim]no[/dim]"
        sandbox = "[green]yes[/green]" if p.sandbox else "[dim]no[/dim]"
        cap = p.capabilities
        lookup = "[green]yes[/green]" if cap and cap.lookup else "[dim]no[/dim]"
        search = "[green]yes[/green]" if cap and cap.search else "[dim]no[/dim]"
        stock = "[green]yes[/green]" if cap and cap.stock_price else "[dim]no[/dim]"
        ds = "[green]yes[/green]" if cap and cap.datasheet else "[dim]no[/dim]"
        lc = "[green]yes[/green]" if cap and cap.lifecycle else "[dim]no[/dim]"
        cart = "[yellow]disabled[/yellow]" if cap and not cap.cart else "[dim]n/a[/dim]"
        order = "[yellow]disabled[/yellow]" if cap and not cap.order else "[dim]n/a[/dim]"

        status_style = {
            "ready": "[green]",
            "missing credentials": "[red]",
            "mock only": "[yellow]",
        }.get(p.status, "[white]")

        table.add_row(
            p.name, configured, live, sandbox,
            lookup, search, stock, ds, lc, cart, order,
            f"{status_style}{p.status}[/]",
        )

    console.print(table)


def _resolve_provider(
    supplier: str | None,
    mock: bool,
    reg,
):
    """Resolve provider by --supplier or --mock. Errors if none available."""
    if mock:
        return reg.get("mock")

    if supplier:
        provider = reg.get(supplier)
        if not provider:
            console.print(f"[red]Provider '{supplier}' not found.[/red]")
            console.print(f"Available: {', '.join(p.name for p in reg.list_providers())}")
            raise typer.Exit(1)
        if not provider.is_configured():
            from footfindr.suppliers.http import SupplierHTTPError
            try:
                provider.lookup_mpn("__test__")
            except SupplierHTTPError as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1)
            except Exception:
                pass
        return provider

    # No --mock and no --supplier: try all configured live providers
    configured = reg.get_configured_live()
    if configured:
        return configured[0]  # Return first configured provider

    console.print("[red]No live supplier credentials configured.[/red]")
    console.print("Set environment variables for at least one provider:")
    console.print("  FOOTFINDR_MOUSER_PART_API_KEY")
    console.print("  FOOTFINDR_NEXAR_CLIENT_ID + FOOTFINDR_NEXAR_CLIENT_SECRET")
    console.print("  FOOTFINDR_JLCPCB_APP_ID + FOOTFINDR_JLCPCB_ACCESS_KEY")
    console.print("  FOOTFINDR_DIGIKEY_CLIENT_ID + FOOTFINDR_DIGIKEY_CLIENT_SECRET")
    console.print("\nor run:\n  ff supplier lookup <MPN> --mock")
    raise typer.Exit(1)


@supplier_app.command("lookup")
def supplier_lookup(
    mpn: str = typer.Argument(..., help="Manufacturer part number to look up."),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", "-m", help="Manufacturer name."),
    supplier: Optional[str] = typer.Option(None, "--supplier", "-s", help="Specific supplier to query."),
    mock: bool = typer.Option(False, "--mock", help="Use mock provider for testing."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Skip cache, query provider directly."),
    refresh: bool = typer.Option(False, "--refresh", help="Force refresh from live provider (bypass cache)."),
    add_to_session: bool = typer.Option(False, "--add-to-session", "--add", help="Add result to active search session."),
    debug: bool = typer.Option(False, "--debug", help="Show debug output (secrets redacted)."),
) -> None:
    """Look up a part by MPN via supplier cache and provider."""
    import logging
    if debug:
        logging.basicConfig(level=logging.DEBUG)

    from footfindr.suppliers.cache import SupplierCache
    from footfindr.suppliers.registry import SupplierRegistry

    cache = SupplierCache()
    reg = SupplierRegistry()

    # Check cache first (unless refresh or no-cache or debug)
    if not no_cache and not refresh and not debug:
        cached = cache.lookup(mpn, manufacturer=manufacturer, supplier=supplier)
        # Filter out invalid/corrupt cached entries
        cached = [c for c in cached if c.is_valid()]
        if cached:
            console.print(f"[bold cyan]Cache hit for {mpn}[/bold cyan]")
            for entry in cached:
                _print_supplier_part(entry)
            # If --add-to-session, merge cached results
            if add_to_session and cached:
                _merge_lookup_into_session(cached)
            cache.close()
            return

    provider = _resolve_provider(supplier, mock, reg)

    try:
        # Pass debug if the provider supports it
        lookup_kwargs: dict = {"manufacturer": manufacturer}
        if hasattr(provider, 'lookup_mpn') and 'debug' in provider.lookup_mpn.__code__.co_varnames:
            lookup_kwargs["debug"] = debug
        result = provider.lookup_mpn(mpn, **lookup_kwargs)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        cache.close()
        raise typer.Exit(1)

    if result and result.is_valid():
        cache.store(result)
        console.print(f"[bold green]Found via {provider.name}[/bold green] [dim](live)[/dim]")
        _print_supplier_part(result)
        # If --add-to-session, merge into active session
        if add_to_session:
            _merge_lookup_into_session([result])
    elif result and not result.is_valid():
        console.print(
            f"[yellow]{provider.name} returned data but no usable product details for {mpn}.[/yellow]\n"
            f"[dim]No cache entry written.[/dim]"
        )
    else:
        console.print(f"[yellow]No results for {mpn} via {provider.name}[/yellow]")

    cache.close()


def _merge_lookup_into_session(results: list) -> None:
    """Merge lookup result(s) into the active search session."""
    from footfindr.cli_supplier import _merge_results_into_session, _get_session_manager

    mgr = _get_session_manager()
    existing = mgr.load()

    if existing:
        merged, skipped = _merge_results_into_session(existing, results)
        mgr.save(existing)
        console.print(
            f"[green]Added {merged} result(s) to active session "
            f"({len(existing.original_results)} total, {skipped} duplicate(s) skipped)[/green]"
        )
    else:
        # No existing session — create new from lookup
        import datetime
        from footfindr.suppliers.session import SearchSession
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        session = SearchSession(
            query=results[0].mpn if results else "lookup",
            suppliers=[results[0].supplier] if results else [],
            created_at=now,
            last_updated=now,
            original_results=list(results),
            active_result_ids=[r.result_id for r in results],
        )
        mgr.save(session)
        console.print(f"[green]No existing session. Created new session with {len(results)} result(s).[/green]")


# M8.5: Register variant browser commands from cli_supplier module
from footfindr.cli_supplier import register_supplier_commands as _reg_supplier
_reg_supplier(supplier_app, lib_app)

# M8.6: Register constraint and plan commands
from footfindr.cli_constraint import register_constraint_commands as _reg_constraint
_reg_constraint(app)
from footfindr.cli_plan import register_plan_commands as _reg_plan
_reg_plan(app)

# M9.4: Register intelligence commands (net, rails, cap, suggest)
from footfindr.cli_intelligence import register_intelligence_commands as _reg_intel
_reg_intel(app)


# M8.7: Alias discovery command
@app.command("aliases")
def aliases_cmd() -> None:
    """Show all command and flag aliases."""
    from rich.table import Table as RichTable

    table = RichTable(
        title="FootFindr CLI Aliases",
        show_lines=False,
        title_style="bold cyan",
    )
    table.add_column("Canonical", style="bold")
    table.add_column("Aliases", style="green")
    table.add_column("Notes", style="dim")

    # Command group aliases
    table.add_row("ff supplier", "ff sup, ff supp", "Top-level group")
    table.add_row("ff constraint", "ff con, ff cons, ff constraints", "Top-level group")
    table.add_row("", "", "")

    # Supplier subcommand aliases
    table.add_row("ff supplier search", "ff sup s", "")
    table.add_row("ff supplier filter", "ff sup filt, ff sup f", "")
    table.add_row("ff supplier filter-clear", "ff sup fclear, ff sup fc", "")
    table.add_row("ff supplier sort", "ff sup sort", "No alias")
    table.add_row("ff supplier group", "ff sup grp, ff sup g", "")
    table.add_row("ff supplier list", "ff sup ls", "")
    table.add_row("ff supplier show", "ff sup sh", "")
    table.add_row("ff supplier fields", "ff sup fld", "")
    table.add_row("ff supplier explain-diff", "ff sup diff", "")
    table.add_row("ff supplier explain", "ff sup exp", "")
    table.add_row("ff supplier choose", "ff sup ch", "")
    table.add_row("ff supplier shortlist", "ff sup sl", "Sub-app")
    table.add_row("ff supplier session", "ff sup sess", "Sub-app")
    table.add_row("ff supplier search-for", "ff sup sf", "")
    table.add_row("", "", "")

    # Library aliases
    table.add_row("ff lib promote-supplier", "ff lib psup, ff lib prom-sup", "")
    table.add_row("", "", "")

    # Constraint aliases
    table.add_row("ff constraint group", "ff con grp", "Sub-app")
    table.add_row("ff constraint group create", "ff con grp new", "")
    table.add_row("", "", "")

    # Plan aliases
    table.add_row("ff plan list", "ff plan ls", "")
    table.add_row("ff plan show", "ff plan sh", "")
    table.add_row("ff plan apply", "ff plan ap", "")
    table.add_row("ff plan discard", "ff plan drop", "")
    table.add_row("", "", "")

    # Flag aliases
    table.add_row("[bold]Flag Aliases[/bold]", "", "")
    table.add_row("--supplier", "-s", "")
    table.add_row("--suppliers", "-S, --sups", "")
    table.add_row("--mini", "--min, -q", "")
    table.add_row("--refresh", "-r", "")
    table.add_row("--json", "-j", "")
    table.add_row("--columns", "--cols", "")
    table.add_row("--part-numbers-only", "--pn-only", "")
    table.add_row("--apply", "-a", "")
    table.add_row("--plan", "-p", "")
    table.add_row("--dry-run", "--dry", "")
    table.add_row("--force", "-f", "")
    table.add_row("", "", "")

    # Supplier name aliases
    table.add_row("[bold]Supplier Aliases[/bold]", "", "")
    table.add_row("digikey", "dk, digi", "")
    table.add_row("mouser", "mou", "")
    table.add_row("jlcpcb", "jlc, lcsc", "")
    table.add_row("nexar", "nex", "")
    table.add_row("", "", "")

    # Field aliases
    table.add_row("[bold]Field Aliases[/bold]", "", "")
    table.add_row("package", "pack, pkg, case", "")
    table.add_row("temperature_range", "temp, temperature", "")
    table.add_row("lifecycle", "status, stat", "")
    table.add_row("supplier_pn", "sku, spn", "")
    table.add_row("voltage", "volt, v", "")
    table.add_row("capacitance", "cap", "")
    table.add_row("resistance", "res", "")
    table.add_row("tolerance", "tol", "")
    table.add_row("dielectric", "diel", "")
    table.add_row("current", "curr, i", "")
    table.add_row("frequency", "freq", "")
    table.add_row("datasheet_url", "datasheet, ds", "")

    console.print(table)
    console.print("\n[dim]Canonical commands always work. Aliases are hidden from --help.[/dim]")

# M9: Register review, BOM check, source-check, cost, profile commands
from footfindr.cli_review import register_review_commands as _reg_review
_reg_review(proj_app, bom_app)

@supplier_app.command("refresh")
def supplier_refresh(
    mpn: str = typer.Argument(..., help="MPN to refresh stock/price for."),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", "-m"),
    supplier: Optional[str] = typer.Option(None, "--supplier", "-s"),
    mock: bool = typer.Option(False, "--mock", help="Use mock provider."),
) -> None:
    """Refresh stock/price data for an MPN from a live provider."""
    from footfindr.suppliers.cache import SupplierCache
    from footfindr.suppliers.registry import SupplierRegistry

    reg = SupplierRegistry()
    provider = _resolve_provider(supplier, mock, reg)

    try:
        result = provider.lookup_mpn(mpn, manufacturer=manufacturer)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if result and result.is_valid():
        cache = SupplierCache()
        cache.store(result)
        cache.close()
        console.print(f"[green]Refreshed {mpn} via {provider.name}[/green]")
        _print_supplier_part(result)
    elif result and not result.is_valid():
        console.print(f"[yellow]{provider.name} returned no usable data for {mpn}[/yellow]")
    else:
        console.print(f"[yellow]No data for {mpn} via {provider.name}[/yellow]")


@supplier_app.command("compare")
def supplier_compare(
    mpn: str = typer.Argument(..., help="MPN to compare across suppliers."),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", "-m"),
    refresh: bool = typer.Option(False, "--refresh", help="Force refresh from live providers."),
    cache_only: bool = typer.Option(False, "--cache-only", help="Only use cached data."),
) -> None:
    """Compare a part across all configured suppliers."""
    from footfindr.suppliers.cache import SupplierCache
    from footfindr.suppliers.registry import SupplierRegistry

    reg = SupplierRegistry()
    cache = SupplierCache()
    results: list = []

    if refresh:
        for provider in reg.get_configured_live():
            try:
                result = provider.lookup_mpn(mpn, manufacturer=manufacturer)
                if result:
                    cache.store(result)
                    results.append(result)
                    console.print(f"  [green]OK[/green] {provider.name}")
                else:
                    console.print(f"  [dim]-[/dim] {provider.name}: no results")
            except Exception as e:
                console.print(f"  [red]FAIL[/red] {provider.name}: {e}")
        # Also get mock
        from footfindr.suppliers.mock import MockSupplierProvider
        mock = MockSupplierProvider()
        mock_result = mock.lookup_mpn(mpn, manufacturer=manufacturer)
        if mock_result:
            results.append(mock_result)
    elif cache_only:
        cached = cache.lookup(mpn, manufacturer=manufacturer)
        results = cached
    else:
        # Try cache first, then configured providers for missing
        cached = cache.lookup(mpn, manufacturer=manufacturer)
        if cached:
            results = cached
        else:
            for provider in reg.get_configured_live():
                try:
                    result = provider.lookup_mpn(mpn, manufacturer=manufacturer)
                    if result:
                        cache.store(result)
                        results.append(result)
                except Exception as e:
                    console.print(f"  [red]FAIL[/red] {provider.name}: {e}")

    cache.close()

    if not results:
        console.print(f"[yellow]No data for {mpn} across any supplier[/yellow]")
        return

    # Build comparison table
    table = Table(title=f"Supplier Comparison: {mpn}", show_lines=True, title_style="bold cyan")
    table.add_column("Field", style="bold")
    for r in results:
        table.add_column(r.supplier.upper())

    def _row(field, extractor):
        vals = [extractor(r) for r in results]
        table.add_row(field, *vals)

    _row("MPN", lambda r: r.mpn)
    _row("Manufacturer", lambda r: r.manufacturer or "-")
    _row("Supplier PN", lambda r: r.supplier_pn or "-")
    _row("Stock", lambda r: f"{r.stock:,}" if r.stock is not None else "-")
    _row("MOQ", lambda r: str(r.minimum_order_quantity) if r.minimum_order_quantity else "-")
    _row("Best Price", lambda r: f"${r.price_breaks[0].unit_price:.4f}" if r.price_breaks else "-")
    _row("Price Breaks", lambda r: ", ".join(
        f"{pb.quantity}+: ${pb.unit_price:.4f}" for pb in r.price_breaks[:4]
    ) if r.price_breaks else "-")
    _row("Packaging", lambda r: r.packaging or "-")
    _row("Lead Time", lambda r: r.lead_time or "-")
    _row("Lifecycle", lambda r: r.lifecycle or "-")
    _row("Datasheet", lambda r: "yes" if r.datasheet_url else "-")
    _row("Last Checked", lambda r: r.last_checked[:19] if r.last_checked else "-")
    _row("Source", lambda r: r.source)

    console.print(table)


# --- supplier auth subcommands ---

auth_app = typer.Typer(help="Supplier authentication management.")
supplier_app.add_typer(auth_app, name="auth")


@auth_app.command("status")
def supplier_auth_status(
    provider_name: str = typer.Argument(..., help="Provider name (digikey, mouser, nexar, jlcpcb)."),
) -> None:
    """Show authentication status for a supplier provider."""
    from footfindr.suppliers.auth import (
        digikey_auth_status,
        mouser_auth_status,
        nexar_auth_status,
        jlcpcb_auth_status,
    )

    status_funcs = {
        "digikey": digikey_auth_status,
        "mouser": mouser_auth_status,
        "nexar": nexar_auth_status,
        "jlcpcb": jlcpcb_auth_status,
    }

    func = status_funcs.get(provider_name.lower())
    if not func:
        console.print(f"[red]Unknown provider '{provider_name}'[/red]")
        console.print(f"Available: {', '.join(status_funcs.keys())}")
        raise typer.Exit(1)

    status = func()

    table = Table(title=f"Auth Status: {status.provider}", show_lines=True, title_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    configured_str = "[green]yes[/green]" if status.configured else "[red]no[/red]"
    table.add_row("Configured", configured_str)

    if status.env_vars_present:
        table.add_row("Env vars set", ", ".join(status.env_vars_present))
    if status.env_vars_missing:
        table.add_row("Env vars missing", "[red]" + ", ".join(status.env_vars_missing) + "[/red]")

    if status.sandbox:
        table.add_row("Mode", "[yellow]sandbox[/yellow]")

    console.print(table)

    if not status.configured:
        console.print(f"\n[yellow]To configure {status.provider}:[/yellow]")
        if status.env_vars_missing:
            for var in status.env_vars_missing:
                console.print(f"  export {var}=<value>")
        console.print(f"  or add to .footfindr/.env")


@auth_app.command("test")
def supplier_auth_test(
    provider_name: str = typer.Argument(..., help="Provider name to test connectivity."),
) -> None:
    """Test API connectivity for a supplier provider."""
    from footfindr.suppliers.registry import SupplierRegistry

    reg = SupplierRegistry()
    provider = reg.get(provider_name.lower())
    if not provider:
        console.print(f"[red]Unknown provider '{provider_name}'[/red]")
        raise typer.Exit(1)

    if not provider.is_configured():
        console.print(f"[red]{provider_name} is not configured. Run: ff supplier auth {provider_name} status[/red]")
        raise typer.Exit(1)

    console.print(f"Testing {provider_name} connectivity...")

    # For DigiKey, report both token modes separately
    if provider_name.lower() == "digikey":
        from footfindr.suppliers.auth import (
            DigiKeyOAuthManager, OAuthTokenManager, DigiKeyCredentials,
        )

        console.print(f"  Credentials: [green]configured[/green]")

        # Test 2-legged token acquisition
        creds = DigiKeyCredentials.from_env()
        base = provider._get_base_url()
        try:
            two_leg_mgr = OAuthTokenManager(
                provider_name="digikey",
                token_url=f"{base}/v1/oauth2/token",
                client_id=creds.client_id,
                client_secret=creds.client_secret,
            )
            two_leg_mgr.get_token()
            console.print(f"  2-legged token: [green]acquired[/green]")
        except Exception as e:
            console.print(f"  2-legged token: [red]failed ({e})[/red]")

        # Check 3-legged cached token
        has_3leg = DigiKeyOAuthManager.has_cached_tokens()
        if has_3leg:
            console.print(f"  3-legged token: [green]cached[/green]")
        else:
            console.print(f"  3-legged token: [yellow]missing (run: ff supplier auth login digikey)[/yellow]")

        # Report which mode the provider will use
        token_mgr = provider._get_token_mgr()
        console.print(f"  Active mode: [bold]{provider._auth_mode}[/bold]")

    try:
        # Make the smallest possible API call
        result = provider.search("test", category=None)
        # provider.search() may return SupplierSearchPage or list
        items = result.items if hasattr(result, "items") else result
        count = len(items)
        console.print(f"  [green]API test: OK using {provider._auth_mode or 'default'} token. "
                       f"Got {count} results.[/green]")
    except Exception as e:
        console.print(f"  [yellow]API test: failed ({e})[/yellow]")
        if provider_name.lower() == "digikey" and not has_3leg:
            console.print("  [yellow]Action: run ff supplier auth login digikey[/yellow]")
        raise typer.Exit(1)


@auth_app.command("clear-tokens")
def supplier_auth_clear_tokens() -> None:
    """Clear all cached OAuth tokens."""
    from footfindr.suppliers.auth import clear_cached_tokens
    if clear_cached_tokens():
        console.print("[green]Cleared cached OAuth tokens.[/green]")
    else:
        console.print("[yellow]No cached tokens found.[/yellow]")


@auth_app.command("login")
def supplier_auth_login(
    provider_name: str = typer.Argument(..., help="Provider to authorize (currently only 'digikey')."),
    timeout: int = typer.Option(600, "--timeout", "-t", help="Seconds to wait for callback."),
    debug: bool = typer.Option(False, "--debug", help="Print debug diagnostics (secrets redacted)."),
    manual_code: bool = typer.Option(False, "--manual-code", help="Paste redirect URL manually instead of using callback server."),
    port: int | None = typer.Option(None, "--port", "-p", help="Override callback server port (default 8765)."),
) -> None:
    """Authorize with a provider that uses browser-based OAuth (DigiKey).

    Opens a browser for you to log in and authorize FootFindr.
    The access/refresh tokens are cached at .footfindr/auth/tokens.json.

    Examples:

        ff supplier auth login digikey
        ff supplier auth login digikey --timeout 600
        ff supplier auth login digikey --manual-code
        ff supplier auth login digikey --port 8766 --debug
    """
    if provider_name.lower() != "digikey":
        console.print(f"[yellow]{provider_name} does not require browser login.[/yellow]")
        console.print("Only DigiKey uses the Authorization Code flow.")
        return

    from footfindr.suppliers.auth import DigiKeyCredentials, DigiKeyOAuthManager

    creds = DigiKeyCredentials.from_env()
    if not creds:
        console.print("[red]DigiKey credentials not configured.[/red]")
        console.print("Set FOOTFINDR_DIGIKEY_CLIENT_ID and FOOTFINDR_DIGIKEY_CLIENT_SECRET")
        raise typer.Exit(1)

    console.print("[bold]Starting DigiKey OAuth Authorization Code flow...[/bold]")
    try:
        mgr = DigiKeyOAuthManager(
            client_id=creds.client_id,
            client_secret=creds.client_secret,
            callback_url=creds.callback_url or "https://localhost:8765/digikey/oauth/callback",
            sandbox=creds.sandbox,
        )
        mgr.do_login(
            timeout=timeout,
            debug=debug,
            manual_code=manual_code,
            port=port,
        )
        console.print(f"[green]DigiKey authorized successfully![/green]")
        console.print("Tokens cached at .footfindr/auth/tokens.json")
        console.print("You can now use: ff supplier lookup <MPN> --supplier digikey")
    except Exception as e:
        console.print(f"[red]DigiKey authorization failed: {e}[/red]")
        raise typer.Exit(1)


# --- supplier cache subcommands ---

cache_app = typer.Typer(help="Supplier cache management.")
supplier_app.add_typer(cache_app, name="cache")


@cache_app.command("info")
def supplier_cache_info() -> None:
    """Show supplier cache statistics."""
    from footfindr.suppliers.cache import SupplierCache

    cache = SupplierCache()
    info = cache.info()
    cache.close()

    table = Table(title="Supplier Cache", show_lines=True, title_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Total entries", str(info.total_entries))
    table.add_row("Suppliers", ", ".join(info.suppliers) if info.suppliers else "none")
    table.add_row("DB size", f"{info.db_size_bytes / 1024:.0f} KB")
    table.add_row("Last updated", info.last_updated or "never")
    table.add_row("Schema version", info.schema_version)

    console.print(table)


@cache_app.command("clear")
def supplier_cache_clear(
    supplier: Optional[str] = typer.Option(None, "--supplier", help="Clear only this supplier."),
    mpn: Optional[str] = typer.Option(None, "--mpn", help="Clear only this MPN."),
) -> None:
    """Clear supplier cache entries.

    Examples:

        ff supplier cache clear
        ff supplier cache clear --supplier digikey
        ff supplier cache clear --supplier digikey --mpn GRM155R60J106ME05D
    """
    from footfindr.suppliers.cache import SupplierCache

    cache = SupplierCache()
    count = cache.clear(supplier=supplier, mpn=mpn)
    cache.close()

    parts = []
    if supplier:
        parts.append(f"supplier={supplier}")
    if mpn:
        parts.append(f"mpn={mpn}")

    label = f" ({', '.join(parts)})" if parts else ""
    console.print(f"[green]Cleared {count} entries from supplier cache{label}[/green]")


@cache_app.command("show")
def supplier_cache_show(
    mpn: str = typer.Argument(..., help="MPN to show cached data for."),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", "-m"),
) -> None:
    """Show cached supplier data for an MPN."""
    from footfindr.suppliers.cache import SupplierCache

    cache = SupplierCache()
    results = cache.lookup(mpn, manufacturer=manufacturer)
    cache.close()

    if not results:
        console.print(f"[yellow]No cached data for {mpn}[/yellow]")
        return

    for entry in results:
        _print_supplier_part(entry)


def _print_supplier_part(part) -> None:
    """Print a supplier part in a Rich table."""
    source_tag = f" [dim][{part.source.upper()}][/dim]" if part.source else ""

    table = Table(show_lines=True, title_style="bold")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Supplier", f"{part.supplier}{source_tag}")
    table.add_row("MPN", part.mpn)
    if part.manufacturer:
        table.add_row("Manufacturer", part.manufacturer)
    if part.supplier_pn:
        table.add_row("Supplier PN", part.supplier_pn)
    if hasattr(part, 'lcsc_pn') and part.lcsc_pn:
        table.add_row("LCSC Part #", part.lcsc_pn)
    if hasattr(part, 'jlc_category') and part.jlc_category:
        table.add_row("JLC Category", part.jlc_category)
    if part.description:
        table.add_row("Description", part.description)
    if part.stock is not None:
        table.add_row("Stock", f"{part.stock:,}")
    if part.price_breaks:
        prices = ", ".join(
            f"{pb.quantity}+: ${pb.unit_price:.4f}"
            for pb in part.price_breaks
        )
        table.add_row("Price breaks", prices)
    if part.lifecycle:
        table.add_row("Lifecycle", part.lifecycle)
    if part.packaging:
        table.add_row("Packaging", part.packaging)
    if part.lead_time:
        table.add_row("Lead time", part.lead_time)
    if part.datasheet_url:
        table.add_row("Datasheet", part.datasheet_url)
    if hasattr(part, 'product_url') and part.product_url:
        table.add_row("Product URL", part.product_url)
    if part.last_checked:
        table.add_row("Last checked", part.last_checked)

    console.print(table)


# ---------------------------------------------------------------------------
# jlc commands
# ---------------------------------------------------------------------------

jlc_app = typer.Typer(help="JLCPCB/LCSC compatibility checker and annotator.")
app.add_typer(jlc_app, name="jlc")


@jlc_app.command("check")
def jlc_check_cmd(
    schematic: Optional[str] = typer.Argument(None, help="Schematic path (uses active project if omitted)."),
    cache_only: bool = typer.Option(False, "--cache-only", help="Only use cached data, no provider lookup."),
    live: bool = typer.Option(False, "--live", help="Query JLCPCB/LCSC API for LCSC codes."),
) -> None:
    """Check schematic for JLCPCB/LCSC compatibility. Read-only."""
    from footfindr.jlc import jlc_check

    sch_path = _resolve_schematic(schematic)

    report = jlc_check(sch_path, cache_only=cache_only, live=live)

    table = Table(title="JLCPCB Compatibility Check", show_lines=True, title_style="bold cyan")
    table.add_column("Ref", style="bold")
    table.add_column("MPN")
    table.add_column("Existing LCSC")
    table.add_column("Matched LCSC")
    table.add_column("JLC Category")
    table.add_column("Status")

    for s in report.statuses:
        status_style = {
            "already_annotated": "[green]",
            "exact": "[blue]",
            "ambiguous": "[yellow]",
            "none": "[red]",
        }.get(s.match_type, "[white]")

        table.add_row(
            s.ref,
            s.mpn or "",
            s.existing_lcsc or "",
            s.matched_lcsc or "",
            s.jlc_category or "",
            f"{status_style}{s.match_type}[/]",
        )

    console.print(table)
    console.print(f"\nTotal: {report.total} | "
                  f"[green]Annotated: {report.already_annotated}[/green] | "
                  f"[blue]Exact match: {report.exact_match}[/blue] | "
                  f"[yellow]Ambiguous: {report.ambiguous}[/yellow] | "
                  f"[red]No match: {report.no_match}[/red]")


@jlc_app.command("annotate")
def jlc_annotate_cmd(
    schematic: Optional[str] = typer.Argument(None, help="Schematic path (uses active project if omitted)."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Show proposed changes vs apply them."),
    live: bool = typer.Option(False, "--live", help="Query JLCPCB/LCSC API for LCSC codes."),
) -> None:
    """Annotate schematic with LCSC Part # fields.

    By default runs in dry-run mode. Use --apply to write exact matches only.
    """
    from footfindr.jlc import jlc_annotate

    sch_path = _resolve_schematic(schematic)

    report = jlc_annotate(sch_path, dry_run=dry_run, live=live)

    exact = [s for s in report.statuses if s.match_type == "exact" and s.matched_lcsc]

    if dry_run:
        if exact:
            console.print("[bold]Proposed LCSC annotations (dry-run):[/bold]")
            for s in exact:
                console.print(f"  {s.ref}: LCSC Part # = {s.matched_lcsc}")
            console.print(f"\n[yellow]{len(exact)} writes proposed. Run with --apply to write.[/yellow]")
        else:
            console.print("[yellow]No exact matches to annotate.[/yellow]")
    else:
        if exact:
            console.print(f"[green]Wrote LCSC Part # for {len(exact)} exact-match parts.[/green]")
            for s in exact:
                console.print(f"  {s.ref}: LCSC Part # = {s.matched_lcsc}")
        else:
            console.print("[yellow]No exact matches found - nothing written.[/yellow]")

    if report.ambiguous > 0:
        console.print(f"[yellow]{report.ambiguous} ambiguous matches - not written (requires manual review).[/yellow]")
    if report.no_match > 0:
        console.print(f"[red]{report.no_match} parts with no LCSC match.[/red]")


# ---------------------------------------------------------------------------
# bom supplier-check
# ---------------------------------------------------------------------------

@bom_app.command("supplier-check")
def bom_supplier_check(
    suppliers: str = typer.Option("mouser,nexar,jlcpcb", "--suppliers", help="Comma-separated supplier list."),
    refresh: bool = typer.Option(False, "--refresh", help="Force refresh from live providers."),
    cache_only: bool = typer.Option(False, "--cache-only", help="Only use cached data."),
) -> None:
    """Check BOM parts against supplier APIs for stock/price."""
    from footfindr.suppliers.cache import SupplierCache
    from footfindr.suppliers.registry import SupplierRegistry
    from footfindr.kicad.schematic import KiCadSchematicReader

    sch_path = _resolve_schematic(None)

    reader = KiCadSchematicReader()
    sch = reader.read(str(sch_path))

    reg = SupplierRegistry()
    cache = SupplierCache()
    supplier_names = [s.strip() for s in suppliers.split(",")]

    table = Table(title="BOM Supplier Check", show_lines=True, title_style="bold cyan")
    table.add_column("Ref", style="bold")
    table.add_column("MPN")
    table.add_column("Manufacturer")
    table.add_column("Suppliers Found")
    table.add_column("Best Stock")
    table.add_column("Best Price")
    table.add_column("Notes")

    for sym in sch.symbols:
        if sym.dnp:
            continue
        mpn = sym.fields.get("MPN", "") or sym.fields.get("mpn", "")
        manufacturer = sym.fields.get("Manufacturer", "") or sym.fields.get("manufacturer", "")

        if not mpn:
            table.add_row(sym.ref, "-", manufacturer or "-", "-", "-", "-", "[dim]No MPN[/dim]")
            continue

        found_suppliers = []
        best_stock = 0
        best_price = None

        for sname in supplier_names:
            provider = reg.get(sname)
            if not provider or not provider.is_configured():
                continue

            # Try cache or refresh
            entries = cache.lookup(mpn, supplier=sname, manufacturer=manufacturer or None)
            if not entries and (refresh or not cache_only):
                try:
                    result = provider.lookup_mpn(mpn, manufacturer=manufacturer or None)
                    if result:
                        cache.store(result)
                        entries = [result]
                except Exception:
                    continue

            for entry in entries:
                found_suppliers.append(sname)
                if entry.stock and entry.stock > best_stock:
                    best_stock = entry.stock
                if entry.price_breaks:
                    price = entry.price_breaks[0].unit_price
                    if best_price is None or price < best_price:
                        best_price = price

        stock_str = f"{best_stock:,}" if best_stock > 0 else "-"
        price_str = f"${best_price:.4f}" if best_price is not None else "-"
        found_str = ", ".join(set(found_suppliers)) if found_suppliers else "[red]none[/red]"

        table.add_row(sym.ref, mpn, manufacturer or "-", found_str, stock_str, price_str, "")

    cache.close()
    console.print(table)


# ---------------------------------------------------------------------------
# Placeholder stub commands (future)
# ---------------------------------------------------------------------------

rf_app = typer.Typer(help="RF component/model extension (not implemented yet).")
app.add_typer(rf_app, name="rf")


@rf_app.callback(invoke_without_command=True)
def rf_placeholder() -> None:
    """RF extension -- not implemented yet."""
    console.print("[yellow]RF extension not implemented yet.[/yellow]")
    console.print("  Future: ff rf search-inductor, ff rf match, ff rf sparam")


pwr_app = typer.Typer(help="Power calculator/checker (not implemented yet).")
app.add_typer(pwr_app, name="pwr")


@pwr_app.callback(invoke_without_command=True)
def pwr_placeholder() -> None:
    """Power calculator -- not implemented yet."""
    console.print("[yellow]Power calculator not implemented yet.[/yellow]")
    console.print("  Future: ff pwr res, ff pwr cap, ff pwr rail, ff pwr budget")


pwrlib_app = typer.Typer(help="Power electronics library (not implemented yet).")
app.add_typer(pwrlib_app, name="pwrlib")


@pwrlib_app.callback(invoke_without_command=True)
def pwrlib_placeholder() -> None:
    """Power electronics library -- not implemented yet."""
    console.print("[yellow]Power electronics library not implemented yet.[/yellow]")
    console.print("  Future: ff pwrlib buck, ff pwrlib ldo, ff pwrlib fet")


# ---------------------------------------------------------------------------
# Placeholder stubs: cpl / fab (future JLCPCB support)
# ---------------------------------------------------------------------------

@app.command(hidden=True)
def cpl(
    project: Optional[str] = typer.Argument(None),
    profile: str = typer.Option("jlcpcb", "--profile"),
) -> None:
    """Generate component placement list (CPL) -- not implemented yet."""
    console.print("[yellow]CPL generation not implemented yet.[/yellow]")
    console.print("  Future: ff cpl <project> --profile jlcpcb")


@app.command(hidden=True)
def fab(
    project: Optional[str] = typer.Argument(None),
    target: str = typer.Option("jlcpcb", "--target"),
) -> None:
    """Generate full fabrication package -- not implemented yet."""
    console.print("[yellow]Fabrication package not implemented yet.[/yellow]")
    console.print("  Future: ff fab <project> --target jlcpcb")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
