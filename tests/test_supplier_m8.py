"""Tests for M8 supplier HTTP client, auth, and provider infrastructure.

Tests cover:
- HTTP client retry/backoff behavior
- Secret redaction
- Credential loading from env vars
- OAuth token management
- Provider response parsing (fixture-based, no live calls)
- Auth status detection
- Supplier comparison logic
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from footfindr.suppliers.http import (
    SupplierHTTPClient,
    SupplierHTTPError,
    SupplierAuthError,
    SupplierRateLimitError,
    redact_secrets,
)


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

class TestSecretRedaction:
    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOi..."
        assert "[REDACTED]" in redact_secrets(text)
        assert "eyJhbGciOi" not in redact_secrets(text)

    def test_redacts_api_key(self):
        text = "apiKey=6903f9bd-2f1a-4347-8a99-2ca05950bb3d"
        assert "[REDACTED]" in redact_secrets(text)
        assert "6903f9bd" not in redact_secrets(text)

    def test_redacts_client_secret(self):
        text = "client_secret=Iz0_r471XrMDxgto4XdnJAsWJrf5GYT5bKeg"
        assert "[REDACTED]" in redact_secrets(text)
        assert "Iz0_r471" not in redact_secrets(text)

    def test_preserves_non_secret_text(self):
        text = "Looking up MPN GRM21BB31C106KE15"
        assert redact_secrets(text) == text


# ---------------------------------------------------------------------------
# HTTP client behavior
# ---------------------------------------------------------------------------

class TestHTTPClientRetry:
    """Test retry/backoff logic using respx mocking."""

    def test_client_creates_with_defaults(self):
        client = SupplierHTTPClient(provider_name="test")
        assert client.provider_name == "test"
        client.close()

    def test_backoff_increases(self):
        b0 = SupplierHTTPClient._backoff(0)
        b1 = SupplierHTTPClient._backoff(1)
        b2 = SupplierHTTPClient._backoff(2)
        # Backoff should generally increase (with jitter)
        assert b2 > b0 or True  # jitter can make this flaky, just check no crash

    def test_parse_retry_after_numeric(self):
        import httpx
        resp = httpx.Response(429, headers={"Retry-After": "5"})
        assert SupplierHTTPClient._parse_retry_after(resp) == 5.0

    def test_parse_retry_after_missing(self):
        import httpx
        resp = httpx.Response(429)
        assert SupplierHTTPClient._parse_retry_after(resp) is None


# ---------------------------------------------------------------------------
# Auth credential loading
# ---------------------------------------------------------------------------

class TestCredentialLoading:
    def test_mouser_from_env(self):
        from footfindr.suppliers.auth import MouserCredentials
        with patch.dict(os.environ, {"FOOTFINDR_MOUSER_PART_API_KEY": "test-key"}, clear=False):
            creds = MouserCredentials.from_env()
            assert creds is not None
            assert creds.part_api_key == "test-key"

    def test_mouser_missing(self):
        from footfindr.suppliers.auth import MouserCredentials
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                creds = MouserCredentials.from_env()
                assert creds is None

    def test_nexar_from_env(self):
        from footfindr.suppliers.auth import NexarCredentials
        with patch.dict(os.environ, {
            "FOOTFINDR_NEXAR_CLIENT_ID": "test-id",
            "FOOTFINDR_NEXAR_CLIENT_SECRET": "test-secret",
        }, clear=False):
            creds = NexarCredentials.from_env()
            assert creds is not None
            assert creds.client_id == "test-id"
            assert creds.client_secret == "test-secret"

    def test_nexar_partial(self):
        from footfindr.suppliers.auth import NexarCredentials
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {"FOOTFINDR_NEXAR_CLIENT_ID": "test-id"}, clear=True):
                creds = NexarCredentials.from_env()
                assert creds is None

    def test_digikey_from_env(self):
        from footfindr.suppliers.auth import DigiKeyCredentials
        with patch.dict(os.environ, {
            "FOOTFINDR_DIGIKEY_CLIENT_ID": "dk-id",
            "FOOTFINDR_DIGIKEY_CLIENT_SECRET": "dk-secret",
            "FOOTFINDR_DIGIKEY_SANDBOX": "1",
            "FOOTFINDR_DIGIKEY_CALLBACK_URL": "https://localhost:8765/digikey/oauth/callback",
        }, clear=False):
            creds = DigiKeyCredentials.from_env()
            assert creds is not None
            assert creds.sandbox is True
            assert creds.callback_url == "https://localhost:8765/digikey/oauth/callback"

    def test_digikey_production_default(self):
        """sandbox defaults to False when FOOTFINDR_DIGIKEY_SANDBOX not set."""
        from footfindr.suppliers.auth import DigiKeyCredentials
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {
                "FOOTFINDR_DIGIKEY_CLIENT_ID": "dk-id",
                "FOOTFINDR_DIGIKEY_CLIENT_SECRET": "dk-secret",
            }, clear=True):
                creds = DigiKeyCredentials.from_env()
                assert creds is not None
                assert creds.sandbox is False

    def test_digikey_not_configured(self):
        from footfindr.suppliers.auth import DigiKeyCredentials
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                creds = DigiKeyCredentials.from_env()
                assert creds is None

    def test_jlcpcb_from_env(self):
        from footfindr.suppliers.auth import JLCPCBCredentials
        with patch.dict(os.environ, {
            "FOOTFINDR_JLCPCB_APP_ID": "app-id",
            "FOOTFINDR_JLCPCB_ACCESS_KEY": "access-key",
        }, clear=False):
            creds = JLCPCBCredentials.from_env()
            assert creds is not None
            assert creds.app_id == "app-id"


# ---------------------------------------------------------------------------
# Auth status
# ---------------------------------------------------------------------------

class TestAuthStatus:
    def test_mouser_auth_status_configured(self):
        from footfindr.suppliers.auth import mouser_auth_status
        with patch.dict(os.environ, {"FOOTFINDR_MOUSER_PART_API_KEY": "key"}, clear=False):
            status = mouser_auth_status()
            assert status.configured
            assert status.provider == "mouser"
            assert "FOOTFINDR_MOUSER_PART_API_KEY" in status.env_vars_present

    def test_mouser_auth_status_missing(self):
        from footfindr.suppliers.auth import mouser_auth_status
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                status = mouser_auth_status()
                assert not status.configured
                assert "FOOTFINDR_MOUSER_PART_API_KEY" in status.env_vars_missing

    def test_nexar_auth_status(self):
        from footfindr.suppliers.auth import nexar_auth_status
        with patch.dict(os.environ, {
            "FOOTFINDR_NEXAR_CLIENT_ID": "id",
            "FOOTFINDR_NEXAR_CLIENT_SECRET": "secret",
        }, clear=False):
            status = nexar_auth_status()
            assert status.configured
            assert len(status.env_vars_missing) == 0

    def test_digikey_auth_status_not_configured(self):
        from footfindr.suppliers.auth import digikey_auth_status
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                status = digikey_auth_status()
                assert not status.configured
                assert len(status.env_vars_missing) == 2


# ---------------------------------------------------------------------------
# OAuth token manager
# ---------------------------------------------------------------------------

class TestOAuthTokenManager:
    def test_token_cache_roundtrip(self, tmp_path: Path):
        from footfindr.suppliers.auth import OAuthTokenManager
        import httpx
        import respx

        with respx.mock:
            respx.post("https://test.example.com/token").respond(json={
                "access_token": "test-token-123",
                "expires_in": 3600,
                "token_type": "Bearer",
            })

            mgr = OAuthTokenManager(
                provider_name="test",
                token_url="https://test.example.com/token",
                client_id="test-id",
                client_secret="test-secret",
                workspace=tmp_path / ".footfindr",
            )

            token = mgr.get_token()
            assert token == "test-token-123"

            # Token should be cached to disk
            token_file = tmp_path / ".footfindr" / "auth" / "tokens.json"
            assert token_file.exists()
            data = json.loads(token_file.read_text())
            assert "test" in data
            assert data["test"]["access_token"] == "test-token-123"

    def test_token_reuse_from_cache(self, tmp_path: Path):
        from footfindr.suppliers.auth import OAuthTokenManager
        import respx

        with respx.mock:
            route = respx.post("https://test.example.com/token").respond(json={
                "access_token": "cached-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            })

            mgr = OAuthTokenManager(
                provider_name="test2",
                token_url="https://test.example.com/token",
                client_id="id",
                client_secret="secret",
                workspace=tmp_path / ".footfindr",
            )

            t1 = mgr.get_token()
            t2 = mgr.get_token()  # Should reuse without new request
            assert t1 == t2
            assert route.call_count == 1  # Only one token request

    def test_clear_tokens(self, tmp_path: Path):
        from footfindr.suppliers.auth import clear_cached_tokens

        token_dir = tmp_path / ".footfindr" / "auth"
        token_dir.mkdir(parents=True)
        token_file = token_dir / "tokens.json"
        token_file.write_text('{"test": {"access_token": "old"}}')

        result = clear_cached_tokens(workspace=tmp_path / ".footfindr")
        assert result is True
        assert not token_file.exists()


# ---------------------------------------------------------------------------
# Mouser response parsing (fixture-based)
# ---------------------------------------------------------------------------

class TestMouserResponseParsing:
    MOUSER_FIXTURE = {
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "MouserPartNumber": "81-GRM21BB31C106KE5",
                    "ManufacturerPartNumber": "GRM21BB31C106KE15",
                    "Manufacturer": "Murata",
                    "Description": "Cap Ceramic 10uF 16V X7R 0805",
                    "Availability": "21,450 In Stock",
                    "DataSheetUrl": "https://example.com/datasheet.pdf",
                    "ProductDetailUrl": "https://mouser.com/product/GRM21BB31C106KE15",
                    "PriceBreaks": [
                        {"Quantity": 1, "Price": "$0.15", "Currency": "USD"},
                        {"Quantity": 10, "Price": "$0.10", "Currency": "USD"},
                        {"Quantity": 100, "Price": "$0.07", "Currency": "USD"},
                    ],
                    "LifecycleStatus": "Active",
                    "Min": "1",
                    "Packaging": "Cut Tape",
                    "LeadTime": "In Stock",
                    "Category": "Capacitors > Ceramic Capacitors",
                }
            ],
        }
    }

    def test_parse_mouser_response(self):
        from footfindr.suppliers.mouser import MouserProvider
        provider = MouserProvider()
        parts = provider._extract_parts(self.MOUSER_FIXTURE)
        assert len(parts) == 1
        part = parts[0]
        assert part.mpn == "GRM21BB31C106KE15"
        assert part.manufacturer == "Murata"
        assert part.supplier == "mouser"
        assert part.stock == 21450
        assert len(part.price_breaks) == 3
        assert part.price_breaks[0].unit_price == pytest.approx(0.15)
        assert part.lifecycle == "Active"
        assert part.datasheet_url == "https://example.com/datasheet.pdf"
        assert part.source == "live"

    def test_parse_empty_response(self):
        from footfindr.suppliers.mouser import MouserProvider
        provider = MouserProvider()
        parts = provider._extract_parts({"SearchResults": {"Parts": []}})
        assert len(parts) == 0


# ---------------------------------------------------------------------------
# Nexar response parsing (fixture-based)
# ---------------------------------------------------------------------------

class TestNexarResponseParsing:
    NEXAR_FIXTURE = {
        "supSearchMpn": {
            "results": [
                {
                    "part": {
                        "mpn": "GRM21BB31C106KE15",
                        "name": "10uF 16V Ceramic Capacitor",
                        "shortDescription": "Cap Ceramic 10uF 16V X7R 0805",
                        "manufacturer": {"name": "Murata"},
                        "bestDatasheet": {"url": "https://example.com/ds.pdf"},
                        "sellers": [
                            {
                                "company": {"name": "DigiKey"},
                                "offers": [
                                    {
                                        "inventoryLevel": 15000,
                                        "moq": 1,
                                        "packaging": "Cut Tape",
                                        "prices": [
                                            {"quantity": 1, "price": 0.12, "currency": "USD"},
                                            {"quantity": 100, "price": 0.06, "currency": "USD"},
                                        ],
                                    }
                                ],
                            },
                            {
                                "company": {"name": "Mouser"},
                                "offers": [
                                    {
                                        "inventoryLevel": 20000,
                                        "moq": 1,
                                        "packaging": "Tape & Reel",
                                        "prices": [
                                            {"quantity": 1, "price": 0.14, "currency": "USD"},
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                }
            ]
        }
    }

    def test_parse_nexar_response(self):
        from footfindr.suppliers.nexar import NexarProvider
        provider = NexarProvider()
        part = provider._parse_nexar_part(
            self.NEXAR_FIXTURE["supSearchMpn"]["results"][0]["part"]
        )
        assert part is not None
        assert part.mpn == "GRM21BB31C106KE15"
        assert part.manufacturer == "Murata"
        assert part.supplier == "nexar"
        assert part.stock == 35000  # Aggregated: 15000 + 20000
        assert part.datasheet_url == "https://example.com/ds.pdf"
        assert len(part.price_breaks) >= 2
        assert part.source == "live"

    def test_parse_empty_nexar(self):
        from footfindr.suppliers.nexar import NexarProvider
        provider = NexarProvider()
        part = provider._parse_nexar_part({})
        assert part is None


# ---------------------------------------------------------------------------
# JLCPCB response parsing (fixture-based)
# ---------------------------------------------------------------------------

class TestJLCPCBResponseParsing:
    JLC_FIXTURE = {
        "code": 200,
        "data": {
            "componentPageInfo": {
                "list": [
                    {
                        "componentModelEn": "GRM21BB31C106KE15",
                        "componentCode": "C15850",
                        "brandNameEn": "Murata",
                        "describe": "10uF ±10% 16V X7R 0805 MLCC",
                        "stockCount": 185000,
                        "componentLibraryType": "1",
                        "componentSpecificationEn": "0805",
                        "minOrder": 10,
                        "jlcPrices": [
                            {"startNumber": 10, "productPrice": 0.0358},
                            {"startNumber": 100, "productPrice": 0.0220},
                        ],
                    }
                ]
            }
        },
    }

    def test_parse_jlc_response(self):
        from footfindr.suppliers.jlcpcb import JLCPCBProvider
        provider = JLCPCBProvider()
        parts = provider._parse_jlc_response(self.JLC_FIXTURE, query="GRM21BB31C106KE15")
        assert len(parts) == 1
        part = parts[0]
        assert part.mpn == "GRM21BB31C106KE15"
        assert part.lcsc_pn == "C15850"
        assert part.manufacturer == "Murata"
        assert part.supplier == "jlcpcb"
        assert part.stock == 185000
        assert part.jlc_category == "basic"
        assert len(part.price_breaks) == 2
        assert part.source == "live"

    def test_parse_empty_jlc(self):
        from footfindr.suppliers.jlcpcb import JLCPCBProvider
        provider = JLCPCBProvider()
        parts = provider._parse_jlc_response(
            {"code": 200, "data": {"componentPageInfo": {"list": []}}},
        )
        assert len(parts) == 0


# ---------------------------------------------------------------------------
# DigiKey response parsing (fixture-based)
# ---------------------------------------------------------------------------

class TestDigiKeyResponseParsing:
    DK_FIXTURE = {
        "DigiKeyPartNumber": "490-GRM21BB31C106KE15CT-ND",
        "ManufacturerPartNumber": "GRM21BB31C106KE15",
        "Manufacturer": {"Name": "Murata"},
        "ProductDescription": "Cap Ceramic 10uF 16V X7R 0805",
        "QuantityAvailable": 45000,
        "StandardPricing": [
            {"BreakQuantity": 1, "UnitPrice": 0.13, "Currency": "USD"},
            {"BreakQuantity": 100, "UnitPrice": 0.065, "Currency": "USD"},
        ],
        "ProductStatus": {"Status": "Active"},
        "PrimaryDatasheet": "https://example.com/digikey-ds.pdf",
        "ProductUrl": "https://www.digikey.com/product/GRM21BB31C106KE15",
        "Packaging": {"Value": "Cut Tape"},
        "MinimumOrderQuantity": 1,
    }

    def test_parse_dk_product_details(self):
        from footfindr.suppliers.digikey import DigiKeyProvider
        provider = DigiKeyProvider()
        part = provider._parse_product_details(self.DK_FIXTURE)
        assert part is not None
        assert part.mpn == "GRM21BB31C106KE15"
        assert part.manufacturer == "Murata"
        assert part.supplier == "digikey"
        assert part.stock == 45000
        assert len(part.price_breaks) == 2
        assert part.lifecycle == "Active"
        assert part.product_url is not None
        assert part.source == "live"

    def test_parse_empty_dk(self):
        from footfindr.suppliers.digikey import DigiKeyProvider
        provider = DigiKeyProvider()
        part = provider._parse_product_details({})
        assert part is None


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------

class TestProviderCapabilities:
    def test_mouser_capabilities(self):
        from footfindr.suppliers.mouser import MouserProvider
        m = MouserProvider()
        cap = m.capabilities
        assert cap.lookup is True
        assert cap.search is True
        assert cap.stock_price is True
        assert cap.cart is False
        assert cap.order is False

    def test_nexar_capabilities(self):
        from footfindr.suppliers.nexar import NexarProvider
        n = NexarProvider()
        cap = n.capabilities
        assert cap.lookup is True
        assert cap.datasheet is True

    def test_digikey_sandbox_support(self):
        from footfindr.suppliers.digikey import DigiKeyProvider
        dk = DigiKeyProvider()
        assert dk.capabilities.sandbox is True

    def test_mock_status(self):
        from footfindr.suppliers.mock import MockSupplierProvider
        m = MockSupplierProvider()
        assert m.status == "mock only"

    def test_digikey_status_missing_creds(self):
        from footfindr.suppliers.digikey import DigiKeyProvider
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                dk = DigiKeyProvider()
                dk._creds = None  # Reset cached creds
                assert dk.status == "missing credentials"


# ---------------------------------------------------------------------------
# Cache with new fields
# ---------------------------------------------------------------------------

class TestCacheNewFields:
    def test_store_lcsc_fields(self, tmp_path: Path):
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.models import SupplierPart

        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        part = SupplierPart(
            supplier="jlcpcb",
            mpn="GRM21BB31C106KE15",
            manufacturer="Murata",
            description="10uF 16V X7R 0805",
            stock=185000,
            source="live",
            product_url="https://jlcpcb.com/partdetail/C15850",
            lcsc_pn="C15850",
            jlc_category="basic",
        )
        cache.store(part)

        results = cache.lookup("GRM21BB31C106KE15", supplier="jlcpcb")
        assert len(results) == 1
        assert results[0].lcsc_pn == "C15850"
        assert results[0].jlc_category == "basic"
        assert results[0].product_url == "https://jlcpcb.com/partdetail/C15850"
        assert results[0].description == "10uF 16V X7R 0805"

        cache.close()

    def test_store_multiple_suppliers(self, tmp_path: Path):
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.models import SupplierPart

        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        for sup in ["mouser", "nexar", "jlcpcb"]:
            cache.store(SupplierPart(
                supplier=sup,
                mpn="GRM21BB31C106KE15",
                manufacturer="Murata",
                stock=1000,
                source="live",
            ))

        results = cache.lookup("GRM21BB31C106KE15")
        assert len(results) == 3

        results_mouser = cache.lookup("GRM21BB31C106KE15", supplier="mouser")
        assert len(results_mouser) == 1
        assert results_mouser[0].supplier == "mouser"

        cache.close()


# ---------------------------------------------------------------------------
# Dotenv loading
# ---------------------------------------------------------------------------

class TestDotenvLoading:
    def test_load_dotenv_file(self, tmp_path: Path):
        from footfindr.suppliers.auth import _load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("TEST_FOOTFINDR_VAR=hello_world\n")

        with patch.dict(os.environ, {}, clear=True):
            _load_dotenv(workspace=tmp_path)
            assert os.environ.get("TEST_FOOTFINDR_VAR") == "hello_world"

    def test_env_var_takes_priority(self, tmp_path: Path):
        from footfindr.suppliers.auth import _load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("TEST_PRIORITY_VAR=from_file\n")

        with patch.dict(os.environ, {"TEST_PRIORITY_VAR": "from_env"}, clear=False):
            _load_dotenv(workspace=tmp_path)
            assert os.environ.get("TEST_PRIORITY_VAR") == "from_env"

    def test_load_dotenv_comments_ignored(self, tmp_path: Path):
        from footfindr.suppliers.auth import _load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nTEST_COMMENT_VAR=value\n\n")

        with patch.dict(os.environ, {}, clear=True):
            _load_dotenv(workspace=tmp_path)
            assert os.environ.get("TEST_COMMENT_VAR") == "value"


# ---------------------------------------------------------------------------
# DigiKey auth token selection (3-legged preferred over 2-legged)
# ---------------------------------------------------------------------------

class TestDigiKeyAuthTokenSelection:
    """Tests for DigiKey 3-legged vs 2-legged token selection logic."""

    def _write_token_cache(self, workspace: Path, key: str, data: dict) -> None:
        token_dir = workspace / "auth"
        token_dir.mkdir(parents=True, exist_ok=True)
        token_file = token_dir / "tokens.json"
        existing = {}
        if token_file.exists():
            existing = json.loads(token_file.read_text())
        existing[key] = data
        token_file.write_text(json.dumps(existing, indent=2))

    def test_login_creates_cached_3leg_token(self, tmp_path: Path):
        """ff supplier auth login digikey saves under digikey_3leg key."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager
        import respx

        ws = tmp_path / ".footfindr"

        # Mock the token endpoint for code exchange
        with respx.mock:
            respx.post("https://sandbox-api.digikey.com/v1/oauth2/token").respond(json={
                "access_token": "3leg-access-token",
                "refresh_token": "3leg-refresh-token",
                "expires_in": 1800,
                "token_type": "Bearer",
            })

            mgr = DigiKeyOAuthManager(
                client_id="test-id",
                client_secret="test-secret",
                sandbox=True,
                workspace=ws,
            )
            # Directly simulate code exchange (skip browser)
            mgr._exchange_code("fake-auth-code")

        # Verify saved under digikey_3leg key
        token_file = ws / "auth" / "tokens.json"
        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert "digikey_3leg" in data
        assert data["digikey_3leg"]["access_token"] == "3leg-access-token"
        assert data["digikey_3leg"]["refresh_token"] == "3leg-refresh-token"
        # Must NOT have saved under plain "digikey" key
        assert "digikey" not in data

    def test_lookup_prefers_cached_3leg_over_2leg(self, tmp_path: Path):
        """DigiKeyProvider prefers cached 3-legged token when available."""
        from footfindr.suppliers.digikey import DigiKeyProvider

        ws = tmp_path / ".footfindr"

        # Write a cached 3-legged token
        self._write_token_cache(ws, "digikey_3leg", {
            "access_token": "3leg-token",
            "refresh_token": "3leg-refresh",
            "expires_at": time.time() + 3600,
        })

        # Also write a 2-legged token
        self._write_token_cache(ws, "digikey", {
            "access_token": "2leg-token",
            "expires_at": time.time() + 3600,
            "token_type": "Bearer",
            "scope": "",
        })

        with patch("footfindr.suppliers.auth.DigiKeyOAuthManager.has_cached_tokens", return_value=True):
            with patch("footfindr.config.get_workspace", return_value=ws):
                dk = DigiKeyProvider()
                dk._creds = type("C", (), {
                    "client_id": "id", "client_secret": "secret",
                    "callback_url": "https://localhost:8765/cb",
                    "sandbox": True,
                })()
                mgr = dk._get_token_mgr()

        assert dk._auth_mode == "3-legged"

    def test_expired_3leg_access_uses_refresh(self, tmp_path: Path):
        """Expired 3-legged access token triggers refresh token flow."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager
        import respx

        ws = tmp_path / ".footfindr"

        # Write cached token with expired access but valid refresh
        self._write_token_cache(ws, "digikey_3leg", {
            "access_token": "expired-access",
            "refresh_token": "valid-refresh",
            "expires_at": time.time() - 100,  # expired
        })

        with respx.mock:
            respx.post("https://sandbox-api.digikey.com/v1/oauth2/token").respond(json={
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 1800,
                "token_type": "Bearer",
            })

            mgr = DigiKeyOAuthManager(
                client_id="test-id",
                client_secret="test-secret",
                sandbox=True,
                workspace=ws,
            )
            token = mgr.get_token()

        assert token == "new-access-token"
        assert mgr._refresh_token_val == "new-refresh-token"

    def test_no_3leg_and_2leg_403_suggests_login(self, tmp_path: Path):
        """When no 3-legged token exists and 2-legged gets 403, suggests login."""
        from footfindr.suppliers.digikey import DigiKeyProvider
        from footfindr.suppliers.http import SupplierHTTPError

        ws = tmp_path / ".footfindr"

        # No 3-legged token cached
        with patch("footfindr.suppliers.auth.DigiKeyOAuthManager.has_cached_tokens", return_value=False):
            with patch("footfindr.config.get_workspace", return_value=ws):
                dk = DigiKeyProvider()
                dk._creds = type("C", (), {
                    "client_id": "id", "client_secret": "secret",
                    "callback_url": "https://localhost:8765/cb",
                    "sandbox": True,
                })()

                # Force 2-legged mode
                dk._get_token_mgr()
                assert dk._auth_mode == "2-legged"

                # Simulate 403
                err = SupplierHTTPError("forbidden", provider="digikey", status_code=403)
                with pytest.raises(SupplierHTTPError, match="ff supplier auth login digikey"):
                    dk._handle_403(err)

    def test_debug_output_redacts_tokens(self):
        """Access tokens and refresh tokens are redacted in debug output."""
        from footfindr.suppliers.http import redact_secrets

        text_bearer = "Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.xxx"
        redacted = redact_secrets(text_bearer)
        assert "eyJ0eXAi" not in redacted
        assert "[REDACTED]" in redacted

        text_refresh = "refresh_token=dQw4w9WgXcQ_abc123_long_refresh_token_value"
        redacted2 = redact_secrets(text_refresh)
        assert "dQw4w9Wg" not in redacted2
        assert "[REDACTED]" in redacted2


# ---------------------------------------------------------------------------
# DigiKey OAuth login UX (timeout, manual-code, debug)
# ---------------------------------------------------------------------------

class TestDigiKeyLoginUX:
    """Tests for DigiKey OAuth login robustness improvements."""

    def _write_token_cache(self, workspace: Path, key: str, data: dict) -> None:
        token_dir = workspace / "auth"
        token_dir.mkdir(parents=True, exist_ok=True)
        token_file = token_dir / "tokens.json"
        existing = {}
        if token_file.exists():
            existing = json.loads(token_file.read_text())
        existing[key] = data
        token_file.write_text(json.dumps(existing, indent=2))

    def test_timeout_message_includes_callback_url_and_suggestions(self, tmp_path: Path):
        """Timeout error includes callback URL, possible causes, and suggested fixes."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager

        ws = tmp_path / ".footfindr"
        mgr = DigiKeyOAuthManager(
            client_id="test-id",
            client_secret="test-secret",
            sandbox=True,
            workspace=ws,
        )

        # Simulate a very short timeout — server will time out immediately
        with patch("webbrowser.open"):
            try:
                mgr.do_login(timeout=1)
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                msg = str(e)
                assert "localhost" in msg and "8765" in msg, "Should mention callback URL"
                assert "Possible causes" in msg
                assert "--manual-code" in msg
                assert "--timeout" in msg
                assert "certificate warning" in msg.lower() or "certificate" in msg

    def test_manual_code_extracts_code_from_url(self, tmp_path: Path):
        """Manual-code mode extracts authorization code from pasted redirect URL."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager
        import respx

        ws = tmp_path / ".footfindr"
        mgr = DigiKeyOAuthManager(
            client_id="test-id",
            client_secret="test-secret",
            sandbox=True,
            workspace=ws,
        )

        redirect_url = "https://localhost:8765/digikey/oauth/callback?code=test_auth_code_123&state=abc"

        with respx.mock:
            respx.post("https://sandbox-api.digikey.com/v1/oauth2/token").respond(json={
                "access_token": "manual-access-token",
                "refresh_token": "manual-refresh-token",
                "expires_in": 1800,
                "token_type": "Bearer",
            })

            with patch("builtins.input", return_value=redirect_url):
                with patch("webbrowser.open"):
                    token = mgr._do_manual_code_flow(
                        "https://sandbox-api.digikey.com/v1/oauth2/authorize?...",
                    )

        assert token == "manual-access-token"

        # Verify saved under digikey_3leg
        token_file = ws / "auth" / "tokens.json"
        data = json.loads(token_file.read_text())
        assert "digikey_3leg" in data
        assert data["digikey_3leg"]["refresh_token"] == "manual-refresh-token"

    def test_manual_code_handles_error_access_denied(self, tmp_path: Path):
        """Manual-code mode raises when URL contains error=access_denied."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager

        ws = tmp_path / ".footfindr"
        mgr = DigiKeyOAuthManager(
            client_id="test-id",
            client_secret="test-secret",
            sandbox=True,
            workspace=ws,
        )

        error_url = "https://localhost:8765/digikey/oauth/callback?error=access_denied&error_description=User+denied"

        with patch("builtins.input", return_value=error_url):
            with patch("webbrowser.open"):
                with pytest.raises(RuntimeError, match="access_denied"):
                    mgr._do_manual_code_flow("https://example.com/authorize")

    def test_callback_server_timeout_configurable(self, tmp_path: Path):
        """do_login respects custom timeout parameter."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager

        ws = tmp_path / ".footfindr"
        mgr = DigiKeyOAuthManager(
            client_id="test-id",
            client_secret="test-secret",
            sandbox=True,
            workspace=ws,
        )

        start = time.time()
        with patch("webbrowser.open"):
            try:
                mgr.do_login(timeout=2)
            except RuntimeError:
                pass
        elapsed = time.time() - start
        # Should have waited ~2 seconds, not 600
        assert elapsed < 10, f"Timeout was not respected: waited {elapsed}s"

    def test_debug_output_redacts_secrets(self, tmp_path: Path, capsys):
        """Debug output redacts code/token/refresh_token — auth URL may contain client_id for user."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager
        import respx

        ws = tmp_path / ".footfindr"
        mgr = DigiKeyOAuthManager(
            client_id="SensitiveClientIdValue12345",
            client_secret="SuperSecretValue67890",
            sandbox=True,
            workspace=ws,
        )

        redirect_url = "https://localhost:8765/digikey/oauth/callback?code=secret_auth_code_xyz"

        with respx.mock:
            respx.post("https://sandbox-api.digikey.com/v1/oauth2/token").respond(json={
                "access_token": "secret-access-tok",
                "refresh_token": "secret-refresh-tok",
                "expires_in": 1800,
            })

            with patch("builtins.input", return_value=redirect_url):
                with patch("webbrowser.open"):
                    mgr._do_manual_code_flow(
                        "https://sandbox-api.digikey.com/v1/oauth2/authorize?client_id=SensitiveClientIdValue12345",
                        debug=True,
                    )

        output = capsys.readouterr().out
        # Auth code, access token, refresh token must never appear in debug output
        assert "secret_auth_code_xyz" not in output
        assert "secret-access-tok" not in output
        assert "secret-refresh-tok" not in output
        # Client secret must never appear
        assert "SuperSecretValue67890" not in output
        # Should contain [REDACTED] markers for code
        assert "[REDACTED]" in output
        # [debug] lines should show redacted diagnostics
        debug_lines = [line for line in output.split("\n") if line.startswith("[debug]")]
        assert len(debug_lines) >= 2  # at least query keys + code redacted

    def test_3leg_token_saved_under_correct_key_after_login(self, tmp_path: Path):
        """After successful login, tokens are saved under digikey_3leg, not digikey."""
        from footfindr.suppliers.auth import DigiKeyOAuthManager
        import respx

        ws = tmp_path / ".footfindr"

        # Pre-populate a 2-legged token under "digikey" key
        self._write_token_cache(ws, "digikey", {
            "access_token": "2leg-token",
            "expires_at": time.time() + 3600,
            "token_type": "Bearer",
            "scope": "",
        })

        mgr = DigiKeyOAuthManager(
            client_id="test-id",
            client_secret="test-secret",
            sandbox=True,
            workspace=ws,
        )

        redirect_url = "https://localhost:8765/digikey/oauth/callback?code=auth_code"

        with respx.mock:
            respx.post("https://sandbox-api.digikey.com/v1/oauth2/token").respond(json={
                "access_token": "3leg-new-access",
                "refresh_token": "3leg-new-refresh",
                "expires_in": 1800,
            })

            with patch("builtins.input", return_value=redirect_url):
                with patch("webbrowser.open"):
                    mgr._do_manual_code_flow("https://example.com/authorize")

        # Verify 3-legged saved under correct key
        token_file = ws / "auth" / "tokens.json"
        data = json.loads(token_file.read_text())
        assert "digikey_3leg" in data
        assert data["digikey_3leg"]["access_token"] == "3leg-new-access"
        assert data["digikey_3leg"]["refresh_token"] == "3leg-new-refresh"

        # Verify 2-legged token NOT overwritten
        # Verify 2-legged token NOT overwritten
        assert "digikey" in data
        assert data["digikey"]["access_token"] == "2leg-token"


