"""Resolve engine — orchestrates the resolver chain.

Dispatches each component through the resolver hierarchy:
  1. Locked/skip → UNCHANGED
  2. Exact InternalPN → exact match
  3. Exact MPN → exact match
  4. Capacitor resolver → value match
  5. Resistor resolver → value match
  6. Fallback → SKIP/REVIEW

Only AUTO decisions are applied when ``--apply`` is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from footfindr.config import FootFindrConfig
from footfindr.core.models import (
    ComponentCategory,
    ComponentContext,
    Decision,
    DecisionStatus,
    PartRecord,
)
from footfindr.kicad.field_writer import KiCadFieldWriter
from footfindr.kicad.schematic import KiCadSchematic, KiCadSchematicReader, KiCadSymbol
from footfindr.libraries.manager import LibraryManager
from footfindr.resolve.capacitors import CapacitorResolver
from footfindr.resolve.exact import ExactResolver
from footfindr.resolve.resistors import ResistorResolver


class ResolveEngine:
    """Top-level resolver that runs the chain for each component."""

    def __init__(
        self,
        config: FootFindrConfig,
        approved_parts: list[PartRecord],
    ) -> None:
        self.config = config
        self.approved_parts = approved_parts
        self._exact = ExactResolver()
        self._capacitor = CapacitorResolver()
        self._resistor = ResistorResolver()

    def resolve_component(self, ctx: ComponentContext) -> Decision:
        """Resolve a single component through the resolver chain."""

        # 1. Locked → UNCHANGED
        if ctx.locked:
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.UNCHANGED,
                confidence=1.0,
                reasons=["FootFindrLocked=true"],
                component_value=ctx.value,
                component_category=ctx.category,
            )

        # 2. DNP → SKIP
        if ctx.dnp:
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.SKIP,
                confidence=1.0,
                reasons=["Component marked DNP"],
                component_value=ctx.value,
                component_category=ctx.category,
            )

        # 3. Exact InternalPN / MPN
        decision = self._exact.resolve(ctx, self.approved_parts)
        if decision is not None:
            return decision

        # 4. Capacitor resolver
        decision = self._capacitor.resolve(ctx, self.approved_parts, self.config)
        if decision is not None:
            return decision

        # 5. Resistor resolver
        decision = self._resistor.resolve(ctx, self.approved_parts, self.config)
        if decision is not None:
            return decision

        # 6. Fallback — SKIP
        return Decision(
            ref=ctx.ref,
            status=DecisionStatus.SKIP,
            confidence=0.0,
            reasons=[f"No resolver matched for {ctx.ref} (category={ctx.category})"],
            component_value=ctx.value,
            component_category=ctx.category,
        )

    def resolve_schematic(
        self,
        schematic: KiCadSchematic,
        targets: Optional[list[str]] = None,
    ) -> list[Decision]:
        """Resolve all (or targeted) components in a schematic.

        Args:
            schematic: Parsed KiCad schematic.
            targets: List of reference designators, or None for all.
                     Special values: ``["all"]`` resolves everything.

        Returns:
            List of Decision objects.
        """
        symbols = schematic.symbols

        if targets and targets != ["all"]:
            # Filter to requested refs
            target_set = set(targets)
            symbols = [s for s in symbols if s.ref in target_set]

            # Check for missing refs
            found = {s.ref for s in symbols}
            for t in targets:
                if t not in found:
                    # Could be a group name like "caps" — skip for now
                    pass

        decisions: list[Decision] = []
        for sym in symbols:
            ctx = self._symbol_to_context(sym)
            decision = self.resolve_component(ctx)
            decisions.append(decision)

        return decisions

    def _symbol_to_context(self, sym: KiCadSymbol) -> ComponentContext:
        """Convert a KiCadSymbol to a ComponentContext for the resolver."""
        # Detect locked status
        locked_val = sym.fields.get("FootFindrLocked", "").lower()
        locked = locked_val in ("true", "yes", "1")

        # Detect DNP
        dnp = sym.dnp
        dnp_field = sym.fields.get("DNP", "").lower()
        if dnp_field in ("true", "yes", "1", "dnp"):
            dnp = True

        return ComponentContext(
            ref=sym.ref,
            value=sym.value or "",
            symbol=sym.lib_id,
            footprint=sym.footprint,
            fields=dict(sym.fields),
            category=sym.category,
            locked=locked,
            dnp=dnp,
        )


def apply_decisions(
    schematic_path: str | Path,
    decisions: list[Decision],
    *,
    force: bool = False,
    backup: bool = True,
    min_confidence: float = 0.92,
) -> dict[str, str | None]:
    """Apply AUTO decisions to the schematic file.

    Only decisions with status=AUTO and confidence >= min_confidence are written.

    Returns ``{ref: error_or_None}``.
    """
    updates: dict[str, dict[str, str]] = {}

    for d in decisions:
        if d.status != DecisionStatus.AUTO:
            continue
        if d.confidence < min_confidence:
            continue
        if not d.fields_to_write:
            continue

        updates[d.ref] = d.fields_to_write
        d.applied = True

    if not updates:
        return {}

    writer = KiCadFieldWriter()
    return writer.update_fields(
        schematic_path,
        updates,
        backup=backup,
        force=force,
    )


def run_resolve(
    schematic_path: str | Path,
    targets: Optional[list[str]] = None,
    *,
    apply: bool = False,
    force: bool = False,
    backup: bool = True,
    min_confidence: float = 0.92,
    config: Optional[FootFindrConfig] = None,
    approved_parts: Optional[list[PartRecord]] = None,
) -> list[Decision]:
    """High-level resolve workflow.

    1. Load config and approved parts if not provided.
    2. Parse schematic.
    3. Resolve targets.
    4. Optionally apply AUTO decisions.
    5. Return all decisions.
    """
    from footfindr.config import load_config

    if config is None:
        config = load_config()

    if approved_parts is None:
        mgr = LibraryManager()
        approved_parts = mgr.load_approved_parts()

    reader = KiCadSchematicReader()
    schematic = reader.read(schematic_path)

    engine = ResolveEngine(config, approved_parts)
    decisions = engine.resolve_schematic(schematic, targets)

    if apply:
        results = apply_decisions(
            schematic_path,
            decisions,
            force=force,
            backup=backup,
            min_confidence=min_confidence,
        )
        # Update decisions with apply results
        for d in decisions:
            if d.ref in results:
                err = results[d.ref]
                if err:
                    d.applied = False
                    d.errors.append(err)

    return decisions
