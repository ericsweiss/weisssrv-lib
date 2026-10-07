"""scripts/maintenance-run-with-verify.sh always runs the verify, and reports both."""
from __future__ import annotations

import subprocess

from script_loader import REPO

WRAPPER = REPO / "scripts" / "maintenance-run-with-verify.sh"


def _run(tmp_path, args, verify_body="exit 0", verify_name="post-maintenance-verify.sh", env=None):
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    if verify_body is not None:
        (scripts / verify_name).write_text(
            "#!/usr/bin/env bash\necho verify-ran\n%s\n" % verify_body, encoding="utf-8"
        )
    environment = {"CI_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(WRAPPER), *args],
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
    )


def test_a_successful_command_and_verify_pass(tmp_path):
    proc = _run(tmp_path, ["true"])
    assert proc.returncode == 0
    assert "verify-ran" in proc.stdout
    assert "command OK; verify OK" in proc.stdout


def test_a_failing_command_still_runs_the_verify(tmp_path):
    """The reason the wrapper exists: the verify is not conditional."""
    proc = _run(tmp_path, ["bash", "-c", "exit 3"])
    assert proc.returncode == 3
    assert "verify-ran" in proc.stdout
    assert "command FAILED (rc=3)" in proc.stdout


def test_a_failing_verify_fails_an_otherwise_clean_run(tmp_path):
    proc = _run(tmp_path, ["true"], verify_body="exit 7")
    assert proc.returncode == 7
    assert "verify FAILED (rc=7)" in proc.stdout


def test_the_command_rc_wins_when_both_fail(tmp_path):
    proc = _run(tmp_path, ["bash", "-c", "exit 3"], verify_body="exit 7")
    assert proc.returncode == 3


def test_no_command_is_a_usage_error(tmp_path):
    proc = _run(tmp_path, [])
    assert proc.returncode == 64
    assert "at least one argument" in proc.stderr


def test_an_unreadable_verify_is_a_usage_error(tmp_path):
    """Fail loud rather than reporting a maintenance run nothing verified."""
    proc = _run(tmp_path, ["true"], verify_body=None)
    assert proc.returncode == 64
    assert "not found or not readable" in proc.stderr


def test_verify_script_env_retargets_the_verify(tmp_path):
    proc = _run(
        tmp_path,
        ["true"],
        verify_name="deploy-verify.sh",
        env={"VERIFY_SCRIPT": "scripts/deploy-verify.sh"},
    )
    assert proc.returncode == 0
    assert "verify-ran" in proc.stdout
