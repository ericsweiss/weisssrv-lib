"""Linter pins are held equal across the molecule image and the ci/lint templates.

docker/molecule-ci/requirements.txt and the ci/lint spec:inputs defaults pin the
same three tools. If they drift, the lint stage and the molecule run evaluate the
same roles under different linter versions with no signal. The library's own
.gitlab-ci.yml carries a fourth copy (an explicit yamllint_version override), so
it is held to the same value here.

ansible-core is deliberately outside the contract: no template input pins it,
ansible-lint pulls its own.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO / "docker" / "molecule-ci" / "requirements.txt"
ANSIBLE_LINT_TEMPLATE = REPO / "ci" / "lint" / "ansible-lint.yml"
YAML_LINT_TEMPLATE = REPO / "ci" / "lint" / "yaml-lint.yml"
LIB_CI = REPO / ".gitlab-ci.yml"


def image_pin(package: str) -> str:
    """The full `name==version` / `name<version` requirement line for a package."""
    pattern = re.compile(rf"^{re.escape(package)}\s*([=<>!~]=?.*)$")
    for line in REQUIREMENTS.read_text().splitlines():
        match = pattern.match(line.strip())
        if match:
            return f"{package}{match.group(1).strip()}"
    raise AssertionError(f"{REQUIREMENTS} has no pin for {package}")


def template_input_default(template: Path, name: str) -> str:
    """The `default:` of one spec:inputs entry (the first YAML document)."""
    spec = next(yaml.safe_load_all(template.read_text()))
    inputs = (spec or {}).get("spec", {}).get("inputs", {})
    assert name in inputs, f"{template} has no input {name!r}"
    return str(inputs[name]["default"])


def lib_include_input(local: str, name: str) -> str:
    """The value the library's own pipeline passes for one include input."""
    includes = yaml.safe_load(LIB_CI.read_text())["include"]
    for entry in includes:
        if isinstance(entry, dict) and entry.get("local") == local:
            assert name in entry.get("inputs", {}), f"{local} include does not pass {name!r}"
            return str(entry["inputs"][name])
    raise AssertionError(f"{LIB_CI} does not include {local}")


class TestAnsibleLintParity:
    def test_version_matches_the_molecule_image(self):
        assert f"ansible-lint=={template_input_default(ANSIBLE_LINT_TEMPLATE, 'ansible_lint_version')}" == image_pin("ansible-lint")

    def test_pip_extra_carries_the_image_black_ceiling(self):
        assert image_pin("black") in template_input_default(ANSIBLE_LINT_TEMPLATE, "pip_extra").split()


class TestYamllintParity:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(lambda: template_input_default(YAML_LINT_TEMPLATE, "yamllint_version"), id="template-default"),
            pytest.param(lambda: lib_include_input("/ci/lint/yaml-lint.yml", "yamllint_version"), id="lib-pipeline-override"),
        ],
    )
    def test_matches_the_molecule_image(self, value):
        assert f"yamllint=={value()}" == image_pin("yamllint")


class TestContractIsDocumented:
    """Both sides name the gate, so a bumper is pointed at the other copy."""

    @pytest.mark.parametrize(
        "path", [REQUIREMENTS, ANSIBLE_LINT_TEMPLATE, YAML_LINT_TEMPLATE], ids=lambda p: p.name
    )
    def test_names_this_module(self, path):
        assert "test_lint_version_parity" in path.read_text()
