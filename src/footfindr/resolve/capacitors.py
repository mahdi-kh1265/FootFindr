"""Capacitor resolver.

Matches schematic capacitors against the approved library by normalised
capacitance value. Supports explicit constraint fields: VoltageMin,
PackageHint, Dielectric, Tolerance for filtering candidates.

Resolution logic:
  1. Parse capacitance from the component's Value field.
  2. Find all approved capacitors with matching capacitance.
  3. Apply constraint filters (VoltageMin, PackageHint, Dielectric, Tolerance).
  4. Single match -> AUTO.
  5. Multiple matches -> REVIEW.
  6. No match -> SKIP or ERROR.
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
    capacitances_match,
    normalize_capacitance_display,
    parse_capacitance,
    parse_voltage,
)


class CapacitorResolver:
    """Resolve capacitors by value matching against approved parts."""

    def resolve(
        self,
        ctx: ComponentContext,
        approved_parts: list[PartRecord],
        config: FootFindrConfig,
    ) -> Optional[Decision]:
        """Try to resolve a capacitor.

        Returns a Decision, or None if this component is not a capacitor.
        """
        if ctx.category != "capacitor":
            return None

        # Parse the capacitance value
        cap_value = parse_capacitance(ctx.value)
        if cap_value is None:
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.SKIP,
                confidence=0.0,
                reasons=[f"Cannot parse capacitance from value '{ctx.value}'"],
                component_value=ctx.value,
                component_category="capacitor",
            )

        cap_display = normalize_capacitance_display(cap_value)

        # Find matching approved capacitors
        candidates = []
        for part in approved_parts:
            if part.category.value != "capacitor":
                continue
            if not part.approved:
                continue
            if not part.footprint:
                continue

            # Parse part capacitance
            part_cap_str = part.specs.capacitance or part.value
            if not part_cap_str:
                continue
            part_cap = parse_capacitance(part_cap_str)
            if part_cap is None:
                continue

            if capacitances_match(cap_value, part_cap):
                candidates.append(part)

        if not candidates:
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.SKIP,
                confidence=0.0,
                reasons=[f"No approved capacitor found for {cap_display}"],
                requirements=[
                    DecisionSource(
                        field_name="capacitance",
                        value=cap_display,
                        source="KiCad Value field",
                    ),
                ],
                component_value=ctx.value,
                component_category="capacitor",
            )

        # ----- Apply constraint filters from schematic fields -----
        reasons_for_filtering: list[str] = []
        filtered = list(candidates)

        # VoltageMin filter
        voltage_min_str = ctx.fields.get("VoltageMin")
        if voltage_min_str:
            voltage_min = parse_voltage(voltage_min_str)
            if voltage_min is not None:
                before = len(filtered)
                filtered = [
                    p for p in filtered
                    if p.specs.voltage_rating and
                    (parse_voltage(p.specs.voltage_rating) or 0) >= voltage_min
                ]
                if len(filtered) < before:
                    reasons_for_filtering.append(
                        f"VoltageMin={voltage_min_str} filtered {before - len(filtered)} candidates"
                    )

        # PackageHint filter
        package_hint = ctx.fields.get("PackageHint")
        if package_hint:
            pkg_match = [p for p in filtered if p.package and p.package == package_hint]
            if pkg_match:
                reasons_for_filtering.append(
                    f"PackageHint={package_hint} selected {len(pkg_match)} candidates"
                )
                filtered = pkg_match
            else:
                reasons_for_filtering.append(
                    f"PackageHint={package_hint} matched none; keeping all {len(filtered)} candidates"
                )

        # Dielectric filter (hard constraint — explicit schematic field)
        dielectric_hint = ctx.fields.get("Dielectric")
        if dielectric_hint:
            before = len(filtered)
            die_match = [
                p for p in filtered
                if p.specs.dielectric and p.specs.dielectric.upper() == dielectric_hint.upper()
            ]
            filtered = die_match  # Hard filter: if none match, filtered becomes empty
            if len(filtered) < before:
                reasons_for_filtering.append(
                    f"Dielectric={dielectric_hint} filtered {before - len(filtered)} candidates"
                )

        # Tolerance filter
        tolerance_hint = ctx.fields.get("Tolerance")
        if tolerance_hint:
            before = len(filtered)
            tol_match = [
                p for p in filtered
                if p.specs.tolerance and p.specs.tolerance == tolerance_hint
            ]
            if tol_match:
                filtered = tol_match
                reasons_for_filtering.append(
                    f"Tolerance={tolerance_hint} selected {len(filtered)} candidates"
                )

        # If filters eliminated everything, that's an error/review
        if not filtered and candidates:
            constraint_desc = ", ".join(
                f"{k}={v}" for k, v in [
                    ("VoltageMin", voltage_min_str),
                    ("PackageHint", package_hint),
                    ("Dielectric", dielectric_hint),
                    ("Tolerance", tolerance_hint),
                ] if v
            )
            return Decision(
                ref=ctx.ref,
                status=DecisionStatus.REVIEW,
                confidence=0.2,
                reasons=[
                    f"{len(candidates)} approved caps match {cap_display}, "
                    f"but none satisfy constraints: {constraint_desc}"
                ] + reasons_for_filtering,
                candidate_summary=[
                    {
                        "internal_pn": c.internal_pn,
                        "package": c.package,
                        "voltage_rating": c.specs.voltage_rating,
                        "dielectric": c.specs.dielectric,
                        "footprint": c.footprint,
                    }
                    for c in candidates
                ],
                component_value=ctx.value,
                component_category="capacitor",
            )

        # Use filtered candidates for final decision
        candidates = filtered

        if len(candidates) == 1:
            return self._make_auto_decision(ctx, candidates[0], cap_display, reasons_for_filtering)

        # Multiple candidates -- mark REVIEW
        candidate_summary = []
        for c in candidates:
            candidate_summary.append({
                "internal_pn": c.internal_pn,
                "package": c.package,
                "voltage_rating": c.specs.voltage_rating,
                "dielectric": c.specs.dielectric,
                "footprint": c.footprint,
            })

        return Decision(
            ref=ctx.ref,
            status=DecisionStatus.REVIEW,
            confidence=0.5,
            reasons=[
                f"Multiple approved capacitors match {cap_display}: "
                f"{', '.join(c.internal_pn for c in candidates)}"
            ] + reasons_for_filtering,
            candidate_summary=candidate_summary,
            requirements=[
                DecisionSource(
                    field_name="capacitance",
                    value=cap_display,
                    source="KiCad Value field",
                ),
            ],
            component_value=ctx.value,
            component_category="capacitor",
        )

    def _make_auto_decision(
        self,
        ctx: ComponentContext,
        part: PartRecord,
        cap_display: str,
        extra_reasons: list[str] | None = None,
    ) -> Decision:
        """Build an AUTO decision for a capacitor."""
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
        if part.specs.tolerance:
            fields_to_write["Tolerance"] = part.specs.tolerance
        if part.specs.dielectric:
            fields_to_write["Dielectric"] = part.specs.dielectric

        fields_to_write["FootFindrStatus"] = "AUTO"
        fields_to_write["FootFindrConfidence"] = "0.95"
        fields_to_write["FootFindrReason"] = (
            f"Approved {cap_display} cap: {part.internal_pn}"
        )

        old_fields = dict(ctx.fields)
        if ctx.footprint:
            old_fields["Footprint"] = ctx.footprint

        reasons = [
            f"Single approved match for {cap_display} -> {part.internal_pn} "
            f"({part.package}, {part.specs.voltage_rating or '?'})"
        ]
        if extra_reasons:
            reasons.extend(extra_reasons)

        return Decision(
            ref=ctx.ref,
            status=DecisionStatus.AUTO,
            confidence=0.95,
            selected_internal_pn=part.internal_pn,
            selected_mpn=part.mpn,
            selected_footprint=part.footprint,
            fields_to_write=fields_to_write,
            old_fields=old_fields,
            reasons=reasons,
            requirements=[
                DecisionSource(
                    field_name="capacitance",
                    value=cap_display,
                    source="KiCad Value field",
                ),
            ],
            source_library=part.source_library,
            component_value=ctx.value,
            component_category="capacitor",
        )
