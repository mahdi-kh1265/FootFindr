"""Suggest pipeline orchestrator (M9.4B — nested scoring).

Orchestrates the full suggest flow:
1. Parse schematic → get component
2. Build net connections → NetGraph + PinCompleteness
3. Scan rails → RailInfo
4. Classify role (softmax) → role + probabilities
5. Run package sweep → list[PackageScore] with PackageEvidence
6. Compute package utility → ranked packages
7. Run rank stability → decision
8. For top package(s), score individual MPNs → list[CandidateScore]
9. Build and persist SuggestionRecord

Zero-evidence guard:
    If all packages have zero viable evidence:
        decision = "no_recommendation"
        top_package = None
        top_candidate_mpn = None

Read-only — never modifies KiCad schematics.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from footfindr.intelligence.models import (
    CandidateScore,
    Fact,
    PackageScore,
    SuggestionRecord,
    compute_context_hash,
    SCORING_POLICY_VERSION,
)

logger = logging.getLogger("footfindr.intelligence.suggest")


def suggest_component(
    ref: str,
    schematic_path: str | Path,
    *,
    use_cache: bool = True,
    use_live: bool = False,
    supplier: str | None = None,
    workspace: Path | None = None,
    project_name: str | None = None,
) -> SuggestionRecord:
    """Run the full suggest pipeline for a component.

    Returns a SuggestionRecord with package ranking, candidate ranking,
    evidence chain, rank stability, and decision.
    """
    from footfindr.core.units import parse_capacitance, parse_voltage
    from footfindr.intelligence.cap_classifier import classify_capacitor
    from footfindr.intelligence.net_graph import get_connectivity_provider
    from footfindr.intelligence.package_sweep import (
        package_sweep,
        compute_required_voltage,
    )
    from footfindr.intelligence.rail_scanner import scan_rails
    from footfindr.intelligence.scoring import (
        CapacitorScorer,
        DEFAULT_PACKAGE_UTILITY_WEIGHTS,
        compute_package_utility,
    )
    from footfindr.intelligence.rank_stability import analyze_rank_stability
    from footfindr.intelligence.storage import SuggestionStore
    from footfindr.kicad.schematic import KiCadSchematicReader

    evidence: list[Fact] = []

    # Step 1: Parse schematic
    reader = KiCadSchematicReader()
    sch = reader.read(str(schematic_path))

    symbol = None
    for sym in sch.symbols:
        if sym.ref == ref:
            symbol = sym
            break

    if symbol is None:
        return SuggestionRecord(
            ref=ref,
            source_project=project_name,
            decision="no_recommendation",
            decision_reason=f"Component {ref} not found in schematic",
            evidence=[Fact(
                key="error",
                value=f"Component {ref} not found in schematic",
                source="schematic parse",
            )],
        )

    value_raw = symbol.value or ""
    category = _detect_category(ref, symbol)

    evidence.append(Fact(
        key="value_raw", value=value_raw,
        source="schematic Value field",
        evidence=[f"Value field = '{value_raw}'"],
    ))
    evidence.append(Fact(
        key="category", value=category,
        source="ref prefix / lib_id",
        evidence=[f"Detected category: {category}"],
    ))

    # Step 2: Build net graph + pin completeness
    provider = get_connectivity_provider()
    net_graph = provider.build_net_graph(schematic_path)
    connections = net_graph.get_connections(ref)
    completeness = net_graph.get_pin_completeness(ref)

    evidence.append(Fact(
        key="pin_completeness",
        value=completeness.completeness,
        source=f"net graph ({provider.backend_name})",
        evidence=[
            f"Expected pins: {completeness.expected_pins}",
            f"Resolved pins: {completeness.resolved_pins}",
            f"Completeness: {completeness.completeness:.2f}",
        ] + ([f"Unresolved: {completeness.unresolved_pins}"] if completeness.unresolved_pins else []),
    ))

    for conn in connections:
        evidence.append(Fact(
            key=f"net_{conn.pin}", value=conn.net,
            source=f"net graph ({provider.backend_name})",
            evidence=[f"Pin {conn.pin} connected to net {conn.net}"],
        ))

    # Step 3: Scan rails
    rails = scan_rails(net_graph, workspace=workspace)
    for rail in rails:
        if rail.voltage is not None:
            evidence.append(Fact(
                key=f"rail_{rail.net}", value=rail.voltage,
                unit="V", source=rail.source,
                evidence=rail.evidence,
            ))

    # Step 4: Classify role (softmax)
    role = None
    role_confidence = 0.0
    role_probabilities: dict[str, float] = {}
    if category == "capacitor":
        classification = classify_capacitor(
            ref, value_raw, connections, rails,
            pin_completeness=completeness.completeness,
        )
        role = classification.role
        role_confidence = classification.confidence
        role_probabilities = classification.role_probabilities
        evidence.append(Fact(
            key="role", value=role,
            source="cap classifier (softmax)",
            confidence=role_confidence,
            evidence=classification.evidence,
        ))

    # Step 5: Determine voltage context
    voltage_str = _infer_required_voltage(symbol, connections, rails)
    voltage_v = parse_voltage(voltage_str) if voltage_str else None

    # Step 6: Package sweep
    package_ranking: list[PackageScore] = []
    data_source = "none"
    if category == "capacitor" and value_raw:
        package_ranking, data_source = package_sweep(
            value_raw,
            voltage=voltage_str,
            use_cache=use_cache,
            use_live=use_live,
            supplier=supplier,
        )
        evidence.append(Fact(
            key="data_source", value=data_source,
            source="package sweep",
            evidence=[f"Supplier data source: {data_source}"],
        ))

    # Step 7: Compute package utility scores
    cap_farads = parse_capacitance(value_raw) if value_raw else None
    scoring_context = {
        "component_type": "capacitor",
        "target_capacitance_f": cap_farads,
        "required_voltage_v": voltage_v,
        "role": role or "unknown",
        "role_confidence": role_confidence,
        "pin_completeness": completeness.completeness,
    }

    for ps in package_ranking:
        utility, terms = compute_package_utility(ps, scoring_context)
        ps.score = utility
        ps.terms = terms

    # Re-sort by utility score
    package_ranking.sort(key=lambda s: s.score, reverse=True)

    # Step 8: Rank stability
    stability = analyze_rank_stability(
        package_ranking, scoring_context,
    )

    # Zero-evidence guard
    has_viable = any(ps.viable_count > 0 for ps in package_ranking)
    if not has_viable and package_ranking:
        stability.decision = "no_recommendation"
        stability.decision_reason = (
            "No viable/rankable supplier-backed package evidence"
        )

    # Step 9: Nested MPN scoring within top package(s)
    candidate_ranking: list[CandidateScore] = []
    top_package = None
    top_mpn = None

    if category == "capacitor" and stability.decision != "no_recommendation" and package_ranking:
        scorer = CapacitorScorer()

        # Select top 2-3 feasible packages for MPN scoring
        feasible_pkgs = [ps for ps in package_ranking if ps.viable_count > 0][:3]

        for pkg_score in feasible_pkgs:
            scoring_context["package_viable_count"] = pkg_score.viable_count

            candidates = _get_candidates_for_package(
                value_raw, voltage_str, pkg_score.package,
                use_cache=use_cache, use_live=use_live, supplier=supplier,
            )

            for cand in candidates:
                cs = scorer.score_candidate(cand, scoring_context, ref=ref)
                if cs.hard_pass:
                    candidate_ranking.append(cs)

        candidate_ranking.sort(key=lambda c: c.final_score, reverse=True)

        if candidate_ranking:
            top_mpn = candidate_ranking[0].mpn
        if feasible_pkgs:
            top_package = feasible_pkgs[0].package

    # Build context hash
    ctx_hash = compute_context_hash(ref, value_raw, connections, rails)

    # Build SuggestionRecord
    record = SuggestionRecord(
        ref=ref,
        context_hash=ctx_hash,
        source_project=project_name,
        role=role,
        role_confidence=role_confidence,
        role_probabilities=role_probabilities,
        package_ranking=package_ranking,
        candidate_ranking=candidate_ranking,
        top_candidate_mpn=top_mpn,
        top_package=top_package,
        evidence=evidence,
        weights_used=dict(DEFAULT_PACKAGE_UTILITY_WEIGHTS),
        data_source=data_source,
        decision=stability.decision,
        decision_reason=stability.decision_reason,
        rank_stability=stability,
        pin_completeness=completeness.completeness,
    )

    # Persist
    store = SuggestionStore(workspace=workspace)
    store.save(record)

    return record


def _detect_category(ref: str, symbol: Any) -> str:
    """Detect component category from ref prefix or lib_id."""
    prefix = ref.rstrip("0123456789").upper()
    if prefix == "C":
        return "capacitor"
    if prefix == "R":
        return "resistor"
    if prefix == "L":
        return "inductor"
    if prefix == "D":
        return "diode"
    if prefix == "U":
        return "ic"
    if prefix == "Q":
        return "transistor"
    if prefix == "J" or prefix == "P":
        return "connector"
    if prefix == "Y" or prefix == "X":
        return "crystal"
    return "unknown"


def _infer_required_voltage(
    symbol: Any,
    connections: list,
    rails: list,
) -> str | None:
    """Infer required voltage from schematic context.

    Uses V_required = ceil_standard(k_derate * V_rail).
    """
    from footfindr.intelligence.package_sweep import compute_required_voltage

    # Check explicit VoltageMin field
    fields = getattr(symbol, "fields", {})
    voltage_min = fields.get("VoltageMin")
    if voltage_min:
        return voltage_min

    # Infer from rail context
    for conn in connections:
        for rail in rails:
            if rail.net == conn.net and rail.voltage is not None and rail.voltage > 0:
                v_req = compute_required_voltage(rail.voltage)
                return f"{v_req}V"

    return None


def _get_candidates_for_package(
    value: str,
    voltage: str | None,
    package: str,
    *,
    use_cache: bool = True,
    use_live: bool = False,
    supplier: str | None = None,
) -> list[Any]:
    """Get supplier candidates for a specific package."""
    from footfindr.intelligence.package_sweep import (
        _build_search_query,
        _get_supplier_results,
        normalize_capacitance,
        normalize_package,
        normalize_voltage,
    )
    from footfindr.core.units import parse_capacitance, parse_voltage

    query = _build_search_query(value, voltage, package)
    results, _ = _get_supplier_results(
        query, package,
        use_cache=use_cache,
        use_live=use_live,
        supplier=supplier,
    )

    cap_farads = parse_capacitance(value)
    voltage_v = parse_voltage(voltage) if voltage else None

    # Filter viable
    viable = []
    for part in results:
        desc = getattr(part, "description", "") or ""
        part_pkg_raw = getattr(part, "package", "") or ""
        part_pkg = normalize_package(part_pkg_raw) or normalize_package(desc) or ""

        # Package match
        if part_pkg and part_pkg != package:
            if package.lower() not in part_pkg_raw.lower() and package not in desc:
                continue

        # Cap match
        part_cap = normalize_capacitance(desc)
        if cap_farads and part_cap:
            ratio = part_cap / cap_farads if cap_farads > 0 else 0
            if ratio < 0.5 or ratio > 2.0:
                continue

        # Voltage
        if voltage_v:
            part_v = normalize_voltage(desc)
            if part_v is not None and part_v < voltage_v:
                continue

        # Lifecycle
        lifecycle = (getattr(part, "lifecycle", "") or "").strip().lower()
        if lifecycle in ("obsolete", "discontinued", "eol"):
            continue

        viable.append(part)

    return viable
