"""JSON decision log writer.

Writes ``footfindr_decisions.json`` after every resolve run.  The log
includes enough old/new field data to support future undo functionality.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from footfindr.core.models import Decision


def write_decision_log(
    decisions: list[Decision],
    *,
    schematic_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """Serialize decisions to a JSON log file."""
    if output_path is None:
        output_path = Path("footfindr_decisions.json")
    else:
        output_path = Path(output_path)

    log: dict[str, Any] = {
        "footfindr_version": "0.1.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "schematic": str(schematic_path) if schematic_path else None,
        "summary": _summarise(decisions),
        "decisions": [_decision_to_dict(d) for d in decisions],
    }

    output_path.write_text(
        json.dumps(log, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


def _summarise(decisions: list[Decision]) -> dict[str, int]:
    """Count decisions by status."""
    counts: dict[str, int] = {}
    for d in decisions:
        key = d.status.value
        counts[key] = counts.get(key, 0) + 1
    counts["total"] = len(decisions)
    counts["applied"] = sum(1 for d in decisions if d.applied)
    return counts


def _decision_to_dict(d: Decision) -> dict[str, Any]:
    """Convert a Decision to a JSON-serialisable dict."""
    return {
        "ref": d.ref,
        "status": d.status.value,
        "confidence": d.confidence,
        "component_value": d.component_value,
        "component_category": d.component_category,
        "selected_internal_pn": d.selected_internal_pn,
        "selected_mpn": d.selected_mpn,
        "selected_footprint": d.selected_footprint,
        "fields_to_write": d.fields_to_write,
        "old_fields": d.old_fields,
        "reasons": d.reasons,
        "warnings": d.warnings,
        "errors": d.errors,
        "source_library": d.source_library,
        "applied": d.applied,
    }
