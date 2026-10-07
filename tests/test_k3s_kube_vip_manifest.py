"""Both arms of the kube-vip auth branch render.

The default authenticates with the pod's ServiceAccount token; the escape hatch
mounts the host kubeconfig.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import jinja2.meta
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "k3s"
TEMPLATE = ROLE / "templates" / "kube-vip-manifest.yaml.j2"
HOST_KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"

# Pins the role asserts instead of defaulting (tasks/server.yml).
ASSERTED_PINS = {"k3s_kube_vip_version", "k3s_api_vip"}

CONTEXT = {
    "ansible_managed": "managed",
    "k3s_kube_vip_version": "v1.0.0",
    "k3s_kube_vip_interface": "eth0",
    "k3s_api_port": 6443,
    "k3s_api_vip": "192.0.2.10",
    "k3s_kube_vip_resources": {"limits": {"memory": "64Mi"}},
}


def render(host_kubeconfig: bool) -> str:
    env = ansible_env(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(TEMPLATE.read_text(encoding="utf-8"))
    return template.render(k3s_kube_vip_host_kubeconfig=host_kubeconfig, **CONTEXT)


def test_the_default_authenticates_in_cluster():
    manifest = render(False)
    # `manager` rejects --inCluster; dropping the host kubeconfig mount is what
    # makes the pod use its ServiceAccount token.
    assert "--inCluster" not in manifest
    assert HOST_KUBECONFIG not in manifest
    assert "mountPath: /etc/kubernetes/admin.conf" not in manifest


def test_the_opt_out_mounts_the_host_kubeconfig():
    manifest = render(True)
    assert "--inCluster" not in manifest
    assert "mountPath: /etc/kubernetes/admin.conf" in manifest
    assert HOST_KUBECONFIG in manifest
    # FileOrCreate would fabricate an empty kubeconfig and kube-vip would fail
    # to authenticate with no sign the credential was missing.
    assert "type: File" in manifest
    assert "FileOrCreate" not in manifest


def documents(host_kubeconfig: bool) -> list[dict]:
    return [d for d in yaml.safe_load_all(render(host_kubeconfig)) if d]


def only(docs: list[dict], kind: str) -> dict:
    found = [d for d in docs if d.get("kind") == kind]
    assert len(found) == 1, f"expected exactly one {kind}, got {len(found)}"
    return found[0]


@pytest.mark.parametrize("host_kubeconfig", [False, True])
def test_both_arms_render_the_four_documents(host_kubeconfig):
    docs = documents(host_kubeconfig)
    assert [d["kind"] for d in docs] == [
        "ServiceAccount", "ClusterRole", "ClusterRoleBinding", "DaemonSet"
    ]


def test_the_in_cluster_arm_carries_the_rbac_it_now_depends_on():
    """No host kubeconfig mount makes the ServiceAccount the pod's real privilege."""
    docs = documents(False)
    account = only(docs, "ServiceAccount")
    assert account["metadata"]["name"] == "kube-vip"
    assert account["metadata"]["namespace"] == "kube-system"

    binding = only(docs, "ClusterRoleBinding")
    assert binding["roleRef"]["kind"] == "ClusterRole"
    assert binding["roleRef"]["name"] == "kube-vip"
    assert {"kind": "ServiceAccount", "name": "kube-vip",
            "namespace": "kube-system"} in binding["subjects"]

    pod = only(docs, "DaemonSet")["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "kube-vip"


def test_the_cluster_role_covers_every_api_kube_vip_uses():
    rules = only(documents(False), "ClusterRole")["rules"]

    def verbs_for(group: str, resource: str) -> set[str]:
        return {
            verb
            for rule in rules
            if group in rule.get("apiGroups", []) and resource in rule.get("resources", [])
            for verb in rule.get("verbs", [])
        }

    assert {"list", "get", "watch"} <= verbs_for("", "services")
    assert {"list", "get", "watch"} <= verbs_for("", "endpoints")
    assert verbs_for("", "nodes") & {"update", "patch"}
    assert verbs_for("coordination.k8s.io", "leases") & {"update", "create"}


def test_the_role_default_is_the_in_cluster_arm():
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    assert defaults["k3s_kube_vip_host_kubeconfig"] is False


def test_every_variable_has_a_role_default_or_an_asserted_pin():
    """A variable with neither is undefined wherever the template renders.

    The molecule scenario renders this template from its converge play, whose
    only source for a variable the inventory does not set is defaults/main.yml.
    """
    referenced = jinja2.meta.find_undeclared_variables(
        ansible_env().parse(TEMPLATE.read_text(encoding="utf-8")))
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    unresolved = referenced - set(defaults) - ASSERTED_PINS - {"ansible_managed"}
    assert not unresolved, f"no role default and not asserted: {sorted(unresolved)}"
