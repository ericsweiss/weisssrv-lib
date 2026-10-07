#!/usr/bin/env python3
"""Cross-file pins asserted equal in comments are asserted here too.

The three pin sets: docs/VERSIONING.md. Linter pins live in
tests/test_lint_version_parity.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml
from _helpers import template_input_default

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import CILoader as _CILoader  # noqa: E402

GITHUB_EXAMPLE = REPO / "ci" / "github" / "ci.example.yml"
DOCKER_BUILD_TEMPLATE = REPO / "ci" / "build" / "docker-build.yml"
MOLECULE_CI_DOCKERFILE = REPO / "docker" / "molecule-ci" / "Dockerfile"
MOLECULE_TEST_DOCKERFILE = REPO / "docker" / "molecule-test" / "Dockerfile"
PRE_COMMIT = REPO / "lint" / "pre-commit-config.yaml"
LIB_CI = REPO / ".gitlab-ci.yml"
MOLECULE_JOBS = REPO / ".gitlab" / "ci" / "molecule-jobs.gitlab-ci.yml"
MOLECULE_MATRIX = REPO / "ci" / "internal" / "molecule-matrix.gitlab-ci.yml"
KUBECTL_SETUP = REPO / "ci" / "deploy" / "kubectl-setup.yml"
FLUX_LINT = REPO / "ci" / "validate" / "flux-lint.yml"
DOCKER_DIND_TEMPLATE = REPO / "ci" / "templates" / "docker-dind.yml"
ADGUARD_ROLE = (
    REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "adguard_home"
)
COLLECTION_RUNTIME = (
    REPO / "ansible_collections" / "weisssrv" / "infra" / "meta" / "runtime.yml"
)


_VARS_FALLBACK = re.compile(r"\$\{\{\s*vars\.\w+\s*\|\|\s*'([^']+)'\s*\}\}")


def workflow_env(name: str) -> str:
    """One entry of the GitHub example workflow's top-level `env:` block.

    A `${{ vars.X || 'literal' }}` value resolves to its literal fallback: the
    pin a consumer that sets no repository variable actually runs.
    """
    env = yaml.safe_load(GITHUB_EXAMPLE.read_text()).get("env", {})
    assert name in env, f"{GITHUB_EXAMPLE} has no env entry {name!r}"
    value = str(env[name])
    match = _VARS_FALLBACK.fullmatch(value)
    return match.group(1) if match else value


class TestGitHubExampleWorkflowPins:
    """Each pin the example workflow says it copies from a ci/ template."""

    @pytest.mark.parametrize(
        ("env_name", "template", "input_name"),
        [
            ("YAMLLINT_VERSION", "ci/lint/yaml-lint.yml", "yamllint_version"),
            ("KUSTOMIZE_VERSION", "ci/validate/flux-lint.yml", "kustomize_version"),
            ("PYYAML_VERSION", "ci/validate/flux-lint.yml", "pyyaml_version"),
            ("KUSTOMIZE_SHA256", "ci/validate/flux-lint.yml", "kustomize_sha256"),
            ("KUBECONFORM_VERSION", "ci/validate/flux-lint.yml", "kubeconform_version"),
            ("KUBECONFORM_SHA256", "ci/validate/flux-lint.yml", "kubeconform_sha256"),
            ("RUFF_VERSION", "ci/lint/python-lint.yml", "ruff_version"),
            ("ALLOWED_SKIPS", "ci/validate/flux-lint.yml", "allowed_skips"),
            ("CRD_CATALOG_REF", "ci/validate/flux-lint.yml", "crd_catalog_ref"),
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
class TestLibraryOwnPins:
    """Pins the library holds against itself; nothing else reads them."""

    def test_kubectl_pin_is_within_one_minor_of_the_k8s_line(self):
        """scripts/check-kubectl-version-pin.py reads CONSUMER paths only."""
        kubectl = template_input_default(KUBECTL_SETUP, "kubectl_version")
        k8s = workflow_env("K8S_VERSION")
        km = tuple(int(p) for p in kubectl.lstrip("v").split(".")[:2])
        sm = tuple(int(p) for p in k8s.split(".")[:2])
        assert km[0] == sm[0] and abs(km[1] - sm[1]) <= 1, (
            f"kubectl {kubectl} is more than one minor from K8S_VERSION {k8s}"
        )

    def test_k8s_minor_matches_the_flux_lint_fallback(self):
        """K8S_VERSION is otherwise held equal to nothing."""
        m = re.search(r'K8S_VER="\$\{K8S_VERSION_INPUT:-([\d.]+)\}"', FLUX_LINT.read_text())
        assert m, "flux-lint simple-mode K8S_VER fallback not found"
        assert workflow_env("K8S_VERSION") == m.group(1)


_DIND_INPUTS = (
    "dind_service",
    "docker_cli_version",
    "docker_cli_sha256_amd64",
    "docker_cli_sha256_arm64",
    "buildx_version",
    "buildx_sha256_amd64",
    "buildx_sha256_arm64",
    "login_registry",
    "login_user",
    "login_password",
)


_DIND_RESOURCE_INPUTS = ("service_memory_limit", "service_memory_request")

# Exact, not a floor: a variable added to one side only must be red.
_DIND_SHARED_VARS = frozenset({
    "KUBERNETES_SERVICE_MEMORY_LIMIT", "KUBERNETES_SERVICE_MEMORY_REQUEST",
    "DOCKER_HOST", "DOCKER_TLS_CERTDIR", "DOCKER_BUILDKIT",
    "DOCKER_CLI_VERSION", "DOCKER_CLI_SHA256_AMD64", "DOCKER_CLI_SHA256_ARM64",
    "BUILDX_VERSION", "BUILDX_SHA256_AMD64", "BUILDX_SHA256_ARM64",
})


def _dind_job(path: Path) -> dict:
    """The single job a DinD-shaped template defines, past its `spec:` header."""
    documents = [
        doc for doc in yaml.load_all(path.read_text(), Loader=_CILoader) if doc
    ]
    return list(documents[-1].values())[0]


class TestDockerDindTemplateParity:
    """docker-build.yml copies docker-dind.yml's body; `extends:` is not used."""

    @pytest.mark.parametrize("name", _DIND_INPUTS + _DIND_RESOURCE_INPUTS)
    def test_the_dind_template_declares_the_same_default(self, name):
        assert template_input_default(DOCKER_DIND_TEMPLATE, name) == (
            template_input_default(DOCKER_BUILD_TEMPLATE, name)
        )

    def test_the_services_block_is_the_same(self):
        assert _dind_job(DOCKER_DIND_TEMPLATE)["services"] == (
            _dind_job(DOCKER_BUILD_TEMPLATE)["services"]
        )

    def test_the_shared_variables_carry_the_same_values(self):
        dind = _dind_job(DOCKER_DIND_TEMPLATE)["variables"]
        build = _dind_job(DOCKER_BUILD_TEMPLATE)["variables"]
        assert set(dind) == _DIND_SHARED_VARS, "docker-dind.yml gained/lost a variable"
        assert _DIND_SHARED_VARS <= set(build), sorted(_DIND_SHARED_VARS - set(build))
        assert {k: dind[k] for k in _DIND_SHARED_VARS} == {
            k: build[k] for k in _DIND_SHARED_VARS
        }

    def test_the_bootstrap_and_login_steps_are_byte_identical(self):
        dind = _dind_job(DOCKER_DIND_TEMPLATE)["before_script"]
        build = _dind_job(DOCKER_BUILD_TEMPLATE)["before_script"]
        # Shape first: a step appended to the fragment consumers extend would
        # otherwise be invisible. docker-build.yml carries an extra helper.
        assert len(dind) == 2, "docker-dind.yml gained/lost a before_script step"
        assert build[:2] == dind, "the shared bootstrap/login prefix drifted"
        assert dind[0] == build[0], "the CLI/buildx bootstrap step drifted"
        assert dind[1] == build[1], "the registry-login step drifted"


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

    `adguard_home_version` has no role default, so the scenarios' own pins are
    the contract the image tracks.
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


