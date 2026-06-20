"""FootFindr configuration loading and defaults.

Loads ``footfindr.yaml`` and exposes typed dataclasses for rails, policies,
package-footprint maps, and resolver settings.  The workspace path is kept
configurable — ``get_workspace()`` resolves the active workspace directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# ---------------------------------------------------------------------------
# Workspace resolution (configurable, not hard-coded)
# ---------------------------------------------------------------------------

_DEFAULT_WORKSPACE_NAME = ".footfindr"


def get_workspace(
    *,
    explicit: Optional[str | Path] = None,
    project_dir: Optional[str | Path] = None,
) -> Path:
    """Resolve the FootFindr workspace directory.

    Priority:
    1. Explicit path passed via CLI ``--workspace`` option.
    2. ``FOOTFINDR_WORKSPACE`` environment variable.
    3. ``<project_dir>/.footfindr/`` if project_dir given.
    4. ``<cwd>/.footfindr/`` as last resort.
    """
    if explicit:
        return Path(explicit)

    env = os.environ.get("FOOTFINDR_WORKSPACE")
    if env:
        return Path(env)

    base = Path(project_dir) if project_dir else Path.cwd()
    return base / _DEFAULT_WORKSPACE_NAME


# ---------------------------------------------------------------------------
# User-level configuration (~/.footfindr/config.yaml)
# ---------------------------------------------------------------------------

def get_user_config_dir() -> Path:
    """Return the user-level FootFindr config directory."""
    return Path.home() / ".footfindr"


def get_user_config_path() -> Path:
    """Return the user-level config file path."""
    return get_user_config_dir() / "config.yaml"


def load_user_config() -> dict:
    """Load user-level configuration.

    Returns empty dict if config file doesn't exist.
    """
    path = get_user_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data
    except (yaml.YAMLError, OSError):
        return {}


def save_user_config(data: dict) -> None:
    """Save user-level configuration."""
    path = get_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)


def get_kicad_roots() -> list[Path]:
    """Get configured KiCad project root directories."""
    cfg = load_user_config()
    kicad = cfg.get("kicad", {})
    roots_raw = kicad.get("roots", [])
    if isinstance(roots_raw, str):
        roots_raw = [roots_raw]
    return [Path(r) for r in roots_raw if r]


def add_kicad_root(root: str | Path) -> None:
    """Add a KiCad root to user configuration."""
    cfg = load_user_config()
    kicad = cfg.setdefault("kicad", {})
    roots = kicad.setdefault("roots", [])
    root_str = str(Path(root).resolve())
    if root_str not in roots:
        roots.append(root_str)
    save_user_config(cfg)


def set_kicad_root(root: str | Path) -> None:
    """Set a single KiCad root (replacing existing)."""
    cfg = load_user_config()
    kicad = cfg.setdefault("kicad", {})
    kicad["roots"] = [str(Path(root).resolve())]
    save_user_config(cfg)


def get_user_config_value(key: str) -> Any:
    """Get a dotted config key (e.g. 'kicad.roots')."""
    cfg = load_user_config()
    parts = key.split(".")
    current: Any = cfg
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def set_user_config_value(key: str, value: Any) -> None:
    """Set a dotted config key (e.g. 'kicad.root')."""
    cfg = load_user_config()
    parts = key.split(".")
    current = cfg
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    # Handle 'kicad.root' as alias for 'kicad.roots' (list)
    final_key = parts[-1]
    if final_key == "root" and parts[:-1] == ["kicad"]:
        # Treat as roots list
        existing = current.get("roots", [])
        if isinstance(existing, str):
            existing = [existing]
        if isinstance(value, str):
            current["roots"] = [value]
        else:
            current["roots"] = value
    else:
        current[final_key] = value
    save_user_config(cfg)


# ---------------------------------------------------------------------------
# Typed config structures
# ---------------------------------------------------------------------------

@dataclass
class RailConfig:
    """A named power rail."""
    voltage: float
    force_manual_review: bool = False


@dataclass
class CapacitorPolicy:
    """Policy for capacitor resolution."""
    voltage_derating: float = 2.0
    preferred_dielectrics: list[str] = field(default_factory=lambda: ["X7R", "X5R"])
    stock_min: int = 100
    package_policy: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResistorPolicy:
    """Policy for resistor resolution."""
    default_signal_package: str = "0603"
    default_tolerance: str = "1%"
    power_derating: float = 0.5
    voltage_derating: float = 0.5
    stock_min: int = 100


@dataclass
class ResolvePolicy:
    """Top-level resolver policy."""
    auto_apply_min_confidence: float = 0.92
    backup_on_apply: bool = True
    respect_locks: bool = True
    overwrite_existing_footprints: bool = False
    write_fields: list[str] = field(default_factory=lambda: [
        "Footprint", "InternalPN", "MPN", "Manufacturer", "Package",
        "VoltageRating", "PowerRating", "Tolerance", "Dielectric",
        "FootFindrStatus", "FootFindrConfidence", "FootFindrReason",
    ])


@dataclass
class FootFindrConfig:
    """Full FootFindr configuration, loaded from ``footfindr.yaml``."""
    version: str = "0.1"
    project_name: str | None = None
    schematic: str | None = None
    data_dir: str = "footfindr_data"
    parts_db: str = "footfindr.sqlite"
    approved_parts_file: str | None = None

    resolve: ResolvePolicy = field(default_factory=ResolvePolicy)
    capacitors: CapacitorPolicy = field(default_factory=CapacitorPolicy)
    resistors: ResistorPolicy = field(default_factory=ResistorPolicy)

    rails: dict[str, RailConfig] = field(default_factory=dict)
    high_risk_net_patterns: list[str] = field(default_factory=lambda: [
        "HV", "EOM", "RF", "CLK", "GATE", "SW", "RESET", "VPI", "PIEZO",
        "LASER", "BIAS", "SENSE",
    ])

    package_footprints: dict[str, dict[str, str]] = field(default_factory=dict)


def _default_config() -> FootFindrConfig:
    """Return a sensible default config when no file is found."""
    cfg = FootFindrConfig()
    cfg.rails = {
        "+3V3": RailConfig(voltage=3.3),
        "+5V": RailConfig(voltage=5.0),
        "+12V": RailConfig(voltage=12.0),
    }
    cfg.package_footprints = {
        "capacitor": {
            "0402": "Capacitor_SMD:C_0402_1005Metric",
            "0603": "Capacitor_SMD:C_0603_1608Metric",
            "0805": "Capacitor_SMD:C_0805_2012Metric",
            "1206": "Capacitor_SMD:C_1206_3216Metric",
        },
        "resistor": {
            "0402": "Resistor_SMD:R_0402_1005Metric",
            "0603": "Resistor_SMD:R_0603_1608Metric",
            "0805": "Resistor_SMD:R_0805_2012Metric",
            "1206": "Resistor_SMD:R_1206_3216Metric",
        },
    }
    return cfg


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def load_config(path: str | Path | None = None) -> FootFindrConfig:
    """Load FootFindr configuration from a YAML file.

    Falls back to defaults if the file is not found.
    """
    if path is None:
        candidates = [Path("footfindr.yaml"), Path("schemas/footfindr.yaml")]
        for c in candidates:
            if c.exists():
                path = c
                break
    if path is None or not Path(path).exists():
        return _default_config()

    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    cfg = _default_config()
    cfg.version = str(raw.get("version", cfg.version))

    project = raw.get("project", {})
    cfg.project_name = project.get("name")
    cfg.schematic = project.get("schematic")
    cfg.data_dir = project.get("data_dir", cfg.data_dir)
    cfg.parts_db = project.get("parts_db", cfg.parts_db)

    # Resolve policy
    res = raw.get("resolve", {})
    cfg.resolve.auto_apply_min_confidence = res.get(
        "auto_apply_min_confidence", cfg.resolve.auto_apply_min_confidence
    )
    cfg.resolve.backup_on_apply = res.get("backup_on_apply", cfg.resolve.backup_on_apply)
    cfg.resolve.respect_locks = res.get("respect_locks", cfg.resolve.respect_locks)
    cfg.resolve.overwrite_existing_footprints = res.get(
        "overwrite_existing_footprints", cfg.resolve.overwrite_existing_footprints
    )
    if "write_fields" in res:
        cfg.resolve.write_fields = res["write_fields"]

    # Rails
    rails_raw = raw.get("rails", {})
    for name, rd in rails_raw.items():
        if isinstance(rd, dict):
            cfg.rails[name] = RailConfig(
                voltage=float(rd.get("voltage", 0)),
                force_manual_review=rd.get("force_manual_review", False),
            )

    # Risk
    risk = raw.get("risk", {})
    if "high_risk_net_patterns" in risk:
        cfg.high_risk_net_patterns = risk["high_risk_net_patterns"]

    # Capacitor policy
    cap = raw.get("capacitors", {})
    cfg.capacitors.voltage_derating = cap.get("voltage_derating", cfg.capacitors.voltage_derating)
    cfg.capacitors.preferred_dielectrics = cap.get(
        "preferred_dielectrics", cfg.capacitors.preferred_dielectrics
    )

    # Resistor policy
    rp = raw.get("resistors", {})
    cfg.resistors.default_signal_package = rp.get(
        "default_signal_package", cfg.resistors.default_signal_package
    )
    cfg.resistors.power_derating = rp.get("power_derating", cfg.resistors.power_derating)

    # Package footprints
    pf = raw.get("package_footprints", {})
    if pf:
        cfg.package_footprints = pf

    return cfg
