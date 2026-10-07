"""The ansible pin is held equal across the deploy image and the deploy template.

docker/ansible-deploy/requirements.txt bakes what a deploy job gets from the
image; ci/deploy/deploy-base.yml's `ansible_version` default is the fallback.
"""
from __future__ import annotations

import re
from pathlib import Path

from _helpers import template_input_default

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


def test_image_bakes_the_version_the_template_installs() -> None:
    assert image_pin("ansible") == template_input_default(
        DEPLOY_BASE_TEMPLATE, "ansible_version"
    )
