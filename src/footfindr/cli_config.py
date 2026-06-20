"""CLI commands for user configuration.

Registers commands as ``ff config`` / ``ff cfg``.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

console = Console()

config_app = typer.Typer(help="FootFindr user configuration.")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key (dotted, e.g. kicad.root)."),
    value: str = typer.Argument(..., help="Value to set."),
) -> None:
    """Set a configuration value."""
    from footfindr.config import set_user_config_value

    set_user_config_value(key, value)
    console.print(f"[green]Set {key} = {value}[/green]")


@config_app.command("add")
def config_add(
    key: str = typer.Argument(..., help="Config key (dotted, e.g. kicad.root)."),
    value: str = typer.Argument(..., help="Value to add."),
) -> None:
    """Add a value to a list config key."""
    from footfindr.config import load_user_config, save_user_config
    from pathlib import Path

    cfg = load_user_config()

    # Handle kicad.root / kicad.roots specially
    if key in ("kicad.root", "kicad.roots"):
        from footfindr.config import add_kicad_root
        add_kicad_root(value)
        console.print(f"[green]Added KiCad root: {Path(value).resolve()}[/green]")
        return

    # Generic list append
    parts = key.split(".")
    current = cfg
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    final = parts[-1]
    existing = current.get(final, [])
    if isinstance(existing, list):
        if value not in existing:
            existing.append(value)
        current[final] = existing
    else:
        current[final] = [existing, value] if existing else [value]

    save_user_config(cfg)
    console.print(f"[green]Added {value} to {key}[/green]")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config key (dotted, e.g. kicad.root)."),
    as_json: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Get a configuration value."""
    from footfindr.config import get_user_config_value

    # Handle kicad.root as alias
    if key == "kicad.root":
        key = "kicad.roots"

    value = get_user_config_value(key)
    if value is None:
        console.print(f"[yellow]{key}: (not set)[/yellow]")
        return

    if as_json:
        console.print_json(json.dumps({key: value}))
    elif isinstance(value, list):
        console.print(f"[bold]{key}:[/bold]")
        for v in value:
            console.print(f"  - {v}")
    else:
        console.print(f"{key} = {value}")


@config_app.command("list")
def config_list(
    as_json: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """List all configuration values."""
    from footfindr.config import load_user_config, get_user_config_path

    cfg = load_user_config()
    config_path = get_user_config_path()

    if as_json:
        console.print_json(json.dumps({
            "config_path": str(config_path),
            "values": cfg,
        }))
        return

    console.print(f"\n[bold cyan]FootFindr Configuration[/bold cyan]")
    console.print(f"  Config file: [dim]{config_path}[/dim]")
    console.print()

    if not cfg:
        console.print("[yellow]No configuration values set.[/yellow]")
        console.print("  Run: ff config set kicad.root <path>")
        return

    _print_config_tree(cfg, indent=0)


def _print_config_tree(data: dict, indent: int = 0) -> None:
    """Print a nested dict as a tree."""
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            console.print(f"{prefix}[bold]{key}:[/bold]")
            _print_config_tree(value, indent + 1)
        elif isinstance(value, list):
            console.print(f"{prefix}[bold]{key}:[/bold]")
            for item in value:
                console.print(f"{prefix}  - {item}")
        else:
            console.print(f"{prefix}{key}: {value}")
