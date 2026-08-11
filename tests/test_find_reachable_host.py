#!/usr/bin/env python3
"""Tests for scripts/find-reachable-host.sh — the entry-point picker every
delegated deploy runs first. It decides WHICH host a subsequent command targets,
so a regression here mis-selects silently rather than failing.

An `ssh` stub on PATH decides which targets answer; $UP lists them.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "find-reachable-host.sh"
BASH = shutil.which("bash") or "/bin/bash"

# Answers only for targets in $UP; 255 (ssh's own "could not connect") otherwise.
# Options are skipped so the target is found positionally, exactly as the real
# ssh would parse them.
FAKE_SSH = """\
#!%s
args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) shift 2 ;;
        *) args+=("$1"); shift ;;
    esac
done
printf '%%s\\n' "${args[0]}" >> "$TRACE"
case " ${UP:-} " in
    *" ${args[0]} "*) exit 0 ;;
esac
exit 255
""" % BASH


@pytest.fixture()
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "ssh"
    stub.write_text(FAKE_SSH)
    stub.chmod(0o755)
    trace = tmp_path / "trace"
    trace.write_text("")

    def _run(*args, up=""):
        # A CLOSED env, like the sibling shell suites: nothing the script or its
        # stub reads may arrive from the ambient environment, or the suite passes
        # and fails by where it runs. Only the real PATH tail is kept so the stub
        # can still reach bash.
        proc = subprocess.run(
            [BASH, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env={"PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                 "TRACE": str(trace), "UP": up},
        )
        return proc, trace.read_text().split()

    return _run


def test_prints_the_first_reachable_target(run):
    proc, probed = run("host-a", "host-b", "host-c", up="host-b host-c")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "host-b"
    # Preference order is the argument order, and it stops at the first hit.
    assert probed == ["host-a", "host-b"]


def test_honours_user_at_host_targets(run):
    proc, _ = run("eric@10.0.0.10", up="eric@10.0.0.10")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "eric@10.0.0.10"


def test_exits_one_with_diagnostics_when_nothing_answers(run):
    proc, probed = run("host-a", "host-b", up="")
    assert proc.returncode == 1
    assert proc.stdout.strip() == ""
    assert "no reachable SSH target" in proc.stderr
    assert "host-a" in proc.stderr and "host-b" in proc.stderr
    # Every candidate is tried before giving up.
    assert probed == ["host-a", "host-b"]


def test_usage_error_is_distinct_from_not_found(run):
    """rc 2 (called wrong) must not read as rc 1 (nothing reachable) — a wrapper
    that retries on 1 would loop forever on a mis-invocation."""
    proc, _ = run()
    assert proc.returncode == 2
    assert "Usage:" in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
