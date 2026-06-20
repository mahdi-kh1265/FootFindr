"""AI provider interface and implementations.

The AI sidecar is for draft profile generation and datasheet extraction.
AI output NEVER directly modifies KiCad files.  All AI outputs are marked
``human_approved: false`` and must be reviewed before use.

Provides:
  - ``AIProvider`` protocol
  - ``NullAIProvider`` (no-op, always available)
  - ``MockAIProvider`` (returns canned data for tests)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from footfindr.ai.schemas import DraftICProfile, PinProfile, SupportPartRequirement


class AIProvider(ABC):
    """Abstract base for AI providers used by FootFindr."""

    @abstractmethod
    def extract_ic_profile(
        self,
        datasheet_text: str,
        mpn: str,
    ) -> DraftICProfile:
        """Extract a draft IC profile from datasheet text.

        The result is ALWAYS marked ``human_approved=False``.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""
        ...


class NullAIProvider(AIProvider):
    """No-op AI provider — used when no AI is configured."""

    def extract_ic_profile(self, datasheet_text: str, mpn: str) -> DraftICProfile:
        return DraftICProfile(
            mpn=mpn,
            human_approved=False,
            confidence=0.0,
            notes="No AI provider configured. Create profile manually.",
        )

    def is_available(self) -> bool:
        return True


class MockAIProvider(AIProvider):
    """Mock AI provider for tests — returns realistic but canned data."""

    def extract_ic_profile(self, datasheet_text: str, mpn: str) -> DraftICProfile:
        return DraftICProfile(
            mpn=mpn,
            aliases=[mpn.split("/")[0]] if "/" in mpn else [],
            package="QFN-20",  # mock
            pins=[
                PinProfile(number="1", name="VIN", function="power_input"),
                PinProfile(number="2", name="VOUT", function="power_output"),
                PinProfile(number="3", name="GND", function="ground"),
            ],
            recommended_support_parts=[
                SupportPartRequirement(
                    pin="VIN",
                    role="input_decoupling",
                    component_type="capacitor",
                    value="10uF",
                    notes="Place close to VIN pin",
                ),
            ],
            human_approved=False,
            confidence=0.6,
            source_documents=["mock_datasheet"],
            notes="Auto-generated mock profile — requires human review",
        )

    def is_available(self) -> bool:
        return True
