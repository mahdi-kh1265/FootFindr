"""Resistor resolver.

Matches schematic resistors against the approved library by normalised
resistance value.  Only approved parts are used for AUTO decisions.

Resolution logic:
  1. Parse resistance from the component's Value field.
  2. Find all approved resistors with matching resistance.
  3. Single match -> AUTO.
  4. Multiple matches -> REVIEW.
  5. No match -> SKIP.
"""

from __future__ import annotations

from typing import Optional

from footfindr.config import FootFindrConfig
from footfindr.core.models import (
    ComponentContext,
    Decision,
    DecisionSource,
    DecisionStatus,
    PartRecord,
)
from footfindr.core.units import (
    normalize_resistance_display,
    parse_resistance,
    resistances_match,
)


class ResistorResolver:
    """Resolve resistors by value matching against approved parts."""

    def resolve(
        self,
        ctx: ComponentContext,
        approved_parts: list[PartRecord],
        config: FootFindrConfig,
    ) -> Optional[Decision]:
        """Try to resolve a resistor.

        Returns a Decision, or None if this component is not a resistor.
        """
        if ctx.category != "resistor":
            return None

        # Parse the resistance value
        res_value = parse_resistance(ctx.value)
        if res_value is None:
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.SKIP,
                confidence=0.0,
                reasons=[f"Cannot parse resistance from value '{ctx.value}'"],
                component_value=ctx.value,
                component_category="resistor",
            )

        res_display = normalize_resistance_display(res_value)

        # Find matching approved resistors
        candidates = []
        for part in approved_parts:
            if part.category.value != "resistor":
                continue
            if not part.approved:
                continue
            if not part.footprint:
                continue

            # Parse part resistance
            part_res_str = part.specs.resistance or part.value
            if not part_res_str:
                continue
            part_res = parse_resistance(part_res_str)
            if part_res is None:
                continue

            if resistances_match(res_value, part_res):
                candidates.append(part)

        if not candidates:
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.SKIP,
                confidence=0.0,
                reasons=[f"No approved resistor found for {res_display}"],
                requirements=[
                    DecisionSource(
                        field_name="resistance",
                        value=res_display,
                        source="KiCad Value field",
                    ),
                ],
                component_value=ctx.value,
                component_category="resistor",
            )

        if len(candidates) == 1:
            return self._make_auto_decision(ctx, candidates[0], res_display)

        # Multiple candidates
        candidate_summary = []
        for c in candidates:
            candidate_summary.append({
                "internal_pn": c.internal_pn,
                "package": c.package,
                "power_rating": c.specs.power_rating,
                "footprint": c.footprint,
            })

        return Decision(
            ref=ctx.ref,
            status=DecisionStatus.REVIEW,
            confidence=0.5,
            reasons=[
                f"Multiple approved resistors match {res_display}: "
                f"{', '.join(c.internal_pn for c in candidates)}"
            ],
            candidate_summary=candidate_summary,
            requirements=[
                DecisionSource(
                    field_name="resistance",
                    value=res_display,
                    source="KiCad Value field",
                ),
            ],
            component_value=ctx.value,
            component_category="resistor",
        )

    def _make_auto_decision(
        self,
        ctx: ComponentContext,
        part: PartRecord,
        res_display: str,
    ) -> Decision:
        """Build an AUTO decision for a resistor."""
        fields_to_write: dict[str, str] = {}

        if part.footprint:
            fields_to_write["Footprint"] = part.footprint
        fields_to_write["InternalPN"] = part.internal_pn
        if part.mpn:
            fields_to_write["MPN"] = part.mpn
        if part.manufacturer:
            fields_to_write["Manufacturer"] = part.manufacturer
        if part.package:
            fields_to_write["Package"] = part.package
        if part.specs.power_rating:
            fields_to_write["PowerRating"] = part.specs.power_rating
        if part.specs.tolerance:
            fields_to_write["Tolerance"] = part.specs.tolerance

        fields_to_write["FootFindrStatus"] = "AUTO"
        fields_to_write["FootFindrConfidence"] = "0.95"
        fields_to_write["FootFindrReason"] = (
            f"Approved {res_display} resistor: {part.internal_pn}"
        )

        old_fields = dict(ctx.fields)
        if ctx.footprint:
            old_fields["Footprint"] = ctx.footprint

        return Decision(
            ref=ctx.ref,
            status=DecisionStatus.AUTO,
            confidence=0.95,
            selected_internal_pn=part.internal_pn,
            selected_mpn=part.mpn,
            selected_footprint=part.footprint,
            fields_to_write=fields_to_write,
            old_fields=old_fields,
            reasons=[
                f"Single approved match for {res_display} -> {part.internal_pn} "
                f"({part.package}, {part.specs.power_rating or '?'})"
            ],
            requirements=[
                DecisionSource(
                    field_name="resistance",
                    value=res_display,
                    source="KiCad Value field",
                ),
            ],
            source_library=part.source_library,
            component_value=ctx.value,
            component_category="resistor",
        )
