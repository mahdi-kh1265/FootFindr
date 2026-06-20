"""Mock supplier provider for testing and development.

Returns canned data for known test MPNs.  Does not make any network calls.
All results are marked with ``source='mock'``.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from footfindr.suppliers.base import ProviderCapabilities, SupplierProvider
from footfindr.suppliers.models import PriceBreak, SupplierOffer, SupplierPart

# Canned mock data for known parts
_MOCK_PARTS: dict[str, SupplierPart] = {
    "GRM21BB31C106KE15": SupplierPart(
        supplier="mock",
        supplier_pn="MOCK-GRM21BB31C106KE15",
        supplier_url="https://mock.example.com/GRM21BB31C106KE15",
        mpn="GRM21BB31C106KE15",
        manufacturer="Murata",
        description="10uF 16V X7R 0805 MLCC",
        stock=25000,
        price_breaks=[
            PriceBreak(quantity=1, unit_price=0.12),
            PriceBreak(quantity=10, unit_price=0.08),
            PriceBreak(quantity=100, unit_price=0.05),
            PriceBreak(quantity=1000, unit_price=0.03),
        ],
        currency="USD",
        minimum_order_quantity=1,
        packaging="tape-reel",
        lead_time="2-4 weeks",
        datasheet_url="https://mock.example.com/datasheets/GRM21BB31C106KE15.pdf",
        lifecycle="active",
        last_checked=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source="mock",
        category="capacitor",
        package="0805",
    ),
    "GRM155R71C104KA88": SupplierPart(
        supplier="mock",
        supplier_pn="MOCK-GRM155R71C104KA88",
        supplier_url="https://mock.example.com/GRM155R71C104KA88",
        mpn="GRM155R71C104KA88",
        manufacturer="Murata",
        description="100nF 16V X7R 0402 MLCC",
        stock=150000,
        price_breaks=[
            PriceBreak(quantity=1, unit_price=0.02),
            PriceBreak(quantity=100, unit_price=0.01),
            PriceBreak(quantity=1000, unit_price=0.005),
        ],
        currency="USD",
        minimum_order_quantity=1,
        packaging="tape-reel",
        lead_time="in stock",
        datasheet_url="https://mock.example.com/datasheets/GRM155R71C104KA88.pdf",
        lifecycle="active",
        last_checked=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source="mock",
        category="capacitor",
        package="0402",
    ),
}

# Mock LCSC part numbers for JLC compatibility testing
_MOCK_LCSC: dict[str, str] = {
    "GRM21BB31C106KE15": "C15850",
    "GRM155R71C104KA88": "C307331",
}


class MockSupplierProvider(SupplierProvider):
    """Mock supplier that returns canned data. No network calls."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def is_live_implemented(self) -> bool:
        return False

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            lookup=True,
            search=True,
            stock_price=True,
            datasheet=True,
            lifecycle=True,
        )

    def lookup_mpn(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[SupplierPart]:
        mpn_upper = mpn.strip().upper()
        part = _MOCK_PARTS.get(mpn_upper)
        if part:
            # Update timestamp
            part.last_checked = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return part

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        **filters: Any,
    ) -> list[SupplierPart]:
        q = query.upper()
        results = []
        for mpn, part in _MOCK_PARTS.items():
            if q in mpn or q in (part.description or "").upper():
                results.append(part)
        return results

    def refresh_stock(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> list[SupplierOffer]:
        part = self.lookup_mpn(mpn, manufacturer=manufacturer)
        if not part:
            return []
        return [
            SupplierOffer(
                supplier="mock",
                supplier_pn=part.supplier_pn,
                stock=part.stock,
                price_breaks=part.price_breaks,
                currency=part.currency,
                minimum_order_quantity=part.minimum_order_quantity,
                packaging=part.packaging,
                lead_time=part.lead_time,
                last_checked=part.last_checked,
                source="mock",
            )
        ]

    def get_datasheet_url(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[str]:
        part = self.lookup_mpn(mpn, manufacturer=manufacturer)
        return part.datasheet_url if part else None

    def is_configured(self) -> bool:
        return True

    def lookup_lcsc(self, mpn: str) -> str | None:
        """Mock LCSC part number lookup for JLC compatibility."""
        return _MOCK_LCSC.get(mpn.strip().upper())
