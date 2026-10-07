#!/usr/bin/env python3
"""The version-bump-bot template's triggers stay pinned to `run_branch` and to a
non-empty `gate` guarding a PAT that can push, and its checker step soft-fails
only the exit codes `check_soft_fail_exit_codes` names.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "maintenance" / "version-bump-bot.yml"
CONTRACT = REPO / "docs" / "INCLUDE-CONTRACT.md"
CONTRACT_HEADING = "## ci/maintenance/version-bump-bot.yml"

GATE = "$[[ inputs.gate ]]"
BRANCH_PIN = '$CI_COMMIT_BRANCH == "$[[ inputs.run_branch ]]"'


def _documents(text: str) -> tuple[dict, dict]:
    """(spec header, job body) of a two-document spec:inputs template."""
    spec, body = yaml.safe_load_all(text)
    return spec, body


def _rule_conditions(text: str) -> list[str]:
    _, body = _documents(text)
    job = body["$[[ inputs.job_name ]]"]
    return [rule["if"] for rule in job["rules"]]


def _violations(text: str) -> list[str]:
    """Rule conditions missing either restriction, as failure messages."""
    bad = []
    for condition in _rule_conditions(text):
        missing = [m for m in (GATE, BRANCH_PIN) if m not in condition]
        if missing:
            bad.append(f"{condition!r} is missing {', '.join(missing)}")
    return bad


def _contract_section() -> str:
    """The template's own section of INCLUDE-CONTRACT.md."""
    text = CONTRACT.read_text()
    start = text.index(CONTRACT_HEADING)
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else len(text)]


def test_every_trigger_is_branch_pinned_and_gated() -> None:
    assert _violations(TEMPLATE.read_text()) == []


def test_there_are_triggers_to_check() -> None:
    """A template whose rules vanished would otherwise pass vacuously."""
    assert len(_rule_conditions(TEMPLATE.read_text())) >= 2


def test_bot_token_description_requires_protection() -> None:
    """Masking hides the value in logs; only protection keeps it off an
    untrusted ref, so the input has to say so."""
    spec, _ = _documents(TEMPLATE.read_text())
    description = spec["spec"]["inputs"]["bot_token"]["description"]
    assert "PROTECT" in description


def _checker_step(text: str) -> str:
    """The script step that runs `check_command` behind the rc narrowing."""
    _, body = _documents(text)
    steps = body["$[[ inputs.job_name ]]"]["script"]
    matching = [s for s in steps if "inputs.check_command" in s]
    assert len(matching) == 1, "expected exactly one checker step"
    return matching[0]


def _run_checker(check_command: str, soft_fail: str = "1") -> int:
    """Exit status of the rendered checker step under the runner's `set -e`."""
    script = _checker_step(TEMPLATE.read_text())
    script = script.replace("$[[ inputs.check_command ]]", check_command)
    script = script.replace("$[[ inputs.check_soft_fail_exit_codes ]]", soft_fail)
    return subprocess.run(
        ["bash", "-c", "set -eo pipefail\n" + script], check=False
    ).returncode


def test_soft_fail_input_defaults_to_updates_found_only() -> None:
    spec, _ = _documents(TEMPLATE.read_text())
    assert spec["spec"]["inputs"]["check_soft_fail_exit_codes"]["default"] == "1"


def test_check_command_description_does_not_recommend_masking() -> None:
    """A blanket `|| true` hides a broken checker as an empty run."""
    spec, _ = _documents(TEMPLATE.read_text())
    description = spec["spec"]["inputs"]["check_command"]["description"]
    assert "|| true" not in description
    assert "check_soft_fail_exit_codes" in description


def test_contract_section_does_not_recommend_masking() -> None:
    """The contract a consumer reads agrees with the template's rc semantics."""
    section = _contract_section()
    assert "check_soft_fail_exit_codes" in section
    assert "Do not wrap it in `|| true`" in section
    assert 'check_command: "\u2026 || true"' not in section


@pytest.mark.parametrize(
    ("check_command", "expected"),
    [
        ("exit 0", 0),
        ("exit 1", 0),
        ("exit 2", 2),
        ("exit 127", 127),
    ],
)
def test_only_listed_exit_codes_are_soft_failed(check_command: str, expected: int) -> None:
    assert _run_checker(check_command) == expected


def test_multi_line_checker_fails_on_its_first_failing_line() -> None:
    """Errexit stays on inside the wrapper, so a later line cannot mask rc."""
    assert _run_checker("exit 2\nexit 0") == 2


def test_extra_soft_fail_codes_are_honoured() -> None:
    assert _run_checker("exit 2", soft_fail="1 2") == 0


def test_soft_fail_codes_apply_to_the_whole_command() -> None:
    """A composite checker's LAST step decides rc, so a real error that exits a
    soft-failed code is swallowed — the input description says so."""
    assert _run_checker("true\nexit 1") == 0
    spec, _ = _documents(TEMPLATE.read_text())
    description = spec["spec"]["inputs"]["check_soft_fail_exit_codes"]["description"]
    assert "WHOLE command" in description


def test_artifacts_publish_nothing_by_default() -> None:
    """The seam is opt-in: a consumer that names no path gets no artifact."""
    spec, _ = _documents(TEMPLATE.read_text())
    inputs = spec["spec"]["inputs"]
    assert inputs["artifact_paths"]["type"] == "array"
    assert inputs["artifact_paths"]["default"] == []
    assert inputs["artifact_expire_in"]["default"] == "30 days"


def test_the_artifacts_block_is_driven_by_the_inputs() -> None:
    """A hardcoded path or retention would make the seam unreachable, and
    `when: always` is what publishes the report of the run that failed."""
    _, body = _documents(TEMPLATE.read_text())
    artifacts = body["$[[ inputs.job_name ]]"]["artifacts"]
    assert artifacts["paths"] == "$[[ inputs.artifact_paths ]]"
    assert artifacts["expire_in"] == "$[[ inputs.artifact_expire_in ]]"
    assert artifacts["when"] == "always"


@pytest.mark.parametrize(("key", "ref"), [
    ("paths", "$[[ inputs.artifact_paths ]]"),
    ("expire_in", "$[[ inputs.artifact_expire_in ]]"),
])
def test_a_hardcoded_artifact_value_is_caught(key: str, ref: str) -> None:
    """Mutation check: the assertion above is load-bearing, not vacuous."""
    mutated = TEMPLATE.read_text().replace(ref, "version-report.json")
    _, body = _documents(mutated)
    assert body["$[[ inputs.job_name ]]"]["artifacts"][key] == "version-report.json"


@pytest.mark.parametrize("dropped", [GATE, BRANCH_PIN])
def test_gate_fails_when_a_restriction_is_removed(dropped: str) -> None:
    """Mutation check: the assertion above is load-bearing, not vacuous."""
    mutated = TEMPLATE.read_text().replace(f' && {dropped}', "")
    assert _violations(mutated), f"removing {dropped} must be caught"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
