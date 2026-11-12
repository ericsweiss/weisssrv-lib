#!/usr/bin/env python3
"""The terraform-drift-plan template never runs on a merge request, and keeps
drift as the only tolerated exit code. The job reads provider credentials from
the vault, which unmerged branch code must never reach.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from script_loader import SCRIPTS, load_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "validate" / "terraform-drift-plan.yml"

# The template carries `!reference`, which yaml.safe_load cannot read.
ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

GUARD = "$[[ inputs.secrets_guard ]]"
SCHEDULE = '$CI_PIPELINE_SOURCE == "schedule"'
DEFAULT_BRANCH = '$CI_COMMIT_BRANCH == "$[[ inputs.default_branch ]]"'


def _job(text: str) -> dict:
    return ci_yaml.parse_ci(text)["$[[ inputs.job_name ]]"]


def _merge_request_rules(rules: list) -> list:
    return [r for r in rules if "merge_request_event" in str(r.get("if", ""))]


def _ungated_rules(rules: list) -> list:
    return [r for r in rules if GUARD not in str(r.get("if", ""))]


def test_no_rule_runs_on_a_merge_request() -> None:
    assert _merge_request_rules(_job(TEMPLATE.read_text())["rules"]) == []


def test_every_rule_is_secrets_gated() -> None:
    assert _ungated_rules(_job(TEMPLATE.read_text())["rules"]) == []


def test_the_schedule_and_the_post_merge_rule_are_the_only_triggers() -> None:
    """The schedule is the detector; the post-merge run is path-filtered."""
    rules = _job(TEMPLATE.read_text())["rules"]
    schedule = [r for r in rules if r["if"].startswith(SCHEDULE)]
    post_merge = [r for r in rules if r["if"].startswith(DEFAULT_BRANCH)]
    assert len(schedule) == 1 and len(post_merge) == 1 and len(rules) == 2
    assert "changes" not in schedule[0]
    assert post_merge[0]["changes"] == "$[[ inputs.changes ]]"


def test_only_the_drift_exit_code_is_tolerated() -> None:
    """`allow_failure: true` would turn the detector into a no-op."""
    allow_failure = _job(TEMPLATE.read_text())["allow_failure"]
    assert allow_failure == {"exit_codes": [2]}


APPLY = re.compile(r"\bterraform\s+apply\b")


def _applies(script: list) -> list:
    return [line for line in script if APPLY.search(str(line))]


def test_the_script_never_applies() -> None:
    """The template's central claim: reconciling stays a supervised apply."""
    assert _applies(_job(TEMPLATE.read_text())["script"]) == []


def test_the_plan_asks_for_a_detailed_exit_code() -> None:
    """Without it the tolerated exit code 2 never happens and drift reads green."""
    plans = [
        line for line in _job(TEMPLATE.read_text())["script"]
        if "terraform plan" in str(line)
    ]
    assert plans
    assert all("-detailed-exitcode" in str(line) for line in plans)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_the_plan_step_is_posix_shell() -> None:
    """The job image runs the script with sh, so bash-only words break it."""
    text = TEMPLATE.read_text()
    assert "PIPESTATUS" not in text
    assert "Check block assertion failed" in text
