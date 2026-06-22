"""Data models for the evidence-based intelligence engine (M9.4).

All models are deterministic dataclasses.  Every claim is traceable
to a ``Fact``, ``ScoreTerm``, or explicit policy.  No hallucinated
reasoning — every field has a defined source.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Schema / policy versioning
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.2.0"
SCORING_POLICY_VERSION = "probabilistic_package_utility_v1"


# ---------------------------------------------------------------------------
# Fact model
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    """A single traceable fact about a component or net.

    Every claim in the system should be backed by one or more Facts.
    """
    key: str
    value: Any
    unit: str | None = None
    source: str = ""
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fact:
        ts = d.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                ts = datetime.now(timezone.utc)
        elif not isinstance(ts, datetime):
            ts = datetime.now(timezone.utc)
        return cls(
            key=d["key"],
            value=d["value"],
            unit=d.get("unit"),
            source=d.get("source", ""),
            confidence=d.get("confidence", 1.0),
            timestamp=ts,
            evidence=d.get("evidence", []),
        )


# ---------------------------------------------------------------------------
# Net connectivity
# ---------------------------------------------------------------------------

@dataclass
class NetConnection:
    """A single pin-to-net connection for a component."""
    ref: str
    pin: str | None = None
    pin_name: str | None = None
    net: str = ""
    net_type: str | None = None  # "power", "ground", "signal", None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "pin": self.pin,
            "pin_name": self.pin_name,
            "net": self.net,
            "net_type": self.net_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NetConnection:
        return cls(
            ref=d["ref"],
            pin=d.get("pin"),
            pin_name=d.get("pin_name"),
            net=d.get("net", ""),
            net_type=d.get("net_type"),
        )


# ---------------------------------------------------------------------------
# Rail info
# ---------------------------------------------------------------------------

@dataclass
class RailInfo:
    """Inferred information about a power rail net."""
    net: str
    voltage: float | None = None
    unit: str = "V"
    confidence: float = 0.5
    source: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def is_ground(self) -> bool:
        return self.voltage == 0.0 or self.net_type == "ground"

    @property
    def net_type(self) -> str | None:
        if self.voltage is not None and self.voltage == 0.0:
            return "ground"
        if self.voltage is not None and self.voltage > 0:
            return "power"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "net": self.net,
            "voltage": self.voltage,
            "unit": self.unit,
            "confidence": self.confidence,
            "source": self.source,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RailInfo:
        return cls(
            net=d["net"],
            voltage=d.get("voltage"),
            unit=d.get("unit", "V"),
            confidence=d.get("confidence", 0.5),
            source=d.get("source", ""),
            evidence=d.get("evidence", []),
        )


# ---------------------------------------------------------------------------
# Intelligence component context (distinct from core.models.ComponentContext)
# ---------------------------------------------------------------------------

@dataclass
class IntelligenceContext:
    """Full intelligence context for a component."""
    ref: str
    category: str | None = None
    value_raw: str | None = None
    parsed_value: Any | None = None
    nets: list[NetConnection] = field(default_factory=list)
    rail_context: RailInfo | None = None
    role_probs: dict[str, float] = field(default_factory=dict)
    explicit_constraints: dict[str, Any] = field(default_factory=dict)
    inferred_constraints: dict[str, Any] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "category": self.category,
            "value_raw": self.value_raw,
            "parsed_value": self.parsed_value,
            "nets": [n.to_dict() for n in self.nets],
            "rail_context": self.rail_context.to_dict() if self.rail_context else None,
            "role_probs": self.role_probs,
            "explicit_constraints": self.explicit_constraints,
            "inferred_constraints": self.inferred_constraints,
            "facts": [f.to_dict() for f in self.facts],
        }


# ---------------------------------------------------------------------------
# Score terms
# ---------------------------------------------------------------------------

@dataclass
class ScoreTerm:
    """A single term in the MCDA/TOPSIS score decomposition."""
    name: str
    value: float
    weight: float
    contribution: float  # weight * value in the ideal/anti-ideal distance
    source_facts: list[str] = field(default_factory=list)
    missing_data_penalty: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "weight": self.weight,
            "contribution": self.contribution,
            "source_facts": self.source_facts,
            "missing_data_penalty": self.missing_data_penalty,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScoreTerm:
        return cls(
            name=d["name"],
            value=d.get("value", 0.0),
            weight=d.get("weight", 0.0),
            contribution=d.get("contribution", 0.0),
            source_facts=d.get("source_facts", []),
            missing_data_penalty=d.get("missing_data_penalty", 0.0),
        )


# ---------------------------------------------------------------------------
# Package evidence (rich per-package supplier data)
# ---------------------------------------------------------------------------

@dataclass
class PackageEvidence:
    """Detailed evidence for a package size from supplier data."""
    package: str
    raw_count: int = 0
    parsed_count: int = 0
    viable_count: int = 0
    active_count: int = 0
    in_stock_count: int = 0
    total_stock: int = 0
    manufacturer_count: int = 0
    manufacturer_entropy: float = 0.0
    median_price: float | None = None
    price_quantiles: dict[str, float] = field(default_factory=dict)
    lifecycle_distribution: dict[str, int] = field(default_factory=dict)
    attribute_completeness: float = 0.0
    reject_reasons: dict[str, int] = field(default_factory=dict)
    first_raw_mpns: list[str] = field(default_factory=list)
    query_strings: list[str] = field(default_factory=list)
    # M9.5: three-bucket viability model
    unverified_count: int = 0
    definitive_reject_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "raw_count": self.raw_count,
            "parsed_count": self.parsed_count,
            "viable_count": self.viable_count,
            "active_count": self.active_count,
            "in_stock_count": self.in_stock_count,
            "total_stock": self.total_stock,
            "manufacturer_count": self.manufacturer_count,
            "manufacturer_entropy": round(self.manufacturer_entropy, 4),
            "median_price": self.median_price,
            "price_quantiles": self.price_quantiles,
            "lifecycle_distribution": self.lifecycle_distribution,
            "attribute_completeness": round(self.attribute_completeness, 4),
            "reject_reasons": self.reject_reasons,
            "first_raw_mpns": self.first_raw_mpns,
            "query_strings": self.query_strings,
            "unverified_count": self.unverified_count,
            "definitive_reject_count": self.definitive_reject_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackageEvidence:
        return cls(
            package=d["package"],
            raw_count=d.get("raw_count", 0),
            parsed_count=d.get("parsed_count", 0),
            viable_count=d.get("viable_count", 0),
            active_count=d.get("active_count", 0),
            in_stock_count=d.get("in_stock_count", 0),
            total_stock=d.get("total_stock", 0),
            manufacturer_count=d.get("manufacturer_count", 0),
            manufacturer_entropy=d.get("manufacturer_entropy", 0.0),
            median_price=d.get("median_price"),
            price_quantiles=d.get("price_quantiles", {}),
            lifecycle_distribution=d.get("lifecycle_distribution", {}),
            attribute_completeness=d.get("attribute_completeness", 0.0),
            reject_reasons=d.get("reject_reasons", {}),
            first_raw_mpns=d.get("first_raw_mpns", []),
            query_strings=d.get("query_strings", []),
            unverified_count=d.get("unverified_count", 0),
            definitive_reject_count=d.get("definitive_reject_count", 0),
        )


# ---------------------------------------------------------------------------
# Package score
# ---------------------------------------------------------------------------

@dataclass
class PackageScore:
    """Score for a package size (e.g. 0603) based on supplier evidence."""
    package: str
    viable_count: int = 0
    active_count: int = 0
    in_stock_count: int = 0
    total_stock: int = 0
    manufacturer_count: int = 0
    median_price: float | None = None
    length_mm: float | None = None
    width_mm: float | None = None
    area_mm2: float | None = None
    height_mm: float | None = None
    score: float = 0.0
    terms: list[ScoreTerm] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    package_evidence: PackageEvidence | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "viable_count": self.viable_count,
            "active_count": self.active_count,
            "in_stock_count": self.in_stock_count,
            "total_stock": self.total_stock,
            "manufacturer_count": self.manufacturer_count,
            "median_price": self.median_price,
            "length_mm": self.length_mm,
            "width_mm": self.width_mm,
            "area_mm2": self.area_mm2,
            "height_mm": self.height_mm,
            "score": self.score,
            "terms": [t.to_dict() for t in self.terms],
            "evidence": self.evidence,
            "package_evidence": self.package_evidence.to_dict() if self.package_evidence else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackageScore:
        pe = d.get("package_evidence")
        return cls(
            package=d["package"],
            viable_count=d.get("viable_count", 0),
            active_count=d.get("active_count", 0),
            in_stock_count=d.get("in_stock_count", 0),
            total_stock=d.get("total_stock", 0),
            manufacturer_count=d.get("manufacturer_count", 0),
            median_price=d.get("median_price"),
            length_mm=d.get("length_mm"),
            width_mm=d.get("width_mm"),
            area_mm2=d.get("area_mm2"),
            height_mm=d.get("height_mm"),
            score=d.get("score", 0.0),
            terms=[ScoreTerm.from_dict(t) for t in d.get("terms", [])],
            evidence=d.get("evidence", []),
            package_evidence=PackageEvidence.from_dict(pe) if pe else None,
        )


# ---------------------------------------------------------------------------
# Candidate score (individual MPN)
# ---------------------------------------------------------------------------

@dataclass
class CandidateScore:
    """Score for a specific MPN candidate."""
    ref: str
    candidate_id: str  # supplier|manufacturer|mpn|supplier_pn
    mpn: str = ""
    manufacturer: str = ""
    package: str = ""
    component_type: str = ""
    hard_pass: bool = True
    hard_fail_reasons: list[str] = field(default_factory=list)
    terms: list[ScoreTerm] = field(default_factory=list)
    topsis_score: float = 0.0
    uncertainty_penalty: float = 0.0
    risk_penalty: float = 0.0
    final_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "candidate_id": self.candidate_id,
            "mpn": self.mpn,
            "manufacturer": self.manufacturer,
            "package": self.package,
            "component_type": self.component_type,
            "hard_pass": self.hard_pass,
            "hard_fail_reasons": self.hard_fail_reasons,
            "terms": [t.to_dict() for t in self.terms],
            "topsis_score": self.topsis_score,
            "uncertainty_penalty": self.uncertainty_penalty,
            "risk_penalty": self.risk_penalty,
            "final_score": self.final_score,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CandidateScore:
        return cls(
            ref=d["ref"],
            candidate_id=d.get("candidate_id", ""),
            mpn=d.get("mpn", ""),
            manufacturer=d.get("manufacturer", ""),
            package=d.get("package", ""),
            component_type=d.get("component_type", ""),
            hard_pass=d.get("hard_pass", True),
            hard_fail_reasons=d.get("hard_fail_reasons", []),
            terms=[ScoreTerm.from_dict(t) for t in d.get("terms", [])],
            topsis_score=d.get("topsis_score", 0.0),
            uncertainty_penalty=d.get("uncertainty_penalty", 0.0),
            risk_penalty=d.get("risk_penalty", 0.0),
            final_score=d.get("final_score", 0.0),
        )


# ---------------------------------------------------------------------------
# Rank stability
# ---------------------------------------------------------------------------

@dataclass
class PackageStats:
    """Per-package Monte Carlo statistics."""
    package: str
    mean_score: float = 0.0
    std_score: float = 0.0
    p_win: float = 0.0
    score_margin: float = 0.0  # mean(this) - mean(second)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "mean_score": round(self.mean_score, 4),
            "std_score": round(self.std_score, 4),
            "p_win": round(self.p_win, 4),
            "score_margin": round(self.score_margin, 4),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackageStats:
        return cls(
            package=d["package"],
            mean_score=d.get("mean_score", 0.0),
            std_score=d.get("std_score", 0.0),
            p_win=d.get("p_win", 0.0),
            score_margin=d.get("score_margin", 0.0),
        )


@dataclass
class RankStabilityResult:
    """Monte Carlo rank stability analysis result."""
    package_stats: dict[str, PackageStats] = field(default_factory=dict)
    decision: str = "no_recommendation"
    decision_reason: str = ""
    n_samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_stats": {k: v.to_dict() for k, v in self.package_stats.items()},
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "n_samples": self.n_samples,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RankStabilityResult:
        stats = {}
        for k, v in d.get("package_stats", {}).items():
            stats[k] = PackageStats.from_dict(v)
        return cls(
            package_stats=stats,
            decision=d.get("decision", "no_recommendation"),
            decision_reason=d.get("decision_reason", ""),
            n_samples=d.get("n_samples", 0),
        )


# ---------------------------------------------------------------------------
# Suggestion record (the persisted output)
# ---------------------------------------------------------------------------

@dataclass
class SuggestionRecord:
    """Full persisted suggestion for a component.

    Includes context hash for staleness detection, all scoring evidence,
    and the weights/policy used to generate the result.
    """
    ref: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context_hash: str = ""
    schema_version: str = SCHEMA_VERSION
    scoring_policy_version: str = SCORING_POLICY_VERSION
    source_project: str | None = None
    created_at: str = ""
    role: str | None = None
    role_confidence: float = 0.0
    role_probabilities: dict[str, float] = field(default_factory=dict)
    package_ranking: list[PackageScore] = field(default_factory=list)
    candidate_ranking: list[CandidateScore] = field(default_factory=list)
    top_candidate_mpn: str | None = None
    top_package: str | None = None
    evidence: list[Fact] = field(default_factory=list)
    weights_used: dict[str, float] = field(default_factory=dict)
    data_source: str = ""  # "cache", "live", "mock", "mixed"
    decision: str = "no_recommendation"
    decision_reason: str = ""
    rank_stability: RankStabilityResult | None = None
    pin_completeness: float = 1.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = self.timestamp.isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "timestamp": self.timestamp.isoformat(),
            "context_hash": self.context_hash,
            "schema_version": self.schema_version,
            "scoring_policy_version": self.scoring_policy_version,
            "source_project": self.source_project,
            "created_at": self.created_at,
            "role": self.role,
            "role_confidence": self.role_confidence,
            "role_probabilities": self.role_probabilities,
            "package_ranking": [p.to_dict() for p in self.package_ranking],
            "candidate_ranking": [c.to_dict() for c in self.candidate_ranking],
            "top_candidate_mpn": self.top_candidate_mpn,
            "top_package": self.top_package,
            "evidence": [f.to_dict() for f in self.evidence],
            "weights_used": self.weights_used,
            "data_source": self.data_source,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "rank_stability": self.rank_stability.to_dict() if self.rank_stability else None,
            "pin_completeness": self.pin_completeness,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SuggestionRecord:
        ts = d.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                ts = datetime.now(timezone.utc)
        elif not isinstance(ts, datetime):
            ts = datetime.now(timezone.utc)
        rs = d.get("rank_stability")
        return cls(
            ref=d["ref"],
            timestamp=ts,
            context_hash=d.get("context_hash", ""),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            scoring_policy_version=d.get("scoring_policy_version", SCORING_POLICY_VERSION),
            source_project=d.get("source_project"),
            created_at=d.get("created_at", ""),
            role=d.get("role"),
            role_confidence=d.get("role_confidence", 0.0),
            role_probabilities=d.get("role_probabilities", {}),
            package_ranking=[PackageScore.from_dict(p) for p in d.get("package_ranking", [])],
            candidate_ranking=[CandidateScore.from_dict(c) for c in d.get("candidate_ranking", [])],
            top_candidate_mpn=d.get("top_candidate_mpn"),
            top_package=d.get("top_package"),
            evidence=[Fact.from_dict(f) for f in d.get("evidence", [])],
            weights_used=d.get("weights_used", {}),
            data_source=d.get("data_source", ""),
            decision=d.get("decision", "no_recommendation"),
            decision_reason=d.get("decision_reason", ""),
            rank_stability=RankStabilityResult.from_dict(rs) if rs else None,
            pin_completeness=d.get("pin_completeness", 1.0),
        )


# ---------------------------------------------------------------------------
# Context hash helper
# ---------------------------------------------------------------------------

def compute_context_hash(
    ref: str,
    value: str | None,
    nets: list[NetConnection] | None = None,
    rails: list[RailInfo] | None = None,
) -> str:
    """Compute a deterministic hash of the component context."""
    parts = [ref, value or ""]
    if nets:
        for n in sorted(nets, key=lambda x: (x.pin or "", x.net)):
            parts.append(f"{n.pin}:{n.net}")
    if rails:
        for r in sorted(rails, key=lambda x: x.net):
            parts.append(f"{r.net}:{r.voltage}")
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
