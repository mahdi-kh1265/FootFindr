"""KiCad schematic S-expression parser and reader.

Parses ``.kicad_sch`` files into a lightweight representation of symbols
and their properties.  The parser handles KiCad 7/8 format S-expressions
and tracks character positions so the field writer can do targeted edits.

This is a *read-only* module — it never modifies the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from footfindr.core.units import detect_category, detect_category_from_lib_id


# ---------------------------------------------------------------------------
# S-expression tokeniser / parser with position tracking
# ---------------------------------------------------------------------------

@dataclass
class SExprToken:
    """A single token in a KiCad S-expression."""
    kind: str  # "LPAREN", "RPAREN", "STRING", "ATOM"
    value: str
    start: int  # byte offset in source
    end: int    # byte offset (exclusive) in source


def tokenize_sexpr(text: str) -> list[SExprToken]:
    """Tokenise a KiCad S-expression string."""
    tokens: list[SExprToken] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif ch == "(":
            tokens.append(SExprToken("LPAREN", "(", i, i + 1))
            i += 1
        elif ch == ")":
            tokens.append(SExprToken("RPAREN", ")", i, i + 1))
            i += 1
        elif ch == '"':
            # Quoted string — handle escapes
            start = i
            i += 1
            parts: list[str] = []
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 1
                    if i < n:
                        parts.append(text[i])
                    i += 1
                elif c == '"':
                    i += 1
                    break
                else:
                    parts.append(c)
                    i += 1
            tokens.append(SExprToken("STRING", "".join(parts), start, i))
        else:
            # Atom — read until whitespace or paren
            start = i
            while i < n and text[i] not in " \t\r\n()\"":
                i += 1
            tokens.append(SExprToken("ATOM", text[start:i], start, i))
    return tokens


@dataclass
class SExprNode:
    """A node in the parsed S-expression tree."""
    kind: str  # "list", "atom", "string"
    value: str = ""
    children: list[SExprNode] = field(default_factory=list)
    start: int = 0   # character offset in source
    end: int = 0     # character offset in source (exclusive)

    @property
    def tag(self) -> str:
        """For list nodes, the first child atom value (the S-expr 'tag')."""
        if self.kind == "list" and self.children and self.children[0].kind == "atom":
            return self.children[0].value
        return ""


def parse_sexpr(text: str) -> list[SExprNode]:
    """Parse an S-expression string into a tree of SExprNode."""
    tokens = tokenize_sexpr(text)
    pos = 0

    def _parse_one() -> Optional[SExprNode]:
        nonlocal pos
        if pos >= len(tokens):
            return None
        tok = tokens[pos]
        if tok.kind == "RPAREN":
            return None
        if tok.kind in ("ATOM", "STRING"):
            pos += 1
            return SExprNode(
                kind="atom" if tok.kind == "ATOM" else "string",
                value=tok.value,
                start=tok.start,
                end=tok.end,
            )
        if tok.kind == "LPAREN":
            start = tok.start
            pos += 1
            children: list[SExprNode] = []
            while pos < len(tokens) and tokens[pos].kind != "RPAREN":
                child = _parse_one()
                if child is not None:
                    children.append(child)
            end = tokens[pos].end if pos < len(tokens) else len(text)
            pos += 1  # consume RPAREN
            node = SExprNode(kind="list", children=children, start=start, end=end)
            return node
        pos += 1
        return None

    result: list[SExprNode] = []
    while pos < len(tokens):
        node = _parse_one()
        if node:
            result.append(node)
    return result


# ---------------------------------------------------------------------------
# KiCad symbol / schematic data classes
# ---------------------------------------------------------------------------

@dataclass
class KiCadSymbol:
    """A placed symbol in a KiCad schematic."""
    ref: str
    value: str | None = None
    footprint: str | None = None
    lib_id: str | None = None
    uuid: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    category: str | None = None
    dnp: bool = False

    # Position tracking for the field writer
    node_start: int = 0
    node_end: int = 0
    # Property positions: field_name -> (value_start, value_end, property_start, property_end)
    _property_positions: dict[str, tuple[int, int, int, int]] = field(
        default_factory=dict, repr=False
    )
    # Position of the last property's closing paren — for inserting new properties
    _last_property_end: int = 0


@dataclass
class KiCadSchematic:
    """Parsed representation of a KiCad schematic."""
    path: Path
    symbols: list[KiCadSymbol]
    raw_text: str = ""

    def symbol_by_ref(self, ref: str) -> Optional[KiCadSymbol]:
        """Find a symbol by reference designator."""
        for sym in self.symbols:
            if sym.ref == ref:
                return sym
        return None

    def refs(self) -> list[str]:
        """Return all reference designators."""
        return [s.ref for s in self.symbols]


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class KiCadSchematicReader:
    """Reads a KiCad ``.kicad_sch`` file into a ``KiCadSchematic``."""

    def read(self, path: str | Path) -> KiCadSchematic:
        """Parse a schematic file."""
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        tree = parse_sexpr(text)

        symbols: list[KiCadSymbol] = []
        if tree:
            root = tree[0]
            self._extract_symbols(root, text, symbols)

        return KiCadSchematic(path=p, symbols=symbols, raw_text=text)

    def _extract_symbols(
        self,
        node: SExprNode,
        text: str,
        out: list[KiCadSymbol],
    ) -> None:
        """Walk the tree and extract placed symbol instances."""
        if node.kind != "list":
            return

        # Skip lib_symbols — those are library definitions, not placed instances
        if node.tag == "lib_symbols":
            return

        if node.tag == "symbol":
            sym = self._parse_symbol(node, text)
            if sym and sym.ref:
                out.append(sym)
            return  # Don't recurse into symbol children for more symbols

        # Recurse into children (e.g., the root kicad_sch node)
        for child in node.children:
            self._extract_symbols(child, text, out)

    def _parse_symbol(self, node: SExprNode, text: str) -> Optional[KiCadSymbol]:
        """Parse a single (symbol ...) block."""
        sym = KiCadSymbol(ref="", node_start=node.start, node_end=node.end)

        last_prop_end = 0

        for child in node.children:
            if child.kind != "list":
                continue

            tag = child.tag

            if tag == "lib_id" and len(child.children) > 1:
                sym.lib_id = child.children[1].value

            elif tag == "uuid" and len(child.children) > 1:
                sym.uuid = child.children[1].value

            elif tag == "dnp" and len(child.children) > 1:
                sym.dnp = child.children[1].value.lower() in ("yes", "true", "1")

            elif tag == "property" and len(child.children) >= 3:
                prop_name = child.children[1].value
                prop_value_node = child.children[2]
                prop_value = prop_value_node.value

                # Track position of the property value string for targeted editing
                sym._property_positions[prop_name] = (
                    prop_value_node.start,  # value start
                    prop_value_node.end,    # value end
                    child.start,            # property start
                    child.end,              # property end
                )
                last_prop_end = max(last_prop_end, child.end)

                if prop_name == "Reference":
                    sym.ref = prop_value
                elif prop_name == "Value":
                    sym.value = prop_value
                elif prop_name == "Footprint":
                    sym.footprint = prop_value if prop_value else None
                else:
                    sym.fields[prop_name] = prop_value

        sym._last_property_end = last_prop_end

        # Detect category
        cat = detect_category(sym.ref)
        if not cat and sym.lib_id:
            cat = detect_category_from_lib_id(sym.lib_id)
        sym.category = cat

        return sym if sym.ref else None
