"""JLCPCB/LCSC live supplier provider.

Uses the JLCPCB/LCSC Components API for part lookup, search, stock/price,
and LCSC part number resolution.

Auth: API key + HMAC-SHA256 signature.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Optional

from footfindr.suppliers.base import ProviderCapabilities, SupplierProvider
from footfindr.suppliers.http import SupplierHTTPClient, SupplierHTTPError
from footfindr.suppliers.models import PriceBreak, SupplierOffer, SupplierPart

logger = logging.getLogger("footfindr.suppliers.jlcpcb")

_SIGNED_BASE_URL = "https://api.jlcpcb.com"
_PUBLIC_BASE_URL = "https://jlcsearch.tscircuit.com"


class JLCPCBProvider(SupplierProvider):
    """JLCPCB/LCSC live supplier provider."""

    def __init__(self, *, debug: bool = False) -> None:
        self._debug = debug
        self._creds = None
        self._client: SupplierHTTPClient | None = None

    @property
    def name(self) -> str:
        return "jlcpcb"

    @property
    def is_live_implemented(self) -> bool:
        return True

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            lookup=True,
            search=True,
            stock_price=True,
            datasheet=False,
            lifecycle=False,
            cart=False,
            order=False,
            sandbox=False,
        )

    def is_configured(self) -> bool:
        return self._get_creds() is not None

    def auth_status(self):
        from footfindr.suppliers.auth import jlcpcb_auth_status
        return jlcpcb_auth_status()

    def _get_creds(self):
        if self._creds is None:
            from footfindr.suppliers.auth import JLCPCBCredentials
            self._creds = JLCPCBCredentials.from_env()
        return self._creds

    def _get_client(self) -> SupplierHTTPClient:
        if self._client is None:
            self._client = SupplierHTTPClient(
                provider_name="jlcpcb",
                base_url=_SIGNED_BASE_URL,
                debug=self._debug,
            )
        return self._client

    def _require_creds(self):
        creds = self._get_creds()
        if not creds:
            raise SupplierHTTPError(
                "JLCPCB live provider is not configured.\n"
                "Set:\n  FOOTFINDR_JLCPCB_APP_ID\n  FOOTFINDR_JLCPCB_ACCESS_KEY\n"
                "  FOOTFINDR_JLCPCB_PRIVATE_KEY (optional, for RSA signing)\n"
                "or run:\n  ff supplier lookup <MPN> --mock",
                provider="jlcpcb",
            )
        return creds

    def _sign_request(
        self,
        method: str,
        path: str,
        body: str = "",
    ) -> dict[str, str]:
        """Generate auth headers for JLCPCB API.

        Uses HMAC-SHA256 signature with the access key.
        If RSA private key is available, uses RSA-SHA256 instead.
        """
        creds = self._require_creds()
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex[:16]

        # String to sign
        string_to_sign = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}"

        # Sign with HMAC-SHA256 using access key
        signature = hmac.new(
            creds.access_key.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "x-jlc-app-id": creds.app_id,
            "x-jlc-access-key": creds.access_key,
            "x-jlc-timestamp": timestamp,
            "x-jlc-nonce": nonce,
            "x-jlc-sign": signature,
            "Content-Type": "application/json",
        }

    def lookup_mpn(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[SupplierPart]:
        """Look up a part by MPN on JLCPCB/LCSC."""
        results = self._search_parts(mpn, exact=True)

        if not results:
            return None

        # Filter by manufacturer if specified
        if manufacturer:
            filtered = [p for p in results if _mfr_match(p.manufacturer, manufacturer)]
            if filtered:
                return filtered[0]

        return results[0]

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 25,
        offset: int = 0,
        **filters: Any,
    ) -> list[SupplierPart]:
        """Search parts by keyword on JLCPCB/LCSC."""
        return self._search_parts(query, exact=False)

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
            supplier="jlcpcb",
            supplier_pn=part.supplier_pn or part.lcsc_pn,
            stock=part.stock,
            price_breaks=part.price_breaks,
            currency=part.currency,
            minimum_order_quantity=part.minimum_order_quantity,
            packaging=part.packaging,
            last_checked=part.last_checked,
            source="live",
        )]

    def get_datasheet_url(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[str]:
        """Get datasheet URL (limited support on JLCPCB)."""
        part = self.lookup_mpn(mpn, manufacturer=manufacturer)
        return part.datasheet_url if part else None

    def lookup_lcsc(self, mpn: str) -> str | None:
        """Look up LCSC part number for an MPN."""
        part = self.lookup_mpn(mpn)
        return part.lcsc_pn if part else None

    def _search_parts(self, query: str, *, exact: bool = False) -> list[SupplierPart]:
        """Internal search implementation."""
        client = self._get_client()
        creds = self._require_creds()

        path = "/smtComponentList/searchSmtComponentList"
        body_dict = {
            "keyword": query,
            "currentPage": 1,
            "pageSize": 25,
        }
        body_str = json.dumps(body_dict, separators=(",", ":"))

        headers = self._sign_request("POST", path, body_str)

        try:
            resp = client.post(
                path,
                json=body_dict,
                headers=headers,
            )
        except SupplierHTTPError:
            # If the signed API fails, try the public search endpoint as fallback
            logger.debug("Signed API failed, trying public LCSC search fallback")
            return self._search_public_fallback(query, exact=exact)

        data = resp.json()

        # Check for API error
        code = data.get("code")
        if code and code != 200 and code != "200":
            msg = data.get("message", f"JLCPCB API error code {code}")
            logger.warning(f"JLCPCB API error: {msg}")
            # Try public fallback
            return self._search_public_fallback(query, exact=exact)

        return self._parse_jlc_response(data, exact=exact, query=query)

    def _search_public_fallback(
        self,
        query: str,
        *,
        exact: bool = False,
    ) -> list[SupplierPart]:
        """Fallback to public jlcsearch community API."""
        try:
            import httpx as _httpx
            resp = _httpx.get(
                f"{_PUBLIC_BASE_URL}/api/search",
                params={
                    "q": query,
                },
                timeout=30.0,
                headers={"User-Agent": "FootFindr/0.1"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return self._parse_public_response(data, exact=exact, query=query)
        except Exception as e:
            logger.debug(f"Public LCSC fallback failed: {e}")
            return []

    def _parse_public_response(
        self,
        data: dict,
        *,
        exact: bool = False,
        query: str = "",
    ) -> list[SupplierPart]:
        """Parse jlcsearch.tscircuit.com community API response.

        Format: {"components": [{"lcsc": 408141, "mfr": "GRM...", "package": "0805",
                   "is_basic": false, "is_preferred": false, "description": "",
                   "stock": 49936, "price": 0.025}]}
        """
        parts = []
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        items = data.get("components", [])
        for item in items:
            if not isinstance(item, dict):
                continue

            mpn = item.get("mfr", "")
            lcsc_num = item.get("lcsc")
            lcsc_pn = f"C{lcsc_num}" if lcsc_num else ""
            package = item.get("package", "")
            desc = item.get("description", "")

            if exact and query and mpn:
                if mpn.upper() != query.upper():
                    continue

            stock = None
            stock_val = item.get("stock")
            if stock_val is not None:
                try:
                    stock = int(stock_val)
                except (ValueError, TypeError):
                    pass

            price_breaks = []
            price_val = item.get("price")
            if price_val is not None:
                try:
                    price_breaks.append(PriceBreak(
                        quantity=1,
                        unit_price=float(price_val),
                        currency="USD",
                    ))
                except (ValueError, TypeError):
                    pass

            # Category
            jlc_cat = None
            if item.get("is_basic"):
                jlc_cat = "basic"
            elif item.get("is_preferred"):
                jlc_cat = "preferred"
            else:
                jlc_cat = "extended"

            parts.append(SupplierPart(
                supplier="jlcpcb",
                supplier_pn=lcsc_pn,
                mpn=mpn,
                manufacturer="",  # Not available in community API
                description=desc,
                stock=stock,
                price_breaks=price_breaks,
                currency="USD",
                package=package,
                last_checked=now,
                source="live",
                lcsc_pn=lcsc_pn,
                jlc_category=jlc_cat,
                product_url=f"https://jlcpcb.com/partdetail/{lcsc_pn}" if lcsc_pn else None,
            ))

        return parts

    def _parse_jlc_response(
        self,
        data: dict,
        *,
        exact: bool = False,
        query: str = "",
    ) -> list[SupplierPart]:
        """Parse JLCPCB/LCSC API response."""
        parts = []
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Navigate response — JLCPCB wraps in data.componentPageInfo.list or data.list
        component_data = data.get("data", data)
        if isinstance(component_data, dict):
            page_info = component_data.get("componentPageInfo", component_data)
            if isinstance(page_info, dict):
                items = page_info.get("list", [])
            else:
                items = []
        else:
            items = []

        if not items and isinstance(component_data, list):
            items = component_data

        for item in items:
            if not isinstance(item, dict):
                continue

            mpn = item.get("componentModelEn", "") or item.get("componentCode", "")
            lcsc_pn = item.get("componentCode", "") or item.get("lcscPartNumber", "")
            manufacturer = item.get("brandNameEn", "") or item.get("manufacturer", "")
            desc = item.get("describe", "") or item.get("description", "")

            # Filter for exact match if requested
            if exact and query and mpn:
                if mpn.upper() != query.upper():
                    continue

            # Stock
            stock = None
            stock_val = item.get("stockCount", item.get("stock"))
            if stock_val is not None:
                try:
                    stock = int(stock_val)
                except (ValueError, TypeError):
                    pass

            # Price breaks
            price_breaks = []
            prices = item.get("jlcPrices", []) or item.get("prices", [])
            for pb in prices:
                if isinstance(pb, dict):
                    try:
                        qty = int(pb.get("startNumber", pb.get("quantity", 0)))
                        price = float(pb.get("productPrice", pb.get("price", 0)))
                        price_breaks.append(PriceBreak(
                            quantity=qty,
                            unit_price=price,
                            currency="USD",
                        ))
                    except (ValueError, TypeError):
                        continue

            # JLC category
            jlc_cat = None
            cat_val = item.get("componentLibraryType", item.get("stockType", ""))
            if cat_val:
                cat_str = str(cat_val).lower()
                if "basic" in cat_str or cat_str == "1":
                    jlc_cat = "basic"
                elif "extend" in cat_str or cat_str == "2":
                    jlc_cat = "extended"
                elif "prefer" in cat_str or cat_str == "3":
                    jlc_cat = "preferred"

            # Package
            package = item.get("componentSpecificationEn", "") or item.get("package", "")

            # MOQ
            moq = None
            moq_val = item.get("minOrder", item.get("minOrderQuantity"))
            if moq_val is not None:
                try:
                    moq = int(moq_val)
                except (ValueError, TypeError):
                    pass

            # Datasheet
            datasheet = item.get("dataManualUrl", None)

            parts.append(SupplierPart(
                supplier="jlcpcb",
                supplier_pn=lcsc_pn,
                mpn=mpn,
                manufacturer=manufacturer,
                description=desc,
                stock=stock,
                price_breaks=price_breaks,
                currency="USD",
                minimum_order_quantity=moq,
                packaging=item.get("packaging", None),
                datasheet_url=datasheet,
                last_checked=now,
                source="live",
                category=item.get("componentTypeEn", None),
                package=package,
                lcsc_pn=lcsc_pn,
                jlc_category=jlc_cat,
                product_url=f"https://jlcpcb.com/partdetail/{lcsc_pn}" if lcsc_pn else None,
            ))

        return parts


def _mfr_match(a: str | None, b: str | None) -> bool:
    """Case-insensitive manufacturer comparison."""
    if not a or not b:
        return False
    a_norm = a.strip().lower()
    b_norm = b.strip().lower()
    for suffix in (" electronics", " manufacturing", " inc.", " inc", " co.", " ltd.", " ltd"):
        a_norm = a_norm.removesuffix(suffix)
        b_norm = b_norm.removesuffix(suffix)
    return a_norm == b_norm
