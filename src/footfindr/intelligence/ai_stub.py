"""AI adapter stub for the intelligence engine (M9.4).

AI is disabled by default.  When enabled, it can provide explanations
and review of deterministic results, but it **never** owns facts, scores,
or rankings.  The deterministic graph/scoring engine owns truth.

Environment variables::

    FOOTFINDR_AI_PROVIDER=none             # default — AI disabled
    FOOTFINDR_AI_PROVIDER=openai_compatible
    FOOTFINDR_AI_BASE_URL=http://localhost:11434/v1
    FOOTFINDR_AI_MODEL=qwen3:4b
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any


class IntelligenceAI(ABC):
    """Abstract interface for AI-assisted explanation/review.

    AI must never be the source of truth.  It can explain, review,
    or suggest tool plans, but scoring and facts are deterministic.
    """

    @abstractmethod
    def explain_suggestion(
        self,
        suggestion_data: dict[str, Any],
    ) -> str:
        """Generate a human-readable explanation of a suggestion.

        The suggestion_data dict comes from the deterministic engine.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if AI is configured and reachable."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the AI provider."""
        ...


class NullIntelligenceAI(IntelligenceAI):
    """No-op AI — used when AI is disabled (default)."""

    def explain_suggestion(self, suggestion_data: dict[str, Any]) -> str:
        return "(AI explanation disabled. Set FOOTFINDR_AI_PROVIDER to enable.)"

    def is_available(self) -> bool:
        return False

    @property
    def provider_name(self) -> str:
        return "none"


def get_intelligence_ai() -> IntelligenceAI:
    """Get the configured AI provider.

    Returns ``NullIntelligenceAI`` unless ``FOOTFINDR_AI_PROVIDER``
    is explicitly set to a supported provider.
    """
    provider = os.environ.get("FOOTFINDR_AI_PROVIDER", "none").lower()

    if provider == "none" or not provider:
        return NullIntelligenceAI()

    if provider == "openai_compatible":
        # Future: return OpenAICompatibleIntelligenceAI(...)
        # For now, return null with a note
        return NullIntelligenceAI()

    return NullIntelligenceAI()
