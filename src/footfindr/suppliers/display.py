"""Rich display formatters for supplier search results.

All rendering logic lives here — the CLI commands are thin wrappers
that call these functions and print the output.

Key UX principles:
- Mini output is context-aware: hides redundant columns, shows command-relevant fields.
- Explicit --columns overrides all defaults.
- Named --view presets for common use cases.
- Status line shows active session state compactly.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from footfindr.suppliers.badges import (
    MPNGroup,
    Recommendation,
    compute_badges,
    extract_differentiators,
    group_by_mpn,
)
from footfindr.suppliers.models import SupplierPart

_console = Console()


# ---------------------------------------------------------------------------
# Badge formatting
# ---------------------------------------------------------------------------

_BADGE_STYLES = {
    "IN_STOCK": "green",
    "ACTIVE": "green",
    "LOW_STOCK": "yellow",
    "NRND": "yellow",
    "OBSOLETE": "red",
    "NO_DATASHEET": "dim",
    "NO_PRICE": "dim",
    "EXPENSIVE": "red",
    "JLC_AVAILABLE": "cyan",
    "FOOTPRINT_REVIEW": "dim",
    "LOW_RELEVANCE": "dim red",
}


def _format_badges(badges: list[str], *, compact: bool = False) -> str:
    """Format badges as a styled pipe-separated string."""
    if compact:
        # In mini mode, show only the most important badges
        priority = ["OBSOLETE", "NRND", "LOW_STOCK", "EXPENSIVE", "NO_PRICE", "LOW_RELEVANCE"]
        filtered = [b for b in badges if b in priority]
        return " | ".join(filtered) if filtered else ""
    return " | ".join(badges)


def _format_price(price: float | None, currency: str = "USD") -> str:
    if price is None:
        return "-"
    return f"${price:.4f}"


def _format_stock(stock: int | None) -> str:
    if stock is None:
        return "?"
    return f"{stock:,}"


def _format_price_breaks(
    part: SupplierPart,
    *,
    qty: int | None = None,
    max_breaks: int = 4,
) -> str:
    """Format price breaks, highlighting the qty-relevant break."""
    if not part.price_breaks:
        return "-"
    breaks = []
    for pb in part.price_breaks[:max_breaks]:
        s = f"{pb.quantity}+: ${pb.unit_price:.4f}"
        breaks.append(s)
    result = ", ".join(breaks)
    if len(part.price_breaks) > max_breaks:
        result += f" (+{len(part.price_breaks) - max_breaks} more)"
    return result


def _get_pkg(part: SupplierPart) -> str:
    """Get the best available package string for a part."""
    return part.package or part.supplier_device_package or part.attributes.get("Package / Case", "") or ""


# ---------------------------------------------------------------------------
# Column resolution for context-aware mini output
# ---------------------------------------------------------------------------

def _resolve_mini_columns(
    results: list[SupplierPart],
    *,
    context_fields: list[str] | None = None,
    columns: list[str] | None = None,
    view: str | None = None,
    qty: int | None = None,
) -> list[str]:
    """Determine which columns to show in mini mode.

    Priority:
    1. Explicit --columns overrides everything.
    2. Named --view provides a preset.
    3. Context-aware defaults: # + MPN + command-relevant field(s) + badges.
       Hides supplier/manufacturer when they are constant across results.
    """
    from footfindr.suppliers.session import NAMED_VIEWS

    # 1. Explicit columns
    if columns:
        return columns

    # 2. Named view
    if view and view in NAMED_VIEWS:
        return NAMED_VIEWS[view]

    # 3. Context-aware defaults
    cols: list[str] = ["mpn"]

    # Detect if supplier/manufacturer are constant
    suppliers = set(r.supplier for r in results)
    manufacturers = set((r.manufacturer or "").strip() for r in results)
    show_supplier = len(suppliers) > 1
    show_manufacturer = len(manufacturers) > 1

    if show_supplier:
        cols.append("supplier")

    # Add the command-relevant field(s) from sort/filter/group
    relevant = set(context_fields or [])

    # Show relevant fields (but not mpn/supplier/manufacturer if already shown)
    for f in (context_fields or []):
        if f not in cols and f not in ("mpn",):
            if f == "manufacturer" and not show_manufacturer:
                continue
            if f == "supplier" and not show_supplier:
                continue
            cols.append(f)

    # Default: show stock if no context fields, or if stock isn't already added
    if "stock" not in cols:
        cols.append("stock")

    # Add price if qty-aware pricing or price is a context field
    if qty and "price" not in cols:
        cols.append("price")

    # Always end with badges
    if "badges" not in cols:
        cols.append("badges")

    return cols


# ---------------------------------------------------------------------------
# Full search result table
# ---------------------------------------------------------------------------

def render_search_table(
    results: list[SupplierPart],
    *,
    qty: int | None = None,
    query: str = "",
) -> Table:
    """Render a full search result table with all columns."""
    table = Table(
        title=f"Supplier Search: {query}" if query else "Supplier Search Results",
        show_lines=True,
        expand=True,
    )

    table.add_column("#", style="dim", width=3)
    table.add_column("MPN", style="bold")
    table.add_column("Supplier")
    table.add_column("Manufacturer")
    table.add_column("Supplier PN")
    table.add_column("Description", max_width=40)
    table.add_column("Package")
    table.add_column("Stock", justify="right")
    if qty:
        table.add_column(f"Price @{qty}", justify="right")
        table.add_column(f"Ext @{qty}", justify="right")
    else:
        table.add_column("Best Price", justify="right")
    table.add_column("MOQ", justify="right")
    table.add_column("Lifecycle")
    table.add_column("Badges")

    for i, r in enumerate(results, 1):
        badges = r.badges or compute_badges(r, results, query=query)
        r.badges = badges

        pkg = _get_pkg(r)

        if qty:
            price = _format_price(r.best_price(qty))
            bp = r.best_price(qty)
            ext = f"${bp * qty:.2f}" if bp else "-"
        else:
            price = _format_price(r.best_price())
            ext = None

        row = [
            str(i),
            r.mpn or "",
            r.supplier,
            r.manufacturer or "",
            r.supplier_pn or "",
            (r.description or "")[:40],
            pkg[:20],
            _format_stock(r.stock),
            price,
        ]
        if qty:
            row.append(ext)
        row.extend([
            str(r.minimum_order_quantity) if r.minimum_order_quantity else "-",
            r.lifecycle or r.product_status or "-",
            _format_badges(badges),
        ])

        table.add_row(*row)

    return table


# ---------------------------------------------------------------------------
# Mini mode — context-aware
# ---------------------------------------------------------------------------

def render_mini_table(
    results: list[SupplierPart],
    *,
    context_fields: list[str] | None = None,
    columns: list[str] | None = None,
    view: str | None = None,
    qty: int | None = None,
    query: str = "",
    status_line: str | None = None,
) -> Table:
    """Render a compact mini table.

    Context-aware: adjusts columns based on what the user is doing.
    Hides redundant supplier/manufacturer when constant.
    Shows command-relevant fields (sort/filter/group field).
    """
    cols = _resolve_mini_columns(
        results,
        context_fields=context_fields,
        columns=columns,
        view=view,
        qty=qty,
    )

    # Build title from status line or query
    title = status_line or (f"Search: {query}" if query else None)

    table = Table(
        title=title,
        show_lines=False,
        box=None,
        pad_edge=False,
    )

    table.add_column("#", style="dim", width=3)

    # Add columns based on resolved column list
    for col in cols:
        if col == "mpn":
            table.add_column("MPN", style="bold")
        elif col == "supplier":
            table.add_column("Supplier", style="dim")
        elif col == "manufacturer":
            table.add_column("Manufacturer")
        elif col == "supplier_pn":
            table.add_column("Supplier PN")
        elif col == "description":
            table.add_column("Description", max_width=40)
        elif col == "package":
            table.add_column("Package")
        elif col == "supplier_device_package":
            table.add_column("Device Pkg")
        elif col == "stock":
            table.add_column("Stock", justify="right")
        elif col == "price":
            if qty:
                table.add_column(f"@{qty}", justify="right")
            else:
                table.add_column("Price", justify="right")
        elif col == "moq":
            table.add_column("MOQ", justify="right")
        elif col == "lifecycle":
            table.add_column("Lifecycle")
        elif col == "packaging":
            table.add_column("Packaging")
        elif col == "temperature_range":
            table.add_column("Temp")
        elif col == "mounting_type":
            table.add_column("Mounting")
        elif col == "lead_time":
            table.add_column("Lead")
        elif col == "badges":
            table.add_column("Badges")
        elif col == "datasheet_url":
            table.add_column("Datasheet")
        elif col == "product_url":
            table.add_column("URL")
        else:
            # Dynamic attribute column
            table.add_column(col[:20])

    for i, r in enumerate(results, 1):
        badges = r.badges or compute_badges(r, results, query=query)
        badge_str = _format_badges(badges, compact=True)

        row = [str(i)]

        for col in cols:
            if col == "mpn":
                row.append(r.mpn or "")
            elif col == "supplier":
                row.append(r.supplier)
            elif col == "manufacturer":
                row.append(r.manufacturer or "")
            elif col == "supplier_pn":
                row.append(r.supplier_pn or "")
            elif col == "description":
                row.append((r.description or "")[:40])
            elif col == "package":
                pkg = _get_pkg(r)
                row.append(pkg[:18] if pkg else "-")
            elif col == "supplier_device_package":
                row.append((r.supplier_device_package or "")[:18])
            elif col == "stock":
                row.append(_format_stock(r.stock))
            elif col == "price":
                row.append(_format_price(r.best_price(qty)))
            elif col == "moq":
                row.append(str(r.minimum_order_quantity) if r.minimum_order_quantity else "-")
            elif col == "lifecycle":
                row.append(r.lifecycle or "-")
            elif col == "packaging":
                row.append(r.packaging or "-")
            elif col == "temperature_range":
                row.append((r.temperature_range or "")[:16] or "-")
            elif col == "mounting_type":
                row.append(r.mounting_type or "-")
            elif col == "lead_time":
                row.append(str(r.lead_time) if r.lead_time else "-")
            elif col == "badges":
                row.append(badge_str)
            elif col == "datasheet_url":
                row.append("yes" if r.datasheet_url else "no")
            elif col == "product_url":
                row.append("yes" if r.product_url else "no")
            else:
                # Dynamic attribute
                row.append((r.attributes.get(col, "") or "")[:20])

        table.add_row(*row)

    return table


def render_part_numbers_only(results: list[SupplierPart]) -> str:
    """Ultra-minimal part-number-only output."""
    return "\n".join(r.mpn for r in results if r.mpn)


# ---------------------------------------------------------------------------
# Grouped view
# ---------------------------------------------------------------------------

def render_grouped(
    results: list[SupplierPart],
    field: str,
    *,
    mini: bool = True,
) -> str:
    """Render results grouped by a field."""
    # Get values and group
    groups: dict[str, list[tuple[int, SupplierPart]]] = {}
    for i, r in enumerate(results, 1):
        val = _get_group_value(r, field) or "(unknown)"
        groups.setdefault(val, []).append((i, r))

    lines: list[str] = []
    for group_name, items in sorted(groups.items()):
        lines.append(f"\n{group_name}:")
        for idx, part in items:
            if mini:
                pkg = part.package or part.supplier_device_package or ""
                stock = _format_stock(part.stock)
                lines.append(f"  {idx:>3}  {part.mpn:<30s} {part.supplier:<10s} {stock:>10s}")
            else:
                lines.append(f"  {idx:>3}  {part.mpn} ({part.supplier}) - {part.description or ''}")
    return "\n".join(lines)


def _get_group_value(part: SupplierPart, field: str) -> str:
    """Get the value of a field for grouping."""
    if field == "package":
        return part.package or part.supplier_device_package or part.attributes.get("Package / Case", "")
    elif field == "temperature_range":
        return part.temperature_range or part.attributes.get("Operating Temperature", "")
    elif field == "price":
        bp = part.best_price()
        return f"${bp:.4f}" if bp else "(no price)"
    elif field in (part.attributes or {}):
        return part.attributes.get(field, "")
    else:
        return str(getattr(part, field, "") or "")


# ---------------------------------------------------------------------------
# MPN-grouped view (supplier SKU collapse)
# ---------------------------------------------------------------------------

def render_mpn_grouped(groups: list[MPNGroup], *, mini: bool = True) -> str:
    """Render parts grouped by manufacturer MPN with supplier SKUs underneath."""
    lines: list[str] = []
    for g in groups:
        lines.append(f"\n{g.mpn}  ({g.manufacturer or '?'})")
        for v in g.variants:
            pkg = v.packaging or ""
            stock = _format_stock(v.stock)
            price = _format_price(v.best_price())
            spn = v.supplier_pn or ""
            lines.append(f"  - {v.supplier:<10s} {spn:<35s} {pkg:<16s} {stock:>10s}  {price}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

def render_comparison(
    parts: list[SupplierPart],
    *,
    qty: int | None = None,
) -> Table:
    """Side-by-side comparison of selected variants."""
    table = Table(title="Variant Comparison", show_lines=True)
    table.add_column("Field", style="bold")
    for p in parts:
        table.add_column(p.mpn or p.supplier_pn or "?")

    fields = [
        ("Supplier", lambda p: p.supplier),
        ("Manufacturer", lambda p: p.manufacturer or "-"),
        ("Supplier PN", lambda p: p.supplier_pn or "-"),
        ("Description", lambda p: (p.description or "-")[:50]),
        ("Package", lambda p: p.package or p.supplier_device_package or p.attributes.get("Package / Case", "-")),
        ("Mounting", lambda p: p.mounting_type or p.attributes.get("Mounting Type", "-")),
        ("Temp Range", lambda p: p.temperature_range or p.attributes.get("Operating Temperature", "-")),
        ("Stock", lambda p: _format_stock(p.stock)),
    ]

    if qty:
        fields.append((f"Price @{qty}", lambda p: _format_price(p.best_price(qty))))
        fields.append((f"Extended @{qty}", lambda p: f"${p.best_price(qty) * qty:.2f}" if p.best_price(qty) else "-"))
    else:
        fields.append(("Best Price", lambda p: _format_price(p.best_price())))

    fields.extend([
        ("Price Breaks", lambda p: _format_price_breaks(p, qty=qty, max_breaks=3)),
        ("MOQ", lambda p: str(p.minimum_order_quantity) if p.minimum_order_quantity else "-"),
        ("Lifecycle", lambda p: p.lifecycle or p.product_status or "-"),
        ("Packaging", lambda p: p.packaging or "-"),
        ("Lead Time", lambda p: p.lead_time or "-"),
        ("Datasheet", lambda p: "yes" if p.datasheet_url else "no"),
        ("Product URL", lambda p: "yes" if p.product_url else "no"),
        ("Badges", lambda p: " | ".join(compute_badges(p, parts))),
    ])

    for name, getter in fields:
        row = [name] + [getter(p) for p in parts]
        table.add_row(*row)

    return table


# ---------------------------------------------------------------------------
# Part detail view
# ---------------------------------------------------------------------------

def render_part_detail(
    part: SupplierPart,
    *,
    qty: int | None = None,
    show_attributes: bool = True,
) -> Table:
    """Render full detail view for a single part."""
    badges = compute_badges(part)
    table = Table(show_lines=True, title=f"Part Details: {part.mpn}")

    table.add_column("Field", style="bold", width=20)
    table.add_column("Value")

    rows = [
        ("Supplier", f"{part.supplier} [{'LIVE' if part.source == 'live' else part.source}]"),
        ("MPN", part.mpn or "-"),
        ("Manufacturer", part.manufacturer or "-"),
        ("Supplier PN", part.supplier_pn or "-"),
        ("Description", part.description or "-"),
        ("Package / Case", part.package or part.attributes.get("Package / Case", "-")),
        ("Supplier Device Pkg", part.supplier_device_package or "-"),
        ("Mounting Type", part.mounting_type or part.attributes.get("Mounting Type", "-")),
        ("Temperature Range", part.temperature_range or part.attributes.get("Operating Temperature", "-")),
        ("Stock", _format_stock(part.stock)),
    ]

    if qty:
        bp = part.best_price(qty)
        rows.append((f"Unit Price @{qty}", _format_price(bp)))
        if bp:
            rows.append((f"Extended @{qty}", f"${bp * qty:.2f}"))

    rows.extend([
        ("Price Breaks", _format_price_breaks(part, qty=qty)),
        ("MOQ", str(part.minimum_order_quantity) if part.minimum_order_quantity else "-"),
        ("Lifecycle", part.lifecycle or part.product_status or "-"),
        ("Packaging", part.packaging or "-"),
        ("Lead Time", str(part.lead_time) if part.lead_time else "-"),
        ("Datasheet", part.datasheet_url or "-"),
        ("Product URL", part.product_url or "-"),
        ("Badges", " | ".join(badges)),
        ("Last Checked", part.last_checked or "-"),
        ("Source", part.source),
    ])

    if part.lcsc_pn:
        rows.append(("LCSC PN", part.lcsc_pn))
    if part.jlc_category:
        rows.append(("JLC Category", part.jlc_category))

    for name, val in rows:
        table.add_row(name, str(val))

    # Attributes table
    if show_attributes and part.attributes:
        table.add_row("", "")
        table.add_row("[bold]Attributes[/bold]", "")
        for k, v in sorted(part.attributes.items()):
            if v and v != "-":
                table.add_row(f"  {k}", v)

    return table


# ---------------------------------------------------------------------------
# Field discovery display
# ---------------------------------------------------------------------------

def render_fields_table(fields: list) -> Table:
    """Render a field discovery table showing available fields and coverage."""
    table = Table(
        title="Available Fields",
        show_lines=False,
        box=None,
        pad_edge=False,
    )
    table.add_column("Field", style="bold")
    table.add_column("Aliases", style="dim")
    table.add_column("Coverage", justify="right")
    table.add_column("Source", style="dim")

    for fi in fields:
        aliases = ", ".join(fi.aliases) if fi.aliases else "-"
        source = "attribute" if fi.is_attribute else "standard"
        table.add_row(fi.canonical, aliases, fi.coverage, source)

    return table


# ---------------------------------------------------------------------------
# Recommendation display
# ---------------------------------------------------------------------------

def render_recommendations(recs: list[Recommendation]) -> str:
    """Format deterministic recommendations."""
    if not recs:
        return "No recommendations available."

    lines: list[str] = []
    for r in recs:
        cat_label = {
            "best_value": "Best value",
            "prototype": "Recommended for prototype",
            "compact": "Recommended for compact board",
        }.get(r.category, r.category)

        lines.append(f"\n{cat_label}:")
        lines.append(f"  {r.mpn} ({r.supplier})")
        lines.append(f"  Reason: {r.reason}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shortlist display
# ---------------------------------------------------------------------------

def render_shortlist(entries: list[dict]) -> Table:
    """Render shortlist entries as a table."""
    table = Table(title="Supplier Shortlist", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("MPN", style="bold")
    table.add_column("Supplier")
    table.add_column("Manufacturer")
    table.add_column("Package")
    table.add_column("Description", max_width=40)
    table.add_column("Added", style="dim")

    for i, e in enumerate(entries, 1):
        table.add_row(
            str(i),
            e.get("mpn", ""),
            e.get("supplier", ""),
            e.get("manufacturer", ""),
            e.get("package", ""),
            (e.get("description", "") or "")[:40],
            (e.get("added_at", "") or "")[:10],
        )

    return table


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_csv(results: list[SupplierPart], path: str, *, qty: int | None = None) -> int:
    """Export results to CSV. Returns count of rows written."""
    import csv

    fields = [
        "mpn", "supplier", "manufacturer", "supplier_pn", "description",
        "package", "mounting_type", "temperature_range", "stock",
        "best_price", "moq", "lifecycle", "packaging", "lead_time",
        "datasheet_url", "product_url", "badges",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            badges = compute_badges(r, results)
            writer.writerow({
                "mpn": r.mpn,
                "supplier": r.supplier,
                "manufacturer": r.manufacturer or "",
                "supplier_pn": r.supplier_pn or "",
                "description": r.description or "",
                "package": r.package or r.supplier_device_package or "",
                "mounting_type": r.mounting_type or "",
                "temperature_range": r.temperature_range or "",
                "stock": r.stock if r.stock is not None else "",
                "best_price": r.best_price(qty) if r.best_price(qty) else "",
                "moq": r.minimum_order_quantity or "",
                "lifecycle": r.lifecycle or "",
                "packaging": r.packaging or "",
                "lead_time": r.lead_time or "",
                "datasheet_url": r.datasheet_url or "",
                "product_url": r.product_url or "",
                "badges": " | ".join(badges),
            })
    return len(results)


def export_markdown(results: list[SupplierPart], path: str, *, qty: int | None = None) -> int:
    """Export results to Markdown table. Returns count of rows written."""
    headers = ["MPN", "Supplier", "Manufacturer", "Package", "Stock", "Price", "Lifecycle", "Badges"]

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    for r in results:
        badges = compute_badges(r, results)
        price = _format_price(r.best_price(qty))
        pkg = r.package or r.supplier_device_package or ""
        lines.append(
            f"| {r.mpn} | {r.supplier} | {r.manufacturer or ''} | "
            f"{pkg} | {_format_stock(r.stock)} | {price} | "
            f"{r.lifecycle or ''} | {' '.join(badges)} |"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(results)


# ---------------------------------------------------------------------------
# Explain-diff rendering (M8.6)
# ---------------------------------------------------------------------------

# Package rework/solderability contextual notes
_PACKAGE_NOTES: dict[str, str] = {
    "dfn": "DFN: no-lead, difficult rework, requires hot air/reflow",
    "qfn": "QFN: no-lead, difficult rework, requires hot air/reflow",
    "bga": "BGA: requires specialized rework (reballing), not hand-solderable",
    "wlcsp": "WLCSP: wafer-level CSP, very small, requires reflow",
    "msop": "MSOP: small pitch, hand-solderable with care",
    "soic": "SOIC: easy to hand-solder",
    "sop": "SOP: easy to hand-solder",
    "tssop": "TSSOP: small pitch, hand-solderable with care",
    "sot": "SOT: easy to hand-solder",
    "to-": "TO package: through-hole, easy to prototype",
    "dip": "DIP: through-hole, easy to hand-solder",
    "lga": "LGA: land grid array, difficult rework",
    "0201": "0201: extremely small, requires reflow, not hand-solderable",
    "0402": "0402: very small, difficult to hand-solder",
    "0603": "0603: small, hand-solderable with skill",
    "0805": "0805: standard size, easy to hand-solder",
    "1206": "1206: large, easy to hand-solder",
    "1210": "1210: large, easy to hand-solder",
    "2512": "2512: large power component, easy to hand-solder",
}


def _get_package_note(pkg: str) -> str | None:
    """Get contextual note for a package type."""
    if not pkg:
        return None
    lp = pkg.lower()
    for key, note in _PACKAGE_NOTES.items():
        if key in lp:
            return note
    return None


def _compare_field(parts: list[SupplierPart], field: str, getter) -> dict | None:
    """Compare a field across parts. Returns diff dict if values differ, None if same."""
    values = []
    for p in parts:
        val = getter(p)
        values.append(str(val) if val is not None and val != "" else "")

    # All same?
    if len(set(values)) <= 1:
        return None

    return {"field": field, "values": values}


def render_explain_diff(
    parts: list[SupplierPart],
    *,
    as_json: bool = False,
) -> str | dict | list:
    """Render engineering explanation of differences between parts.

    If as_json=True, returns a dict for JSON serialization.
    Otherwise returns a Rich-renderable string.
    """
    if not parts:
        return {"error": "No parts to compare"} if as_json else "No parts to compare"

    # Define comparison fields with getters
    fields = [
        ("MPN", lambda p: p.mpn),
        ("Supplier", lambda p: p.supplier),
        ("Supplier PN", lambda p: p.supplier_pn),
        ("Manufacturer", lambda p: p.manufacturer),
        ("Description", lambda p: p.description),
        ("Package", lambda p: p.package or getattr(p, "supplier_device_package", "")),
        ("Stock", lambda p: p.stock),
        ("Lifecycle", lambda p: getattr(p, "lifecycle", "")),
        ("Unit Price", lambda p: _format_price(p.best_price())),
        ("Price (100)", lambda p: _format_price(p.best_price(100))),
        ("Product URL", lambda p: getattr(p, "product_url", "")),
        ("Temperature Range", lambda p: getattr(p, "temperature_range", "")),
        ("Datasheet", lambda p: getattr(p, "datasheet_url", "")),
    ]

    # Add dynamic attributes
    all_attr_keys: set[str] = set()
    for p in parts:
        attrs = getattr(p, "attributes", {})
        if attrs:
            all_attr_keys.update(attrs.keys())

    for key in sorted(all_attr_keys):
        fields.append((key, lambda p, k=key: getattr(p, "attributes", {}).get(k, "")))

    # Compute diffs
    diffs = []
    shared = []
    for field_name, getter in fields:
        result = _compare_field(parts, field_name, getter)
        if result:
            diffs.append(result)
        else:
            values = [getter(p) for p in parts]
            val_str = str(values[0]) if values else ""
            if val_str:
                shared.append({"field": field_name, "value": val_str})

    # Package notes
    pkg_notes = []
    for p in parts:
        pkg = p.package or getattr(p, "supplier_device_package", "")
        note = _get_package_note(pkg)
        if note:
            pkg_notes.append({"mpn": p.mpn, "note": note})

    if as_json:
        return {
            "parts": [
                {"index": i + 1, "mpn": p.mpn, "supplier": p.supplier}
                for i, p in enumerate(parts)
            ],
            "differences": diffs,
            "shared": shared,
            "package_notes": pkg_notes,
        }

    # Rich output
    lines: list[str] = []
    lines.append("")

    # Header
    part_labels = [f"#{i+1} {p.mpn}" for i, p in enumerate(parts)]
    lines.append(f"[bold cyan]Comparing: {' vs '.join(part_labels)}[/bold cyan]")
    lines.append("")

    if diffs:
        lines.append("[bold]Differences:[/bold]")
        for d in diffs:
            lines.append(f"  [bold]{d['field']}[/bold]:")
            for i, val in enumerate(d["values"]):
                label = part_labels[i] if i < len(part_labels) else f"#{i+1}"
                lines.append(f"    {label}: {val or '[dim]—[/dim]'}")
        lines.append("")

    if pkg_notes:
        lines.append("[bold]Package / rework notes:[/bold]")
        for pn in pkg_notes:
            lines.append(f"  {pn['mpn']}: {pn['note']}")
        lines.append("")

    if shared:
        lines.append("[dim]Shared fields: " + ", ".join(s["field"] for s in shared[:10]))
        if len(shared) > 10:
            lines.append(f"  ... and {len(shared) - 10} more[/dim]")
        else:
            lines.append("[/dim]")

    return "\n".join(lines)

