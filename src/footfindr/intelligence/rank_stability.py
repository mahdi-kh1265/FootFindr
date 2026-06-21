"""Monte Carlo rank stability analysis (M9.4B).

Samples N=200 perturbations to determine if the top package
recommendation is stable under uncertainty:

    1. Perturb weights via Dirichlet(α * 100)
    2. Perturb package viable counts via Poisson noise
    3. Perturb rail voltage ± uncertainty
    4. Sample role from softmax distribution

For each sample, compute J_p and record winner.

Decision rules:
    no feasible packages     → "no_recommendation"
    P_win(top) < 0.65        → "review" (unstable)
    score_margin < threshold → "review" (close tradeoff)
    else                     → "recommend"
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

from footfindr.intelligence.models import (
    PackageScore,
    PackageStats,
    RankStabilityResult,
)

logger = logging.getLogger("footfindr.intelligence.rank_stability")


# ---------------------------------------------------------------------------
# Dirichlet sampling (stdlib only, no numpy/scipy)
# ---------------------------------------------------------------------------

def _sample_dirichlet(alphas: list[float], rng: random.Random) -> list[float]:
    """Sample from Dirichlet distribution using Gamma variates.

    Dir(α1, ..., αk) = normalize(Gamma(α1,1), ..., Gamma(αk,1))
    """
    samples = [rng.gammavariate(a, 1.0) for a in alphas]
    total = sum(samples)
    if total <= 0:
        n = len(samples)
        return [1.0 / n] * n
    return [s / total for s in samples]


def _poisson(lam: float, rng: random.Random) -> int:
    """Sample from Poisson(λ) using Knuth's algorithm."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p < L:
            break
    return k - 1


# ---------------------------------------------------------------------------
# Rank stability analysis
# ---------------------------------------------------------------------------

def analyze_rank_stability(
    package_scores: list[PackageScore],
    context: dict[str, Any],
    *,
    n_samples: int = 200,
    seed: int | None = 42,
    weights: dict[str, float] | None = None,
) -> RankStabilityResult:
    """Run Monte Carlo rank stability analysis.

    Args:
        package_scores: PackageScore list from package sweep
        context: Scoring context dict
        n_samples: Number of MC samples
        seed: RNG seed (deterministic by default)
        weights: Nominal weight vector

    Returns:
        RankStabilityResult with per-package stats and decision.
    """
    from footfindr.intelligence.scoring import (
        DEFAULT_PACKAGE_UTILITY_WEIGHTS,
        compute_package_utility,
    )

    if not package_scores:
        return RankStabilityResult(
            decision="no_recommendation",
            decision_reason="No packages to evaluate",
            n_samples=0,
        )

    # Check if any package is feasible (has viable evidence)
    feasible = [ps for ps in package_scores if ps.viable_count > 0]
    if not feasible:
        return RankStabilityResult(
            package_stats={
                ps.package: PackageStats(package=ps.package)
                for ps in package_scores
            },
            decision="no_recommendation",
            decision_reason="No viable/rankable supplier-backed package evidence",
            n_samples=0,
        )

    nominal_weights = weights or dict(DEFAULT_PACKAGE_UTILITY_WEIGHTS)
    weight_names = list(nominal_weights.keys())
    nominal_alphas = [nominal_weights[k] * 100 for k in weight_names]

    rng = random.Random(seed)

    # Per-package score accumulator
    scores_per_pkg: dict[str, list[float]] = {
        ps.package: [] for ps in package_scores
    }
    win_counts: dict[str, int] = {
        ps.package: 0 for ps in package_scores
    }

    for _ in range(n_samples):
        # 1. Perturb weights
        sampled_w = _sample_dirichlet(nominal_alphas, rng)
        perturbed_weights = dict(zip(weight_names, sampled_w))

        # 2. Perturb context
        perturbed_ctx = dict(context)
        rail_v = context.get("required_voltage_v")
        if rail_v and rail_v > 0:
            noise = rng.gauss(0, rail_v * 0.05)  # ±5% voltage uncertainty
            perturbed_ctx["required_voltage_v"] = max(0.1, rail_v + noise)

        # 3. Perturb role confidence
        rc = context.get("role_confidence", 0.5)
        perturbed_ctx["role_confidence"] = max(0.01, min(0.99, rc + rng.gauss(0, 0.05)))

        # 4. Compute utility for each package
        sample_scores: dict[str, float] = {}
        for ps in package_scores:
            # Perturb viable count via Poisson
            perturbed_ps = _perturb_package_score(ps, rng)
            score, _ = compute_package_utility(
                perturbed_ps, perturbed_ctx, perturbed_weights,
            )
            sample_scores[ps.package] = score
            scores_per_pkg[ps.package].append(score)

        # Record winner
        if sample_scores:
            winner = max(sample_scores, key=lambda k: sample_scores[k])
            win_counts[winner] = win_counts.get(winner, 0) + 1

    # Compute statistics
    stats: dict[str, PackageStats] = {}
    for pkg, scores in scores_per_pkg.items():
        if not scores:
            stats[pkg] = PackageStats(package=pkg)
            continue
        mean_s = sum(scores) / len(scores)
        var_s = sum((s - mean_s) ** 2 for s in scores) / len(scores)
        std_s = math.sqrt(var_s)
        p_win = win_counts.get(pkg, 0) / n_samples

        stats[pkg] = PackageStats(
            package=pkg,
            mean_score=round(mean_s, 4),
            std_score=round(std_s, 4),
            p_win=round(p_win, 4),
        )

    # Compute margins
    sorted_by_mean = sorted(stats.values(), key=lambda s: s.mean_score, reverse=True)
    if len(sorted_by_mean) >= 2:
        top = sorted_by_mean[0]
        second = sorted_by_mean[1]
        top.score_margin = round(top.mean_score - second.mean_score, 4)

    # Decision
    top_pkg = sorted_by_mean[0] if sorted_by_mean else None
    if top_pkg is None:
        decision = "no_recommendation"
        reason = "No packages evaluated"
    elif top_pkg.p_win < 0.65:
        decision = "review"
        reason = (
            f"Top package {top_pkg.package} wins only {top_pkg.p_win:.0%} of samples "
            f"(threshold: 65%). Ranking is unstable."
        )
    elif top_pkg.score_margin < 0.02:
        decision = "review"
        reason = (
            f"Score margin ({top_pkg.score_margin:.4f}) between top packages is very small. "
            f"Multiple packages are competitive."
        )
    else:
        decision = "recommend"
        reason = (
            f"Package {top_pkg.package} wins {top_pkg.p_win:.0%} of samples "
            f"with margin {top_pkg.score_margin:.4f}."
        )

    return RankStabilityResult(
        package_stats=stats,
        decision=decision,
        decision_reason=reason,
        n_samples=n_samples,
    )


