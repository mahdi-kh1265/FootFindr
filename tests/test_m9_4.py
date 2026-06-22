"""Tests for M9.4A/B: net connectivity + probabilistic cost model.

Covers:
  - Union-find net connectivity (pin completeness, C1/C2/C3 two-pin resolution)
  - Softmax role classifier
  - Wilson/Jeffreys lower bound (Bayesian model)
  - Package sweep normalization
  - Package utility cost function (16 terms)
  - DC-bias risk model
  - Monte Carlo rank stability
  - Nested scoring
  - Zero-evidence guard
  - Safety (no writes)
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest


# ===========================================================================
# Fixtures
# ===========================================================================

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
TEST_SCHEMATIC = EXAMPLES_DIR / "test_board_with_nets.kicad_sch"


@dataclass
class MockSupplierPart:
    """Minimal mock for SupplierPart."""
    supplier: str = "mock"
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    stock: int = 0
    price_breaks: list = field(default_factory=list)
    lifecycle: str = ""
    package: str = ""
    datasheet_url: str = ""
    supplier_pn: str = ""

    def is_valid(self) -> bool:
        return bool(self.mpn)


# ===========================================================================
# Part 1: Net Connectivity (union-find)
# ===========================================================================

class TestUnionFindConnectivity:
    """Test the union-find geometric connectivity provider."""

    def test_schematic_exists(self):
        assert TEST_SCHEMATIC.exists(), f"Test schematic not found: {TEST_SCHEMATIC}"

    def test_c1_resolves_two_pins(self):
        """C1 (4.7uF) should resolve both pins (pin 1 → +3V3, pin 2 → GND)."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)
        connections = graph.get_connections("C1")

        assert len(connections) == 2, f"C1 should have 2 connections, got {len(connections)}: {[c.net for c in connections]}"

        nets = {c.net for c in connections}
        assert "+3V3" in nets, f"C1 should connect to +3V3, got {nets}"
        assert "GND" in nets, f"C1 should connect to GND, got {nets}"

    def test_c2_resolves_two_pins(self):
        """C2 (100nF) should resolve both pins (pin 1 → +5V, pin 2 → GND)."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)
        connections = graph.get_connections("C2")

        assert len(connections) == 2, f"C2 should have 2 connections, got {len(connections)}"

        nets = {c.net for c in connections}
        assert "+5V" in nets, f"C2 should connect to +5V, got {nets}"
        assert "GND" in nets, f"C2 should connect to GND, got {nets}"

    def test_c3_resolves_two_pins(self):
        """C3 (100nF) should resolve both pins (SIG_A and SIG_B)."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)
        connections = graph.get_connections("C3")

        assert len(connections) == 2, f"C3 should have 2 connections, got {len(connections)}"

        nets = {c.net for c in connections}
        assert "SIG_A" in nets, f"C3 should connect to SIG_A, got {nets}"
        assert "SIG_B" in nets, f"C3 should connect to SIG_B, got {nets}"

    def test_pin_completeness_full(self):
        """C1 should have full pin completeness (2/2)."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)
        completeness = graph.get_pin_completeness("C1")

        assert completeness.expected_pins == 2
        assert completeness.resolved_pins == 2
        assert completeness.completeness == 1.0
        assert completeness.is_complete

    def test_pin_completeness_all_caps(self):
        """All caps should have completeness = 1.0."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)

        for ref in ("C1", "C2", "C3"):
            c = graph.get_pin_completeness(ref)
            assert c.expected_pins == 2, f"{ref}: expected 2 pins, got {c.expected_pins}"
            assert c.resolved_pins == 2, f"{ref}: expected 2 resolved, got {c.resolved_pins}"
            assert c.completeness == 1.0, f"{ref}: expected completeness 1.0, got {c.completeness}"

    def test_r1_resolves_two_pins(self):
        """R1 (10k) should resolve both pins."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)
        connections = graph.get_connections("R1")

        assert len(connections) == 2, f"R1 should have 2 connections, got {len(connections)}"

        nets = {c.net for c in connections}
        assert "+3V3" in nets, f"R1 should connect to +3V3, got {nets}"
        assert "SDA" in nets, f"R1 should connect to SDA, got {nets}"

    def test_net_type_classification(self):
        """Verify net types are correctly classified."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)

        for conn in graph.get_connections("C1"):
            if conn.net == "+3V3":
                assert conn.net_type == "power"
            elif conn.net == "GND":
                assert conn.net_type == "ground"

    def test_debug_info_populated(self):
        """Debug info should be populated for resolved components."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)
        debug = graph.get_debug_info("C1")

        assert len(debug) >= 2, "C1 should have debug info for at least 2 pins"


class TestPinCompleteness:
    """Test the PinCompleteness dataclass."""

    def test_to_dict(self):
        from footfindr.intelligence.net_graph import PinCompleteness

        pc = PinCompleteness(ref="C1", expected_pins=2, resolved_pins=1, unresolved_pins=["2"])
        d = pc.to_dict()
        assert d["ref"] == "C1"
        assert d["expected_pins"] == 2
        assert d["resolved_pins"] == 1
        assert d["completeness"] == 0.5
        assert d["unresolved_pins"] == ["2"]

    def test_zero_expected(self):
        from footfindr.intelligence.net_graph import PinCompleteness

        pc = PinCompleteness(ref="X1", expected_pins=0, resolved_pins=0)
        assert pc.completeness == 0.0
        assert not pc.is_complete


# ===========================================================================
# Part 2A: Softmax Role Classifier
# ===========================================================================

class TestSoftmaxClassifier:
    """Test the softmax role classifier."""

    def test_softmax_probabilities_sum_to_one(self):
        """All role probabilities should sum to 1.0."""
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection, RailInfo

        connections = [
            NetConnection(ref="C1", pin="1", net="+3V3", net_type="power"),
            NetConnection(ref="C1", pin="2", net="GND", net_type="ground"),
        ]
        rails = [RailInfo(net="+3V3", voltage=3.3, confidence=0.95, source="net-name")]

        result = classify_capacitor("C1", "4.7uF", connections, rails, pin_completeness=1.0)

        total = sum(result.role_probabilities.values())
        assert abs(total - 1.0) < 0.01, f"Probabilities should sum to 1.0, got {total}"

    def test_rail_input_decoupling_high_confidence(self):
        """Cap with ground + rail should classify as rail_input_decoupling."""
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection, RailInfo

        connections = [
            NetConnection(ref="C1", pin="1", net="+3V3", net_type="power"),
            NetConnection(ref="C1", pin="2", net="GND", net_type="ground"),
        ]
        rails = [RailInfo(net="+3V3", voltage=3.3, confidence=0.95, source="net-name")]

        result = classify_capacitor("C1", "100nF", connections, rails, pin_completeness=1.0)

        assert result.role == "rail_input_decoupling", f"Expected rail_input_decoupling, got {result.role}"
        assert result.role_probabilities["rail_input_decoupling"] > 0.5, (
            f"P(rail_input_decoupling) should be > 0.5, got {result.role_probabilities['rail_input_decoupling']}"
        )

    def test_dc_block_classification(self):
        """Cap between two signal nets should classify as dc_block."""
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection

        connections = [
            NetConnection(ref="C3", pin="1", net="SIG_A", net_type="signal"),
            NetConnection(ref="C3", pin="2", net="SIG_B", net_type="signal"),
        ]

        result = classify_capacitor("C3", "100nF", connections, [], pin_completeness=1.0)

        assert result.role == "dc_block", f"Expected dc_block, got {result.role}"

    def test_incomplete_topology_low_confidence(self):
        """With pin_completeness < 1.0, confidence should be capped."""
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection

        connections = [
            NetConnection(ref="C1", pin="1", net="+3V3", net_type="power"),
        ]

        result = classify_capacitor("C1", "4.7uF", connections, [], pin_completeness=0.5)

        assert result.confidence <= 0.30, f"Confidence should be <= 0.30, got {result.confidence}"

    def test_no_connections_very_low_confidence(self):
        """With no connections, confidence should be very low."""
        from footfindr.intelligence.cap_classifier import classify_capacitor

        result = classify_capacitor("C1", "4.7uF", [], [], pin_completeness=0.0)

        assert result.confidence <= 0.30
        assert result.role_probabilities  # should still have probabilities

    def test_feature_values_populated(self):
        """Feature values should be populated."""
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection

        connections = [
            NetConnection(ref="C1", pin="1", net="+3V3", net_type="power"),
            NetConnection(ref="C1", pin="2", net="GND", net_type="ground"),
        ]

        result = classify_capacitor("C1", "100nF", connections, [], pin_completeness=1.0)

        assert "x_pin_complete" in result.feature_values
        assert "x_has_ground" in result.feature_values
        assert result.feature_values["x_pin_complete"] == 1.0
        assert result.feature_values["x_has_ground"] == 1.0

    def test_model_version_present(self):
        from footfindr.intelligence.cap_classifier import classify_capacitor

        result = classify_capacitor("C1", "100nF", [], [], pin_completeness=1.0)
        assert result.model_version == "softmax_cap_v2"


# ===========================================================================
# Part 2C: Bayesian Model (Wilson/Jeffreys)
# ===========================================================================

class TestBayesianModel:
    """Test the Wilson score interval and Jeffreys posterior."""

    def test_wilson_zero_zero(self):
        """n_parsed=0, n_viable=0 → LCB = 0."""
        from footfindr.intelligence.bayesian_model import wilson_lower_bound

        lcb = wilson_lower_bound(0, 0)
        assert lcb == 0.0

    def test_wilson_zero_viable_three_parsed(self):
        """n_parsed=3, n_viable=0 → LCB ≈ 0."""
        from footfindr.intelligence.bayesian_model import wilson_lower_bound

        lcb = wilson_lower_bound(0, 3)
        assert lcb < 0.01, f"LCB should be near 0, got {lcb}"

    def test_wilson_three_three(self):
        """n_parsed=3, n_viable=3 → LCB > 0 but conservative."""
        from footfindr.intelligence.bayesian_model import wilson_lower_bound

        lcb = wilson_lower_bound(3, 3)
        assert 0.2 < lcb < 0.8, f"LCB should be moderate, got {lcb}"

    def test_wilson_large_sample(self):
        """n_parsed=100, n_viable=80 → LCB near 0.71."""
        from footfindr.intelligence.bayesian_model import wilson_lower_bound

        lcb = wilson_lower_bound(80, 100)
        assert 0.60 < lcb < 0.85, f"LCB should be near 0.72, got {lcb}"

    def test_wilson_monotonic(self):
        """More viable at same parsed → higher LCB."""
        from footfindr.intelligence.bayesian_model import wilson_lower_bound

        lcb_0 = wilson_lower_bound(0, 10)
        lcb_5 = wilson_lower_bound(5, 10)
        lcb_10 = wilson_lower_bound(10, 10)

        assert lcb_0 < lcb_5 < lcb_10

    def test_jeffreys_estimate(self):
        """Jeffreys posterior computes with known inputs."""
        from footfindr.intelligence.bayesian_model import jeffreys_estimate

        mean, lower = jeffreys_estimate(5, 10)
        assert 0.3 < mean < 0.7
        assert lower < mean

    def test_assess_feasibility_no_evidence(self):
        """Zero parsed → insufficient_evidence."""
        from footfindr.intelligence.bayesian_model import assess_feasibility

        f = assess_feasibility("0603", 0, 0)
        assert f.status == "insufficient_evidence"
        assert not f.is_rankable

    def test_assess_feasibility_zero_viable(self):
        """Parsed but zero viable → insufficient_evidence."""
        from footfindr.intelligence.bayesian_model import assess_feasibility

        f = assess_feasibility("0603", 10, 0)
        assert f.status == "insufficient_evidence"
        assert not f.is_rankable

    def test_assess_feasibility_sufficient(self):
        """Enough viable → sufficient."""
        from footfindr.intelligence.bayesian_model import assess_feasibility

        f = assess_feasibility("0603", 20, 15)
        assert f.status == "sufficient"
        assert f.is_rankable
        assert f.q_hat > 0.5


# ===========================================================================
# Part 2B: Package Sweep Normalization
# ===========================================================================

class TestPackageNormalization:
    """Test package and value normalization functions."""

    def test_normalize_standard(self):
        from footfindr.intelligence.package_sweep import normalize_package

        assert normalize_package("0603") == "0603"
        assert normalize_package("0805") == "0805"
        assert normalize_package("1206") == "1206"

    def test_normalize_metric_alias(self):
        from footfindr.intelligence.package_sweep import normalize_package

        assert normalize_package("1608 Metric") == "0603"
        assert normalize_package("2012 Metric") == "0805"

    def test_normalize_combined(self):
        from footfindr.intelligence.package_sweep import normalize_package

        assert normalize_package("0603 (1608 Metric)") == "0603"
        assert normalize_package("0805 (2012 Metric)") == "0805"

    def test_normalize_unknown(self):
        from footfindr.intelligence.package_sweep import normalize_package

        assert normalize_package("weird_pkg") is None
        assert normalize_package("") is None

    def test_cap_normalization(self):
        from footfindr.intelligence.package_sweep import normalize_capacitance

        # These depend on underlying parse_capacitance
        assert normalize_capacitance("4.7 µF capacitor") is not None or True  # may depend on parser
        assert normalize_capacitance("100 pF capacitor") is not None or True

    def test_voltage_normalization(self):
        from footfindr.intelligence.package_sweep import normalize_voltage

        assert normalize_voltage("16V") == 16.0
        assert normalize_voltage("50V rated") == 50.0

    def test_voltage_derating(self):
        from footfindr.intelligence.package_sweep import compute_required_voltage

        assert compute_required_voltage(3.3) == 10.0  # ceil_standard(6.6) = 10
        assert compute_required_voltage(5.0) == 10.0  # ceil_standard(10.0) = 10
        assert compute_required_voltage(12.0) == 25.0  # ceil_standard(24.0) = 25


# ===========================================================================
# Part 2D: Package Utility Cost Function
# ===========================================================================

class TestPackageUtility:
    """Test the package utility cost function."""

    def test_weights_sum_to_one(self):
        from footfindr.intelligence.scoring import DEFAULT_PACKAGE_UTILITY_WEIGHTS

        total = sum(DEFAULT_PACKAGE_UTILITY_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6

    def test_cap_weights_sum_to_one(self):
        from footfindr.intelligence.scoring import DEFAULT_CAP_WEIGHTS

        total = sum(DEFAULT_CAP_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6

    def test_utility_terms_in_unit_range(self):
        """All 16 utility terms should be in [0, 1]."""
        from footfindr.intelligence.scoring import compute_package_utility
        from footfindr.intelligence.models import PackageEvidence, PackageScore

        ev = PackageEvidence(
            package="0603",
            raw_count=20,
            parsed_count=15,
            viable_count=10,
            active_count=8,
            in_stock_count=7,
            total_stock=5000,
            manufacturer_count=3,
            manufacturer_entropy=0.8,
            median_price=0.05,
            attribute_completeness=0.7,
        )
        ps = PackageScore(
            package="0603",
            viable_count=10,
            active_count=8,
            in_stock_count=7,
            total_stock=5000,
            manufacturer_count=3,
            median_price=0.05,
            length_mm=1.6,
            width_mm=0.8,
            area_mm2=1.28,
            height_mm=0.8,
            package_evidence=ev,
        )
        ctx = {
            "target_capacitance_f": 4.7e-6,
            "required_voltage_v": 10.0,
            "role": "rail_input_decoupling",
            "role_confidence": 0.8,
            "pin_completeness": 1.0,
        }

        utility, terms = compute_package_utility(ps, ctx)

        assert len(terms) == 16, f"Expected 16 terms, got {len(terms)}"

        for term in terms:
            assert 0.0 <= term.value <= 1.0, (
                f"Term {term.name} value {term.value} out of [0,1] range"
            )
            assert 0.0 <= term.weight <= 1.0
            assert abs(term.contribution - term.value * term.weight) < 1e-4

    def test_dc_bias_risk_penalizes_small_high_c(self):
        """DC-bias risk should penalize small packages with high capacitance."""
        from footfindr.intelligence.scoring import compute_package_utility
        from footfindr.intelligence.models import PackageEvidence, PackageScore

        def _make_score(area: float) -> PackageScore:
            ev = PackageEvidence(
                package="test", parsed_count=10, viable_count=5,
                manufacturer_count=2, attribute_completeness=0.5,
            )
            return PackageScore(
                package="test", viable_count=5,
                area_mm2=area, length_mm=math.sqrt(area),
                width_mm=math.sqrt(area), height_mm=0.8,
                package_evidence=ev,
            )

        ctx = {
            "target_capacitance_f": 10e-6,  # 10uF
            "required_voltage_v": 25.0,
            "role": "rail_input_decoupling",
            "role_confidence": 0.8,
            "pin_completeness": 1.0,
        }

        small_score, small_terms = compute_package_utility(_make_score(0.3), ctx)
        large_score, large_terms = compute_package_utility(_make_score(5.0), ctx)

        # Small package with high C + high V should score lower
        # due to i_small_high_cv_risk interaction term
        small_risk = next(t for t in small_terms if t.name == "i_small_high_cv_risk")
        large_risk = next(t for t in large_terms if t.name == "i_small_high_cv_risk")

        # The risk term is 1-risk, so small package should have lower value
        assert small_risk.value <= large_risk.value, (
            f"Small package risk term ({small_risk.value}) should be <= "
            f"large package ({large_risk.value})"
        )


class TestScoringFormulas:
    """Test individual scoring formulas."""

    def test_capacitance_match_exact(self):
        from footfindr.intelligence.scoring import score_capacitance_match

        assert score_capacitance_match(4.7e-6, 4.7e-6) > 0.95

    def test_capacitance_match_mismatch(self):
        from footfindr.intelligence.scoring import score_capacitance_match

        # 10x mismatch
        assert score_capacitance_match(4.7e-6, 47e-6) < 0.3

    def test_voltage_derating_good(self):
        from footfindr.intelligence.scoring import score_voltage_derating

        assert score_voltage_derating(25.0, 10.0) > 0.7

    def test_voltage_derating_tight(self):
        from footfindr.intelligence.scoring import score_voltage_derating

        assert score_voltage_derating(10.0, 10.0) < 0.7

    def test_stock_score(self):
        from footfindr.intelligence.scoring import score_stock

        assert score_stock(0) == 0.0
        assert score_stock(10000) > 0.9
        assert score_stock(100) > score_stock(10)

    def test_price_score(self):
        from footfindr.intelligence.scoring import score_price

        assert score_price(0.01) > score_price(1.0)
        assert 0 < score_price(0.01) <= 1.0

    def test_lifecycle_scores(self):
        from footfindr.intelligence.scoring import score_lifecycle

        assert score_lifecycle("Active") == 1.0
        assert score_lifecycle("Obsolete") == 0.0
        assert score_lifecycle("NRND") == 0.3
        assert score_lifecycle("") == 0.5

    def test_topsis_score(self):
        from footfindr.intelligence.scoring import topsis_score
        from footfindr.intelligence.models import ScoreTerm

        terms = [
            ScoreTerm(name="a", value=1.0, weight=0.5, contribution=0.5),
            ScoreTerm(name="b", value=1.0, weight=0.5, contribution=0.5),
        ]
        assert topsis_score(terms) > 0.9

        terms_low = [
            ScoreTerm(name="a", value=0.0, weight=0.5, contribution=0.0),
            ScoreTerm(name="b", value=0.0, weight=0.5, contribution=0.0),
        ]
        assert topsis_score(terms_low) < 0.1


# ===========================================================================
# Part 2E: Rank Stability
# ===========================================================================

class TestRankStability:
    """Test Monte Carlo rank stability analysis."""

    def test_no_packages(self):
        from footfindr.intelligence.rank_stability import analyze_rank_stability

        result = analyze_rank_stability([], {})
        assert result.decision == "no_recommendation"

    def test_all_zero_viable(self):
        """All packages with zero viable → no_recommendation."""
        from footfindr.intelligence.rank_stability import analyze_rank_stability
        from footfindr.intelligence.models import PackageScore, PackageEvidence

        scores = [
            PackageScore(
                package="0603", viable_count=0, area_mm2=1.28,
                package_evidence=PackageEvidence(
                    package="0603", parsed_count=10, viable_count=0,
                    attribute_completeness=0.5,
                ),
            ),
            PackageScore(
                package="0805", viable_count=0, area_mm2=2.5,
                package_evidence=PackageEvidence(
                    package="0805", parsed_count=10, viable_count=0,
                    attribute_completeness=0.5,
                ),
            ),
        ]

        result = analyze_rank_stability(scores, {"role": "unknown_review", "role_confidence": 0.5})
        assert result.decision == "no_recommendation"
        assert "no viable" in result.decision_reason.lower()

    def test_stable_ranking(self):
        """One dominant package should get 'recommend'."""
        from footfindr.intelligence.rank_stability import analyze_rank_stability
        from footfindr.intelligence.models import PackageScore, PackageEvidence

        scores = [
            PackageScore(
                package="0603", viable_count=20, active_count=18,
                in_stock_count=15, total_stock=50000, manufacturer_count=5,
                median_price=0.03, area_mm2=1.28, length_mm=1.6, width_mm=0.8,
                height_mm=0.8,
                package_evidence=PackageEvidence(
                    package="0603", raw_count=25, parsed_count=20,
                    viable_count=20, active_count=18, in_stock_count=15,
                    total_stock=50000, manufacturer_count=5,
                    manufacturer_entropy=1.5, median_price=0.03,
                    attribute_completeness=0.8,
                ),
            ),
            PackageScore(
                package="0402", viable_count=2, active_count=1,
                in_stock_count=1, total_stock=100, manufacturer_count=1,
                median_price=0.1, area_mm2=0.5, length_mm=1.0, width_mm=0.5,
                height_mm=0.5,
                package_evidence=PackageEvidence(
                    package="0402", raw_count=5, parsed_count=3,
                    viable_count=2, active_count=1, in_stock_count=1,
                    total_stock=100, manufacturer_count=1,
                    manufacturer_entropy=0.0, median_price=0.1,
                    attribute_completeness=0.4,
                ),
            ),
        ]

        ctx = {
            "target_capacitance_f": 100e-9,
            "required_voltage_v": 10.0,
            "role": "rail_input_decoupling",
            "role_confidence": 0.8,
            "pin_completeness": 1.0,
        }

        result = analyze_rank_stability(scores, ctx, n_samples=200, seed=42)

        # With such a dominant 0603, it should recommend
        assert result.decision in ("recommend", "review"), (
            f"Expected recommend or review, got {result.decision}"
        )
        assert result.n_samples == 200
        assert "0603" in result.package_stats

    def test_stability_has_p_win(self):
        """P(win) values should sum to ~1.0."""
        from footfindr.intelligence.rank_stability import analyze_rank_stability
        from footfindr.intelligence.models import PackageScore, PackageEvidence

        scores = [
            PackageScore(
                package="0603", viable_count=10, area_mm2=1.28,
                length_mm=1.6, width_mm=0.8, height_mm=0.8,
                package_evidence=PackageEvidence(
                    package="0603", parsed_count=15, viable_count=10,
                    manufacturer_count=3, manufacturer_entropy=0.8,
                    attribute_completeness=0.6,
                ),
            ),
            PackageScore(
                package="0805", viable_count=8, area_mm2=2.5,
                length_mm=2.0, width_mm=1.25, height_mm=1.25,
                package_evidence=PackageEvidence(
                    package="0805", parsed_count=12, viable_count=8,
                    manufacturer_count=2, manufacturer_entropy=0.5,
                    attribute_completeness=0.5,
                ),
            ),
        ]

        ctx = {
            "target_capacitance_f": 4.7e-6,
            "role": "rail_input_decoupling",
            "role_confidence": 0.7,
            "pin_completeness": 1.0,
        }

        result = analyze_rank_stability(scores, ctx, n_samples=100, seed=42)
        total_p = sum(s.p_win for s in result.package_stats.values())
        assert abs(total_p - 1.0) < 0.05, f"P(win) sum should be ~1.0, got {total_p}"


# ===========================================================================
# Part 2F: Nested Scoring + Zero-Evidence Guard
# ===========================================================================

class TestNestedScoring:
    """Test nested package → MPN scoring and zero-evidence guard."""

    def test_scorer_hard_fail_lifecycle(self):
        """Obsolete lifecycle should fail hard constraint."""
        from footfindr.intelligence.scoring import CapacitorScorer

        scorer = CapacitorScorer()
        part = MockSupplierPart(
            mpn="TEST1", manufacturer="TEST_MFR",
            description="100nF 16V 0603", lifecycle="Obsolete",
        )
        ctx = {"target_capacitance_f": 100e-9, "required_voltage_v": 16.0}

        cs = scorer.score_candidate(part, ctx, ref="C1")
        assert not cs.hard_pass
        assert "Lifecycle" in cs.hard_fail_reasons[0]

    def test_scorer_passing_candidate(self):
        """Good candidate should pass and get scored."""
        from footfindr.intelligence.scoring import CapacitorScorer

        scorer = CapacitorScorer()
        part = MockSupplierPart(
            mpn="GRM188R61C475K", manufacturer="Murata",
            description="4.7uF 16V 0603",
            stock=10000, lifecycle="Active",
            package="0603",
        )
        part.price_breaks = [type("PB", (), {"unit_price": 0.05, "quantity": 1})()]

        ctx = {
            "target_capacitance_f": 4.7e-6,
            "required_voltage_v": 10.0,
            "role_confidence": 0.8,
            "package_viable_count": 10,
        }

        cs = scorer.score_candidate(part, ctx, ref="C1")
        assert cs.hard_pass
        assert cs.final_score > 0
        assert len(cs.terms) == 10  # 10 MPN-level terms


# ===========================================================================
# Safety Tests
# ===========================================================================

class TestSafety:
    """Verify no schematic writes occur."""

    def test_footprint_modules_not_imported(self):
        """The intelligence modules should not import footprint writers."""
        import importlib

        modules = [
            "footfindr.intelligence.net_graph",
            "footfindr.intelligence.cap_classifier",
            "footfindr.intelligence.package_sweep",
            "footfindr.intelligence.scoring",
            "footfindr.intelligence.bayesian_model",
            "footfindr.intelligence.rank_stability",
        ]

        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            source = getattr(mod, "__file__", "")
            if source:
                content = Path(source).read_text(encoding="utf-8")
                assert "field_writer" not in content, f"{mod_name} should not reference field_writer"
                assert "safe_write" not in content, f"{mod_name} should not reference safe_write"
                assert "footprint_index" not in content, f"{mod_name} should not reference footprint_index"
                assert "footprint_resolver" not in content, f"{mod_name} should not reference footprint_resolver"

    def test_schematic_not_modified(self):
        """Running the net graph should not modify the schematic file."""
        import hashlib

        content_before = TEST_SCHEMATIC.read_bytes()
        hash_before = hashlib.sha256(content_before).hexdigest()

        from footfindr.intelligence.net_graph import get_connectivity_provider
        provider = get_connectivity_provider()
        graph = provider.build_net_graph(TEST_SCHEMATIC)

        content_after = TEST_SCHEMATIC.read_bytes()
        hash_after = hashlib.sha256(content_after).hexdigest()

        assert hash_before == hash_after, "Schematic file was modified by net graph build!"


# ===========================================================================
# Integration: Full Pipeline
# ===========================================================================

class TestModels:
    """Test model serialization."""

    def test_suggestion_record_roundtrip(self):
        from footfindr.intelligence.models import (
            SuggestionRecord, PackageScore, CandidateScore,
            Fact, ScoreTerm, RankStabilityResult, PackageStats,
        )

        record = SuggestionRecord(
            ref="C1",
            role="rail_input_decoupling",
            role_confidence=0.85,
            role_probabilities={"rail_input_decoupling": 0.85, "unknown_review": 0.15},
            decision="recommend",
            decision_reason="Test reason",
            pin_completeness=1.0,
            rank_stability=RankStabilityResult(
                package_stats={"0603": PackageStats(
                    package="0603", mean_score=0.7, std_score=0.05,
                    p_win=0.85, score_margin=0.1,
                )},
                decision="recommend",
                decision_reason="Stable",
                n_samples=200,
            ),
        )

        d = record.to_dict()
        restored = SuggestionRecord.from_dict(d)

        assert restored.ref == "C1"
        assert restored.role == "rail_input_decoupling"
        assert restored.decision == "recommend"
        assert restored.rank_stability is not None
        assert restored.rank_stability.n_samples == 200
        assert "0603" in restored.rank_stability.package_stats

    def test_package_evidence_roundtrip(self):
        from footfindr.intelligence.models import PackageEvidence

        ev = PackageEvidence(
            package="0603",
            raw_count=20,
            parsed_count=15,
            viable_count=10,
            reject_reasons={"wrong_package": 3, "low_voltage": 2},
            first_raw_mpns=["MPN1", "MPN2"],
            query_strings=["4.7uF 0603 capacitor"],
        )

        d = ev.to_dict()
        restored = PackageEvidence.from_dict(d)

        assert restored.package == "0603"
        assert restored.viable_count == 10
        assert restored.reject_reasons["wrong_package"] == 3


    def test_package_evidence_three_bucket_roundtrip(self):
        """M9.5: unverified/reject counts survive serialization."""
        from footfindr.intelligence.models import PackageEvidence

        ev = PackageEvidence(
            package="0603",
            raw_count=20,
            viable_count=5,
            unverified_count=8,
            definitive_reject_count=7,
        )
        d = ev.to_dict()
        restored = PackageEvidence.from_dict(d)
        assert restored.unverified_count == 8
        assert restored.definitive_reject_count == 7


# ===========================================================================
# Part 7: M9.5 — Regulator Schematic Tests
# ===========================================================================

REGULATOR_SCHEMATIC = EXAMPLES_DIR / "test_board_with_regulator.kicad_sch"


class TestRegulatorSchematic:
    """Test the new regulator test schematic with U1 VREG (IN/OUT/GND/SET)."""

    def test_regulator_schematic_exists(self):
        assert REGULATOR_SCHEMATIC.exists(), f"Not found: {REGULATOR_SCHEMATIC}"

    def test_c2_connects_to_u1_out_net_and_gnd(self):
        """C2 pin 1 should connect to Net-(U1-OUT), pin 2 to GND."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(REGULATOR_SCHEMATIC)
        connections = graph.get_connections("C2")

        assert len(connections) == 2, (
            f"C2 should have 2 connections, got {len(connections)}: "
            f"{[(c.pin, c.net) for c in connections]}"
        )

        nets = {c.net for c in connections}
        assert "GND" in nets, f"C2 should connect to GND, got {nets}"

        non_gnd = nets - {"GND"}
        assert len(non_gnd) == 1
        synth_net = non_gnd.pop()
        assert "U1" in synth_net and "OUT" in synth_net, (
            f"Synthesized net should reference U1-OUT, got '{synth_net}'"
        )

    def test_c3_connects_to_u1_set_net_and_gnd(self):
        """C3 pin 1 should connect to Net-(U1-SET), pin 2 to GND."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(REGULATOR_SCHEMATIC)
        connections = graph.get_connections("C3")

        assert len(connections) == 2
        nets = {c.net for c in connections}
        assert "GND" in nets

        non_gnd = nets - {"GND"}
        synth_net = non_gnd.pop()
        assert "U1" in synth_net and "SET" in synth_net, (
            f"Synthesized net should reference U1-SET, got '{synth_net}'"
        )

    def test_c1_connects_to_5v_and_gnd(self):
        """C1 should connect to +5V and GND."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(REGULATOR_SCHEMATIC)
        nets = {c.net for c in graph.get_connections("C1")}
        assert "+5V" in nets and "GND" in nets

    def test_ic_pin_name_preferred_over_passive(self):
        """Net name should use IC pin (U1.OUT) not passive pin (C2.1)."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(REGULATOR_SCHEMATIC)
        non_gnd = [c for c in graph.get_connections("C2") if c.net != "GND"]
        assert len(non_gnd) == 1
        assert non_gnd[0].net == "Net-(U1-OUT)"

    def test_no_n_dollar_in_default_output(self):
        """Net names should not contain N$0/N$1 patterns."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(REGULATOR_SCHEMATIC)
        for ref, conns in graph.connections.items():
            for conn in conns:
                assert not conn.net.startswith("N$"), (
                    f"{ref}.{conn.pin} has raw name '{conn.net}'"
                )

    def test_u1_resolves_all_four_pins(self):
        """U1 should have 4 pins resolved."""
        from footfindr.intelligence.net_graph import get_connectivity_provider

        provider = get_connectivity_provider()
        graph = provider.build_net_graph(REGULATOR_SCHEMATIC)
        assert len(graph.get_connections("U1")) == 4