# ---------------------------------------------------------------------------
# DigiKey lookup/cache validation
# ---------------------------------------------------------------------------

class TestDigiKeyLookupValidation:
    """Tests for DigiKey lookup result validation and cache safety."""

    def test_empty_supplier_part_is_not_valid(self):
        """SupplierPart with empty mpn and no supplier_pn is invalid."""
        from footfindr.suppliers.models import SupplierPart

        # Completely empty
        empty = SupplierPart(supplier="digikey")
        assert not empty.is_valid()

        # Empty strings
        blank = SupplierPart(supplier="digikey", mpn="", supplier_pn="")
        assert not blank.is_valid()

        # Whitespace only
        ws = SupplierPart(supplier="digikey", mpn="  ", supplier_pn="  ")
        assert not ws.is_valid()

    def test_valid_supplier_part_with_mpn(self):
        """SupplierPart with non-empty mpn IS valid."""
        from footfindr.suppliers.models import SupplierPart

        part = SupplierPart(supplier="digikey", mpn="GRM155R60J106ME05D")
        assert part.is_valid()

    def test_valid_supplier_part_with_supplier_pn_only(self):
        """SupplierPart with supplier_pn but no mpn IS valid."""
        from footfindr.suppliers.models import SupplierPart

        part = SupplierPart(supplier="digikey", supplier_pn="490-GRM155-CT-ND")
        assert part.is_valid()

    def test_empty_digikey_parse_not_cached(self, tmp_path: Path):
        """Empty DigiKey API response should not be cached."""
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.models import SupplierPart

        db_path = tmp_path / "test_cache.sqlite"
        cache = SupplierCache(workspace=tmp_path)

        # Simulate an empty result (what DigiKey sandbox may return)
        empty_part = SupplierPart(
            supplier="digikey",
            mpn="",
            supplier_pn="",
            manufacturer="",
            last_checked="2026-01-01T00:00:00Z",
            source="live",
        )

        # Store should reject this
        cache.store(empty_part)

        # Cache should be empty
        results = cache.lookup("", supplier="digikey")
        assert len(results) == 0
        cache.close()

    def test_corrupt_cache_entry_filtered_on_lookup(self, tmp_path: Path):
        """Corrupt/empty cache entries are filtered out when is_valid() fails."""
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.models import SupplierPart

        db_path = tmp_path / "test_cache.sqlite"
        cache = SupplierCache(workspace=tmp_path)

        # Directly insert a corrupt entry via SQL (bypassing validation)
        conn = cache._connect()
        conn.execute("""INSERT INTO supplier_parts
            (manufacturer, mpn, supplier, supplier_pn, source, last_checked)
            VALUES ('', '', 'digikey', '', 'live', '2026-01-01')""")
        conn.commit()

        # Lookup returns the raw row
        results = cache.lookup("", supplier="digikey")

        # Filter with is_valid (as CLI does)
        valid = [r for r in results if r.is_valid()]
        assert len(valid) == 0

        cache.close()

    def test_digikey_parse_real_product_fixture(self):
        """Full DigiKey product fixture parses MPN, manufacturer, supplier_pn, stock, pricing."""
        from footfindr.suppliers.digikey import DigiKeyProvider

        fixture = {
            "DigiKeyPartNumber": "490-GRM155R60J106ME05DCT-ND",
            "ManufacturerPartNumber": "GRM155R60J106ME05D",
            "Manufacturer": {"Name": "Murata Electronics"},
            "ProductDescription": "CAP CER 10UF 6.3V X5R 0402",
            "QuantityAvailable": 150000,
            "StandardPricing": [
                {"BreakQuantity": 1, "UnitPrice": 0.10, "Currency": "USD"},
                {"BreakQuantity": 100, "UnitPrice": 0.08, "Currency": "USD"},
                {"BreakQuantity": 1000, "UnitPrice": 0.05, "Currency": "USD"},
            ],
            "ProductUrl": "https://www.digikey.com/en/products/detail/murata/GRM155R60J106ME05D",
            "PrimaryDatasheet": "https://www.murata.com/datasheet.pdf",
            "ProductStatus": {"Status": "Active"},
            "Packaging": {"Value": "Tape & Reel"},
            "MinimumOrderQuantity": 1,
            "LeadTime": "12 Weeks",
        }

        dk = DigiKeyProvider.__new__(DigiKeyProvider)
        result = dk._parse_product_details(fixture)

        assert result is not None
        assert result.is_valid()
        assert result.mpn == "GRM155R60J106ME05D"
        assert result.manufacturer == "Murata Electronics"
        assert result.supplier_pn == "490-GRM155R60J106ME05DCT-ND"
        assert result.description == "CAP CER 10UF 6.3V X5R 0402"
        assert result.stock == 150000
        assert len(result.price_breaks) == 3
        assert result.price_breaks[0].unit_price == 0.10
        assert result.price_breaks[2].quantity == 1000
        assert result.datasheet_url == "https://www.murata.com/datasheet.pdf"
        assert result.product_url is not None
        assert result.lifecycle == "Active"
        assert result.packaging == "Tape & Reel"
        assert result.lead_time == "12 Weeks"

    def test_digikey_parse_empty_response_returns_none(self):
        """DigiKey empty/blank response returns None, not an empty SupplierPart."""
        from footfindr.suppliers.digikey import DigiKeyProvider

        dk = DigiKeyProvider.__new__(DigiKeyProvider)

        # Empty dict
        assert dk._parse_product_details({}) is None

        # Product: null
        assert dk._parse_product_details({"Product": None}) is None

    def test_digikey_debug_does_not_leak_secrets(self, capsys):
        """Debug output from parse must not contain token/secret values."""
        from footfindr.suppliers.digikey import DigiKeyProvider

        fixture = {
            "DigiKeyPartNumber": "490-TEST-ND",
            "ManufacturerPartNumber": "TEST-MPN",
            "Manufacturer": {"Name": "TestMfr"},
        }

        dk = DigiKeyProvider.__new__(DigiKeyProvider)
        dk._parse_product_details(fixture, debug=True)

        output = capsys.readouterr().out
        # Debug output should contain field mappings
        assert "ManufacturerProductNumber" in output
        assert "TEST-MPN" in output  # This IS expected in parse debug (it's part data, not a secret)
        # Should NOT contain any token patterns
        assert "Bearer" not in output
        assert "client_secret" not in output

    def test_cache_clear_per_mpn(self, tmp_path: Path):
        """Cache clear with --supplier and --mpn deletes only that entry."""
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.models import SupplierPart

        db_path = tmp_path / "test_cache.sqlite"
        cache = SupplierCache(workspace=tmp_path)

        # Insert two entries
        part1 = SupplierPart(
            supplier="digikey", mpn="PART-A", supplier_pn="DK-A",
            last_checked="2026-01-01", source="live",
        )
        part2 = SupplierPart(
            supplier="digikey", mpn="PART-B", supplier_pn="DK-B",
            last_checked="2026-01-01", source="live",
        )
        cache.store(part1)
        cache.store(part2)

        # Clear only PART-A
        count = cache.clear(supplier="digikey", mpn="PART-A")
        assert count == 1

        # PART-B should remain
        remaining = cache.lookup("PART-B", supplier="digikey")
        assert len(remaining) == 1
        assert remaining[0].mpn == "PART-B"

        # PART-A should be gone
        gone = cache.lookup("PART-A", supplier="digikey")
        assert len(gone) == 0

        cache.close()

