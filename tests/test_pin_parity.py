#!/usr/bin/env python3
"""Cross-file pins that are asserted equal in COMMENTS are asserted here too.

Three sets of pins are copied between files that no single tool reads together,
each carrying a comment or a doc sentence promising the copies match. A comment
is not a gate: the copies drift on the next bump and nothing notices.

* `ci/github/ci.example.yml` — the forge-portable GitHub workflow a consumer
  VENDORS rather than includes. Every tool it pins carries a "Matches the
  weisssrv-lib <x> template default" comment, and docs/INCLUDE-CONTRACT.md
  states that "both CI shapes gate on identical tools". Drift means the two
  shapes silently lint under different linters.
* The docker CLI / DinD line — one 27.5.1-with-digests set spread over the
  build template, the library's own release job, its molecule jobs and the
  molecule-ci image. The DinD service is the one component that runs
  PRIVILEGED, and it executes the binaries the sha256 pins protect.
* `docker/molecule-test/Dockerfile`'s `ADGUARD_HOME_VERSION` — the release
  tarball staged in the image so DNS scenarios install from disk. A mismatch
  with the version the scenarios pin is not fatal (the role falls back to
  fetching github.com mid-test), which is exactly why it rots unnoticed: the
  symptom is a slower, flakier job, not a red one.

tests/test_lint_version_parity.py owns the linter pins shared between the
molecule image and the ci/lint templates; this module owns everything else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent

GITHUB_EXAMPLE = REPO / "ci" / "github" / "ci.example.yml"
DOCKER_BUILD_TEMPLATE = REPO / "ci" / "build" / "docker-build.yml"
MOLECULE_CI_DOCKERFILE = REPO / "docker" / "molecule-ci" / "Dockerfile"
MOLECULE_TEST_DOCKERFILE = REPO / "docker" / "molecule-test" / "Dockerfile"
PRE_COMMIT = REPO / "lint" / "pre-commit-config.yaml"
LIB_CI = REPO / ".gitlab-ci.yml"
MOLECULE_JOBS = REPO / ".gitlab" / "ci" / "molecule-jobs.gitlab-ci.yml"
ADGUARD_ROLE = (
    REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "adguard_home"
)


def template_input_default(template: Path, name: str) -> str:
    """The `default:` of one spec:inputs entry (the first YAML document)."""
    spec = next(yaml.safe_load_all(template.read_text()))
    inputs = (spec or {}).get("spec", {}).get("inputs", {})
    assert name in inputs, f"{template} has no input {name!r}"
    return str(inputs[name]["default"])


def workflow_env(name: str) -> str:
    """One entry of the GitHub example workflow's top-level `env:` block."""
    env = yaml.safe_load(GITHUB_EXAMPLE.read_text()).get("env", {})
    assert name in env, f"{GITHUB_EXAMPLE} has no env entry {name!r}"
    return str(env[name])


class TestGitHubExampleWorkflowPins:
    """Each pin the example workflow says it copies from a ci/ template."""

    @pytest.mark.parametrize(
        ("env_name", "template", "input_name"),
        [
            ("YAMLLINT_VERSION", "ci/lint/yaml-lint.yml", "yamllint_version"),
            ("KUSTOMIZE_VERSION", "ci/validate/flux-lint.yml", "kustomize_version"),
            ("KUSTOMIZE_SHA256", "ci/validate/flux-lint.yml", "kustomize_sha256"),
            ("KUBECONFORM_VERSION", "ci/validate/flux-lint.yml", "kubeconform_version"),
            ("KUBECONFORM_SHA256", "ci/validate/flux-lint.yml", "kubeconform_sha256"),
            ("RUFF_VERSION", "ci/lint/python-lint.yml", "ruff_version"),
        ],
        ids=lambda v: v if isinstance(v, str) and v.isupper() else None,
    )
    def test_matches_the_template_default(self, env_name, template, input_name):
        assert workflow_env(env_name) == template_input_default(
            REPO / template, input_name
        )

    def test_shellcheck_matches_the_template_image_tag(self):
        """The shellcheck template pins a TAG, not a version input."""
        image = template_input_default(REPO / "ci/lint/shellcheck.yml", "image")
        assert image.endswith(f":v{workflow_env('SHELLCHECK_VERSION')}"), image

    def test_gitleaks_matches_the_shipped_pre_commit_rev(self):
        """The workflow downloads a release; pre-commit pins the same rev."""
        config = yaml.safe_load(PRE_COMMIT.read_text())
        revs = [
            repo["rev"]
            for repo in config["repos"]
            if "gitleaks" in repo["repo"]
        ]
        assert revs == [f"v{workflow_env('GITLEAKS_VERSION')}"], revs


# `docker:<version>-<variant>@sha256:<digest>` — every pinned reference shape.
_DOCKER_IMAGE_RE = re.compile(
    r"docker:(?P<version>\d+\.\d+\.\d+)-(?P<variant>[a-z]+)@sha256:(?P<digest>[0-9a-f]{64})"
)

