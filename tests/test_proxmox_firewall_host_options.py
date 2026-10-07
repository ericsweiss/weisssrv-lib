"""host.fw.j2's nftables option, which selects the PVE firewall implementation.

It is omitted rather than written as `nftables: 0` when off, so a node keeps the
implementation it runs today; molecule cannot prove the off arm writes nothing.
"""
from __future__ import annotations

import difflib
from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "proxmox_firewall"
TEMPLATE = ROLE / "templates" / "host.fw.j2"
DEFAULTS = ROLE / "defaults" / "main.yml"
VARIABLE = "proxmox_firewall_nftables"

# Everything else host.fw.j2 reads, so one render differs from the next only in
# the value under test.
CONTEXT = {
    "ansible_managed": "managed",
    "proxmox_firewall_log_level_in": "nolog",
    "proxmox_firewall_host_rules": [],
    "proxmox_firewall_egress_filtering": False,
    "proxmox_firewall_host_extra_groups": [],
    "proxmox_firewall_storage_role_name": "nas",
    "proxmox_role": "compute",
}


def render(**overrides) -> str:
    # trim_blocks matches the template module, so a block tag on its own line
    # eats the newline after it as it does on a node.
    env = ansible_env(undefined=jinja2.StrictUndefined, keep_trailing_newline=True,
                      trim_blocks=True)
    template = env.from_string(TEMPLATE.read_text(encoding="utf-8"))
    return template.render(**{**CONTEXT, **overrides})


def option_lines(rendered: str) -> list[str]:
    """The `[OPTIONS]` section's lines, stripped, comments and blanks dropped."""
    head = rendered.split("[RULES]")[0].splitlines()
    return [line.strip() for line in head if line.strip() and not line.startswith("#")]


def test_the_default_is_off() -> None:
    """An opt-in switch between firewall implementations, so the role default
    has to leave every node where it is."""
    assert yaml.safe_load(DEFAULTS.read_text())[VARIABLE] is False


def test_enabled_renders_the_option_in_the_options_section() -> None:
    assert "nftables: 1" in option_lines(render(**{VARIABLE: True}))


def test_disabled_renders_no_option_at_all() -> None:
    """`nftables: 0` would also select an implementation — the iptables one —
    on a node whose host.fw had the option set before."""
    assert "nftables" not in render(**{VARIABLE: False})


def test_the_option_is_the_only_difference_between_the_two_arms() -> None:
    """Pinned as a one-line delta: the flag must not move a rule, a group
    reference or the trailing DROP."""
    off = render(**{VARIABLE: False}).splitlines()
    on = render(**{VARIABLE: True}).splitlines()
    delta = [line for line in difflib.ndiff(off, on) if line[:2] in ("+ ", "- ")]
    assert delta == ["+ nftables: 1"]


@pytest.mark.parametrize("value", [True, "true", "yes", "1", 1])
def test_truthy_values_select_nftables(value) -> None:
    assert "nftables: 1" in render(**{VARIABLE: value})


@pytest.mark.parametrize("value", [False, "false", "no", "0", 0, ""])
def test_falsey_values_render_nothing(value) -> None:
    """A string from `-e` or a YAML `"false"` is truthy to plain Jinja, so the
    `| bool` filter is what keeps it from switching a node's firewall."""
    assert "nftables" not in render(**{VARIABLE: value})
