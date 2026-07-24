"""Shared pytest fixtures for the CLI tests.

Each test gets a fresh copy of the tenant scaffold (tests/fixtures/scaffold, a
faithful copy of the weisssrv-project-template tree) in a tmpdir, so edits never
touch the fixtures. The package is imported off the repo path (no install
needed), keeping the suite offline.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

# Make `weisssrv_lib_cli` importable without an install (cli/ on sys.path).
_CLI_ROOT = Path(__file__).resolve().parent.parent
if str(_CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLI_ROOT))

_FIXTURE = _CLI_ROOT / "tests" / "fixtures" / "scaffold"


@pytest.fixture()
def scaffold(tmp_path: Path) -> Path:
    """A throwaway copy of the tenant scaffold; returns its root."""
    dest = tmp_path / "project"
    shutil.copytree(_FIXTURE, dest)
    return dest
