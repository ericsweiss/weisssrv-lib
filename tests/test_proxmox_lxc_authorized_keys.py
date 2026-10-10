"""The LXC authorized_keys reconcile is a merge, not a whole-file rewrite.

A forced-command entry seeded outside Ansible is how a cert reaches a
container, and a whole-file rewrite strands the next renewal."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "proxmox_lxc"
MAIN = ROLE / "tasks" / "main.yml"
DEFAULTS = ROLE / "defaults" / "main.yml"
TASK = "Reconcile SSH authorized keys for the admin user"
PRUNE = "proxmox_lxc_ssh_authorized_keys_prune"

MANAGED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAMANAGED managed@test"
STALE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAASTALE stale@test"
FOREIGN = (
    'command="/usr/local/bin/cert-receive",restrict '
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAACERT cert@test"
)

# `pct exec <id> -- <argv>` runs <argv> here instead of in a container, and the
# ownership flags need root, so install drops them and chown is recorded.
SHIMS = {
    "pct": """#!/bin/bash
[ "$1" = "exec" ] || exit 0
shift 2
[ "$1" = "--" ] && shift
exec "$@"
""",
    "install": """#!/bin/bash
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-g) shift 2;;
    *) args+=("$1"); shift;;
  esac
done
echo "install ${args[*]}" >> "$SHIM_LOG"
exec /usr/bin/install "${args[@]}"
""",
    "chown": """#!/bin/bash
echo "chown $*" >> "$SHIM_LOG"
exit 0
""",
}


def _script(home: Path, keys: str, prune: bool) -> str:
    """The task's own shell body, rendered for one home directory and key set."""
    tasks = yaml.safe_load(MAIN.read_text(encoding="utf-8"))
    body = next(t["ansible.builtin.shell"]["cmd"] for t in tasks if t.get("name") == TASK)
    return ansible_env().from_string(body).render(
        proxmox_lxc_id=199,
        proxmox_lxc_admin_user="molecule",
        proxmox_lxc_admin_home=str(home),
        proxmox_lxc_ssh_public_keys=keys,
        **{PRUNE: prune},
    )


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    for name, body in SHIMS.items():
        shim = path / name
        shim.write_text(body, encoding="utf-8")
        shim.chmod(0o755)
    return path


def _run(bindir: Path, home: Path, keys: str = MANAGED, prune: bool = False):
    env = {
        "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % bindir,
        "SHIM_LOG": str(bindir / "calls.log"),
    }
    return subprocess.run(
        ["bash", "-c", _script(home, keys, prune)],
        capture_output=True, text=True, env=env,
    )


def _seed(home: Path, *lines: str) -> Path:
    auth = home / ".ssh" / "authorized_keys"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text("".join("%s\n" % line for line in lines), encoding="utf-8")
    return auth


def test_the_prune_default_keeps_unmanaged_entries() -> None:
    """Preserving is the default: the opt-in is the destructive direction."""
    assert yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))[PRUNE] is False


def test_a_foreign_forced_command_survives_the_reconcile(bindir, tmp_path) -> None:
    home = tmp_path / "home"
    auth = _seed(home, FOREIGN, STALE)
    proc = _run(bindir, home)
    assert proc.returncode == 0, proc.stderr
    assert "CHANGED" in proc.stdout
    assert auth.read_text(encoding="utf-8").splitlines() == [MANAGED, FOREIGN, STALE]


def test_the_replay_is_a_no_op(bindir, tmp_path) -> None:
    home = tmp_path / "home"
    auth = _seed(home, FOREIGN)
    _run(bindir, home)
    first = auth.read_text(encoding="utf-8")
    proc = _run(bindir, home)
    assert proc.returncode == 0, proc.stderr
    assert "CHANGED" not in proc.stdout
    assert auth.read_text(encoding="utf-8") == first


def test_a_rotated_key_is_added_without_dropping_the_foreign_entry(bindir, tmp_path) -> None:
    home = tmp_path / "home"
    auth = _seed(home, MANAGED, FOREIGN)
    rotated = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAROTATED rotated@test"
    proc = _run(bindir, home, keys=rotated)
    assert proc.returncode == 0, proc.stderr
    lines = auth.read_text(encoding="utf-8").splitlines()
    assert lines[0] == rotated
    assert FOREIGN in lines
    # The superseded key is an unmanaged line like any other, so rotation alone
    # does not revoke it; prune is the lever that does.
    assert MANAGED in lines


def test_prune_makes_the_managed_set_authoritative(bindir, tmp_path) -> None:
    home = tmp_path / "home"
    auth = _seed(home, FOREIGN, STALE)
    proc = _run(bindir, home, prune=True)
    assert proc.returncode == 0, proc.stderr
    assert auth.read_text(encoding="utf-8").splitlines() == [MANAGED]


def test_a_first_install_writes_only_the_managed_set(bindir, tmp_path) -> None:
    """No authorized_keys yet: the merge must not read a file that is absent."""
    home = tmp_path / "home"
    proc = _run(bindir, home)
    assert proc.returncode == 0, proc.stderr
    auth = home / ".ssh" / "authorized_keys"
    assert auth.read_text(encoding="utf-8").splitlines() == [MANAGED]
    assert auth.stat().st_mode & 0o777 == 0o600


def test_the_file_is_chowned_to_the_admin_user_before_the_move(bindir, tmp_path) -> None:
    """sshd ignores an authorized_keys owned by root, so the chown is not
    optional even on a run that rewrites nothing."""
    home = tmp_path / "home"
    _seed(home, FOREIGN)
    _run(bindir, home)
    assert "chown molecule:molecule" in (bindir / "calls.log").read_text(encoding="utf-8")
