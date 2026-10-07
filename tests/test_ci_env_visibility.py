"""$CI reaches a test body, so require_tool/require_tags fail rather than skip.

conftest's autouse scrub is function-scoped, so listing CI there would hide it
from every function-scoped fixture and turn each tool-driven gate into a skip.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from _helpers import require_tool
from script_loader import load_path

# Loaded by path: a bare `import conftest` resolves to cli/tests' conftest when
# the whole suite runs in one session, and that module has no CI_ENV.
CONFTEST = load_path(Path(__file__).with_name("conftest.py"))

MISSING = "definitely-not-a-binary"
GATE = "conftest CI-scrub regression"


def test_the_ci_scrub_leaves_ci_alone():
    assert "CI" not in CONFTEST.CI_ENV, (
        "require_tool/require_tags read $CI to choose fail-vs-skip; scrubbing it "
        "makes every tool-driven gate skip silently in CI"
    )


@pytest.fixture(scope="module", autouse=True)
def _ambient_ci():
    """CI as a job sets it: in the real environment, not via monkeypatch."""
    previous = os.environ.get("CI")
    os.environ["CI"] = "true"
    yield
    if previous is None:
        del os.environ["CI"]
    else:
        os.environ["CI"] = previous


@pytest.fixture
def gate_outcome():
    """require_tool's outcome from inside a function-scoped fixture."""
    try:
        require_tool(MISSING, GATE, "Install it in the job.")
    except pytest.fail.Exception:
        return "failed"
    except pytest.skip.Exception:
        return "skipped"
    return "passed"


def test_require_tool_fails_loudly_when_ci_is_set(gate_outcome):
    assert gate_outcome == "failed", (
        "require_tool %s with $CI set — a function-scoped fixture no longer sees "
        "CI, so tool-driven gates certify checks they never ran" % gate_outcome
    )
