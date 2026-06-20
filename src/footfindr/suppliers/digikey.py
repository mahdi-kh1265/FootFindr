"""DigiKey live supplier provider.

Uses the DigiKey Product Information API V4 for part lookup, keyword
search, pricing, and datasheet URLs.

Auth strategy (priority order):
  1. Two-legged OAuth (client_credentials) — no browser needed.
     Works for sandbox-ProductInformation V4 and production apps
     configured for 2-legged access.
  2. Three-legged OAuth (Authorization Code) — browser login fallback.
     Required for user-specific endpoints (MyLists, Quote, Ordering).
     Triggered via: ff supplier auth login digikey

Sandbox: https://sandbox-api.digikey.com
Production: https://api.digikey.com
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from footfindr.suppliers.base import ProviderCapabilities, SupplierProvider
from footfindr.suppliers.http import SupplierHTTPClient, SupplierHTTPError
from footfindr.suppliers.models import PriceBreak, SupplierOffer, SupplierPart

logger = logging.getLogger("footfindr.suppliers.digikey")

_PRODUCTION_BASE = "https://api.digikey.com"
_SANDBOX_BASE = "https://sandbox-api.digikey.com"
_TOKEN_PATH = "/v1/oauth2/token"


class DigiKeyProvider(SupplierProvider):
    """Real DigiKey API provider.

    Tries two-legged OAuth (client_credentials) first for product
    lookup/search. Falls back to three-legged if a cached refresh
    token is available from ``ff supplier auth login digikey``.
    """

    def __init__(self, *, debug: bool = False) -> None:
        self._debug = debug
        self._creds = None
        self._token_mgr = None
        self._client: SupplierHTTPClient | None = None
        self._auth_mode: str | None = None  # "2-legged" or "3-legged"

    @property
    def name(self) -> str:
        return "digikey"

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
            sandbox=True,
        )

    def is_configured(self) -> bool:
        return self._get_creds() is not None

    def auth_status(self):
        from footfindr.suppliers.auth import digikey_auth_status
        return digikey_auth_status()

    def _get_creds(self):
        if self._creds is None:
            from footfindr.suppliers.auth import DigiKeyCredentials
            self._creds = DigiKeyCredentials.from_env()
        return self._creds

    def _get_base_url(self) -> str:
        creds = self._get_creds()
        if creds and creds.sandbox:
            return _SANDBOX_BASE
        return _PRODUCTION_BASE

    def _get_token_mgr(self):
        """Get or create a token manager.

        Priority:
          1. Three-legged (Authorization Code) if a cached refresh token
             exists from a prior ``ff supplier auth login digikey``.
          2. Two-legged (client_credentials) as fallback — no browser needed.
        """
        if self._token_mgr is not None:
            return self._token_mgr

        creds = self._require_creds()

        # Strategy 1: Check for cached 3-legged token (from prior browser login)
        from footfindr.suppliers.auth import DigiKeyOAuthManager
        if DigiKeyOAuthManager.has_cached_tokens():
            mgr = DigiKeyOAuthManager(
                client_id=creds.client_id,
                client_secret=creds.client_secret,
                callback_url=creds.callback_url or "https://localhost:8765/digikey/oauth/callback",
                sandbox=creds.sandbox,
            )
            if mgr._refresh_token_val or (mgr._access_token and mgr._expires_at > __import__('time').time() + 60):
                self._token_mgr = mgr
                self._auth_mode = "3-legged"
                logger.info("[digikey] Using cached 3-legged OAuth token")
                return self._token_mgr

        # Strategy 2: Fall back to two-legged OAuth (client_credentials)
        from footfindr.suppliers.auth import OAuthTokenManager
        base = self._get_base_url()
        self._token_mgr = OAuthTokenManager(
            provider_name="digikey",
            token_url=f"{base}{_TOKEN_PATH}",
            client_id=creds.client_id,
            client_secret=creds.client_secret,
        )
        self._auth_mode = "2-legged"
        return self._token_mgr

    def _fallback_to_3_legged(self) -> bool:
        """Try to switch to three-legged OAuth using a cached refresh token.

        Returns True if a cached 3-legged token was found. Does NOT
        launch a browser -- only uses existing cached tokens.
        """
        creds = self._require_creds()
        from footfindr.suppliers.auth import DigiKeyOAuthManager

        mgr = DigiKeyOAuthManager(
            client_id=creds.client_id,
            client_secret=creds.client_secret,
            callback_url=creds.callback_url or "https://localhost:8765/digikey/oauth/callback",
            sandbox=creds.sandbox,
        )
        # Only use if we already have a cached refresh token
        if mgr._refresh_token_val:
            self._token_mgr = mgr
            self._auth_mode = "3-legged"
            logger.info("[digikey] Switched to 3-legged OAuth (cached refresh token)")
            return True
        return False

    def _get_client(self) -> SupplierHTTPClient:
        if self._client is None:
            creds = self._require_creds()
            base_url = self._get_base_url()
            self._client = SupplierHTTPClient(
                provider_name="digikey",
                base_url=base_url,
                debug=self._debug,
                headers={
                    "X-DIGIKEY-Client-Id": creds.client_id,
                    "X-DIGIKEY-Locale-Language": "en",
                    "X-DIGIKEY-Locale-Currency": "USD",
                    "X-DIGIKEY-Locale-Site": "US",
                },
            )
        return self._client

    def _require_creds(self):
        creds = self._get_creds()
        if not creds:
            raise SupplierHTTPError(
                "DigiKey live provider is not configured.\n"
                "Set:\n  FOOTFINDR_DIGIKEY_CLIENT_ID\n  FOOTFINDR_DIGIKEY_CLIENT_SECRET\n"
                "  FOOTFINDR_DIGIKEY_SANDBOX=1 (for sandbox)\n"
                "or run:\n  ff supplier lookup <MPN> --mock",
                provider="digikey",
            )
        return creds

    def _auth_headers(self) -> dict[str, str]:
        token_mgr = self._get_token_mgr()
        try:
            token = token_mgr.get_token()
        except RuntimeError as e:
            # Two-legged failed — try 3-legged fallback
            if self._auth_mode == "2-legged" and self._fallback_to_3_legged():
                token = self._token_mgr.get_token()
            else:
                raise SupplierHTTPError(
                    f"DigiKey OAuth token request failed ({e}).\n"
                    "Two-legged (client_credentials) did not work.\n"
                    "If this endpoint requires user authorization, run:\n"
                    "  ff supplier auth login digikey\n"
                    "to authorize via browser (three-legged OAuth).",
                    provider="digikey",
                ) from e
        return {"Authorization": f"Bearer {token}"}

    def _handle_403(self, e: SupplierHTTPError) -> None:
        """Handle 403 Forbidden by trying 3-legged fallback or raising clear message."""
        if self._auth_mode == "2-legged":
            if self._fallback_to_3_legged():
                logger.info("[digikey] 403 with 2-legged; retrying with 3-legged OAuth")
                return  # caller should retry
            raise SupplierHTTPError(
                "DigiKey 403 Forbidden: two-legged OAuth (client_credentials) is not "
                "authorized for this API.\n"
                "This sandbox/production app may require three-legged OAuth.\n"
                "Run:\n  ff supplier auth login digikey\n"
                "to authorize via browser, then retry.",
                provider="digikey",
                status_code=403,
            ) from e
        raise  # already on 3-legged, nothing more to try

    def lookup_mpn(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
        debug: bool = False,
    ) -> Optional[SupplierPart]:
        """Look up a part by DigiKey part number or MPN."""
        client = self._get_client()

        endpoint = f"/products/v4/search/{mpn}/productdetails"
        if debug:
            print(f"[debug] digikey auth_mode: {self._auth_mode or 'default'}")
            print(f"[debug] digikey endpoint: {endpoint}")

        def _on_401():
            self._get_token_mgr().invalidate()

        # Try product details endpoint
        try:
            resp = client.get(
                endpoint,
                headers=self._auth_headers(),
                retry_on_401=True,
                on_401=_on_401,
            )
        except SupplierHTTPError as e:
            if debug:
                print(f"[debug] digikey HTTP error: {e.status_code}")
            if e.status_code == 403:
                self._handle_403(e)
                self._client = None
                return self.lookup_mpn(mpn, manufacturer=manufacturer, debug=debug)
            if e.status_code == 404:
                if debug:
                    print("[debug] digikey 404: falling back to keyword search")
                results = self.search(mpn)
                if manufacturer and results:
                    filtered = [r for r in results if _mfr_match(r.manufacturer, manufacturer)]
                    return filtered[0] if filtered else results[0]
                return results[0] if results else None
            raise

        data = resp.json()

        if debug:
            print(f"[debug] digikey HTTP status: {resp.status_code}")
            print(f"[debug] digikey response top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")

        result = self._parse_product_details(data, debug=debug)

        if result and not result.is_valid():
            if debug:
                print("[debug] digikey parsed result is empty/invalid — discarding")
            is_sandbox = bool(self._creds and self._creds.sandbox)
            print(
                f"DigiKey {'sandbox ' if is_sandbox else ''}returned no usable product details for {mpn}.\n"
                "No cache entry written."
            )
            return None

        if debug and result:
            print(f"[debug] digikey parsed: mpn={result.mpn!r}, supplier_pn={result.supplier_pn!r}, "
                  f"mfr={result.manufacturer!r}, stock={result.stock}, "
                  f"prices={len(result.price_breaks)}")

        return result

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 25,
        offset: int = 0,
        **filters: Any,
    ) -> "SupplierSearchPage":
        """Search parts using DigiKey keyword search.

        Returns a SupplierSearchPage with pagination metadata.
        """
        from footfindr.suppliers.base import SupplierSearchPage

        client = self._get_client()

        def _on_401():
            self._get_token_mgr().invalidate()

        payload = {
            "Keywords": query,
            "RecordCount": min(limit, 50),
            "RecordStartPosition": offset,
            "ExcludeMarketPlaceProducts": True,
        }

        try:
            resp = client.post(
                "/products/v4/search/keyword",
                json=payload,
                headers=self._auth_headers(),
                retry_on_401=True,
                on_401=_on_401,
            )
        except SupplierHTTPError as e:
            if e.status_code == 403:
                self._handle_403(e)
                # Retry with 3-legged token
                self._client = None
                return self.search(query, category=category, limit=limit, offset=offset, **filters)
            raise

        data = resp.json()
        items = self._parse_search_results(data)

        # Extract pagination metadata from DigiKey response
        total_available = (
            data.get("ProductsCount")
            or data.get("ResultCount")
            or data.get("ExactManufacturerProductsCount")
        )
        if isinstance(total_available, str):
            try:
                total_available = int(total_available)
            except ValueError:
                total_available = None

        # Determine has_more
        has_more = len(items) == min(limit, 50)
        if total_available is not None:
            has_more = (offset + len(items)) < total_available

        return SupplierSearchPage(
            items=items,
            supplier="digikey",
            query=query,
            limit=limit,
            offset=offset,
            total_available=total_available,
            has_more=has_more,
        )

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
            supplier="digikey",
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

    def _parse_product_details(
        self,
        data: dict,
        *,
        debug: bool = False,
    ) -> SupplierPart | None:
        """Parse DigiKey product details response.

        Supports both V4 production and sandbox response shapes:
          - V4 production: ManufacturerProductNumber, Description.ProductDescription,
            ProductVariations[].DigiKeyProductNumber, ProductVariations[].StandardPricing,
            DatasheetUrl
          - Legacy/sandbox: ManufacturerPartNumber, ProductDescription,
            DigiKeyPartNumber, StandardPricing, PrimaryDatasheet
        """
        if not data:
            return None

        # DigiKey V4 wraps product in a "Product" key
        product = data
        if "Product" in data and isinstance(data["Product"], dict):
            product = data["Product"]
        elif "Product" in data and data["Product"] is None:
            if debug:
                print("[debug] digikey response has Product: null")
            return None

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # --- MPN (V4: ManufacturerProductNumber, legacy: ManufacturerPartNumber) ---
        mpn = (
            product.get("ManufacturerProductNumber")
            or product.get("ManufacturerPartNumber")
            or ""
        )

        # --- Manufacturer ---
        mfr_raw = product.get("Manufacturer", {})
        if isinstance(mfr_raw, dict):
            manufacturer = mfr_raw.get("Name", "") or mfr_raw.get("Value", "")
        else:
            manufacturer = str(mfr_raw) if mfr_raw else ""

        # --- Description (V4: nested in Description dict, legacy: flat) ---
        desc_raw = product.get("Description")
        if isinstance(desc_raw, dict):
            description = desc_raw.get("ProductDescription", "")
        else:
            description = product.get("ProductDescription") or (desc_raw if isinstance(desc_raw, str) else None)

        # --- ProductVariations (V4: pricing/packaging/DK part number per variant) ---
        variations = product.get("ProductVariations", [])

        # DigiKey PN: first variation, or legacy flat field
        supplier_pn = ""
        if variations and isinstance(variations, list):
            supplier_pn = variations[0].get("DigiKeyProductNumber", "") or ""
        if not supplier_pn:
            supplier_pn = product.get("DigiKeyPartNumber", "") or ""

        # --- Pricing: prefer Cut Tape (CT) variant, then first variant, then flat ---
        price_breaks = []
        pricing_source = None

        if variations:
            # Find the most useful variant for pricing (Cut Tape > first)
            ct_var = None
            for v in variations:
                pkg = v.get("PackageType", {})
                pkg_name = pkg.get("Name", "") if isinstance(pkg, dict) else str(pkg)
                if "cut tape" in pkg_name.lower():
                    ct_var = v
                    break
            chosen = ct_var or variations[0]
            pricing_list = chosen.get("StandardPricing", [])
            pricing_source = "ProductVariations"
        else:
            pricing_list = product.get("StandardPricing", [])
            pricing_source = "StandardPricing (flat)"

        for pb in pricing_list:
            try:
                price_breaks.append(PriceBreak(
                    quantity=int(pb.get("BreakQuantity", 0)),
                    unit_price=float(pb.get("UnitPrice", 0)),
                    currency=pb.get("Currency", "USD"),
                ))
            except (ValueError, TypeError):
                continue

        # --- Packaging: from chosen variation or legacy ---
        packaging = None
        if variations:
            chosen_pkg = (ct_var or variations[0]).get("PackageType", {})
            if isinstance(chosen_pkg, dict):
                packaging = chosen_pkg.get("Name")
        if not packaging:
            pkg_raw = product.get("Packaging", {})
            if isinstance(pkg_raw, dict):
                packaging = pkg_raw.get("Value")
            elif pkg_raw:
                packaging = str(pkg_raw)

        # --- Stock ---
        stock = product.get("QuantityAvailable")
        if stock is not None:
            try:
                stock = int(stock)
            except (ValueError, TypeError):
                stock = None

        # --- Lifecycle ---
        lifecycle = None
        status = product.get("ProductStatus", {})
        if isinstance(status, dict):
            lifecycle = status.get("Status")
        elif isinstance(status, str):
            lifecycle = status

        # --- Datasheet (V4: DatasheetUrl, legacy: PrimaryDatasheet) ---
        datasheet_url = (
            product.get("DatasheetUrl")
            or product.get("PrimaryDatasheet")
            or None
        )

        # --- Lead time ---
        lead_time = product.get("ManufacturerLeadWeeks") or product.get("LeadTime")
        if lead_time and isinstance(lead_time, (int, float)):
            lead_time = f"{lead_time} Weeks"

        # --- Product URL ---
        product_url = product.get("ProductUrl")

        # --- Parameters (V4: rich product attributes) ---
        attributes: dict[str, str] = {}
        mounting_type = None
        temperature_range = None
        supplier_device_package = None
        package_case = None

        parameters = product.get("Parameters", [])
        for param in parameters:
            key = param.get("ParameterText", "")
            val = param.get("ValueText", "")
            if not key or not val or val == "-":
                continue
            attributes[key] = val
            # Map known parameters to normalized fields
            key_lower = key.lower()
            if key_lower == "mounting type":
                mounting_type = val
            elif key_lower == "operating temperature":
                temperature_range = val
            elif key_lower == "supplier device package":
                supplier_device_package = val
            elif key_lower in ("package / case",):
                package_case = val

        if debug:
            print("[debug] digikey JSON path mapping:")
            print(f"  ManufacturerProductNumber -> mpn={mpn!r}")
            print(f"  Manufacturer.Name -> manufacturer={manufacturer!r}")
            print(f"  ProductVariations[0].DigiKeyProductNumber -> supplier_pn={supplier_pn!r}")
            print(f"  Description.ProductDescription -> description={description!r}")
            print(f"  QuantityAvailable -> stock={stock}")
            print(f"  {pricing_source} -> price_breaks={len(price_breaks)}")
            print(f"  DatasheetUrl -> datasheet_url={datasheet_url!r}")
            print(f"  ProductUrl -> product_url={product_url!r}")
            print(f"  ProductVariations count={len(variations)}")
            print(f"  Parameters count={len(parameters)}, attributes={len(attributes)}")

        return SupplierPart(
            supplier="digikey",
            supplier_pn=supplier_pn,
            supplier_url=product_url,
            mpn=mpn,
            manufacturer=manufacturer,
            description=description,
            stock=stock,
            price_breaks=price_breaks,
            currency="USD",
            minimum_order_quantity=product.get("MinimumOrderQuantity"),
            packaging=packaging,
            lead_time=lead_time,
            datasheet_url=datasheet_url,
            lifecycle=lifecycle,
            last_checked=now,
            source="live",
            product_url=product_url,
            package=package_case,
            mounting_type=mounting_type,
            temperature_range=temperature_range,
            supplier_device_package=supplier_device_package,
            product_status=lifecycle,
            attributes=attributes,
        )

    def _parse_search_results(self, data: dict) -> list[SupplierPart]:
        """Parse DigiKey keyword search results."""
        parts = []
        products = data.get("Products", [])
        for p in products:
            part = self._parse_product_details(p)
            if part:
                parts.append(part)
        return parts


def _mfr_match(a: str | None, b: str | None) -> bool:
    """Case-insensitive manufacturer comparison."""
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()
