"""Tests for equivalent-value parametric search.

Verifies that queries like 100n, 0.1u, and 0.10uF return the same parts,
and that 4k7, 4700, and 4.7k return the same resistance parts.
"""

from __future__ import annotations

import pytest

from footfindr.core.models import (
    ComponentCategory,
    ElectricalSpecs,
    PartRecord,
    PartStatus,
)
from footfindr.libraries.promotion import (
    _dielectric_matches,
    _normalize_query_value,
    _value_matches,
)


def _make_cap(internal_pn: str, cap_str: str, **kwargs) -> PartRecord:
    """Create a capacitor PartRecord for testing."""
    return PartRecord(
        internal_pn=internal_pn,
        category=ComponentCategory.CAPACITOR,
        value=cap_str,
        specs=ElectricalSpecs(
            capacitance=cap_str,
            voltage_rating=kwargs.get("voltage", None),
            dielectric=kwargs.get("dielectric", None),
            tolerance=kwargs.get("tolerance", None),
        ),
        package=kwargs.get("package", None),
        manufacturer=kwargs.get("manufacturer", None),
        mpn=kwargs.get("mpn", None),
        status=PartStatus.RAW,
        approved=False,
    )


def _make_res(internal_pn: str, res_str: str, **kwargs) -> PartRecord:
    """Create a resistor PartRecord for testing."""
    return PartRecord(
        internal_pn=internal_pn,
        category=ComponentCategory.RESISTOR,
        value=res_str,
        specs=ElectricalSpecs(
            resistance=res_str,
            tolerance=kwargs.get("tolerance", None),
        ),
        package=kwargs.get("package", None),
        manufacturer=kwargs.get("manufacturer", None),
        mpn=kwargs.get("mpn", None),
        status=PartStatus.RAW,
        approved=False,
    )


# ---------------------------------------------------------------------------
# Normalize query value
# ---------------------------------------------------------------------------

class TestNormalizeQueryValue:
    def test_capacitance_100n(self):
        val, domain = _normalize_query_value("100n", category_hint="capacitor")
        assert domain == "capacitance"
        assert val == pytest.approx(1e-7)

    def test_capacitance_0_1u(self):
        val, domain = _normalize_query_value("0.1u", category_hint="capacitor")
        assert domain == "capacitance"
        assert val == pytest.approx(1e-7)

    def test_capacitance_0_10uF(self):
        val, domain = _normalize_query_value("0.10uF", category_hint="capacitor")
        assert domain == "capacitance"
        assert val == pytest.approx(1e-7)

    def test_capacitance_100nF(self):
        val, domain = _normalize_query_value("100nF", category_hint="cap")
        assert domain == "capacitance"
        assert val == pytest.approx(1e-7)

    def test_capacitance_10u(self):
        val, domain = _normalize_query_value("10u", category_hint="cap")
        assert domain == "capacitance"
        assert val == pytest.approx(1e-5)

    def test_capacitance_4u7(self):
        val, domain = _normalize_query_value("4u7", category_hint="cap")
        assert domain == "capacitance"
        assert val == pytest.approx(4.7e-6)

    def test_capacitance_100p(self):
        val, domain = _normalize_query_value("100p", category_hint="cap")
        assert domain == "capacitance"
        assert val == pytest.approx(1e-10)

    def test_resistance_4k7(self):
        val, domain = _normalize_query_value("4k7", category_hint="resistor")
        assert domain == "resistance"
        assert val == pytest.approx(4700.0)

    def test_resistance_4700(self):
        val, domain = _normalize_query_value("4700", category_hint="resistor")
        assert domain == "resistance"
        assert val == pytest.approx(4700.0)

    def test_resistance_4_7k(self):
        val, domain = _normalize_query_value("4.7k", category_hint="resistor")
        assert domain == "resistance"
        assert val == pytest.approx(4700.0)

    def test_resistance_0R1(self):
        val, domain = _normalize_query_value("0R1", category_hint="resistor")
        assert domain == "resistance"
        assert val == pytest.approx(0.1)

    def test_resistance_0_1R(self):
        val, domain = _normalize_query_value("0.1R", category_hint="resistor")
        assert domain == "resistance"
        assert val == pytest.approx(0.1)

    def test_resistance_10k(self):
        val, domain = _normalize_query_value("10k", category_hint="res")
        assert domain == "resistance"
        assert val == pytest.approx(10000.0)

    def test_unknown_query(self):
        val, domain = _normalize_query_value("murata grm", category_hint=None)
        assert val is None
        assert domain == "unknown"

    def test_empty_query(self):
        val, domain = _normalize_query_value("")
        assert val is None
        assert domain == "unknown"


