"""The molecule job's script contracts.

The junit sanitizer runs with --strict: without it a stale expectation degrades
to a warning. The DinD wait fails the job when the sidecar never comes up.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from script_loader import SCRIPTS, load_path

REPO = Path(__file__).resolve().parent.parent
CI_FILE = REPO / ".gitlab" / "ci" / "molecule-jobs.gitlab-ci.yml"

# The file carries `!reference`, which yaml.safe_load cannot read.
ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

SANITIZER = "sanitize-junit-expected-failures.py"


def _script_lines() -> list[str]:
    doc = ci_yaml.parse_ci(CI_FILE.read_text(encoding="utf-8"))
    lines: list[str] = []
    for job in doc.values():
        if not isinstance(job, dict):
            continue
        for key in ("before_script", "script", "after_script"):
            part = job.get(key)
            if isinstance(part, list):
                lines.extend(str(x) for x in part)
            elif isinstance(part, str):
                lines.append(part)
    return lines


def _sanitizer_invocations(lines: list[str]) -> list[str]:
    return [line for line in lines if SANITIZER in line]


def test_the_sanitizer_is_invoked_at_all() -> None:
    """A vacuity guard: no invocation would satisfy the strict check trivially."""
    assert _sanitizer_invocations(_script_lines())


def test_every_sanitizer_invocation_is_strict() -> None:
    found = _sanitizer_invocations(_script_lines())
    assert all("--strict" in line for line in found)


def _dind_wait() -> str:
    blocks = [line for line in _script_lines() if "Waiting for DinD" in line]
    assert len(blocks) == 1
    return blocks[0]


@pytest.mark.parametrize(("docker_rc", "job_rc"), [(1, 1), (0, 0)])
def test_the_dind_wait_fails_when_the_sidecar_never_answers(
    tmp_path: Path, docker_rc: int, job_rc: int
) -> None:
    for tool, rc in (("docker", docker_rc), ("sleep", 0)):
        stub = tmp_path / tool
        stub.write_text(f"#!/bin/sh\nexit {rc}\n")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", "-c", _dind_wait()], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == job_rc
    assert ("DinD sidecar not ready" in result.stderr) == bool(job_rc)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
