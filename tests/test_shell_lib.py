#!/usr/bin/env python3
"""Tests for scripts/shell-lib.sh — the two primitives every delegated-deploy
wrapper builds on. A regression in `ssh_probe`'s option set or `timeout_cmd`'s
fallback chain does not fail loudly: it mis-selects (or hangs on) a target,
which is the one failure mode the callers exist to prevent.

Stubs for `ssh`, `timeout` and `gtimeout` on a controlled PATH decide what each
helper sees, so nothing here touches a real host.

Run with pytest:
    python3 -m pytest tests/test_shell_lib.py -v
"""

import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "shell-lib.sh"
# Absolute: the tests run with PATH set to the stub dir alone, so the
# interpreter itself has to be resolved before that PATH applies.
BASH = shutil.which("bash") or "/bin/bash"

# Records how it was invoked, then runs the rest — so a test can tell which of
# timeout/gtimeout/neither the helper picked AND still observe the real call.
# Absolute shebangs: `/usr/bin/env bash` cannot resolve on the stubs-only PATH.
RECORDING_TIMEOUT = """\
#!%s
printf '%%s %%s\\n' "${0##*/}" "$1" >> "$TRACE"
shift
exec "$@"
""" % BASH

RECORDING_SSH = """\
#!%s
printf 'ssh %%s\\n' "$*" >> "$TRACE"
exit "${SSH_RC:-0}"
""" % BASH


@pytest.fixture()
def shell(tmp_path):
    """Run a snippet with shell-lib.sh sourced; `tools` picks which stubs exist."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    trace.write_text("")

    def _stub(name, body):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    def _run(snippet, tools=("timeout", "ssh"), **env):
        for name in tools:
            _stub(name, RECORDING_SSH if name == "ssh" else RECORDING_TIMEOUT)
        # PATH is ONLY the stub dir: `command -v timeout` must not find the
        # host's real coreutils and make the fallback untestable.
        proc = subprocess.run(
            [BASH, "-c", '. "$1"\n%s' % snippet, "bash", str(LIB)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                "PATH": str(bin_dir),
                "TRACE": str(trace),
                **{k: str(v) for k, v in env.items()},
            },
        )
        return proc, trace.read_text().splitlines()

    return _run


def test_sourcing_has_no_side_effects_under_set_e(shell):
    """Function-only by contract: a caller sources it under `set -e` before
    anything else, so a stray top-level command would abort that caller."""
    proc, trace = shell('set -e\necho sourced', tools=())
    assert proc.returncode == 0
    assert proc.stdout.strip() == "sourced"
    assert trace == []


def test_timeout_cmd_prefers_coreutils_timeout(shell):
    proc, trace = shell('timeout_cmd 9 /bin/echo hi', tools=("timeout",))
    assert proc.returncode == 0
    assert trace == ["timeout 9"]
    assert proc.stdout.strip() == "hi"


def test_timeout_cmd_falls_back_to_gtimeout(shell):
    """macOS ships no `timeout`; coreutils installs it as `gtimeout`."""
    proc, trace = shell('timeout_cmd 9 /bin/echo hi', tools=("gtimeout",))
    assert proc.returncode == 0
    assert trace == ["gtimeout 9"]


def test_timeout_cmd_runs_unbounded_when_neither_exists(shell):
    """The documented last resort: still run the command rather than fail, so a
    box without coreutils is not silently unable to probe anything."""
    proc, trace = shell('timeout_cmd 9 /bin/echo hi', tools=())
    assert proc.returncode == 0
    assert proc.stdout.strip() == "hi"
    assert trace == []


def test_timeout_cmd_propagates_the_commands_exit_code(shell):
    proc, _ = shell('timeout_cmd 9 /bin/sh -c "exit 7"', tools=())
    assert proc.returncode == 7


def test_ssh_probe_passes_the_hardening_options_target_and_command(shell):
    proc, trace = shell('ssh_probe host-a "true"')
    assert proc.returncode == 0
    assert trace == [
        "timeout 6",
        "ssh -o ConnectTimeout=2 -o BatchMode=yes "
        "-o ServerAliveInterval=2 -o ServerAliveCountMax=2 host-a true",
    ]


def test_ssh_probe_is_bounded_by_timeout_cmd(shell):
    """The wall-clock backstop is what saves a host that connects then stalls;
    without it ConnectTimeout alone leaves the wrapper hanging indefinitely."""
    _, trace = shell('ssh_probe host-a "true"')
    assert trace[0] == "timeout 6"


def test_ssh_probe_reports_an_unreachable_target(shell):
    proc, _ = shell('ssh_probe host-a "true"', SSH_RC=255)
    assert proc.returncode == 255


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
