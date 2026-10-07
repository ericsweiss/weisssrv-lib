"""Linter pins are held equal across the molecule image and the ci/lint templates.

docker/molecule-ci/requirements.txt, the ci/lint defaults and this repo's
yamllint override pin the same tools. Contract: docs/VERSIONING.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from _helpers import template_input_default

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
