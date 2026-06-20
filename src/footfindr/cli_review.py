"""CLI commands for project review, BOM check, source-check, cost, profiles, and packets.

Registers commands on proj_app and bom_app from cli.py.
CLI stays thin — all logic lives in review.py and assembly_profiles.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def _resolve_schematic_for_review(explicit: str | None = None) -> str:
    """Resolve schematic path from explicit arg or active project."""
    from footfindr.project import resolve_schematic_path
    return resolve_schematic_path(explicit)


def _get_project_name() -> str:
    """Get active project name or fallback."""
    from footfindr.project import ProjectManager
    pm = ProjectManager()
    active = pm.get_active()
    if active:
        return active.name
    return "(no project)"


# ---------------------------------------------------------------------------
# ff project review
# ---------------------------------------------------------------------------

def register_review_commands(proj_app: typer.Typer, bom_app: typer.Typer) -> None:
    """Register all M9 commands on the project and BOM apps."""

    # -------------------------------------------------------------------
    # ff project review / ff proj review
    # -------------------------------------------------------------------

    @proj_app.command("review")
    def project_review_cmd(
        profile: str = typer.Option("prototype", "--profile", "-p",
                                    help="Assembly profile."),
        qty: int = typer.Option(1, "--qty", "-n", help="Build quantity."),
        as_json: bool = typer.Option(False, "--json", "-j",
                                     help="Output as JSON."),
        fix_plan: bool = typer.Option(False, "--fix-plan",
                                      help="Generate fix plan."),
    ) -> None:
        """Review project buildability, BOM health, and sourcing risks."""
        from footfindr.review import ProjectReviewer

        try:
            sch_path = _resolve_schematic_for_review()
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        reviewer = ProjectReviewer(
            sch_path, profile=profile, qty=qty,
            project_name=_get_project_name(),
        )
        result = reviewer.review()

        if as_json:
            console.print_json(json.dumps(result.to_dict(), indent=2))
            return

        # Rich output
        console.print(f"\n[bold cyan]Project Review: "
                      f"{result.project_name}[/bold cyan]")
        console.print(f"  Profile: {result.profile}  |  "
                      f"Qty: {result.qty}  |  "
                      f"Schematic: [dim]{result.schematic_path}[/dim]")
        console.print()

        # Summary table
        summary = Table(title="Summary", show_lines=False,
                        title_style="bold")
        summary.add_column("Metric", style="bold")
        summary.add_column("Value", justify="right")
        summary.add_row("Refs scanned", str(result.ref_count))
        summary.add_row("BOM lines", str(result.bom_line_count))
        summary.add_row("Resolved", str(result.resolved_count))
        summary.add_row(
            "Missing MPN",
            f"[red]{result.missing_mpn_count}[/red]"
            if result.missing_mpn_count else "0",
        )
        summary.add_row(
            "Constraint failures",
            f"[red]{result.constraint_failure_count}[/red]"
            if result.constraint_failure_count else "0",
        )
        summary.add_row(
            "Supplier risks (HIGH+)",
            f"[yellow]{result.supplier_risk_count}[/yellow]"
            if result.supplier_risk_count else "0",
        )
        if result.total_bom_cost is not None:
            summary.add_row("Est. BOM cost",
                            f"${result.total_bom_cost:.2f}")
            summary.add_row("Priced / Unpriced",
                            f"{result.priced_lines} / "
                            f"{result.unpriced_lines}")
        console.print(summary)
        console.print()

        # Top issues
        top = [i for i in result.issues
               if i.severity in ("BLOCKER", "FAIL")]
        if top:
            console.print("[bold red]Top Issues:[/bold red]")
            for issue in top[:15]:
                console.print(f"  [red]{issue.severity}[/red] "
                              f"[bold]{issue.code}[/bold] "
                              f"{issue.ref or ''}: {issue.message}")
                if issue.suggested_action:
                    console.print(f"    [dim]→ {issue.suggested_action}[/dim]")
            if len(top) > 15:
                console.print(f"  ... and {len(top) - 15} more")
            console.print()

        # Warnings (compact)
        warns = [i for i in result.issues if i.severity == "WARN"]
        if warns:
            console.print(f"[yellow]Warnings: {len(warns)}[/yellow]")
            for w in warns[:10]:
                console.print(f"  [yellow]WARN[/yellow] {w.code} "
                              f"{w.ref or ''}: {w.message}")
            if len(warns) > 10:
                console.print(f"  ... and {len(warns) - 10} more")
            console.print()

        # Supplier risk summary
        risky = [s for s in result.source_checks
                 if s.risk_level != "LOW"]
        if risky:
            console.print(f"[bold]Supplier Risks: "
                          f"{len(risky)} part(s)[/bold]")
            risk_table = Table(show_lines=False)
            risk_table.add_column("Ref", style="bold")
            risk_table.add_column("MPN")
            risk_table.add_column("Risk")
            risk_table.add_column("Best")
            risk_table.add_column("Stock", justify="right")
            risk_table.add_column("Notes")
            for s in risky[:15]:
                risk_style = {
                    "BLOCKER": "bold red",
                    "HIGH": "red",
                    "MEDIUM": "yellow",
                }.get(s.risk_level, "")
                risk_table.add_row(
                    s.ref,
                    s.mpn[:25],
                    f"[{risk_style}]{s.risk_level}[/{risk_style}]",
                    s.best_supplier or "-",
                    f"{s.best_stock:,}" if s.best_stock else "0",
                    ", ".join(s.risk_codes[:3]),
                )
            console.print(risk_table)
            console.print()

        # Recommended actions
        if result.recommended_actions:
            console.print("[bold]Recommended Actions:[/bold]")
            for i, action in enumerate(result.recommended_actions, 1):
                console.print(f"  {i}. {action}")
            console.print()

        # Fix-plan generation
        if fix_plan:
            plan = reviewer.generate_fix_plan()
            if plan:
                console.print(f"[green]Plan generated: "
                              f"{plan.plan_id}[/green]")
                console.print(f"  Steps: {len(plan.steps)}")
                console.print("  View: ff plan show latest")
                console.print("  Apply: ff plan apply latest")
            else:
                console.print("[dim]No actionable fixes to plan.[/dim]")

    # -------------------------------------------------------------------
    # ff project packet / ff proj packet
    # -------------------------------------------------------------------

    @proj_app.command("packet")
    def project_packet_cmd(
        out: str = typer.Option("review.md", "--out", "-o",
                                help="Output file path."),
        profile: str = typer.Option("prototype", "--profile", "-p",
                                    help="Assembly profile."),
        qty: int = typer.Option(1, "--qty", "-n", help="Build quantity."),
    ) -> None:
        """Generate a markdown review packet."""
        from footfindr.review import ProjectReviewer

        try:
            sch_path = _resolve_schematic_for_review()
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        reviewer = ProjectReviewer(
            sch_path, profile=profile, qty=qty,
            project_name=_get_project_name(),
        )
        out_path = reviewer.generate_packet(
            out_path=out, profile=profile, qty=qty)
        console.print(f"[green]Review packet written to: {out_path}[/green]")

    # -------------------------------------------------------------------
    # ff bom check
    # -------------------------------------------------------------------

    @bom_app.command("check")
    def bom_check_cmd(
        constraints: bool = typer.Option(False, "--constraints", "-c",
                                         help="Include constraint checks."),
        profile: str = typer.Option("prototype", "--profile", "-p",
                                    help="Assembly profile."),
        as_json: bool = typer.Option(False, "--json", "-j",
                                     help="Output as JSON."),
        fix_plan: bool = typer.Option(False, "--fix-plan",
                                      help="Generate fix plan."),
    ) -> None:
        """Check BOM for missing fields, constraint failures, and profile risks."""
        from footfindr.review import ProjectReviewer

        try:
            sch_path = _resolve_schematic_for_review()
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        reviewer = ProjectReviewer(
            sch_path, profile=profile,
            project_name=_get_project_name(),
        )
        issues = reviewer.bom_check(
            check_constraints=constraints, profile_name=profile)

        if as_json:
            console.print_json(json.dumps(
                {"issues": [i.to_dict() for i in issues]}, indent=2))
            return

        if not issues:
            console.print("[green]BOM check passed. No issues found.[/green]")
            return

        # Group by severity
        sev_counts = {}
        for i in issues:
            sev_counts[i.severity] = sev_counts.get(i.severity, 0) + 1

        console.print(f"\n[bold cyan]BOM Check — Profile: {profile}[/bold cyan]")
        console.print(f"  Issues found: {len(issues)}")
        for sev in ("BLOCKER", "FAIL", "WARN", "INFO"):
            if sev in sev_counts:
                style = {"BLOCKER": "bold red", "FAIL": "red",
                         "WARN": "yellow", "INFO": "dim"}.get(sev, "")
                console.print(f"  [{style}]{sev}: {sev_counts[sev]}[/{style}]")
        console.print()

        table = Table(show_lines=False)
        table.add_column("Sev", width=7)
        table.add_column("Ref", style="bold", width=8)
        table.add_column("Code", width=25)
        table.add_column("Message")
        table.add_column("Action", style="dim")

        for issue in issues[:40]:
            sev_style = {"BLOCKER": "bold red", "FAIL": "red",
                         "WARN": "yellow", "INFO": "dim"}.get(issue.severity, "")
            table.add_row(
                f"[{sev_style}]{issue.severity}[/{sev_style}]",
                issue.ref or "",
                issue.code,
                issue.message[:60],
                (issue.suggested_action or "")[:40],
            )

        console.print(table)
        if len(issues) > 40:
            console.print(f"\n  [dim]... and {len(issues) - 40} more. "
                          f"Use --json for full output.[/dim]")

        if fix_plan:
            reviewer_for_plan = ProjectReviewer(
                sch_path, profile=profile,
                project_name=_get_project_name(),
            )
            plan = reviewer_for_plan.generate_fix_plan()
            if plan:
                console.print(f"\n[green]Plan generated: "
                              f"{plan.plan_id}[/green]")
                console.print("  View: ff plan show latest")

    # -------------------------------------------------------------------
    # ff bom source-check
    # -------------------------------------------------------------------

    @bom_app.command("source-check")
    def bom_source_check_cmd(
        suppliers_str: Optional[str] = typer.Option(
            None, "--suppliers", "-S", "--sups",
            help="Comma-separated supplier list."),
        supplier: Optional[str] = typer.Option(
            None, "--supplier", "-s", help="Single supplier."),
        qty: int = typer.Option(1, "--qty", "-n", help="Build quantity."),
        refresh: bool = typer.Option(False, "--refresh", "-r",
                                     help="Query live supplier APIs."),
        cache_only: bool = typer.Option(True, "--cache-only", "--cache",
                                        help="Only use cached data (default)."),
        as_json: bool = typer.Option(False, "--json", "-j",
                                     help="Output as JSON."),
    ) -> None:
        """Check BOM parts against supplier cache for stock/price/risk."""
        from footfindr.review import ProjectReviewer
        from footfindr.suppliers.registry import SupplierRegistry

        try:
            sch_path = _resolve_schematic_for_review()
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        # Determine suppliers
        sup_list = None
        if suppliers_str:
            reg = SupplierRegistry()
            sup_list = [reg.normalize_name(s.strip())
                        for s in suppliers_str.split(",")]
        elif supplier:
            reg = SupplierRegistry()
            sup_list = [reg.normalize_name(supplier)]

        # If -r is passed, override cache_only
        actual_cache_only = cache_only and not refresh

        if actual_cache_only and not refresh:
            console.print("[dim]Using supplier cache only. "
                          "Pass -r / --refresh to query live supplier "
                          "APIs.[/dim]")

        reviewer = ProjectReviewer(
            sch_path, qty=qty, project_name=_get_project_name(),
        )
        results = reviewer.source_check(
            suppliers=sup_list, refresh=refresh,
            cache_only=actual_cache_only, qty=qty,
        )

        if as_json:
            console.print_json(json.dumps(
                {"source_checks": [r.to_dict() for r in results]},
                indent=2))
            return

        console.print(f"\n[bold cyan]Source Check — Qty: {qty}[/bold cyan]")

        # Summary counts
        by_risk = {}
        for r in results:
            by_risk[r.risk_level] = by_risk.get(r.risk_level, 0) + 1
        for level in ("BLOCKER", "HIGH", "MEDIUM", "LOW"):
            if level in by_risk:
                style = {"BLOCKER": "bold red", "HIGH": "red",
                         "MEDIUM": "yellow", "LOW": "green"}.get(level, "")
                console.print(f"  [{style}]{level}: "
                              f"{by_risk[level]}[/{style}]")
        console.print()

        table = Table(show_lines=False)
        table.add_column("Ref", style="bold")
        table.add_column("MPN")
        table.add_column("Risk")
        table.add_column("Best Source")
        table.add_column("Stock", justify="right")
        table.add_column("Price@Qty", justify="right")
        table.add_column("Sources", justify="right")
        table.add_column("Notes")

        for r in results:
            risk_style = {"BLOCKER": "bold red", "HIGH": "red",
                          "MEDIUM": "yellow", "LOW": "green"}.get(
                r.risk_level, "")
            price_str = (f"${r.best_price:.4f}"
                         if r.best_price is not None else "-")
            stock_str = f"{r.best_stock:,}" if r.best_stock else "0"
            notes = ", ".join(r.risk_codes[:3])
            if r.has_jlc:
                notes += " JLC✓" if notes else "JLC✓"

            table.add_row(
                r.ref,
                r.mpn[:25] if r.mpn else "(no MPN)",
                f"[{risk_style}]{r.risk_level}[/{risk_style}]",
                r.best_supplier or "-",
                stock_str,
                price_str,
                str(r.supplier_count),
                notes,
            )

        console.print(table)

    # -------------------------------------------------------------------
    # ff bom cost
    # -------------------------------------------------------------------

    @bom_app.command("cost")
    def bom_cost_cmd(
        qty: int = typer.Option(1, "--qty", "-n", help="Build quantity."),
        suppliers_str: Optional[str] = typer.Option(
            None, "--suppliers", "-S", "--sups",
            help="Comma-separated supplier list."),
        profile: str = typer.Option("posm", "--profile", "-p",
                                    help="BOM profile."),
        as_json: bool = typer.Option(False, "--json", "-j",
                                     help="Output as JSON."),
    ) -> None:
        """Estimate BOM cost at a given build quantity."""
        from footfindr.review import ProjectReviewer
        from footfindr.suppliers.registry import SupplierRegistry

        try:
            sch_path = _resolve_schematic_for_review()
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        sup_list = None
        if suppliers_str:
            reg = SupplierRegistry()
            sup_list = [reg.normalize_name(s.strip())
                        for s in suppliers_str.split(",")]

        reviewer = ProjectReviewer(
            sch_path, qty=qty, project_name=_get_project_name(),
        )
        cost_lines, total = reviewer.cost_rollup(
            suppliers=sup_list, qty=qty)

        if as_json:
            console.print_json(json.dumps({
                "qty": qty,
                "cost_lines": [c.to_dict() for c in cost_lines],
                "total_bom_cost": total,
                "priced_lines": sum(1 for c in cost_lines if c.priced),
                "unpriced_lines": sum(1 for c in cost_lines if not c.priced),
            }, indent=2))
            return

        priced = [c for c in cost_lines if c.priced]
        unpriced = [c for c in cost_lines if not c.priced]

        console.print(f"\n[bold cyan]BOM Cost Estimate — "
                      f"Qty: {qty} boards[/bold cyan]\n")

        if priced:
            table = Table(show_lines=False, title="Priced Lines")
            table.add_column("Ref", style="bold")
            table.add_column("MPN")
            table.add_column("Qty/Board", justify="right")
            table.add_column("Required", justify="right")
            table.add_column("Unit $", justify="right")
            table.add_column("Extended $", justify="right")
            table.add_column("Source")
            table.add_column("MOQ", justify="center")

            for c in priced:
                moq_str = "⚠" if c.moq_warning else ""
                table.add_row(
                    c.ref[:20], c.mpn[:20],
                    str(c.qty_per_board), str(c.required_qty),
                    f"${c.unit_price:.4f}" if c.unit_price else "-",
                    f"${c.extended_price:.2f}" if c.extended_price else "-",
                    c.supplier or "-",
                    moq_str,
                )
            console.print(table)

        console.print(f"\n  Priced lines: {len(priced)}/{len(cost_lines)}")
        console.print(f"  Unpriced lines: {len(unpriced)}")
        if total is not None:
            unit_cost = total / qty if qty > 0 else total
            console.print(f"  [bold]Estimated unit BOM cost: "
                          f"${unit_cost:.2f}[/bold]")
            console.print(f"  [bold]Estimated total build BOM: "
                          f"${total:.2f}[/bold]")
        else:
            console.print("  [yellow]No pricing data available. "
                          "Run: ff bom source-check -S dk,mou,jlc -r[/yellow]")

    # -------------------------------------------------------------------
    # ff profile list / ff profile show
    # -------------------------------------------------------------------

    profile_app = typer.Typer(help="Assembly profile management.")

    @profile_app.command("list")
    def profile_list_cmd(
        as_json: bool = typer.Option(False, "--json", "-j",
                                     help="Output as JSON."),
    ) -> None:
        """List available assembly profiles."""
        from footfindr.assembly_profiles import list_profiles

        profiles = list_profiles()

        if as_json:
            console.print_json(json.dumps(
                {"profiles": [p.to_dict() for p in profiles]}, indent=2))
            return

        table = Table(title="Assembly Profiles", show_lines=False,
                      title_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("Description")
        table.add_column("Pkg Rules", justify="right")
        table.add_column("Checks", justify="right")

        for p in profiles:
            table.add_row(
                p.name, p.description,
                str(len(p.package_rules)),
                str(len(p.checks)),
            )
        console.print(table)

    @profile_app.command("show")
    def profile_show_cmd(
        name: str = typer.Argument(..., help="Profile name."),
        as_json: bool = typer.Option(False, "--json", "-j",
                                     help="Output as JSON."),
    ) -> None:
        """Show details of an assembly profile."""
        from footfindr.assembly_profiles import get_profile

        try:
            profile = get_profile(name)
        except KeyError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        if as_json:
            console.print_json(json.dumps(profile.to_dict(), indent=2))
            return

        console.print(f"\n[bold cyan]Assembly Profile: "
                      f"{profile.name}[/bold cyan]")
        console.print(f"  {profile.description}\n")

        if profile.package_rules:
            console.print("[bold]Package Rules:[/bold]")
            pkg_table = Table(show_lines=False)
            pkg_table.add_column("Pattern")
            pkg_table.add_column("Severity")
            pkg_table.add_column("Reason")
            for rule in profile.package_rules:
                sev_style = {"FAIL": "red", "WARN": "yellow",
                             "INFO": "dim"}.get(rule.severity, "")
                pkg_table.add_row(
                    rule.pattern,
                    f"[{sev_style}]{rule.severity}[/{sev_style}]",
                    rule.reason,
                )
            console.print(pkg_table)
            console.print()

        if profile.checks:
            console.print("[bold]Enabled Checks:[/bold]")
            for check in profile.checks:
                console.print(f"  • {check}")

    # -------------------------------------------------------------------
    # Register on main apps
    # -------------------------------------------------------------------

    # Profile app as top-level
    from footfindr.cli import app as main_app
    main_app.add_typer(profile_app, name="profile")
