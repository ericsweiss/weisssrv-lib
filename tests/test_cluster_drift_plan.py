"""ci/validate/cluster-drift-plan.yml keeps the highest gate exit code.

rc 1 is allowed drift; rc 2 is an uninspectable cluster and must stay red, so
a run mixing the two reports 2.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml
from script_loader import SCRIPTS, load_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "validate" / "cluster-drift-plan.yml"
TERRAFORM_TWIN = REPO / "ci" / "validate" / "terraform-drift-plan.yml"

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

_INPUT_RE = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9_]+)\s*\]\]")


def _spec_and_body() -> tuple[dict, str]:
    head, body = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)
    return yaml.safe_load(head)["spec"]["inputs"], body


def _job(gates: str = "placeholder") -> dict:
    """The rendered job. GitLab substitutes a multi-line input into the already
    parsed script value, so the test re-indents it to re-parse the YAML."""
    inputs, body = _spec_and_body()
    values = {name: str(meta.get("default", "")) for name, meta in inputs.items()}
    indent = re.search(r"^( *)\$\[\[ inputs\.gates \]\]", body, re.M).group(1)
    values["gates"] = f"\n{indent}".join(gates.splitlines())
    rendered = _INPUT_RE.sub(lambda m: values[m.group(1)], body)
    return ci_yaml.parse_ci(rendered)["cluster-drift-plan"]


def _run(gates: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", _job(gates)["script"][0]], capture_output=True, text=True
    )


@pytest.mark.parametrize(
    ("gates", "expected"),
    [
        ("drift_gate 'true'", 0),
        ("drift_gate 'exit 1'", 1),
        ("drift_gate 'exit 2'", 2),
        # The finding: an uninspectable gate must not be reported as drift.
        ("drift_gate 'exit 1'\ndrift_gate 'exit 2'", 2),
        ("drift_gate 'exit 2'\ndrift_gate 'exit 1'", 2),
        ("drift_gate 'exit 1'\ndrift_gate 'true'", 1),
    ],
)
def test_the_highest_gate_code_becomes_the_job_code(gates, expected):
    proc = _run(gates)
    assert proc.returncode == expected, proc.stdout + proc.stderr


def test_every_gate_runs_even_after_one_fails():
    """No errexit: a drift finding must not hide the next detector."""
    proc = _run("drift_gate 'echo first; exit 1'\ndrift_gate 'echo second; exit 1'")
    assert proc.returncode == 1
    assert "first" in proc.stdout and "second" in proc.stdout


def test_a_clean_run_says_so():
    assert "No drift" in _run("drift_gate 'true'").stdout


def test_only_rc_one_is_an_allowed_failure():
    """exit_codes: [1] is the contract; a blanket allow_failure would mask rc 2."""
    job = _job()
    assert job["allow_failure"] == {"exit_codes": [1]}


def test_the_job_never_runs_on_a_merge_request():
    """It reads a cluster-admin kubeconfig, so not on unmerged branch code."""
    rules = _job()["rules"]
    assert all("merge_request_event" not in rule.get("if", "") for rule in rules)
    assert all("schedule" in rule.get("if", "") for rule in rules)


def test_gates_is_required():
    """A defaulted gate list would ship a detector that inspects nothing."""
    inputs, _body = _spec_and_body()
    assert "default" not in inputs["gates"]


def test_the_terraform_twin_still_allows_its_own_drift_code():
    """The two detectors differ by tool, not by exit-code discipline."""
    head = yaml.safe_load(TERRAFORM_TWIN.read_text().split("\n---\n", 1)[0])
    assert head["spec"]["inputs"]["job_name"]["default"] == "terraform-drift-plan"
    body = TERRAFORM_TWIN.read_text().split("\n---\n", 1)[1]
    assert "exit_codes: [2]" in body
