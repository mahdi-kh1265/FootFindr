"""Net graph and connectivity extraction (M9.4A — union-find rewrite).

Uses a geometric union-find approach to build true connected components
from KiCad ``.kicad_sch`` S-expression data:

    1. Parse wire segments, symbol pins, labels, power ports, junctions.
    2. Quantize coordinates to a snap grid (0.01 mm).
    3. Build graph nodes for every geometric point.
    4. Union connected points:
       - same snapped position
       - both endpoints of a wire segment
       - pin point on a wire segment (point-on-segment test)
    5. Assign net names per connected component (label/power wins).
    6. Return every symbol pin → net mapping.

Architecture note:
    ``ConnectivityProvider`` interface preserved for later backends.
    This rewrite does NOT touch the existing schematic write path.
"""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footfindr.intelligence.models import NetConnection

logger = logging.getLogger("footfindr.intelligence.net_graph")


# ---------------------------------------------------------------------------
# PinCompleteness — passive topology analysis
# ---------------------------------------------------------------------------

@dataclass
class PinCompleteness:
    """Pin resolution completeness for a component."""
    ref: str
    expected_pins: int
    resolved_pins: int
    unresolved_pins: list[str] = field(default_factory=list)

    @property
    def completeness(self) -> float:
        if self.expected_pins <= 0:
            return 0.0
        return self.resolved_pins / self.expected_pins

    @property
    def is_complete(self) -> bool:
        return self.expected_pins > 0 and self.resolved_pins >= self.expected_pins

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "expected_pins": self.expected_pins,
            "resolved_pins": self.resolved_pins,
            "completeness": round(self.completeness, 3),
            "unresolved_pins": self.unresolved_pins,
        }


# ---------------------------------------------------------------------------
# NetGraph — the output of connectivity analysis
# ---------------------------------------------------------------------------

@dataclass
class NetGraph:
    """Graph of net connections extracted from a schematic.

    ``connections``: ref -> list of pin-to-net connections
    ``nets``:        net_name -> all connections on that net
    """
    connections: dict[str, list[NetConnection]] = field(default_factory=dict)
    nets: dict[str, list[NetConnection]] = field(default_factory=dict)
    _expected_pins: dict[str, int] = field(default_factory=dict)
    _debug_info: dict[str, list[str]] = field(default_factory=dict)

    def get_connections(self, ref: str) -> list[NetConnection]:
        """Get all net connections for a component."""
        return self.connections.get(ref, [])

    def get_neighbors(self, ref: str) -> dict[str, list[NetConnection]]:
        """Get all components sharing nets with the given ref."""
        result: dict[str, list[NetConnection]] = {}
        for conn in self.get_connections(ref):
            if conn.net:
                net_conns = self.nets.get(conn.net, [])
                others = [c for c in net_conns if c.ref != ref]
                if others:
                    result[conn.net] = others
        return result

    def all_net_names(self) -> list[str]:
        """Return all net names."""
        return sorted(self.nets.keys())

    def get_pin_completeness(self, ref: str) -> PinCompleteness:
        """Compute pin resolution completeness for a component."""
        expected = self._expected_pins.get(ref, 0)
        resolved = len(self.connections.get(ref, []))
        all_pin_nums = set()
        # Collect all expected pin numbers
        for conn in self.connections.get(ref, []):
            if conn.pin:
                all_pin_nums.add(conn.pin)
        # Determine unresolved
        expected_nums = set(str(i) for i in range(1, expected + 1))
        unresolved = sorted(expected_nums - all_pin_nums)
        return PinCompleteness(
            ref=ref,
            expected_pins=expected,
            resolved_pins=min(resolved, expected),
            unresolved_pins=unresolved,
        )

    def get_debug_info(self, ref: str) -> list[str]:
        """Get debug/justify info for a component."""
        return self._debug_info.get(ref, [])


# ---------------------------------------------------------------------------
# ConnectivityProvider abstraction
# ---------------------------------------------------------------------------

