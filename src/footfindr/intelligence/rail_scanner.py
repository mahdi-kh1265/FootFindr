"""Power rail voltage inference (M9.4A).

Scans net names from the net graph and infers rail voltages using
deterministic regex patterns.  Conservative — does not overclaim
ambiguous rails like VCC or VDDA.

Rules:
    +3V3, +5V, +12V, VDD_1V8 -> infer voltage
    GND, AGND, DGND, PGND    -> 0V ground family
    VBUS                      -> 5V medium confidence
    VCC, VDDA, AVDD           -> unknown unless user override/alias

Supports user overrides via ``ff rails set`` and aliases via
``ff rails alias``, persisted in ``.footfindr/intelligence/rails.yaml``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from footfindr.intelligence.models import Fact, RailInfo
from footfindr.intelligence.net_graph import NetGraph

logger = logging.getLogger("footfindr.intelligence.rail_scanner")


# ---------------------------------------------------------------------------
# Ground-family net names
# ---------------------------------------------------------------------------

_GROUND_NETS = frozenset({
    "GND", "AGND", "DGND", "PGND", "SGND", "GNDA", "GNDD",
    "VSS", "AVSS", "DVSS", "GNDPWR", "EARTH",
})


# ---------------------------------------------------------------------------
# Voltage inference patterns (ordered by specificity)
# ---------------------------------------------------------------------------

# Each entry: (compiled regex, voltage_extractor, confidence, source_desc)
_VOLTAGE_PATTERNS: list[tuple[re.Pattern, Any, float, str]] = [
    # +3V3 -> 3.3V
    (re.compile(r"^\+(\d+)V(\d+)$"), lambda m: float(f"{m.group(1)}.{m.group(2)}"), 0.95, "net-name"),
    # +5V -> 5.0V
    (re.compile(r"^\+(\d+(?:\.\d+)?)V$"), lambda m: float(m.group(1)), 0.95, "net-name"),
    # +12V0 -> 12.0V (variant)
    (re.compile(r"^\+(\d+)V0$"), lambda m: float(m.group(1)), 0.95, "net-name"),
    # VDD_1V8 -> 1.8V
    (re.compile(r"^VDD[_]?(\d+)V(\d+)$", re.IGNORECASE),
     lambda m: float(f"{m.group(1)}.{m.group(2)}"), 0.90, "net-name"),
    # VDD_3V3 -> 3.3V
    (re.compile(r"^V[A-Z]*[_]?(\d+)V(\d+)$", re.IGNORECASE),
     lambda m: float(f"{m.group(1)}.{m.group(2)}"), 0.85, "net-name"),
    # VBUS -> 5.0V (USB convention, medium confidence)
    (re.compile(r"^VBUS$", re.IGNORECASE), lambda m: 5.0, 0.70, "USB-name heuristic"),
    # VUSB -> 5.0V
    (re.compile(r"^VUSB$", re.IGNORECASE), lambda m: 5.0, 0.70, "USB-name heuristic"),
]


def scan_rails(
    net_graph: NetGraph,
    *,
    workspace: Path | None = None,
) -> list[RailInfo]:
    """Scan all nets and infer rail voltages.

    Returns a list of RailInfo for all power/ground nets found.
    Applies user overrides and aliases from storage.
    """
    from footfindr.intelligence.storage import RailOverrideStore

    store = RailOverrideStore(workspace=workspace)
    overrides = store.get_overrides()
    aliases = store.get_aliases()

    all_net_names = net_graph.all_net_names()
    rails: list[RailInfo] = []
    seen: set[str] = set()

    for net_name in all_net_names:
        if net_name in seen:
            continue
        seen.add(net_name)

        rail = _infer_rail(net_name, overrides, aliases)
        if rail is not None:
            rails.append(rail)

    # Also check nets from overrides/aliases that might not be in the graph
    for net_name in overrides:
        if net_name not in seen:
            seen.add(net_name)
            rail = _infer_rail(net_name, overrides, aliases)
            if rail:
                rails.append(rail)

    # Sort: ground first, then by voltage, then by name
    rails.sort(key=lambda r: (
        0 if r.is_ground else 1,
        r.voltage if r.voltage is not None else 999,
        r.net,
    ))

    return rails


def scan_rails_from_names(net_names: list[str], **kwargs) -> list[RailInfo]:
    """Convenience: scan rails from a list of net names without a full NetGraph."""
    graph = NetGraph()
    for name in net_names:
        # Create dummy entries so all_net_names() returns them
        graph.nets[name] = []
    return scan_rails(graph, **kwargs)


def _infer_rail(
    net_name: str,
    overrides: dict[str, dict[str, Any]],
    aliases: dict[str, str],
) -> RailInfo | None:
    """Infer voltage for a single net name."""
    name = net_name.strip()
    if not name:
        return None

    # Check user override first
    if name in overrides:
        ov = overrides[name]
        return RailInfo(
            net=name,
            voltage=ov.get("voltage"),
            confidence=0.99,
            source="user-override",
            evidence=[f"User set {name} = {ov.get('voltage')}V"],
        )

    # Check alias — resolve to the target net
    if name in aliases:
        target = aliases[name]
        resolved = _infer_rail(target, overrides, {})
        if resolved:
            return RailInfo(
                net=name,
                voltage=resolved.voltage,
                confidence=min(resolved.confidence, 0.90),
                source=f"alias->{target}",
                evidence=[f"Alias: {name} -> {target}"] + resolved.evidence,
            )

    # Ground family
    upper = name.upper()
    if upper in _GROUND_NETS:
        return RailInfo(
            net=name,
            voltage=0.0,
            confidence=0.95,
            source="ground-symbol/name",
            evidence=[f"{name} matches ground-family pattern"],
        )

    # Voltage inference patterns
    for pattern, extractor, confidence, source in _VOLTAGE_PATTERNS:
        m = pattern.match(name)
        if m:
            try:
                voltage = extractor(m)
            except (ValueError, TypeError):
                continue
            return RailInfo(
                net=name,
                voltage=voltage,
                confidence=confidence,
                source=source,
                evidence=[f"Inferred {voltage}V from net name '{name}'"],
            )

    # Known power-like names with unknown voltage
    if _is_power_like_name(name):
        return RailInfo(
            net=name,
            voltage=None,
            confidence=0.50,
            source="power-symbol/name",
            evidence=[f"{name} looks like a power net but voltage is unknown"],
        )

    return None


def _is_power_like_name(name: str) -> bool:
    """Check if a net name looks like a power supply but has unknown voltage."""
    upper = name.upper()
    prefixes = ("VCC", "VDD", "VDDA", "AVDD", "DVDD", "PVDD",
                "VCCA", "VCCB", "VCCD", "VDDI", "VDDO",
                "V+", "V-", "VP", "VN", "VPWR")
    for p in prefixes:
        if upper == p or upper.startswith(p + "_"):
            return True
    return False
