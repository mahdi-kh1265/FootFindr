"""Exact InternalPN / MPN resolver.

Highest-trust path: if the schematic component already has an InternalPN or MPN
field that exactly matches an approved part, use that part's footprint and fields.
"""

from __future__ import annotations

from typing import Optional

from footfindr.core.models import (
    ComponentContext,
    Decision,
    DecisionSource,
    DecisionStatus,
    PartRecord,
)


class ExactResolver:
    """Resolve components by exact InternalPN or MPN match."""

    def resolve(
        self,
        ctx: ComponentContext,
        approved_parts: list[PartRecord],
    ) -> Optional[Decision]:
        """Try to resolve by exact InternalPN, then exact MPN.

        Returns a Decision if a match is found, None otherwise.
        """
        # 1. Exact InternalPN
        internal_pn = ctx.fields.get("InternalPN")
        if internal_pn:
            for part in approved_parts:
                if part.internal_pn == internal_pn:
                    if not part.approved:
                        return Decision(
                            ref=ctx.ref,
                            status=DecisionStatus.REVIEW,
                            confidence=0.5,
                            selected_internal_pn=part.internal_pn,
                            selected_mpn=part.mpn,
                            reasons=[
                                f"InternalPN '{internal_pn}' found but part is not approved "
                                f"(status={part.status.value})"
                            ],
                            component_value=ctx.value,
                            component_category=ctx.category,
                        )

                    return self._make_decision(ctx, part, match_type="InternalPN")

            # InternalPN specified but not found
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.ERROR,
                confidence=0.0,
                reasons=[f"InternalPN '{internal_pn}' not found in approved library"],
                errors=[f"Unknown InternalPN: {internal_pn}"],
                component_value=ctx.value,
                component_category=ctx.category,
            )

        # 2. Exact MPN
        mpn = ctx.fields.get("MPN")
        if mpn:
            for part in approved_parts:
                if part.mpn and part.mpn == mpn:
                    if not part.approved:
                        return Decision(
                            ref=ctx.ref,
                            status=DecisionStatus.REVIEW,
                            confidence=0.5,
                            selected_internal_pn=part.internal_pn,
                            selected_mpn=part.mpn,
                            reasons=[
                                f"MPN '{mpn}' found but part is not approved "
                                f"(status={part.status.value})"
                            ],
                            component_value=ctx.value,
                            component_category=ctx.category,
                        )
                    return self._make_decision(ctx, part, match_type="MPN")

            # MPN specified but not found in approved library
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.REVIEW,
                confidence=0.3,
                reasons=[f"MPN '{mpn}' not found in approved library"],
                warnings=[f"MPN '{mpn}' may need to be added to approved parts"],
                component_value=ctx.value,
                component_category=ctx.category,
            )

        return None  # No InternalPN or MPN -- pass to next resolver

    def _make_decision(
        self,
        ctx: ComponentContext,
        part: PartRecord,
        *,
        match_type: str,
    ) -> Decision:
        """Build an AUTO decision from an exact match."""
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
        if part.specs.voltage_rating:
            fields_to_write["VoltageRating"] = part.specs.voltage_rating
        if part.specs.power_rating:
            fields_to_write["PowerRating"] = part.specs.power_rating
        if part.specs.tolerance:
            fields_to_write["Tolerance"] = part.specs.tolerance
        if part.specs.dielectric:
            fields_to_write["Dielectric"] = part.specs.dielectric

        fields_to_write["FootFindrStatus"] = "AUTO"
        fields_to_write["FootFindrConfidence"] = "0.99"
        fields_to_write["FootFindrReason"] = f"Exact {match_type} match: {part.internal_pn}"

        # Snapshot old fields
        old_fields = dict(ctx.fields)
        if ctx.footprint:
            old_fields["Footprint"] = ctx.footprint

        return Decision(
            ref=ctx.ref,
            status=DecisionStatus.AUTO,
            confidence=0.99,
            selected_internal_pn=part.internal_pn,
            selected_mpn=part.mpn,
            selected_footprint=part.footprint,
            fields_to_write=fields_to_write,
            old_fields=old_fields,
            reasons=[f"Exact {match_type} match -> {part.internal_pn}"],
            requirements=[
                DecisionSource(
                    field_name=match_type,
                    value=part.internal_pn if match_type == "InternalPN" else (part.mpn or ""),
                    source=f"KiCad {match_type} field",
                ),
            ],
            source_library=part.source_library,
            component_value=ctx.value,
            component_category=ctx.category,
        )
