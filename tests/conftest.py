"""Shared pytest fixtures for FootFindr tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "examples"
SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


@pytest.fixture
def simple_schematic_path(tmp_path: Path) -> Path:
    """Copy the simple_board.kicad_sch fixture into a temp directory."""
    src = FIXTURES_DIR / "simple_board.kicad_sch"
    dst = tmp_path / "simple_board.kicad_sch"
    shutil.copy2(str(src), str(dst))
    return dst


@pytest.fixture
def approved_parts_path() -> Path:
    """Return path to the approved_parts.yaml seed file."""
    return SCHEMAS_DIR / "approved_parts.yaml"


@pytest.fixture
def approved_parts():
    """Load approved parts as PartRecord objects."""
    from footfindr.libraries.manager import LibraryManager

    mgr = LibraryManager()
    path = SCHEMAS_DIR / "approved_parts.yaml"
    if not path.exists():
        path = SCHEMAS_DIR / "approved_parts.example.yaml"
    return mgr._parse_approved_yaml(path)


@pytest.fixture
def config():
    """Load FootFindr config."""
    from footfindr.config import load_config
    return load_config()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temp workspace for library tests."""
    ws = tmp_path / ".footfindr"
    ws.mkdir()
    return ws
