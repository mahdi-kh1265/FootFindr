"""IC profile drafter using AI provider.

Creates draft IC profiles from datasheet text.  All outputs are marked
``human_approved: false``.  The drafter never modifies KiCad files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from footfindr.ai.provider import AIProvider, NullAIProvider
from footfindr.ai.schemas import DraftICProfile


class ProfileDrafter:
    """Drafts IC profiles using an AI provider and datasheet text."""

    def __init__(self, provider: Optional[AIProvider] = None) -> None:
        self._provider = provider or NullAIProvider()

    def draft(
        self,
        mpn: str,
        datasheet_text: Optional[str] = None,
    ) -> DraftICProfile:
        """Create a draft IC profile.

        If datasheet_text is provided and an AI provider is available,
        the provider extracts structured data.  Otherwise, a skeleton
        profile is created.
        """
        if datasheet_text and self._provider.is_available():
            profile = self._provider.extract_ic_profile(datasheet_text, mpn)
        else:
            profile = DraftICProfile(
                mpn=mpn,
                human_approved=False,
                confidence=0.0,
                notes="Draft profile — no datasheet text or AI provider available.",
            )

        # Enforce safety: always mark as not approved
        profile.human_approved = False
        return profile

    def save_draft(
        self,
        profile: DraftICProfile,
        output_dir: str | Path,
        *,
        fmt: str = "yaml",
    ) -> Path:
        """Save a draft profile to disk."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_mpn = profile.mpn.replace("/", "_").replace(" ", "_")

        if fmt == "json":
            path = output_dir / f"{safe_mpn}_profile.json"
            path.write_text(
                json.dumps(profile.model_dump(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            path = output_dir / f"{safe_mpn}_profile.yaml"
            path.write_text(
                yaml.dump(profile.model_dump(), default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        return path
