"""Ref management CLI commands (M9.2).

Commands for inspecting, checking, and assigning parts to schematic references.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.table import Table

console = Console()
logger = logging.getLogger("footfindr.cli.ref")


def register_ref_commands(ref_app: typer.Typer) -> None:
    """Register all ref subcommands on the given Typer app."""

    # ---- ff ref show <ref> ----
    @ref_app.command("show")
    def ref_show_cmd(
        ref_name: str = typer.Argument(..., help="Schematic reference (e.g. C1, U3)."),
        as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    ) -> None:
        """Show schematic fields and constraints for a reference."""
        from footfindr.constraints import ConstraintManager, _op_to_prefix, infer_category

        # Read schematic
        sym = _load_symbol(ref_name)

        cmgr = ConstraintManager()
        constraints = cmgr.get_constraints_for(ref_name)
        cat, cat_conf = infer_category(ref_name)

        if as_json:
            data = _build_ref_info(ref_name, sym, constraints, cat, cat_conf)
            console.print(json.dumps(data, indent=2, default=str))
            return

        # Rich output
        console.print(f"\n[bold cyan]Ref: {ref_name}[/bold cyan]")
        console.print(f"  [dim]Category: {cat} (confidence: {cat_conf})[/dim]")

        if sym:
            console.print(f"\n[bold]Schematic Fields:[/bold]")
            _print_field("Value", sym.value)
            _print_field("Footprint", sym.footprint)
            _print_field("Lib ID", sym.lib_id)
            for fname, fval in sorted(sym.fields.items()):
                if fname not in ("Reference", "Value", "Footprint"):
                    _print_field(fname, fval)
        else:
            console.print(f"\n  [yellow]Not found in active schematic[/yellow]")

        if constraints:
            console.print(f"\n[bold]Constraints:[/bold]")
            for c in constraints:
                console.print(f"  {c.field} {_op_to_prefix(c.op)}{c.value}")
        else:
            console.print(f"\n  [dim]No constraints defined[/dim]")

        console.print()

    # ---- ff ref check <ref> ----
    @ref_app.command("check")
    def ref_check_cmd(
        ref_name: str = typer.Argument(..., help="Schematic reference (e.g. C1, U3)."),
        as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    ) -> None:
        """Check schematic fields, constraints, library status, and supplier cache for a ref."""
        from footfindr.constraints import (
            ConstraintManager, _op_to_prefix, infer_category,
            check_part_constraints, Constraint,
        )

        sym = _load_symbol(ref_name)
        cmgr = ConstraintManager()
        constraints = cmgr.get_constraints_for(ref_name)
        cat, cat_conf = infer_category(ref_name)

        # Library status
        lib_status = _check_library_status(sym)

        # Constraint check against schematic fields
        constraint_results = []
        if sym and constraints:
            for c in constraints:
                actual = _get_field_for_constraint(sym, c.field)
                if actual is not None:
                    from footfindr.constraints import check_constraint
                    result = check_constraint(c, actual)
                    constraint_results.append({
                        "field": c.field,
                        "expected": f"{_op_to_prefix(c.op)}{c.value}",
                        "actual": actual,
                        "passed": result.passed,
                        "message": result.message,
                    })
                else:
                    constraint_results.append({
                        "field": c.field,
                        "expected": f"{_op_to_prefix(c.op)}{c.value}",
                        "actual": None,
                        "passed": None,
                        "message": "No schematic data for this field",
                    })

        if as_json:
            data = _build_ref_info(ref_name, sym, constraints, cat, cat_conf)
            data["library_status"] = lib_status
            data["constraint_results"] = constraint_results
            console.print(json.dumps(data, indent=2, default=str))
            return

        # Rich output
        console.print(f"\n[bold cyan]Ref Check: {ref_name}[/bold cyan]")
        console.print(f"  [dim]Category: {cat} ({cat_conf})[/dim]")

        if sym:
            console.print(f"\n[bold]Schematic:[/bold]")
            _print_field("Value", sym.value)
            _print_field("Footprint", sym.footprint)
            mpn = sym.fields.get("MPN", sym.fields.get("mpn"))
            mfr = sym.fields.get("Manufacturer", sym.fields.get("manufacturer"))
            ipn = sym.fields.get("InternalPN", sym.fields.get("internal_pn"))
            _print_field("Manufacturer", mfr)
            _print_field("MPN", mpn)
            _print_field("InternalPN", ipn)
        else:
            console.print(f"\n  [yellow]Not found in active schematic[/yellow]")

        if lib_status:
            console.print(f"\n[bold]Library Status:[/bold]")
            for k, v in lib_status.items():
                console.print(f"  {k}: {v}")

        if constraint_results:
            console.print(f"\n[bold]Constraint Check:[/bold]")
            for cr in constraint_results:
                if cr["passed"] is True:
                    status = "[green]PASS[/green]"
                elif cr["passed"] is False:
                    status = "[red]FAIL[/red]"
                else:
                    status = "[dim]UNKNOWN[/dim]"
                actual = rich_escape(str(cr["actual"] or "—"))
                console.print(f"  {cr['field']}: expected {cr['expected']}, actual {actual}  {status}")
        elif constraints:
            console.print(f"\n  [dim]Cannot check constraints — no schematic data[/dim]")

        console.print()

    # ---- ff ref assign <ref> <source> [index] ----
    @ref_app.command("assign")
    def ref_assign_cmd(
        ref_name: str = typer.Argument(..., help="Schematic reference (e.g. C1, U3)."),
        source: str = typer.Argument(..., help="'.' for active session, or approved library IPN."),
        index: Optional[int] = typer.Argument(None, help="Result index (1-based) if using active session."),
        as_ipn: Optional[str] = typer.Option(None, "--as", help="Internal part number for the library entry."),
        to_library: Optional[str] = typer.Option(None, "--to", help="Target library (e.g. POSM)."),
        plan: bool = typer.Option(False, "--plan", "-p", help="Generate plan without applying."),
        apply: bool = typer.Option(False, "--apply", "-a", help="Generate and apply plan."),
        force: bool = typer.Option(False, "--force", "-f", help="Apply even if constraints fail or collisions exist."),
    ) -> None:
        """Assign a supplier part or approved library entry to a schematic reference."""
        from footfindr.constraints import ConstraintManager, _op_to_prefix
        from footfindr.plans import Plan, PlanStep, PlanManager, PlanError, check_collisions
        from footfindr.kicad.safe_write import snapshot_schematic

        if not plan and not apply:
            console.print("[red]Specify --plan or --apply[/red]")
            raise typer.Exit(1)

        cmgr = ConstraintManager()
        plan_mgr = PlanManager()

        # Determine mode: supplier session vs library assignment
        if source == ".":
            # Supplier session mode — resolve part from active session
            if index is None:
                console.print("[red]Specify result index when using active session (e.g. '. 1')[/red]")
                raise typer.Exit(1)

            part = _resolve_session_part(index)
            if part is None:
                raise typer.Exit(1)

            if not as_ipn:
                console.print("[red]Specify --as <IPN> for the internal part number[/red]")
                raise typer.Exit(1)

            console.print(f"\n[bold cyan]Assigning to {ref_name}:[/bold cyan]")
            console.print(f"  MPN: {rich_escape(part.mpn)}")
            console.print(f"  IPN: {rich_escape(as_ipn)}")
            if to_library:
                console.print(f"  Library: {rich_escape(to_library)}")

            # Constraint check
            constraints = cmgr.get_constraints_for(ref_name)
            if constraints:
                from footfindr.constraints import check_part_constraints
                results = check_part_constraints(constraints, part)
                hard_failures = [r for r in results if not r.passed and not r.is_soft]
                soft_failures = [r for r in results if not r.passed and r.is_soft]

                console.print(f"\n[bold]Constraint check:[/bold]")
                for r in results:
                    status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
                    if r.is_soft and not r.passed:
                        status = "[yellow]WARN[/yellow]"
                    console.print(f"  {r.constraint.field} {_op_to_prefix(r.constraint.op)}{r.constraint.value}: {status} (actual: {rich_escape(str(r.actual_value or '?'))})")

                if hard_failures and not force:
                    console.print(f"\n[red]{len(hard_failures)} hard constraint(s) failed. Use --force to override.[/red]")
                    raise typer.Exit(1)

            # Collision check
            collision_warnings = []
            if to_library:
                try:
                    from footfindr.libraries.manager import LibraryManager
                    lib_mgr = LibraryManager()
                    collision_warnings = check_collisions(
                        mpn=part.mpn,
                        internal_pn=as_ipn,
                        supplier_pn=getattr(part, "supplier_pn", None),
                        target_library=to_library,
                        manager=lib_mgr,
                    )
                    if collision_warnings:
                        console.print(f"\n[bold]Collision warnings:[/bold]")
                        for cw in collision_warnings:
                            console.print(f"  [yellow]{cw.message}[/yellow]")
                        if not force:
                            console.print(f"\n[yellow]Collisions detected. Use --force to override.[/yellow]")
                            raise typer.Exit(1)
                except ImportError:
                    pass  # Library not configured

            # Build plan steps
            steps = []

            # Step 1: Promote to library (if target library specified)
            if to_library:
                promote_data = {
                    "mpn": part.mpn,
                    "manufacturer": getattr(part, "manufacturer", ""),
                    "supplier": getattr(part, "supplier", ""),
                    "supplier_pn": getattr(part, "supplier_pn", ""),
                    "description": getattr(part, "description", ""),
                    "package": getattr(part, "package", ""),
                    "datasheet": getattr(part, "product_url", ""),
                }
                steps.append(PlanStep(
                    operation="promote",
                    target_file=f"library:{to_library}",
                    target_key=as_ipn,
                    new_value=promote_data,
                    reason=f"Promote {part.mpn} to {to_library} as {as_ipn}",
                ))

            # Step 2: Update schematic fields
            sch_path = _get_schematic_path()
            if sch_path:
                update_fields = {
                    "Manufacturer": getattr(part, "manufacturer", ""),
                    "MPN": part.mpn,
                    "InternalPN": as_ipn,
                    "Datasheet": getattr(part, "product_url", ""),
                    "FootFindrStatus": "assigned",
                    "FootFindrConfidence": "high",
                    "FootFindrSource": f"supplier:{getattr(part, 'supplier', '')}",
                    "FootFindrFootprintStatus": "REVIEW",
                }
                # LCSC Part #
                lcsc = getattr(part, "supplier_pn", "")
                if getattr(part, "supplier", "").lower() in ("jlcpcb", "lcsc"):
                    update_fields["LCSC Part #"] = lcsc

                steps.append(PlanStep(
                    operation="update_schematic",
                    target_file=str(sch_path),
                    target_key=ref_name,
                    new_value=update_fields,
                    reason=f"Assign {part.mpn} ({as_ipn}) to {ref_name}",
                ))

            _create_and_handle_plan(
                steps=steps,
                operation="ref-assign",
                plan_mgr=plan_mgr,
                sch_path=sch_path,
                provenance={
                    "ref": ref_name,
                    "mpn": part.mpn,
                    "internal_pn": as_ipn,
                    "target_library": to_library or "",
                    "supplier": getattr(part, "supplier", ""),
                },
                collision_warnings=[cw.message for cw in collision_warnings],
                do_apply=apply,
                force=force,
            )

        else:
            # Library assignment mode — look up existing approved part
            ipn = source
            console.print(f"\n[bold cyan]Assigning approved part to {ref_name}:[/bold cyan]")
            console.print(f"  IPN: {rich_escape(ipn)}")

            # Look up in library
            lib_part = _lookup_library_part(ipn)
            if lib_part is None:
                console.print(f"[red]Part '{ipn}' not found in approved library[/red]")
                raise typer.Exit(1)

            sch_path = _get_schematic_path()
            if not sch_path:
                console.print("[red]No active schematic found[/red]")
                raise typer.Exit(1)

            mpn = getattr(lib_part, "mpn", "") or ""
            mfr = getattr(lib_part, "manufacturer", "") or ""
            ds = getattr(lib_part, "datasheet", "") or ""

            update_fields = {
                "Manufacturer": mfr,
                "MPN": mpn,
                "InternalPN": ipn,
                "Datasheet": ds,
                "FootFindrStatus": "assigned",
                "FootFindrConfidence": "high",
                "FootFindrSource": f"library:{ipn}",
                "FootFindrFootprintStatus": "REVIEW",
            }

            steps = [PlanStep(
                operation="update_schematic",
                target_file=str(sch_path),
                target_key=ref_name,
                new_value=update_fields,
                reason=f"Assign approved part {ipn} to {ref_name}",
            )]

            _create_and_handle_plan(
                steps=steps,
                operation="ref-assign-library",
                plan_mgr=plan_mgr,
                sch_path=sch_path,
                provenance={
                    "ref": ref_name,
                    "mpn": mpn,
                    "internal_pn": ipn,
                    "source": "library",
                },
                collision_warnings=[],
                do_apply=apply,
                force=force,
            )

    # ---- Aliases ----
    ref_app.command("sh", hidden=True)(ref_show_cmd)
    ref_app.command("ch", hidden=True)(ref_check_cmd)
    ref_app.command("set", hidden=True)(ref_assign_cmd)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_field(name: str, value: str | None) -> None:
    """Print a single field with formatting."""
    if value:
        console.print(f"  {name}: {rich_escape(value)}")
    else:
        console.print(f"  {name}: [dim]—[/dim]")


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


def _get_schematic_path() -> Path | None:
    """Get the active schematic path, or None."""
    try:
        from footfindr.project import resolve_schematic_path
        return Path(resolve_schematic_path())
    except Exception:
        return None


def _get_field_for_constraint(sym, field_name: str) -> str | None:
    """Extract a schematic field value that corresponds to a constraint field."""
    if not sym:
        return None

    # Direct field lookup
    _FIELD_MAP = {
        "capacitance": ("Value", "Capacitance"),
        "resistance": ("Value", "Resistance"),
        "voltage": ("Voltage - Rated", "Voltage"),
        "dielectric": ("Temperature Coefficient", "Dielectric"),
        "package": ("Footprint",),
        "tolerance": ("Tolerance",),
        "value": ("Value",),
        "family": ("MPN", "Value"),
    }

    candidates = _FIELD_MAP.get(field_name, (field_name,))
    for field in candidates:
        if field == "Value" and sym.value:
            return sym.value
        if field == "Footprint" and sym.footprint:
            return sym.footprint
        if field in sym.fields and sym.fields[field]:
            return sym.fields[field]
    return None


def _check_library_status(sym) -> dict:
    """Check if the symbol's MPN/IPN is in an approved library."""
    if not sym:
        return {"status": "no_schematic_data"}

    mpn = sym.fields.get("MPN", sym.fields.get("mpn", ""))
    ipn = sym.fields.get("InternalPN", sym.fields.get("internal_pn", ""))

    if not mpn and not ipn:
        return {"status": "unassigned", "message": "No MPN or InternalPN in schematic"}

    result = {"mpn": mpn, "internal_pn": ipn}

    try:
        from footfindr.libraries.manager import LibraryManager
        lib_mgr = LibraryManager()
        if ipn:
            part = lib_mgr.lookup(ipn)
            if part:
                result["status"] = "approved"
                result["library"] = getattr(part, "library_name", "unknown")
                return result
        result["status"] = "not_in_library"
    except Exception:
        result["status"] = "library_unavailable"

    return result


