"""The runtime bond guards fail loudly instead of reporting a green play.

A swallowed all_slaves_active write re-opens a network black-hole."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nic_tuning"
MAIN = ROLE / "tasks" / "main.yml"
TASK = "Enforce all_slaves_active=0 on active-backup bonds at runtime (no reboot)"


def _script(sysfs_root: Path) -> str:
    """The task's real shell body, pointed at a fixture sysfs tree."""
    tasks = yaml.safe_load(MAIN.read_text(encoding="utf-8"))
    body = next(t["ansible.builtin.shell"] for t in tasks if t.get("name") == TASK)
    assert "{{" not in body, "the task body is no longer plain shell"
    return body.replace("/sys/class/net", str(sysfs_root))


def _bond(root: Path, mode: str = "active-backup", asa: str = "1") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bonding_masters").write_text("bond0\n", encoding="utf-8")
    bonding = root / "bond0" / "bonding"
    bonding.mkdir(parents=True)
    (bonding / "mode").write_text("%s 1\n" % mode, encoding="utf-8")
    (bonding / "all_slaves_active").write_text("%s\n" % asa, encoding="utf-8")


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", _script(root)], capture_output=True, text=True)


def test_a_stale_guard_is_flipped_and_reported(tmp_path):
    root = tmp_path / "net"
    _bond(root)
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    assert "CHANGED" in proc.stdout
    assert (root / "bond0" / "bonding" / "all_slaves_active").read_text().strip() == "0"


def test_a_bond_already_at_zero_is_a_no_op(tmp_path):
    root = tmp_path / "net"
    _bond(root, asa="0")
    proc = _run(root)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "OK"


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a 0444 file anyway")
def test_a_failed_write_is_loud(tmp_path):
    """An unwritable knob must exit non-zero, not report ok."""
    root = tmp_path / "net"
    _bond(root)
    (root / "bond0" / "bonding" / "all_slaves_active").chmod(0o444)
    proc = _run(root)
    assert proc.returncode == 4
    assert "WRITEFAIL" in proc.stdout


def test_the_write_is_not_chained_behind_an_and(tmp_path):
    """`set -eu` does not fire for a failed command in an `&&` list, so the
    write has to be tested explicitly. Runs where root defeats file modes."""
    body = _script(tmp_path)
    write = [ln for ln in body.splitlines() if "all_slaves_active" in ln and ">" in ln]
    assert write and all("&&" not in ln for ln in write), write
    assert "WRITEFAIL" in body and "exit 4" in body


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file anyway")
def test_an_unreadable_bond_attribute_is_loud(tmp_path):
    """A bond the script cannot inspect must not be reported as compliant."""
    root = tmp_path / "net"
    _bond(root)
    (root / "bond0" / "bonding" / "all_slaves_active").chmod(0o000)
    proc = _run(root)
    assert proc.returncode == 4
    assert "READFAIL" in proc.stdout
    assert "OK" not in proc.stdout


def test_a_bond_that_disappeared_mid_loop_is_benign(tmp_path):
    """bonding_masters names a bond whose sysfs directory is already gone."""
    root = tmp_path / "net"
    root.mkdir(parents=True)
    (root / "bonding_masters").write_text("bond0\n", encoding="utf-8")
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_a_host_with_no_bonding_module_says_so(tmp_path):
    root = tmp_path / "net"
    root.mkdir()
    proc = _run(root)
    assert proc.returncode == 0
    assert proc.stdout.startswith("NOBOND")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