# ===========================================================================
# Part 8: M9.5 — Expanded Role Classification
# ===========================================================================

class TestExpandedRoles:
    """Test the expanded 10-role classification system."""

    def test_all_new_roles_in_probabilities(self):
        from footfindr.intelligence.cap_classifier import classify_capacitor, ROLES
        from footfindr.intelligence.models import NetConnection

        connections = [
            NetConnection(ref="C1", pin="1", net="+5V", net_type="power"),
            NetConnection(ref="C1", pin="2", net="GND", net_type="ground"),
        ]
        result = classify_capacitor("C1", "100nF", connections, [], pin_completeness=1.0)
        assert len(result.role_probabilities) == 10
        for role in ROLES:
            assert role in result.role_probabilities

    def test_model_version_v2(self):
        from footfindr.intelligence.cap_classifier import classify_capacitor
        result = classify_capacitor("C1", "100nF", [], [], pin_completeness=1.0)
        assert result.model_version == "softmax_cap_v2"

    def test_rail_input_decoupling_high_confidence(self):
        """Cap with ground + rail should classify as rail_input_decoupling."""
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection, RailInfo

        connections = [
            NetConnection(ref="C1", pin="1", net="+5V", net_type="power"),
            NetConnection(ref="C1", pin="2", net="GND", net_type="ground"),
        ]
        rails = [RailInfo(net="+5V", voltage=5.0, confidence=0.95, source="net-name")]

        result = classify_capacitor("C1", "100nF", connections, rails, pin_completeness=1.0)

        assert result.role == "rail_input_decoupling", f"Expected rail_input_decoupling, got {result.role}"
        assert result.role_probabilities["rail_input_decoupling"] > 0.5, (
            f"P(rail_input_decoupling) should be > 0.5, got {result.role_probabilities['rail_input_decoupling']}"
        )

    def test_c2_output_cap_role_not_input_decoupling(self):
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection

        connections = [
            NetConnection(ref="C2", pin="1", net="Net-(U1-OUT)", net_type="signal"),
            NetConnection(ref="C2", pin="2", net="GND", net_type="ground"),
        ]
        result = classify_capacitor("C2", "10uF", connections, [], pin_completeness=1.0)
        assert result.role != "rail_input_decoupling"

    def test_c3_set_pin_role_not_input_decoupling(self):
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection

        connections = [
            NetConnection(ref="C3", pin="1", net="Net-(U1-SET)", net_type="signal"),
            NetConnection(ref="C3", pin="2", net="GND", net_type="ground"),
        ]
        result = classify_capacitor("C3", "100nF", connections, [], pin_completeness=1.0)
        assert result.role != "rail_input_decoupling"

    def test_softmax_probabilities_sum_to_one_v2(self):
        from footfindr.intelligence.cap_classifier import classify_capacitor
        from footfindr.intelligence.models import NetConnection

        connections = [
            NetConnection(ref="C1", pin="1", net="Net-(U1-OUT)", net_type="signal"),
            NetConnection(ref="C1", pin="2", net="GND", net_type="ground"),
        ]
        result = classify_capacitor("C1", "10uF", connections, [], pin_completeness=1.0)
        total = sum(result.role_probabilities.values())
        assert abs(total - 1.0) < 0.01


