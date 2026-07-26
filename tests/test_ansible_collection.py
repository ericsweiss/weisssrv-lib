#!/usr/bin/env python3
"""Repo invariants for the weisssrv.infra collection scaffold.

Three things drift silently and break consumers long after the commit that
caused them: the galaxy dependency set vs the copies molecule and the CI image
install, the collection version vs the library tag, and the relative depths the
shared molecule base config resolves against.

Run with pytest:
    python3 -m pytest tests/test_ansible_collection.py -v
"""

import os
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COLLECTION = REPO / "ansible_collections" / "weisssrv" / "infra"
GALAXY = COLLECTION / "galaxy.yml"
RUNTIME = COLLECTION / "meta" / "runtime.yml"
REQUIREMENTS = COLLECTION / "requirements.yml"
MOLECULE_BASE = COLLECTION / "molecule-shared" / "base.yml"
IMAGE_REQUIREMENTS = REPO / "docker" / "molecule-ci" / "requirements.yml"
CLI_PYPROJECT = REPO / "cli" / "pyproject.toml"


def _load(path: Path):
    with path.open() as f:
        return yaml.safe_load(f)


def _requirement_pins(path: Path) -> dict:
    return {c["name"]: c["version"] for c in _load(path)["collections"]}


@pytest.fixture(scope="module")
def galaxy() -> dict:
    return _load(GALAXY)


class TestGalaxyMetadata:
    def test_identity(self, galaxy):
        assert galaxy["namespace"] == "weisssrv"
        assert galaxy["name"] == "infra"

    def test_required_fields_present(self, galaxy):
        # ansible-galaxy refuses to build without these.
        for key in ("version", "readme", "authors", "description", "license", "repository"):
            assert galaxy.get(key), f"galaxy.yml is missing {key}"

    def test_readme_exists(self, galaxy):
        assert (COLLECTION / galaxy["readme"]).is_file()

    def test_version_matches_the_cli_distribution(self, galaxy):
        # One library tag versions the whole repo (docs/VERSIONING.md).
        match = re.search(r'(?m)^version = "([^"]+)"', CLI_PYPROJECT.read_text())
        assert match, "cli/pyproject.toml has no version"
        assert str(galaxy["version"]) == match.group(1)

    def test_tags_are_galaxy_legal(self, galaxy):
        for tag in galaxy["tags"]:
            assert re.fullmatch(r"[a-z0-9]+", tag), f"galaxy tag {tag!r} must be lowercase alphanumeric"

    def test_requires_ansible_is_declared(self):
        assert _load(RUNTIME)["requires_ansible"].startswith(">=")


class TestDependencyParity:
    """galaxy.yml is the consumer contract; the two requirements.yml are copies."""

    def test_test_requirements_match_galaxy_dependencies(self, galaxy):
        assert _requirement_pins(REQUIREMENTS) == galaxy["dependencies"]

    def test_ci_image_requirements_match_galaxy_dependencies(self, galaxy):
        assert _requirement_pins(IMAGE_REQUIREMENTS) == galaxy["dependencies"]

    def test_pins_carry_an_upper_bound(self, galaxy):
        for name, spec in galaxy["dependencies"].items():
            assert "<" in spec, f"{name} pin {spec!r} has no upper bound"


class TestMoleculeBasePaths:
    """The base config's relative paths, resolved from the dirs molecule uses.

    ansible-playbook runs with CWD = the scenario dir; ansible-galaxy runs with
    CWD = the role dir. A tree reshape breaks these silently — nothing fails
    until a scenario runs in CI.
    """

    ROLE_DIR = COLLECTION / "roles" / "somerole"
    SCENARIO_DIR = ROLE_DIR / "molecule" / "default"

    @pytest.fixture(scope="class")
    def base(self) -> dict:
        return _load(MOLECULE_BASE)

    def _from(self, start: Path, relative: str) -> Path:
        return Path(os.path.normpath(start / relative))

    def test_galaxy_requirements_resolve_from_the_role_dir(self, base):
        options = base["dependency"]["options"]
        for key in ("role-file", "requirements-file"):
            assert self._from(self.ROLE_DIR, options[key]) == REQUIREMENTS

    def test_prepare_playbook_resolves_from_the_scenario_dir(self, base):
        prepare = self._from(self.SCENARIO_DIR, base["provisioner"]["playbooks"]["prepare"])
        assert prepare.is_file()

    def test_roles_path_resolves_to_the_collection_roles_dir(self, base):
        roles_path = base["provisioner"]["env"]["ANSIBLE_ROLES_PATH"]
        assert self._from(self.SCENARIO_DIR, roles_path) == COLLECTION / "roles"

    def test_collections_path_starts_at_the_repo_root(self, base):
        entries = base["provisioner"]["env"]["ANSIBLE_COLLECTIONS_PATH"].split(":")
        assert self._from(self.SCENARIO_DIR, entries[0]) == REPO

    def test_collections_path_keeps_the_ansible_defaults(self, base):
        entries = base["provisioner"]["env"]["ANSIBLE_COLLECTIONS_PATH"].split(":")
        # Dropping these would hide the galaxy dependencies the `dependency`
        # step installs (molecule sets no collections path of its own).
        assert entries[1:] == ["~/.ansible/collections", "/usr/share/ansible/collections"]
