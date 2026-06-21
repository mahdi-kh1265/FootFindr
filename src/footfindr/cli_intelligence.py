"""Intelligence CLI commands (M9.4).

Implements:
    ff net show <ref>           — show component net connections
    ff net neighbors <ref>      — show components sharing nets
    ff rails scan               — infer rail voltages from net names
    ff rails set <net> <voltage> — user-override a rail voltage
    ff rails alias <net> <target> — alias one net name to another
    ff cap classify <ref>       — classify capacitor role
    ff cap package-sweep <ref>  — sweep packages for a capacitor
    ff cap score <ref>          — score individual candidates
    ff suggest <ref>            — full suggestion pipeline

--justify / -j: decision evidence, equations, model terms, role
    probabilities, utility terms, score decomposition, rank stability

--debug: raw query strings, raw result counts, first raw MPNs,
    parsed fields, reject reasons, geometry details, union-find nodes

All commands are read-only.  No schematic writes.

Registered from cli.py via ``register_intelligence_commands(app)``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()
logger = logging.getLogger("footfindr.cli.intelligence")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_schematic_path(project: str | None = None) -> Path:
    """Resolve the active schematic path."""
    from footfindr.project import ProjectManager
    mgr = ProjectManager()
    if project:
        try:
            meta = mgr.status(project)
        except ValueError:
            console.print(f"[red]Project '{project}' not found.[/red]")
            raise typer.Exit(1)
    else:
        meta = mgr.get_active()
    if not meta:
        console.print("[red]No active project. Run: ff project use <name>[/red]")
        raise typer.Exit(1)
    sch_path = Path(meta.schematic)
    if not sch_path.exists():
        console.print(f"[red]Schematic not found: {sch_path}[/red]")
        raise typer.Exit(1)
    return sch_path


def _get_workspace() -> Path:
    """Get the workspace path."""
    from footfindr.config import get_workspace
    return get_workspace()


def _get_project_name() -> str | None:
    """Get active project name."""
    from footfindr.project import ProjectManager
    mgr = ProjectManager()
    return mgr.get_active_name()


def _print_justify_header(title: str) -> None:
    """Print a justify section header."""
    console.print(f"\n[bold cyan]{title}[/bold cyan]")
    console.print("[dim]" + "─" * 60 + "[/dim]")


def _print_debug_header(title: str) -> None:
    """Print a debug section header."""
    console.print(f"\n[bold magenta]DEBUG: {title}[/bold magenta]")
    console.print("[dim]" + "═" * 60 + "[/dim]")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_intelligence_commands(app: typer.Typer) -> None:
    """Register all M9.4 intelligence CLI commands."""

    # --- ff net ---
    net_app = typer.Typer(help="Net graph and connectivity commands.")
    app.add_typer(net_app, name="net")

    # --- ff rails ---
    rails_app = typer.Typer(help="Power rail inference commands.")
    app.add_typer(rails_app, name="rails")

    # --- ff cap ---
    cap_app = typer.Typer(help="Capacitor intelligence commands.")
    app.add_typer(cap_app, name="cap")

    # ========================================================================
    # ff net show <ref>
    # ========================================================================
    @net_app.command("show")
    def net_show(
        ref: str = typer.Argument(..., help="Reference designator (e.g. C1)."),
        project: Optional[str] = typer.Option(None, "--project", "-p"),
        justify: bool = typer.Option(False, "--justify", "-j", help="Show full evidence."),
        debug: bool = typer.Option(False, "--debug", help="Show geometry and union-find details."),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
    ) -> None:
        """Show net connections for a component."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        sch_path = _get_schematic_path(project)
        provider = get_connectivity_provider()
        graph = provider.build_net_graph(sch_path)
        connections = graph.get_connections(ref)
        completeness = graph.get_pin_completeness(ref)

        if as_json:
            data = {
                "ref": ref,
                "backend": provider.backend_name,
                "connections": [c.to_dict() for c in connections],
                "pin_completeness": completeness.to_dict(),
            }
            console.print(json.dumps(data, indent=2))
            return

        if not connections:
            console.print(f"[yellow]No net connections found for {ref}.[/yellow]")
            console.print(f"[dim]Backend: {provider.backend_name}[/dim]")
            console.print(
                f"[dim]Pin completeness: {completeness.resolved_pins}/{completeness.expected_pins} "
                f"({completeness.completeness:.0%})[/dim]"
            )
            return

        table = Table(
            title=f"Net Connections: {ref}",
            show_lines=True,
            title_style="bold cyan",
        )
        table.add_column("Pin", style="bold")
        table.add_column("Net")
        table.add_column("Type")

        for conn in connections:
            pin_str = conn.pin or "unknown"
            net_type = conn.net_type or "—"
            table.add_row(pin_str, conn.net, net_type)

        console.print(table)
        console.print(f"[dim]Backend: {provider.backend_name}[/dim]")
        console.print(
            f"[dim]Pin completeness: {completeness.resolved_pins}/{completeness.expected_pins} "
            f"({completeness.completeness:.0%})[/dim]"
        )

        if justify:
            _print_justify_header("Pin Completeness")
            console.print(f"  Expected pins: {completeness.expected_pins}")
            console.print(f"  Resolved pins: {completeness.resolved_pins}")
            console.print(f"  Completeness: {completeness.completeness:.2%}")
            if completeness.unresolved_pins:
                console.print(f"  Unresolved pins: {completeness.unresolved_pins}")

            _print_justify_header("Connection Evidence")
            for conn in connections:
                conf_str = "high" if conn.net_type in ("power", "ground") else "medium"
                console.print(
                    f"  pin {conn.pin or '?'} → {conn.net} "
                    f"[dim](type={conn.net_type or 'unknown'}, confidence={conf_str})[/dim]"
                )

        if debug:
            _print_debug_header(f"Geometry Details: {ref}")
            debug_info = graph.get_debug_info(ref)
            if debug_info:
                for line in debug_info:
                    console.print(f"  {line}")
            else:
                console.print("  [dim]No debug info available[/dim]")

    # ========================================================================
    # ff net neighbors <ref>
    # ========================================================================
    @net_app.command("neighbors")
    def net_neighbors(
        ref: str = typer.Argument(..., help="Reference designator (e.g. C1)."),
        project: Optional[str] = typer.Option(None, "--project", "-p"),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
    ) -> None:
        """Show components sharing nets with a given component."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        sch_path = _get_schematic_path(project)
        provider = get_connectivity_provider()
        graph = provider.build_net_graph(sch_path)
        neighbors = graph.get_neighbors(ref)

        if as_json:
            data = {
                "ref": ref,
                "neighbors": {
                    net: [c.to_dict() for c in conns]
                    for net, conns in neighbors.items()
                },
            }
            console.print(json.dumps(data, indent=2))
            return

        if not neighbors:
            console.print(f"[yellow]No neighbors found for {ref}.[/yellow]")
            return

        for net, conns in neighbors.items():
            table = Table(
                title=f"Net: {net}",
                show_lines=True,
                title_style="bold",
            )
            table.add_column("Ref", style="bold")
            table.add_column("Pin")
            table.add_column("Type")

            for conn in conns:
                table.add_row(
                    conn.ref,
                    conn.pin or "?",
                    conn.net_type or "—",
                )

            console.print(table)

    # ========================================================================
    # ff rails scan
    # ========================================================================
    @rails_app.command("scan")
    def rails_scan(
        project: Optional[str] = typer.Option(None, "--project", "-p"),
        justify: bool = typer.Option(False, "--justify", "-j", help="Show full evidence."),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
    ) -> None:
        """Infer power rail voltages from net names."""
        from footfindr.intelligence.net_graph import get_connectivity_provider
        from footfindr.intelligence.rail_scanner import scan_rails

        sch_path = _get_schematic_path(project)
        provider = get_connectivity_provider()
        graph = provider.build_net_graph(sch_path)
        workspace = _get_workspace()
        rails = scan_rails(graph, workspace=workspace)

        if as_json:
            data = [r.to_dict() for r in rails]
            console.print(json.dumps(data, indent=2))
            return

        if not rails:
            console.print("[yellow]No power rails detected.[/yellow]")
            return

        table = Table(
            title="Inferred Power Rails",
            show_lines=True,
            title_style="bold cyan",
        )
        table.add_column("Net", style="bold")
        table.add_column("Voltage")
        table.add_column("Confidence")
        table.add_column("Source")

        for rail in rails:
            v_str = f"{rail.voltage}V" if rail.voltage is not None else "unknown"
            conf_str = f"{rail.confidence:.0%}"
            table.add_row(rail.net, v_str, conf_str, rail.source)

        console.print(table)

        if justify:
            _print_justify_header("Evidence")
            for rail in rails:
                for ev in rail.evidence:
                    console.print(f"  {rail.net}: {ev}")

    # ========================================================================
    # ff rails set <net> <voltage>
    # ========================================================================
    @rails_app.command("set")
    def rails_set(
        net: str = typer.Argument(..., help="Net name (e.g. VCC)."),
        voltage: str = typer.Argument(..., help="Voltage value (e.g. 3.3V)."),
    ) -> None:
        """Set a user-defined voltage for a net."""
        from footfindr.core.units import parse_voltage
        from footfindr.intelligence.storage import RailOverrideStore

        v = parse_voltage(voltage)
        if v is None:
            console.print(f"[red]Cannot parse voltage: {voltage}[/red]")
            raise typer.Exit(1)

        store = RailOverrideStore(workspace=_get_workspace())
        store.set_voltage(net, v)
        console.print(f"[green]Set {net} = {v}V[/green]")

    # ========================================================================
    # ff rails alias <net> <target>
    # ========================================================================
    @rails_app.command("alias")
    def rails_alias(
        net: str = typer.Argument(..., help="Net name to alias (e.g. VDDA)."),
        target: str = typer.Argument(..., help="Target net name (e.g. +3V3)."),
    ) -> None:
        """Alias one net name to another for rail inference."""
        from footfindr.intelligence.storage import RailOverrideStore

        store = RailOverrideStore(workspace=_get_workspace())
        store.set_alias(net, target)
        console.print(f"[green]Alias: {net} → {target}[/green]")

    # ========================================================================
    # ff cap classify <ref>
    # ========================================================================
    @cap_app.command("classify")
    def cap_classify(
        ref: str = typer.Argument(..., help="Capacitor reference (e.g. C1)."),
        project: Optional[str] = typer.Option(None, "--project", "-p"),
        justify: bool = typer.Option(False, "--justify", "-j", help="Show full evidence."),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
    ) -> None:
        """Classify a capacitor's role based on net context."""
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.net_graph import get_connectivity_provider
        from footfindr.intelligence.rail_scanner import scan_rails
        from footfindr.kicad.schematic import KiCadSchematicReader

        sch_path = _get_schematic_path(project)
        reader = KiCadSchematicReader()
        sch = reader.read(str(sch_path))
        symbol = sch.symbol_by_ref(ref)
        if not symbol:
            console.print(f"[red]Component {ref} not found in schematic.[/red]")
            raise typer.Exit(1)

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(sch_path)
        connections = graph.get_connections(ref)
        completeness = graph.get_pin_completeness(ref)
        workspace = _get_workspace()
        rails = scan_rails(graph, workspace=workspace)

        classification = classify_capacitor(
            ref, symbol.value, connections, rails,
            pin_completeness=completeness.completeness,
        )

        if as_json:
            console.print(json.dumps(classification.to_dict(), indent=2))
            return

        conf_pct = f"{classification.confidence:.0%}"
        console.print(f"[bold]{ref}[/bold]: {classification.value_display}")
        console.print(f"  Role: [cyan]{classification.role}[/cyan] (confidence {conf_pct})")
        console.print(f"  Nets: {classification.nets_description}")
        console.print(f"  Pin completeness: {classification.pin_completeness:.0%}")
        console.print(f"  Parser confidence: {classification.parser_confidence:.0%}")

        if justify:
            _print_justify_header("Role Probabilities")
            sorted_probs = sorted(
                classification.role_probabilities.items(),
                key=lambda x: x[1], reverse=True,
            )
            for role_name, prob in sorted_probs:
                bar = "█" * int(prob * 40) + "░" * (40 - int(prob * 40))
                console.print(f"  {role_name:24s} {prob:.4f} {bar}")

            _print_justify_header("Feature Values")
            for fname, fval in classification.feature_values.items():
                console.print(f"  {fname}: {fval:.4f}")

            _print_justify_header("Model Info")
            console.print(f"  Model version: {classification.model_version}")
            console.print(f"  Pin completeness: {classification.pin_completeness:.4f}")
            console.print(f"  Parser confidence: {classification.parser_confidence:.4f}")

            _print_justify_header("Evidence")
            for ev in classification.evidence:
                console.print(f"  • {ev}")
            if classification.facts:
                _print_justify_header("Facts")
                for fact in classification.facts:
                    console.print(f"  {fact.key} = {fact.value} [{fact.source}]")

    # ========================================================================
    # ff cap package-sweep <ref>
    # ========================================================================
    @cap_app.command("package-sweep")
    def cap_package_sweep(
        ref: str = typer.Argument(..., help="Capacitor reference (e.g. C1)."),
        project: Optional[str] = typer.Option(None, "--project", "-p"),
        justify: bool = typer.Option(False, "--justify", "-j", help="Show full evidence."),
        debug: bool = typer.Option(False, "--debug", help="Show raw query strings, MPNs, reject reasons."),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
        refresh: bool = typer.Option(False, "--refresh", "-r", help="Force live supplier queries."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s"),
    ) -> None:
        """Sweep package sizes for a capacitor, evaluating supplier availability."""
        from footfindr.intelligence.net_graph import get_connectivity_provider
        from footfindr.intelligence.package_sweep import (
            package_sweep,
            compute_required_voltage,
        )
        from footfindr.intelligence.rail_scanner import scan_rails
        from footfindr.intelligence.scoring import compute_package_utility
        from footfindr.intelligence.bayesian_model import assess_feasibility
        from footfindr.kicad.schematic import KiCadSchematicReader
        from footfindr.core.units import parse_capacitance

        sch_path = _get_schematic_path(project)
        reader = KiCadSchematicReader()
        sch = reader.read(str(sch_path))
        symbol = sch.symbol_by_ref(ref)
        if not symbol:
            console.print(f"[red]Component {ref} not found in schematic.[/red]")
            raise typer.Exit(1)

        value_raw = symbol.value or ""
        if not value_raw:
            console.print(f"[red]No value found for {ref}.[/red]")
            raise typer.Exit(1)

        # Infer voltage context
        provider = get_connectivity_provider()
        graph = provider.build_net_graph(sch_path)
        workspace = _get_workspace()
        rails = scan_rails(graph, workspace=workspace)
        connections = graph.get_connections(ref)

        voltage_str = None
        for conn in connections:
            for rail in rails:
                if rail.net == conn.net and rail.voltage and rail.voltage > 0:
                    v_req = compute_required_voltage(rail.voltage)
                    voltage_str = f"{v_req}V"
                    break

        scores, data_source = package_sweep(
            value_raw,
            voltage=voltage_str,
            use_cache=True,
            use_live=refresh,
            supplier=supplier,
        )

        if as_json:
            data = {
                "ref": ref,
                "value": value_raw,
                "data_source": data_source,
                "packages": [s.to_dict() for s in scores],
            }
            console.print(json.dumps(data, indent=2))
            return

        console.print(f"[bold]{ref}[/bold]: {value_raw}")
        console.print(f"[dim]Data source: {data_source}[/dim]")
        if voltage_str:
            console.print(f"[dim]Required voltage: {voltage_str}[/dim]")

        if not scores:
            console.print("[yellow]No package scores computed (no supplier data).[/yellow]")
            if not refresh:
                console.print(f"[dim]Try: ff cap package-sweep {ref} --refresh[/dim]")
            return

        table = Table(
            title="Package Ranking",
            show_lines=True,
            title_style="bold cyan",
        )
        table.add_column("Package", style="bold")
        table.add_column("Score", justify="right")
        table.add_column("Viable", justify="right")
        table.add_column("In Stock", justify="right")
        table.add_column("Mfrs", justify="right")
        table.add_column("Med. Price", justify="right")
        table.add_column("Area mm²", justify="right")

        for s in scores:
            price_str = f"${s.median_price:.4f}" if s.median_price else "—"
            area_str = f"{s.area_mm2:.2f}" if s.area_mm2 else "—"
            table.add_row(
                s.package,
                f"{s.score:.3f}",
                str(s.viable_count),
                str(s.in_stock_count),
                str(s.manufacturer_count),
                price_str,
                area_str,
            )

        console.print(table)

        if justify:
            _print_justify_header("Package Evidence")
            for s in scores:
                for ev in s.evidence:
                    console.print(f"  {s.package}: {ev}")

            _print_justify_header("Bayesian Feasibility")
            for s in scores:
                ev = s.package_evidence
                if ev:
                    feas = assess_feasibility(
                        s.package, ev.parsed_count, ev.viable_count,
                    )
                    console.print(
                        f"  {s.package}: q_hat={feas.q_hat:.3f}  "
                        f"Wilson LCB={feas.q_lcb:.3f}  "
                        f"status={feas.status}  "
                        f"rankable={feas.is_rankable}"
                    )

            # Utility terms for top package
            if scores:
                cap_f = parse_capacitance(value_raw)
                from footfindr.core.units import parse_voltage
                v_v = parse_voltage(voltage_str) if voltage_str else None
                ctx = {
                    "target_capacitance_f": cap_f,
                    "required_voltage_v": v_v,
                    "role": "unknown",
                    "role_confidence": 0.5,
                    "pin_completeness": 1.0,
                }
                top = scores[0]
                utility, terms = compute_package_utility(top, ctx)
                _print_justify_header(f"Utility Terms: {top.package} (score={utility:.4f})")
                for term in terms:
                    console.print(
                        f"  {term.name:30s} value={term.value:.4f} "
                        f"× w={term.weight:.3f} = {term.contribution:.4f}"
                    )

        if debug:
            _print_debug_header("Raw Package Sweep Data")
            for s in scores:
                ev = s.package_evidence
                if ev:
                    console.print(f"\n  [bold]{s.package}[/bold]")
                    console.print(f"    Query strings: {ev.query_strings}")
                    console.print(f"    Raw result count: {ev.raw_count}")
                    console.print(f"    Parsed count: {ev.parsed_count}")
                    console.print(f"    Viable count: {ev.viable_count}")
                    console.print(f"    First raw MPNs: {ev.first_raw_mpns[:5]}")
                    console.print(f"    Reject reasons: {ev.reject_reasons}")
                    console.print(f"    Lifecycle dist: {ev.lifecycle_distribution}")
                    console.print(f"    Mfr entropy: {ev.manufacturer_entropy:.4f}")
                    console.print(f"    Attr completeness: {ev.attribute_completeness:.4f}")

    # ========================================================================
    # ff cap score <ref>
    # ========================================================================
    @cap_app.command("score")
    def cap_score(
        ref: str = typer.Argument(..., help="Capacitor reference (e.g. C1)."),
        project: Optional[str] = typer.Option(None, "--project", "-p"),
        justify: bool = typer.Option(False, "--justify", "-j", help="Show score decomposition."),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
        refresh: bool = typer.Option(False, "--refresh", "-r", help="Force live supplier queries."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s"),
    ) -> None:
        """Score individual MPN candidates for a capacitor."""
        from footfindr.intelligence.suggest import suggest_component

        sch_path = _get_schematic_path(project)
        workspace = _get_workspace()
        project_name = _get_project_name()

        record = suggest_component(
            ref, sch_path,
            use_cache=True,
            use_live=refresh,
            supplier=supplier,
            workspace=workspace,
            project_name=project_name,
        )

        if as_json:
            console.print(json.dumps({
                "ref": ref,
                "decision": record.decision,
                "decision_reason": record.decision_reason,
                "candidate_ranking": [c.to_dict() for c in record.candidate_ranking],
                "weights_used": record.weights_used,
            }, indent=2))
            return

        console.print(f"[bold]{ref}[/bold]: decision = {record.decision}")
        if record.decision_reason:
            console.print(f"  Reason: {record.decision_reason}")

        if not record.candidate_ranking:
            console.print(f"[yellow]No scored candidates for {ref}.[/yellow]")
            if not refresh:
                console.print(f"[dim]Try: ff cap score {ref} --refresh[/dim]")
            return

        table = Table(
            title=f"Candidate Ranking: {ref}",
            show_lines=True,
            title_style="bold cyan",
        )
        table.add_column("#", justify="right")
        table.add_column("MPN", style="bold")
        table.add_column("Mfr")
        table.add_column("Pkg")
        table.add_column("TOPSIS", justify="right")
        table.add_column("Final", justify="right")

        for i, c in enumerate(record.candidate_ranking[:20], 1):
            table.add_row(
                str(i),
                c.mpn,
                c.manufacturer,
                c.package,
                f"{c.topsis_score:.3f}",
                f"{c.final_score:.3f}",
            )

        console.print(table)

        if justify:
            _print_justify_header("Weights Used")
            console.print(f"  Policy: {record.scoring_policy_version}")
            for name, weight in record.weights_used.items():
                console.print(f"  {name}: {weight:.2f}")

            if record.candidate_ranking:
                top = record.candidate_ranking[0]
                _print_justify_header(f"Score Decomposition: {top.mpn}")
                for term in top.terms:
                    penalty_str = f" [yellow](missing data: -{term.missing_data_penalty:.2f})[/yellow]" if term.missing_data_penalty > 0 else ""
                    console.print(
                        f"  {term.name}: value={term.value:.3f} "
                        f"× weight={term.weight:.2f} "
                        f"= {term.contribution:.4f}{penalty_str}"
                    )
                console.print(f"\n  TOPSIS: {top.topsis_score:.4f}")
                console.print(f"  Uncertainty penalty: -{top.uncertainty_penalty:.4f}")
                console.print(f"  Risk penalty: -{top.risk_penalty:.4f}")
                console.print(f"  [bold]Final score: {top.final_score:.4f}[/bold]")

    # ========================================================================
    # ff suggest <ref>
    # ========================================================================
    @app.command("suggest")
    def suggest(
        ref: str = typer.Argument(..., help="Reference designator (e.g. C1)."),
        project: Optional[str] = typer.Option(None, "--project", "-p"),
        justify: bool = typer.Option(False, "--justify", "-j", help="Show full evidence and score decomposition."),
        debug: bool = typer.Option(False, "--debug", help="Show raw internal details."),
        as_json: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
        mpn: bool = typer.Option(False, "--mpn", help="Print only the stored top candidate MPN."),
        refresh: bool = typer.Option(False, "--refresh", "-r", help="Force live supplier queries."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s"),
    ) -> None:
        """Suggest a component (MPN, package) based on evidence.

        Default: uses cached/session data. Use --refresh for live queries.
        Use --mpn to print only the stored top MPN (no recompute).
        """
        from footfindr.intelligence.models import compute_context_hash
        from footfindr.intelligence.net_graph import get_connectivity_provider
        from footfindr.intelligence.rail_scanner import scan_rails
        from footfindr.intelligence.storage import SuggestionStore
        from footfindr.intelligence.suggest import suggest_component
        from footfindr.kicad.schematic import KiCadSchematicReader

        workspace = _get_workspace()
        project_name = _get_project_name()

        # --mpn mode: read stored suggestion only, no recompute
        if mpn:
            store = SuggestionStore(workspace=workspace)
            record = store.load(ref)
            if record is None:
                console.print(
                    f"No stored suggestion for {ref}.",
                    highlight=False,
                )
                console.print(f"Run: ff suggest {ref}", highlight=False)
                raise typer.Exit(1)

            # Check staleness
            sch_path = _get_schematic_path(project)
            reader = KiCadSchematicReader()
            sch = reader.read(str(sch_path))
            symbol = sch.symbol_by_ref(ref)
            if symbol:
                provider = get_connectivity_provider()
                graph = provider.build_net_graph(sch_path)
                connections = graph.get_connections(ref)
                rails = scan_rails(graph, workspace=workspace)
                current_hash = compute_context_hash(
                    ref, symbol.value, connections, rails,
                )
                if record.context_hash and current_hash != record.context_hash:
                    # Print to stderr so stdout stays clean for scripting
                    import sys
                    print(
                        f"Stored suggestion for {ref} may be stale.",
                        file=sys.stderr,
                    )
                    print(f"Run: ff suggest {ref}", file=sys.stderr)

            if record.top_candidate_mpn:
                # Clean stdout for scripting
                print(record.top_candidate_mpn)
            else:
                console.print(
                    f"No top candidate MPN in stored suggestion for {ref}.",
                    highlight=False,
                )
                console.print(f"Run: ff suggest {ref}", highlight=False)
                raise typer.Exit(1)
            return

        # Full suggest pipeline
        sch_path = _get_schematic_path(project)
        record = suggest_component(
            ref, sch_path,
            use_cache=True,
            use_live=refresh,
            supplier=supplier,
            workspace=workspace,
            project_name=project_name,
        )

        if as_json:
            console.print(json.dumps(record.to_dict(), indent=2, default=str))
            return

        # Compact default output
        console.print(f"\n[bold cyan]Suggestion: {ref}[/bold cyan]")
        console.print(f"  Decision: [bold]{record.decision}[/bold]")
        if record.decision_reason:
            console.print(f"  Reason: {record.decision_reason}")
        console.print(f"  Data source: {record.data_source}")
        console.print(f"  Pin completeness: {record.pin_completeness:.0%}")
        if record.role:
            console.print(f"  Role: {record.role} ({record.role_confidence:.0%})")
        if record.top_package:
            console.print(f"  Top package: {record.top_package}")
        if record.top_candidate_mpn:
            console.print(f"  Top MPN: [bold green]{record.top_candidate_mpn}[/bold green]")
        elif record.decision == "no_recommendation":
            console.print("  [yellow]No package recommendation.[/yellow]")
            console.print(f"  [yellow]Reason: {record.decision_reason}[/yellow]")
        else:
            console.print("  [yellow]No MPN candidates scored.[/yellow]")

        # Package ranking summary
        if record.package_ranking:
            console.print("\n  [dim]Package ranking:[/dim]")
            for ps in record.package_ranking[:5]:
                console.print(f"    {ps.package}  score {ps.score:.3f}  ({ps.viable_count} viable)")

        # Top candidates summary
        if record.candidate_ranking:
            console.print("\n  [dim]Top candidates:[/dim]")
            for cs in record.candidate_ranking[:5]:
                console.print(f"    {cs.mpn}  score {cs.final_score:.3f}  ({cs.manufacturer})")

        console.print(f"\n  [dim]Context hash: {record.context_hash}[/dim]")
        console.print(f"  [dim]Policy: {record.scoring_policy_version}[/dim]")

        if justify:
            # Role probabilities
            if record.role_probabilities:
                _print_justify_header("Role Probabilities")
                sorted_probs = sorted(
                    record.role_probabilities.items(),
                    key=lambda x: x[1], reverse=True,
                )
                for role_name, prob in sorted_probs:
                    bar = "█" * int(prob * 40) + "░" * (40 - int(prob * 40))
                    console.print(f"  {role_name:24s} {prob:.4f} {bar}")

            # Weights
            _print_justify_header("Weights Used")
            console.print(f"  Policy: {record.scoring_policy_version}")
            for name, weight in record.weights_used.items():
                console.print(f"  {name}: {weight:.2f}")

            # Rank stability
            if record.rank_stability:
                rs = record.rank_stability
                _print_justify_header("Rank Stability (Monte Carlo)")
                console.print(f"  Samples: {rs.n_samples}")
                console.print(f"  Decision: [bold]{rs.decision}[/bold]")
                console.print(f"  Reason: {rs.decision_reason}")
                for pkg, stats in sorted(
                    rs.package_stats.items(),
                    key=lambda x: x[1].mean_score,
                    reverse=True,
                ):
                    console.print(
                        f"  {pkg}: mean={stats.mean_score:.4f} "
                        f"std={stats.std_score:.4f} "
                        f"P(win)={stats.p_win:.2%} "
                        f"margin={stats.score_margin:.4f}"
                    )

            # Package utility terms
            if record.package_ranking:
                top = record.package_ranking[0]
                if top.terms:
                    _print_justify_header(f"Package Utility Terms: {top.package}")
                    for term in top.terms:
                        console.print(
                            f"  {term.name:30s} value={term.value:.4f} "
                            f"× w={term.weight:.3f} = {term.contribution:.4f}"
                        )

            # Package evidence
            if record.package_ranking:
                _print_justify_header("Package Evidence")
                for ps in record.package_ranking:
                    for ev in ps.evidence:
                        console.print(f"  {ps.package}: {ev}")

            # Score decomposition for top candidate
            if record.candidate_ranking:
                top = record.candidate_ranking[0]
                _print_justify_header(f"MPN Score Decomposition: {top.mpn}")
                for term in top.terms:
                    penalty_str = ""
                    if term.missing_data_penalty > 0:
                        penalty_str = f" [yellow](missing: -{term.missing_data_penalty:.2f})[/yellow]"
                    console.print(
                        f"  {term.name}: {term.value:.3f} "
                        f"× {term.weight:.2f} = {term.contribution:.4f}{penalty_str}"
                    )
                console.print(f"\n  TOPSIS: {top.topsis_score:.4f}")
                console.print(f"  Uncertainty: -{top.uncertainty_penalty:.4f}")
                console.print(f"  Risk: -{top.risk_penalty:.4f}")
                console.print(f"  [bold]Final: {top.final_score:.4f}[/bold]")

            # Fact chain
            if record.evidence:
                _print_justify_header("Evidence Chain")
                for fact in record.evidence:
                    console.print(f"  {fact.key} = {fact.value} [{fact.source}]")

        if debug:
            _print_debug_header("Internal State")
            console.print(f"  Context hash: {record.context_hash}")
            console.print(f"  Schema version: {record.schema_version}")
            console.print(f"  Scoring policy: {record.scoring_policy_version}")
            console.print(f"  Pin completeness: {record.pin_completeness}")

            if record.package_ranking:
                _print_debug_header("Raw Package Data")
                for ps in record.package_ranking:
                    ev = ps.package_evidence
                    if ev:
                        console.print(f"\n  [bold]{ps.package}[/bold]")
                        console.print(f"    Query strings: {ev.query_strings}")
                        console.print(f"    Raw count: {ev.raw_count}")
                        console.print(f"    Parsed count: {ev.parsed_count}")
                        console.print(f"    Viable count: {ev.viable_count}")
                        console.print(f"    First MPNs: {ev.first_raw_mpns[:5]}")
                        console.print(f"    Reject reasons: {ev.reject_reasons}")
                        console.print(f"    Lifecycle dist: {ev.lifecycle_distribution}")
