"""Bayesian package feasibility model (M9.4B).

Implements conservative viability lower bounds for package scoring.

Uses **Wilson score interval** (with continuity correction) as the
primary method for the lower bound on viability proportion.  This is
preferred over a normal approximation because it behaves correctly at
boundary cases:

    n_parsed=0, n_viable=0  → q_LCB ≈ 0
    n_parsed=3, n_viable=0  → q_LCB ≈ 0
    n_parsed=3, n_viable=3  → q_LCB > 0 but conservative
    n_parsed=100, n_viable=80 → q_LCB near 0.72

Also provides a Jeffreys-style posterior estimate using the
Beta(alpha + n_viable, beta + n_fail) parameterization.

No scipy dependency.  All computations use stdlib ``math``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Wilson score lower bound
# ---------------------------------------------------------------------------

def wilson_lower_bound(
    n_viable: int,
    n_parsed: int,
    *,
    z: float = 1.96,        # 95% confidence
    continuity: bool = True,
) -> float:
    """Wilson score interval lower bound for binomial proportion.

    Args:
        n_viable: Number of viable (success) items.
        n_parsed: Total number of parsed items.
        z: Z-score for confidence level (1.96 = 95%).
        continuity: Apply continuity correction (recommended for small n).

    Returns:
        Lower bound of the confidence interval [0, 1].
        Returns 0.0 when n_parsed == 0.

    Behavior at key points:
        (0, 0)   → 0.0 (no evidence)
        (0, 3)   → ~0.0 (some evidence, all failed)
        (3, 3)   → ~0.44 (small sample, all pass)
        (80, 100) → ~0.71 (large sample, mostly pass)
    """
    if n_parsed <= 0:
        return 0.0

    n = n_parsed
    p_hat = n_viable / n
    z2 = z * z

    if continuity and n > 0:
        # Wilson with continuity correction
        # Lower bound with correction term
        correction = 1.0 / (2.0 * n)
        p_adj = max(0.0, p_hat - correction)
        denom = 1.0 + z2 / n
        center = (p_adj + z2 / (2.0 * n)) / denom
        inner = max(0.0, (p_adj * (1.0 - p_adj) + z2 / (4.0 * n)) / n)
        spread = z * math.sqrt(inner) / denom
        lower = max(0.0, center - spread)
    else:
        # Standard Wilson (no continuity correction)
        denom = 1.0 + z2 / n
        center = (p_hat + z2 / (2.0 * n)) / denom
        inner = max(0.0, (p_hat * (1.0 - p_hat) + z2 / (4.0 * n)) / n)
        spread = z * math.sqrt(inner) / denom
        lower = max(0.0, center - spread)

    return lower


# ---------------------------------------------------------------------------
# Jeffreys posterior estimate (Beta distribution)
# ---------------------------------------------------------------------------

def jeffreys_estimate(
    n_viable: int,
    n_parsed: int,
    *,
    alpha0: float = 0.5,   # Jeffreys prior
    beta0: float = 0.5,
) -> tuple[float, float]:
    """Jeffreys posterior mean and approximate lower bound.

    Uses Beta(alpha0 + n_viable, beta0 + n_fail) posterior.

    Returns:
        (posterior_mean, approximate_lower_bound)

    The lower bound uses a conservative estimate:
        lower ≈ mean - 2 * std(Beta)
    clamped to [0, 1].
    """
    alpha = alpha0 + n_viable
    beta = beta0 + (n_parsed - n_viable)

    mean = alpha / (alpha + beta)

    # Variance of Beta distribution
    var = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
    std = math.sqrt(var)

    lower = max(0.0, mean - 2.0 * std)
    return (mean, lower)


# ---------------------------------------------------------------------------
# Feasibility assessment
# ---------------------------------------------------------------------------

@dataclass
class PackageFeasibility:
    """Feasibility assessment for a package size."""
    package: str
    n_parsed: int = 0
    n_viable: int = 0
    q_hat: float = 0.0          # point estimate (proportion)
    q_lcb: float = 0.0          # Wilson lower confidence bound
    q_jeffreys_mean: float = 0.0
    q_jeffreys_lcb: float = 0.0
    is_rankable: bool = False
    status: str = ""             # "sufficient", "marginal", "insufficient_evidence"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "n_parsed": self.n_parsed,
            "n_viable": self.n_viable,
            "q_hat": round(self.q_hat, 4),
            "q_lcb": round(self.q_lcb, 4),
            "q_jeffreys_mean": round(self.q_jeffreys_mean, 4),
            "q_jeffreys_lcb": round(self.q_jeffreys_lcb, 4),
            "is_rankable": self.is_rankable,
            "status": self.status,
            "evidence": self.evidence,
        }


# Minimum parsed count for rankability (even 1 viable with 0 parsed is not rankable)
_MIN_PARSED = 1


def assess_feasibility(
    package: str,
    n_parsed: int,
    n_viable: int,
    *,
    min_parsed: int = _MIN_PARSED,
    min_viable_for_confidence: int = 3,
    z: float = 1.96,
) -> PackageFeasibility:
    """Assess package feasibility from viability counts.

    Args:
        package: Package name (e.g. "0603")
        n_parsed: Total parsed results
        n_viable: Results passing all filters
        min_parsed: Minimum parsed results for rankability
        min_viable_for_confidence: Minimum viable for "sufficient" status
        z: Z-score for Wilson interval

    Returns:
        PackageFeasibility with viability bounds and rankability.
    """
    evidence: list[str] = []

    if n_parsed <= 0:
        return PackageFeasibility(
            package=package,
            n_parsed=0,
            n_viable=0,
            status="insufficient_evidence",
            evidence=["No parsed results available"],
        )

    q_hat = n_viable / n_parsed if n_parsed > 0 else 0.0
    q_lcb = wilson_lower_bound(n_viable, n_parsed, z=z)
    j_mean, j_lcb = jeffreys_estimate(n_viable, n_parsed)

    evidence.append(f"Parsed {n_parsed} results, {n_viable} viable")
    evidence.append(f"Point estimate q_hat = {q_hat:.3f}")
    evidence.append(f"Wilson LCB (95%) = {q_lcb:.3f}")
    evidence.append(f"Jeffreys posterior mean = {j_mean:.3f}")

    # Rankability
    is_rankable = n_parsed >= min_parsed and n_viable > 0

    # Status
    if n_viable <= 0:
        status = "insufficient_evidence"
        evidence.append("Zero viable results — not rankable")
    elif n_parsed < min_parsed:
        status = "insufficient_evidence"
        evidence.append(f"Parsed count {n_parsed} below minimum {min_parsed}")
    elif n_viable < min_viable_for_confidence:
        status = "marginal"
        evidence.append(f"Viable count {n_viable} below confidence threshold {min_viable_for_confidence}")
    else:
        status = "sufficient"
        evidence.append(f"Viable count {n_viable} meets confidence threshold")

    return PackageFeasibility(
        package=package,
        n_parsed=n_parsed,
        n_viable=n_viable,
        q_hat=round(q_hat, 4),
        q_lcb=round(q_lcb, 4),
        q_jeffreys_mean=round(j_mean, 4),
        q_jeffreys_lcb=round(j_lcb, 4),
        is_rankable=is_rankable,
        status=status,
        evidence=evidence,
    )
