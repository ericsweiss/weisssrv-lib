#!/usr/bin/env python3
"""The docs-link-check template's `check_paths` input turns on the checker's
backticked-path pass, and its `false` default leaves a pipeline-wide
CHECK_DOC_LINKS_PATHS in force.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from script_loader import REPO, SCRIPTS, load_path

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

TEMPLATE = REPO / "ci" / "lint" / "docs-link-check.yml"


def _job() -> dict:
    return ci_yaml.load_ci(TEMPLATE)["$[[ inputs.job_name ]]"]


def _script(check_paths: str) -> str:
    (step,) = _job()["script"]
    rendered = step.replace("$[[ inputs.check_paths ]]", check_paths)
    rendered = rendered.replace("$[[ inputs.script_path ]]", "scripts/check-doc-links.py")
    return rendered.replace("$[[ inputs.roots ]]", "")


def _switch_seen(tmp_path: Path, check_paths: str, inherited: str | None) -> str:
    """CHECK_DOC_LINKS_PATHS as the checker sees it."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "check-doc-links.py").write_text(
        "import os\nprint(os.environ.get('CHECK_DOC_LINKS_PATHS', '<unset>'))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CHECK_DOC_LINKS_PATHS"}
    if inherited is not None:
        env["CHECK_DOC_LINKS_PATHS"] = inherited
    result = subprocess.run(
        ["bash", "-c", "set -eo pipefail\n" + _script(check_paths)],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_input_is_a_boolean_that_defaults_off() -> None:
    check_paths = ci_yaml.spec_inputs(TEMPLATE.read_text())["check_paths"]
    assert check_paths["type"] == "boolean"
    assert check_paths["default"] is False


@pytest.mark.parametrize(
    ("check_paths", "inherited", "expected"),
    [
        ("true", None, "1"),
        ("false", None, "<unset>"),
        # A pipeline that sets the variable globally keeps it at the default.
        ("false", "1", "1"),
    ],
)
def test_the_switch_reaches_the_checker(
    tmp_path: Path, check_paths: str, inherited: str | None, expected: str
) -> None:
    assert _switch_seen(tmp_path, check_paths, inherited) == expected


def test_the_job_sets_no_variable_of_that_name() -> None:
    """A job-level entry would override the pipeline's global value."""
    variables = _job().get("variables") or {}
    assert "CHECK_DOC_LINKS_PATHS" not in variables


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
