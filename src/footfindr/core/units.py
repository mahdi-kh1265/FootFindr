"""Electronics-focused unit parser for FootFindr.

Parses EE-conventional strings like 10uF, 100nF, 4u7, 10k, 271R, 0R1, 16V, 0.25W
into canonical SI numerical values.  Does *not* use Pint — the patterns here
are much narrower and more forgiving of EE shorthand than a general unit library.

Every public function is deterministic and side-effect-free.
"""

from __future__ import annotations

import math
import re
from typing import Optional

# ---------------------------------------------------------------------------
# SI multiplier maps
# ---------------------------------------------------------------------------

_SI_PREFIX: dict[str, float] = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}

# Used specifically for resistor parsing where R means ×1 (decimal separator)
_RESISTOR_MULTIPLIER: dict[str, float] = {
    "R": 1.0,
    "r": 1.0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "m": 1e-3,  # milliohm
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Pattern: number with optional embedded multiplier letter (e.g. 4k7, 0R1, 4u7)
_EMBEDDED_RE = re.compile(
    r"^(\d+)"                      # integer part before the letter
    r"([a-zA-ZµΩ])"                # multiplier/unit letter
    r"(\d+)$"                      # fractional part after the letter
)

# Pattern: normal number followed by optional unit (e.g. 10uF, 100nF, 271R, 16V)
_NORMAL_RE = re.compile(
    r"^([0-9]*\.?[0-9]+)"          # number (int or float)
    r"\s*"
    r"([fpnuµmkKMGT]?)"           # SI prefix (optional)
    r"([FΩRVWAHhz]?)"            # unit letter (optional)
    r"([a-zA-Z]*)"                 # trailing unit text (ohm, Hz, etc.)
    r"$"
)

# Fraction pattern for power: 1/4W, 1/8W
_FRACTION_RE = re.compile(
    r"^(\d+)\s*/\s*(\d+)"         # fraction
    r"\s*"
    r"([WVA]?)$"                   # optional unit
)


def _parse_ee_value(
    raw: str,
    *,
    default_unit: str = "",
    category: str = "generic",
) -> Optional[float]:
    """Parse an EE value string into a float in base SI units.

    Returns None if the string cannot be parsed.
    """
    s = raw.strip()
    if not s:
        return None

    # Try fraction first (1/4W, 1/8W)
    m = _FRACTION_RE.match(s)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return None
        return num / den

    # Try embedded multiplier form: 4k7, 0R1, 4u7
    m = _EMBEDDED_RE.match(s)
    if m:
        integer_part = m.group(1)
        letter = m.group(2)
        frac_part = m.group(3)
        combined = float(f"{integer_part}.{frac_part}")

        # Determine multiplier
        if category == "resistance" and letter.upper() == "R":
            return combined * 1.0
        elif letter in _SI_PREFIX:
            return combined * _SI_PREFIX[letter]
        elif letter in _RESISTOR_MULTIPLIER and category == "resistance":
            return combined * _RESISTOR_MULTIPLIER[letter]
        return None

    # Try normal form: 10uF, 100nF, 271R, 16V, 0.25W, bare number
    m = _NORMAL_RE.match(s)
    if m:
        number = float(m.group(1))
        prefix = m.group(2)
        unit_letter = m.group(3)
        trailing = m.group(4)

        # Determine multiplier from prefix
        if prefix in _SI_PREFIX:
            multiplier = _SI_PREFIX[prefix]
        else:
            multiplier = 1.0

        # Special case: bare number for resistance (e.g. "271", "10000")
        if not prefix and not unit_letter and not trailing and category == "resistance":
            return number

        # Special case: number + R for resistance (e.g. "271R", "0.1R")
        if unit_letter in ("R", "r", "Ω") or trailing.lower() in ("ohm", "ohms"):
            return number * multiplier

        return number * multiplier

    return None


# ---------------------------------------------------------------------------
# Public API: parse to canonical float values
# ---------------------------------------------------------------------------

def parse_capacitance(raw: str) -> Optional[float]:
    """Parse a capacitance string to farads.

    Examples:
        >>> parse_capacitance("100nF")
        1e-07
        >>> parse_capacitance("10uF")
        1e-05
        >>> parse_capacitance("4u7")
        4.7e-06
        >>> parse_capacitance("100pF")
        1e-10
    """
    s = raw.strip()
    if not s:
        return None

    # Remove trailing 'F' or 'f' if present after processing
    # Handle forms like "100nF", "10uF", "4u7"
    return _parse_ee_value(s, category="capacitance")


def parse_resistance(raw: str) -> Optional[float]:
    """Parse a resistance string to ohms.

    Examples:
        >>> parse_resistance("271")
        271.0
        >>> parse_resistance("271R")
        271.0
        >>> parse_resistance("10k")
        10000.0
        >>> parse_resistance("4k7")
        4700.0
        >>> parse_resistance("0.1R")
        0.1
        >>> parse_resistance("0R1")
        0.1
        >>> parse_resistance("1M")
        1000000.0
    """
    s = raw.strip()
    if not s:
        return None

    return _parse_ee_value(s, category="resistance")


def parse_voltage(raw: str) -> Optional[float]:
    """Parse a voltage string to volts.

    Examples:
        >>> parse_voltage("16V")
        16.0
        >>> parse_voltage("6.3V")
        6.3
    """
    s = raw.strip()
    if not s:
        return None
    return _parse_ee_value(s, category="voltage")


def parse_power(raw: str) -> Optional[float]:
    """Parse a power string to watts.

    Examples:
        >>> parse_power("0.25W")
        0.25
        >>> parse_power("0.1W")
        0.1
        >>> parse_power("1/4W")
        0.25
    """
    s = raw.strip()
    if not s:
        return None
    return _parse_ee_value(s, category="power")


def parse_current(raw: str) -> Optional[float]:
    """Parse a current string to amperes.

    Examples:
        >>> parse_current("500mA")
        0.5
        >>> parse_current("2A")
        2.0
    """
    s = raw.strip()
    if not s:
        return None
    return _parse_ee_value(s, category="current")


# ---------------------------------------------------------------------------
# Display normalisation — canonical human-readable strings
# ---------------------------------------------------------------------------

def normalize_capacitance_display(farads: float) -> str:
    """Format a capacitance value into the most readable EE string.

    Examples:
        >>> normalize_capacitance_display(1e-7)
        '100nF'
        >>> normalize_capacitance_display(1e-5)
        '10uF'
        >>> normalize_capacitance_display(4.7e-6)
        '4.7uF'
    """
    if farads >= 1.0:
        return _fmt_value(farads, "F")
    elif farads >= 1e-3:
        return _fmt_value(farads * 1e3, "mF")
    elif farads >= 1e-6:
        return _fmt_value(farads * 1e6, "uF")
    elif farads >= 1e-9:
        return _fmt_value(farads * 1e9, "nF")
    else:
        return _fmt_value(farads * 1e12, "pF")


def normalize_resistance_display(ohms: float) -> str:
    """Format a resistance value into the most readable EE string.

    Examples:
        >>> normalize_resistance_display(271.0)
        '271R'
        >>> normalize_resistance_display(10000.0)
        '10k'
        >>> normalize_resistance_display(4700.0)
        '4.7k'
        >>> normalize_resistance_display(0.1)
        '0.1R'
        >>> normalize_resistance_display(1000000.0)
        '1M'
    """
    if ohms >= 1e6:
        return _fmt_value(ohms / 1e6, "M")
    elif ohms >= 1e3:
        return _fmt_value(ohms / 1e3, "k")
    else:
        return _fmt_value(ohms, "R")


def normalize_voltage_display(volts: float) -> str:
    """Format voltage value."""
    return _fmt_value(volts, "V")


def normalize_power_display(watts: float) -> str:
    """Format power value."""
    if watts < 1.0:
        return _fmt_value(watts * 1e3, "mW")
    return _fmt_value(watts, "W")


def _fmt_value(val: float, suffix: str) -> str:
    """Format a value, stripping trailing zeros."""
    if val == int(val):
        return f"{int(val)}{suffix}"
    # Use up to 3 decimal places, strip trailing zeros
    formatted = f"{val:.3f}".rstrip("0").rstrip(".")
    return f"{formatted}{suffix}"


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

def capacitances_match(a: float, b: float, rel_tol: float = 0.05) -> bool:
    """Check if two capacitance values are equivalent within tolerance."""
    return math.isclose(a, b, rel_tol=rel_tol)


def resistances_match(a: float, b: float, rel_tol: float = 0.02) -> bool:
    """Check if two resistance values are equivalent within tolerance."""
    if a == 0 and b == 0:
        return True
    return math.isclose(a, b, rel_tol=rel_tol)


# ---------------------------------------------------------------------------
# Component category detection from schematic reference designator
# ---------------------------------------------------------------------------

_REF_CATEGORY_MAP: dict[str, str] = {
    "C": "capacitor",
    "R": "resistor",
    "L": "inductor",
    "U": "ic",
    "IC": "ic",
    "J": "connector",
    "P": "connector",
    "D": "diode",
    "Q": "transistor",
    "Y": "crystal",
    "LED": "led",
}


def detect_category(ref: str) -> Optional[str]:
    """Detect component category from reference designator prefix.

    Examples:
        >>> detect_category("C17")
        'capacitor'
        >>> detect_category("R3")
        'resistor'
        >>> detect_category("U1")
        'ic'
    """
    # Extract alpha prefix
    prefix = ""
    for ch in ref:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return _REF_CATEGORY_MAP.get(prefix.upper())


def detect_category_from_lib_id(lib_id: str) -> Optional[str]:
    """Detect category from KiCad symbol library ID.

    Examples:
        >>> detect_category_from_lib_id("Device:C")
        'capacitor'
        >>> detect_category_from_lib_id("Device:R")
        'resistor'
    """
    if not lib_id:
        return None
    # Take the part after ':'
    name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    name_lower = name.lower()

    if name_lower in ("c", "c_small", "c_polarized", "c_polarized_small"):
        return "capacitor"
    if name_lower in ("r", "r_small", "r_us"):
        return "resistor"
    if name_lower in ("l", "l_small"):
        return "inductor"
    if "led" in name_lower:
        return "led"
    if name_lower.startswith("d"):
        return "diode"
    if name_lower.startswith("q_"):
        return "transistor"
    if name_lower.startswith(("conn", "jack", "plug")):
        return "connector"
    return None
