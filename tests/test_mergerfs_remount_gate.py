"""The MergerFS remount gate blocks the cycle without aborting the play.

A busy union skips the process probe by design; a probe that could not run at
all is the case the assert exists for.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
GATE = (REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nas_storage"
        / "tasks" / "mergerfs_remount_gate.yml")

BUSY = {"rc": 1, "stdout": "ACTIVE_NFS_CLIENTS"}
IDLE = {"rc": 0, "stdout": "NFS_IDLE"}
SKIPPED = {"skipped": True, "changed": False}
NEVER_RAN = {"failed": True, "msg": "could not transfer the probe"}


def _tasks() -> list:
    return yaml.safe_load(GATE.read_text(encoding="utf-8"))


def _assert_holds(nfs: list, process: list) -> bool:
    """Evaluate the gate's real assert conditions against fabricated registers."""
    task = next(t for t in _tasks() if "ansible.builtin.assert" in t)
    env = ansible_env(undefined=jinja2.ChainableUndefined)
    context = {
        "nas_storage_mergerfs_nfs_check": {"results": nfs},
        "nas_storage_mergerfs_process_check": {"results": process},
    }
    for condition in task["ansible.builtin.assert"]["that"]:
        if env.from_string("{{ (%s) | bool }}" % condition).render(**context) != "True":
            return False
    return True


def _safe_to_remount(nfs: list, process: list, unions: int) -> bool:
    task = next(t for t in _tasks()
                if t.get("name") == "Check if safe remount is possible")
    expression = task["ansible.builtin.set_fact"]["nas_storage_mergerfs_safe_to_remount"]
    env = ansible_env(undefined=jinja2.ChainableUndefined)
    rendered = env.from_string(expression).render(
        nas_storage_mergerfs_needs_remount=True,
        nas_storage_mergerfs_nfs_check={"results": nfs},
        nas_storage_mergerfs_process_check={"results": process},
        nas_storage_mergerfs_mounts=[{"name": "u%d" % i} for i in range(unions)],
    )
    return rendered.strip() == "True"


def test_a_busy_union_skips_the_process_probe_without_failing_the_play():
    """Every union busy is the normal state on a NAS serving NFS: the process
    probe is skipped for every item, and the storage deploy must continue."""
    assert _assert_holds([BUSY, BUSY], [SKIPPED, SKIPPED])


def test_a_busy_union_still_blocks_the_remount_cycle():
    assert _safe_to_remount([BUSY, BUSY], [SKIPPED, SKIPPED], unions=2) is False


def test_an_all_idle_run_may_remount():
    assert _assert_holds([IDLE, IDLE], [IDLE, IDLE])
    assert _safe_to_remount([IDLE, IDLE], [IDLE, IDLE], unions=2) is True


def test_a_probe_that_never_ran_still_fails_the_assert():
    """No rc and no skip: the script could not be transferred or executed, and
    counting that clean would unmount a union with live clients."""
    assert _assert_holds([IDLE, NEVER_RAN], [IDLE, IDLE]) is False
    assert _assert_holds([IDLE, IDLE], [IDLE, NEVER_RAN]) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