def _ci_document(path: Path) -> dict:
    """The first non-empty YAML document of a GitLab CI file."""
    return next(doc for doc in yaml.load_all(path.read_text(), Loader=_CILoader) if doc)


def _include_inputs(path: Path, local: str) -> dict:
    """The `inputs:` of one `include: - local: <path>` entry."""
    for entry in _ci_document(path).get("include", []):
        if isinstance(entry, dict) and entry.get("local") == local:
            return entry.get("inputs", {})
    raise AssertionError(f"{path} has no include of {local}")


class TestPyYamlPinParity:
    """The three PyYAML spellings .gitlab-ci.yml says move together."""

    def test_the_three_spellings_agree(self):
        variable = str(_ci_document(LIB_CI)["variables"]["PYYAML_VERSION"])
        include = str(
            _include_inputs(LIB_CI, "/ci/test/python-tests.yml")["pyyaml_version"]
        )
        default = template_input_default(MOLECULE_MATRIX, "pyyaml_version")
        assert variable == include == default, (
            f"PYYAML_VERSION {variable}, python-tests input {include}, "
            f"molecule-matrix default {default}"
        )


class TestAnsibleCoreFloorParity:
    """collection-floor-build proves the floor the collection advertises."""

    def test_the_floor_build_pins_the_advertised_floor(self):
        requires = yaml.safe_load(COLLECTION_RUNTIME.read_text())["requires_ansible"]
        match = re.match(r">=\s*(\d+\.\d+)", requires)
        assert match, f"requires_ansible {requires!r} is not a >= floor"
        floor = match.group(1)
        pinned = str(_ci_document(LIB_CI)["collection-floor-build"]["variables"][
            "ANSIBLE_CORE_FLOOR"
        ])
        assert pinned.startswith(floor + "."), (
            f"collection-floor-build pins ansible-core {pinned}, not the {floor} floor"
        )


class TestContractIsDocumented:
    """Both sides name the gate, so a bumper is pointed at the other copy."""

    @pytest.mark.parametrize(
        "path",
        [
            GITHUB_EXAMPLE,
            DOCKER_BUILD_TEMPLATE,
            DOCKER_DIND_TEMPLATE,
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
