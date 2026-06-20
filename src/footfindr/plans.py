"""Plan/apply model for safe mutations.

Plans are generated for operations that mutate library or schematic files.
They are persisted at ``.footfindr/plans/`` and must be explicitly applied.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("footfindr.plans")


@dataclass
class PlanStep:
    """A single step in a plan."""
    operation: str          # "promote", "bind_footprint", "update_field"
    target_file: str        # path to file that would be modified
    target_key: str         # internal_pn, ref, etc.
    old_value: Any | None = None
    new_value: Any = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "operation": self.operation,
            "target_file": self.target_file,
            "target_key": self.target_key,
        }
        if self.old_value is not None:
            d["old_value"] = self.old_value
        if self.new_value is not None:
            d["new_value"] = self.new_value
        if self.reason:
            d["reason"] = self.reason
        if self.warnings:
            d["warnings"] = self.warnings
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanStep:
        return cls(
            operation=d.get("operation", ""),
            target_file=d.get("target_file", ""),
            target_key=d.get("target_key", ""),
            old_value=d.get("old_value"),
            new_value=d.get("new_value"),
            reason=d.get("reason"),
            warnings=d.get("warnings", []),
        )


@dataclass
class Plan:
    """A plan for a mutation operation."""
    plan_id: str
    operation: str            # "promote-supplier", "batch-promote", etc.
    created_at: str
    steps: list[PlanStep] = field(default_factory=list)
    constraint_check: dict | None = None
    collision_warnings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    status: str = "pending"   # "pending", "applied", "discarded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "operation": self.operation,
            "created_at": self.created_at,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "constraint_check": self.constraint_check,
            "collision_warnings": self.collision_warnings,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        return cls(
            plan_id=d.get("plan_id", ""),
            operation=d.get("operation", ""),
            created_at=d.get("created_at", ""),
            status=d.get("status", "pending"),
            steps=[PlanStep.from_dict(s) for s in d.get("steps", [])],
            constraint_check=d.get("constraint_check"),
            collision_warnings=d.get("collision_warnings", []),
            provenance=d.get("provenance", {}),
        )


class PlanManager:
    """Manage plan persistence and lifecycle."""

    def __init__(self, workspace: Path | None = None) -> None:
        from footfindr.config import get_workspace as _gw
        ws = Path(workspace) if workspace else _gw()
        self._plans_dir = ws / "plans"

    def _ensure_dir(self) -> None:
        self._plans_dir.mkdir(parents=True, exist_ok=True)
        # Add .gitignore if not present
        gitignore = self._plans_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    def create(self, plan: Plan) -> Path:
        """Save a new plan to disk. Returns the plan file path."""
        self._ensure_dir()
        filename = f"{plan.plan_id}.yaml"
        path = self._plans_dir / filename
        path.write_text(
            yaml.dump(plan.to_dict(), default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        logger.info(f"Plan saved: {path}")
        return path

    def load(self, plan_id: str) -> Plan | None:
        """Load a plan by ID."""
        path = self._plans_dir / f"{plan_id}.yaml"
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return Plan.from_dict(data)
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Failed to load plan {plan_id}: {e}")
            return None

    def load_latest(self) -> Plan | None:
        """Load the most recent plan."""
        plans = self.list_plans()
        if not plans:
            return None
        # Sort by plan_id (timestamp-based), take last
        plans.sort(key=lambda p: p.plan_id)
        return plans[-1]

    def list_plans(self) -> list[Plan]:
        """List all plans."""
        if not self._plans_dir.exists():
            return []
        plans = []
        for f in sorted(self._plans_dir.glob("*.yaml")):
            if f.name == ".gitignore":
                continue
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                plans.append(Plan.from_dict(data))
            except (yaml.YAMLError, OSError):
                continue
        return plans

    def apply(self, plan: Plan) -> None:
        """Apply a plan: execute its steps.

        Supports promote and update_schematic operations.
        """
        if plan.status != "pending":
            raise PlanError(f"Plan {plan.plan_id} is already {plan.status}")

        for step in plan.steps:
            if step.operation == "promote":
                self._apply_promote(step, plan.provenance)
            elif step.operation == "update_schematic":
                self._apply_update_schematic(step, plan.provenance)
            else:
                raise PlanError(f"Unknown operation: {step.operation}")

        plan.status = "applied"
        self.create(plan)  # overwrite with updated status

    def _apply_promote(self, step: PlanStep, provenance: dict) -> None:
        """Execute a promote step."""
        from footfindr.libraries.manager import LibraryManager
        from footfindr.libraries.promotion import promote_from_supplier_data

        target_library = provenance.get("target_library", "")
        internal_pn = step.target_key
        new_value = step.new_value

        if not isinstance(new_value, dict):
            raise PlanError(f"Plan step has no valid new_value dict")

        manager = LibraryManager()
        promote_from_supplier_data(
            data=new_value,
            target_library=target_library,
            manager=manager,
            internal_pn=internal_pn,
        )

    def _apply_update_schematic(self, step: PlanStep, provenance: dict) -> None:
        """Execute a schematic field update step using safe_write."""
        from footfindr.kicad.safe_write import (
            SchematicSnapshot, safe_write, SchematicChangedError,
        )

        ref = step.target_key
        updates = step.new_value
        sch_path = step.target_file

        if not isinstance(updates, dict):
            raise PlanError(f"Plan step has no valid new_value dict for schematic update")

        # Reconstruct snapshot from provenance for verification
        snapshot = None
        snap_data = provenance.get("schematic_snapshot")
        if snap_data:
            snapshot = SchematicSnapshot.from_dict(snap_data)

        try:
            results = safe_write(
                path=sch_path,
                updates={ref: updates},
                snapshot=snapshot,
                backup=True,
            )
            for r_ref, err in results.items():
                if err:
                    raise PlanError(f"Schematic update failed for {r_ref}: {err}")
            logger.info(f"Schematic updated: {ref} in {sch_path}")
        except SchematicChangedError as e:
            raise PlanError(f"Schematic changed since plan was created: {e}")


    def discard(self, plan: Plan) -> None:
        """Mark a plan as discarded."""
        if plan.status != "pending":
            raise PlanError(f"Plan {plan.plan_id} is already {plan.status}")
        plan.status = "discarded"
        self.create(plan)  # overwrite with updated status

    @staticmethod
    def generate_plan_id(operation: str = "promote") -> str:
        """Generate a timestamp-based plan ID."""
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{ts}_{operation}"


class PlanError(Exception):
    """Raised when plan operations fail."""


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------

@dataclass
class CollisionWarning:
    """A collision found during pre-promotion check."""
    collision_type: str   # "same_internal_pn", "same_mpn", "same_supplier_pn",
                          # "similar_family"
    existing_pn: str      # the conflicting internal_pn
    existing_mpn: str     # MPN of existing part
    message: str


def check_collisions(
    mpn: str,
    internal_pn: str,
    supplier_pn: str | None,
    target_library: str,
    manager,
) -> list[CollisionWarning]:
    """Check for collisions before promotion.

    Returns a list of collision warnings.
    """
    warnings: list[CollisionWarning] = []

    try:
        existing_parts = manager.load_approved_parts()
    except Exception:
        existing_parts = []

    for p in existing_parts:
        # Same internal PN
        if p.internal_pn == internal_pn:
            warnings.append(CollisionWarning(
                collision_type="same_internal_pn",
                existing_pn=p.internal_pn,
                existing_mpn=p.mpn or "",
                message=f"Internal PN '{internal_pn}' already exists",
            ))

        # Same MPN
        if p.mpn and p.mpn == mpn:
            warnings.append(CollisionWarning(
                collision_type="same_mpn",
                existing_pn=p.internal_pn,
                existing_mpn=p.mpn,
                message=f"MPN '{mpn}' already approved as '{p.internal_pn}'",
            ))

        # Same supplier PN
        if supplier_pn:
            for sup, spn in (p.supplier_pns or {}).items():
                if spn == supplier_pn:
                    warnings.append(CollisionWarning(
                        collision_type="same_supplier_pn",
                        existing_pn=p.internal_pn,
                        existing_mpn=p.mpn or "",
                        message=f"Supplier PN '{supplier_pn}' already linked to '{p.internal_pn}'",
                    ))

        # Similar family (shared MPN prefix ≥ 5 chars)
        if p.mpn and mpn and len(mpn) >= 5:
            prefix_len = min(len(mpn), len(p.mpn))
            common = 0
            for i in range(prefix_len):
                if mpn[i].upper() == p.mpn[i].upper():
                    common += 1
                else:
                    break
            if common >= 5 and p.mpn != mpn:
                warnings.append(CollisionWarning(
                    collision_type="similar_family",
                    existing_pn=p.internal_pn,
                    existing_mpn=p.mpn,
                    message=f"Similar MPN family: existing '{p.mpn}' ({p.internal_pn})",
                ))

    return warnings
