"""KiCad schematic inspection for FootFindr.

Read-only analysis of .kicad_sch files: counts refs, values, footprints,
field coverage, and detects hierarchical subsheets.  Supports recursive
parsing of subsheets.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SchematicInspection:
    """Summary of a parsed KiCad schematic."""
    path: str
    symbol_count: int = 0
    refs: list[str] = field(default_factory=list)
    values_summary: dict[str, int] = field(default_factory=dict)
    footprints_summary: dict[str, int] = field(default_factory=dict)
    field_names: list[str] = field(default_factory=list)
    has_mpn: int = 0
    has_manufacturer: int = 0
    has_internal_pn: int = 0
    has_footprint: int = 0
    sheets_parsed: int = 1          # root + successful subsheets
    subsheets_detected: list[str] = field(default_factory=list)
    subsheets_exist: list[str] = field(default_factory=list)
    subsheets_missing: list[str] = field(default_factory=list)
    subsheets_parse_failed: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    writable: bool = False
    last_modified: str | None = None
    is_complete: bool = True        # False if subsheets couldn't be parsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "symbol_count": self.symbol_count,
            "refs": self.refs,
            "values_summary": self.values_summary,
            "footprints_summary": self.footprints_summary,
            "field_names": self.field_names,
            "has_mpn": self.has_mpn,
            "has_manufacturer": self.has_manufacturer,
            "has_internal_pn": self.has_internal_pn,
            "has_footprint": self.has_footprint,
            "sheets_parsed": self.sheets_parsed,
            "subsheets_detected": self.subsheets_detected,
            "subsheets_exist": self.subsheets_exist,
            "subsheets_missing": self.subsheets_missing,
            "subsheets_parse_failed": self.subsheets_parse_failed,
            "parse_warnings": self.parse_warnings,
            "writable": self.writable,
            "last_modified": self.last_modified,
            "is_complete": self.is_complete,
        }


def inspect_schematic(
    path: str | Path,
    *,
    recursive: bool = True,
) -> SchematicInspection:
    """Parse a schematic and produce a summary.

    If ``recursive`` is True, attempts to parse subsheets and include
    their refs in the results.  If a subsheet fails to parse, it is
    recorded and the inspection is marked as incomplete.
    """
    from footfindr.kicad.discovery import detect_subsheets
    from footfindr.kicad.schematic import KiCadSchematicReader

    p = Path(path).resolve()
    result = SchematicInspection(path=str(p))

    # Check file metadata
    if p.exists():
        stat = p.stat()
        result.last_modified = datetime.datetime.fromtimestamp(
            stat.st_mtime, tz=datetime.timezone.utc
        ).isoformat()
        try:
            result.writable = os.access(str(p), os.W_OK)
        except OSError:
            result.writable = False
    else:
        result.parse_warnings.append(f"File not found: {p}")
        result.is_complete = False
        return result

    # Parse root schematic
    reader = KiCadSchematicReader()
    try:
        sch = reader.read(str(p))
    except Exception as e:
        result.parse_warnings.append(f"Failed to parse root schematic: {e}")
        result.is_complete = False
        return result

    all_symbols = list(sch.symbols)

    # Detect subsheets
    subsheets = detect_subsheets(p)
    result.subsheets_detected = [str(s) for s in subsheets]

    for ss in subsheets:
        if ss.exists():
            result.subsheets_exist.append(str(ss))
        else:
            result.subsheets_missing.append(str(ss))
            result.is_complete = False
            result.parse_warnings.append(
                f"Subsheet file missing: {ss.name}"
            )

    # Recursive subsheet parsing
    if recursive and subsheets:
        parsed_paths: set[str] = {str(p)}
        _parse_subsheets_recursive(
            subsheets, reader, all_symbols, result, parsed_paths
        )

    # Aggregate results from all parsed symbols
    field_names_set: set[str] = set()
    values: dict[str, int] = {}
    footprints: dict[str, int] = {}
    seen_refs: set[str] = set()

    for sym in all_symbols:
        if sym.dnp:
            continue
        # Deduplicate refs across sheets
        if sym.ref in seen_refs:
            continue
        seen_refs.add(sym.ref)

        result.refs.append(sym.ref)

        # Value summary
        v = sym.value or "(empty)"
        values[v] = values.get(v, 0) + 1

        # Footprint summary
        fp = sym.footprint or "(none)"
        footprints[fp] = footprints.get(fp, 0) + 1

        # Field coverage
        if sym.footprint:
            result.has_footprint += 1

        fields = sym.fields
        field_names_set.update(fields.keys())

        mpn = fields.get("MPN", "") or fields.get("mpn", "")
        if mpn:
            result.has_mpn += 1
        mfr = fields.get("Manufacturer", "") or fields.get("manufacturer", "")
        if mfr:
            result.has_manufacturer += 1
        ipn = fields.get("InternalPN", "")
        if ipn:
            result.has_internal_pn += 1

    result.symbol_count = len(result.refs)
    result.values_summary = values
    result.footprints_summary = footprints
    result.field_names = sorted(field_names_set)

    return result


def _parse_subsheets_recursive(
    subsheets: list[Path],
    reader,
    all_symbols: list,
    result: SchematicInspection,
    parsed_paths: set[str],
) -> None:
    """Recursively parse subsheet .kicad_sch files."""
    from footfindr.kicad.discovery import detect_subsheets

    for ss in subsheets:
        ss_key = str(ss.resolve())
        if ss_key in parsed_paths:
            continue
        parsed_paths.add(ss_key)

        if not ss.exists():
            continue

        try:
            sub_sch = reader.read(str(ss))
            all_symbols.extend(sub_sch.symbols)
            result.sheets_parsed += 1

            # Recurse into sub-subsheets
            sub_subsheets = detect_subsheets(ss)
            if sub_subsheets:
                for sub_ss in sub_subsheets:
                    if str(sub_ss) not in result.subsheets_detected:
                        result.subsheets_detected.append(str(sub_ss))
                    if sub_ss.exists():
                        if str(sub_ss) not in result.subsheets_exist:
                            result.subsheets_exist.append(str(sub_ss))
                    else:
                        if str(sub_ss) not in result.subsheets_missing:
                            result.subsheets_missing.append(str(sub_ss))
                            result.is_complete = False

                _parse_subsheets_recursive(
                    sub_subsheets, reader, all_symbols, result, parsed_paths
                )
        except Exception as e:
            result.subsheets_parse_failed.append(str(ss))
            result.is_complete = False
            result.parse_warnings.append(
                f"Failed to parse subsheet {ss.name}: {e}"
            )
