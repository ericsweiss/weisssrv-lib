"""Non-default molecule scenarios duplicate molecule-shared/base.yml.

The copies let `molecule -s <scenario> test` run without `-c`; CI passes it anyway.
This test holds them equal to the base, minus `platforms` and provisioner paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COLLECTION = REPO / "ansible_collections" / "weisssrv" / "infra"
BASE = COLLECTION / "molecule-shared" / "base.yml"

SHARED_KEYS = ("dependency", "driver", "verifier", "scenario")
SHARED_PROVISIONER_KEYS = ("name", "env", "config_options")


def self_contained_scenarios():
    return sorted(
        path
        for path in COLLECTION.glob("roles/*/molecule/*/molecule.yml")
        if path.parent.name != "default"
    )


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_there_are_self_contained_scenarios_to_check():
    assert self_contained_scenarios(), "no non-default molecule scenarios found"


@pytest.mark.parametrize(
    "scenario", self_contained_scenarios(), ids=lambda p: f"{p.parents[2].name}/{p.parent.name}"
)
def test_scenario_mirrors_the_shared_base(scenario):
    base = load(BASE)
    config = load(scenario)

    for key in SHARED_KEYS:
        assert config.get(key) == base[key], f"{scenario} '{key}' has drifted from base.yml"

    for key in SHARED_PROVISIONER_KEYS:
        assert config["provisioner"].get(key) == base["provisioner"][key], (
            f"{scenario} 'provisioner.{key}' has drifted from base.yml"
        )
