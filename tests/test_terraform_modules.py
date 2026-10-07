"""Repo invariant: every terraform/modules/ module ships a README and tests.

docs/README.md links one README per module, and ci/validate/terraform.yml only
proves that SOME module has tests. Every gate here has a negative case.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULES = REPO / "terraform" / "modules"
DOCS_INDEX = REPO / "docs" / "README.md"
UNIFI_VARIABLES = MODULES / "unifi-network" / "variables.tf"

# Frozen against terraform/modules/README.md "Validation regexes are written out
# per validation": the shared forms are hand-copied, so a divergence is silent.
SHIPPED_VALIDATION_REGEXES = frozenset({
    r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$",
    r"^[0-9]{1,3}(\\.[0-9]{1,3}){3}$",
    r"^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$",
    r"^[0-9]{1,3}(\\.[0-9]{1,3}){3}(/[0-9]{1,2})?$",
    r"^([0-9]+h)?([0-9]+m)?([0-9]+s)?$",
    r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$",
    r"^[\\x20-\\x7e]{8,63}$",
})

REPEATED_VALIDATION_REGEXES = {
    r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$": 4,
    r"^[0-9]{1,3}(\\.[0-9]{1,3}){3}$": 5,
}


def module_dirs(root: Path) -> list:
    """Every module directory under `root` (a dir holding a .tf file)."""
    return sorted(
        p for p in root.iterdir() if p.is_dir() and any(p.glob("*.tf"))
    )


MODULE_DIRS = module_dirs(MODULES)


def test_there_are_modules_to_check():
    """Guard the guard: an empty walk makes every assertion below vacuous."""
    assert len(MODULE_DIRS) >= 4


@pytest.mark.parametrize("module", MODULE_DIRS, ids=lambda p: p.name)
def test_every_module_has_a_readme(module: Path):
    assert (module / "README.md").is_file(), (
        "%s has no README.md; docs/README.md links one per module." % module.name
    )


@pytest.mark.parametrize("module", MODULE_DIRS, ids=lambda p: p.name)
def test_the_docs_index_links_the_module_readme(module: Path):
    link = "../terraform/modules/%s/README.md" % module.name
    assert link in DOCS_INDEX.read_text(encoding="utf-8"), (
        "docs/README.md does not link %s" % link
    )


def test_the_collector_only_counts_real_modules(tmp_path):
    """A README-only directory is not a module; a .tf directory is."""
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "main.tf").write_text("")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "README.md").write_text("")
    (tmp_path / "README.md").write_text("")
    assert [p.name for p in module_dirs(tmp_path)] == ["real"]


def has_readme(module: Path) -> bool:
    return (module / "README.md").is_file()


def module_tests(module: Path) -> list:
    """Every terraform test file the module ships."""
    return sorted(module.glob("tests/*.tftest.hcl")) + sorted(
        module.glob("*.tftest.hcl"))


@pytest.mark.parametrize("module", MODULE_DIRS, ids=lambda p: p.name)
def test_every_module_ships_terraform_tests(module: Path):
    assert module_tests(module), (
        "%s ships no *.tftest.hcl; ci/validate/terraform.yml only proves that "
        "one module has tests." % module.name
    )


@pytest.mark.parametrize("module", MODULE_DIRS, ids=lambda p: p.name)
def test_module_tests_cover_a_failure_case(module: Path):
    bodies = [path.read_text(encoding="utf-8") for path in module_tests(module)]
    assert any('run "' in body for body in bodies), (
        "%s declares no `run` block" % module.name
    )
    assert any("expect_failures" in body for body in bodies), (
        "%s asserts no `expect_failures`, so no validation is proven to fire"
        % module.name
    )


def test_the_readme_gate_fails_on_a_module_without_one(tmp_path):
    """The gate is proven to catch, not just to pass today."""
    module = tmp_path / "new-module"
    module.mkdir()
    (module / "main.tf").write_text("")
    assert module_dirs(tmp_path) == [module]
    assert not has_readme(module)


def test_the_tests_gate_fails_on_a_module_without_any(tmp_path):
    module = tmp_path / "new-module"
    module.mkdir()
    (module / "main.tf").write_text("")
    assert module_tests(module) == []


def validation_regexes(path: Path) -> Counter:
    """Every `regex("...")` literal in a .tf file, spelled as it is written."""
    return Counter(re.findall(
        r'regex\("((?:[^"\\]|\\.)*)"', path.read_text(encoding="utf-8")))


def test_unifi_network_ships_only_the_frozen_validation_regexes():
    found = frozenset(validation_regexes(UNIFI_VARIABLES))
    assert found == SHIPPED_VALIDATION_REGEXES, (
        "unifi-network/variables.tf validation regexes drifted from the frozen "
        "set; see terraform/modules/README.md 'Validation regexes are written "
        "out per validation'. Added %r, removed %r"
        % (sorted(found - SHIPPED_VALIDATION_REGEXES),
           sorted(SHIPPED_VALIDATION_REGEXES - found))
    )


def test_the_shared_validation_regexes_are_still_copied_in_full():
    counts = validation_regexes(UNIFI_VARIABLES)
    for pattern, expected in REPEATED_VALIDATION_REGEXES.items():
        assert counts[pattern] == expected, (
            "%r appears %d times, expected %d: a copy was dropped or one "
            "diverged by a character" % (pattern, counts[pattern], expected)
        )


def test_the_regex_parity_gate_catches_a_diverged_copy(tmp_path):
    """One character changed in one port-list copy must red both gates."""
    port_list = r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$"
    mutated = tmp_path / "variables.tf"
    mutated.write_text(
        UNIFI_VARIABLES.read_text(encoding="utf-8").replace(
            port_list, port_list.replace("^[0-9]+", "^[0-9]*", 1), 1),
        encoding="utf-8",
    )
    counts = validation_regexes(mutated)
    assert frozenset(counts) != SHIPPED_VALIDATION_REGEXES
    assert counts[port_list] == REPEATED_VALIDATION_REGEXES[port_list] - 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
