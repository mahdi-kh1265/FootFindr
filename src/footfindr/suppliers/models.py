"""Supplier data models for FootFindr.

Defines the full data structures for supplier parts, offers, price breaks,
and cache entries.  These are used by all supplier providers and the
supplier cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PriceBreak:
    """A single quantity-price tier."""
    quantity: int
    unit_price: float
    currency: str = "USD"


@dataclass
class SupplierPart:
    """Full part record from a supplier lookup."""
    supplier: str
    supplier_pn: str | None = None
    supplier_url: str | None = None
    mpn: str = ""
    manufacturer: str | None = None
    description: str | None = None
    stock: int | None = None
    price_breaks: list[PriceBreak] = field(default_factory=list)
    currency: str = "USD"
    minimum_order_quantity: int | None = None
    packaging: str | None = None  # tape, reel, cut-tape, etc.
    lead_time: str | None = None
    datasheet_url: str | None = None
    lifecycle: str | None = None  # active, nrnd, obsolete
    last_checked: str | None = None
    source: str = "live"  # 'live', 'mock', 'manual', 'cache'
    category: str | None = None
    package: str | None = None
    product_url: str | None = None
    lcsc_pn: str | None = None  # LCSC part number (e.g., C15850)
    jlc_category: str | None = None  # basic/extended/preferred

    # Rich variant browsing fields (M8.5)
    mounting_type: str | None = None          # "Surface Mount", "Through Hole"
    temperature_range: str | None = None      # "-40°C ~ 85°C"
    supplier_device_package: str | None = None  # Supplier's package name
    product_status: str | None = None         # "Active", "NRND", etc.
    attributes: dict[str, str] = field(default_factory=dict)  # Raw param key→value
    badges: list[str] = field(default_factory=list)  # Computed risk/status badges

    @property
    def result_id(self) -> str:
        """Stable identity key for session state, shortlist, and promotion.

        Format: ``supplier|manufacturer|mpn|supplier_pn``
        """
        return "|".join([
            self.supplier or "",
            self.manufacturer or "",
            self.mpn or "",
            self.supplier_pn or "",
        ])

    def is_valid(self) -> bool:
        """Check if this supplier part has enough data to be useful.

        A valid result must have at least a non-empty MPN.  Results with
        no MPN, no supplier_pn, no manufacturer, and no stock are treated
        as empty/corrupt and should not be cached or displayed as success.
        """
        if self.mpn and self.mpn.strip():
            return True
        if self.supplier_pn and self.supplier_pn.strip():
            return True
        return False

    def best_price(self, qty: int | None = None) -> float | None:
        """Return unit price for the given quantity, or cheapest break."""
        if not self.price_breaks:
            return None
        if qty is None:
            return self.price_breaks[0].unit_price
        # Find the best price break <= qty
        applicable = [pb for pb in self.price_breaks if pb.quantity <= qty]
        if applicable:
            return applicable[-1].unit_price
        return self.price_breaks[0].unit_price


@dataclass
class SupplierOffer:
    """A supplier offer for stock/price refresh."""
    supplier: str
    supplier_pn: str | None = None
    stock: int | None = None
    price_breaks: list[PriceBreak] = field(default_factory=list)
    currency: str = "USD"
    minimum_order_quantity: int | None = None
    packaging: str | None = None
    lead_time: str | None = None
    last_checked: str | None = None
    source: str = "live"


@dataclass
class CacheEntry:
    """Wrapper for cached supplier data."""
    manufacturer: str | None = None
    mpn: str = ""
    supplier: str = ""
    supplier_pn: str | None = None
    supplier_url: str | None = None
    stock: int | None = None
    price_breaks_json: str | None = None  # JSON serialized
    currency: str = "USD"
    minimum_order_quantity: int | None = None
    packaging: str | None = None
    lead_time: str | None = None
    datasheet_url: str | None = None
    lifecycle: str | None = None
    last_checked: str | None = None
    source: str = "live"