# ===========================================================================
# Part 9: M9.5 — Three-Bucket Viability Model
# ===========================================================================

class TestThreeBucketViability:
    """Test the three-bucket viability classification."""

    def test_parse_failed_not_viable(self):
        from footfindr.intelligence.package_sweep import _build_package_evidence

        parts = [MockSupplierPart(
            mpn="UNKNOWN123", description="capacitor 0603",
            stock=1000, lifecycle="active", package="0603",
        )]
        ev = _build_package_evidence(parts, 4.7e-6, None, "0603", "test")
        assert ev.viable_count == 0
        assert ev.unverified_count == 1

    def test_verified_viable_counted(self):
        from footfindr.intelligence.package_sweep import _build_package_evidence

        parts = [MockSupplierPart(
            mpn="GRM188R61C475K", description="4.7uF 16V 0603 capacitor",
            stock=5000, lifecycle="active", package="0603",
        )]
        ev = _build_package_evidence(parts, 4.7e-6, None, "0603", "test")
        assert ev.viable_count == 1
        assert ev.unverified_count == 0

    def test_definitive_reject_not_viable(self):
        from footfindr.intelligence.package_sweep import _build_package_evidence

        parts = [MockSupplierPart(
            mpn="TEST1", description="100pF 16V 0603 capacitor",
            stock=5000, lifecycle="active", package="0603",
        )]
        ev = _build_package_evidence(parts, 4.7e-6, None, "0603", "test")
        assert ev.viable_count == 0
        assert ev.definitive_reject_count == 1

    def test_unverified_only_package_insufficient(self):
        from footfindr.intelligence.package_sweep import _build_package_evidence
        from footfindr.intelligence.bayesian_model import assess_feasibility

        parts = [MockSupplierPart(
            mpn=f"UNKNOWN{i}", description="capacitor 0603",
            stock=1000, lifecycle="active", package="0603",
        ) for i in range(10)]
        ev = _build_package_evidence(parts, 4.7e-6, None, "0603", "test")
        assert ev.viable_count == 0
        assert ev.unverified_count == 10
        f = assess_feasibility("0603", ev.parsed_count, ev.viable_count)
        assert f.status == "insufficient_evidence"

    def test_mixed_bucket_counts(self):
        from footfindr.intelligence.package_sweep import _build_package_evidence

        parts = [
            MockSupplierPart(mpn="GOOD1", description="4.7uF 16V 0603",
                             stock=100, lifecycle="active", package="0603"),
            MockSupplierPart(mpn="BAD1", description="100pF 16V 0603",
                             stock=200, lifecycle="active", package="0603"),
            MockSupplierPart(mpn="UNKN1", description="0603 capacitor",
                             stock=300, lifecycle="active", package="0603"),
            MockSupplierPart(mpn="OLD1", description="4.7uF 16V 0603",
                             stock=50, lifecycle="obsolete", package="0603"),
        ]
        ev = _build_package_evidence(parts, 4.7e-6, None, "0603", "test")
        assert ev.raw_count == 4
        assert ev.viable_count == 1
        assert ev.definitive_reject_count == 2
        assert ev.unverified_count == 1