# ---------------------------------------------------------------------------
# Value matching
# ---------------------------------------------------------------------------

class TestValueMatches:
    def test_cap_100n_matches_100nF(self):
        cap = _make_cap("CAP1", "100nF")
        assert _value_matches(1e-7, "capacitance", cap)

    def test_cap_100n_matches_0_1uF(self):
        cap = _make_cap("CAP2", "0.1µF")
        assert _value_matches(1e-7, "capacitance", cap)

    def test_cap_100n_no_match_10nF(self):
        cap = _make_cap("CAP3", "10nF")
        assert not _value_matches(1e-7, "capacitance", cap)

    def test_res_4k7_matches_4700(self):
        res = _make_res("RES1", "4700")
        assert _value_matches(4700.0, "resistance", res)

    def test_res_4k7_matches_4k7(self):
        res = _make_res("RES2", "4k7")
        assert _value_matches(4700.0, "resistance", res)

    def test_res_4k7_matches_4_7k(self):
        res = _make_res("RES3", "4.7k")
        assert _value_matches(4700.0, "resistance", res)

    def test_res_4k7_no_match_4_7M(self):
        res = _make_res("RES4", "4.7M")
        assert not _value_matches(4700.0, "resistance", res)

    def test_0R1_equivalences(self):
        res1 = _make_res("RES5", "0R1")
        res2 = _make_res("RES6", "0.1R")
        assert _value_matches(0.1, "resistance", res1)
        assert _value_matches(0.1, "resistance", res2)


# ---------------------------------------------------------------------------
# Equivalent search: all queries for same value return same set
# ---------------------------------------------------------------------------

class TestEquivalentSearch:
    """Tests that different representations of the same value
    produce the same normalized query value."""

    def test_100n_0_1u_equivalence(self):
        v1, d1 = _normalize_query_value("100n", "cap")
        v2, d2 = _normalize_query_value("0.1u", "cap")
        v3, d3 = _normalize_query_value("0.10uF", "cap")
        assert d1 == d2 == d3 == "capacitance"
        assert v1 == pytest.approx(v2)
        assert v2 == pytest.approx(v3)

    def test_100p_0_1n(self):
        v1, d1 = _normalize_query_value("100p", "cap")
        v2, d2 = _normalize_query_value("0.1n", "cap")
        assert d1 == d2 == "capacitance"
        assert v1 == pytest.approx(v2)

    def test_4k7_4700_4_7k_equivalence(self):
        v1, d1 = _normalize_query_value("4k7", "res")
        v2, d2 = _normalize_query_value("4700", "res")
        v3, d3 = _normalize_query_value("4.7k", "res")
        assert d1 == d2 == d3 == "resistance"
        assert v1 == pytest.approx(v2)
        assert v2 == pytest.approx(v3)

    def test_tight_tolerance_no_false_positives(self):
        """100n should NOT match 99n or 101n."""
        cap_100n = _make_cap("CAP100n", "100nF")
        cap_99n = _make_cap("CAP99n", "99nF")
        cap_101n = _make_cap("CAP101n", "101nF")

        v, d = _normalize_query_value("100n", "cap")
        assert _value_matches(v, d, cap_100n)
        assert not _value_matches(v, d, cap_99n)
        assert not _value_matches(v, d, cap_101n)


# ---------------------------------------------------------------------------
# Dielectric matching
# ---------------------------------------------------------------------------

class TestDielectricMatching:
    def test_exact_match(self):
        assert _dielectric_matches("X7R", "X7R")
        assert _dielectric_matches("x7r", "X7R")

    def test_c0g_np0_alias(self):
        assert _dielectric_matches("c0g", "C0G")
        assert _dielectric_matches("C0G", "NP0")
        assert _dielectric_matches("np0", "C0G")
        assert _dielectric_matches("NP0", "NP0")

    def test_no_match(self):
        assert not _dielectric_matches("X7R", "C0G")
        assert not _dielectric_matches("X5R", None)
        assert not _dielectric_matches("X7R", "X5R")