def _build_ref_info(ref_name, sym, constraints, cat, cat_conf) -> dict:
    """Build ref info dict for JSON output."""
    from footfindr.constraints import _op_to_prefix

    data = {
        "ref": ref_name,
        "category": cat,
        "category_confidence": cat_conf,
    }

    if sym:
        data["schematic"] = {
            "value": sym.value,
            "footprint": sym.footprint,
            "lib_id": sym.lib_id,
            "fields": dict(sym.fields),
        }
    else:
        data["schematic"] = None

    data["constraints"] = [
        {"field": c.field, "op": c.op, "value": c.value, "display": f"{_op_to_prefix(c.op)}{c.value}"}
        for c in constraints
    ]

    return data


def _resolve_session_part(index: int):
    """Resolve a supplier part from the active session by 1-based index."""
    try:
        from footfindr.config import get_workspace
        from footfindr.suppliers.session import SessionManager
        mgr = SessionManager(workspace=get_workspace())
        session = mgr.require_session()
        results = session.active_results()
        if 1 <= index <= len(results):
            return results[index - 1]
        console.print(f"[red]Index {index} out of range (1-{len(results)})[/red]")
        return None
    except Exception as e:
        console.print(f"[red]Cannot load active session: {e}[/red]")
        return None


def _lookup_library_part(ipn: str):
    """Look up a part by InternalPN in approved libraries."""
    try:
        from footfindr.libraries.manager import LibraryManager
        mgr = LibraryManager()
        return mgr.lookup(ipn)
    except Exception:
        return None


