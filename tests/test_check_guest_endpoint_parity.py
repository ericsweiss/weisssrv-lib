#!/usr/bin/env python3
"""Unit tests for scripts/check-guest-endpoint-parity.py.

Each test builds a throwaway repo — inventory, cluster-config and a manifest
tree — and asserts the findings, or the exit 2 the gate refuses to pass with.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from script_loader import load_script  # noqa: E402

gate = load_script("check-guest-endpoint-parity.py")

HOSTS = """
all:
  children:
    pve:
      hosts:
        pve-nas-01:
          ansible_host: 10.0.10.102
    k3s:
      children:
        k3s_servers:
          hosts:
            k3s-01:
              ansible_host: 10.0.10.221
            k3s-02:
              ansible_host: 10.0.10.222
        k3s_agents:
          hosts:
            k3s-03:
              ansible_host: 10.0.10.231
"""

CONFIG = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-config
data:
  cluster_lan_cidr: 10.0.10.0/24
  cluster_lan_gateway: 10.0.10.1
  cluster_plex_ip: 10.0.10.152
"""


def _repo(tmp_path: Path, *, hosts: str = HOSTS, config: str = CONFIG,
          manifests: dict | None = None, group_vars: dict | None = None) -> Path:
    inventory = tmp_path / "ansible" / "inventories" / "prod"
    (inventory / "group_vars").mkdir(parents=True)
    (inventory / "host_vars").mkdir(parents=True)
    (inventory / "hosts.yml").write_text(hosts, encoding="utf-8")
    sources = tmp_path / "kubernetes" / "infrastructure" / "sources"
    sources.mkdir(parents=True)
    (sources / "cluster-config.yaml").write_text(config, encoding="utf-8")
    for name, body in (manifests or {}).items():
        path = tmp_path / "kubernetes" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    for name, body in (group_vars or {}).items():
        (inventory / "group_vars" / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _slice(address: str, name: str = "plex") -> str:
    return f"""
        apiVersion: discovery.k8s.io/v1
        kind: EndpointSlice
        metadata:
          name: {name}
        addressType: IPv4
        endpoints:
          - addresses: ["{address}"]
    """


def _exports(*specs: str, path: str = "/tank/media") -> str:
    lines = ["nas_storage_exports:", f"  - path: {path}", "    clients:"]
    lines += [f'      - spec: "{spec}"' for spec in specs]
    return "\n".join(lines) + "\n"


def test_a_clean_tree_passes(tmp_path):
    root = _repo(tmp_path, manifests={"apps/plex/endpointslice.yaml": _slice("10.0.10.102")})
    assert gate.check(root) == ([], 1)


def test_an_address_no_host_holds_is_a_finding(tmp_path):
    root = _repo(tmp_path, manifests={"apps/plex/endpointslice.yaml": _slice("10.0.10.199")})
    problems, checked = gate.check(root)
    assert checked == 1
    assert len(problems) == 1 and "10.0.10.199" in problems[0]
    assert "renumbered on one side only" in problems[0]


def test_an_address_outside_every_declared_lan_is_out_of_scope(tmp_path):
    """An off-LAN endpoint is a real upstream, not a guest, so comparing it
    against the inventory would fail every external backend."""
    root = _repo(tmp_path, manifests={
        "apps/a/slice.yaml": _slice("1.1.1.1", name="upstream"),
        "apps/b/slice.yaml": _slice("10.0.10.102"),
    })
    assert gate.check(root) == ([], 1)


def test_the_gateway_is_allowed_without_being_a_host(tmp_path):
    root = _repo(tmp_path, manifests={
        "apps/gw/slice.yaml": _slice("10.0.10.1", name="gw"),
        "apps/b/slice.yaml": _slice("10.0.10.102"),
    })
    assert gate.check(root) == ([], 1)


def test_an_endpoints_object_is_read_too(tmp_path):
    """The v1 `Endpoints` shape spells its addresses under subsets, so a reader
    that knows only EndpointSlice walks past half the corpus."""
    root = _repo(tmp_path, manifests={"apps/old/endpoints.yaml": """
        apiVersion: v1
        kind: Endpoints
        metadata:
          name: legacy
        subsets:
          - addresses:
              - ip: 10.0.10.199
    """})
    problems, checked = gate.check(root)
    assert checked == 1 and "Endpoints/legacy" in problems[0]


def test_a_cluster_config_placeholder_resolves_before_comparison(tmp_path):
    """Manifests spell a guest address as `${cluster_*}`, so the gate must
    substitute the way Flux does or every address fails the IP parse."""
    root = _repo(tmp_path, manifests={
        "apps/plex/slice.yaml": _slice("${cluster_plex_ip}"),
    })
    problems, checked = gate.check(root)
    assert checked == 1 and "10.0.10.152" in problems[0]


def test_an_unknown_placeholder_is_reported_as_no_address(tmp_path):
    root = _repo(tmp_path, manifests={
        "apps/plex/slice.yaml": _slice("${cluster_nope}"),
        "apps/b/slice.yaml": _slice("10.0.10.102", name="nas"),
    })
    problems, _checked = gate.check(root)
    assert any("is not an IP address" in p for p in problems)


def test_a_roster_placeholder_that_resolves_to_nothing_is_reported(tmp_path):
    """A whole-list roster key that cluster-config does not define would drop
    every address it holds, and an empty list passes every check below it."""
    root = _repo(tmp_path, manifests={
        "apps/syslog/slice.yaml": """
            apiVersion: discovery.k8s.io/v1
            kind: EndpointSlice
            metadata:
              name: syslog
            endpoints: ${cluster_syslog_roster}
        """,
        "apps/b/slice.yaml": _slice("10.0.10.102"),
    })
    problems, _checked = gate.check(root)
    assert any("names no cluster-config key" in p for p in problems)


def test_an_unreadable_manifest_is_reported_not_skipped(tmp_path):
    root = _repo(tmp_path, manifests={
        "apps/broken/slice.yaml": "a: [unclosed\n",
        "apps/b/slice.yaml": _slice("10.0.10.102"),
    })
    problems, _checked = gate.check(root)
    assert any("could not inspect it" in p for p in problems)


def test_an_export_cidr_covering_no_host_is_a_finding(tmp_path):
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.10.64/27")},
    )
    problems, _checked = gate.check(root)
    assert any("covers no ansible_host" in p for p in problems)


