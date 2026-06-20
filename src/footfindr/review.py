"""Project review engine for FootFindr.

Deterministic project intelligence: BOM checks, source-check,
cost rollup, assembly profile evaluation, packet generation.

All logic is read-only unless generate_fix_plan() is called.
Plans generated are conservative and require explicit apply.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from footfindr.assembly_profiles import (
    RISK_THRESHOLDS,
    AssemblyProfile,
    get_profile,
)
from footfindr.bom.models import ProjectIssue

logger = logging.getLogger("footfindr.review")


# ---------------------------------------------------------------------------
# Result data models
# ---------------------------------------------------------------------------

@dataclass
class SourceCheckResult:
    """Result of checking a single BOM line against supplier data."""
    ref: str
    mpn: str
    manufacturer: str
    risk_level: str               # "LOW", "MEDIUM", "HIGH", "BLOCKER"
    risk_codes: list[str] = field(default_factory=list)
    best_supplier: str | None = None
    best_stock: int = 0
    best_price: float | None = None
    best_price_qty: int | None = None
    supplier_count: int = 0
    has_jlc: bool = False
    moq: int | None = None
    lifecycle: str | None = None
    lead_time: str | None = None
    datasheet_url: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "ref": self.ref,
            "mpn": self.mpn,
            "manufacturer": self.manufacturer,
            "risk_level": self.risk_level,
            "risk_codes": self.risk_codes,
            "best_supplier": self.best_supplier,
            "best_stock": self.best_stock,
            "supplier_count": self.supplier_count,
            "has_jlc": self.has_jlc,
            "notes": self.notes,
        }
        if self.best_price is not None:
            d["best_price"] = self.best_price
        if self.best_price_qty is not None:
            d["best_price_qty"] = self.best_price_qty
        if self.moq is not None:
            d["moq"] = self.moq
        if self.lifecycle:
            d["lifecycle"] = self.lifecycle
        if self.lead_time:
            d["lead_time"] = self.lead_time
        if self.datasheet_url:
            d["datasheet_url"] = self.datasheet_url
        return d


@dataclass
class CostLine:
    """Cost information for a single BOM line."""
    ref: str
    mpn: str
    qty_per_board: int
    required_qty: int           # qty_per_board * build_qty
    unit_price: float | None = None
    extended_price: float | None = None
    supplier: str | None = None
    moq_warning: bool = False
    priced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "mpn": self.mpn,
            "qty_per_board": self.qty_per_board,
            "required_qty": self.required_qty,
            "unit_price": self.unit_price,
            "extended_price": self.extended_price,
            "supplier": self.supplier,
            "moq_warning": self.moq_warning,
            "priced": self.priced,
        }


@dataclass
class ProjectReviewResult:
    """Complete project review result."""
    project_name: str
    profile: str
    qty: int
    schematic_path: str
    timestamp: str
    ref_count: int = 0
    bom_line_count: int = 0
    resolved_count: int = 0
    missing_mpn_count: int = 0
    constraint_failure_count: int = 0
    supplier_risk_count: int = 0
    issues: list[ProjectIssue] = field(default_factory=list)
    source_checks: list[SourceCheckResult] = field(default_factory=list)
    cost_lines: list[CostLine] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    total_bom_cost: float | None = None
    priced_lines: int = 0
    unpriced_lines: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "profile": self.profile,
            "qty": self.qty,
            "schematic_path": self.schematic_path,
            "timestamp": self.timestamp,
            "summary": {
                "ref_count": self.ref_count,
                "bom_line_count": self.bom_line_count,
                "resolved_count": self.resolved_count,
                "missing_mpn_count": self.missing_mpn_count,
                "constraint_failure_count": self.constraint_failure_count,
                "supplier_risk_count": self.supplier_risk_count,
                "total_bom_cost": self.total_bom_cost,
                "priced_lines": self.priced_lines,
                "unpriced_lines": self.unpriced_lines,
            },
            "issues": [i.to_dict() for i in self.issues],
            "source_checks": [s.to_dict() for s in self.source_checks],
            "cost_lines": [c.to_dict() for c in self.cost_lines],
            "recommended_actions": self.recommended_actions,
        }


# ---------------------------------------------------------------------------
# Helper: select best price at quantity
# ---------------------------------------------------------------------------

def _best_price_at_qty(price_breaks: list, qty: int) -> float | None:
    """Select the best unit price for a given quantity from price breaks."""
    if not price_breaks:
        return None
    # Sort by quantity ascending
    sorted_pbs = sorted(price_breaks, key=lambda pb: pb.quantity)
    best = None
    for pb in sorted_pbs:
        if pb.quantity <= qty:
            best = pb.unit_price
    # If no tier covers the qty, use the smallest tier
    if best is None and sorted_pbs:
        best = sorted_pbs[0].unit_price
    return best


def _risk_level_from_codes(codes: list[str]) -> str:
    """Determine overall risk level from a list of risk codes."""
    if any(c in ("OBSOLETE",) for c in codes):
        return "BLOCKER"
    if any(c in ("NO_STOCK", "NRND", "NO_SUPPLIER_RESULT") for c in codes):
        return "HIGH"
    if any(c in ("LOW_STOCK", "NO_PRICE", "HIGH_MOQ", "LONG_LEAD_TIME",
                  "SINGLE_SOURCE") for c in codes):
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# ProjectReviewer — main engine
# ---------------------------------------------------------------------------

class ProjectReviewer:
    """Performs project review, BOM checks, source-check, and cost rollup.

    All operations are read-only. Plans are generated but not applied.
    """

    def __init__(
        self,
        schematic_path: str | Path,
        *,
        profile: str = "prototype",
        qty: int = 1,
        workspace: str | Path | None = None,
        project_name: str | None = None,
    ) -> None:
        self._schematic_path = Path(schematic_path)
        self._profile_name = profile
        self._qty = qty
        self._workspace = workspace
        self._project_name = project_name or self._schematic_path.stem

        # Lazy-loaded
        self._schematic = None
        self._approved_parts: list | None = None
        self._by_ipn: dict = {}
        self._by_mpn: dict = {}

    def _load_schematic(self):
        if self._schematic is not None:
            return self._schematic
        from footfindr.kicad.schematic import KiCadSchematicReader
        reader = KiCadSchematicReader()
        self._schematic = reader.read(str(self._schematic_path))
        return self._schematic

    def _load_approved(self) -> list:
        if self._approved_parts is not None:
            return self._approved_parts
        from footfindr.libraries.manager import LibraryManager
        mgr = LibraryManager(workspace=self._workspace)
        try:
            self._approved_parts = mgr.load_approved_parts()
        except Exception:
            self._approved_parts = []
        self._by_ipn = {}
        self._by_mpn = {}
        for p in self._approved_parts:
            self._by_ipn[p.internal_pn] = p
            if p.mpn and p.mpn != "TBD":
                self._by_mpn[p.mpn] = p
        return self._approved_parts

    # -------------------------------------------------------------------
    # BOM check — structural/design-field focused
    # -------------------------------------------------------------------

    def bom_check(
        self,
        *,
        check_constraints: bool = False,
        profile_name: str | None = None,
    ) -> list[ProjectIssue]:
        """Check BOM for missing fields, unapproved parts, constraint
        failures, and assembly profile warnings.

        Returns a list of ProjectIssue sorted by severity.
        """
        sch = self._load_schematic()
        self._load_approved()
        issues: list[ProjectIssue] = []
        prof_name = profile_name or self._profile_name

        # Load assembly profile (if available)
        try:
            a_profile = get_profile(prof_name)
        except KeyError:
            a_profile = None

        # Load constraints (if requested)
        constraint_mgr = None
        if check_constraints:
            from footfindr.constraints import ConstraintManager
            constraint_mgr = ConstraintManager(workspace=self._workspace)

        # Track seen IPNs for duplicate detection
        ipn_refs: dict[str, list[str]] = {}

        for sym in sch.symbols:
            if sym.dnp:
                continue

            ref = sym.ref
            fields = sym.fields
            ipn = fields.get("InternalPN", "")
            mpn = fields.get("MPN", "") or fields.get("mpn", "")
            manufacturer = fields.get("Manufacturer", "")
            footprint = sym.footprint or ""
            lcsc = fields.get("LCSC Part #", "") or fields.get("LCSC", "")
            package = fields.get("Package", "")

            # Track IPN usage
            if ipn:
                ipn_refs.setdefault(ipn, []).append(ref)

            # --- Missing fields ---
            if not mpn:
                issues.append(ProjectIssue(
                    severity="FAIL", code="MISSING_MPN", ref=ref,
                    field="MPN",
                    message=f"{ref}: missing MPN field.",
                    suggested_action=f"ff supplier search <query> -s dk",
                ))
            if not manufacturer:
                issues.append(ProjectIssue(
                    severity="WARN", code="MISSING_MANUFACTURER", ref=ref,
                    field="Manufacturer",
                    message=f"{ref}: missing Manufacturer field.",
                ))
            if not ipn:
                issues.append(ProjectIssue(
                    severity="WARN", code="MISSING_IPN", ref=ref,
                    field="InternalPN",
                    message=f"{ref}: missing InternalPN field.",
                    suggested_action="ff resolve all",
                ))
            if not footprint:
                issues.append(ProjectIssue(
                    severity="FAIL", code="MISSING_FOOTPRINT", ref=ref,
                    field="Footprint",
                    message=f"{ref}: missing Footprint.",
                ))

            # --- LCSC check (profile-dependent severity) ---
            if not lcsc:
                sev = "FAIL" if prof_name == "jlcpcb" else "WARN"
                issues.append(ProjectIssue(
                    severity=sev, code="MISSING_LCSC", ref=ref,
                    field="LCSC Part #",
                    message=f"{ref}: missing LCSC Part #.",
                    suggested_action="ff jlc check",
                    plan_available=(prof_name == "jlcpcb"),
                ))

            # --- Approved library check ---
            part = None
            if ipn and ipn in self._by_ipn:
                part = self._by_ipn[ipn]
            elif mpn and mpn in self._by_mpn:
                part = self._by_mpn[mpn]

            if not part and (ipn or mpn):
                issues.append(ProjectIssue(
                    severity="WARN", code="UNAPPROVED_PART", ref=ref,
                    field=None,
                    message=f"{ref}: part not in approved library "
                            f"(MPN={mpn or 'N/A'}, IPN={ipn or 'N/A'}).",
                ))

            if part:
                # Check deprecated/blocked
                status = getattr(part, "status", "approved")
                if status in ("deprecated", "blocked", "obsolete"):
                    issues.append(ProjectIssue(
                        severity="FAIL", code="DEPRECATED_PART", ref=ref,
                        field="status",
                        message=f"{ref}: part '{part.internal_pn}' is "
                                f"{status} in approved library.",
                    ))
                # Check field inconsistency: MPN mismatch
                if mpn and part.mpn and mpn != part.mpn and part.mpn != "TBD":
                    issues.append(ProjectIssue(
                        severity="WARN", code="FIELD_INCONSISTENCY", ref=ref,
                        field="MPN",
                        message=f"{ref}: schematic MPN '{mpn}' differs from "
                                f"library MPN '{part.mpn}' for IPN '{ipn}'.",
                    ))

            # --- Assembly profile package check ---
            if a_profile:
                pkg = package or footprint
                rule = a_profile.check_package(pkg)
                if rule:
                    # Check if constraint explicitly accepts this package
                    accepted = False
                    if constraint_mgr:
                        from footfindr.constraints import Constraint
                        cons = constraint_mgr.get_constraints_for(ref)
                        for c in cons:
                            if c.field == "package" and c.op == "eq":
                                if c.value.upper() in pkg.upper():
                                    accepted = True
                    if accepted:
                        issues.append(ProjectIssue(
                            severity="INFO", code="PROFILE_PACKAGE_ACCEPTED",
                            ref=ref, field="package",
                            message=f"{ref}: {rule.pattern} accepted by "
                                    f"explicit constraint. {rule.reason}",
                        ))
                    else:
                        issues.append(ProjectIssue(
                            severity=rule.severity,
                            code="PROFILE_PACKAGE_RISK",
                            ref=ref, field="package",
                            message=f"{ref}: {rule.reason}",
                        ))

            # --- Constraint checks ---
            if constraint_mgr:
                from footfindr.constraints import (
                    Constraint, check_constraint, _get_part_field,
                )
                cons = constraint_mgr.get_constraints_for(ref)
                for c in cons:
                    # Get actual value from schematic fields or approved part
                    actual = ""
                    field_map = {
                        "voltage": "VoltageRating",
                        "package": "Package",
                        "dielectric": "Dielectric",
                        "tolerance": "Tolerance",
                        "capacitance": "Value",
                        "resistance": "Value",
                    }
                    sch_field = field_map.get(c.field, c.field)
                    actual = fields.get(sch_field, "")

                    # Fall back to approved part
                    if not actual and part:
                        actual = _get_part_field(part, c.field)

                    if not actual and c.field == "package":
                        actual = package or footprint

                    result = check_constraint(c, actual)
                    if not result.passed and not result.is_soft:
                        issues.append(ProjectIssue(
                            severity="FAIL", code="CONSTRAINT_FAIL",
                            ref=ref, field=c.field,
                            message=f"{ref}: {result.message}",
                        ))
                    elif not result.passed and result.is_soft:
                        issues.append(ProjectIssue(
                            severity="WARN", code="CONSTRAINT_WARN",
                            ref=ref, field=c.field,
                            message=f"{ref}: {result.message}",
                        ))

        # --- Duplicate IPN detection ---
        for ipn, refs in ipn_refs.items():
            if len(refs) > 1:
                # This is normal grouping, but check for different values
                pass  # BOM grouping handles this; skip false positives

        # Sort by severity
        sev_order = {"BLOCKER": 0, "FAIL": 1, "WARN": 2, "INFO": 3}
        issues.sort(key=lambda i: (sev_order.get(i.severity, 9), i.ref or ""))
        return issues

    # -------------------------------------------------------------------
    # Source check — supplier risk assessment
    # -------------------------------------------------------------------

    def source_check(
        self,
        *,
        suppliers: list[str] | None = None,
        refresh: bool = False,
        cache_only: bool = True,
        qty: int | None = None,
    ) -> list[SourceCheckResult]:
        """Check each BOM line with MPN against supplier cache.

        Defaults to cache-only (no live API calls).
        """
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.registry import SupplierRegistry

        sch = self._load_schematic()
        build_qty = qty or self._qty
        reg = SupplierRegistry()
        cache = SupplierCache()

        supplier_names = suppliers or ["digikey", "mouser", "jlcpcb"]

        results: list[SourceCheckResult] = []

        # Group symbols by MPN to avoid duplicate lookups
        mpn_refs: dict[str, list[str]] = {}
        mpn_mfr: dict[str, str] = {}
        mpn_qty: dict[str, int] = {}

        for sym in sch.symbols:
            if sym.dnp:
                continue
            mpn = sym.fields.get("MPN", "") or sym.fields.get("mpn", "")
            if not mpn:
                results.append(SourceCheckResult(
                    ref=sym.ref, mpn="", manufacturer="",
                    risk_level="HIGH", risk_codes=["NO_SUPPLIER_RESULT"],
                    notes=["No MPN defined"],
                ))
                continue
            mpn_refs.setdefault(mpn, []).append(sym.ref)
            if mpn not in mpn_mfr:
                mpn_mfr[mpn] = (
                    sym.fields.get("Manufacturer", "")
                    or sym.fields.get("manufacturer", "")
                )
            mpn_qty[mpn] = mpn_qty.get(mpn, 0) + 1

        for mpn, refs in mpn_refs.items():
            manufacturer = mpn_mfr.get(mpn, "")
            qty_per_board = mpn_qty[mpn]
            required_qty = qty_per_board * build_qty

            all_entries = []
            found_suppliers: set[str] = set()

            for sname in supplier_names:
                norm_name = reg.normalize_name(sname)
                entries = cache.lookup(mpn, supplier=norm_name,
                                       manufacturer=manufacturer or None)

                if not entries and refresh and not cache_only:
                    provider = reg.get(norm_name)
                    if provider and provider.is_configured():
                        try:
                            result = provider.lookup_mpn(
                                mpn, manufacturer=manufacturer or None)
                            if result:
                                cache.store(result)
                                entries = [result]
                        except Exception as e:
                            logger.warning(
                                f"Live lookup failed for {mpn} on {sname}: {e}")

                for entry in entries:
                    all_entries.append(entry)
                    found_suppliers.add(entry.supplier)

            # Assess risk
            risk_codes: list[str] = []
            notes: list[str] = []
            best_stock = 0
            best_price: float | None = None
            best_supplier: str | None = None
            best_price_qty: int | None = None
            moq: int | None = None
            lifecycle: str | None = None
            lead_time: str | None = None
            datasheet_url: str | None = None
            has_jlc = False

            if not all_entries:
                risk_codes.append("NO_SUPPLIER_RESULT")
                notes.append(
                    f"No cached supplier data. Run: "
                    f"ff bom source-check -S {','.join(supplier_names)} -r")
            else:
                for entry in all_entries:
                    # Stock
                    stock = entry.stock or 0
                    if stock > best_stock:
                        best_stock = stock
                        best_supplier = entry.supplier

                    # Price
                    price = _best_price_at_qty(entry.price_breaks, required_qty)
                    if price is not None:
                        if best_price is None or price < best_price:
                            best_price = price
                            best_price_qty = required_qty
                            if not best_supplier:
                                best_supplier = entry.supplier

                    # MOQ
                    if entry.minimum_order_quantity:
                        if moq is None or entry.minimum_order_quantity < moq:
                            moq = entry.minimum_order_quantity

                    # Lifecycle
                    if entry.lifecycle:
                        lifecycle = entry.lifecycle

                    # Lead time
                    if entry.lead_time:
                        lead_time = entry.lead_time

                    # Datasheet
                    if entry.datasheet_url:
                        datasheet_url = entry.datasheet_url

                    # JLC
                    if entry.supplier in ("jlcpcb", "lcsc") or entry.lcsc_pn:
                        has_jlc = True

                # Risk code assessment
                if best_stock == 0:
                    risk_codes.append("NO_STOCK")
                elif best_stock < RISK_THRESHOLDS["low_stock"]:
                    risk_codes.append("LOW_STOCK")

                if lifecycle:
                    lc = lifecycle.lower()
                    if "obsolete" in lc:
                        risk_codes.append("OBSOLETE")
                    elif "nrnd" in lc or "not recommended" in lc:
                        risk_codes.append("NRND")

                if best_price is None:
                    risk_codes.append("NO_PRICE")

                if moq and moq > required_qty * RISK_THRESHOLDS["high_moq_multiplier"]:
                    risk_codes.append("HIGH_MOQ")
                    notes.append(f"MOQ={moq}, required={required_qty}")

                if len(found_suppliers) == 1:
                    risk_codes.append("SINGLE_SOURCE")

                if not has_jlc:
                    risk_codes.append("NO_JLC_MATCH")

                if not datasheet_url:
                    risk_codes.append("MISSING_DATASHEET")

                # Lead time check
                if lead_time:
                    _check_lead_time(lead_time, risk_codes)

            risk_level = _risk_level_from_codes(risk_codes)

            # Create one result per ref in this MPN group
            for r in refs:
                results.append(SourceCheckResult(
                    ref=r, mpn=mpn, manufacturer=manufacturer,
                    risk_level=risk_level, risk_codes=list(risk_codes),
                    best_supplier=best_supplier, best_stock=best_stock,
                    best_price=best_price, best_price_qty=best_price_qty,
                    supplier_count=len(found_suppliers), has_jlc=has_jlc,
                    moq=moq, lifecycle=lifecycle, lead_time=lead_time,
                    datasheet_url=datasheet_url, notes=list(notes),
                ))

        cache.close()
        return results

    # -------------------------------------------------------------------
    # Cost rollup
    # -------------------------------------------------------------------

    def cost_rollup(
        self,
        *,
        suppliers: list[str] | None = None,
        qty: int | None = None,
    ) -> tuple[list[CostLine], float | None]:
        """Calculate BOM cost estimate.

        Uses cached supplier data. Returns (cost_lines, total_cost).
        Total cost is None if no lines are priced.
        """
        from footfindr.suppliers.cache import SupplierCache
        from footfindr.suppliers.registry import SupplierRegistry

        sch = self._load_schematic()
        build_qty = qty or self._qty
        reg = SupplierRegistry()
        cache = SupplierCache()

        supplier_names = suppliers or ["digikey", "mouser", "jlcpcb"]

        # Group by MPN
        mpn_refs: dict[str, list[str]] = {}
        mpn_qty: dict[str, int] = {}
        mpn_mfr: dict[str, str] = {}

        for sym in sch.symbols:
            if sym.dnp:
                continue
            mpn = sym.fields.get("MPN", "") or sym.fields.get("mpn", "")
            if not mpn:
                continue
            mpn_refs.setdefault(mpn, []).append(sym.ref)
            mpn_qty[mpn] = mpn_qty.get(mpn, 0) + 1
            if mpn not in mpn_mfr:
                mpn_mfr[mpn] = (
                    sym.fields.get("Manufacturer", "")
                    or sym.fields.get("manufacturer", "")
                )

        cost_lines: list[CostLine] = []
        total = 0.0
        any_priced = False

        for mpn, refs in mpn_refs.items():
            qty_per_board = mpn_qty[mpn]
            required_qty = qty_per_board * build_qty
            manufacturer = mpn_mfr.get(mpn, "")

            best_price: float | None = None
            best_supplier: str | None = None
            moq_warning = False

            for sname in supplier_names:
                norm_name = reg.normalize_name(sname)
                entries = cache.lookup(mpn, supplier=norm_name,
                                       manufacturer=manufacturer or None)
                for entry in entries:
                    price = _best_price_at_qty(entry.price_breaks, required_qty)
                    if price is not None:
                        if best_price is None or price < best_price:
                            best_price = price
                            best_supplier = entry.supplier
                    if (entry.minimum_order_quantity
                            and entry.minimum_order_quantity > required_qty):
                        moq_warning = True

            ref_str = ", ".join(sorted(refs))
            priced = best_price is not None
            extended = best_price * required_qty if best_price is not None else None

            if priced and extended is not None:
                total += extended
                any_priced = True

            cost_lines.append(CostLine(
                ref=ref_str, mpn=mpn, qty_per_board=qty_per_board,
                required_qty=required_qty, unit_price=best_price,
                extended_price=extended, supplier=best_supplier,
                moq_warning=moq_warning, priced=priced,
            ))

        # Also add unpriced lines for refs with no MPN
        for sym in sch.symbols:
            if sym.dnp:
                continue
            mpn = sym.fields.get("MPN", "") or sym.fields.get("mpn", "")
            if not mpn:
                cost_lines.append(CostLine(
                    ref=sym.ref, mpn="(no MPN)", qty_per_board=1,
                    required_qty=build_qty, priced=False,
                ))

        cache.close()
        return cost_lines, total if any_priced else None

    # -------------------------------------------------------------------
    # Full review — combines all checks
    # -------------------------------------------------------------------

    def review(self) -> ProjectReviewResult:
        """Full project review: BOM check + source-check + cost rollup."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # BOM check
        issues = self.bom_check(check_constraints=True,
                                profile_name=self._profile_name)

        # Source check (cache-only)
        source_checks = self.source_check(cache_only=True, qty=self._qty)

        # Cost rollup (if qty provided)
        cost_lines: list[CostLine] = []
        total_cost: float | None = None
        if self._qty and self._qty >= 1:
            cost_lines, total_cost = self.cost_rollup(qty=self._qty)

        # Aggregate counts
        sch = self._load_schematic()
        non_dnp = [s for s in sch.symbols if not s.dnp]
        ref_count = len(non_dnp)

        # BOM line count (unique MPN groups)
        mpns = set()
        for s in non_dnp:
            mpn = s.fields.get("MPN", "") or s.fields.get("mpn", "")
            if mpn:
                mpns.add(mpn)
        bom_lines = len(mpns) + sum(
            1 for s in non_dnp
            if not (s.fields.get("MPN", "") or s.fields.get("mpn", ""))
        )

        missing_mpn = sum(1 for i in issues if i.code == "MISSING_MPN")
        resolved = ref_count - missing_mpn
        constraint_fails = sum(1 for i in issues if i.code == "CONSTRAINT_FAIL")
        supplier_risks = sum(
            1 for s in source_checks
            if s.risk_level in ("HIGH", "BLOCKER")
        )
        priced_lines = sum(1 for c in cost_lines if c.priced)
        unpriced_lines = len(cost_lines) - priced_lines

        # Recommended actions
        actions: list[str] = []
        if missing_mpn > 0:
            actions.append(
                f"Resolve {missing_mpn} refs with missing MPN: "
                f"ff resolve <schematic> all"
            )
        if constraint_fails > 0:
            actions.append(
                f"Review {constraint_fails} constraint failure(s): "
                f"ff con list"
            )
        no_cache = sum(
            1 for s in source_checks
            if "NO_SUPPLIER_RESULT" in s.risk_codes
        )
        if no_cache > 0:
            actions.append(
                f"Populate supplier cache for {no_cache} part(s): "
                f"ff bom source-check -S dk,mou,jlc -r"
            )
        if supplier_risks > 0:
            actions.append(
                f"Review {supplier_risks} high/blocker supplier risk(s): "
                f"ff bom source-check --cache"
            )
        if unpriced_lines > 0 and self._qty >= 1:
            actions.append(
                f"{unpriced_lines} BOM line(s) have no pricing data."
            )

        return ProjectReviewResult(
            project_name=self._project_name,
            profile=self._profile_name,
            qty=self._qty,
            schematic_path=str(self._schematic_path),
            timestamp=now,
            ref_count=ref_count,
            bom_line_count=bom_lines,
            resolved_count=resolved,
            missing_mpn_count=missing_mpn,
            constraint_failure_count=constraint_fails,
            supplier_risk_count=supplier_risks,
            issues=issues,
            source_checks=source_checks,
            cost_lines=cost_lines,
            recommended_actions=actions,
            total_bom_cost=total_cost,
            priced_lines=priced_lines,
            unpriced_lines=unpriced_lines,
        )

    # -------------------------------------------------------------------
    # Packet generation (markdown)
    # -------------------------------------------------------------------

    def generate_packet(
        self,
        *,
        out_path: str | Path,
        profile: str | None = None,
        qty: int | None = None,
    ) -> Path:
        """Generate a markdown review packet."""
        result = self.review()
        out = Path(out_path)

        lines: list[str] = []
        lines.append(f"# Project Review: {result.project_name}\n")
        lines.append(f"Generated: {result.timestamp}  ")
        lines.append(f"Profile: {result.profile}  ")
        lines.append(f"Build Qty: {result.qty}  ")
        lines.append(f"Schematic: `{result.schematic_path}`\n")

        # Summary table
        lines.append("## Summary\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Refs scanned | {result.ref_count} |")
        lines.append(f"| BOM lines | {result.bom_line_count} |")
        lines.append(f"| Resolved | {result.resolved_count} |")
        lines.append(f"| Missing MPN | {result.missing_mpn_count} |")
        lines.append(f"| Constraint failures | {result.constraint_failure_count} |")
        lines.append(f"| Supplier risks (HIGH+) | {result.supplier_risk_count} |")
        if result.total_bom_cost is not None:
            lines.append(f"| Estimated BOM cost | ${result.total_bom_cost:.2f} |")
            lines.append(f"| Priced / Unpriced | {result.priced_lines} / {result.unpriced_lines} |")
        lines.append("")

        # Top issues
        top_issues = [i for i in result.issues
                      if i.severity in ("BLOCKER", "FAIL")]
        if top_issues:
            lines.append("## Top Issues\n")
            for issue in top_issues[:20]:
                lines.append(f"- **{issue.severity}** `{issue.code}` "
                             f"{issue.ref or ''}: {issue.message}")
                if issue.suggested_action:
                    lines.append(f"  - Suggested: `{issue.suggested_action}`")
            lines.append("")

        # Warnings
        warns = [i for i in result.issues if i.severity == "WARN"]
        if warns:
            lines.append("## Warnings\n")
            for w in warns[:30]:
                lines.append(f"- **WARN** `{w.code}` "
                             f"{w.ref or ''}: {w.message}")
            if len(warns) > 30:
                lines.append(f"- ... and {len(warns) - 30} more")
            lines.append("")

        # Source risk table
        risky = [s for s in result.source_checks
                 if s.risk_level != "LOW"]
        if risky:
            lines.append("## Supplier Risk\n")
            lines.append("| Ref | MPN | Risk | Best Source | Stock | Price | Notes |")
            lines.append("|-----|-----|------|-------------|-------|-------|-------|")
            for s in risky:
                price_str = f"${s.best_price:.4f}" if s.best_price else "-"
                stock_str = f"{s.best_stock:,}" if s.best_stock else "0"
                notes = "; ".join(s.risk_codes[:3])
                lines.append(
                    f"| {s.ref} | {s.mpn[:25]} | {s.risk_level} | "
                    f"{s.best_supplier or '-'} | {stock_str} | "
                    f"{price_str} | {notes} |"
                )
            lines.append("")

        # Cost rollup
        if result.cost_lines and result.total_bom_cost is not None:
            lines.append("## Cost Rollup\n")
            lines.append(f"Qty: {result.qty} boards\n")
            lines.append(f"- Priced lines: {result.priced_lines}/{len(result.cost_lines)}")
            lines.append(f"- Unpriced lines: {result.unpriced_lines}")
            lines.append(f"- Estimated unit BOM cost: "
                         f"${result.total_bom_cost / result.qty:.2f}"
                         if result.qty > 0 else "")
            lines.append(f"- Estimated total build BOM: "
                         f"${result.total_bom_cost:.2f}")
            lines.append("")

        # Recommended actions
        if result.recommended_actions:
            lines.append("## Recommended Actions\n")
            for i, action in enumerate(result.recommended_actions, 1):
                lines.append(f"{i}. {action}")
            lines.append("")

        out.write_text("\n".join(lines), encoding="utf-8")
        return out

    # -------------------------------------------------------------------
    # Fix-plan generation (conservative)
    # -------------------------------------------------------------------

    def generate_fix_plan(self) -> Any:
        """Generate a conservative plan for safe fixes.

        Only includes:
        - LCSC exact match annotations
        - Missing constraint placeholders
        - Packet generation

        Does NOT include substitutions, promotions, or schematic rewrites.
        Returns Plan or None.
        """
        from footfindr.plans import Plan, PlanStep, PlanManager

        result = self.review()
        steps: list[PlanStep] = []

        # LCSC annotation for jlcpcb profile
        lcsc_missing = [i for i in result.issues if i.code == "MISSING_LCSC"]
        if lcsc_missing:
            steps.append(PlanStep(
                operation="recommend",
                target_file="(schematic)",
                target_key="jlc_annotation",
                new_value=f"{len(lcsc_missing)} refs missing LCSC Part #",
                reason="Run ff jlc check && ff jlc annotate --dry-run to "
                       "find exact LCSC matches.",
            ))

        # Generate review packet
        steps.append(PlanStep(
            operation="recommend",
            target_file="review.md",
            target_key="packet_generation",
            new_value="Generate project review packet",
            reason=f"ff proj packet --out review.md "
                   f"--profile {self._profile_name} --qty {self._qty}",
        ))

        if not steps:
            return None

        mgr = PlanManager(workspace=self._workspace)
        plan = Plan(
            plan_id=mgr.generate_plan_id("review-fix"),
            operation="review-fix",
            created_at=datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            steps=steps,
            provenance={
                "profile": self._profile_name,
                "qty": self._qty,
                "schematic": str(self._schematic_path),
                "issues_found": len(result.issues),
                "source": "ff proj review --fix-plan",
            },
        )
        mgr.create(plan)
        return plan


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _check_lead_time(lead_time_str: str, risk_codes: list[str]) -> None:
    """Parse lead time and add risk code if too long."""
    import re
    # Try to extract weeks or days
    m = re.search(r"(\d+)\s*(?:week|wk)", lead_time_str, re.IGNORECASE)
    if m:
        weeks = int(m.group(1))
        if weeks > (RISK_THRESHOLDS["long_lead_time_days"] // 7):
            risk_codes.append("LONG_LEAD_TIME")
        return
    m = re.search(r"(\d+)\s*(?:day|d\b)", lead_time_str, re.IGNORECASE)
    if m:
        days = int(m.group(1))
        if days > RISK_THRESHOLDS["long_lead_time_days"]:
            risk_codes.append("LONG_LEAD_TIME")
