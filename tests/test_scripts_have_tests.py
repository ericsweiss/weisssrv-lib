#!/usr/bin/env python3
"""Repo invariant: docs/SCRIPTS.md's two promises stay true as scripts are added.

Both claims rot silently — a new script ships documented but ungated, or gated
but undocumented, and nothing notices until a consumer hits the gap. This is the
gate that keeps the sentence honest.
"""

import os
import re
from pathlib import Path
from typing import Dict, List

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
TESTS = Path(__file__).resolve().parent
DOC = REPO / "docs" / "SCRIPTS.md"

# Files under scripts/ that ship without a suite on purpose. Keyed by path
# relative to scripts/, valued by the reason. Empty today, and deliberately an
# explicit list rather than a filter: exempting something has to be a visible
# edit that a reviewer sees, not a side effect of how a file is named.
EXEMPT: Dict[str, str] = {}


def collect(root: Path) -> List[Path]:
    """Every file under `root` this gate claims to cover.

    Recursive and NOT extension-keyed: a script added as `scripts/foo` (no
    suffix — the normal shape for an executable carrying a shebang),
    `scripts/foo.bash`, `scripts/foo.pl` or `scripts/lib/helper.sh` is exactly as
    shipped, and exactly as uncovered, as a `.py`. An extension filter made all
    of those invisible to the assertions below while docs/SCRIPTS.md kept
    claiming universal coverage. `.py`/`.sh` are kept alongside the executable
    bit because three of this repo's own scripts are vendored, not run in place,
    and carry mode 644.
    """
    found = []
    for path in sorted(root.rglob("*")):
        # Generated, not shipped.
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if path.relative_to(root).as_posix() in EXEMPT:
            continue
        if os.access(path, os.X_OK) or path.suffix in (".py", ".sh"):
            found.append(path)
    return found


SHIPPED = collect(SCRIPTS)


def _suite_for(script: Path) -> Path:
    """tests/test_<script name with - and . as _>.py — the repo's convention."""
    return TESTS / ("test_%s.py" % script.stem.replace("-", "_"))


def test_there_are_scripts_to_check():
    """Guard the guard: a bad walk would make every assertion below vacuous."""
    assert len(SHIPPED) > 20


def test_the_collector_sees_every_kind_of_script_it_claims_to_cover(tmp_path):
    """The scope hole this gate had, pinned: an extensionless executable, a
    nested helper and a non-.py/.sh executable were all invisible, so the first
    script shipped in any of those shapes would have gone untested and
    undocumented with the gate still green."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "__pycache__").mkdir()
    for name, mode in (
        ("tool.py", 0o644),  # vendored, not run in place
        ("lib/helper.sh", 0o755),
        ("bare-tool", 0o755),  # shebang, no extension
        ("hook.pl", 0o755),
        ("README.md", 0o644),  # not a script
        ("__pycache__/tool.cpython-313.pyc", 0o755),  # generated
    ):
        target = tmp_path / name
        target.write_text("")
        target.chmod(mode)

    assert {p.name for p in collect(tmp_path)} == {
        "tool.py",
        "helper.sh",
        "bare-tool",
        "hook.pl",
    }


def test_an_exemption_is_an_explicit_edit(tmp_path):
    """Opting a file out is possible, but only by naming it (with a reason)."""
    script = tmp_path / "unloved.sh"
    script.write_text("")
    assert [p.name for p in collect(tmp_path)] == ["unloved.sh"]
    EXEMPT["unloved.sh"] = "test fixture"
    try:
        assert collect(tmp_path) == []
    finally:
        del EXEMPT["unloved.sh"]


@pytest.mark.parametrize("script", SHIPPED, ids=lambda p: p.name)
def test_every_shipped_script_has_a_suite(script: Path):
    suite = _suite_for(script)
    assert suite.exists(), (
        "%s has no tests — add %s, or drop the script. docs/SCRIPTS.md tells "
        "consumers every script here is covered." % (script.name, suite.name)
    )


@pytest.mark.parametrize("script", SHIPPED, ids=lambda p: p.name)
def test_every_suite_actually_exercises_its_script(script: Path):
    """Existence certifies a filename, not coverage: `def test_placeholder: pass`
    in a correctly-named file satisfies the check above and proves nothing. Make
    the suite show it at least reaches for the script it is named after."""
    suite = _suite_for(script)
    if not suite.exists():
        pytest.fail("%s has no suite at all — see the existence check." % script.name)
    body = suite.read_text(encoding="utf-8")
    assert script.name in body, (
        "%s exists but never names %s — a placeholder suite satisfies the "
        "existence check while proving nothing." % (suite.name, script.name)
    )
    assert re.search(r"^\s*def test_", body, re.M), "%s defines no tests" % suite.name


@pytest.mark.parametrize("script", SHIPPED, ids=lambda p: p.name)
def test_every_shipped_script_is_documented(script: Path):
    assert script.name in DOC.read_text(encoding="utf-8"), (
        "%s is not mentioned in docs/SCRIPTS.md" % script.name
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
