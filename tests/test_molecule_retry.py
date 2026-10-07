#!/usr/bin/env python3
"""Tests for scripts/molecule-retry.sh, the pass/fail wrapper every molecule job runs.

A `molecule` stub on PATH decides the outcome, prints the stage banner the
wrapper reads and records its arguments; a `sleep` stub drops the jitter wait.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "molecule-retry.sh"

FAKE_MOLECULE = """\
#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$MOL_LOG"
[ -n "${MOL_STDOUT:-}" ] && echo "$MOL_STDOUT"
for arg in "$@"; do
    [ "$arg" = "destroy" ] && exit 0
done
attempts=$(cat "$MOL_COUNT" 2>/dev/null || echo 0)
attempts=$((attempts + 1))
echo "$attempts" > "$MOL_COUNT"
if [ "$attempts" -le "${MOL_FAIL_UNTIL:-0}" ]; then
    exit "${MOL_FAIL_RC:-1}"
fi
exit 0
"""

FAKE_SLEEP = "#!/usr/bin/env bash\nexit 0\n"


@pytest.fixture()
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("molecule", FAKE_MOLECULE), ("sleep", FAKE_SLEEP)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    log = tmp_path / "molecule.log"
    log.write_text("")

    def _run(**env):
        # The wrapper fails closed on output carrying no stage banner, so a
        # retry case must print one unless it is testing that arm.
        env.setdefault("MOL_STDOUT", "INFO     Running default > prepare")
        proc = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                **os.environ,
                "PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                "MOL_LOG": str(log),
                "MOL_COUNT": str(tmp_path / "count"),
                **{k: str(v) for k, v in env.items()},
            },
        )
        return proc, log.read_text().split()

    return _run


def test_passes_on_the_first_attempt(run):
    proc, calls = run(MOL_SCEN="-s default")
    assert proc.returncode == 0
    assert calls == ["test", "-s", "default"]


def test_retries_after_a_failure_and_then_passes(run):
    proc, calls = run(MOL_FAIL_UNTIL=1)
    assert proc.returncode == 0
    assert calls == ["test", "destroy", "test"]
    assert "destroying + retrying (2/4)" in proc.stdout


def test_exits_with_molecules_code_after_exhausting_the_retries(run):
    proc, calls = run(MOL_FAIL_UNTIL=9, MOL_FAIL_RC=3, MOL_MAX=2)
    assert proc.returncode == 3
    assert calls == ["test", "destroy", "test"]


def test_moves_the_failed_attempts_junit_xml_aside(run, tmp_path):
    junit = tmp_path / "junit"
    junit.mkdir()
    (junit / "attempt-1.xml").write_text("<testsuite/>")
    proc, _ = run(MOL_FAIL_UNTIL=1, JUNIT_OUTPUT_DIR=str(junit))
    assert proc.returncode == 0
    # Only the deciding attempt may reach the pipeline's test report...
    assert list(junit.glob("*.xml")) == []
    # ...but the failed attempt stays downloadable.
    assert (junit / "failed-attempt-1" / "attempt-1.xml").read_text() == "<testsuite/>"


def test_junit_dir_without_xml_is_left_alone(run, tmp_path):
    junit = tmp_path / "junit"
    junit.mkdir()
    proc, _ = run(MOL_FAIL_UNTIL=1, JUNIT_OUTPUT_DIR=str(junit))
    assert proc.returncode == 0
    assert list((junit / "failed-attempt-1").iterdir()) == []


def test_records_the_attempt_count_when_a_dotenv_is_requested(run, tmp_path):
    dotenv = tmp_path / "retry.env"
    proc, _ = run(MOL_FAIL_UNTIL=1, MOLECULE_RETRY_DOTENV=str(dotenv))
    assert proc.returncode == 0
    assert dotenv.read_text().strip() == "MOLECULE_RETRY_ATTEMPTS=2"


def test_records_the_attempt_count_when_the_retries_are_exhausted(run, tmp_path):
    dotenv = tmp_path / "retry.env"
    proc, _ = run(
        MOL_FAIL_UNTIL=9, MOL_FAIL_RC=3, MOL_MAX=2, MOLECULE_RETRY_DOTENV=str(dotenv)
    )
    assert proc.returncode == 3
    assert dotenv.read_text().strip() == "MOLECULE_RETRY_ATTEMPTS=2"


def test_no_dotenv_is_written_when_unrequested(run, tmp_path):
    proc, _ = run()
    assert proc.returncode == 0
    assert not (tmp_path / "retry.env").exists()


def test_a_converge_failure_is_not_retried(run):
    """Reaching converge makes the failure the scenario's verdict."""
    proc, calls = run(
        MOL_FAIL_UNTIL=9,
        MOL_FAIL_RC=2,
        MOL_STDOUT="INFO     Running default > converge",
    )
    assert proc.returncode == 2
    assert calls == ["test"]
    assert "not retrying" in proc.stdout


def test_a_verify_failure_is_not_retried(run):
    """A flaky assertion must not be allowed a second roll of the dice."""
    proc, calls = run(
        MOL_FAIL_UNTIL=1,
        MOL_STDOUT="INFO     Running default > verify",
    )
    assert proc.returncode == 1
    assert calls == ["test"]


def test_a_prepare_failure_is_still_retried(run):
    """The setup stages are what the retry exists for."""
    proc, calls = run(
        MOL_FAIL_UNTIL=1,
        MOL_STDOUT="INFO     Running default > prepare",
    )
    assert proc.returncode == 0
    assert calls == ["test", "destroy", "test"]


def test_the_older_action_banner_is_recognised(run):
    """Molecule's `Action: 'converge'` banner counts as reaching converge."""
    proc, calls = run(MOL_FAIL_UNTIL=9, MOL_FAIL_RC=4, MOL_STDOUT="--> Action: 'converge'")
    assert proc.returncode == 4
    assert calls == ["test"]


def test_the_attempt_count_is_recorded_on_a_converge_failure(run, tmp_path):
    dotenv = tmp_path / "retry.env"
    proc, _ = run(
        MOL_FAIL_UNTIL=9,
        MOL_FAIL_RC=2,
        MOL_STDOUT="INFO     Running default > converge",
        MOLECULE_RETRY_DOTENV=str(dotenv),
    )
    assert proc.returncode == 2
    assert dotenv.read_text().strip() == "MOLECULE_RETRY_ATTEMPTS=1"


def test_output_with_no_stage_banner_is_not_retried(run):
    """An output-format change must not turn real verdicts into silent retries."""
    proc, calls = run(MOL_FAIL_UNTIL=9, MOL_FAIL_RC=5, MOL_STDOUT="something else")
    assert proc.returncode == 5
    assert calls == ["test"]
    assert "no stage banner recognised" in proc.stdout


def test_molecule_output_still_reaches_the_job_log(run):
    """The wrapper tees; a CI log that lost molecule's output is unusable."""
    proc, _ = run(MOL_STDOUT="INFO     Running default > verify")
    assert proc.returncode == 0
    assert "Running default > verify" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
