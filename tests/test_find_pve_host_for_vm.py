#!/usr/bin/env python3
"""Tests for scripts/find-pve-host-for-vm.sh — the HA-resilient "which node runs
this VM" lookup. Every branch of its three-step fallback returns a hostname a
caller then SSHes to, so a wrong answer is acted on rather than reported.

The `ssh` stub EXECUTES the remote command string locally against stub
`ha-manager` / `pvesh` / `qm` binaries, so the script's own grep boundary
matching, sed extraction and `set -o pipefail` handling are the code under test
rather than something the harness papers over.

Run with pytest:
    python3 -m pytest tests/test_find_pve_host_for_vm.py -v
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "find-pve-host-for-vm.sh"
BASH = shutil.which("bash") or "/bin/bash"

FAKE_SSH = """\
#!%s
args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) shift 2 ;;
        *) args+=("$1"); shift ;;
    esac
done
target="${args[0]}"
case " ${UP:-} " in
    *" $target "*) ;;
    *) exit 255 ;;
esac
export SSH_TARGET="$target"
exec bash -c "${args[*]:1}"
""" % BASH

FAKE_SUDO = '#!%s\nexec "$@"\n' % BASH
FAKE_HA_MANAGER = '#!%s\nprintf \'%%s\' "${HA_STATUS:-}"\n' % BASH
FAKE_PVESH = '#!%s\nprintf \'%%s\' "${PVESH_JSON:-[]}"\n' % BASH
# `qm status <vmid>` answers only on the host that actually runs the VM.
FAKE_QM = """\
#!%s
case " ${QM_HOSTS:-} " in
    *" ${SSH_TARGET:-} "*) echo "status: running"; exit 0 ;;
