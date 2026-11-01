"""Coverage for generate-host-log-staleness.py.

Every arm runs against a fixture inventory: the derivation, the cluster-wide
rule, the `--check` drift cases and the exit-2 paths for an unreadable source.
"""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

gen = load_script("generate-host-log-staleness.py")

RUNBOOK = "RUNBOOKS.md#per-host-log-shipping-alerts"

SITE = textwrap.dedent(
    """\
    - name: Base
      hosts: base_managed:extra_servers:!deploy_skipped
      roles:
        - weisssrv.infra.base
        - weisssrv.infra.alloy_host
    """
)

HOSTS = textwrap.dedent(
    """\
    all:
      children:
        base_managed:
          children:
            proxmox:
              hosts:
                pve-one:
                pve-two:
        extra_servers:
          hosts:
            app-one:
    """
)


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def run(repo: Path, *extra: str) -> int:
    return gen.main(["--repo-root", str(repo), "--runbook", RUNBOOK, *extra])


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, gen.SITE_YML, SITE)
    write(tmp_path, gen.HOSTS_YML, HOSTS)
    write(tmp_path, gen.OUTPUT, gen.render(["pve-one", "pve-two", "app-one"], RUNBOOK))
    return tmp_path


ALL = ["pve-one", "pve-two", "app-one"]
CYCLE = (
    "    loop_a:\n      children:\n        loop_b:\n          children:\n"
    "            loop_a:\n              hosts:\n                app-one:\n"
)


@pytest.mark.parametrize(
    ("site", "hosts", "expected"),
    [
        (SITE, HOSTS, ALL),
        (SITE, HOSTS.replace("        app-one:", "        pve-one:"), ["pve-one", "pve-two"]),
        (SITE, HOSTS.replace("      hosts:\n        app-one:\n", "      hosts: {}\n"),
         ["pve-one", "pve-two"]),
        (SITE, HOSTS + "    deploy_skipped:\n      hosts:\n        pve-two:\n", ["pve-one", "app-one"]),
        (SITE.replace("!deploy_skipped", "!pve-two"), HOSTS, ["pve-one", "app-one"]),
        (SITE.replace("extra_servers", "extra_servers:app-two"),
         HOSTS + "    loose:\n      hosts:\n        app-two:\n", [*ALL, "app-two"]),
        (SITE.replace("extra_servers", "extra_servers:later_servers"),
         HOSTS + "    later_servers:\n", ALL),
        (SITE.replace("hosts: base_managed:extra_servers:!deploy_skipped",
                      "hosts: [base_managed, extra_servers]"), HOSTS, ALL),
        (SITE.replace("    - weisssrv.infra.alloy_host\n", "    - role: weisssrv.infra.alloy_host\n"),
         HOSTS, ALL),
        ("- just-a-string\n- hosts: extra_servers\n  roles:\n    - role: 5\n    - role: ''\n"
         "    - 7\n" + SITE, HOSTS, ALL),
    ],
    ids=[
        "inventory-order", "host-in-two-groups", "declared-empty-group", "excluded-group",
        "excluded-host", "included-host", "null-bodied-group", "list-pattern", "dict-role",
        "odd-plays-and-roles",
    ],
)
def test_the_play_resolves_to_its_hosts(repo: Path, site: str, hosts: str, expected) -> None:
    write(repo, gen.SITE_YML, site)
    write(repo, gen.HOSTS_YML, hosts)
    assert gen.role_hosts(repo) == expected


def test_an_unknown_exclusion_warns_and_excludes_nothing(repo: Path, capsys) -> None:
    write(repo, gen.SITE_YML, SITE.replace("!deploy_skipped", "!pve-typo"))
    assert gen.role_hosts(repo) == ALL
    assert "excludes 'pve-typo'" in capsys.readouterr().err


def test_a_declared_exclusion_does_not_warn(repo: Path, capsys) -> None:
    write(repo, gen.HOSTS_YML, HOSTS + "    deploy_skipped:\n")
    assert gen.role_hosts(repo) == ALL
    assert "warning" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("site", "hosts", "message"),
    [
        (SITE.replace("extra_servers", "no_such_group"), HOSTS, "neither a group nor a host"),
        (SITE.replace("!deploy_skipped", "!base_managed:!extra_servers"), HOSTS,
         "resolved to no hosts"),
        (SITE + SITE, HOSTS, "expected exactly one alloy_host play, found 2"),
        (SITE.replace("  hosts: base_managed:extra_servers:!deploy_skipped\n", ""), HOSTS,
         "has no hosts: pattern"),
        (SITE.replace("extra_servers", "loop_a"), HOSTS + CYCLE, "cycle"),
        ("- hosts: [unclosed\n", HOSTS, "site.yml"),
        (SITE, "all: [unclosed\n", "hosts.yml"),
        ("hosts: base_managed\nroles: [weisssrv.infra.alloy_host]\n", HOSTS, "not a list of plays"),
    ],
    ids=[
        "undeclared-group", "everything-excluded", "two-plays", "no-hosts", "cycle",
        "unparseable-site", "unparseable-hosts", "not-a-play-list",
    ],
)
def test_an_unreadable_source_exits_two(repo: Path, capsys, site: str, hosts: str, message) -> None:
    write(repo, gen.SITE_YML, site)
    write(repo, gen.HOSTS_YML, hosts)
    assert run(repo, "--check") == 2
    assert message in capsys.readouterr().err