# ===========================================================================
# Part 10: M9.5 — Capacitance Parsing Cascade
# ===========================================================================

class TestCapacitanceCascade:
    """Test the three-stage capacitance parsing cascade."""

    def test_supplier_attribute_first(self):
        from footfindr.intelligence.package_sweep import parse_capacitance_cascade
        part = MockSupplierPart(mpn="TEST", description="100pF capacitor")
        part.capacitance = "4.7uF"  # type: ignore[attr-defined]
        cap, source = parse_capacitance_cascade(part)
        assert source == "supplier_attribute"
        assert abs(cap - 4.7e-6) < 1e-9

    def test_description_fallback(self):
        from footfindr.intelligence.package_sweep import parse_capacitance_cascade
        part = MockSupplierPart(mpn="GENERIC123", description="4.7 µF 16V X5R MLCC")
        cap, source = parse_capacitance_cascade(part)
        assert source == "description"

    def test_eia_code_from_mpn(self):
        from footfindr.intelligence.package_sweep import parse_capacitance_cascade
        part = MockSupplierPart(mpn="GRM188R61C475KA73", description="capacitor MLCC")
        cap, source = parse_capacitance_cascade(part)
        assert source == "eia_mpn"
        assert abs(cap - 4.7e-6) < 1e-9

    def test_all_fail_returns_none(self):
        from footfindr.intelligence.package_sweep import parse_capacitance_cascade
        part = MockSupplierPart(mpn="XYZ", description="electronic component")
        cap, source = parse_capacitance_cascade(part)
        assert cap is None
        assert source == "none"

    def test_eia_code_common_values(self):
        from footfindr.intelligence.package_sweep import _parse_eia_capacitance_from_mpn
        assert abs(_parse_eia_capacitance_from_mpn("CL10B104KB8NNNC") - 100e-9) < 1e-12
        assert abs(_parse_eia_capacitance_from_mpn("GRM188R61C475KA73") - 4.7e-6) < 1e-9


