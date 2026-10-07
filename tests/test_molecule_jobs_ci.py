"""The molecule job passes --strict to the junit sanitizer.

Without it a stale expectation degrades to a warning, and every
expected-junit-failures.txt becomes an unpoliced downgrade list.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from script_loader import SCRIPTS, load_path

REPO = Path(__file__).resolve().parent.parent
CI_FILE = REPO / ".gitlab" / "ci" / "molecule-jobs.gitlab-ci.yml"

# The file carries `!reference`, which yaml.safe_load cannot read.
ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

SANITIZER = "sanitize-junit-expected-failures.py"


def _script_lines() -> list[str]:
    doc = ci_yaml.parse_ci(CI_FILE.read_text(encoding="utf-8"))
    lines: list[str] = []
    for job in doc.values():
        if not isinstance(job, dict):
            continue
        for key in ("before_script", "script", "after_script"):
            part = job.get(key)
            if isinstance(part, list):
                lines.extend(str(x) for x in part)
            elif isinstance(part, str):
                lines.append(part)
    return lines


def _sanitizer_invocations(lines: list[str]) -> list[str]:
    return [line for line in lines if SANITIZER in line]


def test_the_sanitizer_is_invoked_at_all() -> None:
    """A vacuity guard: no invocation would satisfy the strict check trivially."""
    assert _sanitizer_invocations(_script_lines())


def test_every_sanitizer_invocation_is_strict() -> None:
    found = _sanitizer_invocations(_script_lines())
    assert all("--strict" in line for line in found)


def test_the_strict_assertion_is_load_bearing() -> None:
    """Mutation: dropping the flag must be reported."""
    stripped = [line.replace("--strict", "") for line in _script_lines()]
    found = _sanitizer_invocations(stripped)
    assert found
    assert not all("--strict" in line for line in found)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
