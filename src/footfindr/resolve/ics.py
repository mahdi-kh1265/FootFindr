"""IC resolver stub.

Resolves ICs by exact MPN/InternalPN match against the approved library.
Full IC profile-based resolution is deferred to a future milestone.
"""

from __future__ import annotations

from typing import Optional

from footfindr.core.models import ComponentContext, Decision, PartRecord
from footfindr.resolve.exact import ExactResolver


class ICResolver:
    """IC resolver — delegates to exact resolver for now.

    Future: will consult IC profiles for package verification,
    pin count checking, and support part recommendations.
    """

    def __init__(self) -> None:
        self._exact = ExactResolver()

    def resolve(
        self,
        ctx: ComponentContext,
        approved_parts: list[PartRecord],
    ) -> Optional[Decision]:
        """Try to resolve an IC component."""
        if ctx.category != "ic":
            return None
        return self._exact.resolve(ctx, approved_parts)