_DOCKER_IMAGE_FILES = (DOCKER_BUILD_TEMPLATE, LIB_CI, MOLECULE_JOBS)


def _docker_image_refs() -> list[tuple[str, str, str, str]]:
    """(file, version, variant, digest) for every pinned docker image reference."""
    refs = []
    for path in _DOCKER_IMAGE_FILES:
        for match in _DOCKER_IMAGE_RE.finditer(path.read_text()):
            refs.append(
                (
                    str(path.relative_to(REPO)),
                    match["version"],
                    match["variant"],
                    match["digest"],
                )
            )
    return refs


class TestDockerLineIsOneLine:
    def test_the_scan_finds_every_known_reference(self):
        """A regex that matched nothing would make the assertions below vacuous."""
        refs = _docker_image_refs()
        assert len(refs) >= 4, refs
        assert {ref[0] for ref in refs} == {
            str(p.relative_to(REPO)) for p in _DOCKER_IMAGE_FILES
        }
        assert {ref[2] for ref in refs} >= {"dind", "cli"}

    def test_every_reference_is_on_the_template_version(self):
        expected = template_input_default(DOCKER_BUILD_TEMPLATE, "docker_cli_version")
        offenders = [ref for ref in _docker_image_refs() if ref[1] != expected]
        assert not offenders, f"not on the {expected} line: {offenders}"

    def test_each_variant_resolves_to_one_digest(self):
        by_variant: dict[str, set[str]] = {}
        for _, _, variant, digest in _docker_image_refs():
            by_variant.setdefault(variant, set()).add(digest)
        split = {v: d for v, d in by_variant.items() if len(d) > 1}
        assert not split, f"same image tag, different digests: {split}"

    def test_the_static_cli_tarball_matches_the_template(self):
        """molecule-ci builds the same CLI the template's before_script installs."""
        dockerfile = MOLECULE_CI_DOCKERFILE.read_text()
        version = template_input_default(DOCKER_BUILD_TEMPLATE, "docker_cli_version")
        assert re.findall(r"docker-(\d+\.\d+\.\d+)\.tgz", dockerfile) == [version]

        for arch in ("amd64", "arm64"):
            expected = template_input_default(
                DOCKER_BUILD_TEMPLATE, f"docker_cli_sha256_{arch}"
            )
            assert re.search(
                rf"{arch}\).*DOCKER_SHA256={expected};;", dockerfile
            ), f"{MOLECULE_CI_DOCKERFILE} does not pin {arch} to {expected}"


def _scenario_adguard_pins() -> dict[str, str]:
    """`adguard_home_version` per adguard_home molecule scenario that pins one."""
    pins = {}
    for path in sorted(ADGUARD_ROLE.glob("molecule/*/molecule.yml")):
        match = re.search(
            r"^\s*adguard_home_version:\s*(\S+)\s*$", path.read_text(), re.MULTILINE
        )
        if match:
            pins[path.parent.name] = match.group(1)
    return pins


class TestAdGuardHomeArchivePin:
    """The staged tarball only gets used when its version matches the scenarios.

    `adguard_home_version` has NO role default — it is a required input, because
    a version pin is site data — so the scenarios' own pins are the contract the
    image tracks.
    """

    def test_the_image_pins_a_version(self):
        assert self.image_version(), "molecule-test Dockerfile has no ADGUARD_HOME_VERSION"

    @staticmethod
    def image_version() -> str:
        match = re.search(
            r"^ARG ADGUARD_HOME_VERSION=(\S+)$",
            MOLECULE_TEST_DOCKERFILE.read_text(),
            re.MULTILINE,
        )
        return match.group(1) if match else ""

    def test_both_scenarios_pin_a_version(self):
        pins = _scenario_adguard_pins()
        assert len(pins) >= 2, f"expected the default and tls scenarios, got {pins}"

    def test_every_scenario_pin_matches_the_image(self):
        image = self.image_version()
        offenders = {
            scenario: pin
            for scenario, pin in _scenario_adguard_pins().items()
            if pin != image
        }
        assert not offenders, (
            f"staged archive is v{image}; these scenarios would fall back to "
            f"fetching github.com mid-test: {offenders}"
        )


class TestContractIsDocumented:
    """Both sides name the gate, so a bumper is pointed at the other copy."""

    @pytest.mark.parametrize(
        "path",
        [
            GITHUB_EXAMPLE,
            DOCKER_BUILD_TEMPLATE,
            MOLECULE_CI_DOCKERFILE,
            MOLECULE_TEST_DOCKERFILE,
        ],
        ids=lambda p: str(p.relative_to(REPO)),
    )
    def test_names_this_module(self, path):
        assert "test_pin_parity" in path.read_text()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