def test_check_passes_on_a_generated_file(repo: Path) -> None:
    assert run(repo, "--check") == 0


def test_check_fails_when_the_inventory_gains_a_host(repo: Path) -> None:
    write(repo, gen.HOSTS_YML, HOSTS + "        app-two:\n")
    assert run(repo, "--check") == 1


@pytest.mark.parametrize(
    "edit",
    [
        lambda b: b.replace('host="app-one"', 'host="app-typo"'),
        lambda b: b[:b.index('      - alert: HostLogShippingStale\n        expr: '
                             'absent_over_time({job="journal", host="app-one"}')],
        lambda b: "# a local note\n" + b[b.index("groups:"):],
        lambda b: b.replace("HostLogShippingStaleAll", "HostLogShippingStaleNone"),
    ],
    ids=["wrong-host", "deleted-host-rule", "edited-header", "deleted-cluster-wide-rule"],
)
def test_a_hand_edit_is_drift(repo: Path, edit) -> None:
    """The write path replaces the whole file, header included."""
    write(repo, gen.OUTPUT, edit((repo / gen.OUTPUT).read_text()))
    assert run(repo, "--check") == 1


def test_a_missing_output_is_drift_not_a_crash(repo: Path) -> None:
    (repo / gen.OUTPUT).unlink()
    assert run(repo, "--check") == 1


def test_writing_makes_check_pass(repo: Path) -> None:
    write(repo, gen.OUTPUT, "groups: []\n")
    assert run(repo) == 0
    assert run(repo, "--check") == 0


def test_the_rules_carry_the_cluster_wide_arm_and_per_host_labels(repo: Path) -> None:
    """A host absent from the inventory is covered by nothing but the cluster-wide arm."""
    doc = yaml.safe_load((repo / gen.OUTPUT).read_text())
    assert [g["name"] for g in doc["groups"]] == ["host-log-staleness"]
    rules = doc["groups"][0]["rules"]
    assert rules[0]["alert"] == "HostLogShippingStaleAll"
    assert rules[0]["expr"] == 'absent_over_time({job="journal"}[45m])'
    assert rules[0]["labels"] == {"severity": "warning", "component": "logging"}
    assert [r["labels"] for r in rules[1:]] == [
        {"severity": "warning", "component": "logging", "host": h, "instance": h} for h in ALL
    ]
    assert "`systemctl status alloy` on {{ $labels.host }}" in rules[1]["annotations"]["description"]


def test_every_rule_links_the_runbook_argument(repo: Path) -> None:
    rules = yaml.safe_load((repo / gen.OUTPUT).read_text())["groups"][0]["rules"]
    assert {r["annotations"]["runbook_url"] for r in rules} == {
        "${cluster_runbook_base_url}/" + RUNBOOK
    }
    assert gen.main(["--repo-root", str(repo), "--runbook", "other.md#x", "--check"]) == 1


def test_the_header_names_the_sync_command_in_one_sentence(repo: Path) -> None:
    body = (repo / gen.OUTPUT).read_text()
    assert body.startswith(
        "---\n"
        "# GENERATED by scripts/generate-host-log-staleness.py from the alloy_host play in\n"
        "# ansible/playbooks/site.yml. Run `task flux:sync-host-log-staleness` after an "
        "inventory change.\n"
        '# Host journald ships as {job="journal"}.\n'
        "groups:\n"
    )


def test_count_is_hosts_plus_the_cluster_wide_rule(repo: Path, capsys) -> None:
    assert run(repo, "--count") == 0
    assert capsys.readouterr().out.strip() == "4"


def _output_is_a_directory(repo: Path) -> None:
    (repo / gen.OUTPUT).unlink()
    (repo / gen.OUTPUT).mkdir()


def _output_parent_is_a_file(repo: Path) -> None:
    parent = (repo / gen.OUTPUT).parent
    shutil.rmtree(parent)
    parent.write_text("a file\n")


@pytest.mark.parametrize(
    ("setup", "argv", "message"),
    [
        (_output_is_a_directory, ["--check"], f"{gen.OUTPUT} unreadable"),
        (lambda r: (r / gen.OUTPUT).write_bytes(b"groups:\n  - name: \xff\xfe\n"), ["--check"],
         "unreadable"),
        (_output_parent_is_a_file, [], f"cannot write {gen.OUTPUT}"),
    ],
    ids=["output-is-a-directory", "non-utf8-output", "unwritable-output"],
)
def test_an_unusable_output_exits_two(repo: Path, capsys, setup, argv, message) -> None:
    setup(repo)
    assert run(repo, *argv) == 2
    assert message in capsys.readouterr().err
