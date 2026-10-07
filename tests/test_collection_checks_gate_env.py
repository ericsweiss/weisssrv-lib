#!/usr/bin/env python3
"""Runs check-molecule-matrix-coverage.sh against this repo with the env the
collection-checks job sets, so env or path drift reds a test, not the pipeline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-molecule-matrix-coverage.sh"
CI_FILE = REPO / ".gitlab-ci.yml"
GATE = "bash scripts/check-molecule-matrix-coverage.sh"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!reference", lambda ldr, node: ldr.construct_sequence(node))


def _job_variables(job: str) -> dict[str, str]:
    doc = yaml.load(CI_FILE.read_text(), Loader=_Loader)
    assert job in doc, f"{job} job missing from .gitlab-ci.yml"
    body = doc[job]
    assert any(
        GATE in str(step) for step in body.get("script", [])
    ), f"{job} no longer runs {GATE}"
    return {k: str(v) for k, v in body.get("variables", {}).items()}


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **env},
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def job_env() -> dict[str, str]:
    variables = _job_variables("collection-checks")
    return {
        k: variables[k]
        for k in ("CI_FILE", "ROLES_DIR", "INTEGRATION_DIR")
        if k in variables
    }


def test_gate_passes_with_the_job_env(job_env: dict[str, str]):
    assert set(job_env) == {"CI_FILE", "ROLES_DIR", "INTEGRATION_DIR"}
    result = _run(job_env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_job_env_declares_no_integration_suite(job_env: dict[str, str]):
    # The stacks live in the consuming cluster repo; a non-empty path here must
    # resolve, so only the empty declaration is correct.
    assert job_env["INTEGRATION_DIR"] == ""


def test_unresolvable_integration_dir_fails(job_env: dict[str, str]):
    env = {**job_env, "INTEGRATION_DIR": "ansible_collections/weisssrv/infra/nope"}
    result = _run(env)
    assert result.returncode == 2
    assert "does not exist" in result.stderr
