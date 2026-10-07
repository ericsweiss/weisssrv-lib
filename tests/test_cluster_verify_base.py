#!/usr/bin/env python3
"""The cluster-verify base stays kubectl-only.

Reusing the Ansible deploy base for an in-cluster check drags in the SSH key,
the keyscan and hosts.env, so a verify job fails on a host it never talks to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import CILoader as _CILoader  # noqa: E402

TEMPLATE = REPO / "ci" / "deploy" / "cluster-verify-base.yml"


def _documents() -> tuple[dict, dict]:
    spec, body = (
        doc for doc in yaml.load_all(TEMPLATE.read_text(), Loader=_CILoader) if doc
    )
    return spec, body


def _job() -> dict:
    return list(_documents()[1].values())[0]


def _steps() -> list[str]:
    return [step for step in _job()["before_script"] if isinstance(step, str)]


def _references() -> list[list[str]]:
    """The `!reference` targets in before_script, in order."""
    return [step for step in _job()["before_script"] if isinstance(step, list)]


def test_the_fragment_is_hidden_and_named_by_an_input() -> None:
    spec, body = _documents()
    name = list(body)[0]
    assert name == "$[[ inputs.fragment_name ]]", name
    assert spec["spec"]["inputs"]["fragment_name"]["default"].startswith("."), (
        "the default name must start with a dot or GitLab runs the fragment"
    )


def test_it_does_not_extend_the_ansible_deploy_base() -> None:
    extends = _job()["extends"]
    extends = [extends] if isinstance(extends, str) else extends
    assert extends == [".install-1password"], extends


def test_it_pulls_in_the_op_cli_and_kubectl_in_that_order() -> None:
    """`extends` replaces before_script, so both fragments come by reference."""
    assert _references() == [
        [".install-1password", "before_script"],
        [".kubectl-setup", "before_script"],
    ], _references()


def test_it_needs_no_ssh_key_keyscan_or_hosts_env() -> None:
    body = "\n".join(_steps())
    for forbidden in ("ssh-keyscan", "id_ed25519", "hosts.env", "ansible"):
        assert forbidden not in body, f"{forbidden} belongs to the deploy base"


def test_it_probes_the_api_before_the_job_script_runs() -> None:
    """A dead API surfaces here, not as a failed check halfway through verify."""
    probe = [step for step in _steps() if step.startswith("kubectl version")]
    assert probe, _steps()
    assert "--request-timeout" in probe[0], probe[0]


def test_verification_is_never_cancelled_mid_run() -> None:
    assert _job()["interruptible"] is False


def test_it_retries_only_on_runner_casualties() -> None:
    assert _job()["retry"]["when"] == ["runner_system_failure"]


def test_it_names_its_own_image() -> None:
    spec, _ = _documents()
    assert _job()["image"] == "$[[ inputs.image ]]"
    default = spec["spec"]["inputs"]["image"]["default"]
    assert default.startswith("python:"), default
