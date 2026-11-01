#!/usr/bin/env python3
"""The pr-agent template reviews merge requests only, behind its `gate`, never
blocks a merge, and refuses to run without both keys.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from script_loader import REPO, SCRIPTS, load_path

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

TEMPLATE = REPO / "ci" / "review" / "pr-agent.yml"
GATE = "$[[ inputs.gate ]]"


def _job(text: str | None = None) -> dict:
    body = ci_yaml.parse_ci(text) if text is not None else ci_yaml.load_ci(TEMPLATE)
    return body["$[[ inputs.job_name ]]"]


def _rule_problems(text: str | None = None) -> list[str]:
    """Ways the rules let the job run outside a gated merge request."""
    rules = _job(text)["rules"]
    problems = []
    first = rules[0] if rules else {}
    if first.get("when") != "never" or "schedule" not in str(first.get("if", "")):
        problems.append("a schedule is not excluded first")
    for rule in rules[1:]:
        condition = str(rule.get("if", ""))
        if "merge_request_event" not in condition:
            problems.append(f"{condition!r} runs outside a merge request")
        if GATE not in condition:
            problems.append(f"{condition!r} is not gated")
    if len(rules) < 2:
        problems.append("no merge request rule")
    return problems


def test_only_gated_merge_requests_run_the_review() -> None:
    assert _rule_problems() == []


@pytest.mark.parametrize(
    ("original", "mutation"),
    [
        (f" && {GATE}", ""),
        ('$CI_PIPELINE_SOURCE == "merge_request_event"', '$CI_COMMIT_BRANCH == "main"'),
    ],
)
def test_a_loosened_rule_is_caught(original: str, mutation: str) -> None:
    """Mutation check: the assertion above is load-bearing, not vacuous."""
    text = TEMPLATE.read_text()
    assert original in text, "the rule moved; update this mutation"
    assert _rule_problems(text.replace(original, mutation))


def test_the_review_never_blocks_a_merge() -> None:
    assert _job()["allow_failure"] is True


def _secrets_step(secrets_source: str, openai_key: str, gitlab_token: str) -> str:
    step = _job()["script"][0]
    for name, value in {
        "secrets_source": secrets_source,
        "openai_key": openai_key,
        "gitlab_token": gitlab_token,
        "op_openai_key_ref": "",
        "op_gitlab_token_ref": "",
    }.items():
        step = step.replace(f"$[[ inputs.{name} ]]", value)
    return step


def _run(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """The step under a PATH that holds no `op`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/")}
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True,
        check=False,
    )


def test_env_mode_with_both_keys_passes(tmp_path: Path) -> None:
    assert _run(_secrets_step("env", "k", "t"), tmp_path).returncode == 0


@pytest.mark.parametrize(("openai_key", "gitlab_token"), [("", "t"), ("k", "")])
def test_env_mode_refuses_a_missing_key(
    tmp_path: Path, openai_key: str, gitlab_token: str
) -> None:
    result = _run(_secrets_step("env", openai_key, gitlab_token), tmp_path)
    assert result.returncode == 1
    assert "needs both keys" in result.stderr


def test_1password_mode_refuses_a_missing_op_cli(tmp_path: Path) -> None:
    result = _run(_secrets_step("1password", "k", "t"), tmp_path)
    assert result.returncode == 1
    assert "needs the op CLI" in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
