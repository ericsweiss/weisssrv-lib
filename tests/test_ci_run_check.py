"""Unit tests for scripts/ci-run-check.sh: the gate-job driver's accumulators.

Each test sources the driver in a bash subprocess under the errexit settings a
real gate job uses, then drives run_check with synthetic checks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "ci-run-check.sh"


def _run(body: str) -> subprocess.CompletedProcess:
    """Source the driver under a gate job's own `set -euo pipefail` and run body."""
    script = f"set -euo pipefail\n. {LIB}\n{body}\n"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_a_passing_check_leaves_the_job_green():
    res = _run('run_check first true\nrun_check_summary "sync checks"')
    assert res.returncode == 0, res.stderr
    assert "FAILED" not in res.stdout
    assert "Failed sync checks" not in res.stdout


def test_a_failing_check_sets_overall_and_the_next_check_still_runs():
    """The point of the driver: one red check must not abort the job."""
    res = _run(
        'run_check first false\n'
        'run_check second sh -c "echo ran-second"\n'
        'run_check_summary "policy checks"'
    )
    assert res.returncode == 1, res.stdout + res.stderr
    assert "FAILED: first (rc=1)" in res.stdout
    assert "ran-second" in res.stdout
    assert "Failed policy checks: first" in res.stdout


def test_every_failing_check_is_named_in_the_summary():
    res = _run(
        'run_check one false\nrun_check two true\nrun_check three false\n'
        'run_check_summary'
    )
    assert res.returncode == 1
    assert "Failed checks: one three" in res.stdout


def test_a_check_crashing_mid_pipeline_is_a_failure_not_a_pass():
    """`set -eo pipefail` inside the subshell: a dead producer must not read green."""
    res = _run(
        'check_piped() { false | cat; }\n'
        'run_check piped check_piped\n'
        'run_check_summary'
    )
    assert res.returncode == 1
    assert "FAILED: piped" in res.stdout


def test_errexit_is_restored_after_a_failing_check():
    """A check must not leave the job running with errexit off."""
    res = _run(
        'run_check first false\n'
        'case "$-" in *e*) echo errexit-on ;; *) echo errexit-off ;; esac\n'
        'run_check_summary'
    )
    assert res.returncode == 1
    assert "errexit-on" in res.stdout


def test_a_missing_check_command_fails_rather_than_passing_silently():
    res = _run('run_check absent ./no-such-command\nrun_check_summary')
    assert res.returncode == 1
    assert "FAILED: absent" in res.stdout