def test_an_export_32_naming_a_host_passes(tmp_path):
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.10.221/32", "10.0.10.222/32",
                                        "10.0.10.231/32")},
    )
    assert gate.check(root) == ([], 4)


def test_an_export_wider_than_the_lan_is_reported_not_skipped(tmp_path):
    """The broadest client list an exports file can carry: containment in one
    direction alone left it unchecked and uncounted."""
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.0.0/8")},
    )
    problems, checked = gate.check(root)
    assert checked == 2
    assert any("wider than the host CIDR 10.0.10.0/24" in p for p in problems)


def test_a_default_route_client_spec_is_reported(tmp_path):
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("0.0.0.0/0")},
    )
    problems, _checked = gate.check(root)
    assert any("admits 0.0.0.0/0, which is wider" in p for p in problems)


def test_an_export_outside_every_lan_is_out_of_scope(tmp_path):
    """An export admitting a VPN or off-site range names no inventory host by
    design, so scoping it in would fail a legitimate client list."""
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("100.64.0.0/10")},
    )
    assert gate.check(root) == ([], 1)


def test_an_unresolvable_client_spec_is_left_alone(tmp_path):
    """A hostname, netgroup or wildcard is a legal export client this gate
    cannot resolve, so reporting it would fail a correct inventory."""
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("*.esweiss.com")},
    )
    assert gate.check(root) == ([], 1)


def test_an_export_admitting_part_of_a_group_is_a_finding(tmp_path):
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.10.221/32")},
    )
    problems, _checked = gate.check(root)
    assert any("admits part of k3s_servers but not 10.0.10.222" in p for p in problems)


def test_an_export_admitting_a_whole_group_by_cidr_is_clean(tmp_path):
    """A /24 admits every node, so the partial-group check must compare by
    containment rather than by exact /32 membership."""
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.10.0/24")},
    )
    assert gate.check(root) == ([], 2)


def test_scoped_groups_are_configurable(tmp_path):
    """A site naming its node groups differently gets no partial-group check
    unless it can name them, and a mixed group would report every export."""
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.10.221/32")},
    )
    assert gate.check(root, scoped_groups=())[0] == []
    problems = gate.check(root, scoped_groups=("k3s",))[0]
    assert any("admits part of k3s but not" in p for p in problems)