def _create_and_handle_plan(
    *,
    steps: list,
    operation: str,
    plan_mgr,
    sch_path: Path | None,
    provenance: dict,
    collision_warnings: list[str],
    do_apply: bool,
    force: bool,
) -> None:
    """Create a plan, optionally apply it."""
    from footfindr.plans import Plan, PlanManager

    # Take schematic snapshot for safe-write
    snapshot_data = None
    if sch_path and sch_path.exists():
        from footfindr.kicad.safe_write import snapshot_schematic
        snapshot = snapshot_schematic(sch_path)
        snapshot_data = snapshot.to_dict()
        provenance["schematic_snapshot"] = snapshot_data

    plan_id = PlanManager.generate_plan_id(operation)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    plan = Plan(
        plan_id=plan_id,
        operation=operation,
        created_at=now,
        steps=steps,
        collision_warnings=collision_warnings,
        provenance=provenance,
    )
    plan_mgr.create(plan)

    console.print(f"\n[green]Plan created: {plan_id}[/green]")
    console.print(f"  Steps: {len(steps)}")
    for s in steps:
        console.print(f"    {s.operation}: {s.target_key}")
        if s.reason:
            console.print(f"      {s.reason}")

    if do_apply:
        console.print(f"\n[bold]Applying plan...[/bold]")
        try:
            plan_mgr.apply(plan)
            console.print(f"[green]Plan applied successfully.[/green]")
        except Exception as e:
            console.print(f"[red]Apply failed: {e}[/red]")
            raise typer.Exit(1)
    else:
        console.print(f"\n[dim]Plan saved. Review with: ff plan sh latest[/dim]")
        console.print(f"[dim]Apply with: ff plan ap latest[/dim]")
