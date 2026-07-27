#!/usr/bin/env python3
"""
Unit tests for check-molecule-matrix-coverage.sh.

The script fails when a molecule scenario dir (ansible/roles/*/molecule/*/) or
an integration-test dir (ansible/integration-tests/*/) exists with no matching
entry in the molecule-tests / integration-tests parallel:matrix in
.gitlab-ci.yml. These tests drive it via subprocess inside a throwaway repo
layout, covering:

  - a molecule scenario dir missing from the matrix fails + names it
  - an integration-test dir missing from the matrix fails + names it
  - a matrix entry with no on-disk scenario does NOT fail (one-way check)

The script resolves the repo root from its own location, so each fixture test
runs against a copy of the script placed inside the fixture tree.

Run with pytest:
    python3 -m pytest tests/test_check_molecule_matrix_coverage.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-molecule-matrix-coverage.sh"
REPO_ROOT = Path(__file__).resolve().parent.parent

# Minimal matrix: one molecule scenario (alpha/default) and one integration
# test (stack-a). A fixture that adds an on-disk scenario beyond these must
# fail; one that matches must pass.
FIXTURE_CI = textwrap.dedent(
    """\
    molecule-tests:
      stage: test
      parallel:
        matrix:
          - ROLE: alpha
            SCENARIO: default

    integration-tests:
      stage: test
      parallel:
        matrix:
          - TEST:
              - stack-a
    """
)

MOLECULE_YML = "driver:\n  name: default\n"


def _scenario(repo: Path, role: str, scenario: str):
    d = repo / "ansible" / "roles" / role / "molecule" / scenario
    d.mkdir(parents=True, exist_ok=True)
    (d / "molecule.yml").write_text(MOLECULE_YML)


def _integration(repo: Path, name: str, scenario: str = "default"):
    d = repo / "ansible" / "integration-tests" / name / "molecule" / scenario
    d.mkdir(parents=True, exist_ok=True)
    (d / "molecule.yml").write_text(MOLECULE_YML)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "scripts" / "check-molecule-matrix-coverage.sh")
    (r / ".gitlab-ci.yml").write_text(FIXTURE_CI)
    # Baseline in-sync tree.
    _scenario(r, "alpha", "default")
    _integration(r, "stack-a")
    return r


def _run(repo: Path, env: dict | None = None):
    return subprocess.run(
        ["bash", "scripts/check-molecule-matrix-coverage.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def test_in_sync_fixture_passes(repo: Path):
    res = _run(repo)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_unlisted_molecule_scenario_fails(repo: Path):
    _scenario(repo, "beta", "default")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/roles/beta/molecule/default/" in res.stderr


def test_unlisted_scenario_of_listed_role_fails(repo: Path):
    """A second scenario of an already-listed role still needs its own entry."""
    _scenario(repo, "alpha", "extra")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/roles/alpha/molecule/extra/" in res.stderr


def test_unlisted_integration_test_fails(repo: Path):
    _integration(repo, "stack-b")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/integration-tests/stack-b/" in res.stderr


def test_matrix_entry_without_disk_scenario_does_not_fail(repo: Path):
    """One-way check: a matrix entry pointing at a non-existent scenario is the
    runtime-caught case (molecule errors), so this script must NOT fail on it."""
    ci = (repo / ".gitlab-ci.yml").read_text().replace(
        "      - ROLE: alpha\n        SCENARIO: default\n",
        "      - ROLE: alpha\n        SCENARIO: default\n"
        "      - ROLE: ghost\n        SCENARIO: default\n",
    )
    assert "ghost" in ci
    (repo / ".gitlab-ci.yml").write_text(ci)
    res = _run(repo)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_scenario_dir_without_molecule_yml_ignored(repo: Path):
    """A molecule/<dir> without a molecule.yml isn't a runnable scenario and
    must not trigger a failure (e.g. a stray shared dir)."""
    stray = repo / "ansible/roles/alpha/molecule/shared"
    stray.mkdir(parents=True)
    (stray / "README.md").write_text("not a scenario\n")
    res = _run(repo)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_role_without_any_molecule_scenario_fails(repo: Path):
    """A role dir with no molecule/ at all never appears in the scenario diff,
    so it needs its own check — it would otherwise ship permanently untested."""
    (repo / "ansible/roles/gamma/tasks").mkdir(parents=True)
    (repo / "ansible/roles/gamma/tasks/main.yml").write_text("---\n")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/roles/gamma/" in res.stderr
    assert "UNTESTED_ROLES" in res.stderr


def test_role_with_empty_molecule_dir_fails(repo: Path):
    """A molecule/ dir with no runnable scenario (no molecule.yml) counts as
    untested, same as no molecule/ at all."""
    d = repo / "ansible/roles/gamma/molecule/default"
    d.mkdir(parents=True)
    (d / "README.md").write_text("not a scenario\n")
    res = _run(repo)
    assert res.returncode == 1
    assert "ansible/roles/gamma/" in res.stderr


def test_allowlisted_untested_role_passes(repo: Path):
    """A role named in $UNTESTED_ROLES is exempt from the no-scenario check."""
    (repo / "ansible/roles/gamma/tasks").mkdir(parents=True)
    (repo / "ansible/roles/gamma/tasks/main.yml").write_text("---\n")
    res = _run(repo, {"UNTESTED_ROLES": "gamma"})
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_matrix_over_cap_fails(repo: Path):
    """The matrix cap keeps an aggregate job below GitLab's 50-needs limit."""
    entries = "".join(
        f"      - ROLE: r{i}\n        SCENARIO: default\n" for i in range(3)
    )
    ci = (repo / ".gitlab-ci.yml").read_text().replace(
        "      - ROLE: alpha\n        SCENARIO: default\n",
        "      - ROLE: alpha\n        SCENARIO: default\n" + entries,
    )
    assert "r0" in ci
    (repo / ".gitlab-ci.yml").write_text(ci)
    res = _run(repo, {"MAX_MATRIX_ENTRIES": "2"})
    assert res.returncode == 1
    assert "over the" in res.stderr and "MAX_MATRIX_ENTRIES" in res.stderr


def test_relocated_dirs_and_job_names(repo: Path, tmp_path: Path):
    """A consumer with different dirs/job names points the gate at its own."""
    ci = tmp_path / "custom-ci.yml"
    ci.write_text(
        FIXTURE_CI.replace("molecule-tests:", "role-tests:").replace(
            "integration-tests:", "stack-tests:"
        )
    )
    shutil.copy(ci, repo / "custom-ci.yml")
    shutil.move(str(repo / "ansible" / "roles"), str(repo / "collection-roles"))
    shutil.move(str(repo / "ansible" / "integration-tests"), str(repo / "stacks"))
    res = _run(
        repo,
        {
            "CI_FILE": "custom-ci.yml",
            "ROLES_DIR": "collection-roles",
            "INTEGRATION_DIR": "stacks",
            "MOLECULE_JOB": "role-tests",
            "INTEGRATION_JOB": "stack-tests",
        },
    )
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