def test_several_lan_keys_widen_the_scope(tmp_path):
    config = CONFIG + "  cluster_storage_cidr: 10.0.20.0/24\n"
    root = _repo(
        tmp_path, config=config,
        manifests={
            "apps/a/slice.yaml": _slice("10.0.20.50", name="storage"),
            "apps/b/slice.yaml": _slice("10.0.10.102"),
        },
    )
    assert gate.check(root) == ([], 1)
    problems, checked = gate.check(
        root, lan_cidr_keys=("cluster_lan_cidr", "cluster_storage_cidr")
    )
    assert checked == 2 and any("10.0.20.50" in p for p in problems)


def test_an_extra_lan_cidr_widens_the_scope(tmp_path):
    root = _repo(tmp_path, manifests={
        "apps/a/slice.yaml": _slice("192.168.1.9", name="dmz"),
        "apps/b/slice.yaml": _slice("10.0.10.102"),
    })
    problems, checked = gate.check(root, extra_lan_cidrs=("192.168.1.0/24",))
    assert checked == 2 and any("192.168.1.9" in p for p in problems)


def test_host_vars_exports_are_read_as_well(tmp_path):
    """An export declared on the host rather than the group is the same client
    list; reading only group_vars leaves it uncompared."""
    root = _repo(tmp_path, manifests={"apps/b/slice.yaml": _slice("10.0.10.102")})
    host_vars = root / "ansible" / "inventories" / "prod" / "host_vars"
    (host_vars / "pve-nas-01").mkdir()
    (host_vars / "pve-nas-01" / "storage.yml").write_text(
        textwrap.dedent(_exports("10.0.10.64/27")), encoding="utf-8"
    )
    problems, _checked = gate.check(root)
    assert any("covers no ansible_host" in p for p in problems)


def test_an_extensionless_group_vars_file_is_read(tmp_path):
    """Ansible loads `group_vars/all` with no extension, so globbing the two
    YAML spellings alone left an exports block there unscanned."""
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"all": _exports("10.0.10.64/27")},
    )
    problems, _checked = gate.check(root)
    assert any("covers no ansible_host" in p for p in problems)


