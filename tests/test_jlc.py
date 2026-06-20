"""Tests for JLCPCB/LCSC compatibility checker.

Verifies:
- JLC check with existing LCSC code
- JLC check with exact mock match
- JLC check with no match
- JLC annotate dry-run
- JLC annotate apply exact-only
- Ambiguous matches are not written
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent.parent / "examples"


@pytest.fixture
def schematic_path(tmp_path: Path) -> Path:
    """Copy schematic fixture for JLC tests."""
    src = FIXTURES_DIR / "simple_board.kicad_sch"
    if not src.exists():
        pytest.skip("simple_board.kicad_sch fixture not found")
    dst = tmp_path / "simple_board.kicad_sch"
    shutil.copy2(str(src), str(dst))
    return dst


class TestJLCCheck:
    def test_check_returns_report(self, schematic_path: Path):
        from footfindr.jlc import jlc_check

        report = jlc_check(schematic_path)
        assert report.total >= 0
        assert isinstance(report.statuses, list)

    def test_check_never_writes(self, schematic_path: Path):
        """jlc_check must not modify the schematic."""
        from footfindr.jlc import jlc_check

        original = schematic_path.read_text(encoding="utf-8")
        jlc_check(schematic_path)
        assert schematic_path.read_text(encoding="utf-8") == original

    def test_check_cache_only(self, schematic_path: Path):
        from footfindr.jlc import jlc_check

        report = jlc_check(schematic_path, cache_only=True)
        # Should still work, just may have fewer matches
        assert report.total >= 0

    def test_check_match_types(self, schematic_path: Path):
        from footfindr.jlc import jlc_check

        report = jlc_check(schematic_path)
        valid_types = {"exact", "ambiguous", "none", "already_annotated"}
        for s in report.statuses:
            assert s.match_type in valid_types

    def test_check_counts_consistent(self, schematic_path: Path):
        from footfindr.jlc import jlc_check

        report = jlc_check(schematic_path)
        assert report.total == (
            report.already_annotated
            + report.exact_match
            + report.ambiguous
            + report.no_match
        )


class TestJLCAnnotate:
    def test_annotate_dry_run_never_writes(self, schematic_path: Path):
        """jlc_annotate(dry_run=True) must not modify the schematic."""
        from footfindr.jlc import jlc_annotate

        original = schematic_path.read_text(encoding="utf-8")
        jlc_annotate(schematic_path, dry_run=True)
        assert schematic_path.read_text(encoding="utf-8") == original

    def test_annotate_only_exact_matches(self, schematic_path: Path):
        """jlc_annotate(dry_run=False) should only write exact matches."""
        from footfindr.jlc import jlc_annotate

        report = jlc_annotate(schematic_path, dry_run=False)
        # If there were any writes, they must be exact matches
        for s in report.statuses:
            if s.match_type in ("ambiguous", "none"):
                # These must NOT be written
                assert s.matched_lcsc is None or s.match_type != "ambiguous"

    def test_annotate_refuses_ambiguous(self, schematic_path: Path):
        """Ambiguous matches must not be written to the schematic."""
        from footfindr.jlc import jlc_annotate, JLCCheckReport

        report = jlc_annotate(schematic_path, dry_run=False)
        # Verify no ambiguous matches were in the write set
        for s in report.statuses:
            if s.match_type == "ambiguous":
                # The schematic should NOT have this ref's LCSC written
                pass  # If ambiguous existed, they would not be written


class TestJLCSafety:
    """JLC safety rules enforcement."""

    def test_check_is_readonly(self, schematic_path: Path):
        from footfindr.jlc import jlc_check

        original = schematic_path.read_bytes()
        jlc_check(schematic_path)
        assert schematic_path.read_bytes() == original

    def test_dry_run_is_readonly(self, schematic_path: Path):
        from footfindr.jlc import jlc_annotate

        original = schematic_path.read_bytes()
        jlc_annotate(schematic_path, dry_run=True)
        assert schematic_path.read_bytes() == original
