"""scripts/check-cluster-invariants.py — every arm finds its collision."""
from __future__ import annotations

from pathlib import Path

import pytest

from script_loader import load_script

gate = load_script("check-cluster-invariants.py")

HOSTS = """---
all:
  children:
    pve:
      hosts:
        pve-01:
          ansible_host: 10.0.10.102
          vmid: 102
    guests:
      hosts:
        plex:
          ansible_host: 10.0.10.152
          vmid: 152
"""

CONFIG = """---
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-config
data:
  cluster_lan_cidr: "10.0.10.0/24"
  cluster_k3s_api_vip: "10.0.10.161"
  cluster_metallb_public_vip: "10.0.10.100"
  cluster_metallb_internal_vip: "10.0.10.101"
"""


def _files(tmp_path: Path, hosts: str = HOSTS, config: str = CONFIG):
    (tmp_path / "hosts.yml").write_text(hosts, encoding="utf-8")
    (tmp_path / "cluster-config.yaml").write_text(config, encoding="utf-8")
    return ["--hosts", str(tmp_path / "hosts.yml"),
            "--cluster-config", str(tmp_path / "cluster-config.yaml")]


def test_a_consistent_inventory_passes(tmp_path):
    assert gate.main(_files(tmp_path)) == 0


def test_a_duplicate_vmid_fails(tmp_path, capsys):
    assert gate.main(_files(tmp_path, HOSTS.replace("vmid: 152", "vmid: 102"))) == 1
    assert "duplicate vmid" in capsys.readouterr().err


def test_a_duplicate_address_fails(tmp_path, capsys):
    hosts = HOSTS.replace("ansible_host: 10.0.10.152", "ansible_host: 10.0.10.102")
    assert gate.main(_files(tmp_path, hosts)) == 1
    assert "duplicate ansible_host" in capsys.readouterr().err


def test_a_host_on_a_cluster_vip_fails(tmp_path, capsys):
    hosts = HOSTS.replace("ansible_host: 10.0.10.152", "ansible_host: 10.0.10.161")
    assert gate.main(_files(tmp_path, hosts)) == 1
    assert "cluster_k3s_api_vip" in capsys.readouterr().err


def test_a_host_outside_the_lan_fails(tmp_path, capsys):
    hosts = HOSTS.replace("ansible_host: 10.0.10.152", "ansible_host: 10.0.99.152")
    assert gate.main(_files(tmp_path, hosts)) == 1
    assert "outside cluster_lan_cidr" in capsys.readouterr().err


def test_a_host_named_rather_than_addressed_is_left_alone(tmp_path):
    hosts = HOSTS.replace("ansible_host: 10.0.10.152", "ansible_host: plex.example.test")
    assert gate.main(_files(tmp_path, hosts)) == 0


def test_a_host_in_two_groups_is_not_a_duplicate_of_itself(tmp_path):
    hosts = HOSTS + """    dns:
      hosts:
        plex:
          ansible_host: 10.0.10.152
"""
    assert gate.main(_files(tmp_path, hosts)) == 0


def test_a_host_on_an_undeclared_vip_key_is_caught(tmp_path, capsys):
    config = CONFIG + '  cluster_syslog_vip: "10.0.10.152"\n'
    assert gate.main(_files(tmp_path, config=config)) == 1
    assert "cluster_syslog_vip" in capsys.readouterr().err


def _two_hosts(pve_01: str, pve_02: str) -> str:
    return (
        "---\nall:\n  children:\n    pve:\n      hosts:\n"
        "        pve-01:\n          %s\n        pve-02:\n          %s\n"
        % (pve_01, pve_02)
    )


NO_VIP_CONFIG = CONFIG.split("  cluster_k3s_api_vip")[0]


@pytest.mark.parametrize(
    "hosts, config, needle",
    [
        ("---\nall:\n  children: {}\n", CONFIG, "declares no hosts"),
        (_two_hosts("ansible_host: 10.0.10.102", "ansible_host: 10.0.10.103"),
         CONFIG, "duplicate-vmid arm"),
        (_two_hosts("vmid: 102", "vmid: 103"), CONFIG, "duplicate-ansible_host arm"),
        (_two_hosts("{ansible_host: pve-01.example.test, vmid: 102}",
                    "{ansible_host: pve-02.example.test, vmid: 103}"),
         CONFIG, "IP-literal ansible_host"),
        (HOSTS, NO_VIP_CONFIG, "VIP arm"),
        (HOSTS, CONFIG.replace("cluster_lan_cidr", "cluster_lan_net"), "cluster_lan_cidr"),
        (HOSTS, None, "no cluster config"),
    ],
    ids=["no-hosts", "no-vmid", "no-ansible-host", "named-only", "no-vip",
         "no-lan-cidr", "no-cluster-config"],
)
def test_an_arm_examining_nothing_is_an_operator_error(tmp_path, capsys, hosts, config, needle):
    argv = _files(tmp_path, hosts, config or CONFIG)
    if config is None:
        argv[-1] = str(tmp_path / "absent.yaml")
    assert gate.main(argv) == 2
    assert needle in capsys.readouterr().err


def test_a_missing_inventory_is_an_operator_error(tmp_path):
    assert gate.main(["--hosts", str(tmp_path / "nope.yml")]) == 2