class ConnectivityProvider(ABC):
    """Abstract interface for extracting net connectivity."""

    @abstractmethod
    def build_net_graph(self, schematic_path: str | Path) -> NetGraph:
        """Build a NetGraph from a schematic or project."""
        ...

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Name of this connectivity backend."""
        ...


# ---------------------------------------------------------------------------
# Union-Find data structure
# ---------------------------------------------------------------------------

class _UnionFind:
    """Disjoint set with path compression and union by rank."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def connected(self, a: int, b: int) -> bool:
        return self.find(a) == self.find(b)


# ---------------------------------------------------------------------------
# Internal geometric graph node
# ---------------------------------------------------------------------------

@dataclass
class _GNode:
    """A point in the geometric connectivity graph."""
    x: float
    y: float
    node_type: str  # "wire", "pin", "label", "power", "junction"
    ref: str | None = None          # for pins: component ref
    pin_number: str | None = None   # for pins
    pin_name: str | None = None     # for pins: from lib_symbol (name ...) field
    is_ic_pin: bool = False         # True if lib_id is NOT Device:*/power:*
    net_name: str | None = None     # for labels / power ports
    label_type: str | None = None   # for labels: "label", "global_label", etc.
    lib_id: str | None = None       # for power ports


# ---------------------------------------------------------------------------
# Snap grid for coordinate matching
# ---------------------------------------------------------------------------

_SNAP_RESOLUTION = 0.01  # 0.01 mm — fine enough for KiCad


def _snap(v: float) -> int:
    """Quantize a coordinate to the snap grid, returning an integer key."""
    return round(v / _SNAP_RESOLUTION)


def _snap_key(x: float, y: float) -> tuple[int, int]:
    return (_snap(x), _snap(y))


# ---------------------------------------------------------------------------
# Point-on-segment test
# ---------------------------------------------------------------------------

def _point_on_segment(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float,
    tol: float = 0.05,
) -> bool:
    """Test if point (px,py) lies on segment (x1,y1)-(x2,y2) within tolerance.

    Uses cross-product for distance-to-line and parametric check for
    within-segment bounds.
    """
    dx, dy = x2 - x1, y2 - y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < tol * tol:
        # Degenerate segment — point-to-point distance
        return math.hypot(px - x1, py - y1) < tol

    # Perpendicular distance via cross product
    cross = abs((px - x1) * dy - (py - y1) * dx)
    seg_len = math.sqrt(seg_len_sq)
    dist = cross / seg_len
    if dist > tol:
        return False

    # Parametric position along segment
    t = ((px - x1) * dx + (py - y1) * dy) / seg_len_sq
    return -tol / seg_len <= t <= 1.0 + tol / seg_len


def _dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x1 - x2, y1 - y2)


# ---------------------------------------------------------------------------
# S-expression helpers
# ---------------------------------------------------------------------------

def _extract_float(node, index: int) -> float:
    if index < len(node.children):
        try:
            return float(node.children[index].value)
        except (ValueError, AttributeError):
            pass
    return 0.0


# ---------------------------------------------------------------------------
# KiCad S-expression connectivity provider (union-find rewrite)
# ---------------------------------------------------------------------------

