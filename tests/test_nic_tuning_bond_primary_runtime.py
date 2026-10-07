"""The bond-primary runtime writes land, and a failed write is loud.

The pin exists to keep an active-backup bond off a wedged leg, so a swallowed
sysfs write would leave the play green with the bond still on the wrong leg.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nic_tuning"
SET_TASKS = ROLE / "tasks" / "bond-primary.yml"
ABSENT_TASKS = ROLE / "tasks" / "bond-primary-absent.yml"
SET_TASK = "Set the bond primary at runtime (no reboot)"
ABSENT_TASK = "Clear the bond primary at runtime (no reboot)"
SYSFS = "/sys/class/net"
PROBE_TASK = "Read the bond membership of the requested primary"
GUARD = "Assert the bond primary is a slave of the active-backup bond"
INTERFACES = "/etc/network/interfaces"


def _body(tasks_file: Path, task_name: str) -> str:
    tasks = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
    return next(t["ansible.builtin.shell"] for t in tasks if t.get("name") == task_name)


def _set_script(sysfs_root: Path, iface: str = "eth1", reselect: str = "failure") -> str:
    """The task's real shell body, rendered and pointed at a fixture tree."""
    template = ansible_env().from_string(_body(SET_TASKS, SET_TASK))
    body = template.render(
        nic_tuning_bond_primary=iface, nic_tuning_bond_primary_reselect=reselect
    )
    assert "{{" not in body, "the task body still carries Jinja"
    return body.replace(SYSFS, str(sysfs_root))


def _absent_script(sysfs_root: Path) -> str:
    body = _body(ABSENT_TASKS, ABSENT_TASK)
    assert "{{" not in body, "the absent task body is no longer plain shell"
    return body.replace(SYSFS, str(sysfs_root))


