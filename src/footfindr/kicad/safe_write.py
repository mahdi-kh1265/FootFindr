"""Safe schematic write model for FootFindr.

Protection layer for all schematic file mutations:
  1. Snapshot hash/mtime at plan generation time
  2. Verify unchanged before applying
  3. Create timestamped backup
  4. Write to temp file first
  5. Validate parse of temp file
  6. Atomic replace (rename)

Does not do real-time live editing. All writes go through plan/apply.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("footfindr.kicad.safe_write")


@dataclass
class SchematicSnapshot:
    """Snapshot of schematic file state at a point in time."""
    path: str
    mtime: float
    sha256: str
    size: int
    taken_at: str  # ISO timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mtime": self.mtime,
            "sha256": self.sha256,
            "size": self.size,
            "taken_at": self.taken_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SchematicSnapshot:
        return cls(
            path=d["path"],
            mtime=d["mtime"],
            sha256=d["sha256"],
            size=d["size"],
            taken_at=d.get("taken_at", ""),
        )


class SchematicChangedError(Exception):
    """Raised when the schematic has changed since the snapshot."""


class SchematicWriteError(Exception):
    """Raised when a safe write fails."""


def snapshot_schematic(path: str | Path) -> SchematicSnapshot:
    """Take a snapshot of the schematic's current state."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Schematic not found: {p}")

    stat = p.stat()
    content = p.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return SchematicSnapshot(
        path=str(p.resolve()),
        mtime=stat.st_mtime,
        sha256=sha,
        size=stat.st_size,
        taken_at=now,
    )


def verify_unchanged(snapshot: SchematicSnapshot) -> bool:
    """Check if the schematic is unchanged since the snapshot.

    Returns True if unchanged, False if changed.
    """
    p = Path(snapshot.path)
    if not p.exists():
        return False

    stat = p.stat()
    # Quick check: size and mtime
    if stat.st_size != snapshot.size or stat.st_mtime != snapshot.mtime:
        # Hash check for definitive answer (mtime can change without content change)
        content = p.read_bytes()
        current_sha = hashlib.sha256(content).hexdigest()
        return current_sha == snapshot.sha256

    return True


def create_backup(path: str | Path) -> Path:
    """Create a timestamped backup of the schematic.

    Returns the backup path.
    """
    p = Path(path)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{p.stem}.{ts}.footfindr.bak{p.suffix}"
    backup_path = p.parent / backup_name
    shutil.copy2(str(p), str(backup_path))
    logger.info(f"Backup created: {backup_path}")
    return backup_path


def safe_write(
    path: str | Path,
    updates: dict[str, dict[str, str]],
    *,
    snapshot: SchematicSnapshot | None = None,
    force: bool = False,
    backup: bool = True,
) -> dict[str, str | None]:
    """Write schematic fields with full safety checks.

    Args:
        path: Path to the .kicad_sch file.
        updates: {ref: {field_name: new_value, ...}, ...}
        snapshot: Optional snapshot to verify unchanged.
        force: Allow overwriting non-empty Footprint fields.
        backup: Create backup before writing.

    Returns:
        {ref: error_message_or_None} — None means success.

    Raises:
        SchematicChangedError: If snapshot verification fails.
        SchematicWriteError: If the write or validation fails.
    """
    from footfindr.kicad.field_writer import KiCadFieldWriter
    from footfindr.kicad.schematic import KiCadSchematicReader

    p = Path(path)

    # Step 1: Verify unchanged since plan generation
    if snapshot and not force:
        if not verify_unchanged(snapshot):
            raise SchematicChangedError(
                f"Schematic changed since plan was generated "
                f"(snapshot taken at {snapshot.taken_at}).\n"
                f"Re-run the plan to avoid overwriting unsaved KiCad edits."
            )

    # Step 2: Create timestamped backup
    backup_path = None
    if backup and p.exists():
        backup_path = create_backup(p)

    # Step 3: Write to temp file
    original_text = p.read_text(encoding="utf-8")
    temp_dir = p.parent
    temp_fd = None
    temp_path = None

    try:
        temp_fd, temp_path_str = tempfile.mkstemp(
            suffix=".kicad_sch", prefix=".footfindr_tmp_",
            dir=str(temp_dir),
        )
        temp_path = Path(temp_path_str)
        # Close the file descriptor — we'll write via Path
        import os
        os.close(temp_fd)
        temp_fd = None

        # Write original content to temp
        temp_path.write_text(original_text, encoding="utf-8")

        # Apply field updates to temp file
        writer = KiCadFieldWriter()
        results = writer.update_fields(
            temp_path, updates, backup=False, force=force,
        )

        # Step 4: Validate parse of temp file
        reader = KiCadSchematicReader()
        try:
            validation_sch = reader.read(str(temp_path))
            if not validation_sch.symbols:
                raise SchematicWriteError(
                    "Validation failed: temp file parsed but has no symbols"
                )
        except Exception as e:
            raise SchematicWriteError(
                f"Validation failed: temp file does not parse correctly: {e}"
            )

        # Step 5: Atomic replace (rename temp → original)
        temp_path.replace(p)
        temp_path = None  # Already moved

        logger.info(f"Safe write completed: {p}")
        return results

    except (SchematicChangedError, SchematicWriteError):
        raise
    except Exception as e:
        # Restore from backup if available
        if backup_path and backup_path.exists():
            logger.warning(f"Write failed, restoring from backup: {e}")
            shutil.copy2(str(backup_path), str(p))
        raise SchematicWriteError(f"Write failed: {e}")
    finally:
        # Clean up temp file if still exists
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
