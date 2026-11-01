"""The alloy_host credentials guard, and the scenario that proves it."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "alloy_host"
MAIN = ROLE / "tasks" / "main.yml"

CREDS_ASSERT = "Assert Loki push credentials are present"
FIRST_MUTATION = "Add Grafana signed APT repository"

SCENARIO = ROLE / "molecule" / "default"
CONVERGE = SCENARIO / "converge.yml"
VERIFY = SCENARIO / "verify.yml"
DECLARATION = SCENARIO / "expected-junit-failures.txt"
CREDS_VERIFY = "Assert the empty-credentials https run was rejected"
IDEMPOTENCE_SKIP = "molecule-idempotence-notest"

TASKS = [t for t in yaml.safe_load(MAIN.read_text()) if isinstance(t, dict)]
NAMES = [str(t.get("name", "")) for t in TASKS]


def _task(name: str) -> dict:
    matches = [t for t in TASKS if str(t.get("name", "")) == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r} in {MAIN}"
    return matches[0]


def _negative_path_block() -> dict:
    """The converge post_task that drives the credentials guard to failure."""
    play = yaml.safe_load(CONVERGE.read_text())[0]
    blocks = [t for t in play["post_tasks"] if isinstance(t, dict) and "block" in t]
    assert len(blocks) == 1, f"expected exactly one block/rescue in {CONVERGE}"
    return blocks[0]


def _verify_task(name: str) -> dict:
    tasks = yaml.safe_load(VERIFY.read_text())[0]["tasks"]
    matches = [t for t in tasks if str(t.get("name", "")) == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r} in {VERIFY}"
    return matches[0]


def _declared_lines() -> list[str]:
    return [
        line.strip()
        for line in DECLARATION.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_negative_case_is_skipped_on_the_idempotence_run() -> None:
    """Molecule's idempotence step re-runs converge, so an untagged negative
    block fires the guard twice per job and records two junit failures where
    the declaration below allows one — a green molecule run, a red job."""
    assert IDEMPOTENCE_SKIP in _negative_path_block().get("tags", [])


def test_the_declaration_allows_exactly_one_firing() -> None:
    """The tag above is what makes a single declared occurrence true; a ` ::n`
    count here would instead pin the test sequence into the declaration."""
    assert _declared_lines() == [CREDS_ASSERT]


def test_the_credentials_guard_runs_before_anything_mutates() -> None:
    """The scenario re-includes the role in converge post_tasks; only a guard
    ahead of every mutating task leaves the converged host untouched."""
    assert NAMES.index(CREDS_ASSERT) < NAMES.index(FIRST_MUTATION)


def test_the_negative_case_pins_the_guard_that_rejected_the_run() -> None:
    """A rescued `failed: true` is also true when the re-run dies of an apt
    error, so verify matches the guard's own message — which keeps the role's
    fail_msg and the scenario's expectation coupled."""
    conditions = " ".join(_verify_task(CREDS_VERIFY)["ansible.builtin.assert"]["that"])
    fail_msg = _task(CREDS_ASSERT)["ansible.builtin.assert"]["fail_msg"]
    for variable in ("alloy_host_loki_user", "alloy_host_loki_password"):
        assert variable in conditions
        assert variable in fail_msg
