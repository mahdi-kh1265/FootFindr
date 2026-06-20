"""Pydantic schemas for IC profiles and AI-drafted outputs.

These models represent the structured profile data that FootFindr
extracts (or AI drafts) from datasheets.  All AI-generated profiles
are marked ``human_approved: false`` until explicitly approved.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PinProfile(BaseModel):
    """Profile of a single IC pin."""
    number: str
    name: str | None = None
    function: str | None = None  # power_input, power_output, ground, signal, nc, etc.
    voltage_range: str | None = None
    notes: str | None = None


class SupportPartRequirement(BaseModel):
    """A recommended support part from the datasheet (e.g., decoupling cap)."""
    pin: str
    role: str  # input_decoupling, output_filter, bootstrap, feedback, etc.
    component_type: str  # capacitor, resistor, inductor
    value: str | None = None
    voltage_min: str | None = None
    package_hint: str | None = None
    notes: str | None = None


class ICProfile(BaseModel):
    """Approved IC profile — human-reviewed and ready for resolver use."""
    mpn: str
    aliases: list[str] = Field(default_factory=list)
    package: str | None = None
    pins: list[PinProfile] = Field(default_factory=list)
    recommended_support_parts: list[SupportPartRequirement] = Field(default_factory=list)
    human_approved: bool = True
    source_documents: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    notes: str | None = None


class DraftICProfile(BaseModel):
    """Draft IC profile — AI-generated, NOT approved for resolver use.

    Must be reviewed and promoted to an ``ICProfile`` before the resolver
    will use it for auto-apply decisions.
    """
    mpn: str
    aliases: list[str] = Field(default_factory=list)
    package: str | None = None
    pins: list[PinProfile] = Field(default_factory=list)
    recommended_support_parts: list[SupportPartRequirement] = Field(default_factory=list)
    human_approved: bool = False
    source_documents: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    notes: str | None = None
