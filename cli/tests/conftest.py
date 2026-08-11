"""Shared pytest fixtures for the CLI tests.

Each test gets a fresh copy of the tenant scaffold (tests/fixtures/scaffold) in a
tmpdir, so edits never touch the fixtures. The package is imported off the repo
path (no install needed), keeping the suite offline.

The fixture is byte-identical to the app template except for a stub README;
test_template_contract.py enforces that against a real checkout.
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

from weisssrv_lib_cli import tree  # noqa: E402  (needs the sys.path insert above)

_FIXTURE = _CLI_ROOT / "tests" / "fixtures" / "scaffold"

# The fixture lags the template between re-syncs. Tests that exercise CLI
# BEHAVIOUR against the opt-in manifest layout skip while it does, rather than
# failing about a copy of the template instead of the template: the drift itself
# is what test_template_contract.py's byte-identity gates report.
needs_optional_layout = pytest.mark.skipif(
    not (_FIXTURE / tree.FLUX_DIR / tree.OPTIONAL_DIR).is_dir(),
    reason=f"scaffold fixture predates {tree.FLUX_DIR}/{tree.OPTIONAL_DIR}/ "
    "(fixture re-sync pending)",
)


@pytest.fixture()
def scaffold(tmp_path: Path) -> Path:
    """A throwaway copy of the tenant scaffold; returns its root."""
    dest = tmp_path / "project"
    shutil.copytree(_FIXTURE, dest)
    return dest
