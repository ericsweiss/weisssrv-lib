#!/usr/bin/env python3
"""Repo invariants for the weisssrv.infra collection scaffold.

Three things drift silently and break consumers long after the commit that
caused them: the galaxy dependency set vs the copies molecule and the CI image
install, the collection version vs the library tag semantic-release will cut,
and the relative depths the shared molecule base config resolves against.

Run with pytest:
    python3 -m pytest tests/test_ansible_collection.py -v
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
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
    """galaxy.yml is the consumer contract: exactly what role code addresses.

    The two requirements.yml are TEST-environment supersets (they add the
    molecule driver's collections), so the invariant is containment with
    matching pins, not equality — a runtime dependency missing from either, or
    the same collection pinned two ways, is the drift that breaks a consumer.
    """

    def _assert_contains(self, path, dependencies):
        pins = _requirement_pins(path)
        for name, spec in dependencies.items():
            assert name in pins, f"{path.name} is missing the runtime dependency {name}"
            assert pins[name] == spec, f"{path.name} pins {name} as {pins[name]!r}, galaxy.yml as {spec!r}"

    def test_test_requirements_contain_galaxy_dependencies(self, galaxy):
        self._assert_contains(REQUIREMENTS, galaxy["dependencies"])

    def test_ci_image_requirements_contain_galaxy_dependencies(self, galaxy):
        self._assert_contains(IMAGE_REQUIREMENTS, galaxy["dependencies"])

    def test_test_and_ci_image_requirements_agree(self):
        assert _requirement_pins(REQUIREMENTS) == _requirement_pins(IMAGE_REQUIREMENTS)

    def test_pins_carry_an_upper_bound(self, galaxy):
        for name, spec in galaxy["dependencies"].items():
            assert "<" in spec, f"{name} pin {spec!r} has no upper bound"


def require_previous_tag(sr, tags) -> str:
    """The tag semantic-release would compute the next version from, or stop.

    A clone fetched without tags cannot compute the lineage. Locally that is a
    legitimate checkout shape and skipping is right; under $CI it means the only
    gate binding the declared version to the tag semantic-release will cut has
    silently disappeared — and it disappears exactly when the release line has
    grown past the job's clone depth, i.e. when the release is largest. Fail
    there instead of going green having asserted nothing.
    """
    previous = sr.latest_version_tag(tags)
    if previous is not None:
        return previous
    if os.environ.get("CI"):
        pytest.fail(
            "no version tag in this checkout — the release-lineage gate cannot "
            'run. The job needs the tags: set GIT_DEPTH: "0" on it (the release '
            "job already does), or fetch them explicitly."
        )
    pytest.skip("no version tag in this checkout")


class _NoTags:
    """Stand-in for the semantic-release module in a tag-less checkout."""

    @staticmethod
    def latest_version_tag(tags):
        return None


def test_release_lineage_gate_fails_rather_than_skips_in_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="GIT_DEPTH"):
        require_previous_tag(_NoTags, [])


def test_release_lineage_gate_still_skips_outside_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_previous_tag(_NoTags, [])


class TestReleaseLineage:
    """Bind the declared version to the tag semantic-release would actually cut.

    galaxy.yml and cli/pyproject.toml are bumped by hand in the release MR
    (docs/VERSIONING.md), so without this a mistyped `feat!:` subject cuts
    v0.3.0 while the shipped collection and `--version` still say 0.2.0.
    """

    @staticmethod
    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True
        ).stdout

    @pytest.fixture(scope="class")
    def sr(self):
        script = REPO / "scripts" / "semantic-release.py"
        spec = importlib.util.spec_from_file_location("semantic_release_lineage", script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    @pytest.fixture(scope="class")
    def plan(self, sr):
        if shutil.which("git") is None or not (REPO / ".git").exists():
            pytest.skip("not a git checkout")
        tags = self._git("tag", "--list").split()
        previous = require_previous_tag(sr, tags)
        log_output = self._git(
            "log", "--no-merges", "--format=" + sr.LOG_FORMAT, "%s..HEAD" % previous
        )
        return sr.plan_release(tags, log_output)

    def test_declared_version_is_the_one_that_will_be_tagged(self, galaxy, plan):
        expected = plan.version if plan.released else plan.previous_tag[1:]
        assert str(galaxy["version"]) == expected, (
            "galaxy.yml declares %s but semantic-release would cut %s from the commits "
            "since %s — bump galaxy.yml AND cli/pyproject.toml in this MR (or fix the "
            "commit subject that demands the bump)."
            % (galaxy["version"], expected, plan.previous_tag)
        )

    def test_cli_version_is_the_one_that_will_be_tagged(self, plan):
        match = re.search(r'(?m)^version = "([^"]+)"', CLI_PYPROJECT.read_text())
        expected = plan.version if plan.released else plan.previous_tag[1:]
        assert match and match.group(1) == expected

    def test_declared_version_is_never_behind_a_released_tag(self, galaxy, plan):
        declared = tuple(int(p) for p in str(galaxy["version"]).split("."))
        released = tuple(int(p) for p in plan.previous_tag[1:].split("."))
        assert declared >= released


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
