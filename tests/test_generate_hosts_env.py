"""Tests for scripts/generate-hosts-env.py.

The group -> variable mapping is consumer data (an export map), so the suite
drives the engine with a synthetic inventory plus the shipped example map
(examples/hosts-env-map.example.yml) — which therefore stays proven-loadable.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
EXAMPLE_MAP = REPO / "examples" / "hosts-env-map.example.yml"

_SPEC = importlib.util.spec_from_file_location(
    "gen_hosts_env",
    REPO / "scripts" / "generate-hosts-env.py",
)
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)  # type: ignore[union-attr]


def _minimal_inventory() -> dict:
    def host(ip):
        return {"ansible_host": ip}

    return {
        "all": {
            "children": {
                "proxmox": {"hosts": {"pve-a": host("10.0.0.2"), "pve-b": host("10.0.0.3")}},
                "dns": {"hosts": {"dns-01": host("10.0.0.150"), "dns-02": host("10.0.0.160")}},
                "mail": {"hosts": {"smtp": host("10.0.0.151")}},
                "plex_servers": {"hosts": {"plex": host("10.0.0.152")}},
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
        # WINDOWS_IP is deliberately not combined into the keyscan set.
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
