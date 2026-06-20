"""Nexar/Octopart live supplier provider.

Uses the Nexar GraphQL API for multi-seller part lookup, pricing,
stock, lifecycle, and datasheet data.

Auth: OAuth2 client_credentials via https://identity.nexar.com/connect/token
GraphQL endpoint: https://api.nexar.com/graphql
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from footfindr.suppliers.base import ProviderCapabilities, SupplierProvider
from footfindr.suppliers.http import SupplierHTTPClient, SupplierHTTPError
from footfindr.suppliers.models import PriceBreak, SupplierOffer, SupplierPart

logger = logging.getLogger("footfindr.suppliers.nexar")

_TOKEN_URL = "https://identity.nexar.com/connect/token"
_GRAPHQL_URL = "https://api.nexar.com/graphql"

# GraphQL query for MPN search with offers
_SEARCH_QUERY = """
query SearchMPN($mpn: String!, $limit: Int!) {
  supSearchMpn(q: $mpn, country: "US", currency: "USD", limit: $limit) {
    results {
      part {
        mpn
        name
        shortDescription
        manufacturer {
          name
        }
        bestDatasheet {
          url
        }
        sellers(authorizedOnly: false) {
          company {
            name
          }
          offers {
            inventoryLevel
            moq
            packaging
            prices {
              quantity
              price
              currency
            }
          }
        }
      }
    }
  }
}
"""


class NexarProvider(SupplierProvider):
    """Real Nexar/Octopart API provider."""

    def __init__(self, *, debug: bool = False) -> None:
        self._debug = debug
        self._creds = None
        self._token_mgr = None
        self._client: SupplierHTTPClient | None = None

    @property
    def name(self) -> str:
        return "nexar"

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
        from footfindr.suppliers.auth import nexar_auth_status
        return nexar_auth_status()

    def _get_creds(self):
        if self._creds is None:
            from footfindr.suppliers.auth import NexarCredentials
            self._creds = NexarCredentials.from_env()
        return self._creds

    def _get_token_mgr(self):
        if self._token_mgr is None:
            creds = self._require_creds()
            from footfindr.suppliers.auth import OAuthTokenManager
            self._token_mgr = OAuthTokenManager(
                provider_name="nexar",
                token_url=_TOKEN_URL,
                client_id=creds.client_id,
                client_secret=creds.client_secret,
                scope="supply.domain",
            )
        return self._token_mgr

    def _get_client(self) -> SupplierHTTPClient:
        if self._client is None:
            self._client = SupplierHTTPClient(
                provider_name="nexar",
                debug=self._debug,
            )
        return self._client

    def _require_creds(self):
        creds = self._get_creds()
        if not creds:
            raise SupplierHTTPError(
                "Nexar live provider is not configured.\n"
                "Set:\n  FOOTFINDR_NEXAR_CLIENT_ID\n  FOOTFINDR_NEXAR_CLIENT_SECRET\n"
                "or run:\n  ff supplier lookup <MPN> --mock",
                provider="nexar",
            )
        return creds

    def _graphql(self, query: str, variables: dict) -> dict:
        """Execute a GraphQL query against the Nexar API."""
        token_mgr = self._get_token_mgr()
        client = self._get_client()

        def _on_401():
            token_mgr.invalidate()

        resp = client.post(
            _GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token_mgr.get_token()}"},
            retry_on_401=True,
            on_401=_on_401,
        )

        data = resp.json()
        if "errors" in data:
            errors = data["errors"]
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            raise SupplierHTTPError(
                f"Nexar GraphQL error: {msg}",
                provider="nexar",
                endpoint="graphql",
            )
        return data.get("data", {})

    def lookup_mpn(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[SupplierPart]:
        """Look up a part by MPN using Nexar GraphQL."""
        data = self._graphql(_SEARCH_QUERY, {"mpn": mpn, "limit": 5})

        results = data.get("supSearchMpn", {}).get("results", [])
        if not results:
            return None

        # Find best match
        for result in results:
            part_data = result.get("part", {})
            part_mpn = part_data.get("mpn", "")

            # Filter by manufacturer if specified
            part_mfr = part_data.get("manufacturer", {}).get("name", "")
            if manufacturer and not _mfr_match(part_mfr, manufacturer):
                continue

            return self._parse_nexar_part(part_data)

        # If no manufacturer match, return first result
        if results:
            return self._parse_nexar_part(results[0].get("part", {}))

        return None

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        **filters: Any,
    ) -> list[SupplierPart]:
        """Search parts using Nexar GraphQL."""
        data = self._graphql(_SEARCH_QUERY, {"mpn": query, "limit": 10})

        results = data.get("supSearchMpn", {}).get("results", [])
        parts = []
        for result in results:
            part_data = result.get("part", {})
            part = self._parse_nexar_part(part_data)
            if part:
                parts.append(part)
        return parts

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
            supplier="nexar",
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

    def _parse_nexar_part(self, part_data: dict) -> SupplierPart | None:
        """Parse a Nexar GraphQL part result into SupplierPart.

        Aggregates offers from all sellers into best stock/price.
        """
        if not part_data:
            return None

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        mpn = part_data.get("mpn", "")
        mfr = part_data.get("manufacturer", {}).get("name", "")
        desc = part_data.get("shortDescription") or part_data.get("name", "")

        datasheet_url = None
        best_ds = part_data.get("bestDatasheet")
        if best_ds:
            datasheet_url = best_ds.get("url")

        # Aggregate best stock and price across all sellers
        total_stock = 0
        best_price_breaks: list[PriceBreak] = []
        best_moq: int | None = None
        best_packaging: str | None = None
        seller_names: list[str] = []

        for seller in part_data.get("sellers", []):
            seller_name = seller.get("company", {}).get("name", "")
            if seller_name:
                seller_names.append(seller_name)

            for offer in seller.get("offers", []):
                inv = offer.get("inventoryLevel")
                if inv is not None and isinstance(inv, (int, float)):
                    total_stock += int(inv)

                moq = offer.get("moq")
                if moq is not None:
                    if best_moq is None or moq < best_moq:
                        best_moq = moq

                pkg = offer.get("packaging")
                if pkg and not best_packaging:
                    best_packaging = pkg

                for price_tier in offer.get("prices", []):
                    try:
                        qty = int(price_tier.get("quantity", 0))
                        price = float(price_tier.get("price", 0))
                        currency = price_tier.get("currency", "USD")
                        best_price_breaks.append(PriceBreak(
                            quantity=qty,
                            unit_price=price,
                            currency=currency,
                        ))
                    except (ValueError, TypeError):
                        continue

        # Deduplicate and sort price breaks by quantity
        if best_price_breaks:
            seen = set()
            unique_pbs = []
            for pb in sorted(best_price_breaks, key=lambda x: x.quantity):
                key = pb.quantity
                if key not in seen:
                    seen.add(key)
                    unique_pbs.append(pb)
            best_price_breaks = unique_pbs[:10]  # keep top 10

        # Seller summary for description
        if seller_names and desc:
            top_sellers = ", ".join(seller_names[:3])
            desc = f"{desc} (via {top_sellers}{'...' if len(seller_names) > 3 else ''})"

        return SupplierPart(
            supplier="nexar",
            mpn=mpn,
            manufacturer=mfr,
            description=desc,
            stock=total_stock if total_stock > 0 else None,
            price_breaks=best_price_breaks,
            currency="USD",
            minimum_order_quantity=best_moq,
            packaging=best_packaging,
            datasheet_url=datasheet_url,
            last_checked=now,
            source="live",
        )


def _mfr_match(a: str | None, b: str | None) -> bool:
    """Case-insensitive manufacturer comparison."""
    if not a or not b:
        return False
    # Normalize common suffixes
    a_norm = a.strip().lower()
    b_norm = b.strip().lower()
    for suffix in (" electronics", " manufacturing", " inc.", " inc", " co.", " ltd.", " ltd"):
        a_norm = a_norm.removesuffix(suffix)
        b_norm = b_norm.removesuffix(suffix)
    return a_norm == b_norm
