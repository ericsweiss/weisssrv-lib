"""Tests for scripts/generate-hosts-env.py.

The group -> variable mapping is consumer data (an export map), so the suite
drives the engine with a synthetic inventory plus the shipped example map
(examples/hosts-env-map.example.yml) — which therefore stays proven-loadable.

Run via `python3 -m pytest tests`.
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
