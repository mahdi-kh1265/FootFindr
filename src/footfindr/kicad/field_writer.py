"""KiCad schematic field writer — text-level targeted property editor.

Safety contract:
  1. ALWAYS creates a ``.footfindr.bak`` backup before the first write.
  2. Only edits/adds ``(property ...)`` blocks inside ``(symbol ...)`` blocks.
  3. Never rewrites the full schematic from a regenerated AST.
  4. If a symbol block cannot be located unambiguously → ERROR (no write).
  5. If multiple matching references are found unexpectedly → ERROR (no write).
  6. Existing non-empty ``Footprint`` is NOT overwritten unless ``force=True``.
  7. ``FootFindrLocked=true`` blocks all changes (unless future unlock option).

The writer operates on the raw file text using character-position data from
the reader.  Edits are collected as ``(start, end, replacement)`` tuples and
applied in reverse order so positions remain valid.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from footfindr.kicad.schematic import KiCadSchematic, KiCadSchematicReader


# ---------------------------------------------------------------------------
# Edit operation
# ---------------------------------------------------------------------------

@dataclass
class _TextEdit:
    """A single text replacement: replace text[start:end] with ``replacement``."""
    start: int
    end: int
    replacement: str


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class FieldWriteError(Exception):
    """Raised when a field write cannot be performed safely."""


class KiCadFieldWriter:
    """Writes symbol property updates into a ``.kicad_sch`` file.

    Usage::

        writer = KiCadFieldWriter()
        results = writer.update_fields(
            path="board.kicad_sch",
            updates={"C1": {"Footprint": "Capacitor_SMD:C_0805_2012Metric", "MPN": "XYZ"}},
            backup=True,
            force=False,
        )
    """

    def update_fields(
        self,
        path: str | Path,
        updates: dict[str, dict[str, str]],
        *,
        backup: bool = True,
        force: bool = False,
    ) -> dict[str, str | None]:
        """Update symbol properties in a KiCad schematic.

        Args:
            path: Path to the ``.kicad_sch`` file.
            updates: ``{ref: {field_name: new_value, ...}, ...}``.
            backup: Create a ``.footfindr.bak`` file before writing.
            force: Allow overwriting non-empty ``Footprint`` fields.

        Returns:
            ``{ref: error_message_or_None}`` — None means success.
        """
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        reader = KiCadSchematicReader()
        schematic = reader.read(p)

        results: dict[str, str | None] = {}
        edits: list[_TextEdit] = []

        for ref, field_updates in updates.items():
            err = self._validate_ref(ref, schematic, force)
            if err:
                results[ref] = err
                continue

            sym = schematic.symbol_by_ref(ref)
            assert sym is not None  # validated above

            ref_edits, ref_err = self._compute_edits(sym, field_updates, text, force)
            if ref_err:
                results[ref] = ref_err
                continue

            edits.extend(ref_edits)
            results[ref] = None

        if not edits:
            return results

        # Create backup before any writes
        if backup:
            bak_path = p.with_name(p.name + ".footfindr.bak")
            shutil.copy2(str(p), str(bak_path))

        # Apply edits in reverse offset order so positions stay valid
        edits.sort(key=lambda e: e.start, reverse=True)
        for edit in edits:
            text = text[:edit.start] + edit.replacement + text[edit.end:]

        p.write_text(text, encoding="utf-8")
        return results

    # ----- internal helpers -----

    def _validate_ref(
        self,
        ref: str,
        schematic: KiCadSchematic,
        force: bool,
    ) -> Optional[str]:
        """Validate that a ref can be written to.  Returns error string or None."""
        matching = [s for s in schematic.symbols if s.ref == ref]
        if len(matching) == 0:
            return f"Reference {ref} not found in schematic"
        if len(matching) > 1:
            return f"Multiple symbols with reference {ref} found — ambiguous, skipping"

        sym = matching[0]

        # Check locked
        locked_val = sym.fields.get("FootFindrLocked", "").lower()
        if locked_val in ("true", "yes", "1"):
            return f"{ref} is locked (FootFindrLocked=true)"

        return None

    def _compute_edits(
        self,
        sym,  # KiCadSymbol
        field_updates: dict[str, str],
        text: str,
        force: bool,
    ) -> tuple[list[_TextEdit], Optional[str]]:
        """Compute text edits for updating fields on a single symbol."""
        edits: list[_TextEdit] = []

        for field_name, new_value in field_updates.items():
            # Check: don't overwrite non-empty Footprint unless forced
            if field_name == "Footprint" and not force:
                existing = sym.footprint
                if existing and existing.strip():
                    # Skip this field silently — not an error
                    continue

            if field_name in sym._property_positions:
                # Update existing property value
                val_start, val_end, prop_start, prop_end = sym._property_positions[field_name]
                # The value in the file is quoted: "value"
                # val_start/val_end include the quotes
                # We need to replace the entire quoted string
                old_quoted = text[val_start:val_end]
                new_quoted = f'"{new_value}"'
                edits.append(_TextEdit(val_start, val_end, new_quoted))
            else:
                # Insert new property — find the right place
                insert_pos = sym._last_property_end
                if insert_pos == 0:
                    return [], f"Cannot locate property insertion point for {sym.ref}"

                # Build a new property line.  We insert right after the last property's
                # closing paren, indented to match.
                indent = self._detect_indent(text, insert_pos)
                new_prop = (
                    f'\n{indent}(property "{field_name}" "{new_value}" (at 0 0 0)'
                    f"\n{indent}  (effects (font (size 1.27 1.27)) hide)"
                    f"\n{indent})"
                )
                edits.append(_TextEdit(insert_pos, insert_pos, new_prop))

        return edits, None

    @staticmethod
    def _detect_indent(text: str, pos: int) -> str:
        """Detect the indentation of the line containing ``pos``."""
        # Walk back to find the start of the line
        line_start = text.rfind("\n", 0, pos)
        if line_start == -1:
            line_start = 0
        else:
            line_start += 1

        indent = ""
        for ch in text[line_start:]:
            if ch in " \t":
                indent += ch
            else:
                break
        return indent