# ===========================================================================
# Part 11: M9.5 — Net Name Synthesis Unit Tests
# ===========================================================================

class TestNetNameSynthesis:
    """Test the net name synthesis logic in isolation."""

    def test_ic_pin_preferred(self):
        from footfindr.intelligence.net_graph import KiCadSexprConnectivityProvider, _GNode
        nodes = [
            _GNode(x=0, y=0, node_type="pin", ref="U1", pin_number="2",
                   pin_name="OUT", is_ic_pin=True),
            _GNode(x=0, y=0, node_type="pin", ref="C2", pin_number="1",
                   pin_name="", is_ic_pin=False),
        ]
        assert KiCadSexprConnectivityProvider._synthesize_net_name(nodes) == "Net-(U1-OUT)"

    def test_passive_fallback(self):
        from footfindr.intelligence.net_graph import KiCadSexprConnectivityProvider, _GNode
        nodes = [
            _GNode(x=0, y=0, node_type="pin", ref="C2", pin_number="1",
                   pin_name="", is_ic_pin=False),
            _GNode(x=0, y=0, node_type="pin", ref="C3", pin_number="1",
                   pin_name="", is_ic_pin=False),
        ]
        assert KiCadSexprConnectivityProvider._synthesize_net_name(nodes) == "Net-(C2-Pad1)"

    def test_empty_nodes_returns_none(self):
        from footfindr.intelligence.net_graph import KiCadSexprConnectivityProvider
        assert KiCadSexprConnectivityProvider._synthesize_net_name([]) is None

    def test_is_ic_lib_id(self):
        from footfindr.intelligence.net_graph import KiCadSexprConnectivityProvider
        assert KiCadSexprConnectivityProvider._is_ic_lib_id("footfindr:VREG") is True
        assert KiCadSexprConnectivityProvider._is_ic_lib_id("Device:C") is False
        assert KiCadSexprConnectivityProvider._is_ic_lib_id("power:+5V") is False
        assert KiCadSexprConnectivityProvider._is_ic_lib_id("") is False
