"""Plan CLI commands.

Registered on the main app as ``ff plan``.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

console = Console()

plan_app = typer.Typer(help="Plan/apply model for safe mutations.")


@plan_app.command("list")
def plan_list(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """List all plans."""
    from footfindr.plans import PlanManager

    mgr = PlanManager()
    plans = mgr.list_plans()

    if not plans:
        console.print("[yellow]No plans found.[/yellow]")
        return

    if as_json:
        data = [p.to_dict() for p in plans]
        console.print(json.dumps(data, indent=2, default=str))
        return

    table = Table(title="Plans", show_lines=False, title_style="bold cyan")
    table.add_column("ID", style="bold")
    table.add_column("Operation")
    table.add_column("Status")
    table.add_column("Created")
    table.add_column("Steps")

    for p in plans:
        status_style = {
            "pending": "yellow",
            "applied": "green",
            "discarded": "dim",
        }.get(p.status, "white")
        table.add_row(
            p.plan_id, p.operation,
            f"[{status_style}]{p.status}[/{status_style}]",
            p.created_at[:19] if p.created_at else "",
            str(len(p.steps)),
        )

    console.print(table)


@plan_app.command("show")
def plan_show(
    plan_id: str = typer.Argument("latest", help="Plan ID or 'latest'."),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Show plan details."""
    from footfindr.plans import PlanManager

    mgr = PlanManager()

    if plan_id == "latest":
        plan = mgr.load_latest()
    else:
        plan = mgr.load(plan_id)

    if not plan:
        console.print("[yellow]No plan found.[/yellow]")
        return

    if as_json:
        console.print(json.dumps(plan.to_dict(), indent=2, default=str))
        return

    status_style = {
        "pending": "yellow",
        "applied": "green",
        "discarded": "dim",
    }.get(plan.status, "white")

    console.print(f"\n[bold cyan]Plan: {plan.plan_id}[/bold cyan]")
    console.print(f"  Operation: {plan.operation}")
    console.print(f"  Status: [{status_style}]{plan.status}[/{status_style}]")
    console.print(f"  Created: {plan.created_at}")

    if plan.provenance:
        console.print("\n  [bold]Provenance:[/bold]")
        for k, v in plan.provenance.items():
            if isinstance(v, dict):
                console.print(f"    {k}:")
                for sk, sv in v.items():
                    console.print(f"      {sk}: {sv}")
            else:
                console.print(f"    {k}: {v}")

    if plan.steps:
        console.print(f"\n  [bold]Steps ({len(plan.steps)}):[/bold]")
        for i, step in enumerate(plan.steps, 1):
            console.print(f"    {i}. {step.operation}: {step.target_key}")
            console.print(f"       Target file: {step.target_file}")
            if step.reason:
                console.print(f"       Reason: {step.reason}")
            if step.warnings:
                for w in step.warnings:
                    console.print(f"       [yellow]⚠ {w}[/yellow]")

    if plan.constraint_check:
        console.print("\n  [bold]Constraint check:[/bold]")
        for k, v in plan.constraint_check.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        icon = "✓" if item.get("passed") else ("⚠" if item.get("is_soft") else "✗")
                        style = "green" if item.get("passed") else ("yellow" if item.get("is_soft") else "red")
                        console.print(f"    [{style}]{icon}[/{style}] {item.get('message', '')}")
            else:
                console.print(f"    {k}: {v}")

    if plan.collision_warnings:
        console.print("\n  [bold yellow]Collision warnings:[/bold yellow]")
        for w in plan.collision_warnings:
            console.print(f"    [yellow]⚠ {w}[/yellow]")

    if plan.status == "pending":
        console.print(f"\n  [dim]To apply:  ff plan apply {plan.plan_id}[/dim]")
        console.print(f"  [dim]To discard: ff plan discard {plan.plan_id}[/dim]")


@plan_app.command("apply")
def plan_apply(
    plan_id: str = typer.Argument("latest", help="Plan ID or 'latest'."),
    force: bool = typer.Option(False, "--force", "-f", help="Apply even if constraints fail."),
) -> None:
    """Apply a pending plan."""
    from footfindr.plans import PlanError, PlanManager

    mgr = PlanManager()

    if plan_id == "latest":
        plan = mgr.load_latest()
    else:
        plan = mgr.load(plan_id)

    if not plan:
        console.print("[yellow]No plan found.[/yellow]")
        raise typer.Exit(1)

    if plan.status != "pending":
        console.print(f"[yellow]Plan {plan.plan_id} is already {plan.status}[/yellow]")
        raise typer.Exit(1)

    # Check for constraint failures
    if plan.constraint_check and not force:
        results = plan.constraint_check.get("results", [])
        hard_failures = [r for r in results if not r.get("passed") and not r.get("is_soft")]
        if hard_failures:
            console.print("[red]Constraint check has failures:[/red]")
            for r in hard_failures:
                console.print(f"  [red]✗ {r.get('message', '')}[/red]")
            console.print("\n[yellow]Use --force to apply anyway.[/yellow]")
            raise typer.Exit(1)

    # Check for collisions
    if plan.collision_warnings and not force:
        console.print("[yellow]Collision warnings:[/yellow]")
        for w in plan.collision_warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")
        console.print("\n[yellow]Use --force to apply anyway.[/yellow]")
        raise typer.Exit(1)

    try:
        mgr.apply(plan)
        console.print(f"[green]Plan {plan.plan_id} applied successfully![/green]")
    except PlanError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@plan_app.command("discard")
def plan_discard(
    plan_id: str = typer.Argument("latest", help="Plan ID or 'latest'."),
) -> None:
    """Discard a pending plan."""
    from footfindr.plans import PlanError, PlanManager

    mgr = PlanManager()

    if plan_id == "latest":
        plan = mgr.load_latest()
    else:
        plan = mgr.load(plan_id)

    if not plan:
        console.print("[yellow]No plan found.[/yellow]")
        raise typer.Exit(1)

    if plan.status != "pending":
        console.print(f"[yellow]Plan {plan.plan_id} is already {plan.status}[/yellow]")
        raise typer.Exit(1)

    try:
        mgr.discard(plan)
        console.print(f"[green]Plan {plan.plan_id} discarded.[/green]")
    except PlanError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


def register_plan_commands(main_app: typer.Typer) -> None:
    """Register plan commands on the main app."""
    main_app.add_typer(plan_app, name="plan")


# ---------------------------------------------------------------------------
# Command aliases (M8.7)
# ---------------------------------------------------------------------------
plan_app.command("sh", hidden=True)(plan_show)
plan_app.command("ls", hidden=True)(plan_list)
plan_app.command("ap", hidden=True)(plan_apply)
plan_app.command("drop", hidden=True)(plan_discard)
