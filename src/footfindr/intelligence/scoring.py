"""Probabilistic package utility / cost-function scoring (M9.4B).

Implements the formal utility cost function for package ranking:

    J_pkg(p) = E[U(p)] - λ_sigma * std(U(p))
                        - λ_CVaR * CVaR_alpha(L(p))
                        - λ_regret * E[Regret(p)]

13 utility terms + 3 interaction terms (16 dimensions):
    u_electrical, u_derating, u_dc_bias, u_package_availability,
    u_supply_diversity, u_stock, u_price, u_area, u_height,
    u_footprint, u_assembly, u_role_fit, u_data_quality,
    i_small_high_cv_risk, i_avail_diversity_synergy, i_role_package_fit

The MPN scorer (CapacitorScorer) is nested: it scores individual MPNs
within the top package(s) selected by the utility model.

Weight vector labeled: ``probabilistic_package_utility_v1``
Every ``--justify`` output prints the weights used.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from footfindr.intelligence.models import (
    CandidateScore,
    PackageEvidence,
    PackageScore,
    ScoreTerm,
    SCORING_POLICY_VERSION,
)

logger = logging.getLogger("footfindr.intelligence.scoring")


# ---------------------------------------------------------------------------
# Package utility weights (16 dimensions, sum = 1.0)
# ---------------------------------------------------------------------------

DEFAULT_PACKAGE_UTILITY_WEIGHTS: dict[str, float] = {
    "u_electrical": 0.12,
    "u_derating": 0.10,
    "u_dc_bias": 0.08,
    "u_package_availability": 0.10,
    "u_supply_diversity": 0.06,
    "u_stock": 0.08,
    "u_price": 0.08,
    "u_area": 0.06,
    "u_height": 0.03,
    "u_footprint": 0.05,
    "u_assembly": 0.02,
    "u_role_fit": 0.06,
    "u_data_quality": 0.04,
    "i_small_high_cv_risk": 0.05,
    "i_avail_diversity_synergy": 0.04,
    "i_role_package_fit": 0.03,
}

assert abs(sum(DEFAULT_PACKAGE_UTILITY_WEIGHTS.values()) - 1.0) < 1e-6, (
    f"Weights must sum to 1.0, got {sum(DEFAULT_PACKAGE_UTILITY_WEIGHTS.values())}"
)


# ---------------------------------------------------------------------------
# MPN-level scoring weights (for CapacitorScorer)
# ---------------------------------------------------------------------------

DEFAULT_CAP_WEIGHTS: dict[str, float] = {
    "capacitance_match": 0.20,
    "voltage_derating": 0.15,
    "package_viability": 0.15,
    "stock": 0.10,
    "price": 0.10,
    "lifecycle": 0.10,
    "footprint_confidence": 0.08,
    "manufacturer_preference": 0.05,
    "role_confidence": 0.05,
    "data_completeness": 0.02,
}

assert abs(sum(DEFAULT_CAP_WEIGHTS.values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Scoring formula functions
# ---------------------------------------------------------------------------

def score_capacitance_match(
    target_farads: float,
    actual_farads: float,
    tau: float = 0.3,
) -> float:
    """s_C = exp(-(e_C / tau_C)²)"""
    if target_farads <= 0 or actual_farads <= 0:
        return 0.0
    e_c = math.log10(actual_farads / target_farads)
    return math.exp(-(e_c / tau) ** 2)


def score_voltage_derating(
    actual_voltage: float,
    required_voltage: float,
    target_margin: float = 2.0,
    k: float = 5.0,
) -> float:
    """s_V = sigmoid(k * (margin - target_margin/2))"""
    if required_voltage <= 0 or actual_voltage <= 0:
        return 0.0
    margin = actual_voltage / required_voltage
    x = k * (margin - target_margin / 2)
    return 1.0 / (1.0 + math.exp(-x))


def score_stock(stock: int, target: int = 10000) -> float:
    """s_stock = min(1, log(1+stock) / log(1+target))"""
    if stock <= 0:
        return 0.0
    return min(1.0, math.log(1 + stock) / math.log(1 + target))


def score_price(unit_price: float, scale: float = 0.50) -> float:
    """s_price = exp(-unit_price / scale)"""
    if unit_price <= 0:
        return 1.0
    return math.exp(-unit_price / scale)


def score_package_viability(n_viable: int, n0: float = 5.0) -> float:
    """s_avail = 1 - exp(-N_viable / N0)"""
    if n_viable <= 0:
        return 0.0
    return 1.0 - math.exp(-n_viable / n0)


def score_lifecycle(lifecycle: str | None) -> float:
    if not lifecycle:
        return 0.5
    lc = lifecycle.strip().lower()
    if lc in ("active", "new"):
        return 1.0
    if lc in ("nrnd", "not recommended for new designs"):
        return 0.3
    if lc in ("obsolete", "discontinued", "eol"):
        return 0.0
    return 0.5


def score_manufacturer_pref(manufacturer: str | None) -> float:
    if not manufacturer:
        return 0.3
    mfr = manufacturer.strip().upper()
    tier1 = {"MURATA", "TDK", "SAMSUNG", "KYOCERA AVX", "KEMET", "YAGEO",
             "VISHAY", "PANASONIC", "TAIYO YUDEN", "SAMSUNG ELECTRO-MECHANICS"}
    if mfr in tier1 or any(t in mfr for t in tier1):
        return 1.0
    tier2 = {"BOURNS", "WURTH", "WALSIN", "JOHANSON", "DARFON"}
    if mfr in tier2 or any(t in mfr for t in tier2):
        return 0.7
    return 0.4


def score_data_completeness(
    has_datasheet: bool = False,
    has_lifecycle: bool = False,
    has_stock: bool = False,
    has_price: bool = False,
    has_package: bool = False,
) -> float:
    flags = [has_datasheet, has_lifecycle, has_stock, has_price, has_package]
    return sum(1 for f in flags if f) / len(flags)


# ---------------------------------------------------------------------------
# Package utility computation
# ---------------------------------------------------------------------------

def compute_package_utility(
    pkg_score: PackageScore,
    context: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> tuple[float, list[ScoreTerm]]:
    """Compute full package utility score with all 16 terms.

    Args:
        pkg_score: PackageScore with evidence data
        context: {target_capacitance_f, required_voltage_v, role,
                  role_confidence, pin_completeness, ...}
        weights: Weight vector (default: DEFAULT_PACKAGE_UTILITY_WEIGHTS)

    Returns:
        (utility_score, list of ScoreTerms)
    """
    w = weights or dict(DEFAULT_PACKAGE_UTILITY_WEIGHTS)
    ev = pkg_score.package_evidence or PackageEvidence(package=pkg_score.package)

    cap_farads = context.get("target_capacitance_f")
    required_v = context.get("required_voltage_v")
    role = context.get("role", "unknown")
    role_confidence = context.get("role_confidence", 0.5)
    pin_completeness = context.get("pin_completeness", 1.0)

    terms: list[ScoreTerm] = []

    # --- 1. u_electrical (capacitance availability in this package) ---
    u_elec = score_package_viability(ev.viable_count, n0=5.0)
    terms.append(_term("u_electrical", u_elec, w, "Viable part count for this package"))

    # --- 2. u_derating (voltage headroom for this package) ---
    # Larger packages tend to have better voltage ratings
    u_derate = 0.5  # neutral if no voltage data
    if required_v and ev.viable_count > 0:
        # Assume larger packages have more voltage headroom
        area = pkg_score.area_mm2 or 1.0
        area_factor = min(1.0, math.log(1 + area) / math.log(1 + 5.0))
        u_derate = 0.3 + 0.7 * area_factor
    terms.append(_term("u_derating", u_derate, w, "Package voltage headroom estimate"))

    # --- 3. u_dc_bias (DC bias risk) ---
    u_dc = _compute_dc_bias_utility(pkg_score, cap_farads, required_v)
    terms.append(_term("u_dc_bias", u_dc, w, "DC bias derating risk"))

    # --- 4. u_package_availability ---
    from footfindr.intelligence.bayesian_model import wilson_lower_bound
    q_lcb = wilson_lower_bound(ev.viable_count, ev.parsed_count)
    u_avail = min(1.0, q_lcb + 0.3 * (ev.viable_count / max(1, ev.parsed_count)))
    terms.append(_term("u_package_availability", u_avail, w, f"Wilson LCB={q_lcb:.3f}"))

    # --- 5. u_supply_diversity (manufacturer entropy) ---
    max_entropy = math.log(max(1, ev.manufacturer_count)) if ev.manufacturer_count > 1 else 1.0
    u_div = min(1.0, ev.manufacturer_entropy / max(max_entropy, 0.01))
    terms.append(_term("u_supply_diversity", u_div, w, f"Entropy={ev.manufacturer_entropy:.3f}"))

    # --- 6. u_stock ---
    u_stock = min(1.0, math.log(1 + ev.total_stock) / math.log(1 + 10000)) if ev.total_stock > 0 else 0.0
    terms.append(_term("u_stock", u_stock, w, f"Total stock={ev.total_stock}"))

    # --- 7. u_price ---
    u_price = 0.5
    if ev.median_price is not None and ev.median_price > 0:
        u_price = math.exp(-ev.median_price / 0.50)
    terms.append(_term("u_price", u_price, w, f"Median=${ev.median_price}"))

    # --- 8. u_area (smaller is better for most designs) ---
    area = pkg_score.area_mm2 or 10.0
    u_area = max(0.0, 1.0 - area / 20.0)  # 20mm² = worst
    terms.append(_term("u_area", u_area, w, f"Area={area:.2f}mm²"))

    # --- 9. u_height ---
    height = pkg_score.height_mm or 2.0
    u_height = max(0.0, 1.0 - height / 5.0)  # 5mm = worst
    terms.append(_term("u_height", u_height, w, f"Height={height:.2f}mm"))

    # --- 10. u_footprint (is footprint known/available?) ---
    u_fp = 1.0 if pkg_score.package in ("0402", "0603", "0805", "1206") else 0.7
    terms.append(_term("u_footprint", u_fp, w, "Footprint availability"))

    # --- 11. u_assembly (assembly difficulty) ---
    # Smaller = harder to assemble
    assembly_map = {"0201": 0.3, "0402": 0.6, "0603": 0.9, "0805": 1.0,
                    "1206": 1.0, "1210": 0.95, "1812": 0.9, "2220": 0.85}
    u_asm = assembly_map.get(pkg_score.package, 0.7)
    terms.append(_term("u_assembly", u_asm, w, f"Assembly difficulty for {pkg_score.package}"))

    # --- 12. u_role_fit ---
    u_role = _compute_role_fit(pkg_score, role, role_confidence, cap_farads)
    terms.append(_term("u_role_fit", u_role, w, f"Role={role}, conf={role_confidence:.3f}"))

    # --- 13. u_data_quality ---
    u_dq = ev.attribute_completeness if ev.attribute_completeness > 0 else 0.3
    terms.append(_term("u_data_quality", u_dq, w, f"Attr completeness={ev.attribute_completeness:.3f}"))

    # --- Interaction terms ---

    # I1: small package + high C + high V = risk
    i_risk = _interaction_small_cv_risk(pkg_score, cap_farads, required_v)
    terms.append(_term("i_small_high_cv_risk", 1.0 - i_risk, w, f"Risk penalty={i_risk:.3f}"))

    # I2: availability * diversity synergy
    i_synergy = u_avail * u_div
    terms.append(_term("i_avail_diversity_synergy", i_synergy, w, "Availability × diversity"))

    # I3: role-package fit interaction
    i_role_pkg = u_role * u_fp * pin_completeness
    terms.append(_term("i_role_package_fit", i_role_pkg, w, "Role × footprint × pin completeness"))

    # Compute weighted sum
    total = sum(t.contribution for t in terms)

    return round(total, 4), terms


def _term(
    name: str,
    value: float,
    weights: dict[str, float],
    source: str,
) -> ScoreTerm:
    """Create a ScoreTerm."""
    w = weights.get(name, 0.0)
    return ScoreTerm(
        name=name,
        value=round(value, 4),
        weight=w,
        contribution=round(value * w, 4),
        source_facts=[source],
    )


def _compute_dc_bias_utility(
    pkg: PackageScore,
    cap_farads: float | None,
    required_v: float | None,
) -> float:
    """DC bias risk model:
    R_bias = sigmoid(a0 + a1*log(C) + a2*(V/V_rated) - a3*log(area) + a4*class_II)
    u_dc_bias = 1 - R_bias
    """
    if cap_farads is None or required_v is None:
        return 0.5  # neutral

    area = pkg.area_mm2 or 1.0
    # Class II ceramic (MLCC) assumption for now
    a0, a1, a2, a3, a4 = 0.0, 0.8, 1.5, 1.2, 0.5

    log_c = math.log10(max(cap_farads, 1e-15))
    v_ratio = required_v / max(required_v * 2, 1.0)  # normalized
    log_area = math.log10(max(area, 0.01))

    z = a0 + a1 * (-log_c - 5) + a2 * v_ratio - a3 * log_area + a4 * 1.0
    r_bias = 1.0 / (1.0 + math.exp(-z))
    return round(1.0 - r_bias, 4)


def _compute_role_fit(
    pkg: PackageScore,
    role: str,
    role_confidence: float,
    cap_farads: float | None,
) -> float:
    """Compute how well a package fits the detected role."""
    if role in ("rail_input_decoupling", "rail_output_cap", "regulator_stability_output_cap") and role_confidence > 0.5:
        # Decoupling/bulk caps benefit from larger packages at higher C
        if cap_farads and cap_farads > 1e-6:
            area = pkg.area_mm2 or 1.0
            if area >= 2.0:
                return min(1.0, 0.7 + 0.3 * role_confidence)
            else:
                return 0.5  # small package for large C = risky
        return 0.7 * role_confidence
    if role in ("dc_block", "rc_filter"):
        return 0.6 * role_confidence
    return 0.5  # unknown_review or other role


def _interaction_small_cv_risk(
    pkg: PackageScore,
    cap_farads: float | None,
    required_v: float | None,
) -> float:
    """Risk penalty for small packages with high C and/or high V.

    Returns risk value in [0, 1] where 1 = high risk.
    """
    if cap_farads is None:
        return 0.0

    area = pkg.area_mm2 or 10.0
    risk = 0.0

    # Small package + high capacitance
    if area < 2.0 and cap_farads > 1e-6:
        risk += 0.3
    if area < 1.0 and cap_farads > 100e-9:
        risk += 0.2

    # Small package + high voltage
    if required_v and area < 2.0 and required_v > 25:
        risk += 0.3

    return min(1.0, risk)


# ---------------------------------------------------------------------------
# TOPSIS implementation (for MPN scoring)
# ---------------------------------------------------------------------------

def topsis_score(terms: list[ScoreTerm]) -> float:
    """TOPSIS closeness to ideal = d_anti / (d_ideal + d_anti)"""
    if not terms:
        return 0.0

    d_ideal_sq = 0.0
    d_anti_sq = 0.0

    for term in terms:
        weighted = term.weight * term.value
        ideal_val = term.weight * 1.0
        anti_val = term.weight * 0.0

        d_ideal_sq += (ideal_val - weighted) ** 2
        d_anti_sq += (anti_val - weighted) ** 2

    d_ideal = math.sqrt(d_ideal_sq)
    d_anti = math.sqrt(d_anti_sq)

    if d_ideal + d_anti == 0:
        return 0.5

    return d_anti / (d_ideal + d_anti)


# ---------------------------------------------------------------------------
# Passive scorer base class
# ---------------------------------------------------------------------------

class PassiveScorer(ABC):
    """Base class for passive component scorers."""

    @abstractmethod
    def compute_hard_constraints(
        self,
        candidate: Any,
        context: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        ...

    @abstractmethod
    def compute_score_terms(
        self,
        candidate: Any,
        context: dict[str, Any],
    ) -> list[ScoreTerm]:
        ...

    def score_candidate(
        self,
        candidate: Any,
        context: dict[str, Any],
        *,
        ref: str = "",
    ) -> CandidateScore:
        """Full scoring pipeline: hard constraints + TOPSIS."""
        mpn = getattr(candidate, "mpn", "") or ""
        manufacturer = getattr(candidate, "manufacturer", "") or ""
        package = getattr(candidate, "package", "") or ""
        candidate_id = f"{getattr(candidate, 'supplier', '')}|{manufacturer}|{mpn}|{getattr(candidate, 'supplier_pn', '')}"

        passes, fail_reasons = self.compute_hard_constraints(candidate, context)

        if not passes:
            return CandidateScore(
                ref=ref,
                candidate_id=candidate_id,
                mpn=mpn,
                manufacturer=manufacturer,
                package=package,
                component_type=context.get("component_type", ""),
                hard_pass=False,
                hard_fail_reasons=fail_reasons,
            )

        terms = self.compute_score_terms(candidate, context)
        t_score = topsis_score(terms)

        uncertainty = self._compute_uncertainty_penalty(terms)
        risk = self._compute_risk_penalty(candidate, context)

        final = max(0.0, t_score - uncertainty - risk)

        return CandidateScore(
            ref=ref,
            candidate_id=candidate_id,
            mpn=mpn,
            manufacturer=manufacturer,
            package=package,
            component_type=context.get("component_type", "capacitor"),
            hard_pass=True,
            terms=terms,
            topsis_score=round(t_score, 4),
            uncertainty_penalty=round(uncertainty, 4),
            risk_penalty=round(risk, 4),
            final_score=round(final, 4),
        )

    def _compute_uncertainty_penalty(self, terms: list[ScoreTerm]) -> float:
        total = sum(t.missing_data_penalty * t.weight for t in terms)
        return min(total, 0.3)

    def _compute_risk_penalty(self, candidate: Any, context: dict[str, Any]) -> float:
        penalty = 0.0
        lifecycle = getattr(candidate, "lifecycle", None) or ""
        if lifecycle.lower() in ("nrnd", "not recommended for new designs"):
            penalty += 0.05
        role_confidence = context.get("role_confidence", 1.0)
        if role_confidence < 0.5:
            penalty += 0.03
        return penalty


# ---------------------------------------------------------------------------
# Capacitor MPN scorer
# ---------------------------------------------------------------------------

class CapacitorScorer(PassiveScorer):
    """Capacitor MPN scorer using TOPSIS."""

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or dict(DEFAULT_CAP_WEIGHTS)

    def compute_hard_constraints(
        self,
        candidate: Any,
        context: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        from footfindr.intelligence.package_sweep import normalize_capacitance, normalize_voltage

        failures: list[str] = []

        target_cap = context.get("target_capacitance_f")
        if target_cap:
            desc = getattr(candidate, "description", "") or ""
            part_cap = normalize_capacitance(desc)
            if part_cap is not None:
                ratio = part_cap / target_cap if target_cap > 0 else 0
                if ratio < 0.5 or ratio > 2.0:
                    failures.append(
                        f"Capacitance mismatch: target={target_cap:.2e}F, "
                        f"candidate={part_cap:.2e}F"
                    )

        required_v = context.get("required_voltage_v")
        if required_v:
            desc = getattr(candidate, "description", "") or ""
            part_v = normalize_voltage(desc)
            if part_v is not None and part_v < required_v:
                failures.append(
                    f"Insufficient voltage: required={required_v}V, "
                    f"candidate={part_v}V"
                )

        lifecycle = getattr(candidate, "lifecycle", None) or ""
        if lifecycle.lower() in ("obsolete", "discontinued", "eol"):
            failures.append(f"Lifecycle: {lifecycle}")

        return (len(failures) == 0, failures)

    def compute_score_terms(
        self,
        candidate: Any,
        context: dict[str, Any],
    ) -> list[ScoreTerm]:
        from footfindr.intelligence.package_sweep import normalize_capacitance, normalize_voltage

        terms: list[ScoreTerm] = []

        target_cap = context.get("target_capacitance_f")
        required_v = context.get("required_voltage_v")
        role_conf = context.get("role_confidence", 1.0)
        desc = getattr(candidate, "description", "") or ""

        # 1. Capacitance match
        cap_score = 0.5
        missing_cap = 0.0
        part_cap = normalize_capacitance(desc)
        if target_cap and part_cap:
            cap_score = score_capacitance_match(target_cap, part_cap)
        elif target_cap and not part_cap:
            missing_cap = 0.5
        terms.append(ScoreTerm(
            name="capacitance_match",
            value=round(cap_score, 4),
            weight=self.weights["capacitance_match"],
            contribution=round(cap_score * self.weights["capacitance_match"], 4),
            source_facts=["capacitance value comparison"],
            missing_data_penalty=missing_cap,
        ))

        # 2. Voltage derating
        v_score = 0.5
        missing_v = 0.0
        part_v = normalize_voltage(desc)
        if required_v and part_v:
            v_score = score_voltage_derating(part_v, required_v)
        elif required_v and not part_v:
            missing_v = 0.5
        terms.append(ScoreTerm(
            name="voltage_derating",
            value=round(v_score, 4),
            weight=self.weights["voltage_derating"],
            contribution=round(v_score * self.weights["voltage_derating"], 4),
            source_facts=["voltage rating vs required"],
            missing_data_penalty=missing_v,
        ))

        # 3. Package viability
        pkg_viable = context.get("package_viable_count", 0)
        pkg_score = score_package_viability(pkg_viable)
        terms.append(ScoreTerm(
            name="package_viability",
            value=round(pkg_score, 4),
            weight=self.weights["package_viability"],
            contribution=round(pkg_score * self.weights["package_viability"], 4),
            source_facts=["package sweep viable count"],
        ))

        # 4. Stock
        stock = getattr(candidate, "stock", 0) or 0
        stk_score = score_stock(stock)
        terms.append(ScoreTerm(
            name="stock",
            value=round(stk_score, 4),
            weight=self.weights["stock"],
            contribution=round(stk_score * self.weights["stock"], 4),
            source_facts=["supplier stock level"],
            missing_data_penalty=0.3 if stock == 0 else 0.0,
        ))

        # 5. Price
        price = _get_unit_price(candidate)
        price_score = score_price(price) if price is not None and price > 0 else 0.5
        terms.append(ScoreTerm(
            name="price",
            value=round(price_score, 4),
            weight=self.weights["price"],
            contribution=round(price_score * self.weights["price"], 4),
            source_facts=["unit price"],
            missing_data_penalty=0.3 if price is None else 0.0,
        ))

        # 6. Lifecycle
        lifecycle = getattr(candidate, "lifecycle", None) or ""
        lc_score = score_lifecycle(lifecycle)
        terms.append(ScoreTerm(
            name="lifecycle",
            value=round(lc_score, 4),
            weight=self.weights["lifecycle"],
            contribution=round(lc_score * self.weights["lifecycle"], 4),
            source_facts=["lifecycle status"],
            missing_data_penalty=0.2 if not lifecycle else 0.0,
        ))

        # 7. Footprint confidence
        fp = getattr(candidate, "package", "") or ""
        fp_score = 1.0 if fp else 0.3
        terms.append(ScoreTerm(
            name="footprint_confidence",
            value=round(fp_score, 4),
            weight=self.weights["footprint_confidence"],
            contribution=round(fp_score * self.weights["footprint_confidence"], 4),
            source_facts=["package/footprint availability"],
        ))

        # 8. Manufacturer preference
        mfr = getattr(candidate, "manufacturer", None)
        mfr_score = score_manufacturer_pref(mfr)
        terms.append(ScoreTerm(
            name="manufacturer_preference",
            value=round(mfr_score, 4),
            weight=self.weights["manufacturer_preference"],
            contribution=round(mfr_score * self.weights["manufacturer_preference"], 4),
            source_facts=["manufacturer tier"],
        ))

        # 9. Role confidence
        terms.append(ScoreTerm(
            name="role_confidence",
            value=round(role_conf, 4),
            weight=self.weights["role_confidence"],
            contribution=round(role_conf * self.weights["role_confidence"], 4),
            source_facts=["capacitor role classification"],
        ))

        # 10. Data completeness
        dc_score = score_data_completeness(
            has_datasheet=bool(getattr(candidate, "datasheet_url", None)),
            has_lifecycle=bool(lifecycle),
            has_stock=stock > 0,
            has_price=price is not None and price > 0,
            has_package=bool(fp),
        )
        terms.append(ScoreTerm(
            name="data_completeness",
            value=round(dc_score, 4),
            weight=self.weights["data_completeness"],
            contribution=round(dc_score * self.weights["data_completeness"], 4),
            source_facts=["data field availability"],
        ))

        return terms


# ---------------------------------------------------------------------------
# Resistor scorer (skeleton only)
# ---------------------------------------------------------------------------

class ResistorScorer(PassiveScorer):
    """Resistor scorer — skeleton only."""

    def compute_hard_constraints(self, candidate: Any, context: dict[str, Any]) -> tuple[bool, list[str]]:
        return (True, [])

    def compute_score_terms(self, candidate: Any, context: dict[str, Any]) -> list[ScoreTerm]:
        return []


# ---------------------------------------------------------------------------
# Inductor scorer (skeleton only)
# ---------------------------------------------------------------------------

class InductorScorer(PassiveScorer):
    """Inductor scorer — skeleton only."""

    def compute_hard_constraints(self, candidate: Any, context: dict[str, Any]) -> tuple[bool, list[str]]:
        return (True, [])

    def compute_score_terms(self, candidate: Any, context: dict[str, Any]) -> list[ScoreTerm]:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_unit_price(part: Any, qty: int = 1) -> float | None:
    price_breaks = getattr(part, "price_breaks", None)
    if price_breaks:
        if isinstance(price_breaks, list) and len(price_breaks) > 0:
            pb = price_breaks[0]
            if isinstance(pb, dict):
                return pb.get("unit_price")
            return getattr(pb, "unit_price", None)
    return getattr(part, "unit_price", None)
