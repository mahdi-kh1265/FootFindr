"""Constraint CLI commands.

Registered on the main app as ``ff constraint`` / ``ff constraints``.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

console = Console()

constraint_app = typer.Typer(help="Project-local part constraints.")


@constraint_app.command("set")
def constraint_set(
    ref: str = typer.Argument(..., help="Schematic reference (e.g. C13, U3)."),
    field_name: str = typer.Argument(..., help="Constraint field (voltage, dielectric, package, ...)."),
    value: str = typer.Argument(..., help="Constraint value (>=25V, X7R, 0805, ...)."),
    reason: str | None = typer.Option(None, "--reason", "-r", help="Human-readable reason."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be set without saving."),
) -> None:
    """Set a constraint on a schematic reference."""
    from footfindr.constraints import Constraint, ConstraintManager

    mgr = ConstraintManager()
    c = Constraint.from_field_value(field_name, value, reason=reason)

    if dry_run:
        console.print(f"[dim]Would set:[/dim] {ref}.{c.field} {c.op} {c.value}")
        return

    mgr.set_constraint(ref, field_name, value, reason=reason)
    console.print(f"[green]Set constraint:[/green] {ref}.{c.field} {c.op} {c.value}")


@constraint_app.command("show")
def constraint_show(
    ref: str = typer.Argument(..., help="Schematic reference to show constraints for."),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Show constraints for a specific reference."""
    from footfindr.constraints import ConstraintManager

    mgr = ConstraintManager()
    constraints = mgr.get_constraints_for(ref)

    if not constraints:
        console.print(f"[yellow]No constraints set for {ref}[/yellow]")
        return

    if as_json:
        data = {
            "ref": ref,
            "constraints": [c.to_dict() for c in constraints],
        }
        console.print(json.dumps(data, indent=2, default=str))
        return

    table = Table(title=f"Constraints for {ref}", show_lines=False, title_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Op", style="dim")
    table.add_column("Value", style="green")
    table.add_column("Reason", style="dim")

    for c in constraints:
        table.add_row(c.field, c.op, c.value, c.reason or "")

    console.print(table)


@constraint_app.command("list")
def constraint_list(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """List all constraints."""
    from footfindr.constraints import ConstraintManager

    mgr = ConstraintManager()
    cf = mgr.load()

    if not cf.refs and not cf.groups and not cf.patterns:
        console.print("[yellow]No constraints defined.[/yellow]")
        console.print("[dim]Use: ff constraint set <ref> <field> <value>[/dim]")
        return

    if as_json:
        data = {
            "version": cf.version,
            "refs": {r: rc.to_dict() for r, rc in cf.refs.items()},
            "groups": {g: gc.to_dict() for g, gc in cf.groups.items()},
            "patterns": {p: pc.to_dict() for p, pc in cf.patterns.items()},
        }
        console.print(json.dumps(data, indent=2, default=str))
        return

    if cf.refs:
        table = Table(title="Ref Constraints", show_lines=False, title_style="bold cyan")
        table.add_column("Ref", style="bold")
        table.add_column("Field")
        table.add_column("Op", style="dim")
        table.add_column("Value", style="green")
        table.add_column("Reason", style="dim")

        for ref, rc in cf.refs.items():
            for i, c in enumerate(rc.constraints):
                table.add_row(
                    ref if i == 0 else "",
                    c.field, c.op, c.value,
                    c.reason or (rc.reason if i == 0 else ""),
                )
        console.print(table)

    if cf.groups:
        console.print()
        table = Table(title="Group Constraints", show_lines=False, title_style="bold cyan")
        table.add_column("Group", style="bold")
        table.add_column("Refs", style="dim")
        table.add_column("Field")
        table.add_column("Op", style="dim")
        table.add_column("Value", style="green")

        for gname, gc in cf.groups.items():
            refs_str = ", ".join(gc.refs)
            for i, c in enumerate(gc.constraints):
                table.add_row(
                    gname if i == 0 else "",
                    refs_str if i == 0 else "",
                    c.field, c.op, c.value,
                )
        console.print(table)

    if cf.patterns:
        console.print()
        table = Table(title="Pattern Constraints", show_lines=False, title_style="bold cyan")
        table.add_column("Pattern", style="bold")
        table.add_column("Field")
        table.add_column("Op", style="dim")
        table.add_column("Value", style="green")

        for pat, pc in cf.patterns.items():
            for i, c in enumerate(pc.constraints):
                table.add_row(
                    pat if i == 0 else "",
                    c.field, c.op, c.value,
                )
        console.print(table)


@constraint_app.command("remove")
def constraint_remove(
    ref: str = typer.Argument(..., help="Schematic reference."),
    field_name: str = typer.Argument(..., help="Field to remove constraint for."),
) -> None:
    """Remove a specific constraint from a reference."""
    from footfindr.constraints import ConstraintManager

    mgr = ConstraintManager()
    if mgr.remove_constraint(ref, field_name):
        console.print(f"[green]Removed constraint:[/green] {ref}.{field_name}")
    else:
        console.print(f"[yellow]No constraint '{field_name}' found for {ref}[/yellow]")


@constraint_app.command("clear")
def constraint_clear(
    ref: str = typer.Argument(..., help="Schematic reference to clear all constraints for."),
) -> None:
    """Clear all constraints for a reference."""
    from footfindr.constraints import ConstraintManager

    mgr = ConstraintManager()
    if mgr.clear_ref(ref):
        console.print(f"[green]Cleared all constraints for {ref}[/green]")
    else:
        console.print(f"[yellow]No constraints found for {ref}[/yellow]")


@constraint_app.command("check")
def constraint_check(
    ref: str = typer.Argument(..., help="Schematic reference or 'all'."),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Check if current selection satisfies constraints for a ref."""
    from footfindr.constraints import ConstraintManager, check_part_constraints
    from footfindr.suppliers.session import SessionManager

    mgr = ConstraintManager()

    if ref.lower() == "all":
        _check_all(mgr, as_json)
        return

    constraints = mgr.get_constraints_for(ref)
    if not constraints:
        console.print(f"[yellow]No constraints defined for {ref}[/yellow]")
        return

    # Try to get selected part from active session
    try:
        sess_mgr = SessionManager()
        session = sess_mgr.require_session()
        selected = session.get_selected()
        if not selected:
            console.print("[yellow]No part selected in active session.[/yellow]")
            console.print("[dim]Use: ff supplier choose . <index>[/dim]")
            return
    except Exception:
        console.print("[yellow]No active supplier session.[/yellow]")
        return

    results = check_part_constraints(constraints, selected)

    if as_json:
        data = {
            "ref": ref,
            "part_mpn": selected.mpn,
            "results": [
                {
                    "field": r.constraint.field,
                    "op": r.constraint.op,
                    "expected": r.constraint.value,
                    "actual": r.actual_value,
                    "passed": r.passed,
                    "is_soft": r.is_soft,
                    "message": r.message,
                }
                for r in results
            ],
            "all_hard_passed": all(r.passed for r in results if not r.is_soft),
        }
        console.print(json.dumps(data, indent=2, default=str))
        return

    console.print(f"\n[bold cyan]{ref} constraint check[/bold cyan] for [bold]{selected.mpn}[/bold]:")
    for r in results:
        icon = "✓" if r.passed else ("⚠" if r.is_soft else "✗")
        style = "green" if r.passed else ("yellow" if r.is_soft else "red")
        console.print(f"  [{style}]{icon}[/{style}] {r.message}")

    hard_pass = all(r.passed for r in results if not r.is_soft)
    if hard_pass:
        console.print(f"\n[green]All hard constraints PASS[/green]")
    else:
        console.print(f"\n[red]Some constraints FAIL[/red]")


def _check_all(mgr, as_json: bool) -> None:
    """Check all refs with constraints."""
    cf = mgr.load()
    if not cf.refs:
        console.print("[yellow]No ref constraints defined.[/yellow]")
        return

    console.print("[bold cyan]Checking all refs...[/bold cyan]")
    console.print("[dim]Note: checks require parts selected in an active session.[/dim]\n")

    for ref in cf.refs:
        constraints = mgr.get_constraints_for(ref)
        console.print(f"  [bold]{ref}[/bold]: {len(constraints)} constraint(s)")


@constraint_app.command("apply")
def constraint_apply(
    ref: str = typer.Argument(..., help="Schematic reference."),
    target: str = typer.Argument(".", help="Dot for active session."),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Apply constraints for a ref to narrow active supplier results."""
    from footfindr.constraints import ConstraintManager, apply_constraints_to_results
    from footfindr.suppliers.session import SessionManager

    mgr = ConstraintManager()
    constraints = mgr.get_constraints_for(ref)

    if not constraints:
        console.print(f"[yellow]No constraints defined for {ref}[/yellow]")
        return

    sess_mgr = SessionManager()
    try:
        session = sess_mgr.require_session()
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    active = session.get_active_results()
    passing, summaries = apply_constraints_to_results(constraints, active)

    # Update session
    session.active_result_ids = [p.result_id for p in passing]
    sess_mgr.save(session)

    if as_json:
        data = {
            "ref": ref,
            "constraints": [c.to_dict() for c in constraints],
            "original_count": len(active),
            "passing_count": len(passing),
            "filtered_out": len(active) - len(passing),
        }
        console.print(json.dumps(data, indent=2, default=str))
        return

    console.print(f"\n[bold cyan]Applied {ref} constraints:[/bold cyan]")
    for c in constraints:
        from footfindr.constraints import _op_to_prefix
        prefix = _op_to_prefix(c.op)
        console.print(f"  {c.field} {prefix}{c.value}")
    console.print(f"\n  [green]{len(passing)}[/green] / {len(active)} results pass constraints")
    if len(passing) < len(active):
        console.print(f"  [dim]{len(active) - len(passing)} results filtered out[/dim]")


# ---------------------------------------------------------------------------
# Group sub-commands
# ---------------------------------------------------------------------------

group_app = typer.Typer(help="Manage constraint groups.")
constraint_app.add_typer(group_app, name="group")
constraint_app.add_typer(group_app, name="grp", hidden=True)


@group_app.command("create")
def group_create(
    name: str = typer.Argument(..., help="Group name."),
    reason: str | None = typer.Option(None, "--reason", "-r"),
) -> None:
    """Create a constraint group."""
    from footfindr.constraints import ConstraintManager

    mgr = ConstraintManager()
    mgr.create_group(name, reason=reason)
    console.print(f"[green]Created group:[/green] {name}")


# Register 'new' as alias for 'create'
group_app.command("new", hidden=True)(group_create)


@group_app.command("add")
def group_add(
    name: str = typer.Argument(..., help="Group name."),
    refs: list[str] = typer.Argument(..., help="Refs to add to group."),
) -> None:
    """Add refs to a constraint group."""
    from footfindr.constraints import ConstraintManager

    mgr = ConstraintManager()
    mgr.add_to_group(name, refs)
    console.print(f"[green]Added {len(refs)} ref(s) to {name}[/green]")


@group_app.command("set")
def group_set(
    name: str = typer.Argument(..., help="Group name."),
    field_name: str = typer.Argument(..., help="Constraint field."),
    value: str = typer.Argument(..., help="Constraint value."),
) -> None:
    """Set a constraint on a group."""
    from footfindr.constraints import Constraint, ConstraintManager

    mgr = ConstraintManager()
    mgr.set_group_constraint(name, field_name, value)
    c = Constraint.from_field_value(field_name, value)
    console.print(f"[green]Set group constraint:[/green] {name}.{c.field} {c.op} {c.value}")


@group_app.command("show")
def group_show(
    name: str = typer.Argument(..., help="Group name."),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
) -> None:
    """Show a constraint group."""
    from footfindr.constraints import ConstraintManager

    mgr = ConstraintManager()
    cf = mgr.load()

    if name not in cf.groups:
        console.print(f"[yellow]Group '{name}' not found[/yellow]")
        return

    gc = cf.groups[name]

    if as_json:
        console.print(json.dumps(gc.to_dict(), indent=2, default=str))
        return

    console.print(f"\n[bold cyan]Group: {name}[/bold cyan]")
    console.print(f"  Refs: {', '.join(gc.refs) or '(none)'}")
    if gc.reason:
        console.print(f"  Reason: {gc.reason}")
    if gc.constraints:
        console.print("  Constraints:")
        for c in gc.constraints:
            from footfindr.constraints import _op_to_prefix
            console.print(f"    {c.field} {_op_to_prefix(c.op)}{c.value}")


def register_constraint_commands(main_app: typer.Typer) -> None:
    """Register constraint commands on the main app."""
    main_app.add_typer(constraint_app, name="constraint")
    main_app.add_typer(constraint_app, name="constraints", hidden=True)
    main_app.add_typer(constraint_app, name="con", hidden=True)
    main_app.add_typer(constraint_app, name="cons", hidden=True)
