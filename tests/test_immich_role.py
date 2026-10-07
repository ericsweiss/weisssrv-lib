"""The immich negative-path guards, and the scenario that proves them.

Both guards fire from converge post_tasks, which molecule's idempotence step
re-runs — so the tag holding each to one junit testcase is part of the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "immich"
MAIN = ROLE / "tasks" / "main.yml"

SCENARIO = ROLE / "molecule" / "default"
CONVERGE = SCENARIO / "converge.yml"
VERIFY = SCENARIO / "verify.yml"
DECLARATION = SCENARIO / "expected-junit-failures.txt"

REAL_IP_ASSERT = "Validate the real-IP trust list resolved"
EXPORTER_ASSERT = "Validate the Postgres exporter image pin"
IDEMPOTENCE_SKIP = "molecule-idempotence-notest"

# guard -> (the var its `when` reads, the value the negative case sets it to,
# the verify task that reads the rescued marker)
GUARDS = {
    REAL_IP_ASSERT: (
        "immich_nginx_trust_no_proxy",
        False,
        "Assert an empty real-IP trust list fails the role",
    ),
    EXPORTER_ASSERT: (
        "immich_postgres_exporter_enabled",
        True,
        "Assert an unpinned postgres exporter image fails the role",
    ),
}

TASKS = [t for t in yaml.safe_load(MAIN.read_text()) if isinstance(t, dict)]


def _task(name: str) -> dict:
    matches = [t for t in TASKS if str(t.get("name", "")) == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r} in {MAIN}"
    return matches[0]


def _guard_names() -> list[str]:
    """The leading run of assert-only tasks: the role's whole validation gate."""
    names: list[str] = []
    for task in TASKS:
        if "ansible.builtin.assert" not in task:
            break
        names.append(str(task.get("name", "")))
    return names


def _negative_path_blocks() -> list[dict]:
    """The converge post_tasks that drive a guard to failure."""
    play = yaml.safe_load(CONVERGE.read_text())[0]
    blocks = [t for t in play["post_tasks"] if isinstance(t, dict) and "block" in t]
    assert len(blocks) == 2, f"expected two block/rescue negative cases in {CONVERGE}"
    return blocks


def _overrides(block: dict) -> dict:
    """The vars the negative case drives the role with, block- or task-level."""
    overrides = dict(block.get("vars", {}))
    for task in block["block"]:
        if isinstance(task, dict):
            overrides.update(task.get("vars", {}))
    return overrides


def _negative_case_for(variable: str) -> dict:
    matches = [b for b in _negative_path_blocks() if variable in _overrides(b)]
    assert len(matches) == 1, f"expected one negative case setting {variable!r}"
    return _overrides(matches[0])


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


def test_each_negative_case_is_skipped_on_the_idempotence_run() -> None:
    """Molecule's idempotence step re-runs converge, so an untagged negative
    block fires its guard twice per job and records two junit failures where
    the declaration allows one — a green molecule run, a red job."""
    for block in _negative_path_blocks():
        assert IDEMPOTENCE_SKIP in block.get("tags", []), block.get("name")


def test_the_declaration_allows_exactly_one_firing_per_guard() -> None:
    """The tag above is what makes a single declared occurrence true; a ` ::n`
    count here would instead pin the test sequence into the declaration."""
    assert _declared_lines() == [REAL_IP_ASSERT, EXPORTER_ASSERT]


def test_both_guards_run_before_anything_mutates() -> None:
    """The scenario re-includes the role in converge post_tasks; only a guard
    ahead of every mutating task leaves the converged host untouched, which is
    what keeps the idempotence run clean."""
    gate = _guard_names()
    assert REAL_IP_ASSERT in gate
    assert EXPORTER_ASSERT in gate


def test_each_negative_case_reaches_the_guard_it_targets() -> None:
    """Both guards are `when`-gated, so a negative case that leaves the gate at
    its default renders the whole declaration unobservable rather than red."""
    for guard, (variable, value, _) in GUARDS.items():
        assert variable in str(_task(guard)["when"])
        assert _negative_case_for(variable)[variable] is value


def test_each_negative_case_pins_the_guard_that_rejected_the_run() -> None:
    """A rescued `failed: true` is also true when the re-run dies of something
    else, so verify matches each guard's own message — which keeps the role's
    fail_msg and the scenario's expectation coupled."""
    for guard, (_, _, verify_name) in GUARDS.items():
        conditions = " ".join(_verify_task(verify_name)["ansible.builtin.assert"]["that"])
        needles = re.findall(r"'([^']+)'\s+in\s", conditions)
        assert needles, f"{verify_name} does not match the guard's message"
        fail_msg = " ".join(_task(guard)["ansible.builtin.assert"]["fail_msg"].split())
        for needle in needles:
            assert needle in fail_msg
