#!/usr/bin/env python3
"""Repo invariant: docs/SCRIPTS.md's two promises stay true as scripts are added.

Both claims rot silently — a new script ships documented but ungated, or gated
but undocumented, and nothing notices until a consumer hits the gap. This is the
gate that keeps the sentence honest.

Run with pytest:
    python3 -m pytest tests/test_scripts_have_tests.py -v
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
TESTS = Path(__file__).resolve().parent
DOC = REPO / "docs" / "SCRIPTS.md"

SHIPPED = sorted(p for p in SCRIPTS.iterdir() if p.suffix in (".py", ".sh"))


def _suite_for(script: Path) -> Path:
    """tests/test_<script name with - and . as _>.py — the repo's convention."""
    return TESTS / ("test_%s.py" % script.stem.replace("-", "_"))


def test_there_are_scripts_to_check():
    """Guard the guard: a bad glob would make every assertion below vacuous."""
    assert len(SHIPPED) > 20


@pytest.mark.parametrize("script", SHIPPED, ids=lambda p: p.name)
def test_every_shipped_script_has_a_suite(script: Path):
    suite = _suite_for(script)
    assert suite.exists(), (
        "%s has no tests — add %s, or drop the script. docs/SCRIPTS.md tells "
        "consumers every script here is covered." % (script.name, suite.name)
    )


@pytest.mark.parametrize("script", SHIPPED, ids=lambda p: p.name)
def test_every_shipped_script_is_documented(script: Path):
    assert script.name in DOC.read_text(encoding="utf-8"), (
        "%s is not mentioned in docs/SCRIPTS.md" % script.name
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
