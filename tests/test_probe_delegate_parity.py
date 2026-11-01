"""The probe-delegate task files stay in step.

The two reachability pings are identical apart from their var prefix; all three
probes keep the single-delegate shape.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles"
TWINS = {
    "proxmox_ha": ROLES / "proxmox_ha" / "tasks" / "probe-delegate.yml",
    "proxmox_firewall": ROLES / "proxmox_firewall" / "tasks" / "probe-delegate.yml",
}
CLUSTER_PROBE = ROLES / "proxmox_vm" / "tasks" / "probe-cluster-resources-one.yml"
CLUSTER_PROBE_ENTRY = ROLES / "proxmox_vm" / "tasks" / "probe-cluster-resources.yml"


def normalised(text: str) -> str:
    """The task list, serialised with the role prefix erased."""
    tasks = yaml.safe_load(re.sub(r"_proxmox_(ha|firewall)_", "_ROLE_", text))
    return yaml.safe_dump(tasks, sort_keys=True)


def test_the_two_task_lists_are_identical_modulo_the_prefix():
    ha, firewall = (path.read_text(encoding="utf-8") for path in TWINS.values())
    assert normalised(ha) == normalised(firewall)


@pytest.mark.parametrize(
    "path", [*TWINS.values(), CLUSTER_PROBE], ids=lambda p: p.parent.parent.name
)
def test_each_probe_delegates_once_and_tolerates_an_unreachable_host(path):
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    delegated = [t for t in tasks if "delegate_to" in t]
    assert len(delegated) == 1, "exactly one delegated task per include"
    probe = delegated[0]
    assert "loop" not in probe, "a looped delegate_to ends the task sequence rc 0"
    assert probe["ignore_unreachable"] is True
    assert probe["failed_when"] is False


def test_the_cluster_probe_entry_point_fails_when_nothing_answered():
    """An empty answer must be terminal, not a green no-op."""
    tasks = yaml.safe_load(CLUSTER_PROBE_ENTRY.read_text(encoding="utf-8"))
    failures = [t for t in tasks if "ansible.builtin.fail" in t]
    assert len(failures) == 1
    assert "_proxmox_vm_cluster_json | length == 0" in failures[0]["when"]