def test_a_file_ansible_itself_ignores_is_not_read(tmp_path):
    """A backup or editor leftover beside the vars is not inventory, so parsing
    it would report a file Ansible never reads."""
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml.bak": "{{ not yaml", ".hidden": "{{ not yaml",
                    "nas.yml~": "{{ not yaml"},
    )
    assert gate.check(root) == ([], 1)


def test_an_unreadable_vars_file_is_reported_not_skipped(tmp_path):
    root = _repo(
        tmp_path,
        manifests={"apps/b/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": "a: [unclosed\n"},
    )
    problems, _checked = gate.check(root)
    assert any("its NFS exports were not checked" in p for p in problems)


def test_a_corpus_with_no_endpoint_is_vacuous(tmp_path):
    root = _repo(tmp_path, manifests={"apps/a/deployment.yaml": "kind: Deployment\n"})
    with pytest.raises(gate.Vacuous, match="no EndpointSlice/Endpoints address"):
        gate.check(root)


def test_a_corpus_whose_addresses_are_all_off_lan_is_vacuous(tmp_path):
    """Every address out of scope means the comparison ran over nothing, which
    must not read as a pass."""
    root = _repo(tmp_path, manifests={"apps/a/slice.yaml": _slice("1.1.1.1")})
    with pytest.raises(gate.Vacuous, match="reached the comparison"):
        gate.check(root)


def test_an_inventory_without_addresses_is_vacuous(tmp_path):
    root = _repo(
        tmp_path, hosts="all:\n  hosts:\n    nas:\n      foo: bar\n",
        manifests={"apps/a/slice.yaml": _slice("10.0.10.102")},
    )
    with pytest.raises(gate.Vacuous, match="declares no ansible_host"):
        gate.check(root)


def test_a_missing_inventory_is_vacuous(tmp_path):
    root = _repo(tmp_path, manifests={"apps/a/slice.yaml": _slice("10.0.10.102")})
    (root / "ansible" / "inventories" / "prod" / "hosts.yml").unlink()
    with pytest.raises(gate.Vacuous, match="unreadable"):
        gate.check(root)


def test_a_cluster_config_with_no_lan_key_is_vacuous(tmp_path):
    root = _repo(
        tmp_path,
        config=CONFIG.replace("cluster_lan_cidr: 10.0.10.0/24", "cluster_other: x"),
        manifests={"apps/a/slice.yaml": _slice("10.0.10.102")},
    )
    with pytest.raises(gate.Vacuous, match="has no LAN to scope to"):
        gate.check(root)


def test_an_unparseable_lan_cidr_is_vacuous(tmp_path):
    root = _repo(
        tmp_path,
        config=CONFIG.replace("10.0.10.0/24", "not-a-network"),
        manifests={"apps/a/slice.yaml": _slice("10.0.10.102")},
    )
    with pytest.raises(gate.Vacuous, match="cluster_lan_cidr"):
        gate.check(root)


def test_a_missing_cluster_config_is_vacuous(tmp_path):
    root = _repo(tmp_path, manifests={"apps/a/slice.yaml": _slice("10.0.10.102")})
    (root / "kubernetes" / "infrastructure" / "sources" / "cluster-config.yaml").unlink()
    with pytest.raises(gate.Vacuous, match="cluster-config"):
        gate.check(root)


HOSTS_NAMED = HOSTS.replace(
    "ansible_host: 10.0.10.222", "ansible_host: k3s-02.example.com"
)


def test_a_name_valued_ansible_host_is_skipped_not_fatal(tmp_path, capsys):
    """`ansible_host` is legally a name, and parsing one as an address ended
    the gate in a traceback and exit 1, read by a wrapper as drift."""
    root = _repo(
        tmp_path, hosts=HOSTS_NAMED,
        manifests={"apps/a/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.10.0/24")},
    )
    assert gate.main(["--repo-root", str(root)]) == 0
    assert "2 LAN address(es) checked" in capsys.readouterr().out


def test_a_name_valued_ansible_host_caveats_a_containment_finding(tmp_path):
    """A host behind a name is neither matched nor reported, so a finding that
    rests on containment must say the comparison was partial."""
    root = _repo(
        tmp_path, hosts=HOSTS_NAMED,
        manifests={"apps/a/slice.yaml": _slice("10.0.10.199")},
    )
    problems, _checked = gate.check(root)
    assert any("k3s-02.example.com is not an address" in p for p in problems)


def test_a_group_with_a_name_valued_member_is_compared_on_its_addresses(tmp_path):
    """The partial-group arm parses the same values, so a named member must
    leave both sides of the comparison rather than end the run."""
    root = _repo(
        tmp_path, hosts=HOSTS_NAMED,
        manifests={"apps/a/slice.yaml": _slice("10.0.10.102")},
        group_vars={"nas.yml": _exports("10.0.10.221/32")},
    )
    assert gate.check(root) == ([], 2)


def test_an_inventory_of_only_names_is_vacuous(tmp_path):
    root = _repo(
        tmp_path,
        hosts="all:\n  hosts:\n    nas:\n      ansible_host: nas.example.com\n",
        manifests={"apps/a/slice.yaml": _slice("10.0.10.102")},
    )
    with pytest.raises(gate.Vacuous, match="is a name, not an address"):
        gate.check(root)


def test_main_reports_a_finding_as_exit_1(tmp_path, capsys):
    root = _repo(tmp_path, manifests={"apps/a/slice.yaml": _slice("10.0.10.199")})
    assert gate.main(["--repo-root", str(root)]) == 1
    assert "drifted from the inventory" in capsys.readouterr().err


def test_main_reports_a_clean_run_as_exit_0(tmp_path, capsys):
    root = _repo(tmp_path, manifests={"apps/a/slice.yaml": _slice("10.0.10.102")})
    assert gate.main(["--repo-root", str(root)]) == 0
    assert "1 LAN address(es) checked" in capsys.readouterr().out


def test_main_reports_an_uninspectable_subject_as_exit_2(tmp_path, capsys):
    root = _repo(tmp_path, manifests={"apps/a/deployment.yaml": "kind: Deployment\n"})
    assert gate.main(["--repo-root", str(root)]) == 2
    assert "inspected nothing" in capsys.readouterr().err


def test_main_passes_every_flag_through(tmp_path, capsys):
    root = _repo(tmp_path, manifests={"apps/a/slice.yaml": _slice("192.168.1.9")})
    assert gate.main([
        "--repo-root", str(root),
        "--extra-lan-cidr", "192.168.1.0/24",
        "--manifest-tree", "kubernetes",
        "--inventory", "ansible/inventories/prod",
        "--scoped-group", "k3s",
    ]) == 1
    assert "192.168.1.9" in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
