"""Footprint CLI commands (M9.3).

Commands for KiCad footprint discovery, indexing, searching, and binding.
Registered on ``fp_app`` / ``footprint_app`` from cli.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()
logger = logging.getLogger("footfindr.cli.footprint")


def register_footprint_commands(fp_app: typer.Typer) -> None:
    """Register all M9.3 footprint subcommands on the given Typer app."""

    # ---- ff fp scan ----
    @fp_app.command("scan")
    def fp_scan_cmd(
        reset: bool = typer.Option(False, "--reset", help="Delete existing index before re-scanning."),
        count_pads: bool = typer.Option(False, "--count-pads", help="Count pads in each footprint (slower)."),
        debug: bool = typer.Option(False, "--debug", "-d", help="Debug mode."),
    ) -> None:
        """Scan KiCad footprint libraries and build the search index.

        Parses fp-lib-table files (project + global), resolves library
        paths, and indexes all .kicad_mod files into a SQLite database.
        """
        if debug:
            logging.basicConfig(level=logging.DEBUG)

        from footfindr.kicad.footprint_index import run_footprint_scan

        # Determine project directory
        project_dir = _get_project_dir()

        console.print("\n[bold cyan]Scanning KiCad footprint libraries...[/bold cyan]\n")

        index, report = run_footprint_scan(project_dir=project_dir, reset=reset)

        # Display scan report
        console.print("[bold]Scan Results:[/bold]")
        _status_line("Project fp-lib-table", report.project_fp_table)
        _status_line("Global fp-lib-table", report.global_fp_table)

        if report.env_vars_found:
            console.print(f"  KiCad env vars:          [green]{', '.join(report.env_vars_found)}[/green]")
        else:
            console.print("  KiCad env vars:          [dim]none detected[/dim]")

        if report.resolved_footprint_dir:
            console.print(f"  Resolved footprint dir:  [green]{report.resolved_footprint_dir}[/green]")

        console.print()

        if report.libraries_indexed:
            console.print(f"[bold]Indexed libraries ({len(report.libraries_indexed)}):[/bold]")
            for lib in sorted(report.libraries_indexed):
                console.print(f"  {lib}")
        else:
            console.print("[yellow]No libraries indexed.[/yellow]")

        console.print()
        console.print(f"[bold]Total footprints indexed: {report.total_footprints}[/bold]")

        # --- Prominent built-in warning (M9.3b requirement) ---
        if not report.builtin_indexed:
            console.print()
            console.print("[bold red]╔══════════════════════════════════════════════════════════════╗[/bold red]")
            console.print("[bold red]║  BUILT-IN KICAD FOOTPRINTS NOT INDEXED                      ║[/bold red]")
            console.print("[bold red]║  Footprint assignment for passives will not work.            ║[/bold red]")
            console.print("[bold red]╚══════════════════════════════════════════════════════════════╝[/bold red]")
            console.print()
            console.print("Run:")
            console.print("  [cyan]ff fp diagnose[/cyan]")
            console.print("  [cyan]ff fp repair --apply[/cyan]")
            console.print("  [cyan]ff config set kicad.footprint-dir <path>[/cyan]")
        else:
            console.print(f"\n[green]✓ Built-in KiCad footprints indexed successfully.[/green]")

        if report.errors:
            n_unresolved = sum(1 for e in report.errors if "Cannot resolve" in e)
            n_other = len(report.errors) - n_unresolved
            if n_unresolved and report.builtin_indexed:
                # Built-ins OK but some extras failed — dim warning
                console.print(f"\n[dim]{n_unresolved} library paths could not be resolved (non-critical)[/dim]")
            elif n_other:
                console.print(f"\n[yellow]Warnings ({n_other}):[/yellow]")
                for err in report.errors[:10]:
                    if "Cannot resolve" not in err:
                        console.print(f"  [yellow]{err}[/yellow]")

        console.print()
        index.close()

    # ---- ff fp diagnose ----
    @fp_app.command("diagnose")
    def fp_diagnose_cmd() -> None:
        """Diagnose KiCad footprint configuration.

        Shows detected KiCad versions, config paths, resolved footprint
        directories, and suggests fix commands.
        """
        from footfindr.kicad.footprint_index import run_footprint_diagnose

        diag = run_footprint_diagnose()

        console.print("\n[bold cyan]KiCad Footprint Diagnostics[/bold cyan]\n")

        # Global fp-lib-tables
        tables = diag.get("global_fp_lib_tables", [])
        if tables:
            console.print(f"[bold]Global fp-lib-table(s):[/bold]")
            for t in tables:
                console.print(f"  [green]{t}[/green]")
        else:
            console.print("[yellow]No global fp-lib-table found.[/yellow]")

        # Env vars
        env_vars = diag.get("env_vars", {})
        if env_vars:
            console.print(f"\n[bold]Environment variables:[/bold]")
            for k, v in env_vars.items():
                console.print(f"  {k} = [green]{v}[/green]")
        else:
            console.print(f"\n[dim]No KiCad env vars set (KICAD9_FOOTPRINT_DIR, etc.)[/dim]")

        # Discovered dirs
        discovered = diag.get("discovered_footprint_dirs", [])
        if discovered:
            console.print(f"\n[bold]Discovered footprint directories:[/bold]")
            for d in discovered:
                console.print(f"  [green]{d}[/green]")
        else:
            console.print(f"\n[yellow]No KiCad footprint directories found on this system.[/yellow]")

        # Built-in check
        cap_ok = diag.get("capacitor_smd_exists", False)
        res_ok = diag.get("resistor_smd_exists", False)
        console.print(f"\n[bold]Built-in library check:[/bold]")
        console.print(f"  Capacitor_SMD.pretty:  {'[green]found[/green]' if cap_ok else '[red]NOT FOUND[/red]'}")
        console.print(f"  Resistor_SMD.pretty:   {'[green]found[/green]' if res_ok else '[red]NOT FOUND[/red]'}")

        verified = diag.get("verified_footprint_dir")
        if verified:
            console.print(f"  Verified dir:          [green]{verified}[/green]")

        # User config
        config_fp = diag.get("config_footprint_dir")
        config_table = diag.get("config_fp_table")
        console.print(f"\n[bold]FootFindr config:[/bold]")
        console.print(f"  kicad.footprint-dir:   {config_fp or '[dim]not set[/dim]'}")
        console.print(f"  kicad.global-fp-table: {config_table or '[dim]not set[/dim]'}")

        # Suggested fix
        fix = diag.get("suggested_fix")
        if fix:
            console.print(f"\n[bold yellow]Suggested fix:[/bold yellow]")
            console.print(f"  [cyan]{fix}[/cyan]")
            console.print(f"  Then run: [cyan]ff fp scan --reset[/cyan]")
        elif cap_ok and res_ok:
            console.print(f"\n[green]✓ KiCad footprints available. Run: ff fp scan --reset[/green]")

        console.print()

    # ---- ff fp repair ----
    @fp_app.command("repair")
    def fp_repair_cmd(
        apply: bool = typer.Option(False, "--apply", "-a", help="Apply the fix automatically."),
    ) -> None:
        """Auto-detect KiCad footprint directory and configure it.

        Searches common KiCad install paths for a directory containing
        Capacitor_SMD.pretty, then sets kicad.footprint-dir.
        """
        from footfindr.kicad.footprint_index import discover_kicad_footprint_dirs

        console.print("\n[bold cyan]Searching for KiCad footprint libraries...[/bold cyan]\n")

        discovered = discover_kicad_footprint_dirs()

        if not discovered:
            console.print("[red]Could not find KiCad footprint libraries on this system.[/red]")
            console.print("\nManual fix:")
            console.print("  [cyan]ff config set kicad.footprint-dir <path>[/cyan]")
            console.print("\nCommon locations:")
            console.print(r"  Windows: C:\Program Files\KiCad\9.0\share\kicad\footprints")
            console.print("  macOS:   /Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")
            console.print("  Linux:   /usr/share/kicad/footprints")
            raise typer.Exit(1)

        fp_dir = discovered[0]
        console.print(f"[green]Found KiCad footprints at:[/green]")
        console.print(f"  {fp_dir}")

        # Verify
        cap_ok = (fp_dir / "Capacitor_SMD.pretty").is_dir()
        res_ok = (fp_dir / "Resistor_SMD.pretty").is_dir()
        console.print(f"\n  Capacitor_SMD.pretty: {'[green]✓[/green]' if cap_ok else '[red]✗[/red]'}")
        console.print(f"  Resistor_SMD.pretty:  {'[green]✓[/green]' if res_ok else '[red]✗[/red]'}")

        if apply:
            try:
                from footfindr.config import set_user_config_value
                set_user_config_value("kicad.footprint-dir", str(fp_dir))
                console.print(f"\n[green]✓ Set kicad.footprint-dir = {fp_dir}[/green]")
                console.print(f"Now run: [cyan]ff fp scan --reset[/cyan]")
            except Exception as e:
                console.print(f"\n[red]Failed to set config: {e}[/red]")
                console.print(f"\nManual command:")
                console.print(f'  [cyan]ff config set kicad.footprint-dir "{fp_dir}"[/cyan]')
        else:
            console.print(f"\nTo apply:")
            console.print(f'  [cyan]ff config set kicad.footprint-dir "{fp_dir}"[/cyan]')
            console.print(f"  [cyan]ff fp scan --reset[/cyan]")
            console.print(f"\nOr run: [cyan]ff fp repair --apply[/cyan]")

        console.print()

    # ---- ff fp list ----
    @fp_app.command("list")
    def fp_list_cmd(
        scope: Optional[str] = typer.Option(None, "--scope", "-s", help="Filter by scope (project, global)."),
        library: Optional[str] = typer.Option(None, "--library", "-l", help="Filter by library nickname."),
        limit: int = typer.Option(50, "--limit", "-L", help="Max results."),
    ) -> None:
        """List indexed footprints."""
        from footfindr.kicad.footprint_index import FootprintIndex

        project_dir = _get_project_dir()
        index = FootprintIndex(project_dir=project_dir)

        if library:
            results = [r for r in index.list_all(scope=scope) if r.library_nickname == library]
        else:
            results = index.list_all(scope=scope)

        if not results:
            console.print("[yellow]No footprints indexed. Run: ff fp scan[/yellow]")
            return

        table = Table(
            title=f"Footprints ({len(results)} total, showing {min(len(results), limit)})",
            show_lines=False,
            title_style="bold cyan",
        )
        table.add_column("KiCad ID", max_width=50)
        table.add_column("Library")
        table.add_column("Scope")
        table.add_column("Tokens")

        for r in results[:limit]:
            table.add_row(
                r.kicad_id,
                r.library_nickname,
                r.scope,
                ", ".join(r.package_tokens) if r.package_tokens else "—",
            )

        console.print(table)
        if len(results) > limit:
            console.print(f"[dim]Showing {limit} of {len(results)}. Use --limit to see more.[/dim]")

        index.close()

    # ---- ff fp search <query> ----
    @fp_app.command("search")
    def fp_search_cmd(
        query: str = typer.Argument(..., help="Search query (e.g. '0603', 'QFN-32', 'Capacitor_SMD')."),
        limit: int = typer.Option(30, "--limit", "-L", help="Max results."),
    ) -> None:
        """Search indexed footprints by package, name, or library."""
        from footfindr.kicad.footprint_index import FootprintIndex

        project_dir = _get_project_dir()
        index = FootprintIndex(project_dir=project_dir)

        results = index.search(query)

        if not results:
            console.print(f"[yellow]No footprints matching '{query}'.[/yellow]")
            count = index.count()
            if count == 0:
                console.print("No footprints indexed. Run: ff fp scan")
            else:
                console.print(f"({count} footprints indexed)")
            index.close()
            return

        table = Table(
            title=f"Footprint Search: '{query}' ({len(results)} matches)",
            show_lines=False,
            title_style="bold cyan",
        )
        table.add_column("#", style="dim")
        table.add_column("KiCad ID", max_width=55)
        table.add_column("Library")
        table.add_column("Scope")
        table.add_column("Tokens")
        table.add_column("Pads", justify="right")

        for i, r in enumerate(results[:limit], 1):
            table.add_row(
                str(i),
                r.kicad_id,
                r.library_nickname,
                r.scope,
                ", ".join(r.package_tokens) if r.package_tokens else "—",
                str(r.pad_count) if r.pad_count else "—",
            )

        console.print(table)
        if len(results) > limit:
            console.print(f"[dim]Showing {limit} of {len(results)}. Use --limit to see more.[/dim]")

        index.close()

    # ---- ff fp show <kicad_id> ----
    @fp_app.command("show")
    def fp_show_cmd(
        kicad_id: str = typer.Argument(..., help="Full KiCad ID (e.g. 'Capacitor_SMD:C_0603_1608Metric')."),
    ) -> None:
        """Show details for a specific footprint."""
        from footfindr.kicad.footprint_index import FootprintIndex

        project_dir = _get_project_dir()
        index = FootprintIndex(project_dir=project_dir)

        record = index.get(kicad_id)

        if not record:
            console.print(f"[red]Footprint not found: {kicad_id}[/red]")
            count = index.count()
            if count == 0:
                console.print("No footprints indexed. Run: ff fp scan")
            else:
                # Try a search
                parts = kicad_id.split(":")
                if len(parts) == 2:
                    similar = index.search(parts[1])
                    if similar:
                        console.print(f"\nDid you mean:")
                        for s in similar[:5]:
                            console.print(f"  {s.kicad_id}")
            index.close()
            return

        table = Table(title=f"Footprint: {kicad_id}", show_lines=True, title_style="bold cyan")
        table.add_column("Field", style="bold")
        table.add_column("Value")

        table.add_row("KiCad ID", record.kicad_id)
        table.add_row("Library", record.library_nickname)
        table.add_row("Footprint Name", record.footprint_name)
        table.add_row("Source Path", record.source_path)
        table.add_row("Scope", record.scope)
        table.add_row("Package Tokens", ", ".join(record.package_tokens) if record.package_tokens else "—")
        table.add_row("Pad Count", str(record.pad_count) if record.pad_count else "—")
        table.add_row("Body Dims", record.body_dims or "—")
        table.add_row("Last Indexed", record.last_indexed or "—")

        console.print(table)
        index.close()

    # ---- ff fp bind <ref> <kicad_id> ----
    @fp_app.command("bind")
    def fp_bind_cmd(
        ref: str = typer.Argument(..., help="Schematic reference (e.g. C1, U3)."),
        kicad_id: str = typer.Argument(..., help="KiCad footprint ID (e.g. 'Capacitor_SMD:C_0603_1608Metric')."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip footprint existence check."),
    ) -> None:
        """Bind a specific ref to a KiCad footprint."""
        from footfindr.kicad.footprint_index import FootprintIndex
        from footfindr.kicad.footprint_mappings import FootprintMappings

        # Validate footprint exists (amendment 7)
        if not force:
            project_dir = _get_project_dir()
            index = FootprintIndex(project_dir=project_dir)
            record = index.get(kicad_id)
            index.close()

            if not record:
                console.print(f"[red]Footprint not found in current KiCad footprint index:[/red]")
                console.print(f"  {kicad_id}")
                console.print("\nTry:")
                console.print("  ff fp scan")
                search_part = kicad_id.split(":")[-1] if ":" in kicad_id else kicad_id
                console.print(f"  ff fp search {search_part}")
                console.print("\nUse --force to bind anyway.")
                raise typer.Exit(1)

        mappings = FootprintMappings()
        mappings.bind_ref(ref, kicad_id, reason=f"Manual binding via ff fp bind")
        console.print(f"[green]Bound {ref} → {kicad_id}[/green]")

    # ---- ff fp bind-package <category> <package> <kicad_id> ----
    @fp_app.command("bind-package")
    def fp_bind_package_cmd(
        category: str = typer.Argument(..., help="Category (capacitor, resistor, etc.)."),
        package: str = typer.Argument(..., help="Package code (0603, 0805, etc.)."),
        kicad_id: str = typer.Argument(..., help="KiCad footprint ID."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip footprint existence check."),
    ) -> None:
        """Bind a category+package combination to a KiCad footprint."""
        from footfindr.kicad.footprint_index import FootprintIndex
        from footfindr.kicad.footprint_mappings import FootprintMappings

        # Validate (amendment 7)
        if not force:
            project_dir = _get_project_dir()
            index = FootprintIndex(project_dir=project_dir)
            record = index.get(kicad_id)
            index.close()

            if not record:
                console.print(f"[red]Footprint not found in current KiCad footprint index:[/red]")
                console.print(f"  {kicad_id}")
                console.print("\nTry:")
                console.print("  ff fp scan")
                console.print(f"  ff fp search {package}")
                console.print("\nUse --force to bind anyway.")
                raise typer.Exit(1)

        # Normalize category
        cat_map = {"cap": "capacitor", "res": "resistor", "ind": "inductor", "c": "capacitor", "r": "resistor"}
        cat = cat_map.get(category.lower(), category.lower())

        mappings = FootprintMappings()
        mappings.bind_package(cat, package, kicad_id, reason=f"Manual binding via ff fp bind-package")
        console.print(f"[green]Bound {cat}:{package} → {kicad_id}[/green]")

    # ---- ff fp bind-mpn <mpn> <kicad_id> ----
    @fp_app.command("bind-mpn")
    def fp_bind_mpn_cmd(
        mpn: str = typer.Argument(..., help="Manufacturer part number."),
        kicad_id: str = typer.Argument(..., help="KiCad footprint ID."),
        force: bool = typer.Option(False, "--force", "-f", help="Skip footprint existence check."),
    ) -> None:
        """Bind an MPN to a KiCad footprint."""
        from footfindr.kicad.footprint_index import FootprintIndex
        from footfindr.kicad.footprint_mappings import FootprintMappings

        # Validate (amendment 7)
        if not force:
            project_dir = _get_project_dir()
            index = FootprintIndex(project_dir=project_dir)
            record = index.get(kicad_id)
            index.close()

            if not record:
                console.print(f"[red]Footprint not found in current KiCad footprint index:[/red]")
                console.print(f"  {kicad_id}")
                console.print("\nTry:")
                console.print("  ff fp scan")
                console.print("\nUse --force to bind anyway.")
                raise typer.Exit(1)

        mappings = FootprintMappings()
        mappings.bind_mpn(mpn, kicad_id, reason=f"Manual binding via ff fp bind-mpn")
        console.print(f"[green]Bound MPN {mpn} → {kicad_id}[/green]")

    # ---- ff fp suggest <ref> ----
    @fp_app.command("suggest")
    def fp_suggest_cmd(
        ref: str = typer.Argument(..., help="Schematic reference (e.g. C1, U3)."),
    ) -> None:
        """Suggest footprint for a ref based on its schematic/constraint data.

        Note: Datasheet-derived dimension parsing is not yet implemented.
        """
        from footfindr.constraints import infer_category
        from footfindr.kicad.footprint_index import FootprintIndex
        from footfindr.kicad.footprint_mappings import FootprintMappings
        from footfindr.kicad.footprint_resolver import FootprintResolver

        # Load symbol
        sym = _load_symbol(ref)
        if not sym:
            console.print(f"[red]{ref} not found in active schematic[/red]")
            raise typer.Exit(1)

        cat, _ = infer_category(ref)
        project_dir = _get_project_dir()
        index = FootprintIndex(project_dir=project_dir)
        mappings = FootprintMappings()
        resolver = FootprintResolver(index, mappings)

        # Build a mock SupplierPart-like object from schematic fields
        from types import SimpleNamespace
        mock_part = SimpleNamespace(
            mpn=sym.fields.get("MPN", ""),
            package=sym.fields.get("Package", "") or "",
            attributes={},
        )

        result = resolver.resolve(mock_part, ref, cat)

        console.print(f"\n[bold cyan]Footprint suggestion for {ref}:[/bold cyan]")
        console.print(f"  Status: {result.status}")
        console.print(f"  Confidence: {result.confidence}")
        if result.footprint:
            console.print(f"  Footprint: [green]{result.footprint}[/green]")
        console.print(f"  Reason: {result.reason}")

        if result.candidates:
            console.print(f"\n[bold]Candidates:[/bold]")
            for c in result.candidates[:10]:
                console.print(f"  {c}")

        if result.status == "missing":
            console.print("\n[dim]Datasheet-derived dimension parsing is not yet implemented (M10).[/dim]")
            console.print("[dim]Try: ff fp bind <ref> <kicad_id>[/dim]")

        index.close()

    # ---- ff fp mappings ----
    @fp_app.command("mappings")
    def fp_mappings_cmd() -> None:
        """Show all configured footprint mappings."""
        from footfindr.kicad.footprint_mappings import FootprintMappings

        mappings = FootprintMappings()
        all_maps = mappings.list_all()

        if not all_maps:
            console.print("[dim]No footprint mappings configured.[/dim]")
            return

        for binding_type, items in all_maps.items():
            table = Table(
                title=f"Footprint Mappings: {binding_type}",
                show_lines=True,
                title_style="bold cyan",
            )
            table.add_column("Key", style="bold")
            table.add_column("Footprint")
            table.add_column("Scope")
            table.add_column("Confidence")

            for item in items:
                table.add_row(
                    item.get("key", "?"),
                    item.get("footprint", "?"),
                    item.get("scope", "?"),
                    item.get("confidence", "?"),
                )

            console.print(table)
            console.print()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_project_dir() -> Path | None:
    """Get the active project directory, or None."""
    try:
        from footfindr.project import ProjectManager
        pm = ProjectManager()
        active = pm.get_active()
        if active and active.project_dir:
            return Path(active.project_dir)
    except Exception:
        pass
    return None


def _status_line(label: str, value: str) -> None:
    """Print a formatted status line."""
    if "found" in value.lower() and "not found" not in value.lower():
        console.print(f"  {label + ':':25s} [green]{value}[/green]")
    else:
        console.print(f"  {label + ':':25s} [yellow]{value}[/yellow]")


def _load_symbol(ref_name: str):
    """Try to load a KiCad symbol by ref from the active schematic."""
    try:
        from footfindr.project import resolve_schematic_path
        from footfindr.kicad.schematic import KiCadSchematicReader
        sch_path = resolve_schematic_path()
        reader = KiCadSchematicReader()
        sch = reader.read(sch_path)
        return sch.symbol_by_ref(ref_name)
    except Exception:
        return None