class KiCadSexprConnectivityProvider(ConnectivityProvider):
    """Extract net connectivity from KiCad ``.kicad_sch`` using union-find.

    Builds a geometric graph of all wire endpoints, pin positions,
    label positions, and power port positions, then uses union-find
    to create connected components = nets.
    """

    @property
    def backend_name(self) -> str:
        return "kicad_sexpr"

    def build_net_graph(self, schematic_path: str | Path) -> NetGraph:
        """Parse a .kicad_sch and build connectivity via union-find."""
        from footfindr.kicad.schematic import parse_sexpr

        path = Path(schematic_path)
        if not path.exists():
            logger.warning(f"Schematic not found: {path}")
            return NetGraph()

        try:
            text = path.read_text(encoding="utf-8")
            tree = parse_sexpr(text)
        except Exception as e:
            logger.warning(f"Failed to parse schematic: {e}")
            return NetGraph()

        if not tree:
            return NetGraph()

        root = tree[0]

        # --- Phase 1: Extract raw geometry ---
        lib_pins = self._build_lib_pin_map(root)
        nodes: list[_GNode] = []
        wire_pairs: list[tuple[int, int]] = []  # (node_idx_a, node_idx_b) per wire
        expected_pins: dict[str, int] = {}

        self._extract_wire_nodes(root, nodes, wire_pairs)
        self._extract_placed_pins(root, lib_pins, nodes, expected_pins)
        self._extract_label_nodes(root, nodes)
        self._extract_power_port_nodes(root, lib_pins, nodes)
        self._extract_junction_nodes(root, nodes)

        # --- Phase 2: Build union-find ---
        n = len(nodes)
        if n == 0:
            return NetGraph()

        uf = _UnionFind(n)

        # 2a: Union wire endpoint pairs
        for a, b in wire_pairs:
            uf.union(a, b)

        # 2b: Build spatial index (snapped coords -> list of node indices)
        spatial: dict[tuple[int, int], list[int]] = {}
        for i, nd in enumerate(nodes):
            key = _snap_key(nd.x, nd.y)
            spatial.setdefault(key, []).append(i)

        # 2c: Union nodes at same snapped position
        for indices in spatial.values():
            if len(indices) > 1:
                first = indices[0]
                for j in indices[1:]:
                    uf.union(first, j)

        # 2d: Check pins on wire segments (point-on-segment)
        # Collect non-wire nodes that might sit on a wire body
        pin_and_label_indices = [
            i for i, nd in enumerate(nodes)
            if nd.node_type in ("pin", "label", "power")
        ]
        for i in pin_and_label_indices:
            nd = nodes[i]
            for wa, wb in wire_pairs:
                na, nb = nodes[wa], nodes[wb]
                if _point_on_segment(nd.x, nd.y, na.x, na.y, nb.x, nb.y, tol=0.05):
                    uf.union(i, wa)

        # --- Phase 3: Assign net names per connected component ---
        # Collect named nodes per component (from labels and power ports)
        comp_names: dict[int, list[str]] = {}
        for i, nd in enumerate(nodes):
            if nd.net_name:
                root_id = uf.find(i)
                comp_names.setdefault(root_id, []).append(nd.net_name)

        # Resolve: unique name wins, conflicts marked
        comp_net: dict[int, str] = {}       # root_id -> display name
        comp_internal: dict[int, str] = {}  # root_id -> internal N$X id
        comp_name_source: dict[int, str] = {}  # root_id -> name_source
        auto_id = 0
        for root_id, names in comp_names.items():
            unique = list(dict.fromkeys(names))  # deduplicate preserving order
            if len(unique) == 1:
                comp_net[root_id] = unique[0]
                comp_name_source[root_id] = "label_or_power_port"
            else:
                # Multiple names — use the first, log conflict
                comp_net[root_id] = unique[0]
                comp_name_source[root_id] = "label_or_power_port"
                logger.debug(f"Net name conflict in component {root_id}: {unique}")

        # Collect pin nodes per connected component for net name synthesis
        comp_pin_nodes: dict[int, list[_GNode]] = {}
        for i, nd in enumerate(nodes):
            if nd.node_type == "pin" and nd.ref:
                root_id = uf.find(i)
                comp_pin_nodes.setdefault(root_id, []).append(nd)

        # --- Phase 4: Build NetGraph ---
        graph = NetGraph(_expected_pins=expected_pins)
        debug_info: dict[str, list[str]] = {}

        for i, nd in enumerate(nodes):
            if nd.node_type != "pin" or not nd.ref:
                continue

            root_id = uf.find(i)
            net_name = comp_net.get(root_id)
            name_source = comp_name_source.get(root_id, "")
            internal_id = comp_internal.get(root_id)

            if net_name is None:
                # No label/power port — synthesize KiCad-style name
                internal_id = f"N${auto_id}"
                comp_internal[root_id] = internal_id
                auto_id += 1

                # Synthesize from pin context
                pin_nodes = comp_pin_nodes.get(root_id, [])
                synthesized = self._synthesize_net_name(pin_nodes)
                if synthesized:
                    net_name = synthesized
                    name_source = "synthesized_from_pin_context"
                else:
                    net_name = internal_id
                    name_source = "auto_generated"
                comp_net[root_id] = net_name
                comp_name_source[root_id] = name_source
            elif internal_id is None:
                # Named net — assign internal ID for debug
                internal_id = net_name
                comp_internal[root_id] = internal_id

            net_type = self._classify_net_type(net_name)
            conn = NetConnection(
                ref=nd.ref,
                pin=nd.pin_number,
                pin_name=nd.pin_name or None,
                net=net_name,
                net_type=net_type,
            )
            graph.connections.setdefault(nd.ref, []).append(conn)
            graph.nets.setdefault(net_name, []).append(conn)

            # Debug info — always shows both internal and display
            debug_info.setdefault(nd.ref, []).append(
                f"pin {nd.pin_number}: abs_pos=({nd.x:.2f}, {nd.y:.2f}) "
                f"-> component {root_id} -> "
                f"internal: {comp_internal.get(root_id, '?')}, "
                f"display: {net_name}, "
                f"source: {name_source}"
            )

        graph._debug_info = debug_info
        return graph

    # ----------------------------------------------------------------
    # Phase 1 extractors
    # ----------------------------------------------------------------

    def _build_lib_pin_map(self, root) -> dict[str, list[tuple[str, float, float, str]]]:
        """Build mapping: lib_symbol_name -> [(pin_number, dx, dy, pin_name), ...]"""
        lib_pins: dict[str, list[tuple[str, float, float, str]]] = {}
        for child in root.children:
            if child.tag == "lib_symbols":
                for sym_def in child.children:
                    if sym_def.tag == "symbol":
                        sym_name = sym_def.children[1].value if len(sym_def.children) > 1 else ""
                        self._collect_lib_pins_recursive(sym_def, sym_name, lib_pins)
        return lib_pins

    def _collect_lib_pins_recursive(
        self,
        node,
        symbol_name: str,
        out: dict[str, list[tuple[str, float, float, str]]],
    ) -> None:
        """Recursively collect pin definitions from lib_symbols.

        KiCad lib_symbols have sub-symbols like "C_0_1" (graphics) and
        "C_1_1" (pins for unit 1).  We store pins under multiple keys
        so lookup succeeds regardless of how the placed symbol references them.
        """
        for child in node.children:
            if child.kind != "list":
                continue
            if child.tag == "pin":
                pin_num = self._get_pin_number(child)
                px, py = self._get_pin_at(child)
                pin_name = self._get_pin_name(child)
                # Store under the full sub-symbol name
                out.setdefault(symbol_name, []).append((pin_num, px, py, pin_name))
                # Also store under the top-level lib name (e.g. "Device:C")
                # by extracting the base: "C_1_1" -> store under parent
            elif child.tag == "symbol":
                sub_name = child.children[1].value if len(child.children) > 1 else ""
                self._collect_lib_pins_recursive(child, sub_name, out)
                # Propagate sub-symbol pins up to the parent name
                if sub_name in out:
                    out.setdefault(symbol_name, []).extend(out[sub_name])

    def _get_pin_number(self, pin_node) -> str:
        for child in pin_node.children:
            if child.kind == "list" and child.tag == "number":
                if len(child.children) > 1:
                    return child.children[1].value
        return "?"

    def _get_pin_name(self, pin_node) -> str:
        """Extract pin name from the (name ...) child of a pin node."""
        for child in pin_node.children:
            if child.kind == "list" and child.tag == "name":
                if len(child.children) > 1:
                    name = child.children[1].value
                    # "~" means unnamed/anonymous in KiCad
                    return name if name != "~" else ""
        return ""

    def _get_pin_at(self, pin_node) -> tuple[float, float]:
        for child in pin_node.children:
            if child.kind == "list" and child.tag == "at":
                x = _extract_float(child, 1)
                y = _extract_float(child, 2)
                return x, y
        return 0.0, 0.0

    def _extract_wire_nodes(
        self, root, nodes: list[_GNode], wire_pairs: list[tuple[int, int]],
    ) -> None:
        """Extract wire segments as pairs of nodes."""
        for child in root.children:
            if child.kind != "list" or child.tag != "wire":
                continue
            pts = None
            for sub in child.children:
                if sub.kind == "list" and sub.tag == "pts":
                    pts = sub
            if pts and len(pts.children) >= 3:
                xy1 = pts.children[1]
                xy2 = pts.children[2]
                if xy1.tag == "xy" and xy2.tag == "xy":
                    x1 = _extract_float(xy1, 1)
                    y1 = _extract_float(xy1, 2)
                    x2 = _extract_float(xy2, 1)
                    y2 = _extract_float(xy2, 2)
                    idx_a = len(nodes)
                    nodes.append(_GNode(x=x1, y=y1, node_type="wire"))
                    idx_b = len(nodes)
                    nodes.append(_GNode(x=x2, y=y2, node_type="wire"))
                    wire_pairs.append((idx_a, idx_b))

    def _extract_placed_pins(
        self,
        root,
        lib_pins: dict[str, list[tuple[str, float, float, str]]],
        nodes: list[_GNode],
        expected_pins: dict[str, int],
    ) -> None:
        """Extract absolute pin positions for all placed symbols."""
        for child in root.children:
            if child.kind != "list" or child.tag != "symbol":
                continue
            # Skip lib_symbols subtree
            has_lib_id = any(
                c.tag == "lib_id" for c in child.children if c.kind == "list"
            )
            if not has_lib_id:
                continue
            self._extract_one_symbol_pins(child, lib_pins, nodes, expected_pins)

    @staticmethod
    def _is_ic_lib_id(lib_id: str) -> bool:
        """Return True if a lib_id is NOT a passive device or power symbol.

        IC/connector/active symbols get priority for net name synthesis.
        """
        if not lib_id:
            return False
        lib_lower = lib_id.lower()
        # Passive device symbols
        if lib_lower.startswith("device:"):
            return False
        # Power symbols
        if lib_lower.startswith("power:"):
            return False
        # Everything else is considered "active" (IC, regulator, connector, etc.)
        return True

    def _extract_one_symbol_pins(
        self, sym_node, lib_pins, nodes, expected_pins,
    ) -> None:
        """Extract absolute pin positions for a single placed symbol."""
        ref = ""
        lib_id = ""
        sym_x, sym_y, sym_rot = 0.0, 0.0, 0.0
        mirror_y = False

        for child in sym_node.children:
            if child.kind != "list":
                continue
            if child.tag == "lib_id" and len(child.children) > 1:
                lib_id = child.children[1].value
            elif child.tag == "at" and len(child.children) >= 3:
                sym_x = _extract_float(child, 1)
                sym_y = _extract_float(child, 2)
                if len(child.children) > 3:
                    sym_rot = _extract_float(child, 3)
            elif child.tag == "mirror" and len(child.children) > 1:
                if child.children[1].value == "y":
                    mirror_y = True
            elif child.tag == "property" and len(child.children) >= 3:
                if child.children[1].value == "Reference":
                    ref = child.children[2].value

        if not ref or ref.startswith("#"):
            # Skip power symbols (handled separately) and unnamed
            return

        # Determine if this is an IC/active component
        is_ic = self._is_ic_lib_id(lib_id)

        # Look up pin definitions
        pin_defs = self._find_pin_defs(lib_id, lib_pins)

        # Record expected pins
        if pin_defs:
            # Deduplicate pin numbers
            unique_pins = {pn for pn, _, _, _ in pin_defs}
            expected_pins[ref] = len(unique_pins)
        else:
            # Check for pin instances on the placed symbol
            pin_count = sum(
                1 for c in sym_node.children
                if c.kind == "list" and c.tag == "pin"
            )
            expected_pins[ref] = pin_count

        if pin_defs:
            # Compute absolute positions with transform
            rot_rad = math.radians(sym_rot)
            cos_r = math.cos(rot_rad)
            sin_r = math.sin(rot_rad)

            seen_pins: set[str] = set()
            for pin_num, dx, dy, pin_name in pin_defs:
                if pin_num in seen_pins:
                    continue
                seen_pins.add(pin_num)

                # Apply mirror
                if mirror_y:
                    dx = -dx

                # Apply rotation
                abs_x = sym_x + dx * cos_r - dy * sin_r
                abs_y = sym_y + dx * sin_r + dy * cos_r

                nodes.append(_GNode(
                    x=abs_x, y=abs_y, node_type="pin",
                    ref=ref, pin_number=pin_num,
                    pin_name=pin_name, is_ic_pin=is_ic,
                ))
        else:
            # No lib pin defs — record pins at symbol center (fallback)
            for child in sym_node.children:
                if child.kind == "list" and child.tag == "pin":
                    pin_num = child.children[1].value if len(child.children) > 1 else "?"
                    nodes.append(_GNode(
                        x=sym_x, y=sym_y, node_type="pin",
                        ref=ref, pin_number=pin_num,
                        is_ic_pin=is_ic,
                    ))

    def _find_pin_defs(
        self, lib_id: str, lib_pins: dict[str, list[tuple[str, float, float, str]]],
    ) -> list[tuple[str, float, float, str]]:
        """Find pin definitions for a lib_id, trying multiple key variants."""
        # Direct match: "Device:C"
        if lib_id in lib_pins:
            return lib_pins[lib_id]
        # Base name after colon: "C"
        base = lib_id.split(":")[-1] if ":" in lib_id else lib_id
        if base in lib_pins:
            return lib_pins[base]
        return []

    def _extract_label_nodes(self, root, nodes: list[_GNode]) -> None:
        """Extract label, global_label, and hierarchical_label nodes."""
        for child in root.children:
            if child.kind != "list":
                continue
            if child.tag in ("label", "global_label", "hierarchical_label"):
                name = child.children[1].value if len(child.children) > 1 else ""
                x, y = 0.0, 0.0
                for sub in child.children:
                    if sub.kind == "list" and sub.tag == "at" and len(sub.children) >= 3:
                        x = _extract_float(sub, 1)
                        y = _extract_float(sub, 2)
                if name:
                    nodes.append(_GNode(
                        x=x, y=y, node_type="label",
                        net_name=name, label_type=child.tag,
                    ))

    def _extract_power_port_nodes(
        self, root, lib_pins: dict[str, list[tuple[str, float, float]]],
        nodes: list[_GNode],
    ) -> None:
        """Extract power port symbol instances.

        Power symbols have lib_id from the ``power`` library.
        Their connection point is the pin position (usually pin "1" at offset).
        """
        for child in root.children:
            if child.kind != "list" or child.tag != "symbol":
                continue
            lib_id = ""
            value = ""
            x, y = 0.0, 0.0
            rot = 0.0
            for sub in child.children:
                if sub.kind != "list":
                    continue
                if sub.tag == "lib_id" and len(sub.children) > 1:
                    lib_id = sub.children[1].value
                elif sub.tag == "at" and len(sub.children) >= 3:
                    x = _extract_float(sub, 1)
                    y = _extract_float(sub, 2)
                    if len(sub.children) > 3:
                        rot = _extract_float(sub, 3)
                elif sub.tag == "property" and len(sub.children) >= 3:
                    if sub.children[1].value == "Value":
                        value = sub.children[2].value

            if not lib_id or not ("power" in lib_id.lower() or lib_id.startswith("power:")):
                continue

            net_name = value or lib_id.split(":")[-1]

            # Power port pin position: look up lib pin and transform
            pin_defs = self._find_pin_defs(lib_id, lib_pins)
            if pin_defs:
                # Use first pin's offset
                _, dx, dy, _ = pin_defs[0]
                rot_rad = math.radians(rot)
                cos_r = math.cos(rot_rad)
                sin_r = math.sin(rot_rad)
                px = x + dx * cos_r - dy * sin_r
                py = y + dx * sin_r + dy * cos_r
            else:
                # Fallback: power port pin is at the symbol origin
                px, py = x, y

            nodes.append(_GNode(
                x=px, y=py, node_type="power",
                net_name=net_name, lib_id=lib_id,
            ))

    def _extract_junction_nodes(self, root, nodes: list[_GNode]) -> None:
        """Extract junction nodes."""
        for child in root.children:
            if child.kind != "list" or child.tag != "junction":
                continue
            x, y = 0.0, 0.0
            for sub in child.children:
                if sub.kind == "list" and sub.tag == "at" and len(sub.children) >= 3:
                    x = _extract_float(sub, 1)
                    y = _extract_float(sub, 2)
            nodes.append(_GNode(x=x, y=y, node_type="junction"))

    # ----------------------------------------------------------------
    # Net name synthesis
    # ----------------------------------------------------------------

    @staticmethod
    def _synthesize_net_name(pin_nodes: list[_GNode]) -> str | None:
        """Synthesize a KiCad-style net name from pin context.

        Prefers IC/active pin names over passive pin names.
        Format: ``Net-(RefDes-PinName)``

        Returns None if no useful pin name can be found.
        """
        if not pin_nodes:
            return None

        # Partition into IC pins (named) and passive pins
        ic_named: list[tuple[str, str]] = []   # (ref, pin_name)
        passive_named: list[tuple[str, str]] = []  # (ref, pin_number)

        for nd in pin_nodes:
            if nd.is_ic_pin and nd.pin_name:
                ic_named.append((nd.ref or "?", nd.pin_name))
            elif nd.ref:
                passive_named.append((nd.ref or "?", nd.pin_number or "?"))

        # Prefer IC pin names
        if ic_named:
            ref, pin_name = ic_named[0]
            return f"Net-({ref}-{pin_name})"

        # Fallback to passive pin
        if passive_named:
            ref, pin_num = passive_named[0]
            return f"Net-({ref}-Pad{pin_num})"

        return None

    # ----------------------------------------------------------------
    # Net type classification
    # ----------------------------------------------------------------

    def _classify_net_type(self, net_name: str) -> str | None:
        """Classify a net name as power, ground, or signal."""
        name = net_name.strip()
        upper = name.upper()

        # Auto-generated net
        if name.startswith("N$"):
            return "signal"

        # Synthesized net names are signal-type
        if name.startswith("Net-("):
            return "signal"

        # Ground family
        if upper in ("GND", "AGND", "DGND", "PGND", "SGND", "GNDA", "GNDD",
                      "VSS", "AVSS", "DVSS"):
            return "ground"

        # Power patterns
        if re.match(r"^\+\d+V\d*$", name):
            return "power"
        if re.match(r"^V(DD|CC|IN|OUT|BUS|BAT)", upper):
            return "power"
        if name.startswith("+") or name.startswith("-"):
            return "power"

        return "signal"


# ---------------------------------------------------------------------------
# Default provider factory
# ---------------------------------------------------------------------------

def get_connectivity_provider() -> ConnectivityProvider:
    """Get the default connectivity provider."""
    return KiCadSexprConnectivityProvider()
