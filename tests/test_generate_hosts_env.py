"""Tests for scripts/generate-hosts-env.py.

The suite drives the engine with a synthetic inventory plus the shipped example
map (examples/hosts-env-map.example.yml), which therefore stays proven-loadable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
EXAMPLE_MAP = REPO / "examples" / "hosts-env-map.example.yml"

gen = load_script("generate-hosts-env.py")


def _minimal_inventory() -> dict:
    def host(ip):
        return {"ansible_host": ip}

    return {
        "all": {
            "children": {
                "proxmox": {"hosts": {"pve-a": host("10.0.0.2"), "pve-b": host("10.0.0.3")}},
                "dns": {"hosts": {"dns-01": host("10.0.0.150"), "dns-02": host("10.0.0.160")}},
                "mail": {"hosts": {"smtp": host("10.0.0.151")}},
                "plex_servers": {
                    "hosts": {"plex": dict(host("10.0.0.152"), vm_id=152)}
                },
                "gitlab_servers": {"hosts": {"gitlab": host("10.0.0.153")}},
                "nextcloud_servers": {"hosts": {"nextcloud": host("10.0.0.156")}},
                "immich_servers": {"hosts": {"immich": host("10.0.0.157")}},
                "immich_ml_servers": {"hosts": {"immich-ml": host("10.0.0.158")}},
                "services": {"hosts": {"home": host("10.0.0.154")}},
                "windows_vms": {"hosts": {"windows": host("10.0.0.155")}},
                "k3s_servers": {"hosts": {"s1": host("10.0.0.222"), "s2": host("10.0.0.223")}},
                "k3s_agents": {"hosts": {"a1": host("10.0.0.202")}},
            }
        }
    }


@pytest.fixture()
def exports() -> list[dict]:
    return gen.load_map(EXAMPLE_MAP)[0]


class TestBuild:
    def test_names_vs_ips(self, exports):
        pairs = dict(gen.build(_minimal_inventory(), exports))
        assert pairs["PVE_HOSTS"] == "pve-a pve-b"
        assert pairs["PVE_IPS"] == "10.0.0.2 10.0.0.3"

    def test_group_split(self, exports):
        pairs = dict(gen.build(_minimal_inventory(), exports))
        assert pairs["K3S_SERVERS"] == "10.0.0.222 10.0.0.223"
        assert pairs["K3S_AGENTS"] == "10.0.0.202"

    def test_single_host_selector(self, exports):
        pairs = dict(gen.build(_minimal_inventory(), exports))
        assert pairs["HOME_ASSISTANT_IP"] == "10.0.0.154"

    def test_combine_unions_in_order(self, exports):
        pairs = dict(gen.build(_minimal_inventory(), exports))
        combined = pairs["ALL_SSH_IPS"].split()
        # WINDOWS_IP is not combined into the keyscan set.
        assert "10.0.0.155" not in combined
        assert combined[:2] == ["10.0.0.2", "10.0.0.3"]
        for ip in ("10.0.0.153", "10.0.0.154", "10.0.0.157", "10.0.0.222"):
            assert ip in combined

    def test_optional_group_absent_is_empty(self, exports):
        inv = _minimal_inventory()
        del inv["all"]["children"]["windows_vms"]
        pairs = dict(gen.build(inv, exports))
        assert pairs["WINDOWS_IP"] == ""

    def test_required_group_absent_raises(self, exports):
        inv = _minimal_inventory()
        inv["all"]["children"]["dns"]["hosts"] = {}
        with pytest.raises(ValueError, match="DNS_IPS"):
            gen.build(inv, exports)

    def test_missing_ansible_host_raises(self, exports):
        inv = _minimal_inventory()
        inv["all"]["children"]["proxmox"]["hosts"]["pve-a"] = {}
        with pytest.raises(ValueError, match="ansible_host"):
            gen.build(inv, exports)

    def test_forward_combine_reference_raises(self):
        exports = [
            {"key": "A", "combine": ["B"]},
            {"key": "B", "group": "dns", "value": "ips"},
        ]
        with pytest.raises(ValueError, match="not defined above it"):
            gen.build(_minimal_inventory(), exports)

    def test_unknown_value_kind_raises(self):
        with pytest.raises(ValueError, match="unknown value kind"):
            gen.build(_minimal_inventory(), [{"key": "X", "group": "dns", "value": "bogus"}])


def _nested_inventory() -> dict:
    """A group-of-groups tree: siblings by reference, one child defined inline."""
    inv = _minimal_inventory()
    children = inv["all"]["children"]
    children["k3s"] = {"children": {"k3s_servers": None, "k3s_agents": None}}
    children["base_managed"] = {
        "children": {
            "proxmox": None,
            "dns": None,
            # Defined inline rather than as a top-level sibling.
            "edge": {"hosts": {"edge-01": {"ansible_host": "10.0.0.9"}}},
        }
    }
    children["empty_parent"] = {"children": {}}
    return inv


class TestNestedGroups:
    def test_group_of_groups_unions_children_depth_first(self):
        pairs = dict(
            gen.build(_nested_inventory(), [{"key": "K3S_ALL", "group": "k3s", "value": "ips"}])
        )
        assert pairs["K3S_ALL"] == "10.0.0.222 10.0.0.223 10.0.0.202"

    def test_names_are_stable_across_runs(self):
        spec = [{"key": "BASE", "group": "base_managed", "value": "names"}]
        first = dict(gen.build(_nested_inventory(), spec))["BASE"]
        assert first == "pve-a pve-b dns-01 dns-02 edge-01"
        assert dict(gen.build(_nested_inventory(), spec))["BASE"] == first

    def test_host_selector_searches_nested_members(self):
        pairs = dict(
            gen.build(
                _nested_inventory(),
                [{"key": "EDGE_IP", "group": "base_managed", "host": "edge-01", "value": "ip"}],
            )
        )
        assert pairs["EDGE_IP"] == "10.0.0.9"

    def test_cycle_terminates(self):
        inv = _minimal_inventory()
        inv["all"]["children"]["a"] = {"children": {"b": None}}
        inv["all"]["children"]["b"] = {
            "children": {"a": None},
            "hosts": {"only": {"ansible_host": "10.0.0.1"}},
        }
        pairs = dict(gen.build(inv, [{"key": "A", "group": "a", "value": "ips"}]))
        assert pairs["A"] == "10.0.0.1"

    def test_missing_group_and_empty_group_report_differently(self):
        inv = _nested_inventory()
        with pytest.raises(ValueError, match="not in the inventory"):
            gen.build(inv, [{"key": "X", "group": "nope", "value": "ips"}])
        with pytest.raises(ValueError, match="contains no hosts"):
            gen.build(inv, [{"key": "X", "group": "empty_parent", "value": "ips"}])
        with pytest.raises(ValueError, match="host 'ghost' is not in group"):
            gen.build(inv, [{"key": "X", "group": "dns", "host": "ghost", "value": "ip"}])


class TestRender:
    def test_render_is_shell_sourceable(self, exports, tmp_path):
        pairs = gen.build(_minimal_inventory(), exports)
        rendered = gen.render(pairs, tmp_path / "hosts.yml", "task hosts:sync")
        for line in rendered.splitlines():
            if line and not line.startswith("#"):
                assert '="' in line and line.endswith('"')
        assert "task hosts:sync" in rendered


class TestMain:
    def _write_inventory(self, tmp_path: Path) -> Path:
        p = tmp_path / "hosts.yml"
        p.write_text(yaml.safe_dump(_minimal_inventory()))
        return p

    def test_writes_output_and_is_idempotent(self, tmp_path):
        inv = self._write_inventory(tmp_path)
        out = tmp_path / "hosts.env"
        argv = ["--inventory", str(inv), "--map", str(EXAMPLE_MAP), "--output", str(out)]
        assert gen.main(argv) == 0
        first = out.read_text()
        assert gen.main(argv) == 0
        assert out.read_text() == first
        assert 'PVE_HOSTS="pve-a pve-b"' in first

    def test_map_output_used_when_no_flag(self, tmp_path):
        inv = self._write_inventory(tmp_path)
        m = tmp_path / "map.yml"
        m.write_text(
            yaml.safe_dump(
                {"output": str(tmp_path / "roster.env"),
                 "exports": [{"key": "DNS_IPS", "group": "dns", "value": "ips"}]}
            )
        )
        assert gen.main(["--inventory", str(inv), "--map", str(m)]) == 0
        assert (tmp_path / "roster.env").read_text().endswith(
            'DNS_IPS="10.0.0.150 10.0.0.160"\n'
        )

    def test_missing_inventory_exits_one(self, tmp_path):
        assert gen.main(
            ["--inventory", str(tmp_path / "nope.yml"), "--map", str(EXAMPLE_MAP),
             "--output", str(tmp_path / "o.env")]
        ) == 1

    def test_map_without_exports_exits_one(self, tmp_path):
        inv = self._write_inventory(tmp_path)
        m = tmp_path / "map.yml"
        m.write_text("output: x.env\n")
        assert gen.main(["--inventory", str(inv), "--map", str(m)]) == 1

    def test_no_output_anywhere_exits_one(self, tmp_path):
        inv = self._write_inventory(tmp_path)
        m = tmp_path / "map.yml"
        m.write_text(yaml.safe_dump({"exports": [{"key": "DNS_IPS", "group": "dns"}]}))
        assert gen.main(["--inventory", str(inv), "--map", str(m)]) == 1


class TestHostVarKind:
    """A per-guest inventory value (a Proxmox vmid) reaches the env file."""

    @staticmethod
    def _inventory() -> dict:
        return {
            "all": {
                "children": {
                    "guests": {
                        "hosts": {
                            "plex": {"ansible_host": "10.0.0.152", "vm_id": 152},
                            "gitlab": {"ansible_host": "10.0.0.153", "vm_id": 153},
                        }
                    }
                }
            }
        }

    def test_resolves_one_hosts_variable(self):
        pairs = dict(gen.build(self._inventory(), [
            {"key": "PLEX_VMID", "group": "guests", "host": "plex",
             "value": "hostvar", "var": "vm_id"},
        ]))
        assert pairs["PLEX_VMID"] == "152"

    def test_resolves_the_variable_across_a_group(self):
        pairs = dict(gen.build(self._inventory(), [
            {"key": "GUEST_VMIDS", "group": "guests", "value": "hostvar", "var": "vm_id"},
        ]))
        assert pairs["GUEST_VMIDS"] == "152 153"

    def test_a_missing_var_key_fails_rather_than_emitting_an_empty_value(self):
        with pytest.raises(ValueError, match="hostvar with no `var:`"):
            gen.build(self._inventory(), [
                {"key": "X", "group": "guests", "value": "hostvar"},
            ])

    def test_a_variable_absent_from_the_host_fails_loudly(self):
        inv = self._inventory()
        del inv["all"]["children"]["guests"]["hosts"]["plex"]["vm_id"]
        with pytest.raises(ValueError, match="has no 'vm_id'"):
            gen.build(inv, [
                {"key": "GUEST_VMIDS", "group": "guests", "value": "hostvar", "var": "vm_id"},
            ])

    def test_an_absent_host_is_the_same_loud_failure_as_any_other_kind(self):
        with pytest.raises(ValueError, match="resolved to nothing"):
            gen.build(self._inventory(), [
                {"key": "X", "group": "guests", "host": "absent",
                 "value": "hostvar", "var": "vm_id"},
            ])


class TestGroupVarKind:
    """`groupvar` single-sources a group-level roster into the env file."""

    @staticmethod
    def _inventory() -> dict:
        return {
            "all": {
                "children": {
                    "nas": {
                        "hosts": {"nas-01": {"ansible_host": "10.0.0.2"}},
                        "vars": {"zfs_pools": ["tank", "archive"]},
                    }
                }
            }
        }

    def test_a_group_list_variable_space_joins(self):
        pairs = dict(gen.build(self._inventory(), [
            {"key": "ZFS_POOLS", "group": "nas", "value": "groupvar", "var": "zfs_pools"},
        ]))
        assert pairs["ZFS_POOLS"] == "tank archive"

    def test_a_group_vars_file_is_read_when_the_inventory_declares_nothing(self, tmp_path):
        inv = self._inventory()
        del inv["all"]["children"]["nas"]["vars"]
        (tmp_path / "nas.yml").write_text("zfs_pools: [tank]\n", encoding="utf-8")
        pairs = dict(gen.build(inv, [
            {"key": "ZFS_POOLS", "group": "nas", "value": "groupvar", "var": "zfs_pools"},
        ], tmp_path))
        assert pairs["ZFS_POOLS"] == "tank"

    def test_a_missing_group_var_fails_rather_than_emitting_an_empty_value(self):
        with pytest.raises(ValueError, match="declares no 'absent_var'"):
            gen.build(self._inventory(), [
                {"key": "X", "group": "nas", "value": "groupvar", "var": "absent_var"},
            ])

    def test_an_empty_group_var_fails_loudly(self):
        inv = self._inventory()
        inv["all"]["children"]["nas"]["vars"]["zfs_pools"] = []
        with pytest.raises(ValueError, match="empty 'zfs_pools'"):
            gen.build(inv, [
                {"key": "ZFS_POOLS", "group": "nas", "value": "groupvar",
                 "var": "zfs_pools"},
            ])

    def test_a_groupvar_without_a_var_key_fails(self):
        with pytest.raises(ValueError, match="groupvar with no `var:`"):
            gen.build(self._inventory(), [
                {"key": "X", "group": "nas", "value": "groupvar"},
            ])

    def test_an_undeclared_group_fails_loudly(self):
        with pytest.raises(ValueError, match="not in the inventory"):
            gen.build(self._inventory(), [
                {"key": "X", "group": "absent", "value": "groupvar", "var": "zfs_pools"},
            ])