def _perturb_package_score(
    ps: PackageScore,
    rng: random.Random,
) -> PackageScore:
    """Create a perturbed copy of a PackageScore for MC sampling."""
    from footfindr.intelligence.models import PackageEvidence

    # Poisson-perturb viable count
    viable = _poisson(max(1, ps.viable_count), rng) if ps.viable_count > 0 else 0
    # Guard: viable cannot exceed parsed
    parsed = ps.package_evidence.parsed_count if ps.package_evidence else max(viable, ps.viable_count)
    viable = min(viable, parsed)
    active = min(viable, _poisson(max(1, ps.active_count), rng) if ps.active_count > 0 else 0)
    in_stock = min(viable, _poisson(max(1, ps.in_stock_count), rng) if ps.in_stock_count > 0 else 0)
    mfr_count = max(1, ps.manufacturer_count + rng.randint(-1, 1)) if ps.manufacturer_count > 0 else 0

    # Perturb price
    median_price = ps.median_price
    if median_price is not None and median_price > 0:
        median_price = max(0.001, median_price * (1 + rng.gauss(0, 0.1)))

    # Build perturbed evidence
    ev = ps.package_evidence
    perturbed_ev = None
    if ev:
        perturbed_ev = PackageEvidence(
            package=ev.package,
            raw_count=ev.raw_count,
            parsed_count=ev.parsed_count,
            viable_count=viable,
            active_count=active,
            in_stock_count=in_stock,
            total_stock=max(0, ps.total_stock + rng.randint(-100, 100)) if ps.total_stock > 0 else 0,
            manufacturer_count=mfr_count,
            manufacturer_entropy=ev.manufacturer_entropy,
            median_price=median_price,
            attribute_completeness=ev.attribute_completeness,
        )

    return PackageScore(
        package=ps.package,
        viable_count=viable,
        active_count=active,
        in_stock_count=in_stock,
        total_stock=max(0, ps.total_stock + rng.randint(-100, 100)) if ps.total_stock > 0 else 0,
        manufacturer_count=mfr_count,
        median_price=median_price,
        length_mm=ps.length_mm,
        width_mm=ps.width_mm,
        area_mm2=ps.area_mm2,
        height_mm=ps.height_mm,
        score=ps.score,
        package_evidence=perturbed_ev,
    )
