"""Every site input cluster.fw.j2 renders is covered by the cluster-scope guard.

cluster.fw is written from the calling play's first host, so the guard's
hand-maintained list must not fall behind the template.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "proxmox_firewall"
TEMPLATE = ROLE / "templates" / "cluster.fw.j2"
GUARD = ROLE / "tasks" / "assert_cluster_scope.yml"

# Legitimately per-host, so the guard must NOT demand one value cluster-wide.
PER_HOST = {
    # The node's own address, different on every host.
    "proxmox_firewall_node_ip",
    # Enforced in host.fw.j2; cluster.fw only mentions it in a comment.
    "proxmox_firewall_egress_filtering",
}


def rendered_inputs() -> set:
    return set(re.findall(r"\bproxmox_firewall_[a-z0-9_]+", TEMPLATE.read_text()))


def guarded_inputs() -> set:
    return set(re.findall(r"^\s+- (proxmox_firewall_[a-z0-9_]+)\s*$",
                          GUARD.read_text(), re.M))


def test_the_scan_finds_inputs_to_check():
    """A regex that matched nothing would make the assertion below vacuous."""
    assert len(rendered_inputs()) >= 10
    assert len(guarded_inputs()) >= 10


def test_every_cluster_scope_input_is_guarded():
    missing = sorted(rendered_inputs() - guarded_inputs() - PER_HOST)
    assert not missing, (
        "cluster.fw.j2 renders %s, which assert_cluster_scope.yml does not "
        "guard. Add each to _proxmox_firewall_cluster_scope_vars, or to "
        "PER_HOST here with the reason it is per-host." % ", ".join(missing)
    )


def test_the_guard_names_no_input_the_template_dropped():
    stale = sorted(guarded_inputs() - rendered_inputs())
    assert not stale, (
        "assert_cluster_scope.yml guards %s, which cluster.fw.j2 no longer "
        "renders." % ", ".join(stale)
    )


def test_the_gate_reports_an_unguarded_input():
    """Mutation case: drop a guarded name and the comparison must catch it."""
    rendered = {"proxmox_firewall_a", "proxmox_firewall_b"}
    guarded = {"proxmox_firewall_a"}
    assert sorted(rendered - guarded - PER_HOST) == ["proxmox_firewall_b"]
