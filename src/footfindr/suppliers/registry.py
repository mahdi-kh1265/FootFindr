"""Supplier provider registry.

Central registry of all available supplier providers.  Providers can be
looked up by name and listed with their configuration status.
"""

from __future__ import annotations

from dataclasses import dataclass

from footfindr.suppliers.base import ProviderCapabilities, SupplierProvider


@dataclass
class ProviderInfo:
    """Summary of a registered supplier provider."""
    name: str
    configured: bool
    live_implemented: bool
    capabilities: ProviderCapabilities | None = None
    sandbox: bool = False
    status: str = ""


class SupplierRegistry:
    """Registry of supplier providers."""

    # Alias → canonical supplier name
    SUPPLIER_ALIASES: dict[str, str] = {
        "dk": "digikey",
        "digi": "digikey",
        "mou": "mouser",
        "jlc": "jlcpcb",
        "lcsc": "jlcpcb",
        "nex": "nexar",
        # Canonical names map to themselves
        "digikey": "digikey",
        "mouser": "mouser",
        "jlcpcb": "jlcpcb",
        "nexar": "nexar",
        "mock": "mock",
    }

    def __init__(self) -> None:
        self._providers: dict[str, SupplierProvider] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register all built-in providers."""
        from footfindr.suppliers.digikey import DigiKeyProvider
        from footfindr.suppliers.mouser import MouserProvider
        from footfindr.suppliers.nexar import NexarProvider
        from footfindr.suppliers.jlcpcb import JLCPCBProvider
        from footfindr.suppliers.mock import MockSupplierProvider

        for provider in [
            DigiKeyProvider(),
            MouserProvider(),
            NexarProvider(),
            JLCPCBProvider(),
            MockSupplierProvider(),
        ]:
            self._providers[provider.name] = provider

    @classmethod
    def normalize_name(cls, name: str) -> str:
        """Normalize a supplier alias to its canonical name."""
        return cls.SUPPLIER_ALIASES.get(name.lower().strip(), name.lower().strip())

    def get(self, name: str) -> SupplierProvider | None:
        """Get a provider by name or alias."""
        canonical = self.normalize_name(name)
        return self._providers.get(canonical)

    def list_providers(self) -> list[ProviderInfo]:
        """List all registered providers with status."""
        return [
            ProviderInfo(
                name=p.name,
                configured=p.is_configured(),
                live_implemented=p.is_live_implemented,
                capabilities=p.capabilities,
                sandbox=p.capabilities.sandbox if p.capabilities else False,
                status=p.status,
            )
            for p in self._providers.values()
        ]

    def get_configured_live(self) -> list[SupplierProvider]:
        """Get all configured live providers (excludes mock)."""
        return [
            p for p in self._providers.values()
            if p.is_live_implemented and p.is_configured() and p.name != "mock"
        ]

    def register(self, provider: SupplierProvider) -> None:
        """Register a custom provider."""
        self._providers[provider.name] = provider
