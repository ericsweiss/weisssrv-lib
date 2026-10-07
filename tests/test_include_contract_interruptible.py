#!/usr/bin/env python3
"""The set of CI templates that set `interruptible: false` matches the table in
docs/INCLUDE-CONTRACT.md, so a consumer can read the opt-outs instead of
guessing them from the job templates.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Set

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "INCLUDE-CONTRACT.md"
CI = REPO / "ci"

sys.path.insert(0, str(REPO / "scripts"))
import yaml  # noqa: E402

from ci_yaml import NullTagCILoader as CILoader  # noqa: E402

TABLE_HEADING = "### Jobs that opt out of `interruptible`"


def templates_setting_false() -> Dict[str, Set[str]]:
    """Consumer-facing template -> job names that set `interruptible: false`.

    `ci/internal/` carries no behaviour guarantee for consumers and the
    `*.example.*` files are vendored Actions workflows, not includes.
    """
    found: Dict[str, Set[str]] = {}
    for path in sorted({p for suffix in ("yml", "yaml") for p in CI.rglob(f"*.{suffix}")}):
        relative = path.relative_to(REPO).as_posix()
        if relative.startswith("ci/internal/") or ".example." in path.name:
            continue
        jobs = set()
        for document in yaml.load_all(path.read_text(), Loader=CILoader):
            for name, body in (document or {}).items():
                if isinstance(body, dict) and body.get("interruptible") is False:
                    jobs.add(str(name))
        if jobs:
            found[relative] = jobs
    return found


def documented(doc_text: str) -> Set[str]:
    """Template paths in the opt-out table's first column."""
    after = doc_text.split(TABLE_HEADING, 1)
    if len(after) == 1:
        return set()
    # The table ends at the next heading of any level.
    body = re.split(r"^#", after[1], flags=re.M)[0]
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|", body, flags=re.M)
    return set(rows)


def test_there_are_opt_outs_to_check() -> None:
    """Vacuity guard: an empty discovery would make the comparison pass."""
    assert len(templates_setting_false()) > 2


def test_table_matches_the_templates() -> None:
    actual = set(templates_setting_false())
    listed = documented(DOC.read_text())
    assert actual == listed, (
        "docs/INCLUDE-CONTRACT.md § Jobs that opt out of `interruptible` is out "
        "of step with ci/. Only in the templates: %s. Only in the table: %s."
        % (sorted(actual - listed) or "none", sorted(listed - actual) or "none")
    )


def test_a_missing_row_is_reported() -> None:
    """The gate can FAIL: drop a row and the comparison no longer holds."""
    doc_text = DOC.read_text()
    one = sorted(documented(doc_text))[0]
    mutated = re.sub(r"^\|\s*`%s`\s*\|.*$" % re.escape(one), "", doc_text, flags=re.M)
    assert documented(mutated) == documented(doc_text) - {one}
    assert set(templates_setting_false()) != documented(mutated)
