"""Core typed models for FootFindr.

Rich data model supporting the full lifecycle:
parts, decisions, libraries, physical variants, supplier offers, documents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DecisionStatus(StrEnum):
    """Status of a resolver decision for a single component."""
    AUTO = "AUTO"
    REVIEW = "REVIEW"
    SKIP = "SKIP"
    ERROR = "ERROR"
    UNCHANGED = "UNCHANGED"


class ComponentCategory(StrEnum):
    """Component categories recognised by the resolver."""
    CAPACITOR = "capacitor"
    RESISTOR = "resistor"
    INDUCTOR = "inductor"
    IC = "ic"
    CONNECTOR = "connector"
    DIODE = "diode"
    TRANSISTOR = "transistor"
    CRYSTAL = "crystal"
    LED = "led"
    OTHER = "other"


class PartStatus(StrEnum):
    """Lifecycle status of a part within the FootFindr library system."""
    RAW = "raw"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    BLOCKED = "blocked"


class LibraryKind(StrEnum):
    """Kind of FootFindr library."""
    MASTER = "master"
    SUB = "sub"
    RAW_VENDOR = "raw_vendor"
    APPROVED = "approved"
    PROJECT = "project"


class LifecycleStatus(StrEnum):
    """Manufacturer lifecycle status."""
    ACTIVE = "active"
    NRND = "nrnd"
    OBSOLETE = "obsolete"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Component context — what the resolver sees from the schematic
# ---------------------------------------------------------------------------

@dataclass
class ComponentContext:
    """Everything the resolver knows about a single schematic component."""
    ref: str
    value: str
    symbol: str | None = None
    footprint: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    category: ComponentCategory | None = None
    pins: dict[str, str] = field(default_factory=dict)  # pin_name -> net_name
    nets: set[str] = field(default_factory=set)
    risk_flags: list[str] = field(default_factory=list)
    locked: bool = False
    dnp: bool = False


# ---------------------------------------------------------------------------
# Part data model — the long-term architecture for a real part
# ---------------------------------------------------------------------------

@dataclass
class ElectricalSpecs:
    """Electrical specifications of a part."""
    capacitance: str | None = None
    resistance: str | None = None
    inductance: str | None = None
    voltage_rating: str | None = None
    current_rating: str | None = None
    power_rating: str | None = None
    tolerance: str | None = None
    dielectric: str | None = None
    tempco: str | None = None
    dcr: str | None = None
    esr: str | None = None
    srf: str | None = None
    q: str | None = None
    impedance_at_freq: str | None = None


@dataclass
class PhysicalVariant:
    """Physical package variant of a part."""
    package: str | None = None
    footprint: str | None = None
    body_size: str | None = None
    height: str | None = None
    pad_count: int | None = None
    pitch: str | None = None
    land_pattern_source: str | None = None
    verified: bool = False


@dataclass
class SupplierOffer:
    """A supplier's offer/listing for a part."""
    supplier: str  # digikey, mouser, lcsc, etc.
    supplier_pn: str | None = None
    stock: int | None = None
    price_breaks: list[dict[str, Any]] = field(default_factory=list)
    packaging: str | None = None
    moq: int | None = None
    last_checked: str | None = None


@dataclass
class DocumentRef:
    """Reference to a document (datasheet, app note, eval board BOM)."""
    doc_type: str  # datasheet, app_note, eval_bom, schematic
    local_path: str | None = None
    url: str | None = None
    sha256: str | None = None
    extracted_json_path: str | None = None
    source: str | None = None
    approved: bool = False


@dataclass
class SimulationModelRef:
    """Reference to a simulation model (SPICE, S-param, IBIS, etc.)."""
    model_type: str  # spice, s_parameter, ibis, touchstone, vendor_lib
    model_path: str | None = None
    frequency_range: str | None = None
    validity_notes: str | None = None
    source: str | None = None
    license_info: str | None = None


@dataclass
class PartRecord:
    """Full representation of a part in the FootFindr library system.

    This is the long-term data model. Not all fields are populated in the MVP,
    but the structure supports future supplier APIs, datasheets, inventory,
    RF models, and power libraries.
    """
    internal_pn: str
    category: ComponentCategory
    manufacturer: str | None = None
    mpn: str | None = None
    description: str | None = None
    value: str | None = None

    # Status and lifecycle
    status: PartStatus = PartStatus.APPROVED
    lifecycle: LifecycleStatus = LifecycleStatus.UNKNOWN
    approved: bool = True

    # Electrical
    specs: ElectricalSpecs = field(default_factory=ElectricalSpecs)

    # Physical
    package: str | None = None
    footprint: str | None = None
    physical_variants: list[PhysicalVariant] = field(default_factory=list)

    # Supplier
    supplier_pns: dict[str, str] = field(default_factory=dict)  # supplier -> pn
    supplier_offers: list[SupplierOffer] = field(default_factory=list)

    # Documents and models
    documents: list[DocumentRef] = field(default_factory=list)
    simulation_models: list[SimulationModelRef] = field(default_factory=list)

    # Provenance
    source_library: str | None = None
    source_vendor: str | None = None
    source_series: str | None = None
    source_pack: str | None = None
    source_file: str | None = None
    source_row: int | None = None
    promoted_at: str | None = None
    promoted_from: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Library metadata
# ---------------------------------------------------------------------------

@dataclass
class LibraryMetadata:
    """Metadata for a FootFindr library."""
    name: str
    kind: LibraryKind
    parent: str | None = None
    description: str | None = None
    active: bool = False
    parts_file: str | None = None  # path to YAML/JSON parts file
    created: str | None = None
    modified: str | None = None


# ---------------------------------------------------------------------------
# Decision — the output of the resolver for one component
# ---------------------------------------------------------------------------

@dataclass
class DecisionSource:
    """Where a requirement or selection came from."""
    field_name: str
    value: str
    source: str  # e.g. "KiCad Value field", "footfindr.yaml rails.+5V"


@dataclass
class Decision:
    """Full resolver decision for a single component."""
    ref: str
    status: DecisionStatus
    confidence: float = 0.0

    # What was selected
    selected_internal_pn: str | None = None
    selected_mpn: str | None = None
    selected_footprint: str | None = None

    # Field changes
    fields_to_write: dict[str, str] = field(default_factory=dict)
    old_fields: dict[str, str] = field(default_factory=dict)

    # Reasoning
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requirements: list[DecisionSource] = field(default_factory=list)
    candidate_summary: list[dict[str, Any]] = field(default_factory=list)

    # Provenance
    source_library: str | None = None
    applied: bool = False

    # Component info snapshot
    component_value: str | None = None
    component_category: str | None = None
