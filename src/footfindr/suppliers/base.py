"""Supplier provider base interface.

Defines the contract for supplier API integrations (DigiKey, Mouser,
Nexar, JLCPCB/LCSC).  All ordering/cart methods raise NotImplementedError
to enforce the purchasing safety boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from footfindr.suppliers.models import SupplierPart, SupplierOffer


@dataclass
class ProviderCapabilities:
    """Feature matrix for a supplier provider."""
    lookup: bool = False
    search: bool = False
    stock_price: bool = False
    datasheet: bool = False
    lifecycle: bool = False
    cart: bool = False
    order: bool = False
    sandbox: bool = False


class SupplierProvider(ABC):
    """Abstract base for supplier API providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Supplier name (e.g., 'digikey', 'mouser')."""
        ...

    @abstractmethod
    def lookup_mpn(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[SupplierPart]:
        """Look up a part by MPN. Returns None if not found."""
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        **filters: Any,
    ) -> list[SupplierPart]:
        """Search for parts matching a query string."""
        ...

    @abstractmethod
    def refresh_stock(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> list[SupplierOffer]:
        """Get current stock/price for a part."""
        ...

    @abstractmethod
    def get_datasheet_url(
        self,
        mpn: str,
        *,
        manufacturer: str | None = None,
    ) -> Optional[str]:
        """Get a datasheet URL for a part."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if this provider has valid credentials configured."""
        ...

    @property
    def is_live_implemented(self) -> bool:
        """Whether this provider has live API calls implemented."""
        return False

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Provider capability matrix."""
        return ProviderCapabilities()

    @property
    def status(self) -> str:
        """Human-readable status: 'ready', 'missing credentials', 'mock only', etc."""
        if not self.is_live_implemented:
            return "mock only"
        if not self.is_configured():
            return "missing credentials"
        return "ready"

    def auth_status(self):
        """Get detailed auth status. Override in subclasses."""
        from footfindr.suppliers.auth import AuthStatus
        return AuthStatus(
            provider=self.name,
            configured=self.is_configured(),
            env_vars_present=[],
            env_vars_missing=[],
        )

    def lookup_lcsc(self, mpn: str) -> str | None:
        """Look up LCSC part number for an MPN. Returns None if not available."""
        return None

    # ----- Purchasing safety boundary -----
    # These stubs exist to document the future interface but MUST NOT
    # be implemented until explicit purchasing commands exist.

    def create_cart(self, **kwargs: Any) -> Any:
        """Create a supplier cart. NOT IMPLEMENTED — purchasing not allowed."""
        raise NotImplementedError(
            f"Purchasing via {self.name} is not implemented. "
            "FootFindr does not support automatic purchasing yet."
        )

    def add_to_cart(self, **kwargs: Any) -> Any:
        """Add items to a supplier cart. NOT IMPLEMENTED."""
        raise NotImplementedError(
            f"Cart operations via {self.name} are not implemented."
        )

    def quote(self, **kwargs: Any) -> Any:
        """Get a quote. NOT IMPLEMENTED."""
        raise NotImplementedError(
            f"Quoting via {self.name} is not implemented."
        )

    def submit_order(self, **kwargs: Any) -> Any:
        """Submit an order. NOT IMPLEMENTED — purchasing not allowed."""
        raise NotImplementedError(
            f"Order submission via {self.name} is not implemented. "
            "FootFindr will never submit orders without explicit user command: "
            "ff order submit --supplier <name> --from-cart <cart_id>"
        )
