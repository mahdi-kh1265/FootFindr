"""Rich terminal output for FootFindr.

Renders resolve decisions, part search results, library trees, and
component details as colourful Rich tables and panels.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from footfindr.core.models import Decision, DecisionStatus, PartRecord

console = Console()

# ---------------------------------------------------------------------------
# Status colour mapping
# ---------------------------------------------------------------------------

_STATUS_STYLE: dict[str, str] = {
    DecisionStatus.AUTO: "bold green",
    DecisionStatus.REVIEW: "bold yellow",
    DecisionStatus.SKIP: "dim",
    DecisionStatus.ERROR: "bold red",
    DecisionStatus.UNCHANGED: "dim cyan",
}


# ---------------------------------------------------------------------------
# Resolve summary table
# ---------------------------------------------------------------------------

def print_resolve_summary(decisions: list[Decision], *, applied: bool = False) -> None:
    """Print a Rich table summarising resolver decisions."""
    table = Table(
        title="FootFindr Resolve Results",
        show_lines=True,
        title_style="bold magenta",
    )
    table.add_column("Ref", style="bold")
    table.add_column("Value")
    table.add_column("Category")
    table.add_column("Old Footprint")
    table.add_column("New Footprint")
    table.add_column("Internal PN")
    table.add_column("Status")
    table.add_column("Conf.")
    if applied:
        table.add_column("Applied")
    table.add_column("Reason")

    for d in sorted(decisions, key=lambda x: x.ref):
        style = _STATUS_STYLE.get(d.status, "")
        status_text = Text(d.status.value, style=style)

        old_fp = d.old_fields.get("Footprint", "--")
        new_fp = d.selected_footprint or "--"
        reason = "; ".join(d.reasons[:2]) if d.reasons else "--"
        if len(reason) > 60:
            reason = reason[:57] + "..."

        row = [
            d.ref,
            d.component_value or "--",
            d.component_category or "--",
            old_fp,
            new_fp,
            d.selected_internal_pn or "--",
            status_text,
            f"{d.confidence:.2f}",
        ]
        if applied:
            row.append("[green]yes[/green]" if d.applied else "--")
        row.append(reason)

        table.add_row(*[str(r) if not isinstance(r, Text) else r for r in row])

    console.print(table)

    # Summary line
    counts: dict[str, int] = {}
    for d in decisions:
        counts[d.status.value] = counts.get(d.status.value, 0) + 1
    parts = []
    for status, count in sorted(counts.items()):
        style = _STATUS_STYLE.get(status, "")
        parts.append(f"[{style}]{status}: {count}[/{style}]")
    console.print(f"\n  Total: {len(decisions)}  |  " + "  |  ".join(parts))

    if applied:
        applied_count = sum(1 for d in decisions if d.applied)
        console.print(f"  [bold green]Applied: {applied_count}[/bold green]")


# ---------------------------------------------------------------------------
# Scan summary
# ---------------------------------------------------------------------------

def print_scan_summary(symbols: list) -> None:
    """Print a Rich table of scanned schematic symbols."""
    table = Table(
        title="Schematic Scan",
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("Ref", style="bold")
    table.add_column("Value")
    table.add_column("Category")
    table.add_column("Footprint")
    table.add_column("Lib ID")
    table.add_column("Fields")

    for sym in sorted(symbols, key=lambda s: s.ref):
        extra_fields = {k: v for k, v in sym.fields.items()
                        if k not in ("Reference", "Value", "Footprint", "Datasheet")}
        fields_str = ", ".join(f"{k}={v}" for k, v in extra_fields.items()) if extra_fields else "--"
        if len(fields_str) > 40:
            fields_str = fields_str[:37] + "..."

        table.add_row(
            sym.ref,
            sym.value or "--",
            sym.category or "--",
            sym.footprint or "--",
            sym.lib_id or "--",
            fields_str,
        )

    console.print(table)
    console.print(f"\n  Total symbols: {len(symbols)}")


# ---------------------------------------------------------------------------
# Part display
# ---------------------------------------------------------------------------

def print_part_detail(part: PartRecord) -> None:
    """Print detailed information about a single part."""
    lines = [
        f"[bold]Internal PN:[/bold] {part.internal_pn}",
        f"[bold]Category:[/bold] {part.category.value}",
        f"[bold]Status:[/bold] {part.status.value}",
        f"[bold]Approved:[/bold] {'Yes' if part.approved else 'No'}",
    ]
    if part.manufacturer:
        lines.append(f"[bold]Manufacturer:[/bold] {part.manufacturer}")
    if part.mpn:
        lines.append(f"[bold]MPN:[/bold] {part.mpn}")
    if part.value:
        lines.append(f"[bold]Value:[/bold] {part.value}")
    if part.package:
        lines.append(f"[bold]Package:[/bold] {part.package}")
    if part.footprint:
        lines.append(f"[bold]Footprint:[/bold] {part.footprint}")
    if part.specs.voltage_rating:
        lines.append(f"[bold]Voltage Rating:[/bold] {part.specs.voltage_rating}")
    if part.specs.power_rating:
        lines.append(f"[bold]Power Rating:[/bold] {part.specs.power_rating}")
    if part.specs.tolerance:
        lines.append(f"[bold]Tolerance:[/bold] {part.specs.tolerance}")
    if part.specs.dielectric:
        lines.append(f"[bold]Dielectric:[/bold] {part.specs.dielectric}")
    if part.specs.capacitance:
        lines.append(f"[bold]Capacitance:[/bold] {part.specs.capacitance}")
    if part.specs.resistance:
        lines.append(f"[bold]Resistance:[/bold] {part.specs.resistance}")
    if part.notes:
        lines.append(f"[bold]Notes:[/bold] {part.notes}")

    # Provenance section
    prov_lines = []
    if part.source_vendor:
        prov_lines.append(f"[bold]Source Vendor:[/bold] {part.source_vendor}")
    if part.source_series:
        prov_lines.append(f"[bold]Source Series:[/bold] {part.source_series}")
    if part.source_pack:
        prov_lines.append(f"[bold]Source Pack:[/bold] {part.source_pack}")
    if part.source_library:
        prov_lines.append(f"[bold]Source Library:[/bold] {part.source_library}")
    if part.source_file:
        prov_lines.append(f"[bold]Source File:[/bold] {part.source_file}")
    if part.source_row is not None:
        prov_lines.append(f"[bold]Source Row:[/bold] {part.source_row}")
    if part.promoted_from:
        prov_lines.append(f"[bold]Promoted From:[/bold] {part.promoted_from}")
    if part.promoted_at:
        prov_lines.append(f"[bold]Promoted At:[/bold] {part.promoted_at}")

    if prov_lines:
        lines.append("")
        lines.append("[bold underline]Provenance[/bold underline]")
        lines.extend(prov_lines)

    console.print(Panel("\n".join(lines), title=part.internal_pn, border_style="cyan"))


def print_part_search_results(parts: list[PartRecord]) -> None:
    """Print a table of part search results."""
    if not parts:
        console.print("[yellow]No parts found.[/yellow]")
        return

    table = Table(title="Part Search Results", show_lines=True, title_style="bold cyan")
    table.add_column("Internal PN", style="bold")
    table.add_column("Category")
    table.add_column("Value")
    table.add_column("Package")
    table.add_column("Footprint")
    table.add_column("MPN")
    table.add_column("Status")

    for p in parts:
        table.add_row(
            p.internal_pn,
            p.category.value,
            p.value or "--",
            p.package or "--",
            p.footprint or "--",
            p.mpn or "--",
            p.status.value,
        )

    console.print(table)
    console.print(f"\n  Found: {len(parts)} parts")


# ---------------------------------------------------------------------------
# Library tree
# ---------------------------------------------------------------------------

def print_library_tree(tree: dict) -> None:
    """Print a library hierarchy tree."""
    if not tree:
        console.print("[yellow]No libraries registered.[/yellow]")
        return

    for name, info in tree.items():
        active = " [bold green](active)[/bold green]" if info.get("active") else ""
        console.print(f"  [bold]{name}[/bold] [{info['kind']}]{active}")
        for child_name, child_info in info.get("children", {}).items():
            c_active = " [bold green](active)[/bold green]" if child_info.get("active") else ""
            console.print(f"    +-- [bold]{child_name}[/bold] [{child_info['kind']}]{c_active}")


# ---------------------------------------------------------------------------
# Component explanation (ff why / ff explain)  -- Phase C
# ---------------------------------------------------------------------------

def print_component_explanation(decision: Decision) -> None:
    """Print how FootFindr sees a component and what it decided.

    Renders a comprehensive Rich panel/table showing all resolver
    reasoning, constraints, candidates, and the final decision.
    """
    style = _STATUS_STYLE.get(decision.status, "")

    # --- Section 1: Component Identity ---
    lines = [
        "[bold underline]Component Identity[/bold underline]",
        f"  [bold]Reference:[/bold]        {decision.ref}",
        f"  [bold]Value:[/bold]            {decision.component_value or '--'}",
        f"  [bold]Category:[/bold]         {decision.component_category or '--'}",
    ]

    # Old footprint
    old_fp = decision.old_fields.get("Footprint", "")
    if old_fp:
        lines.append(f"  [bold]Old Footprint:[/bold]    {old_fp}")
    else:
        lines.append("  [bold]Old Footprint:[/bold]    (empty)")

    # --- Section 2: Existing Fields ---
    other_fields = {k: v for k, v in decision.old_fields.items() if k != "Footprint"}
    if other_fields:
        lines.append("")
        lines.append("[bold underline]Existing Fields[/bold underline]")
        for k, v in sorted(other_fields.items()):
            lines.append(f"  {k} = {v}")

    # --- Section 3: Requirements / Constraints ---
    if decision.requirements:
        lines.append("")
        lines.append("[bold underline]Requirements / Constraints[/bold underline]")
        for req in decision.requirements:
            lines.append(f"  {req.field_name} = {req.value}  (from {req.source})")

    # --- Section 4: Resolver Decision ---
    lines.append("")
    lines.append("[bold underline]Resolver Decision[/bold underline]")
    lines.append(f"  [bold]Status:[/bold]          [{style}]{decision.status.value}[/{style}]")
    lines.append(f"  [bold]Confidence:[/bold]      {decision.confidence:.2f}")

    if decision.selected_internal_pn:
        lines.append(f"  [bold]Selected Part:[/bold]   {decision.selected_internal_pn}")
    if decision.selected_mpn:
        lines.append(f"  [bold]Selected MPN:[/bold]    {decision.selected_mpn}")
    if decision.selected_footprint:
        lines.append(f"  [bold]New Footprint:[/bold]   {decision.selected_footprint}")
    if decision.source_library:
        lines.append(f"  [bold]Source Library:[/bold]  {decision.source_library}")

    # Would it be applied?
    would_apply = (
        decision.status == DecisionStatus.AUTO
        and decision.confidence >= 0.92
        and bool(decision.fields_to_write)
    )
    lines.append(
        f"  [bold]Would Apply:[/bold]    "
        f"{'[green]YES[/green]' if would_apply else '[dim]NO[/dim]'}"
        f"  (requires --apply flag)"
    )

    # --- Section 5: Reasons ---
    if decision.reasons:
        lines.append("")
        lines.append("[bold underline]Reasons[/bold underline]")
        for i, r in enumerate(decision.reasons, 1):
            lines.append(f"  {i}. {r}")

    # --- Section 6: Warnings ---
    if decision.warnings:
        lines.append("")
        lines.append("[bold yellow underline]Warnings[/bold yellow underline]")
        for w in decision.warnings:
            lines.append(f"  [yellow]! {w}[/yellow]")

    # --- Section 7: Errors ---
    if decision.errors:
        lines.append("")
        lines.append("[bold red underline]Errors[/bold red underline]")
        for e in decision.errors:
            lines.append(f"  [red]X {e}[/red]")

    # --- Section 8: Candidate Summary ---
    if decision.candidate_summary:
        lines.append("")
        lines.append("[bold underline]Candidate Parts[/bold underline]")
        for c in decision.candidate_summary:
            parts_desc = []
            if c.get("internal_pn"):
                parts_desc.append(c["internal_pn"])
            if c.get("package"):
                parts_desc.append(f"pkg={c['package']}")
            if c.get("voltage_rating"):
                parts_desc.append(f"V={c['voltage_rating']}")
            if c.get("dielectric"):
                parts_desc.append(f"die={c['dielectric']}")
            if c.get("power_rating"):
                parts_desc.append(f"P={c['power_rating']}")
            if c.get("footprint"):
                parts_desc.append(c["footprint"])
            lines.append(f"  - {', '.join(parts_desc)}")

    # --- Section 9: Fields to Write ---
    if decision.fields_to_write:
        lines.append("")
        lines.append("[bold underline]Fields to Write[/bold underline]")
        for k, v in sorted(decision.fields_to_write.items()):
            old_val = decision.old_fields.get(k, "(new)")
            if old_val == v:
                lines.append(f"  {k} = {v}  [dim](unchanged)[/dim]")
            else:
                lines.append(f"  {k} = {v}  [dim](was: {old_val})[/dim]")

    title = f"FootFindr Explanation: {decision.ref}"
    console.print(Panel("\n".join(lines), title=title, border_style="cyan", padding=(1, 2)))
