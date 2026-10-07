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


def _job(text: str) -> dict:
    return ci_yaml.parse_ci(text)["$[[ inputs.job_name ]]"]


def _merge_request_rules(rules: list) -> list:
    return [r for r in rules if "merge_request_event" in str(r.get("if", ""))]


def _ungated_rules(rules: list) -> list:
    return [r for r in rules if GUARD not in str(r.get("if", ""))]


def test_no_rule_runs_on_a_merge_request() -> None:
    assert _merge_request_rules(_job(TEMPLATE.read_text())["rules"]) == []


def test_the_merge_request_assertion_is_load_bearing() -> None:
    """Mutation: an added merge_request_event rule must be reported."""
    rules = _job(TEMPLATE.read_text())["rules"]
    rules.append({"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'})
    assert _merge_request_rules(rules) != []


def test_every_rule_is_secrets_gated() -> None:
    assert _ungated_rules(_job(TEMPLATE.read_text())["rules"]) == []


def test_there_are_rules_to_check() -> None:
    """A template with no rules would satisfy both assertions vacuously."""
    assert len(_job(TEMPLATE.read_text())["rules"]) >= 2


def test_only_the_drift_exit_code_is_tolerated() -> None:
    """`allow_failure: true` would turn the detector into a no-op."""
    allow_failure = _job(TEMPLATE.read_text())["allow_failure"]
    assert allow_failure == {"exit_codes": [2]}
    assert not isinstance(allow_failure, bool)


APPLY = re.compile(r"\bterraform\s+apply\b")


def _applies(script: list) -> list:
    return [line for line in script if APPLY.search(str(line))]


def test_the_script_never_applies() -> None:
    """The template's central claim: reconciling stays a supervised apply."""
    assert _applies(_job(TEMPLATE.read_text())["script"]) == []


def test_the_never_applies_assertion_is_load_bearing() -> None:
    script = _job(TEMPLATE.read_text())["script"] + ["terraform apply -auto-approve"]
    assert _applies(script) != []


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
