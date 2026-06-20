"""Mouser live supplier provider.

Uses the Mouser Search API v2 for part lookup, keyword search,
stock/price refresh, and datasheet URLs.

Auth: API key passed in request body/URL parameter.
Base URL: https://api.mouser.com
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from footfindr.suppliers.base import ProviderCapabilities, SupplierProvider
from footfindr.suppliers.http import SupplierHTTPClient, SupplierHTTPError
from footfindr.suppliers.models import PriceBreak, SupplierOffer, SupplierPart

logger = logging.getLogger("footfindr.suppliers.mouser")

_BASE_URL = "https://api.mouser.com"
_API_VERSION = "v2"


class MouserProvider(SupplierProvider):
    """Real Mouser API provider."""

    def __init__(self, *, debug: bool = False) -> None:
        self._debug = debug
        self._creds = None
        self._client: SupplierHTTPClient | None = None

    @property
    def name(self) -> str:
        return "mouser"

    @property
    def is_live_implemented(self) -> bool:
        return True

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            lookup=True,
            search=True,
            stock_price=True,
            datasheet=True,
            lifecycle=True,
            cart=False,
            order=False,
            sandbox=False,
        )

    def is_configured(self) -> bool:
        return self._get_creds() is not None

    def auth_status(self):
        from footfindr.suppliers.auth import mouser_auth_status
        return mouser_auth_status()

    def _get_creds(self):
        if self._creds is None:
            from footfindr.suppliers.auth import MouserCredentials
            self._creds = MouserCredentials.from_env()
        return self._creds

    def _get_client(self) -> SupplierHTTPClient:
        if self._client is None:
            self._client = SupplierHTTPClient(
                provider_name="mouser",
                base_url=_BASE_URL,
                debug=self._debug,
            )
        return self._client

    def _require_creds(self):
        creds = self._get_creds()
        if not creds:
            raise SupplierHTTPError(
                "Mouser live provider is not configured.\n"
                "Set:\n  FOOTFINDR_MOUSER_PART_API_KEY\n"
                "or run:\n  ff supplier lookup <MPN> --mock",
                provider="mouser",
            )
        return creds

    def lookup_mpn(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[SupplierPart]:
        """Look up a part by MPN using Mouser Part Search API."""
        creds = self._require_creds()
        client = self._get_client()

        payload = {
            "SearchByPartRequest": {
                "mouserPartNumber": mpn,
                "partSearchOptions": "Exact",
            }
        }

        resp = client.post(
            f"/api/{_API_VERSION}/search/partnumber",
            params={"apiKey": creds.part_api_key},
            json=payload,
        )

        data = resp.json()
        parts = self._extract_parts(data)

        if not parts:
            return None

        # If manufacturer specified, filter
        if manufacturer:
            filtered = [p for p in parts if _mfr_match(p.manufacturer, manufacturer)]
            if filtered:
                return filtered[0]

        return parts[0]

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 25,
        offset: int = 0,
        **filters: Any,
    ) -> list[SupplierPart]:
        """Search parts by keyword."""
        creds = self._require_creds()
        client = self._get_client()

        payload = {
            "SearchByKeywordRequest": {
                "keyword": query,
                "records": min(limit, 50),
                "startingRecord": offset,
                "searchOptions": "",
                "searchWithYourSignUpLanguage": "",
            }
        }

        resp = client.post(
            f"/api/{_API_VERSION}/search/keyword",
            params={"apiKey": creds.part_api_key},
            json=payload,
        )

        data = resp.json()
        return self._extract_parts(data)

    def refresh_stock(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> list[SupplierOffer]:
        """Refresh stock/price via lookup."""
        part = self.lookup_mpn(mpn, manufacturer=manufacturer)
        if not part:
            return []
        return [SupplierOffer(
            supplier="mouser",
            supplier_pn=part.supplier_pn,
            stock=part.stock,
            price_breaks=part.price_breaks,
            currency=part.currency,
            minimum_order_quantity=part.minimum_order_quantity,
            packaging=part.packaging,
            lead_time=part.lead_time,
            last_checked=part.last_checked,
            source="live",
        )]

    def get_datasheet_url(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[str]:
        """Get datasheet URL."""
        part = self.lookup_mpn(mpn, manufacturer=manufacturer)
        return part.datasheet_url if part else None

    def _extract_parts(self, data: dict) -> list[SupplierPart]:
        """Parse Mouser API response into SupplierPart list."""
        parts = []
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Navigate response structure
        search_results = data.get("SearchResults", {})
        part_list = search_results.get("Parts", [])

        for p in part_list:
            # Parse price breaks
            price_breaks = []
            for pb in p.get("PriceBreaks", []):
                try:
                    qty = int(pb.get("Quantity", 0))
                    # Price is a string like "$0.10" or "0.10"
                    price_str = str(pb.get("Price", "0")).replace("$", "").replace(",", "").strip()
                    price = float(price_str) if price_str else 0.0
                    currency = pb.get("Currency", "USD")
                    price_breaks.append(PriceBreak(
                        quantity=qty,
                        unit_price=price,
                        currency=currency,
                    ))
                except (ValueError, TypeError):
                    continue

            # Parse stock
            stock = None
            avail_str = str(p.get("Availability", ""))
            # Mouser returns "In Stock" or "xxx In Stock" or just a number
            if avail_str:
                import re
                nums = re.findall(r"[\d,]+", avail_str.replace(",", ""))
                if nums:
                    try:
                        stock = int(nums[0].replace(",", ""))
                    except ValueError:
                        pass

            # MOQ
            moq = None
            moq_str = p.get("Min", "")
            if moq_str:
                try:
                    moq = int(str(moq_str).replace(",", ""))
                except (ValueError, TypeError):
                    pass

            # Lifecycle
            lifecycle = p.get("LifecycleStatus", None)

            parts.append(SupplierPart(
                supplier="mouser",
                supplier_pn=p.get("MouserPartNumber", ""),
                supplier_url=p.get("ProductDetailUrl", None),
                mpn=p.get("ManufacturerPartNumber", ""),
                manufacturer=p.get("Manufacturer", None),
                description=p.get("Description", None),
                stock=stock,
                price_breaks=price_breaks,
                currency="USD",
                minimum_order_quantity=moq,
                packaging=p.get("Packaging", None),
                lead_time=p.get("LeadTime", None),
                datasheet_url=p.get("DataSheetUrl", None),
                lifecycle=lifecycle,
                last_checked=now,
                source="live",
                category=p.get("Category", None),
                product_url=p.get("ProductDetailUrl", None),
            ))

        return parts


def _mfr_match(a: str | None, b: str | None) -> bool:
    """Case-insensitive manufacturer comparison."""
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()
