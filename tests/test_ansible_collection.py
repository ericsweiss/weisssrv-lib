#!/usr/bin/env python3
"""Repo invariants for the weisssrv.infra collection scaffold: the galaxy
dependency set, the collection version against the tag semantic-release cuts,
and the relative depths the shared molecule base config resolves against."""

import os
import re
from pathlib import Path

import pytest
import yaml
from _helpers import git, require_git_checkout, require_tags
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
COLLECTION = REPO / "ansible_collections" / "weisssrv" / "infra"
GALAXY = COLLECTION / "galaxy.yml"
RUNTIME = COLLECTION / "meta" / "runtime.yml"
REQUIREMENTS = COLLECTION / "requirements.yml"
MOLECULE_BASE = COLLECTION / "molecule-shared" / "base.yml"
IMAGE_REQUIREMENTS = REPO / "docker" / "molecule-ci" / "requirements.yml"
CLI_PYPROJECT = REPO / "cli" / "pyproject.toml"
README = REPO / "README.md"


def readme_current_release() -> str:
    """The bare semver from README.md's `## Current release` line.

    Anchored on the first non-blank line after the heading — a loose scan would
    match the `<CURRENT_TAG>` snippets and prose elsewhere on the page.
    """
    lines = README.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() != "## current release":
            continue
        for candidate in lines[i + 1:]:
            if not candidate.strip():
                continue
            match = re.match(r"\*\*v(\d+\.\d+\.\d+)\.\*\*", candidate.strip())
            assert match, (
                "README.md's Current release line must open with **v<semver>.**, got: %r"
                % candidate.strip()
            )
            return match.group(1)
    raise AssertionError("README.md has no `## Current release` heading")


def _load(path: Path):
    with path.open() as f:
        return yaml.safe_load(f)


def _requirement_pins(path: Path) -> dict:
    return {c["name"]: c["version"] for c in _load(path)["collections"]}


@pytest.fixture(scope="module")
def galaxy() -> dict:
    return _load(GALAXY)


def stale_plugin_ignore(collection: Path, build_ignore) -> list:
    """Real plugin files that `build_ignore: plugins` would keep out of the artifact."""
    if "plugins" not in (build_ignore or []):
        return []
    return sorted(
        str(p.relative_to(collection))
        for p in (collection / "plugins").rglob("*")
        if p.is_file() and p.name != "README.md" and p.suffix != ".pyc"
    )


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

    def test_version_matches_the_readme_current_release(self, galaxy):
        # README.md's Current release line is the substitution source for every
        # <CURRENT_TAG> pin snippet, so it ships the tag consumers adopt.
        assert str(galaxy["version"]) == readme_current_release(), (
            "README.md's Current release line says v%s but galaxy.yml declares %s — "
            "bump all three of README.md, galaxy.yml and cli/pyproject.toml together."
            % (readme_current_release(), galaxy["version"])
        )

    def test_tags_are_galaxy_legal(self, galaxy):
        for tag in galaxy["tags"]:
            assert re.fullmatch(r"[a-z0-9]+", tag), f"galaxy tag {tag!r} must be lowercase alphanumeric"

    def test_requires_ansible_is_declared(self):
        assert _load(RUNTIME)["requires_ansible"].startswith(">=")

    def test_a_nested_local_collection_copy_is_build_ignored(self, galaxy):
        """A local `ansible-galaxy install` leaves a full copy under .ansible/."""
        assert ".ansible" in galaxy["build_ignore"]

    def test_plugins_are_not_build_ignored_once_one_ships(self, galaxy):
        """The empty scaffold is ignored; a real plugin must reach the artifact."""
        stale = stale_plugin_ignore(COLLECTION, galaxy.get("build_ignore"))
        assert not stale, (
            "galaxy.yml still build_ignores plugins/, so %s would not ship: drop "
            "the entry." % ", ".join(stale)
        )

    def test_the_build_ignore_gate_fires_on_a_real_plugin(self, tmp_path):
        """A gate with nothing to catch today is still proven to catch."""
        modules = tmp_path / "plugins" / "modules"
        modules.mkdir(parents=True)
        (modules / "thing.py").write_text("DOCUMENTATION = ''\n")
        (tmp_path / "plugins" / "README.md").write_text("scaffold\n")
        assert stale_plugin_ignore(tmp_path, ["plugins"]) == ["plugins/modules/thing.py"]
        assert stale_plugin_ignore(tmp_path, ["roles/*/molecule"]) == []


class TestRoleMetadataParity:
    """Every role's galaxy_info min_ansible_version equals meta/runtime.yml's
    floor; nothing else reconciles the two."""

    @pytest.fixture(scope="class")
    def floor(self) -> str:
        """The bare version from meta/runtime.yml's `requires_ansible` floor."""
        requires = _load(RUNTIME)["requires_ansible"]
        match = re.match(r">=\s*(\d+\.\d+)", requires)
        assert match, f"requires_ansible {requires!r} has no >=X.Y floor to compare against"
        return match.group(1)

    @pytest.fixture(scope="class")
    def role_metas(self) -> dict:
        metas = {
            path.parent.parent.name: _load(path)
            for path in sorted((COLLECTION / "roles").glob("*/meta/main.yml"))
        }
        assert metas, "no role meta/main.yml found"
        return metas

    def test_every_role_declares_the_collection_floor(self, role_metas, floor):
        mismatched = {
            role: meta.get("galaxy_info", {}).get("min_ansible_version")
            for role, meta in role_metas.items()
            if str(meta.get("galaxy_info", {}).get("min_ansible_version", "")) != floor
        }
        assert not mismatched, (
            f"meta/runtime.yml requires ansible-core >={floor}; these roles disagree: "
            f"{mismatched}"
        )

    def test_every_role_declares_an_author(self, role_metas):
        missing = sorted(
            role for role, meta in role_metas.items()
            if not meta.get("galaxy_info", {}).get("author")
        )
        assert not missing, f"roles with no galaxy_info.author: {missing}"


class TestDependencyParity:
    """galaxy.yml is the consumer contract. The two requirements.yml are
    test-environment supersets, so the invariant is containment with matching
    pins, not equality."""

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
    """The tag semantic-release computes the next version from, or stop."""
    previous = sr.latest_version_tag(tags)
    if previous is not None:
        return previous
    require_tags([], "release-lineage")


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
    """The declared version binds to the tag semantic-release would cut;
    galaxy.yml and cli/pyproject.toml are bumped by hand (docs/VERSIONING.md)."""

    @pytest.fixture(scope="class")
    def sr(self):
        return load_script("semantic-release.py", register=True)

    @pytest.fixture(scope="class")
    def plan(self, sr):
        require_git_checkout(REPO)
        tags = git(REPO, "tag", "--list").split()
        previous = require_previous_tag(sr, tags)
        log_output = git(
            REPO,
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
    """The base config's relative paths resolve from the dirs molecule uses:
    the scenario dir for ansible-playbook, the role dir for ansible-galaxy."""

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
