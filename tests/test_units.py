"""Tests for the EE unit parser."""

import pytest
from footfindr.core.units import (
    detect_category,
    normalize_capacitance_display,
    normalize_resistance_display,
    parse_capacitance,
    parse_current,
    parse_power,
    parse_resistance,
    parse_voltage,
    capacitances_match,
    resistances_match,
)


# ---------------------------------------------------------------------------
# Capacitance parsing
# ---------------------------------------------------------------------------

class TestParseCapacitance:
    @pytest.mark.parametrize("raw, expected", [
        ("100nF", 100e-9),
        ("0.1uF", 0.1e-6),
        ("1u", 1e-6),
        ("1uF", 1e-6),
        ("4u7", 4.7e-6),
        ("10uF", 10e-6),
        ("22uF", 22e-6),
        ("100pF", 100e-12),
        ("1nF", 1e-9),
        ("10nF", 10e-9),
        ("4.7uF", 4.7e-6),
        ("47pF", 47e-12),
    ])
    def test_parse_capacitance(self, raw: str, expected: float) -> None:
        result = parse_capacitance(raw)
        assert result is not None
        assert pytest.approx(result, rel=0.01) == expected

    def test_parse_empty(self) -> None:
        assert parse_capacitance("") is None
        assert parse_capacitance("  ") is None


# ---------------------------------------------------------------------------
# Resistance parsing
# ---------------------------------------------------------------------------

class TestParseResistance:
    @pytest.mark.parametrize("raw, expected", [
        ("271", 271.0),
        ("271R", 271.0),
        ("270", 270.0),
        ("10k", 10_000.0),
        ("4k7", 4_700.0),
        ("0.1R", 0.1),
        ("0R1", 0.1),
        ("1M", 1_000_000.0),
        ("1k", 1_000.0),
        ("100", 100.0),
        ("47k", 47_000.0),
    ])
    def test_parse_resistance(self, raw: str, expected: float) -> None:
        result = parse_resistance(raw)
        assert result is not None
        assert pytest.approx(result, rel=0.01) == expected


# ---------------------------------------------------------------------------
# Voltage parsing
# ---------------------------------------------------------------------------

class TestParseVoltage:
    @pytest.mark.parametrize("raw, expected", [
        ("6.3V", 6.3),
        ("16V", 16.0),
        ("3.3V", 3.3),
        ("25V", 25.0),
    ])
    def test_parse_voltage(self, raw: str, expected: float) -> None:
        result = parse_voltage(raw)
        assert result is not None
        assert pytest.approx(result, rel=0.01) == expected


# ---------------------------------------------------------------------------
# Power parsing
# ---------------------------------------------------------------------------

class TestParsePower:
    @pytest.mark.parametrize("raw, expected", [
        ("0.1W", 0.1),
        ("0.25W", 0.25),
        ("1/4W", 0.25),
        ("1/8W", 0.125),
    ])
    def test_parse_power(self, raw: str, expected: float) -> None:
        result = parse_power(raw)
        assert result is not None
        assert pytest.approx(result, rel=0.01) == expected


# ---------------------------------------------------------------------------
# Current parsing
# ---------------------------------------------------------------------------

class TestParseCurrent:
    @pytest.mark.parametrize("raw, expected", [
        ("500mA", 0.5),
        ("2A", 2.0),
    ])
    def test_parse_current(self, raw: str, expected: float) -> None:
        result = parse_current(raw)
        assert result is not None
        assert pytest.approx(result, rel=0.01) == expected


# ---------------------------------------------------------------------------
# Display normalisation
# ---------------------------------------------------------------------------

class TestNormalizeDisplay:
    def test_capacitance_display(self) -> None:
        assert normalize_capacitance_display(1e-7) == "100nF"
        assert normalize_capacitance_display(1e-5) == "10uF"
        assert normalize_capacitance_display(4.7e-6) == "4.7uF"
        assert normalize_capacitance_display(100e-12) == "100pF"

    def test_resistance_display(self) -> None:
        assert normalize_resistance_display(271.0) == "271R"
        assert normalize_resistance_display(10000.0) == "10k"
        assert normalize_resistance_display(4700.0) == "4.7k"
        assert normalize_resistance_display(0.1) == "0.1R"
        assert normalize_resistance_display(1000000.0) == "1M"


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

class TestComparison:
    def test_capacitances_match(self) -> None:
        assert capacitances_match(10e-6, 10e-6)
        assert capacitances_match(10e-6, 10.1e-6)  # within 5%
        assert not capacitances_match(10e-6, 20e-6)

    def test_resistances_match(self) -> None:
        assert resistances_match(271.0, 271.0)
        assert not resistances_match(271.0, 300.0)


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

class TestDetectCategory:
    @pytest.mark.parametrize("ref, expected", [
        ("C17", "capacitor"),
        ("C1", "capacitor"),
        ("R3", "resistor"),
        ("R100", "resistor"),
        ("U1", "ic"),
        ("U27", "ic"),
        ("L1", "inductor"),
        ("D4", "diode"),
        ("J1", "connector"),
        ("Q2", "transistor"),
    ])
    def test_detect_category(self, ref: str, expected: str) -> None:
        assert detect_category(ref) == expected
