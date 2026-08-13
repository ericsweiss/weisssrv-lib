"""The ansible pin is held equal across the deploy image and the deploy template.

docker/ansible-deploy/requirements.txt bakes the ansible a deploy job gets from
the image; ci/deploy/deploy-base.yml's `ansible_version` default is the one the
same job pip-installs when it does not use the image. A job that switches
`image:` must not silently change ansible version, and nothing else in the repo
would notice — the pair is invisible to both the image build and the template
render.

Deliberately narrow: only that pair. The apt packages and the op/base-image
pins have no second copy to drift against.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO / "docker" / "ansible-deploy" / "requirements.txt"
DEPLOY_BASE_TEMPLATE = REPO / "ci" / "deploy" / "deploy-base.yml"


def image_pin(package: str) -> str:
    """The exact `==` version a package is pinned at in the image requirements."""
    pattern = re.compile(rf"^{re.escape(package)}==(.+)$")
    for line in REQUIREMENTS.read_text().splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    raise AssertionError(f"{REQUIREMENTS} has no `==` pin for {package}")


def template_input_default(template: Path, name: str) -> str:
    """The `default:` of one spec:inputs entry (the first YAML document)."""
    spec = next(yaml.safe_load_all(template.read_text()))
    inputs = (spec or {}).get("spec", {}).get("inputs", {})
    assert name in inputs, f"{template} has no input {name!r}"
    return str(inputs[name]["default"])


def test_image_bakes_the_version_the_template_installs() -> None:
    assert image_pin("ansible") == template_input_default(
        DEPLOY_BASE_TEMPLATE, "ansible_version"
    )


def test_the_image_side_names_this_module() -> None:
    """A bumper of the image pin is pointed at the gate.

    Only the image side carries the pointer: ci/deploy/deploy-base.yml is a
    consumer contract surface, and its rendered `spec:inputs` descriptions are
    not the place to document this repo's internal test wiring.
    """
    assert "test_ansible_deploy_image" in REQUIREMENTS.read_text()
