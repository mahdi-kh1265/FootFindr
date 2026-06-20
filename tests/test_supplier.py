"""Tests for supplier providers, cache, and registry.

Verifies:
- Mock provider returns canned results
- Cache store/lookup/clear with manufacturer uniqueness
- Provider interfaces don't make live calls
- Order submission raises NotImplementedError
- Registry lists providers correctly
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path

from footfindr.suppliers.base import SupplierProvider
from footfindr.suppliers.cache import SupplierCache
from footfindr.suppliers.mock import MockSupplierProvider
from footfindr.suppliers.models import PriceBreak, SupplierPart
from footfindr.suppliers.registry import SupplierRegistry


class TestMockProvider:
    def test_name(self):
        mock = MockSupplierProvider()
        assert mock.name == "mock"

    def test_is_configured(self):
        mock = MockSupplierProvider()
        assert mock.is_configured()

    def test_is_live_not_implemented(self):
        mock = MockSupplierProvider()
        assert not mock.is_live_implemented

    def test_lookup_known_mpn(self):
        mock = MockSupplierProvider()
        result = mock.lookup_mpn("GRM21BB31C106KE15")
        assert result is not None
        assert result.mpn == "GRM21BB31C106KE15"
        assert result.manufacturer == "Murata"
        assert result.source == "mock"
        assert result.stock > 0

    def test_lookup_unknown_mpn(self):
        mock = MockSupplierProvider()
        result = mock.lookup_mpn("DOES-NOT-EXIST-12345")
        assert result is None

    def test_lookup_with_manufacturer(self):
        mock = MockSupplierProvider()
        result = mock.lookup_mpn("GRM21BB31C106KE15", manufacturer="Murata")
        assert result is not None

    def test_search(self):
        mock = MockSupplierProvider()
        results = mock.search("GRM21")
        assert len(results) >= 1

    def test_refresh_stock(self):
        mock = MockSupplierProvider()
        offers = mock.refresh_stock("GRM21BB31C106KE15")
        assert len(offers) == 1
        assert offers[0].source == "mock"
        assert offers[0].stock > 0

    def test_get_datasheet_url(self):
        mock = MockSupplierProvider()
        url = mock.get_datasheet_url("GRM21BB31C106KE15")
        assert url is not None
        assert "GRM21BB31C106KE15" in url

    def test_lookup_lcsc(self):
        mock = MockSupplierProvider()
        lcsc = mock.lookup_lcsc("GRM21BB31C106KE15")
        assert lcsc == "C15850"

    def test_lookup_lcsc_unknown(self):
        mock = MockSupplierProvider()
        lcsc = mock.lookup_lcsc("UNKNOWN-123")
        assert lcsc is None


class TestSupplierCache:
    def test_store_and_lookup(self, tmp_path: Path):
        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        part = SupplierPart(
            supplier="mock",
            mpn="GRM21BB31C106KE15",
            manufacturer="Murata",
            stock=25000,
            source="mock",
        )
        cache.store(part)

        results = cache.lookup("GRM21BB31C106KE15")
        assert len(results) == 1
        assert results[0].manufacturer == "Murata"

        cache.close()

    def test_uniqueness_includes_manufacturer(self, tmp_path: Path):
        """UNIQUE constraint on (manufacturer, mpn, supplier)."""
        cache = SupplierCache(workspace=tmp_path / ".footfindr")

        part1 = SupplierPart(
            supplier="mock",
            mpn="SAME-MPN",
            manufacturer="Vendor A",
            stock=100,
            source="mock",
        )
        part2 = SupplierPart(
            supplier="mock",
            mpn="SAME-MPN",
            manufacturer="Vendor B",
            stock=200,
            source="mock",
        )
        cache.store(part1)
        cache.store(part2)

        results = cache.lookup("SAME-MPN")
        assert len(results) == 2

        cache.close()

    def test_upsert(self, tmp_path: Path):
        """Storing same (manufacturer, mpn, supplier) should update."""
        cache = SupplierCache(workspace=tmp_path / ".footfindr")

        part = SupplierPart(
            supplier="mock",
            mpn="GRM21BB31C106KE15",
            manufacturer="Murata",
            stock=100,
            source="mock",
        )
        cache.store(part)

        part.stock = 999
        cache.store(part)

        results = cache.lookup("GRM21BB31C106KE15", manufacturer="Murata")
        assert len(results) == 1
        assert results[0].stock == 999

        cache.close()

    def test_clear_all(self, tmp_path: Path):
        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        cache.store(SupplierPart(supplier="mock", mpn="A", manufacturer="X", source="mock"))
        cache.store(SupplierPart(supplier="mock", mpn="B", manufacturer="Y", source="mock"))

        count = cache.clear()
        assert count == 2

        results = cache.lookup("A")
        assert len(results) == 0

        cache.close()

    def test_clear_by_supplier(self, tmp_path: Path):
        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        cache.store(SupplierPart(supplier="mock", mpn="A", manufacturer="X", source="mock"))
        cache.store(SupplierPart(supplier="digikey", mpn="B", manufacturer="Y", source="live"))

        count = cache.clear(supplier="mock")
        assert count == 1

        results = cache.lookup("B")
        assert len(results) == 1

        cache.close()

    def test_info(self, tmp_path: Path):
        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        cache.store(SupplierPart(supplier="mock", mpn="A", manufacturer="X", source="mock"))

        info = cache.info()
        assert info.total_entries == 1
        assert "mock" in info.suppliers
        assert info.schema_version == "2"

        cache.close()

    def test_price_breaks_roundtrip(self, tmp_path: Path):
        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        part = SupplierPart(
            supplier="mock",
            mpn="TEST-PB",
            manufacturer="Test",
            price_breaks=[
                PriceBreak(quantity=1, unit_price=0.10),
                PriceBreak(quantity=100, unit_price=0.05),
            ],
            source="mock",
        )
        cache.store(part)

        results = cache.lookup("TEST-PB")
        assert len(results) == 1
        assert len(results[0].price_breaks) == 2
        assert results[0].price_breaks[0].quantity == 1
        assert results[0].price_breaks[0].unit_price == pytest.approx(0.10)

        cache.close()

    def test_manufacturer_normalization(self, tmp_path: Path):
        cache = SupplierCache(workspace=tmp_path / ".footfindr")
        part = SupplierPart(
            supplier="mock",
            mpn="TEST-NORM",
            manufacturer="Murata Electronics",
            source="mock",
        )
        cache.store(part)

        # Should normalize to "Murata"
        results = cache.lookup("TEST-NORM", manufacturer="Murata")
        assert len(results) == 1

        cache.close()


class TestProviderSafety:
    """Ordering/cart methods must raise NotImplementedError."""

    def test_mock_create_cart_raises(self):
        mock = MockSupplierProvider()
        with pytest.raises(NotImplementedError):
            mock.create_cart()

    def test_mock_add_to_cart_raises(self):
        mock = MockSupplierProvider()
        with pytest.raises(NotImplementedError):
            mock.add_to_cart()

    def test_mock_quote_raises(self):
        mock = MockSupplierProvider()
        with pytest.raises(NotImplementedError):
            mock.quote()

    def test_mock_submit_order_raises(self):
        mock = MockSupplierProvider()
        with pytest.raises(NotImplementedError):
            mock.submit_order()

    def test_digikey_not_configured_raises(self):
        """DigiKey without credentials raises SupplierHTTPError, not NotImplementedError."""
        from unittest.mock import patch
        from footfindr.suppliers.digikey import DigiKeyProvider
        from footfindr.suppliers.http import SupplierHTTPError
        with patch("footfindr.suppliers.auth._load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                dk = DigiKeyProvider()
                dk._creds = None  # Reset any cached creds
                with pytest.raises(SupplierHTTPError):
                    dk.lookup_mpn("TEST")
                with pytest.raises(NotImplementedError):
                    dk.submit_order()
                assert not dk.is_configured()
                assert dk.is_live_implemented  # Now implemented

    def test_mouser_not_configured_raises(self):
        """Mouser without credentials raises SupplierHTTPError."""
        from footfindr.suppliers.mouser import MouserProvider
        from footfindr.suppliers.http import SupplierHTTPError
        m = MouserProvider()
        if not m.is_configured():
            with pytest.raises(SupplierHTTPError):
                m.lookup_mpn("TEST")
        assert m.is_live_implemented

    def test_nexar_not_configured_raises(self):
        """Nexar without credentials raises SupplierHTTPError."""
        from footfindr.suppliers.nexar import NexarProvider
        from footfindr.suppliers.http import SupplierHTTPError
        n = NexarProvider()
        if not n.is_configured():
            with pytest.raises(SupplierHTTPError):
                n.lookup_mpn("TEST")
        assert n.is_live_implemented

    def test_jlcpcb_not_configured_raises(self):
        """JLCPCB without credentials raises SupplierHTTPError."""
        from footfindr.suppliers.jlcpcb import JLCPCBProvider
        from footfindr.suppliers.http import SupplierHTTPError
        j = JLCPCBProvider()
        if not j.is_configured():
            with pytest.raises(SupplierHTTPError):
                j.lookup_mpn("TEST")
        assert j.is_live_implemented

    def test_all_providers_cart_order_raises(self):
        """All live providers must raise NotImplementedError for cart/order."""
        from footfindr.suppliers.digikey import DigiKeyProvider
        from footfindr.suppliers.mouser import MouserProvider
        from footfindr.suppliers.nexar import NexarProvider
        from footfindr.suppliers.jlcpcb import JLCPCBProvider

        for ProviderClass in [DigiKeyProvider, MouserProvider, NexarProvider, JLCPCBProvider]:
            provider = ProviderClass()
            with pytest.raises(NotImplementedError):
                provider.create_cart()
            with pytest.raises(NotImplementedError):
                provider.submit_order()


class TestRegistry:
    def test_list_providers(self):
        reg = SupplierRegistry()
        providers = reg.list_providers()
        names = {p.name for p in providers}
        assert "digikey" in names
        assert "mouser" in names
        assert "nexar" in names
        assert "jlcpcb" in names
        assert "mock" in names

    def test_get_mock(self):
        reg = SupplierRegistry()
        mock = reg.get("mock")
        assert mock is not None
        assert mock.name == "mock"

    def test_get_unknown(self):
        reg = SupplierRegistry()
        assert reg.get("nonexistent") is None

    def test_mock_is_configured(self):
        reg = SupplierRegistry()
        providers = reg.list_providers()
        mock_info = next(p for p in providers if p.name == "mock")
        assert mock_info.configured
        assert not mock_info.live_implemented

    def test_digikey_live_implemented(self):
        """DigiKey is now live implemented (credentials may or may not be set)."""
        reg = SupplierRegistry()
        providers = reg.list_providers()
        dk_info = next(p for p in providers if p.name == "digikey")
        assert dk_info.live_implemented  # Now implemented

    def test_capabilities_present(self):
        """All providers should have capabilities."""
        reg = SupplierRegistry()
        for p in reg.list_providers():
            assert p.capabilities is not None
            assert isinstance(p.capabilities.lookup, bool)

    def test_get_configured_live(self):
        """get_configured_live returns only configured non-mock providers."""
        reg = SupplierRegistry()
        live = reg.get_configured_live()
        for p in live:
            assert p.name != "mock"
            assert p.is_live_implemented
            assert p.is_configured()

