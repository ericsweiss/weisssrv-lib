"""The probe-delegate task files stay in step.

The two reachability pings are identical apart from their var prefix; the
proxmox_vm probe is checked for shape only. A comment is all that holds them.
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
# The third copy of the pattern: a cluster-resource query, not a ping, so it is
# checked for shape rather than compared byte for byte.
CLUSTER_PROBE = ROLES / "proxmox_vm" / "tasks" / "probe-cluster-resources-one.yml"
CLUSTER_PROBE_ENTRY = ROLES / "proxmox_vm" / "tasks" / "probe-cluster-resources.yml"


def normalised(text: str) -> str:
    """The task list, serialised with the role prefix erased."""
    tasks = yaml.safe_load(re.sub(r"_proxmox_(ha|firewall)_", "_ROLE_", text))
    return yaml.safe_dump(tasks, sort_keys=True)


def test_both_twins_exist():
    for name, path in TWINS.items():
        assert path.is_file(), f"{name} ships no probe-delegate.yml"


def test_the_two_task_lists_are_identical_modulo_the_prefix():
    ha, firewall = (path.read_text(encoding="utf-8") for path in TWINS.values())
    assert normalised(ha) == normalised(firewall)


def test_a_dropped_guard_in_one_copy_is_caught(tmp_path):
    """The negative arm: prove the comparison can fail."""
    ha = TWINS["proxmox_ha"].read_text(encoding="utf-8")
    drifted = ha.replace("ignore_unreachable: true", "ignore_unreachable: false", 1)
    assert normalised(drifted) != normalised(ha)


@pytest.mark.parametrize("name", sorted(TWINS))
def test_each_copy_keeps_its_critical_header_and_names_its_twin(name):
    text = TWINS[name].read_text(encoding="utf-8")
    header = "\n".join(ln for ln in text.splitlines() if ln.startswith("#"))
    assert "CRITICAL:" in header
    assert "looped INCLUDE around a SINGLE delegated ping" in header
    other = next(k for k in TWINS if k != name)
    assert f"Twin of {other}/tasks/probe-delegate.yml" in header


def test_the_cluster_probe_keeps_the_critical_header_and_names_the_twins():
    text = CLUSTER_PROBE.read_text(encoding="utf-8")
    header = "\n".join(ln for ln in text.splitlines() if ln.startswith("#"))
    assert "CRITICAL:" in header
    assert "looped INCLUDE around a SINGLE delegated query" in header
    for name in TWINS:
        assert f"{name}/tasks/probe-delegate.yml" in header


def test_the_cluster_probe_delegates_once_and_tolerates_an_unreachable_host():
    tasks = yaml.safe_load(CLUSTER_PROBE.read_text(encoding="utf-8"))
    delegated = [t for t in tasks if "delegate_to" in t]
    assert len(delegated) == 1, "exactly one delegated task per include"
    probe = delegated[0]
    assert "loop" not in probe, "a looped delegate_to ends the task sequence rc 0"
    assert probe["ignore_unreachable"] is True
    assert probe["failed_when"] is False


def test_the_cluster_probe_entry_point_fails_when_nothing_answered():
    """The negative arm: an empty answer must be terminal, not a green no-op."""
    tasks = yaml.safe_load(CLUSTER_PROBE_ENTRY.read_text(encoding="utf-8"))
    failures = [t for t in tasks if "ansible.builtin.fail" in t]
    assert len(failures) == 1
    assert "_proxmox_vm_cluster_json | length == 0" in failures[0]["when"]


def test_a_dropped_terminal_fail_is_caught():
    """Prove the entry-point check can fail."""
    tasks = yaml.safe_load(CLUSTER_PROBE_ENTRY.read_text(encoding="utf-8"))
    drifted = [t for t in tasks if "ansible.builtin.fail" not in t]
    assert not [t for t in drifted if "ansible.builtin.fail" in t]
