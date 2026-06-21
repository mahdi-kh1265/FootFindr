"""Capacitor role classification — softmax model (M9.4B).

Classifies capacitor roles using a softmax log-likelihood model over
a set of hypotheses (roles).  Each role has a feature weight vector
that produces a log-likelihood from observed evidence features.

Hypothesis set:
    rail_decoupling, bulk, dc_block, rc_filter,
    timing_compensation, crystal_rf, unknown

Final confidence:
    P(h|E) = exp(L_h) / sum_k(exp(L_k))
    role_confidence = P(top) * margin * pin_completeness * parser_confidence

Key constraint:
    If pin_completeness < 1.0 → role = "unknown" or "review",
    confidence = low.  Never high-confidence on incomplete topology.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from footfindr.core.units import parse_capacitance
from footfindr.intelligence.models import Fact, NetConnection, RailInfo

logger = logging.getLogger("footfindr.intelligence.cap_classifier")


# ---------------------------------------------------------------------------
# Model version
# ---------------------------------------------------------------------------

_MODEL_VERSION = "softmax_cap_v1"


# ---------------------------------------------------------------------------
# Role hypothesis set
# ---------------------------------------------------------------------------

ROLES = [
    "rail_decoupling",
    "bulk",
    "dc_block",
    "rc_filter",
    "timing_compensation",
    "crystal_rf",
    "unknown",
]


# ---------------------------------------------------------------------------
# Feature names
# ---------------------------------------------------------------------------

FEATURE_NAMES = [
    "x_pin_complete",       # resolved_pins / expected_pins
    "x_has_ground",         # binary: cap has a ground pin
    "x_ground_confidence",  # net parser confidence for the ground connection
    "x_has_rail",           # binary: cap has a power rail pin
    "x_rail_confidence",    # rail scanner confidence
    "x_value_decoupling",   # plausibility for decoupling range (100pF-100uF)
    "x_value_bulk",         # plausibility for bulk range (>10uF)
    "x_series_signal",      # evidence for signal-to-signal (two signal nets)
    "x_rc_neighbors",       # evidence for neighboring R on same signal net
    "x_power_neighbors",    # evidence for IC power pins on same net
    "x_parser_confidence",  # net parser completeness
    "x_ambiguity",          # conflict/ambiguity score
]


# ---------------------------------------------------------------------------
# Per-role weight vectors (bias + feature weights)
# ---------------------------------------------------------------------------
# Format: {role: {"bias": b, feature_name: beta, ...}}
# Positive beta = evidence for this role. Negative = evidence against.

_ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "rail_decoupling": {
        "bias": -1.0,
        "x_pin_complete": 3.0,
        "x_has_ground": 3.0,
        "x_ground_confidence": 1.0,
        "x_has_rail": 3.0,
        "x_rail_confidence": 2.0,
        "x_value_decoupling": 2.0,
        "x_value_bulk": -0.5,
        "x_series_signal": -3.0,
        "x_rc_neighbors": -1.0,
        "x_power_neighbors": 1.0,
        "x_parser_confidence": 1.0,
        "x_ambiguity": -2.0,
    },
    "bulk": {
        "bias": -2.0,
        "x_pin_complete": 2.0,
        "x_has_ground": 2.5,
        "x_ground_confidence": 0.5,
        "x_has_rail": 2.5,
        "x_rail_confidence": 1.5,
        "x_value_decoupling": 0.5,
        "x_value_bulk": 3.0,
        "x_series_signal": -3.0,
        "x_rc_neighbors": -1.0,
        "x_power_neighbors": 1.5,
        "x_parser_confidence": 1.0,
        "x_ambiguity": -2.0,
    },
    "dc_block": {
        "bias": -2.0,
        "x_pin_complete": 2.0,
        "x_has_ground": -3.0,
        "x_ground_confidence": -1.0,
        "x_has_rail": -2.0,
        "x_rail_confidence": -1.0,
        "x_value_decoupling": 1.0,
        "x_value_bulk": -1.0,
        "x_series_signal": 3.5,
        "x_rc_neighbors": 0.0,
        "x_power_neighbors": -1.0,
        "x_parser_confidence": 1.0,
        "x_ambiguity": -1.0,
    },
    "rc_filter": {
        "bias": -3.0,
        "x_pin_complete": 2.0,
        "x_has_ground": 1.5,
        "x_ground_confidence": 0.5,
        "x_has_rail": -1.0,
        "x_rail_confidence": -0.5,
        "x_value_decoupling": 0.5,
        "x_value_bulk": -2.0,
        "x_series_signal": 1.0,
        "x_rc_neighbors": 4.0,
        "x_power_neighbors": -1.0,
        "x_parser_confidence": 1.0,
        "x_ambiguity": -1.0,
    },
    "timing_compensation": {
        "bias": -4.0,
        "x_pin_complete": 1.5,
        "x_has_ground": -1.0,
        "x_ground_confidence": 0.0,
        "x_has_rail": -1.0,
        "x_rail_confidence": 0.0,
        "x_value_decoupling": -1.0,
        "x_value_bulk": -3.0,
        "x_series_signal": 2.0,
        "x_rc_neighbors": 1.0,
        "x_power_neighbors": -1.0,
        "x_parser_confidence": 1.0,
        "x_ambiguity": -1.0,
    },
    "crystal_rf": {
        "bias": -5.0,
        "x_pin_complete": 1.0,
        "x_has_ground": 0.0,
        "x_ground_confidence": 0.0,
        "x_has_rail": -1.0,
        "x_rail_confidence": 0.0,
        "x_value_decoupling": -2.0,
        "x_value_bulk": -3.0,
        "x_series_signal": 1.0,
        "x_rc_neighbors": -1.0,
        "x_power_neighbors": -1.0,
        "x_parser_confidence": 0.5,
        "x_ambiguity": 0.0,
    },
    "unknown": {
        "bias": 0.5,
        "x_pin_complete": -2.0,
        "x_has_ground": 0.0,
        "x_ground_confidence": 0.0,
        "x_has_rail": 0.0,
        "x_rail_confidence": 0.0,
        "x_value_decoupling": 0.0,
        "x_value_bulk": 0.0,
        "x_series_signal": 0.0,
        "x_rc_neighbors": 0.0,
        "x_power_neighbors": 0.0,
        "x_parser_confidence": -1.0,
        "x_ambiguity": 2.0,
    },
}


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass
class CapClassification:
    """Result of capacitor role classification."""
    ref: str
    role: str              # top role from softmax
    confidence: float      # final calibrated confidence
    value_display: str = ""
    nets_description: str = ""
    evidence: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    role_probabilities: dict[str, float] = field(default_factory=dict)
    feature_values: dict[str, float] = field(default_factory=dict)
    model_version: str = _MODEL_VERSION
    pin_completeness: float = 0.0
    parser_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "role": self.role,
            "confidence": self.confidence,
            "value_display": self.value_display,
            "nets_description": self.nets_description,
            "evidence": self.evidence,
            "facts": [f.to_dict() for f in self.facts],
            "role_probabilities": self.role_probabilities,
            "feature_values": self.feature_values,
            "model_version": self.model_version,
            "pin_completeness": self.pin_completeness,
            "parser_confidence": self.parser_confidence,
        }


# ---------------------------------------------------------------------------
# Plausible ranges
# ---------------------------------------------------------------------------

_MIN_DECOUPLING_F = 100e-12   # 100pF
_MAX_DECOUPLING_F = 100e-6    # 100uF
_BULK_THRESHOLD_F = 10e-6     # 10uF


def _plausibility_decoupling(cap_farads: float | None) -> float:
    """How plausible is this value for decoupling? Returns 0-1."""
    if cap_farads is None:
        return 0.3  # unknown
    if _MIN_DECOUPLING_F <= cap_farads <= _MAX_DECOUPLING_F:
        return 1.0
    return 0.0


def _plausibility_bulk(cap_farads: float | None) -> float:
    """How plausible is this value for bulk capacitance? Returns 0-1."""
    if cap_farads is None:
        return 0.2
    if cap_farads >= _BULK_THRESHOLD_F:
        return 1.0
    if cap_farads >= 1e-6:
        return 0.3
    return 0.0


# ---------------------------------------------------------------------------
# Net type helpers
# ---------------------------------------------------------------------------

def _is_ground(conn: NetConnection) -> bool:
    return conn.net_type == "ground"


def _is_power(conn: NetConnection) -> bool:
    return conn.net_type == "power"


def _is_power_or_suspected(conn: NetConnection, rails: list[RailInfo]) -> bool:
    if conn.net_type == "power":
        return True
    for rail in rails:
        if rail.net == conn.net and rail.voltage is not None and rail.voltage > 0:
            return True
        if rail.net == conn.net and rail.voltage is None:
            return True
    return False


def _find_rail(net_name: str, rails: list[RailInfo]) -> RailInfo | None:
    for rail in rails:
        if rail.net == net_name:
            return rail
    return None


# ---------------------------------------------------------------------------
# Softmax computation
# ---------------------------------------------------------------------------

def _softmax(log_likelihoods: dict[str, float]) -> dict[str, float]:
    """Compute softmax probabilities from log-likelihoods.

    Numerically stable: subtract max before exp.
    """
    if not log_likelihoods:
        return {}
    max_ll = max(log_likelihoods.values())
    exps = {k: math.exp(v - max_ll) for k, v in log_likelihoods.items()}
    total = sum(exps.values())
    if total == 0:
        n = len(exps)
        return {k: 1.0 / n for k in exps}
    return {k: v / total for k, v in exps.items()}


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _extract_features(
    ref: str,
    cap_farads: float | None,
    connections: list[NetConnection],
    rails: list[RailInfo],
    pin_completeness: float,
) -> dict[str, float]:
    """Extract feature vector from schematic evidence."""
    features: dict[str, float] = {}

    # Pin completeness
    features["x_pin_complete"] = pin_completeness

    # Ground / rail pins
    gnd_pins = [c for c in connections if _is_ground(c)]
    rail_pins = [c for c in connections if _is_power_or_suspected(c, rails)]
    signal_pins = [c for c in connections if c.net_type == "signal" or c.net_type is None]

    features["x_has_ground"] = 1.0 if gnd_pins else 0.0
    features["x_has_rail"] = 1.0 if rail_pins else 0.0

    # Ground confidence (best)
    gnd_conf = 0.0
    for c in gnd_pins:
        if c.net_type == "ground":
            gnd_conf = max(gnd_conf, 0.95)
    features["x_ground_confidence"] = gnd_conf

    # Rail confidence (best rail)
    rail_conf = 0.0
    for c in rail_pins:
        ri = _find_rail(c.net, rails) if c.net else None
        if ri:
            rail_conf = max(rail_conf, ri.confidence)
        elif c.net_type == "power":
            rail_conf = max(rail_conf, 0.7)
    features["x_rail_confidence"] = rail_conf

    # Value features
    features["x_value_decoupling"] = _plausibility_decoupling(cap_farads)
    features["x_value_bulk"] = _plausibility_bulk(cap_farads)

    # Signal topology
    features["x_series_signal"] = 1.0 if (len(signal_pins) >= 2 and len(gnd_pins) == 0) else 0.0

    # RC neighbor detection (stub — would need full graph neighbor analysis)
    features["x_rc_neighbors"] = 0.0

    # Power neighbors (stub — would need IC pin analysis)
    features["x_power_neighbors"] = 0.0

    # Parser confidence (based on pin completeness and connection count)
    if connections:
        features["x_parser_confidence"] = min(1.0, pin_completeness * 0.8 + 0.2)
    else:
        features["x_parser_confidence"] = 0.1

    # Ambiguity
    ambiguity = 0.0
    if pin_completeness < 1.0:
        ambiguity += 0.5
    if not connections:
        ambiguity += 0.3
    if len(gnd_pins) == 0 and len(rail_pins) == 0 and len(signal_pins) == 0:
        ambiguity += 0.2
    features["x_ambiguity"] = min(1.0, ambiguity)

    return features


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def classify_capacitor(
    ref: str,
    value_raw: str | None,
    connections: list[NetConnection],
    rails: list[RailInfo],
    *,
    pin_completeness: float = 1.0,
) -> CapClassification:
    """Classify a capacitor's role using the softmax model.

    Args:
        ref: Reference designator (e.g. "C1")
        value_raw: Raw value string from schematic (e.g. "4.7uF")
        connections: Net connections for this cap (from NetGraph)
        rails: Inferred rail information
        pin_completeness: Pin resolution completeness (0-1)

    Returns:
        CapClassification with role, probabilities, features, and evidence.
    """
    from footfindr.core.units import normalize_capacitance_display

    evidence: list[str] = []
    facts: list[Fact] = []

    # Parse capacitance
    cap_farads = parse_capacitance(value_raw or "") if value_raw else None
    cap_display = ""
    if cap_farads is not None:
        cap_display = normalize_capacitance_display(cap_farads)
        facts.append(Fact(
            key="capacitance", value=cap_farads,
            unit="F", source="schematic Value field",
            evidence=[f"Parsed '{value_raw}' as {cap_display}"],
        ))

    # Build nets description
    net_parts = []
    for conn in connections:
        pin_str = f"pin {conn.pin}" if conn.pin else "pin ?"
        net_parts.append(f"{pin_str} -> {conn.net}")
    nets_description = ", ".join(net_parts)

    # Record net facts
    for conn in connections:
        evidence.append(f"{ref} {conn.pin or '?'} -> {conn.net}")
        facts.append(Fact(
            key=f"pin_{conn.pin}_net", value=conn.net,
            source="net graph", evidence=[f"Pin {conn.pin} connected to {conn.net}"],
        ))

    # Extract features
    features = _extract_features(ref, cap_farads, connections, rails, pin_completeness)
    evidence.append(f"Pin completeness: {pin_completeness:.2f}")
    evidence.append(f"Parser confidence: {features['x_parser_confidence']:.2f}")

    # Compute log-likelihoods
    log_likelihoods: dict[str, float] = {}
    for role in ROLES:
        weights = _ROLE_WEIGHTS[role]
        L = weights.get("bias", 0.0)
        for feat_name, feat_val in features.items():
            beta = weights.get(feat_name, 0.0)
            L += beta * feat_val
        log_likelihoods[role] = L

    # Softmax
    probs = _softmax(log_likelihoods)

    # Sort by probability descending
    sorted_roles = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    top_role = sorted_roles[0][0]
    top_prob = sorted_roles[0][1]
    second_prob = sorted_roles[1][1] if len(sorted_roles) > 1 else 0.0

    # Margin factor
    margin = top_prob - second_prob

    # Evidence completeness
    evidence_completeness = 1.0
    if not connections:
        evidence_completeness *= 0.3
    if cap_farads is None:
        evidence_completeness *= 0.7

    # Final calibrated confidence
    final_confidence = (
        top_prob
        * (0.5 + 0.5 * margin)  # scale margin to [0.5, 1.0]
        * pin_completeness
        * features["x_parser_confidence"]
        * evidence_completeness
    )
    final_confidence = round(min(0.99, max(0.01, final_confidence)), 4)

    # Override: incomplete topology → force low confidence
    if pin_completeness < 1.0:
        if final_confidence > 0.30:
            final_confidence = 0.30
        evidence.append(
            f"Confidence capped at {final_confidence:.2f} due to "
            f"incomplete pin topology ({pin_completeness:.2f})"
        )

    # Role evidence
    for role_name, prob in sorted_roles[:3]:
        evidence.append(f"P({role_name}) = {prob:.4f}")

    evidence.append(f"Margin (top - 2nd) = {margin:.4f}")
    evidence.append(f"Final confidence = {final_confidence:.4f}")
    evidence.append(f"Model: {_MODEL_VERSION}")

    # Rail evidence
    for conn in connections:
        if _is_power_or_suspected(conn, rails):
            rail = _find_rail(conn.net, rails)
            if rail and rail.voltage is not None:
                evidence.append(f"{conn.net} inferred as {rail.voltage}V rail from {rail.source}")
            elif rail:
                evidence.append(f"{conn.net} is a power-like net (voltage unknown)")

    return CapClassification(
        ref=ref,
        role=top_role,
        confidence=final_confidence,
        value_display=cap_display,
        nets_description=nets_description,
        evidence=evidence,
        facts=facts,
        role_probabilities={k: round(v, 4) for k, v in probs.items()},
        feature_values={k: round(v, 4) for k, v in features.items()},
        model_version=_MODEL_VERSION,
        pin_completeness=pin_completeness,
        parser_confidence=features["x_parser_confidence"],
    )
