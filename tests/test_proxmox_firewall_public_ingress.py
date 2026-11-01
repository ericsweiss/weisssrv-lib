"""cluster.fw.j2's sg-k3s-ingress-pub and sg-k3s-core groups.

The public VIP seam renders the unscoped rules only when empty, so molecule,
which sets a VIP, cannot prove the default arm on its own.
"""
from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "proxmox_firewall"
TEMPLATE = ROLE / "templates" / "cluster.fw.j2"
DEFAULTS = ROLE / "defaults" / "main.yml"


def group_source(name: str) -> str:
    """The template text of one `[group <name>]` section, up to the next one."""
    text = TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"^\[group %s\]\n(.*?)(?=^\[group )" % re.escape(name), text,
                      re.M | re.S)
    assert match, f"cluster.fw.j2 has no [group {name}] section"
    return match.group(1)


def render_public(**overrides) -> list[str]:
    env = ansible_env(undefined=jinja2.StrictUndefined, trim_blocks=True)
    context = {"proxmox_firewall_wan_wireguard_vips": [],
               "proxmox_firewall_k3s_public_vips": [], **overrides}
    rendered = env.from_string(group_source("sg-k3s-ingress-pub")).render(**context)
    return [ln for ln in rendered.splitlines() if ln.startswith("IN ")]


def test_the_default_is_an_empty_list():
    assert yaml.safe_load(DEFAULTS.read_text())["proxmox_firewall_k3s_public_vips"] == []


def test_no_vip_renders_the_unscoped_rules():
    assert render_public() == [
        "IN ACCEPT -p tcp -dport 443 -log nolog",
        "IN ACCEPT -p tcp -dport 80 -log nolog",
    ]


def test_each_vip_scopes_both_ports():
    assert render_public(proxmox_firewall_k3s_public_vips=["192.0.2.10", "192.0.2.11"]) == [
        "IN ACCEPT -dest 192.0.2.10 -p tcp -dport 443 -log nolog",
        "IN ACCEPT -dest 192.0.2.11 -p tcp -dport 443 -log nolog",
        "IN ACCEPT -dest 192.0.2.10 -p tcp -dport 80 -log nolog",
        "IN ACCEPT -dest 192.0.2.11 -p tcp -dport 80 -log nolog",
    ]


def test_a_vip_list_leaves_no_unscoped_rule():
    rules = render_public(proxmox_firewall_k3s_public_vips=["192.0.2.10"])
    assert all("-dest " in rule for rule in rules)


def test_the_wireguard_rule_keeps_its_own_vip():
    rules = render_public(proxmox_firewall_k3s_public_vips=["192.0.2.10"],
                          proxmox_firewall_wan_wireguard_vips=["192.0.2.20"])
    assert rules[-1] == "IN ACCEPT -dest 192.0.2.20 -p udp -dport 51820 -log nolog"


def test_k3s_core_opens_no_rke2_supervisor_port():
    """k3s serves its supervisor on :6443; :9345 is RKE2's and nothing listens."""
    core = group_source("sg-k3s-core")
    assert "-dport 6443" in core
    assert "9345" not in core


def test_a_blank_vip_never_renders_an_unscoped_rule():
    """A non-empty list scopes every rule, so a blank element fails to compile."""
    rules = render_public(proxmox_firewall_k3s_public_vips=["192.0.2.10", "", None])
    assert len(rules) == 6
    assert all(rule.startswith("IN ACCEPT -dest ") for rule in rules)


ADDRESS_RE = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())["_proxmox_firewall_address_re"]
ASSERTS = yaml.safe_load((ROLE / "tasks" / "assert_port_lists.yml").read_text())


@pytest.mark.parametrize("value", [
    "192.0.2.10", "192.0.2.0/24", "0.0.0.0/0", "255.255.255.255/32",
    "2001:db8::1", "2001:db8::/64", "2001:DB8:0:0:0:0:0:1/128", "::1", "1:2:3:4:5:6:7::",
])
def test_the_vip_grammar_accepts_an_address(value):
    assert re.match(ADDRESS_RE, value)


@pytest.mark.parametrize("value", [
    "", "1", ".", "vip_pub", "192.0.2.10 ", "192.0.2.10 -p udp", "192.0.2.10\n",
    "999.1.1.1", "999.999.999.999", "1.2.3.4/33", "1.2.3.4/99",
    ":", "::::", "abc:", "1:::2", "1::2::3", "12345::1", "2001:db8::/129",
    "1:2:3:4:5:6:7:8:9", "1:2:3:4::5:6:7:8", "::1:2:3:4:5:6:7:8",
    "dc/vip_pub", "+dc/vip_pub",
])
def test_the_vip_grammar_rejects_a_non_address(value):
    assert not re.match(ADDRESS_RE, value)


def test_the_vip_grammar_survives_templating():
    """Ansible templates the var before `match` sees it; the grammar holds no Jinja."""
    assert jinja2.Environment().from_string(ADDRESS_RE).render() == ADDRESS_RE


def test_both_vip_lists_are_schema_checked():
    task = next(t for t in ASSERTS if t["name"] == "Assert the public VIP lists are lists of addresses")
    assert task["loop"] == ["proxmox_firewall_k3s_public_vips", "proxmox_firewall_wan_wireguard_vips"]
    that = " ".join(task["ansible.builtin.assert"]["that"])
    for test in ("is sequence", "is not string", "is not mapping", "select('string')",
                 "select('match', _proxmox_firewall_address_re)"):
        assert test in that
