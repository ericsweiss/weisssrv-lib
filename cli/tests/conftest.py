"""Shared pytest setup for the CLI tests.

The package is imported off the repo path (no install needed), keeping the suite
offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `weisssrv_lib_cli` importable without an install (cli/ on sys.path).
_CLI_ROOT = Path(__file__).resolve().parent.parent
if str(_CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLI_ROOT))