def test_a_malformed_inventory_is_an_operator_error(tmp_path, capsys):
    """A parse failure is not `the inventory has a duplicate address`."""
    hosts = tmp_path / "hosts.yml"
    hosts.write_text("all:\n  hosts: [unclosed\n", encoding="utf-8")
    assert gate.main(["--hosts", str(hosts)]) == 2
    err = capsys.readouterr().err
    assert str(hosts) in err
    assert "Traceback" not in err


def test_a_malformed_cluster_config_is_an_operator_error(tmp_path, capsys):
    argv = _files(tmp_path, config="data: [unclosed\n")
    assert gate.main(argv) == 2
    err = capsys.readouterr().err
    assert "cluster-config.yaml" in err
    assert "Traceback" not in err


# --- alert-rule instance parity ---------------------------------------------

RULES = """---
groups:
  - name: host-exporter
    rules:
      - alert: SlabinfoCollectorDegraded
        expr: up{instance="10.0.10.102:9101"} == 0
      - alert: AnyNodeDown
        expr: up{instance=~"10.0.10.(102|152):9101"} == 0
"""


def _rules(tmp_path: Path, body: str = RULES, name: str = "host-exporter.yaml") -> str:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir(exist_ok=True)
    (rules_dir / name).write_text(body, encoding="utf-8")
    return str(rules_dir)


def test_an_instance_literal_on_an_inventory_host_passes(tmp_path):
    argv = _files(tmp_path) + ["--rules-dir", _rules(tmp_path)]
    assert gate.main(argv) == 0


def test_an_instance_literal_no_host_declares_fails(tmp_path, capsys):
    """The renumber case: the rule still parses and never fires again."""
    body = RULES.replace('instance="10.0.10.102:9101"', 'instance="192.168.1.102:9101"')
    argv = _files(tmp_path) + ["--rules-dir", _rules(tmp_path, body)]
    assert gate.main(argv) == 1
    err = capsys.readouterr().err
    assert "192.168.1.102" in err
    assert "host-exporter.yaml" in err


def test_a_cluster_vip_literal_passes_without_an_allowlist_entry(tmp_path):
    body = RULES.replace('instance="10.0.10.102:9101"', 'instance="10.0.10.161:9101"')
    argv = _files(tmp_path) + ["--rules-dir", _rules(tmp_path, body)]
    assert gate.main(argv) == 0


def test_an_allowlisted_address_passes(tmp_path):
    body = RULES.replace('instance="10.0.10.102:9101"', 'instance="10.0.10.200:9100"')
    argv = _files(tmp_path) + [
        "--rules-dir", _rules(tmp_path, body), "--allow-instance", "10.0.10.200",
    ]
    assert gate.main(argv) == 0


def test_a_stale_allowlist_entry_is_an_operator_error(tmp_path, capsys):
    """An allowlist nobody pins any more hides the next real exception."""
    argv = _files(tmp_path) + [
        "--rules-dir", _rules(tmp_path), "--allow-instance", "10.0.10.254",
    ]
    assert gate.main(argv) == 2
    assert "10.0.10.254" in capsys.readouterr().err


def test_a_rules_tree_with_no_literal_is_an_operator_error(tmp_path, capsys):
    """The arm inspected nothing, so OK would certify a check it never ran."""
    body = RULES.replace('instance="10.0.10.102:9101"', 'instance=~"10.0.10.*"')
    argv = _files(tmp_path) + ["--rules-dir", _rules(tmp_path, body)]
    assert gate.main(argv) == 2
    assert "examining nothing" in capsys.readouterr().err


def test_a_regex_instance_matcher_is_not_held_to_the_inventory(tmp_path):
    """`instance=~` is a deliberate alternation, checked by the rules gates."""
    body = RULES.replace("10.0.10.(102|152)", "10.9.9.(1|2)")
    argv = _files(tmp_path) + ["--rules-dir", _rules(tmp_path, body)]
    assert gate.main(argv) == 0


def test_a_hostname_instance_literal_is_left_to_dns(tmp_path):
    """Only a literal address rots on a renumber; a name is DNS's problem."""
    body = RULES + '      - alert: ByName\n        expr: up{instance="pve-01:9101"} == 0\n'
    argv = _files(tmp_path) + ["--rules-dir", _rules(tmp_path, body)]
    assert gate.main(argv) == 0


def test_a_single_rules_file_is_accepted_as_well_as_a_directory(tmp_path):
    _rules(tmp_path)
    argv = _files(tmp_path) + [
        "--rules-dir", str(tmp_path / "rules" / "host-exporter.yaml"),
    ]
    assert gate.main(argv) == 0


def test_a_missing_rules_path_is_an_operator_error(tmp_path, capsys):
    argv = _files(tmp_path) + ["--rules-dir", str(tmp_path / "absent")]
    assert gate.main(argv) == 2
    assert "no such rules path" in capsys.readouterr().err


def test_the_instance_arm_is_off_unless_a_rules_path_is_given(tmp_path):
    _rules(tmp_path, RULES.replace('instance="10.0.10.102:9101"', 'instance="1.2.3.4"'))
    assert gate.main(_files(tmp_path)) == 0