esac
echo "Configuration file does not exist" >&2
exit 2
""" % BASH

STUBS = {
    "ssh": FAKE_SSH,
    "sudo": FAKE_SUDO,
    "ha-manager": FAKE_HA_MANAGER,
    "pvesh": FAKE_PVESH,
    "qm": FAKE_QM,
}

HOSTS = ("pve-nas-01", "pve-opt-01", "pve-opt-02")
ALL_UP = " ".join(HOSTS)


@pytest.fixture()
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)

    def _run(*args, up=ALL_UP, **env):
        # A CLOSED env, like the sibling shell suites: the script reads
        # $PVE_NODE_PREFIX, so an ambient one would quietly change what the
        # prefix-blind cases below assert. Only the real PATH tail is kept, so
        # the stubs can still reach bash/sed/grep/python3.
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env={
                "PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                "UP": up,
                **{k: str(v) for k, v in env.items()},
            },
        )

    return _run


def test_ha_manager_answers_first(run):
    proc = run("154", *HOSTS, HA_STATUS="service vm:154 (pve-opt-01, started)\n")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-opt-01"


def test_ha_manager_match_is_bounded_so_a_longer_vmid_does_not_match(run):
    """vm:154 must not be satisfied by vm:1540 — the wrong node would be
    returned with no error anywhere."""
    proc = run(
        "154",
        *HOSTS,
        HA_STATUS="service vm:1540 (pve-opt-02, started)\n",
        QM_HOSTS="pve-nas-01",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-nas-01"


def test_an_unparseable_ha_status_line_falls_through_instead_of_being_echoed(run):
    """A grep hit whose `(node, ...)` shape sed cannot parse must not put the
    whole status line into the result."""
    proc = run("154", *HOSTS, HA_STATUS="service vm:154 is in an odd state\n", QM_HOSTS="pve-opt-02")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-opt-02"


def test_the_ha_manager_branch_applies_the_default_prefix(run):
    """`ha-manager status` reports the same BARE node name `pvesh` does, so the
    branch that runs FIRST needs the same rewrite — without it the caller SSHes
    to `opt-01`, which does not resolve, and the failure surfaces a layer up with
    no pointer back here."""
    proc = run("154", *HOSTS, HA_STATUS="service vm:154 (opt-01, started)\n")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-opt-01"


def test_the_ha_manager_branch_normalizes_a_configured_prefix_too(run):
    proc = run(
        "154",
        "node-a",
        "node-b",
        up="node-a node-b",
        HA_STATUS="service vm:154 (b, started)\n",
        PVE_NODE_PREFIX="node-",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "node-b"


def test_the_ha_manager_branch_does_not_double_the_prefix(run):
    proc = run("154", *HOSTS, HA_STATUS="service vm:154 (pve-opt-01, started)\n")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-opt-01"


def test_an_empty_prefix_leaves_the_ha_manager_node_verbatim(run):
    proc = run(
        "154",
        "alpha",
        "beta",
        up="alpha beta",
        HA_STATUS="service vm:154 (beta, started)\n",
        PVE_NODE_PREFIX="",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "beta"


def test_the_per_host_scan_is_exempt_from_the_prefix_rewrite(run):
    """Step 4 returns the caller's OWN SSH target, so prefixing it would corrupt
    a name that is already correct."""
    proc = run(
        "154",
        "node-a",
        "node-b",
        up="node-a node-b",
        QM_HOSTS="node-b",
        PVE_NODE_PREFIX="pve-",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "node-b"


def test_cluster_resources_covers_a_non_ha_vm(run):
    proc = run("154", *HOSTS, PVESH_JSON='[{"vmid": 999, "node": "opt-02"}, {"vmid": 154, "node": "opt-01"}]')
    assert proc.returncode == 0
    # `pvesh` returns the bare node name; the default prefix restores the SSH target.
    assert proc.stdout.strip() == "pve-opt-01"


def test_cluster_resources_does_not_double_the_prefix(run):
    proc = run("154", *HOSTS, PVESH_JSON='[{"vmid": 154, "node": "pve-opt-01"}]')
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-opt-01"


def test_node_prefix_is_configurable(run):
    """A site whose nodes are not named pve-* got an unresolvable hostname back."""
    proc = run(
        "154",
        "node-a",
        "node-b",
        up="node-a node-b",
        PVESH_JSON='[{"vmid": 154, "node": "b"}]',
        PVE_NODE_PREFIX="node-",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "node-b"


def test_an_empty_node_prefix_returns_the_node_name_verbatim(run):
    proc = run(
        "154",
        "alpha",
        "beta",
        up="alpha beta",
        PVESH_JSON='[{"vmid": 154, "node": "beta"}]',
        PVE_NODE_PREFIX="",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "beta"


def test_per_host_qm_scan_is_the_last_resort(run):
    proc = run("154", *HOSTS, QM_HOSTS="pve-opt-02")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-opt-02"


def test_the_first_reachable_host_is_the_cluster_entry_point(run):
    """An unreachable first candidate must not abort the lookup."""
    proc = run("154", *HOSTS, up="pve-opt-01 pve-opt-02", HA_STATUS="service vm:154 (pve-opt-02, started)\n")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "pve-opt-02"


def test_no_reachable_host_is_a_distinct_failure(run):
    proc = run("154", *HOSTS, up="")
    assert proc.returncode == 1
    assert "no reachable host" in proc.stderr


def test_a_vm_nobody_runs_exits_one(run):
    proc = run("154", *HOSTS)
    assert proc.returncode == 1
    assert "VM 154 not found" in proc.stderr


def test_a_non_numeric_vmid_is_rejected_before_any_ssh(run):
    """VMID is interpolated into a remote shell command and an inline python
    snippet — it is pinned to digits up front."""
    proc = run("154; rm -rf /", *HOSTS)
    assert proc.returncode == 2
    assert "VMID must be a positive integer" in proc.stderr


def test_usage_error_when_no_hosts_are_given(run):
    proc = run("154")
    assert proc.returncode == 2
    assert "Usage:" in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