def _bond(
    root: Path,
    mode: str = "active-backup",
    slaves: str = "eth0 eth1",
    primary: str = "",
    reselect: str = "always",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bonding_masters").write_text("bond0\n", encoding="utf-8")
    bonding = root / "bond0" / "bonding"
    bonding.mkdir(parents=True)
    (bonding / "mode").write_text("%s 1\n" % mode, encoding="utf-8")
    (bonding / "slaves").write_text("%s\n" % slaves, encoding="utf-8")
    (bonding / "primary").write_text("%s\n" % primary, encoding="utf-8")
    (bonding / "primary_reselect").write_text("%s 0\n" % reselect, encoding="utf-8")
    return bonding


def _interfaces(path: Path, stanzas: str) -> Path:
    path.write_text(stanzas, encoding="utf-8")
    return path


SEEDED = """auto bond0
iface bond0 inet manual
    bond-slaves eth0 eth1
    bond-mode active-backup
"""


def _probe_script(sysfs_root: Path, interfaces: Path, iface: str = "eth1") -> str:
    """The membership probe's real shell body, on a fixture tree and file."""
    template = ansible_env().from_string(_body(SET_TASKS, PROBE_TASK))
    body = template.render(nic_tuning_bond_primary=iface)
    assert "{{" not in body, "the probe body still carries Jinja"
    return body.replace(SYSFS, str(sysfs_root)).replace(INTERFACES, str(interfaces))


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_an_unpinned_bond_gets_both_writes(tmp_path):
    bonding = _bond(tmp_path / "net")
    proc = _run(_set_script(tmp_path / "net"))
    assert proc.returncode == 0, proc.stderr
    assert "CHANGED" in proc.stdout
    assert (bonding / "primary_reselect").read_text().strip() == "failure"
    assert (bonding / "primary").read_text().strip() == "eth1"


def test_an_already_pinned_bond_is_a_no_op(tmp_path):
    bonding = _bond(tmp_path / "net", primary="eth1", reselect="failure")
    before = {
        name: (bonding / name).read_text() for name in ("primary", "primary_reselect")
    }
    proc = _run(_set_script(tmp_path / "net"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
    assert {
        name: (bonding / name).read_text() for name in ("primary", "primary_reselect")
    } == before


def test_reselect_is_written_before_primary(tmp_path):
    """Under the kernel default `always`, writing primary first moves the active
    slave immediately and blips the uplink."""
    body = _set_script(tmp_path / "net")
    reselect_write = body.index('> "%s/$b/bonding/primary_reselect"' % (tmp_path / "net"))
    primary_write = body.index('> "%s/$b/bonding/primary"' % (tmp_path / "net"))
    assert reselect_write < primary_write, body


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a 0444 file anyway")
def test_a_failed_primary_write_is_loud(tmp_path):
    bonding = _bond(tmp_path / "net", reselect="failure")
    (bonding / "primary").chmod(0o444)
    proc = _run(_set_script(tmp_path / "net"))
    assert proc.returncode == 4
    assert "WRITEFAIL" in proc.stdout


def test_a_leg_no_bond_enslaves_is_reported(tmp_path):
    _bond(tmp_path / "net", slaves="eth0 eth2")
    proc = _run(_set_script(tmp_path / "net"))
    assert proc.returncode == 3
    assert "MISSING" in proc.stdout


def test_a_bond_in_another_mode_is_skipped(tmp_path):
    bonding = _bond(tmp_path / "net", mode="balance-rr")
    proc = _run(_set_script(tmp_path / "net"))
    assert proc.returncode == 3
    assert "MISSING" in proc.stdout
    assert (bonding / "primary").read_text().strip() == ""


def test_a_host_with_no_bonding_module_says_so(tmp_path):
    root = tmp_path / "net"
    root.mkdir()
    proc = _run(_set_script(root))
    assert proc.returncode == 0
    assert proc.stdout.startswith("NOBOND")


def test_the_absent_arm_clears_a_pin_once(tmp_path):
    bonding = _bond(tmp_path / "net", primary="eth1")
    script = _absent_script(tmp_path / "net")
    first = _run(script)
    assert first.returncode == 0, first.stderr
    assert "CHANGED" in first.stdout
    assert (bonding / "primary").read_text().strip() == ""
    second = _run(script)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "OK"


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a 0444 file anyway")
def test_the_absent_arm_fails_loudly_on_an_unwritable_primary(tmp_path):
    bonding = _bond(tmp_path / "net", primary="eth1")
    (bonding / "primary").chmod(0o444)
    proc = _run(_absent_script(tmp_path / "net"))
    assert proc.returncode == 4
    assert "WRITEFAIL" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_the_probe_accepts_a_slave_the_file_and_the_kernel_agree_on(tmp_path):
    _bond(tmp_path / "net")
    interfaces = _interfaces(tmp_path / "interfaces", SEEDED)
    proc = _run(_probe_script(tmp_path / "net", interfaces))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK")


def test_the_probe_rejects_a_leg_the_interfaces_file_never_declares(tmp_path):
    """The typo case on a host with no bonding: the runtime task reports NOBOND
    and goes green, so only this read keeps the bad name out of the file."""
    root = tmp_path / "net"
    root.mkdir()
    interfaces = _interfaces(tmp_path / "interfaces", SEEDED)
    proc = _run(_probe_script(root, interfaces, iface="eth9"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("MISMATCH")
    assert "eth0 eth1" in proc.stdout


def test_the_probe_rejects_a_leg_no_live_bond_enslaves(tmp_path):
    _bond(tmp_path / "net", slaves="eth0 eth2")
    interfaces = _interfaces(
        tmp_path / "interfaces", SEEDED.replace("eth0 eth1", "eth0 eth1 eth2")
    )
    proc = _run(_probe_script(tmp_path / "net", interfaces))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("MISMATCH")
    assert "bond0=[eth0 eth2" in proc.stdout, proc.stdout


def test_the_probe_ignores_a_live_bond_in_another_mode(tmp_path):
    """The pin only reaches active-backup bonds, so a slave of a balance-rr bond
    is not a slave this variable can pin."""
    _bond(tmp_path / "net", mode="balance-rr")
    interfaces = _interfaces(tmp_path / "interfaces", SEEDED)
    proc = _run(_probe_script(tmp_path / "net", interfaces))
    assert proc.stdout.startswith("MISMATCH")


def test_the_probe_accepts_a_file_that_declares_no_slaves(tmp_path):
    """Slaves declared on the legs with `bond-master` leave nothing to compare;
    an unknown membership is not a contradiction."""
    root = tmp_path / "net"
    root.mkdir()
    interfaces = _interfaces(
        tmp_path / "interfaces",
        "auto bond0\niface bond0 inet manual\n    bond-mode active-backup\n",
    )
    proc = _run(_probe_script(root, interfaces, iface="eth9"))
    assert proc.stdout.startswith("OK")


def test_the_probe_reads_the_slaves_of_the_active_backup_stanza_only(tmp_path):
    """Mutation: a neighbouring bond's `bond-slaves` must not satisfy the check,
    or a leg of the wrong bond would be persisted as this bond's primary."""
    root = tmp_path / "net"
    root.mkdir()
    interfaces = _interfaces(
        tmp_path / "interfaces",
        "auto bond1\niface bond1 inet manual\n"
        "    bond-slaves eth8 eth9\n    bond-mode balance-rr\n\n" + SEEDED,
    )
    assert _run(_probe_script(root, interfaces, iface="eth9")).stdout.startswith("MISMATCH")
    assert _run(_probe_script(root, interfaces, iface="eth1")).stdout.startswith("OK")


def test_the_membership_guard_precedes_every_persistent_write():
    """The finding: a guard after the writes leaves an unusable bond-primary line
    in /etc/network/interfaces for the next boot."""
    tasks = yaml.safe_load(SET_TASKS.read_text(encoding="utf-8"))
    names = [task.get("name") for task in tasks]
    writes = [
        index for index, task in enumerate(tasks)
        if "ansible.builtin.lineinfile" in task
    ]
    assert writes, "bond-primary.yml persists nothing any more"
    assert names.index(GUARD) < min(writes), names
