"""The immich negative-path guards lead the role, ahead of every mutation."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "immich" / "tasks" / "main.yml"

REAL_IP_ASSERT = "Validate the real-IP trust list resolved"
EXPORTER_ASSERT = "Validate the Postgres exporter image pin"

TASKS = [t for t in yaml.safe_load(MAIN.read_text()) if isinstance(t, dict)]


def _guard_names() -> list[str]:
    """The leading run of assert-only tasks: the role's whole validation gate."""
    names: list[str] = []
    for task in TASKS:
        if "ansible.builtin.assert" not in task:
            break
        names.append(str(task.get("name", "")))
    return names


def test_both_guards_run_before_anything_mutates() -> None:
    """The scenario re-includes the role in converge post_tasks; only a guard
    ahead of every mutating task leaves the converged host untouched, which is
    what keeps the idempotence run clean."""
    gate = _guard_names()
    assert REAL_IP_ASSERT in gate
    assert EXPORTER_ASSERT in gate
