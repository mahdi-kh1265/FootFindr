"""Supplier variant browser CLI commands (M8.5).

Thin wrappers that delegate to session, display, badges, and shortlist
modules.  Registered on ``supplier_app`` from cli.py.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from footfindr.suppliers.badges import (
    compute_badges,
    extract_differentiators,
    group_by_mpn,
    recommend,
)
from footfindr.suppliers.display import (
    export_csv,
    export_markdown,
    render_comparison,
    render_fields_table,
    render_grouped,
    render_mini_table,
    render_mpn_grouped,
    render_part_detail,
    render_part_numbers_only,
    render_recommendations,
    render_search_table,
    render_shortlist,
)
from footfindr.suppliers.session import (
    SearchFilter,
    SearchSession,
    SessionError,
    SessionManager,
    apply_filter,
    compute_relevance,
    default_interleave_sort,
    default_sort_descending,
    discover_fields,
    is_mpn_like_query,
    parse_filter_value,
    resolve_field_alias,
)

console = Console()
logger = logging.getLogger("footfindr.cli.supplier")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_session_manager() -> SessionManager:
    """Create a SessionManager using the project workspace."""
    from footfindr.config import get_workspace
    return SessionManager(workspace=get_workspace())


def _part_to_json(part) -> dict:
    """Serialize a SupplierPart to a JSON-friendly dict."""
    d = {
        "mpn": part.mpn,
        "supplier": part.supplier,
        "supplier_pn": part.supplier_pn,
        "manufacturer": part.manufacturer,
        "description": part.description,
        "package": part.package,
        "stock": part.stock,
        "lifecycle": getattr(part, "lifecycle", None),
        "product_url": getattr(part, "product_url", None),
        "result_id": getattr(part, "result_id", None),
    }
    # Price breaks
    if hasattr(part, "price_breaks") and part.price_breaks:
        d["price_breaks"] = part.price_breaks
    elif hasattr(part, "unit_price"):
        d["unit_price"] = part.unit_price
    # Supplier device package
    sdp = getattr(part, "supplier_device_package", None)
    if sdp:
        d["supplier_device_package"] = sdp
    # Attributes
    attrs = getattr(part, "attributes", None)
    if attrs:
        d["attributes"] = dict(attrs)
    # Badges
    badges = getattr(part, "badges", None)
    if badges:
        d["badges"] = badges
    return d


def _resolve_providers(
    supplier: str | None,
    suppliers: str | None,
    all_suppliers: bool,
):
    """Resolve which provider(s) to query."""
    from footfindr.suppliers.registry import SupplierRegistry
    reg = SupplierRegistry()

    if all_suppliers:
        return reg.get_configured_live()

    if suppliers:
        names = [s.strip() for s in suppliers.split(",")]
        providers = []
        for name in names:
            p = reg.get(name)
            if p and p.is_configured():
                providers.append(p)
            elif p:
                console.print(f"[yellow]Warning: {name} is not configured, skipping[/yellow]")
            else:
                console.print(f"[yellow]Warning: unknown supplier '{name}', skipping[/yellow]")
        return providers

    if supplier:
        p = reg.get(supplier)
        if not p:
            console.print(f"[red]Unknown supplier: {supplier}[/red]")
            raise typer.Exit(1)
        return [p]

    # Default: all configured live
    configured = reg.get_configured_live()
    if not configured:
        console.print("[red]No live supplier credentials configured.[/red]")
        raise typer.Exit(1)
    return configured


def _require_session() -> SearchSession:
    """Load session or exit with helpful error."""
    mgr = _get_session_manager()
    try:
        return mgr.require_session()
    except SessionError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


def _resolve_dot_index(dot_arg: str, index_arg: str | None) -> tuple[SearchSession, int | None]:
    """Parse dot arguments.  Returns (session, index_or_None)."""
    session = _require_session()
    if index_arg is not None:
        try:
            return session, int(index_arg)
        except ValueError:
            console.print(f"[red]Invalid index: {index_arg}[/red]")
            raise typer.Exit(1)
    return session, None


def _parse_columns(columns_str: str | None) -> list[str] | None:
    """Parse a comma-separated columns string into a list."""
    if not columns_str:
        return None
    return [resolve_field_alias(c.strip()) for c in columns_str.split(",") if c.strip()]


def _print_status(session: SearchSession) -> None:
    """Print the compact status line for the session."""
    console.print(f"[dim]{session.get_status_line()}[/dim]")


# ---------------------------------------------------------------------------
# Session merge helpers (M9.3c)
# ---------------------------------------------------------------------------

def _dedupe_key(part) -> tuple:
    """Build a dedupe key for a SupplierPart.

    Primary: (supplier, supplier_pn)
    Fallback if supplier_pn is missing: (supplier, normalized_mfr, normalized_mpn)
    """
    supplier = getattr(part, 'supplier', '') or ''
    spn = getattr(part, 'supplier_pn', '') or ''
    if spn:
        return (supplier.lower(), spn.lower())
    mfr = (getattr(part, 'manufacturer', '') or '').strip().upper()
    mpn = (getattr(part, 'mpn', '') or '').strip().upper()
    return (supplier.lower(), f"mfr:{mfr}", f"mpn:{mpn}")


def _merge_results_into_session(
    session: SearchSession,
    new_results: list,
) -> tuple[int, int]:
    """Merge new results into an existing session, deduplicating.

    Returns (merged_count, skipped_count).
    """
    existing_keys = set()
    for r in session.original_results:
        existing_keys.add(_dedupe_key(r))

    merged = 0
    skipped = 0
    for item in new_results:
        key = _dedupe_key(item)
        if key not in existing_keys:
            session.original_results.append(item)
            session.active_result_ids.append(item.result_id)
            existing_keys.add(key)
            merged += 1
        else:
            skipped += 1

    return merged, skipped

# ---------------------------------------------------------------------------
# Expansion shard generation (M9.3c expand)
# ---------------------------------------------------------------------------

_PASSIVE_MANUFACTURERS = [
    "Murata", "TDK", "Samsung", "KYOCERA AVX", "KEMET", "Yageo",
    "Vishay", "Panasonic", "Taiyo Yuden", "Bourns", "Wurth",
]

_CAP_DIELECTRICS = ["X5R", "X6S", "X7R", "X7S", "X8R", "C0G", "NP0"]

# Category tokens to append to shards (keeps searches focused)
_CATEGORY_SHARD_TOKENS: dict[str, str] = {
    "capacitor": "capacitor",
    "resistor": "resistor",
    "inductor": "inductor",
    "diode": "diode",
    "led": "LED",
    "transistor": "transistor",
    "connector": "connector",
    "crystal": "crystal",
}


def _generate_expansion_shards(
    session: SearchSession,
    strategy: str = "auto",
    max_queries: int = 20,
) -> list[str]:
    """Generate narrower query shards for the expand command.

    Returns a list of query strings (up to max_queries).
    """
    base = " ".join(session.base_query_parts) if session.base_query_parts else session.query
    cat = session.category or ""
    cat_token = _CATEGORY_SHARD_TOKENS.get(cat, "")

    # If base_query_parts not available, try to strip category from session.query
    if not session.base_query_parts:
        for term in _CATEGORY_SHARD_TOKENS.values():
            if base.endswith(f" {term}"):
                base = base[: -len(f" {term}")].strip()
                break
            if base.endswith(f" ceramic {term}"):
                base = base[: -len(f" ceramic {term}")].strip()
                break

    shards: list[str] = []

    def _add_shard(query: str) -> bool:
        """Add shard if not duplicate and under limit. Returns False if at limit."""
        if len(shards) >= max_queries:
            return False
        if query not in shards:
            shards.append(query)
        return len(shards) < max_queries

    if strategy in ("manufacturer", "auto"):
        for mfr in _PASSIVE_MANUFACTURERS:
            parts = [base, mfr]
            if cat_token:
                parts.append(cat_token)
            if not _add_shard(" ".join(parts)):
                return shards

    if strategy in ("dielectric", "auto") and cat == "capacitor":
        for diel in _CAP_DIELECTRICS:
            parts = [base, diel]
            if cat_token:
                parts.append(cat_token)
            if not _add_shard(" ".join(parts)):
                return shards

    if strategy == "auto" and cat == "capacitor":
        # Combo shards: manufacturer + dielectric
        for mfr in _PASSIVE_MANUFACTURERS:
            for diel in _CAP_DIELECTRICS:
                parts = [base, mfr, diel]
                if cat_token:
                    parts.append(cat_token)
                if not _add_shard(" ".join(parts)):
                    return shards

    return shards


# ---------------------------------------------------------------------------
# Commands -- registered by register_supplier_commands()
# ---------------------------------------------------------------------------

def register_supplier_commands(supplier_app: typer.Typer, lib_app: typer.Typer) -> None:
    """Register all M8.5 supplier variant browser commands."""

    # ---- ff supplier search ----
    @supplier_app.command("search")
    def supplier_search_cmd(
        query: str = typer.Argument(..., help="Search query (keyword, MPN, description)."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s", help="Specific supplier."),
        suppliers: Optional[str] = typer.Option(None, "--suppliers", "-S", help="Comma-separated suppliers."),
        all_suppliers: bool = typer.Option(False, "--all", "-a", help="Query all configured suppliers."),
        mini: bool = typer.Option(False, "--mini", "--min", "-q", help="Compact output."),
        limit: int = typer.Option(25, "--limit", "-l", help="Max results per supplier."),
        qty: Optional[int] = typer.Option(None, "--qty", "-n", help="Quantity for pricing."),
        refresh: bool = typer.Option(False, "--refresh", "-r", help="Bypass cache, query live."),
        cache_only: bool = typer.Option(False, "--cache-only", "-c", help="Only use cached results."),
        add_to_session: bool = typer.Option(False, "--add-to-session", "--add", help="Merge results into active session instead of replacing it."),
        recommend_flag: bool = typer.Option(False, "--recommend", "-R", help="Show deterministic recommendations."),
        export: Optional[str] = typer.Option(None, "--export", "-e", help="Export to file (csv/md)."),
        group_by_supplier: bool = typer.Option(False, "--group-by-supplier", "-G", help="Group results by supplier."),
        strict: bool = typer.Option(False, "--strict", "-x", help="Hide low-relevance results."),
        include_related: bool = typer.Option(False, "--include-related", "-i", help="Show all results including low-relevance."),
        columns_str: Optional[str] = typer.Option(None, "--columns", "--cols", help="Comma-separated column list."),
        view: Optional[str] = typer.Option(None, "--view", "-v", help="Named view (stock, package, price, sourcing, specs)."),
        debug: bool = typer.Option(False, "--debug", "-d", help="Debug mode."),
    ) -> None:
        """Search for parts across supplier APIs and save active context."""
        if debug:
            logging.basicConfig(level=logging.DEBUG)

        if limit > 100:
            console.print("[yellow]Warning: large result sets (>100) may be slow[/yellow]")
            limit = min(limit, 200)

        from footfindr.suppliers.cache import SupplierCache
        cache = SupplierCache()

        providers = _resolve_providers(supplier, suppliers, all_suppliers)
        all_results: list = []

        for provider in providers:
            try:
                # Check search cache first
                if not refresh:
                    cached = cache.lookup_search(provider.name, query)
                    if cached is not None:
                        console.print(f"  [dim]OK {provider.name} (cached, {len(cached)} results)[/dim]")
                        all_results.extend(cached[:limit])
                        continue

                if cache_only:
                    console.print(f"  [dim]SKIP {provider.name} (no cached search)[/dim]")
                    continue

                # Live search — provider.search() may return SupplierSearchPage or list
                search_result = provider.search(query)
                raw_items = search_result.items if hasattr(search_result, "items") else search_result
                results = [r for r in raw_items if r.is_valid()][:limit]
                console.print(f"  [green]OK {provider.name} ({len(results)} results)[/green]")

                # Cache the search result (separate from exact cache)
                if results:
                    cache.store_search(provider.name, query, results)

                all_results.extend(results)

            except Exception as e:
                console.print(f"  [red]FAIL {provider.name}: {e}[/red]")

        cache.close()

        if not all_results:
            console.print(f"[yellow]No results for '{query}'[/yellow]")
            return

        # Compute badges for all results (with query for relevance)
        for r in all_results:
            r.badges = compute_badges(r, all_results, query=query)

        # Relevance filtering for MPN-like queries
        auto_strict = is_mpn_like_query(query) and not include_related
        if strict or auto_strict:
            high_relevance = [r for r in all_results if compute_relevance(r, query) <= 3]
            low_relevance = [r for r in all_results if compute_relevance(r, query) > 3]
            if low_relevance and high_relevance:
                hidden = len(low_relevance)
                all_results = high_relevance
                if not include_related:
                    console.print(f"  [dim]{hidden} low-relevance results hidden (use --include-related to show)[/dim]")

        # Sort: interleaved by default
        if not group_by_supplier:
            all_results = default_interleave_sort(all_results, query, qty)

        mgr = _get_session_manager()

        # --add-to-session: merge into existing session
        if add_to_session:
            existing = mgr.load()
            if existing:
                merged, skipped = _merge_results_into_session(existing, all_results)
                mgr.save(existing)
                console.print(
                    f"[green]Merged {merged} new result(s) into active session "
                    f"({len(existing.original_results)} total, {skipped} duplicate(s) skipped)[/green]"
                )
                # Display merged session
                display_results = existing.get_active_results()
            else:
                # No existing session — create new
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                supplier_names = [p.name for p in providers]
                session = SearchSession(
                    query=query,
                    suppliers=supplier_names,
                    created_at=now,
                    last_updated=now,
                    original_results=all_results,
                    active_result_ids=[r.result_id for r in all_results],
                    quantity=qty,
                )
                mgr.save(session)
                console.print(f"[green]No existing session. Created new session with {len(all_results)} result(s).[/green]")
                display_results = all_results
        else:
            # Normal: replace session
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            supplier_names = [p.name for p in providers]
            session = SearchSession(
                query=query,
                suppliers=supplier_names,
                created_at=now,
                last_updated=now,
                original_results=all_results,
                active_result_ids=[r.result_id for r in all_results],
                quantity=qty,
            )
            mgr.save(session)
            display_results = all_results

        # Display
        columns = _parse_columns(columns_str)
        current_session = mgr.load()
        status_line = current_session.get_status_line() if current_session else ""
        if mini:
            table = render_mini_table(
                display_results, qty=qty, query=query,
                columns=columns, view=view,
                status_line=status_line,
            )
        else:
            table = render_search_table(display_results, qty=qty, query=query)
        console.print(table)
        console.print(f"\n[dim]{len(display_results)} results saved as active search context (use . to reference)[/dim]")

        # Grouped by supplier
        if group_by_supplier:
            console.print(render_grouped(all_results, "supplier", mini=mini))

        # Recommendations
        if recommend_flag:
            recs = recommend(all_results, qty)
            console.print(render_recommendations(recs))

        # Export
        if export:
            if export.endswith(".csv"):
                n = export_csv(all_results, export, qty=qty)
                console.print(f"[green]Exported {n} results to {export}[/green]")
            elif export.endswith(".md"):
                n = export_markdown(all_results, export, qty=qty)
                console.print(f"[green]Exported {n} results to {export}[/green]")
            else:
                console.print(f"[yellow]Unknown export format. Use .csv or .md[/yellow]")

    # ---- ff supplier fields . ----
    @supplier_app.command("fields")
    def supplier_fields_cmd(
        ref: str = typer.Argument(".", help="Active result set ref (use '.')."),
        as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
        attributes_only: bool = typer.Option(False, "--attributes", "-A", help="Show only dynamic attributes."),
    ) -> None:
        """Show available fields for the current active result set."""
        session = _require_session()
        results = session.get_active_results()

        if not results:
            console.print("[yellow]No active results.[/yellow]")
            return

        fields = discover_fields(results)
        if attributes_only:
            fields = [f for f in fields if f.is_attribute]

        if as_json:
            data = [
                {"canonical": f.canonical, "aliases": f.aliases, "coverage": f.coverage,
                 "is_attribute": f.is_attribute}
                for f in fields
            ]
            console.print(json.dumps(data, indent=2, default=str))
            return

        _print_status(session)
        table = render_fields_table(fields)
        console.print(table)

    # ---- ff supplier list . ----
    @supplier_app.command("list")
    def supplier_list_cmd(
        ref: str = typer.Argument(".", help="Active result set ref (use '.')."),
        mini: bool = typer.Option(False, "--mini", "--min", "-q", help="Compact output."),
        show_all: bool = typer.Option(False, "--all", "-A", help="Show all fetched results (no pagination)."),
        part_numbers_only: bool = typer.Option(False, "--part-numbers-only", "-p", help="MPN list only."),
        columns_str: Optional[str] = typer.Option(None, "--columns", "--cols", help="Comma-separated column list."),
        view: Optional[str] = typer.Option(None, "--view", "-v", help="Named view (stock, package, price, sourcing, specs)."),
        as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    ) -> None:
        """List the active supplier search results."""
        session = _require_session()
        results = session.get_active_results()

        if not results:
            console.print("[yellow]Active search has no results.[/yellow]")
            return

        if as_json:
            data = [_part_to_json(r) for r in results]
            console.print(json.dumps(data, indent=2, default=str))
            return

        if part_numbers_only:
            console.print(render_part_numbers_only(results))
            return

        # If --all, show everything; otherwise respect pagination
        if not show_all:
            results = session.get_current_page()

        columns = _parse_columns(columns_str)
        _print_status(session)

        total_fetched = len(session.get_active_results())
        if show_all:
            console.print(f"[dim]Showing all {total_fetched} fetched results[/dim]")
            # Show provider total info when provider reports more than fetched
            if session.provider_status:
                for sup_name, status in session.provider_status.items():
                    total_avail = status.get("provider_total_available")
                    pag_status = status.get("pagination_status", session.pagination_status)
                    if total_avail and total_avail > total_fetched:
                        console.print(
                            f"[yellow]{sup_name} reports {total_avail} total matches, "
                            f"but only {total_fetched} unique results have been fetched.[/yellow]"
                        )
                        if pag_status == "degraded_duplicate_page" or session.pagination_status == "degraded":
                            console.print(
                                "[yellow]Provider pagination is degraded for this query.[/yellow]"
                            )
                            console.print(
                                "[dim]Showing locally fetched/expanded results. Use:\n"
                                "  ff sup expand .     (runs narrower queries to gather more unique candidates)\n"
                                "  ff sup search <exact MPN> --supplier <s> --refresh --add-to-session[/dim]"
                            )
                        else:
                            # Unverified — not yet proven degraded
                            console.print(
                                "[dim]Use:\n"
                                "  ff sup more .       (may return duplicates)\n"
                                "  ff sup expand .     (runs narrower queries to gather more unique candidates)[/dim]"
                            )

        if mini or show_all or columns or view:
            table = render_mini_table(
                results,
                context_fields=session.sort_fields,
                columns=columns,
                view=view,
                qty=session.quantity,
                query=session.query,
                status_line=session.get_status_line(),
            )
        else:
            table = render_search_table(results, qty=session.quantity, query=session.query)
        console.print(table)

    # ---- ff supplier group . <field> ----
    @supplier_app.command("group")
    def supplier_group_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        field: str = typer.Argument(..., help="Field to group by (package, temp, supplier, etc.)."),
        mini: bool = typer.Option(False, "--mini", "--min", "-q", help="Compact output."),
        mpn_variants: bool = typer.Option(False, "--mpn-variants", "-M", help="Group by manufacturer MPN."),
    ) -> None:
        """Group active search results by a field."""
        session = _require_session()
        results = session.get_active_results()

        if not results:
            console.print("[yellow]No active results.[/yellow]")
            return

        canon = resolve_field_alias(field)

        if mpn_variants or canon == "mpn":
            groups = group_by_mpn(results)
            console.print(render_mpn_grouped(groups, mini=mini))
            return

        console.print(render_grouped(results, canon, mini=mini))

    # ---- ff supplier filter . <field> <value> ----
    @supplier_app.command("filter")
    def supplier_filter_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        field: str = typer.Argument(..., help="Field to filter on."),
        value: str = typer.Argument(..., help="Filter value (e.g. 'DFN', '>100', 'Active')."),
        mini: bool = typer.Option(False, "--mini", "--min", "-q", help="Compact output."),
        columns_str: Optional[str] = typer.Option(None, "--columns", "--cols", help="Comma-separated column list."),
        view: Optional[str] = typer.Option(None, "--view", "-v", help="Named view."),
    ) -> None:
        """Filter active search results by a field value."""
        session = _require_session()
        canon = resolve_field_alias(field)
        op, clean_val = parse_filter_value(value)

        filt = SearchFilter(field=canon, op=op, value=clean_val)
        session.filters.append(filt)

        # Re-apply all filters from original_results
        active_ids = []
        for r in session.original_results:
            passes = all(apply_filter(r, f) for f in session.filters)
            if passes:
                active_ids.append(r.result_id)

        session.active_result_ids = active_ids

        mgr = _get_session_manager()
        mgr.save(session)

        results = session.get_active_results()
        filter_desc = f"{canon} {op} '{clean_val}'"
        console.print(f"[dim]Filter: {filter_desc} -> {len(results)} results[/dim]")

        if not results:
            console.print("[yellow]No results match filter. Use: ff supplier filter-clear .[/yellow]")
            return

        columns = _parse_columns(columns_str)
        if mini:
            # Show the filtered field as a context column
            context = [canon] + session.sort_fields
            table = render_mini_table(results, context_fields=context,
                                      columns=columns, view=view,
                                      qty=session.quantity, query=session.query,
                                      status_line=session.get_status_line())
        else:
            table = render_search_table(results, qty=session.quantity, query=session.query)
        console.print(table)

    # ---- ff supplier filter-clear . ----
    @supplier_app.command("filter-clear")
    def supplier_filter_clear_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
    ) -> None:
        """Clear all filters and restore original results."""
        session = _require_session()
        session.filters.clear()
        session.active_result_ids = [r.result_id for r in session.original_results]

        mgr = _get_session_manager()
        mgr.save(session)

        console.print(f"[green]Filters cleared. {len(session.original_results)} results restored.[/green]")

    # ---- ff supplier filter-reset (alias) ----
    @supplier_app.command("filter-reset", hidden=True)
    def supplier_filter_reset_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
    ) -> None:
        """Alias for filter-clear."""
        supplier_filter_clear_cmd(ref=ref)

    # ---- ff supplier sort . <field> ----
    @supplier_app.command("sort")
    def supplier_sort_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        field: str = typer.Argument(..., help="Field to sort by."),
        desc: bool = typer.Option(False, "--desc", "-D", help="Sort descending."),
        asc: bool = typer.Option(False, "--asc", "-A", help="Sort ascending."),
        then: Optional[str] = typer.Option(None, "--then", "-T", help="Secondary sort field (multi-sort)."),
        add: Optional[str] = typer.Option(None, "--add", "-a", help="Add secondary sort field."),
        mini: bool = typer.Option(False, "--mini", "--min", "-q", help="Compact output."),
        columns_str: Optional[str] = typer.Option(None, "--columns", "--cols", help="Comma-separated column list."),
        view: Optional[str] = typer.Option(None, "--view", "-v", help="Named view."),
    ) -> None:
        """Sort active search results by a field.

        Default: replaces any previous sort.
        Use --then or --add for multi-sort.
        """
        session = _require_session()
        canon = resolve_field_alias(field)

        # Default: REPLACE previous sort (not accumulate)
        sort_fields = [canon]

        # Multi-sort: --then or --add appends a secondary field
        secondary = then or add
        if secondary:
            for s in secondary.split(","):
                sf = resolve_field_alias(s.strip())
                if sf not in sort_fields:
                    sort_fields.append(sf)

        session.sort_fields = sort_fields
        if desc:
            session.sort_descending = True
        elif asc:
            session.sort_descending = False
        else:
            session.sort_descending = default_sort_descending(canon)

        mgr = _get_session_manager()
        mgr.save(session)

        results = session.get_active_results()
        direction = "desc" if session.sort_descending else "asc"
        sort_desc = ",".join(sort_fields)
        console.print(f"[dim]Sorted by {sort_desc} ({direction})[/dim]")

        columns = _parse_columns(columns_str)
        if mini:
            # Show sort field(s) as context columns
            table = render_mini_table(results, context_fields=sort_fields,
                                      columns=columns, view=view,
                                      qty=session.quantity, query=session.query,
                                      status_line=session.get_status_line())
        else:
            table = render_search_table(results, qty=session.quantity, query=session.query)
        console.print(table)

    # ---- ff supplier sort-clear . ----
    @supplier_app.command("sort-clear")
    def supplier_sort_clear_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
    ) -> None:
        """Clear sorting and restore default order."""
        session = _require_session()
        session.sort_fields = []
        session.sort_descending = True

        mgr = _get_session_manager()
        mgr.save(session)

        console.print("[green]Sort cleared. Default order restored.[/green]")

    # ---- ff supplier show . [index] ----
    @supplier_app.command("show")
    def supplier_show_cmd(
        ref: str = typer.Argument(".", help="Active result set ref, MPN, or supplier PN."),
        index: Optional[str] = typer.Argument(None, help="1-based index in active results."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s"),
        refresh: bool = typer.Option(False, "--refresh", help="Force live lookup."),
        raw_json: bool = typer.Option(False, "--raw-json", help="Show raw supplier JSON."),
        save_raw: bool = typer.Option(False, "--save-raw", help="Save raw JSON to debug dir."),
        footprint_hints: bool = typer.Option(False, "--footprint-hints", help="Show footprint hints."),
        debug: bool = typer.Option(False, "--debug"),
    ) -> None:
        """Show full details for a specific part."""
        if debug:
            logging.basicConfig(level=logging.DEBUG)

        part = None

        if ref == ".":
            session = _require_session()

            if index is not None:
                try:
                    idx = int(index)
                except ValueError:
                    console.print(f"[red]Invalid index: {index}[/red]")
                    raise typer.Exit(1)
                part = session.get_by_index(idx)
                if not part:
                    console.print(f"[red]No result at index {idx}[/red]")
                    raise typer.Exit(1)
            else:
                # Check for selected result
                selected = session.get_selected()
                if selected:
                    part = selected
                else:
                    active = session.get_active_results()
                    if len(active) == 1:
                        part = active[0]
                    else:
                        console.print("[yellow]Multiple active results. Use:[/yellow]")
                        console.print("  ff supplier show . 1")
                        console.print("  ff supplier filter . package DFN")
                        console.print("  ff supplier choose . 1")
                        raise typer.Exit(1)
        else:
            # Direct MPN or supplier PN lookup
            from footfindr.suppliers.registry import SupplierRegistry
            reg = SupplierRegistry()
            if supplier:
                provider = reg.get(supplier)
                if provider:
                    part = provider.lookup_mpn(ref)
            else:
                for p in reg.get_configured_live():
                    part = p.lookup_mpn(ref)
                    if part and part.is_valid():
                        break

        if not part:
            console.print(f"[yellow]No data found for '{ref}'[/yellow]")
            raise typer.Exit(1)

        mgr = _get_session_manager()
        session_data = mgr.load()

        table = render_part_detail(part, qty=session_data.quantity if session_data else None)
        console.print(table)

        if footprint_hints:
            pkg = part.package or part.supplier_device_package or ""
            if pkg:
                console.print(f"\n[bold]Footprint hint:[/bold]")
                console.print(f"  Package: {pkg}")
                console.print(f"  Confidence: review")
                console.print(f"  [dim]No auto-binding. Manual review required.[/dim]")

        if raw_json or save_raw:
            # Show serialized part data
            from footfindr.suppliers.session import _part_to_dict
            raw = json.dumps(_part_to_dict(part), indent=2, default=str)
            if raw_json:
                console.print(raw)
            if save_raw:
                from footfindr.config import get_workspace
                debug_dir = get_workspace() / "debug" / "supplier_raw" / part.supplier
                debug_dir.mkdir(parents=True, exist_ok=True)
                safe_name = (part.mpn or "unknown").replace("#", "_").replace("/", "_")
                out = debug_dir / f"{safe_name}.json"
                out.write_text(raw, encoding="utf-8")
                console.print(f"[green]Saved raw JSON to {out}[/green]")

    # ---- ff supplier compare-variants . <indices> ----
    @supplier_app.command("compare-variants")
    def supplier_compare_variants_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        indices: str = typer.Argument(..., help="Comma-separated 1-based indices (e.g. 1,3,5)."),
    ) -> None:
        """Compare selected variants side-by-side."""
        session = _require_session()
        parts = []
        for idx_str in indices.split(","):
            try:
                idx = int(idx_str.strip())
            except ValueError:
                console.print(f"[red]Invalid index: {idx_str}[/red]")
                raise typer.Exit(1)
            part = session.get_by_index(idx)
            if part:
                parts.append(part)
            else:
                console.print(f"[yellow]No result at index {idx}[/yellow]")

        if len(parts) < 2:
            console.print("[red]Need at least 2 parts to compare.[/red]")
            raise typer.Exit(1)

        table = render_comparison(parts, qty=session.quantity)
        console.print(table)

        # Show differentiators
        diffs = extract_differentiators(parts)
        if diffs:
            console.print("\n[bold]Key differentiators:[/bold]")
            for name, vals in diffs.items():
                console.print(f"  {name}: {', '.join(vals)}")

    # ---- ff supplier choose . [index] ----
    @supplier_app.command("choose")
    def supplier_choose_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        index: Optional[str] = typer.Argument(None, help="1-based index."),
    ) -> None:
        """Mark a result as selected in the active context."""
        session = _require_session()

        if index is not None:
            try:
                idx = int(index)
            except ValueError:
                console.print(f"[red]Invalid index: {index}[/red]")
                raise typer.Exit(1)
            part = session.get_by_index(idx)
            if not part:
                console.print(f"[red]No result at index {idx}[/red]")
                raise typer.Exit(1)
        else:
            active = session.get_active_results()
            if len(active) == 1:
                part = active[0]
            else:
                console.print("[yellow]Multiple results. Specify an index:[/yellow]")
                console.print("  ff supplier choose . 1")
                raise typer.Exit(1)

        session.selected_result_id = part.result_id
        mgr = _get_session_manager()
        mgr.save(session)

        console.print(f"[green]Selected {part.mpn}[/green]")
        console.print(f"\nNext:")
        console.print(f"  ff supplier show .")
        console.print(f"  ff supplier shortlist add .")
        console.print(f"  ff lib promote-supplier . --to <LIBRARY> --as <INTERNAL_PN>")

    # ---- ff supplier shortlist add/list/remove ----
    shortlist_app = typer.Typer(help="Supplier part shortlist.")
    supplier_app.add_typer(shortlist_app, name="shortlist")

    @shortlist_app.command("add")
    def shortlist_add_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        index: Optional[str] = typer.Argument(None, help="1-based index."),
        notes: Optional[str] = typer.Option(None, "--notes", help="Notes for this entry."),
    ) -> None:
        """Add a part to the shortlist."""
        from footfindr.suppliers.shortlist import Shortlist, ShortlistEntry

        part = None
        session = _require_session()

        if index is not None:
            try:
                idx = int(index)
            except ValueError:
                console.print(f"[red]Invalid index: {index}[/red]")
                raise typer.Exit(1)
            part = session.get_by_index(idx)
        else:
            selected = session.get_selected()
            if selected:
                part = selected
            else:
                active = session.get_active_results()
                if len(active) == 1:
                    part = active[0]
                else:
                    console.print("[yellow]Multiple results. Specify an index or choose first.[/yellow]")
                    raise typer.Exit(1)

        if not part:
            console.print("[red]No part found.[/red]")
            raise typer.Exit(1)

        sl = Shortlist()
        entry = ShortlistEntry.from_supplier_part(part, notes=notes)
        sl.add(entry)
        console.print(f"[green]Added {part.mpn} ({part.supplier}) to shortlist[/green]")

    @shortlist_app.command("list")
    def shortlist_list_cmd() -> None:
        """Show the supplier part shortlist."""
        from footfindr.suppliers.shortlist import Shortlist

        sl = Shortlist()
        entries = sl.list()
        if not entries:
            console.print("[dim]Shortlist is empty.[/dim]")
            return

        table = render_shortlist([e.to_dict() for e in entries])
        console.print(table)

    @shortlist_app.command("remove")
    def shortlist_remove_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        index: Optional[str] = typer.Argument(None, help="1-based shortlist index."),
    ) -> None:
        """Remove a part from the shortlist."""
        from footfindr.suppliers.shortlist import Shortlist

        sl = Shortlist()
        if index is not None:
            try:
                idx = int(index)
            except ValueError:
                console.print(f"[red]Invalid index: {index}[/red]")
                raise typer.Exit(1)
            if sl.remove(index=idx):
                console.print(f"[green]Removed shortlist entry #{idx}[/green]")
            else:
                console.print(f"[red]No entry at index {idx}[/red]")
        else:
            console.print("[yellow]Specify an index: ff supplier shortlist remove . 1[/yellow]")

    @shortlist_app.command("clear")
    def shortlist_clear_cmd() -> None:
        """Clear the entire shortlist."""
        from footfindr.suppliers.shortlist import Shortlist
        sl = Shortlist()
        sl.clear()
        console.print("[green]Shortlist cleared.[/green]")

    # ---- ff lib promote-supplier . --to <lib> --as <pn> ----
    @lib_app.command("promote-supplier")
    def lib_promote_supplier_cmd(
        ref: str = typer.Argument(".", help="Active result set ref, MPN, or supplier PN."),
        index: Optional[str] = typer.Argument(None, help="1-based index in active results."),
        to: str = typer.Option(..., "--to", help="Target library name."),
        as_pn: str = typer.Option(..., "--as", help="Internal part number to assign."),
        for_ref: Optional[str] = typer.Option(None, "--for", help="Schematic ref to check constraints for."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s"),
        apply: bool = typer.Option(False, "--apply", "-a", help="Apply immediately (skip plan)."),
        plan_mode: bool = typer.Option(False, "--plan", "-p", help="Generate plan (default behavior)."),
        dry_run: bool = typer.Option(False, "--dry-run", "--dry", help="Alias for --plan."),
        force: bool = typer.Option(False, "--force", "-f", help="Force promotion despite constraint failures or collisions."),
    ) -> None:
        """Promote a supplier part into an approved library.

        Default behavior generates a plan. Use --apply to write immediately.
        """
        import datetime as _dt

        from footfindr.constraints import ConstraintManager, check_part_constraints, infer_category
        from footfindr.libraries.manager import LibraryManager
        from footfindr.libraries.promotion import promote_from_supplier
        from footfindr.plans import Plan, PlanManager, PlanStep, check_collisions

        part = None

        if ref == ".":
            session = _require_session()
            if index is not None:
                try:
                    idx = int(index)
                except ValueError:
                    console.print(f"[red]Invalid index: {index}[/red]")
                    raise typer.Exit(1)
                part = session.get_by_index(idx)
            else:
                selected = session.get_selected()
                if selected:
                    part = selected
                else:
                    active = session.get_active_results()
                    if len(active) == 1:
                        part = active[0]
                    else:
                        console.print("[red]Ambiguous: multiple results. Choose or specify index.[/red]")
                        raise typer.Exit(1)
        else:
            from footfindr.suppliers.registry import SupplierRegistry
            reg = SupplierRegistry()
            if supplier:
                p = reg.get(supplier)
                if p:
                    part = p.lookup_mpn(ref)
            else:
                for p in reg.get_configured_live():
                    part = p.lookup_mpn(ref)
                    if part and part.is_valid():
                        break

        if not part or not part.is_valid():
            console.print(f"[red]Could not resolve part for '{ref}'[/red]")
            raise typer.Exit(1)

        manager = LibraryManager()

        # Collision detection
        collision_warnings = check_collisions(
            mpn=part.mpn,
            internal_pn=as_pn,
            supplier_pn=part.supplier_pn,
            target_library=to,
            manager=manager,
        )

        # Constraint check
        constraint_check_data = None
        if for_ref:
            cmgr = ConstraintManager()
            constraints = cmgr.get_constraints_for(for_ref)
            if constraints:
                results = check_part_constraints(constraints, part)
                constraint_check_data = {
                    "ref": for_ref,
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

        # Category inference
        cat_value, cat_confidence = infer_category(
            ref=for_ref,
            description=getattr(part, "description", None),
        )

        # Build plan data
        from footfindr.libraries.promotion import _get_library_parts_file
        target_file = str(_get_library_parts_file(to, manager))

        new_value = {
            "internal_pn": as_pn,
            "category": cat_value,
            "manufacturer": part.manufacturer,
            "mpn": part.mpn,
            "description": part.description,
            "package": part.package or getattr(part, 'supplier_device_package', None),
            "supplier_pns": {part.supplier: part.supplier_pn} if part.supplier_pn else {},
            "promoted_from": f"supplier:{part.supplier}",
            "notes": (
                f"{'Selected for ref: ' + for_ref + '. ' if for_ref else ''}"
                f"Promoted from {part.supplier} supplier search. "
                f"Supplier PN: {part.supplier_pn or 'N/A'}. "
                f"Source: {getattr(part, 'product_url', None) or 'N/A'}. "
                f"Footprint: review required."
                f"{' Category: needs review.' if cat_confidence == 'review' else ''}"
            ),
        }

        plan = Plan(
            plan_id=PlanManager.generate_plan_id("promote"),
            operation="promote-supplier",
            created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            steps=[PlanStep(
                operation="promote",
                target_file=target_file,
                target_key=as_pn,
                new_value=new_value,
                reason=f"Promote {part.mpn} from {part.supplier} into {to}",
                warnings=[w.message for w in collision_warnings],
            )],
            constraint_check=constraint_check_data,
            collision_warnings=[w.message for w in collision_warnings],
            provenance={
                "target_library": to,
                "supplier": part.supplier,
                "supplier_pn": part.supplier_pn,
                "mpn": part.mpn,
                "manufacturer": part.manufacturer,
                "result_id": part.result_id if hasattr(part, 'result_id') else None,
                "for_ref": for_ref,
            },
        )

        # If --apply, execute immediately
        if apply:
            # Check constraints
            if constraint_check_data and not force:
                hard_failures = [
                    r for r in constraint_check_data.get("results", [])
                    if not r.get("passed") and not r.get("is_soft")
                ]
                if hard_failures:
                    console.print("[red]Constraint failures:[/red]")
                    for r in hard_failures:
                        console.print(f"  [red]✗ {r['message']}[/red]")
                    console.print("\n[yellow]Use --force to promote anyway.[/yellow]")
                    raise typer.Exit(1)

            # Check collisions
            if collision_warnings and not force:
                console.print("[yellow]Collision warnings:[/yellow]")
                for w in collision_warnings:
                    console.print(f"  [yellow]⚠ {w.message}[/yellow]")
                console.print("\n[yellow]Use --force to promote anyway.[/yellow]")
                raise typer.Exit(1)

            try:
                promoted = promote_from_supplier(
                    part=part,
                    target_library=to,
                    manager=manager,
                    internal_pn=as_pn,
                    for_ref=for_ref,
                )
                plan.status = "applied"
                PlanManager().create(plan)
                console.print(f"[green]Promoted {part.mpn} -> {to} as {as_pn}[/green]")
                console.print(f"  Manufacturer: {promoted.manufacturer}")
                console.print(f"  MPN: {promoted.mpn}")
                console.print(f"  Package: {promoted.package or 'review'}")
                console.print(f"  Category: {promoted.category.value}")
                console.print(f"  Source: {part.supplier} ({part.supplier_pn})")
            except Exception as e:
                console.print(f"[red]Promotion failed: {e}[/red]")
                raise typer.Exit(1)
            return

        # Default: plan mode
        plan_mgr = PlanManager()
        plan_path = plan_mgr.create(plan)

        console.print(f"\n[bold cyan]Promotion plan created: {plan.plan_id}[/bold cyan]")
        console.print(f"  Operation: promote {part.mpn} -> {to} as {as_pn}")
        console.print(f"  Target: {target_file}")
        if for_ref:
            console.print(f"  For ref: {for_ref}")

        if constraint_check_data:
            console.print("\n  [bold]Constraint check:[/bold]")
            for r in constraint_check_data.get("results", []):
                icon = "✓" if r["passed"] else ("⚠" if r.get("is_soft") else "✗")
                style = "green" if r["passed"] else ("yellow" if r.get("is_soft") else "red")
                console.print(f"    [{style}]{icon}[/{style}] {r['message']}")

        if collision_warnings:
            console.print("\n  [bold yellow]Collisions:[/bold yellow]")
            for w in collision_warnings:
                console.print(f"    [yellow]⚠ {w.message}[/yellow]")

        console.print(f"\n  [dim]To apply:  ff plan apply {plan.plan_id}[/dim]")
        console.print(f"  [dim]To discard: ff plan discard {plan.plan_id}[/dim]")

    # ---- ff supplier session ----
    session_app = typer.Typer(help="Active search session management.")
    supplier_app.add_typer(session_app, name="session")

    @session_app.command("show")
    def session_show_cmd(
        as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    ) -> None:
        """Show active search session state."""
        session = _require_session()
        active = session.get_active_results()

        if as_json:
            data = {
                "query": session.query,
                "suppliers": session.suppliers,
                "created_at": session.created_at,
                "last_updated": session.last_updated,
                "original_count": len(session.original_results),
                "active_count": len(active),
                "quantity": session.quantity,
                "sort_fields": session.sort_fields,
                "sort_descending": session.sort_descending,
                "filters": [f"{f.field} {f.op} '{f.value}'" for f in session.filters],
                "selected_result_id": session.selected_result_id,
                "selected_mpn": session.get_selected().mpn if session.get_selected() else None,
            }
            console.print(json.dumps(data, indent=2, default=str))
            return

        console.print("\n[bold cyan]Active Supplier Session[/bold cyan]")
        console.print(f"  Query: [bold]{session.query}[/bold]")
        console.print(f"  Supplier(s): {', '.join(session.suppliers)}")
        console.print(f"  Created: {session.created_at}")
        console.print(f"  Updated: {session.last_updated}")
        console.print(f"  Results: {len(active)} / {len(session.original_results)} original")
        if session.quantity:
            console.print(f"  Quantity: {session.quantity}")
        if session.sort_fields:
            direction = "desc" if session.sort_descending else "asc"
            console.print(f"  Sort: {', '.join(session.sort_fields)} {direction}")
        if session.filters:
            console.print(f"  Filters:")
            for f in session.filters:
                console.print(f"    {f.field} {f.op} '{f.value}'")
        selected = session.get_selected()
        if selected:
            console.print(f"  Selected: #{session.active_result_ids.index(session.selected_result_id)+1} {selected.mpn}")

        console.print("\n  [dim]Available commands:[/dim]")
        console.print("    ff supplier list .")
        console.print("    ff supplier sort . <field>")
        console.print("    ff supplier filter . <field> <value>")
        console.print("    ff supplier show . <index>")
        console.print("    ff supplier explain-diff . <indices>")
        console.print("    ff supplier fields .")
        console.print("    ff supplier session reset")

    @session_app.command("reset")
    def session_reset_cmd() -> None:
        """Reset active session: restore original results, clear filters/sort/selection."""
        session = _require_session()
        session.filters.clear()
        session.sort_fields = []
        session.sort_descending = False
        session.selected_result_id = None
        session.active_result_ids = [r.result_id for r in session.original_results]
        session.last_updated = datetime.datetime.now(datetime.timezone.utc).isoformat()

        mgr = _get_session_manager()
        mgr.save(session)
        console.print(f"[green]Session reset: {len(session.original_results)} results restored[/green]")

    @session_app.command("clear")
    def session_clear_cmd() -> None:
        """Delete active session entirely."""
        mgr = _get_session_manager()
        mgr.clear()
        console.print("[green]Session cleared.[/green]")

    # ---- ff supplier explain-diff . 1,3 ----
    @supplier_app.command("explain-diff")
    def supplier_explain_diff_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        indices: str = typer.Argument(..., help="Comma-separated 1-based indices (e.g. '1,3')."),
        as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    ) -> None:
        """Explain differences between selected parts."""
        from footfindr.suppliers.display import render_explain_diff

        session = _require_session()

        try:
            idx_list = [int(i.strip()) for i in indices.split(",")]
        except ValueError:
            console.print(f"[red]Invalid indices: {indices}[/red]")
            raise typer.Exit(1)

        parts = []
        for idx in idx_list:
            p = session.get_by_index(idx)
            if not p:
                console.print(f"[red]No result at index {idx}[/red]")
                raise typer.Exit(1)
            parts.append(p)

        if as_json:
            data = render_explain_diff(parts, as_json=True)
            console.print(json.dumps(data, indent=2, default=str))
            return

        output = render_explain_diff(parts)
        console.print(output)

    # ---- ff supplier explain . 1 ----
    @supplier_app.command("explain")
    def supplier_explain_cmd(
        ref: str = typer.Argument(".", help="Active result set ref."),
        index: str = typer.Argument(..., help="1-based index."),
        as_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    ) -> None:
        """Explain a single part in context."""
        session = _require_session()

        try:
            idx = int(index)
        except ValueError:
            console.print(f"[red]Invalid index: {index}[/red]")
            raise typer.Exit(1)

        part = session.get_by_index(idx)
        if not part:
            console.print(f"[red]No result at index {idx}[/red]")
            raise typer.Exit(1)

        if as_json:
            console.print(json.dumps(_part_to_json(part), indent=2, default=str))
            return

        detail = render_part_detail(part)
        console.print(detail)

    # ---- ff supplier search-for <ref> ----
    @supplier_app.command("search-for")
    def supplier_search_for_cmd(
        ref_name: str = typer.Argument(..., help="Schematic reference (e.g. C13, U3)."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s"),
        suppliers: Optional[str] = typer.Option(None, "--suppliers", "-S", "--sups"),
        all_suppliers: bool = typer.Option(False, "--all"),
        mini: bool = typer.Option(True, "--mini", "--min", "-q", help="Compact output (default on)."),
        full: bool = typer.Option(False, "--full", help="Full output."),
        limit: int = typer.Option(25, "--limit", "-l"),
        refresh: bool = typer.Option(False, "--refresh", "-r"),
        debug: bool = typer.Option(False, "--debug", "-d", help="Show per-candidate constraint details."),
    ) -> None:
        """Search suppliers using constraints defined for a schematic reference."""
        from rich.markup import escape as rich_escape
        from footfindr.constraints import ConstraintManager, _op_to_prefix, apply_constraints_to_results, check_part_constraints, infer_category

        # --full overrides --mini
        if full:
            mini = False

        cmgr = ConstraintManager()

        # Try to get schematic Value for the ref
        schematic_value = None
        try:
            from footfindr.project import resolve_schematic_path
            sch_path = resolve_schematic_path()
            from footfindr.kicad.schematic import KiCadSchematicReader
            reader = KiCadSchematicReader()
            sch = reader.read(sch_path)
            sym = sch.symbol_by_ref(ref_name)
            if sym and sym.value:
                schematic_value = sym.value
        except Exception:
            pass  # No active schematic — still works with constraints only

        query_str, constraints = cmgr.build_search_query(ref_name, schematic_value=schematic_value)

        if not query_str:
            console.print(f"[yellow]No constraints defined for {ref_name}[/yellow]")
            console.print("[dim]Use: ff constraint set <ref> <field> <value>[/dim]")
            raise typer.Exit(1)

        console.print(f"\n[bold cyan]Ref: {ref_name}[/bold cyan]")
        if schematic_value:
            console.print(f"[dim]Schematic Value: {rich_escape(schematic_value)}[/dim]")
        console.print("[bold]Constraints used:[/bold]")
        for c in constraints:
            if c.field not in ("reason",):
                console.print(f"  {c.field} {_op_to_prefix(c.op)}{c.value}")
        console.print(f"\n[bold]Generated search query:[/bold]")
        console.print(f"  {rich_escape(query_str)}\n")

        # Search function — runs for primary and fallback queries
        from footfindr.suppliers.cache import SupplierCache
        cache = SupplierCache()
        providers = _resolve_providers(supplier, suppliers, all_suppliers)

        # Track provider_total_available across search calls
        _provider_total_available: dict[str, int | None] = {}

        def _run_search(q: str, search_limit: int | None = None, search_offset: int = 0) -> tuple[list, dict[str, int], bool]:
            """Execute search for a given query across providers.

            Returns: (results, provider_offsets, has_more_remote)
            """
            from footfindr.suppliers.base import SupplierSearchPage
            results: list = []
            provider_offsets: dict[str, int] = {}
            has_more_remote = False
            effective_limit = search_limit or limit

            for provider in providers:
                try:
                    if not refresh and search_offset == 0:
                        cached = cache.lookup_search(provider.name, q)
                        if cached is not None:
                            console.print(f"  [dim]OK {provider.name} (cached, {len(cached)} results)[/dim]")
                            results.extend(cached[:effective_limit])
                            provider_offsets[provider.name] = len(cached[:effective_limit])
                            continue
                    search_result = provider.search(q, limit=effective_limit, offset=search_offset)

                    # Handle both SupplierSearchPage and list returns
                    if isinstance(search_result, SupplierSearchPage):
                        provider_results = [r for r in search_result.items if r.is_valid()]
                        has_more_remote = has_more_remote or search_result.has_more
                        total_str = ""
                        if search_result.total_available:
                            total_str = f" of {search_result.total_available}"
                            _provider_total_available[provider.name] = search_result.total_available
                        console.print(f"  [green]OK {provider.name} ({len(provider_results)} results{total_str})[/green]")
                        provider_offsets[provider.name] = search_offset + len(provider_results)
                    else:
                        provider_results = [r for r in search_result if r.is_valid()][:effective_limit]
                        console.print(f"  [green]OK {provider.name} ({len(provider_results)} results)[/green]")
                        provider_offsets[provider.name] = len(provider_results)
                        has_more_remote = has_more_remote or (len(provider_results) == effective_limit)

                    if provider_results and search_offset == 0:
                        cache.store_search(provider.name, q, provider_results)
                    results.extend(provider_results)
                except Exception as e:
                    console.print(f"  [red]FAIL {provider.name}: {e}[/red]")
            return results, provider_offsets, has_more_remote

        all_results, provider_offsets, has_more_remote = _run_search(query_str)

        if debug:
            console.print(f"\n[dim]Debug: cache_key = {rich_escape(query_str)}[/dim]")
            console.print(f"[dim]Debug: providers = {[p.name for p in providers]}[/dim]")
            console.print(f"[dim]Debug: total_results = {len(all_results)}[/dim]")

        if not all_results:
            console.print(f"[yellow]No results for query.[/yellow]")
            cache.close()
            return

        # Badges
        for r in all_results:
            r.badges = compute_badges(r, all_results, query=query_str)

        # Apply constraints as filter
        passing, summaries = apply_constraints_to_results(constraints, all_results)

        # Debug: show per-candidate constraint results
        if debug and summaries:
            console.print("\n[bold]Per-candidate constraint check:[/bold]")
            for part, summary in zip(all_results[:10], summaries[:10]):
                mpn = getattr(part, 'mpn', '?')
                all_passed = summary["passed"]
                status = "[green]PASS[/green]" if all_passed else "[red]FAIL[/red]"
                console.print(f"\n  {rich_escape(mpn)}  {status}")
                for cr in summary["results"]:
                    p_status = "[green]PASS[/green]" if cr.passed else "[red]FAIL[/red]"
                    actual = rich_escape(str(cr.actual_value)) if cr.actual_value else "?"
                    source_info = f" from {cr.source}" if cr.source else ""
                    console.print(f"    {cr.constraint.field}: expected {_op_to_prefix(cr.constraint.op)}{cr.constraint.value}, actual {actual}{source_info}, {p_status}")

        # Fallback query handling
        fallback_used = None
        if not passing:
            console.print(f"\n  [yellow]0/{len(all_results)} results passed full query constraints.[/yellow]")

            # Show top-3 rejected candidates with reasons
            console.print("\n  [bold]Top rejected candidates:[/bold]")
            for i, (part, summary) in enumerate(zip(all_results[:3], summaries[:3])):
                mpn = rich_escape(getattr(part, 'mpn', '?'))
                console.print(f"    {i+1}. {mpn}")
                for cr in summary["results"]:
                    if not cr.passed:
                        actual = rich_escape(str(cr.actual_value)) if cr.actual_value else "?"
                        source_info = f" from {cr.source}" if cr.source else ""
                        console.print(f"       [red]FAIL[/red] {cr.constraint.field}: expected {_op_to_prefix(cr.constraint.op)}{cr.constraint.value}, actual {actual}{source_info}")

            # Try fallback queries
            fallbacks = cmgr.build_fallback_queries(ref_name, schematic_value=schematic_value)
            for fb_query in fallbacks:
                console.print(f"\n  [dim]Trying fallback query:[/dim]")
                console.print(f"    {rich_escape(fb_query)}")
                fb_results, _, _ = _run_search(fb_query)
                if fb_results:
                    for r in fb_results:
                        r.badges = compute_badges(r, fb_results, query=fb_query)
                    fb_passing, _ = apply_constraints_to_results(constraints, fb_results)
                    if fb_passing:
                        console.print(f"  [green]{len(fb_passing)}/{len(fb_results)} results pass constraints[/green]")
                        passing = fb_passing
                        all_results = fb_results
                        fallback_used = fb_query
                        break

            if not passing:
                console.print(f"\n  [dim]No candidates passed constraints across all queries.[/dim]")
                console.print(f"  [dim]Try a manual search with different terms:[/dim]")
                console.print(f"    ff sup s \"{rich_escape(query_str)}\" -s dk -r --mini")
                console.print()
                passing = all_results

        else:
            console.print(f"\n  [green]{len(passing)}/{len(all_results)} results pass constraints[/green]")

        cache.close()

        all_results = default_interleave_sort(passing, query_str)

        # Build base_query_parts: query parts without the category term
        # These are used by expand to generate shard queries
        _base_parts = []
        cat, _ = infer_category(ref_name)
        _CATEGORY_SEARCH_TERMS = {
            "capacitor": "ceramic capacitor", "resistor": "resistor",
            "inductor": "inductor", "diode": "diode",
            "led": "LED", "transistor": "transistor",
            "connector": "connector", "crystal": "crystal",
        }
        cat_term = _CATEGORY_SEARCH_TERMS.get(cat or "", "")
        effective_query = fallback_used or query_str
        if cat_term and effective_query.endswith(cat_term):
            _base_parts = effective_query[: -len(cat_term)].strip().split()
        elif cat_term:
            # Strip last word of category term
            _base_parts = [w for w in effective_query.split() if w.lower() not in cat_term.lower().split()]
        else:
            _base_parts = effective_query.split()

        # Save session
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        supplier_names = [p.name for p in providers]
        session = SearchSession(
            query=fallback_used or query_str,
            suppliers=supplier_names,
            created_at=now,
            last_updated=now,
            original_results=all_results,
            active_result_ids=[r.result_id for r in all_results],
            provider_offsets=provider_offsets,
            # Ref context for expand
            ref_name=ref_name,
            base_query_parts=_base_parts,
            category=cat,
        )
        session._has_more_remote = has_more_remote

        # Store provider_total_available from initial search
        for pname, total in _provider_total_available.items():
            if total and total > len(all_results):
                session.provider_status[pname] = {
                    "provider_total_available": total,
                    "fetched_unique": len(all_results),
                    "pagination_status": "unverified",
                }

        mgr = _get_session_manager()
        mgr.save(session)

        if mini:
            table = render_mini_table(
                all_results, query=fallback_used or query_str,
                status_line=session.get_status_line(),
            )
        else:
            table = render_search_table(all_results, query=fallback_used or query_str)
        console.print(table)

    # -------------------------------------------------------------------
    # Pagination commands (M9.3)
    # -------------------------------------------------------------------

    @supplier_app.command("more")
    def supplier_more_cmd(
        source: str = typer.Argument(".", help="Session source (always '.')."),
        mini: bool = typer.Option(True, "--mini/--full", "--min", "-q/-Q", help="Compact output."),
        debug: bool = typer.Option(False, "--debug", "-d", help="Debug pagination."),
    ) -> None:
        """Show next page of results from the active search session.

        If at the last local page, fetches more results from the provider.
        """
        mgr = _get_session_manager()
        session = mgr.load()
        if not session:
            console.print("[yellow]No active search session. Run a search first.[/yellow]")
            raise typer.Exit(1)

        if debug:
            console.print(f"[dim]query: {session.query}[/dim]")
            console.print(f"[dim]page_size (limit): {session.page_size}[/dim]")
            console.print(f"[dim]current_page: {session.current_page}[/dim]")
            console.print(f"[dim]total_pages: {session.total_pages()}[/dim]")
            console.print(f"[dim]total_results: {len(session.original_results)}[/dim]")
            console.print(f"[dim]provider_offsets: {session.provider_offsets}[/dim]")
            console.print(f"[dim]session suppliers: {session.suppliers}[/dim]")

        if session.has_next_page():
            # We have more local results to show
            session.current_page += 1
        else:
            # At the end of local results — try to fetch more from provider
            fetched_more = False
            if session.provider_offsets:
                from footfindr.suppliers.base import SupplierSearchPage
                # Use session's suppliers list to resolve the correct providers
                sup_names = list(session.provider_offsets.keys())
                providers = _resolve_providers(
                    sup_names[0] if len(sup_names) == 1 else None,
                    ",".join(sup_names) if len(sup_names) > 1 else None,
                    False,
                )
                provider_map = {p.name: p for p in providers}

                if debug:
                    console.print(f"[dim]resolved providers: {list(provider_map.keys())}[/dim]")

                for sup_name, current_offset in session.provider_offsets.items():
                    provider = provider_map.get(sup_name)
                    if not provider:
                        if debug:
                            console.print(f"[dim]provider '{sup_name}' not found in resolved providers[/dim]")
                        continue

                    console.print(f"[dim]Fetching more results from {sup_name} (offset={current_offset})...[/dim]")
                    if debug:
                        console.print(f"[dim]DigiKey request payload fields:[/dim]")
                        console.print(f"[dim]  Keywords: {session.query!r}[/dim]")
                        console.print(f"[dim]  RecordCount: {min(session.page_size, 50)}[/dim]")
                        console.print(f"[dim]  RecordStartPosition: {current_offset}[/dim]")
                        console.print(f"[dim]  ExcludeMarketPlaceProducts: True[/dim]")
                        cache_key = f"supplier={sup_name} query={session.query.strip().upper()!r} offset={current_offset}"
                        console.print(f"[dim]cache key (search cache only used for offset=0): {cache_key}[/dim]")
                    try:
                        search_result = provider.search(
                            session.query,
                            limit=session.page_size,
                            offset=current_offset,
                        )

                        if isinstance(search_result, SupplierSearchPage):
                            new_items = search_result.items
                            if debug:
                                console.print(f"[dim]provider total_available: {search_result.total_available}[/dim]")
                                console.print(f"[dim]provider has_more: {search_result.has_more}[/dim]")
                                console.print(f"[dim]provider offset returned: {search_result.offset}[/dim]")
                                console.print(f"[dim]provider limit returned: {search_result.limit}[/dim]")
                        else:
                            new_items = search_result

                        if debug:
                            console.print(f"[dim]raw returned count: {len(new_items)}[/dim]")
                            raw_mpns = [getattr(it, 'mpn', '?') for it in new_items[:15]]
                            console.print(f"[dim]raw MPNs (first 15): {raw_mpns}[/dim]")
                            raw_spns = [getattr(it, 'supplier_pn', '?') for it in new_items[:15]]
                            console.print(f"[dim]raw supplier PNs (first 15): {raw_spns}[/dim]")

                        # Deduplicate by (supplier, supplier_pn)
                        existing_keys = set()
                        for r in session.original_results:
                            key = (getattr(r, 'supplier', ''), getattr(r, 'supplier_pn', ''))
                            existing_keys.add(key)

                        if debug:
                            console.print(f"[dim]existing dedupe keys count: {len(existing_keys)}[/dim]")
                            # Show first 15 existing supplier PNs for comparison
                            existing_spns = [getattr(r, 'supplier_pn', '?') for r in session.original_results[:15]]
                            console.print(f"[dim]existing page-1 supplier PNs (first 15): {existing_spns}[/dim]")
                            # Check overlap
                            raw_key_set = {(getattr(it, 'supplier', ''), getattr(it, 'supplier_pn', '')) for it in new_items}
                            overlap = existing_keys & raw_key_set
                            console.print(f"[dim]raw vs existing overlap: {len(overlap)} of {len(raw_key_set)} raw items are duplicates[/dim]")

                        unique_new = []
                        for item in new_items:
                            key = (getattr(item, 'supplier', ''), getattr(item, 'supplier_pn', ''))
                            if key not in existing_keys:
                                unique_new.append(item)
                                existing_keys.add(key)

                        if debug:
                            console.print(f"[dim]new unique count: {len(unique_new)}[/dim]")
                            if not unique_new and new_items:
                                console.print(f"[yellow]WARNING: DigiKey returned {len(new_items)} items for offset={current_offset} but ALL are duplicates of page-1.[/yellow]")
                                console.print(f"[yellow]This means DigiKey API may be ignoring RecordStartPosition, or returning relevance-ranked results that overlap.[/yellow]")

                        if unique_new:
                            session.original_results.extend(unique_new)
                            session.active_result_ids.extend([r.result_id for r in unique_new])
                            session.provider_offsets[sup_name] = current_offset + len(new_items)
                            session.current_page += 1
                            fetched_more = True
                            console.print(f"  [green]{len(unique_new)} new results fetched[/green]")
                        else:
                            # Mark pagination as degraded for this provider
                            total_avail = None
                            if isinstance(search_result, SupplierSearchPage):
                                total_avail = search_result.total_available
                            session.pagination_status = "degraded"
                            session.provider_status[sup_name] = {
                                "provider_total_available": total_avail,
                                "fetched_unique": len(session.original_results),
                                "pagination_status": "degraded_duplicate_page",
                                "last_attempted_offset": current_offset,
                                "last_raw_count": len(new_items),
                                "last_new_unique_count": 0,
                            }
                            if total_avail:
                                console.print(
                                    f"\n[yellow]{sup_name} reports {total_avail} total matches, "
                                    f"but returned a duplicate page for offset={current_offset}.[/yellow]"
                                )
                                console.print(
                                    "[yellow]Scrolling is degraded for this query.[/yellow]"
                                )
                            else:
                                console.print(f"  [dim]No new unique results from {sup_name}[/dim]")
                            console.print(
                                "[dim]Use exact MPN/DigiKey PN search or narrow the query, "
                                "then add with --add-to-session.[/dim]"
                            )
                    except Exception as e:
                        console.print(f"  [red]Failed to fetch more from {sup_name}: {e}[/red]")

            if not fetched_more and session.pagination_status != "degraded":
                console.print("[yellow]No more results available.[/yellow]")

        mgr.save(session)

        page_results = session.get_current_page()
        if not page_results:
            console.print("[yellow]No results on this page.[/yellow]")
            return

        console.print(f"\n[dim]{session.get_page_status_line()}[/dim]")
        if mini:
            table = render_mini_table(
                page_results, query=session.query,
                status_line=session.get_status_line(),
            )
        else:
            table = render_search_table(page_results, query=session.query)
        console.print(table)

        # Footer: do NOT suggest 'more' for provider scrolling if degraded
        if session.pagination_status == "degraded":
            console.print(
                "[dim]Provider pagination is degraded. Showing locally fetched/expanded results.\n"
                "Use:\n"
                "  ff sup expand .     (runs narrower queries to gather more unique candidates)\n"
                "  ff sup search <exact> --supplier <s> --refresh --add-to-session[/dim]"
            )
        elif session.has_next_page():
            console.print(f"[dim]Use 'ff sup more .' for next page[/dim]")
        else:
            console.print(f"[dim]Try 'ff sup more .' to fetch more from provider[/dim]")

    @supplier_app.command("next")
    def supplier_next_cmd(
        source: str = typer.Argument(".", help="Session source."),
        mini: bool = typer.Option(True, "--mini/--full", "--min", "-q/-Q", help="Compact output."),
    ) -> None:
        """Move to next page (alias for 'more')."""
        supplier_more_cmd(source, mini)

    @supplier_app.command("prev")
    def supplier_prev_cmd(
        source: str = typer.Argument(".", help="Session source."),
        mini: bool = typer.Option(True, "--mini/--full", "--min", "-q/-Q", help="Compact output."),
    ) -> None:
        """Move to previous page of results."""
        mgr = _get_session_manager()
        session = mgr.load()
        if not session:
            console.print("[yellow]No active search session.[/yellow]")
            raise typer.Exit(1)

        if session.has_prev_page():
            session.current_page -= 1
        else:
            console.print("[yellow]Already on the first page.[/yellow]")

        mgr.save(session)

        page_results = session.get_current_page()
        console.print(f"\n[dim]{session.get_page_status_line()}[/dim]")
        if mini:
            table = render_mini_table(
                page_results, query=session.query,
                status_line=session.get_status_line(),
            )
        else:
            table = render_search_table(page_results, query=session.query)
        console.print(table)

    @supplier_app.command("page")
    def supplier_page_cmd(
        source: str = typer.Argument(".", help="Session source."),
        page_num: int = typer.Argument(..., help="Page number (1-based)."),
        mini: bool = typer.Option(True, "--mini/--full", "--min", "-q/-Q", help="Compact output."),
    ) -> None:
        """Jump to a specific page of results."""
        mgr = _get_session_manager()
        session = mgr.load()
        if not session:
            console.print("[yellow]No active search session.[/yellow]")
            raise typer.Exit(1)

        total = session.total_pages()
        if page_num < 1 or page_num > total:
            console.print(f"[yellow]Page {page_num} out of range (1-{total}).[/yellow]")
            raise typer.Exit(1)

        session.current_page = page_num
        mgr.save(session)

        page_results = session.get_current_page()
        console.print(f"\n[dim]{session.get_page_status_line()}[/dim]")
        if mini:
            table = render_mini_table(
                page_results, query=session.query,
                status_line=session.get_status_line(),
            )
        else:
            table = render_search_table(page_results, query=session.query)
        console.print(table)

    # -------------------------------------------------------------------
    # Expand command (M9.3c) — sharded search to work around degraded pagination
    # -------------------------------------------------------------------

    @supplier_app.command("expand")
    def supplier_expand_cmd(
        source: str = typer.Argument(".", help="Session source (always '.')."),
        supplier: Optional[str] = typer.Option(None, "--supplier", "-s", help="Specific supplier."),
        strategy: str = typer.Option("auto", "--strategy", help="Shard strategy: auto, manufacturer, dielectric."),
        max_queries: int = typer.Option(20, "--max-queries", help="Maximum expansion queries to run."),
        max_results: int = typer.Option(100, "--max-results", help="Stop after this many total unique results."),
        mini: bool = typer.Option(True, "--mini/--full", "--min", "-q/-Q", help="Compact output."),
        debug: bool = typer.Option(False, "--debug", "-d", help="Show per-shard details (human-readable)."),
        trace_http: bool = typer.Option(False, "--trace-http", help="Enable httpcore/httpx wire-level logs."),
    ) -> None:
        """Expand search results by running narrower shard queries.

        Works around DigiKey degraded pagination by running multiple targeted
        searches (by manufacturer, dielectric, etc.) and merging unique results.
        """
        if trace_http:
            logging.basicConfig(level=logging.DEBUG)

        mgr = _get_session_manager()
        session = mgr.load()
        if not session:
            console.print("[yellow]No active search session.[/yellow]")
            console.print("[dim]Run a search first: ff sup sf <ref> -s dk -r --min[/dim]")
            raise typer.Exit(1)

        # Generate shards
        shards = _generate_expansion_shards(session, strategy=strategy, max_queries=max_queries)
        if not shards:
            console.print("[yellow]Could not generate expansion queries.[/yellow]")
            if not session.base_query_parts:
                console.print("[dim]Session has no base query parts. Try: ff sup sf <ref> -s dk -r --min[/dim]")
            raise typer.Exit(1)

        console.print(f"\n[bold cyan]Expanding search: {session.query}[/bold cyan]")
        console.print(f"[dim]Strategy: {strategy} | Max queries: {max_queries} | Max results: {max_results}[/dim]")
        if session.ref_name:
            console.print(f"[dim]Ref: {session.ref_name} | Category: {session.category or '?'}[/dim]")
        console.print(f"[dim]Generated {len(shards)} shard queries[/dim]\n")

        if debug:
            for i, shard in enumerate(shards):
                console.print(f"[dim]  Shard {i+1}: {shard}[/dim]")
            console.print()

        # Load constraints for the ref (if available)
        constraints = []
        if session.ref_name:
            try:
                from footfindr.constraints import ConstraintManager, apply_constraints_to_results
                cmgr = ConstraintManager()
                constraints = cmgr.get_constraints_for(session.ref_name)
            except Exception:
                pass

        # Resolve providers
        providers = _resolve_providers(supplier, None, False) if supplier else []
        if not providers and session.suppliers:
            providers = _resolve_providers(session.suppliers[0], None, False)

        if not providers:
            console.print("[red]No suppliers resolved. Use --supplier to specify one.[/red]")
            raise typer.Exit(1)

        from footfindr.suppliers.base import SupplierSearchPage

        starting_count = len(session.original_results)
        total_merged = 0
        total_skipped = 0
        queries_run = 0
        failed_shards: list[tuple[str, str]] = []

        for i, shard in enumerate(shards):
            # Check if we've hit the max_results target
            if len(session.original_results) >= max_results:
                console.print(f"\n[green]Reached max_results ({max_results}). Stopping expansion.[/green]")
                break

            queries_run += 1

            for provider in providers:
                try:
                    search_result = provider.search(shard, limit=10)
                    raw_items = search_result.items if hasattr(search_result, "items") else search_result
                    raw_count = len(raw_items)

                    # Apply constraints before merge (if available)
                    if constraints:
                        from footfindr.constraints import apply_constraints_to_results
                        passing, _ = apply_constraints_to_results(constraints, raw_items)
                    else:
                        # No constraints — take all valid items
                        passing = [r for r in raw_items if r.is_valid()]

                    pass_count = len(passing)

                    # Merge unique passing results
                    merged, skipped = _merge_results_into_session(session, passing)
                    total_merged += merged
                    total_skipped += skipped

                    if debug or merged > 0:
                        console.print(
                            f"  Shard {i+1}/{len(shards)}: {shard}\n"
                            f"    raw: {raw_count}, pass constraints: {pass_count}, new unique: {merged}"
                        )
                    elif queries_run <= 3 or queries_run % 5 == 0:
                        # Progress indicator for non-debug
                        console.print(f"  Shard {i+1}/{len(shards)}: +{merged} new", end="\r")

                except Exception as e:
                    failed_shards.append((shard, str(e)))
                    console.print(f"  [red]Shard {i+1}/{len(shards)}: FAIL ({e})[/red]")
                    # Keep going — don't lose already-merged results

        # Update expansion metadata on session
        session.expanded = True
        session.expansion_strategy = strategy
        session.expansion_queries_run = queries_run
        session.expansion_new_results = total_merged

        mgr.save(session)

        # Summary
        final_count = len(session.original_results)
        console.print(
            f"\n[bold green]Expanded from {starting_count} to {final_count} unique results "
            f"across {queries_run} queries[/bold green]"
        )
        if total_merged == 0:
            console.print(
                "[yellow]No new unique results found. All shard queries returned duplicates "
                "or results that failed constraints.[/yellow]"
            )
            if debug:
                console.print("[dim]This means DigiKey returned the same top results for all narrower queries.[/dim]")
        if failed_shards:
            console.print(f"[yellow]{len(failed_shards)} shard(s) failed (results from other shards preserved)[/yellow]")
            if debug:
                for shard, err in failed_shards:
                    console.print(f"  [dim]FAIL: {shard}: {err}[/dim]")

        # Display updated results
        display_results = session.get_active_results()
        if mini:
            table = render_mini_table(
                display_results, query=session.query,
                status_line=session.get_status_line(),
            )
        else:
            table = render_search_table(display_results, query=session.query)
        console.print(table)
        console.print(f"\n[dim]{len(display_results)} results in active session[/dim]")

    # -------------------------------------------------------------------
    # Debug pagination probe (M9.3c diagnostics)
    # -------------------------------------------------------------------

    @supplier_app.command("debug-pagination")
    def supplier_debug_pagination_cmd(
        supplier_name: str = typer.Argument(..., help="Supplier to probe (e.g. digikey)."),
        query: str = typer.Argument(..., help="Search query to test pagination with."),
        trace_http: bool = typer.Option(False, "--trace-http", help="Enable httpcore/httpx wire-level logs."),
    ) -> None:
        """Probe supplier pagination to determine if offset scrolling works.

        Sends multiple requests at different offsets and record counts,
        then reports overlap between pages and whether native pagination
        is usable or degraded.
        """
        if trace_http:
            logging.basicConfig(level=logging.DEBUG)

        from footfindr.suppliers.base import SupplierSearchPage

        providers = _resolve_providers(supplier_name, None, False)
        if not providers:
            console.print(f"[red]No provider found for: {supplier_name}[/red]")
            raise typer.Exit(1)

        provider = providers[0]
        console.print(f"\n[bold cyan]Pagination probe: {provider.name}[/bold cyan]")
        console.print(f"[dim]Query: {query}[/dim]")

        # Report auth mode
        auth_mode = getattr(provider, "_auth_mode", "unknown")
        console.print(f"[dim]Auth mode: {auth_mode}[/dim]")
        console.print()

        # Define probe configurations: (record_count, offset, label)
        probes = [
            (10, 0, "Page 1 (count=10, offset=0)"),
            (10, 10, "Page 2 (count=10, offset=10)"),
            (10, 20, "Page 3 (count=10, offset=20)"),
            (25, 0, "Wide page 1 (count=25, offset=0)"),
            (50, 0, "Full page (count=50, offset=0)"),
        ]

        probe_results: list[dict] = []

        for record_count, offset, label in probes:
            console.print(f"  [bold]{label}[/bold]")
            try:
                result = provider.search(query, limit=record_count, offset=offset)
                items = result.items if hasattr(result, "items") else result
                spns = [getattr(it, "supplier_pn", "") for it in items]
                mpns = [getattr(it, "mpn", "") for it in items]
                total = result.total_available if hasattr(result, "total_available") else None

                console.print(f"    returned: {len(items)} items")
                if total:
                    console.print(f"    total_available: {total}")
                console.print(f"    supplier PNs: {spns[:8]}{'...' if len(spns) > 8 else ''}")

                probe_results.append({
                    "label": label,
                    "count": record_count,
                    "offset": offset,
                    "items": len(items),
                    "total": total,
                    "spns": set(spns),
                    "mpns": set(mpns),
                })

            except Exception as e:
                console.print(f"    [red]FAILED: {e}[/red]")
                probe_results.append({
                    "label": label, "count": record_count, "offset": offset,
                    "items": 0, "total": None, "spns": set(), "mpns": set(),
                    "error": str(e),
                })

        # Overlap analysis
        console.print(f"\n[bold]Overlap analysis:[/bold]")

        if len(probe_results) >= 2:
            page1 = probe_results[0]
            page2 = probe_results[1]
            if page1["spns"] and page2["spns"]:
                overlap = page1["spns"] & page2["spns"]
                p2_unique = page2["spns"] - page1["spns"]
                console.print(f"  Page 1 vs Page 2 overlap: {len(overlap)}/{len(page2['spns'])} items are duplicates")
                console.print(f"  Page 2 unique items: {len(p2_unique)}")

        if len(probe_results) >= 3:
            page1 = probe_results[0]
            page3 = probe_results[2]
            if page1["spns"] and page3["spns"]:
                overlap = page1["spns"] & page3["spns"]
                console.print(f"  Page 1 vs Page 3 overlap: {len(overlap)}/{len(page3['spns'])} items are duplicates")

        if len(probe_results) >= 4:
            page1_10 = probe_results[0]
            page1_25 = probe_results[3]
            if page1_10["spns"] and page1_25["spns"]:
                extra = page1_25["spns"] - page1_10["spns"]
                console.print(f"  count=25 vs count=10: {len(extra)} additional unique items from larger page")

        if len(probe_results) >= 5:
            page1_10 = probe_results[0]
            page1_50 = probe_results[4]
            if page1_10["spns"] and page1_50["spns"]:
                extra = page1_50["spns"] - page1_10["spns"]
                console.print(f"  count=50 vs count=10: {len(extra)} additional unique items from larger page")

        # Verdict
        console.print(f"\n[bold]Verdict:[/bold]")
        if len(probe_results) >= 2 and probe_results[0]["spns"] and probe_results[1]["spns"]:
            overlap = probe_results[0]["spns"] & probe_results[1]["spns"]
            overlap_pct = len(overlap) / max(len(probe_results[1]["spns"]), 1) * 100

            if overlap_pct > 80:
                console.print(f"  [red]Native offset pagination: DEGRADED[/red]")
                console.print(f"  [dim]{overlap_pct:.0f}% overlap between page 1 and page 2[/dim]")
                console.print(f"  [dim]RecordStartPosition appears to be ignored by this provider.[/dim]")
                console.print(f"  [green]Workaround: ff sup expand . (sharded search)[/green]")
            elif overlap_pct > 30:
                console.print(f"  [yellow]Native offset pagination: PARTIALLY WORKING[/yellow]")
                console.print(f"  [dim]{overlap_pct:.0f}% overlap — some unique items on page 2, but significant duplication.[/dim]")
            else:
                console.print(f"  [green]Native offset pagination: WORKING[/green]")
                console.print(f"  [dim]{overlap_pct:.0f}% overlap — pages return mostly unique items.[/dim]")
        else:
            console.print(f"  [yellow]Could not determine pagination status (insufficient probe data).[/yellow]")

    # -------------------------------------------------------------------
    # Subcommand aliases (M8.7 + M9.3) — hidden short forms for human use
    # -------------------------------------------------------------------
    supplier_app.command("s", hidden=True)(supplier_search_cmd)
    supplier_app.command("filt", hidden=True)(supplier_filter_cmd)
    supplier_app.command("f", hidden=True)(supplier_filter_cmd)
    supplier_app.command("grp", hidden=True)(supplier_group_cmd)
    supplier_app.command("g", hidden=True)(supplier_group_cmd)
    supplier_app.command("ls", hidden=True)(supplier_list_cmd)
    supplier_app.command("sh", hidden=True)(supplier_show_cmd)
    supplier_app.command("fld", hidden=True)(supplier_fields_cmd)
    supplier_app.command("diff", hidden=True)(supplier_explain_diff_cmd)
    supplier_app.command("exp", hidden=True)(supplier_explain_cmd)
    supplier_app.command("ch", hidden=True)(supplier_choose_cmd)
    supplier_app.command("sf", hidden=True)(supplier_search_for_cmd)

    # Pagination aliases (M9.3)
    supplier_app.command("m", hidden=True)(supplier_more_cmd)
    supplier_app.command("n", hidden=True)(supplier_next_cmd)
    supplier_app.command("p", hidden=True)(supplier_prev_cmd)
    supplier_app.command("pg", hidden=True)(supplier_page_cmd)

    # Expand alias (M9.3c)
    supplier_app.command("ex", hidden=True)(supplier_expand_cmd)

    # Session sub-app already registered as "session"; add "sess" alias
    supplier_app.add_typer(session_app, name="sess", hidden=True)

    # Shortlist sub-app alias
    supplier_app.add_typer(shortlist_app, name="sl", hidden=True)

    # filter-clear aliases
    if hasattr(supplier_app, '_commands'):
        pass  # Typer handles this via Click
    try:
        supplier_app.command("fclear", hidden=True)(supplier_filter_clear_cmd)
        supplier_app.command("fc", hidden=True)(supplier_filter_clear_cmd)
    except Exception:
        pass  # filter-clear may have different registration name

    # lib promote-supplier aliases
    lib_app.command("psup", hidden=True)(lib_promote_supplier_cmd)
    lib_app.command("prom-sup", hidden=True)(lib_promote_supplier_cmd)

