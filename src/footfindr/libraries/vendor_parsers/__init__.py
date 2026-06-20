"""Vendor parser registry.

Exposes ``get_parser(vendor_type)`` to look up the correct parser for
a given vendor slug (e.g. ``"murata-grm"``, ``"generic"``).
"""

from __future__ import annotations

from footfindr.libraries.vendor_parsers.base import VendorParser, VendorParseResult


_REGISTRY: dict[str, type[VendorParser]] = {}
_BOOTSTRAPPED: bool = False


def register_parser(slug: str, cls: type[VendorParser]) -> None:
    """Register a vendor parser class under a slug."""
    _REGISTRY[slug] = cls


def _ensure_bootstrapped() -> None:
    """Import concrete parser modules so they self-register."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    # Each module calls ``register_parser()`` at import time.
    import footfindr.libraries.vendor_parsers.murata_grm  # noqa: F401
    import footfindr.libraries.vendor_parsers.generic_csv  # noqa: F401


def get_parser(vendor_type: str) -> VendorParser:
    """Instantiate and return a parser for *vendor_type*.

    Raises ``ValueError`` if the slug is unknown.
    """
    _ensure_bootstrapped()

    key = vendor_type.lower().replace("_", "-")
    cls = _REGISTRY.get(key)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"Unknown vendor type: '{vendor_type}'. "
            f"Available: {available}"
        )
    return cls()


def list_parsers() -> list[str]:
    """Return sorted list of registered parser slugs."""
    _ensure_bootstrapped()
    return sorted(_REGISTRY.keys())
