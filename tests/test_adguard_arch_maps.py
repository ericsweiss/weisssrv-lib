"""The adguard_home and adguard_sync architecture maps must stay in step.

Both roles fetch a GoReleaser artifact whose name carries an arch token, spelled
the same way by both upstreams; the lookup must fail closed, not default.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles"

MAPS = {
    "adguard_home": "_adguard_home_arch_map",
    "adguard_sync": "_adguard_sync_arch_map",
}


def load_map(role):
    data = yaml.safe_load((ROLES / role / "vars" / "main.yml").read_text(encoding="utf-8"))
    return data[MAPS[role]]


def test_both_roles_ship_an_arch_map():
    for role in MAPS:
        arch_map = load_map(role)
        assert arch_map, f"{role} has an empty arch map"
        assert arch_map["x86_64"] == "amd64"
        assert arch_map["aarch64"] == "arm64"


def test_the_two_maps_agree_on_every_shared_architecture():
    home = load_map("adguard_home")
    sync = load_map("adguard_sync")
    shared = set(home) & set(sync)
    assert shared, "the two arch maps share no architecture"
    mismatched = {arch: (home[arch], sync[arch]) for arch in shared if home[arch] != sync[arch]}
    assert not mismatched, f"arch maps disagree: {mismatched}"


DEFAULT_REINTRODUCED = re.compile(
    r"""default\(\s*['"]amd64['"]|else\s*['"]amd64['"]|get\([^)]*['"]amd64['"]"""
)


def _tasks(role):
    return (ROLES / role / "tasks" / "main.yml").read_text(encoding="utf-8")


def _walk(node):
    """Every task mapping in a tasks file, blocks included."""
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("block", "rescue", "always"):
            if key in node:
                yield from _walk(node[key])


def test_the_roles_look_the_architecture_up_instead_of_defaulting_to_amd64():
    for role in MAPS:
        text = _tasks(role)
        assert MAPS[role] + "[ansible_architecture]" in text, f"{role} does not use its arch map"
        assert not DEFAULT_REINTRODUCED.search(text), f"{role} still falls back to amd64"


def test_the_amd64_default_regex_catches_a_reintroduced_fallback():
    """The negative arm: prove the pattern can fail, not only that it passes."""
    assert DEFAULT_REINTRODUCED.search(
        "{{ _adguard_home_arch_map[ansible_architecture] | default('amd64') }}"
    )
    assert DEFAULT_REINTRODUCED.search(
        '{{ "arm64" if ansible_architecture == "aarch64" else "amd64" }}'
    )


def test_each_role_asserts_the_architecture_is_mapped():
    """The guard itself, not a spelling: deleting the assert must red this."""
    for role, name in MAPS.items():
        tasks = yaml.safe_load(_tasks(role))
        guards = [
            task for task in _walk(tasks)
            if isinstance(task.get("ansible.builtin.assert"), dict)
            and any(
                f"ansible_architecture in {name}" in str(clause)
                for clause in task["ansible.builtin.assert"].get("that") or []
            )
        ]
        assert guards, f"{role} has no assert that {name} covers ansible_architecture"
        assert name in str(guards[0]["ansible.builtin.assert"].get("fail_msg", "")), (
            f"{role}'s guard does not name {name} in its fail_msg"
        )
