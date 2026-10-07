#!/usr/bin/env python3
"""Unit tests for check-molecule-matrix-coverage.sh, both drift directions.

Each test drives the script by subprocess in a throwaway repo layout holding
its own copy of the script, which resolves the repo root from its location.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
SCRIPT = SCRIPTS_DIR / "check-molecule-matrix-coverage.sh"

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
    shutil.copy(SCRIPTS_DIR / "ci_yaml.py", r / "scripts" / "ci_yaml.py")
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


def test_stale_molecule_matrix_entry_fails(repo: Path):
    """A matrix entry whose scenario was renamed or deleted would fail the job at
    runtime, so the gate reports it here."""
    ci = (repo / ".gitlab-ci.yml").read_text().replace(
        "      - ROLE: alpha\n        SCENARIO: default\n",
        "      - ROLE: alpha\n        SCENARIO: default\n"
        "      - ROLE: ghost\n        SCENARIO: default\n",
    )
    assert "ghost" in ci
    (repo / ".gitlab-ci.yml").write_text(ci)
    res = _run(repo)
    assert res.returncode == 1
    assert "ROLE: ghost" in res.stderr
    assert "ansible/roles/ghost/molecule/default/" in res.stderr


def test_stale_integration_matrix_entry_fails(repo: Path):
    ci = (repo / ".gitlab-ci.yml").read_text().replace(
        "          - stack-a\n", "          - stack-a\n          - stack-gone\n"
    )
    assert "stack-gone" in ci
    (repo / ".gitlab-ci.yml").write_text(ci)
    res = _run(repo)
    assert res.returncode == 1
    assert "TEST: stack-gone" in res.stderr


def test_empty_ci_file_exits_2(repo: Path):
    """An empty or non-mapping CI file is an operator error, not a coverage
    finding, and must not surface as a traceback."""
    (repo / ".gitlab-ci.yml").write_text("# only a comment\n")
    res = _run(repo)
    assert res.returncode == 2
    assert "is not a YAML mapping" in res.stderr
    assert "Traceback" not in res.stderr


def test_gitlab_reference_tags_do_not_break_the_parse(repo: Path):
    ci = (repo / ".gitlab-ci.yml").read_text() + (
        "\nother-job:\n  stage: test\n  script: !reference [.base, script]\n"
    )
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


def test_roles_less_consumer_still_checks_the_integration_matrix(repo: Path):
    """ROLES_DIR="" turns the molecule half off; the integration half runs."""
    shutil.rmtree(repo / "ansible" / "roles")
    _integration(repo, "stack-b")
    res = _run(repo, {"ROLES_DIR": ""})
    assert res.returncode == 1
    assert "stack-b" in res.stderr


def test_roles_less_consumer_passes_when_the_integration_matrix_agrees(repo: Path):
    """No roles on disk and a matching integration matrix is a clean run."""
    shutil.rmtree(repo / "ansible" / "roles")
    res = _run(repo, {"ROLES_DIR": ""})
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "molecule half disabled" in res.stdout


def test_integration_less_consumer_passes_and_names_the_skipped_half(repo: Path):
    """INTEGRATION_DIR="" is the no-integration-suite declaration."""
    shutil.rmtree(repo / "ansible" / "integration-tests")
    (repo / ".gitlab-ci.yml").write_text(
        FIXTURE_CI.split("\nintegration-tests:")[0] + "\n"
    )
    res = _run(repo, {"INTEGRATION_DIR": ""})
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "integration half disabled" in res.stdout


def test_non_empty_but_missing_integration_dir_exits_2(repo: Path):
    """A renamed integration tree must not silently disable half the gate."""
    res = _run(repo, {"INTEGRATION_DIR": "collection-integration-tests"})
    assert res.returncode == 2
    assert "collection-integration-tests" in res.stderr
    assert "does not exist" in res.stderr


def test_disabling_both_halves_exits_2(repo: Path):
    res = _run(repo, {"ROLES_DIR": "", "INTEGRATION_DIR": ""})
    assert res.returncode == 2
    assert "check nothing" in res.stderr


def test_empty_subject_exits_2(repo: Path):
    """Both halves enabled, both trees empty of molecule.yml and both matrices
    empty: every comparison is trivially clean, so the gate must refuse."""
    shutil.rmtree(repo / "ansible" / "roles")
    shutil.rmtree(repo / "ansible" / "integration-tests")
    (repo / "ansible" / "roles").mkdir(parents=True)
    (repo / "ansible" / "integration-tests").mkdir(parents=True)
    (repo / ".gitlab-ci.yml").write_text(
        "molecule-tests:\n  stage: test\n\nintegration-tests:\n  stage: test\n"
    )
    res = _run(repo)
    assert res.returncode == 2, f"{res.stdout}\n{res.stderr}"
    assert "inspected nothing" in res.stderr


def test_empty_roles_tree_alone_is_not_vacuous(repo: Path):
    """An empty roles tree with a live integration half still has a subject."""
    shutil.rmtree(repo / "ansible" / "roles")
    (repo / "ansible" / "roles").mkdir(parents=True)
    (repo / ".gitlab-ci.yml").write_text(
        FIXTURE_CI.replace(
            "      - ROLE: alpha\n        SCENARIO: default\n", ""
        ).replace("  parallel:\n    matrix:\n\n", "")
    )
    res = _run(repo)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"


def test_non_empty_but_missing_roles_dir_still_exits_2(repo: Path):
    """A typo is not a declaration: a path that does not resolve is an error."""
    res = _run(repo, {"ROLES_DIR": "collection-roles"})
    assert res.returncode == 2
    assert "does not exist" in res.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
