"""The cluster-init guard on a rebuilt first server.

`k3s_is_first_server` alone writes `cluster-init: true`, bootstrapping a second
etcd cluster against a live quorum; `k3s_join_existing` makes it a rejoin.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "k3s"
TEMPLATE = ROLE / "templates" / "k3s-server-config.yaml.j2"

API_VIP = "192.0.2.10"
API_PORT = 6443
JOIN_LINE = 'server: "https://%s:%d"' % (API_VIP, API_PORT)

CONTEXT = {
    "ansible_managed": "managed",
    "inventory_hostname": "server-01",
    "ansible_host": "192.0.2.11",
    "k3s_cluster_cidr": "10.42.0.0/16",
    "k3s_service_cidr": "10.43.0.0/16",
    "k3s_disable": [],
    "k3s_tls_sans": [],
    "k3s_labels": {},
    "k3s_taints": [],
    "k3s_flannel_backend": "wireguard-native",
    "k3s_api_vip": API_VIP,
    "k3s_api_port": API_PORT,
    "k3s_agent_token": "agent-token",
    "k3s_kubelet_args": [],
    "k3s_audit_enabled": False,
}


def render(*, first_server: bool, join_existing: bool) -> str:
    env = ansible_env(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(TEMPLATE.read_text(encoding="utf-8"))
    return template.render(
        k3s_is_first_server=first_server,
        k3s_join_existing=join_existing,
        **CONTEXT,
    )


def test_a_first_server_with_no_cluster_bootstraps_one():
    rendered = render(first_server=True, join_existing=False)
    assert "cluster-init: true" in rendered
    assert 'server: "https://' not in rendered


def test_a_rebuilt_first_server_rejoins_instead_of_bootstrapping():
    """Without the guard this writes a second etcd cluster over a live quorum."""
    rendered = render(first_server=True, join_existing=True)
    assert "cluster-init" not in rendered
    assert JOIN_LINE in rendered


def test_a_later_server_always_joins():
    rendered = render(first_server=False, join_existing=False)
    assert "cluster-init" not in rendered
    assert JOIN_LINE in rendered


def test_every_arm_stays_valid_yaml():
    for first_server, join_existing in ((True, False), (True, True), (False, False)):
        parsed = yaml.safe_load(
            render(first_server=first_server, join_existing=join_existing)
        )
        assert parsed["service-cidr"] == CONTEXT["k3s_service_cidr"]
