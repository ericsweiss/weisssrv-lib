#!/usr/bin/env python3
"""scripts/shell-lib.sh `ssh_probe` and `timeout_cmd` keep their option set and
fallback chain. `ssh`, `timeout` and `gtimeout` are PATH stubs here.
"""

import os
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


# Replays a scripted kubectl: KUBECTL_OUT on stdout or stderr, KUBECTL_RC as
# the exit status, so a test can drive each of the three read outcomes.
RECORDING_KUBECTL = """\
#!%s
printf 'kubectl %%s\\n' "$*" >> "$TRACE"
if [ "${KUBECTL_RC:-0}" -eq 0 ]; then
    printf '%%s' "${KUBECTL_OUT:-}"
else
    printf '%%s' "${KUBECTL_OUT:-}" >&2
fi
exit "${KUBECTL_RC:-0}"
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
        bodies = {"ssh": RECORDING_SSH, "kubectl": RECORDING_KUBECTL}
        for name in tools:
            _stub(name, bodies.get(name, RECORDING_TIMEOUT))
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
    box without coreutils is not silently unable to probe anything — but say so
    on stderr, since the wall-clock bound is gone."""
    proc, trace = shell('timeout_cmd 9 /bin/echo hi', tools=())
    assert proc.returncode == 0
    assert proc.stdout.strip() == "hi"
    assert trace == []
    assert "UNBOUNDED" in proc.stderr


def test_unbounded_warning_is_emitted_once_per_shell(shell):
    proc, _ = shell(
        'timeout_cmd 9 /bin/echo a\ntimeout_cmd 9 /bin/echo b', tools=()
    )
    assert proc.returncode == 0
    assert proc.stderr.count("UNBOUNDED") == 1


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


def _kubectl_read(shell, out, rc):
    return shell(
        'set -e\nif json="$(kubectl_read secret vpn-credentials -n downloads)"; then\n'
        '  echo "OK:$json"\nelse\n  echo "RC:$?"\nfi',
        tools=("kubectl",),
        KUBECTL_OUT=out,
        KUBECTL_RC=rc,
    )


def test_kubectl_read_prints_the_object_when_it_exists(shell):
    proc, _ = _kubectl_read(shell, '{"kind":"Secret"}', 0)
    assert proc.returncode == 0
    assert proc.stdout.strip() == 'OK:{"kind":"Secret"}'


def test_kubectl_read_reports_notfound_distinctly(shell):
    proc, _ = _kubectl_read(
        shell, 'Error from server (NotFound): secrets "vpn-credentials" not found', 1
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "RC:3"


def test_an_unreadable_cluster_is_a_failed_read_not_an_absent_object(shell):
    """The whole point: `2>/dev/null || true` would hand the caller the same
    empty string as a genuinely absent object, and the caller prints the wrong
    remedy."""
    proc, _ = _kubectl_read(
        shell, "The connection to the server 10.0.0.1:6443 was refused", 1
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "RC:1"
    assert "was refused" in proc.stderr


def test_a_forbidden_read_is_a_failed_read(shell):
    proc, _ = _kubectl_read(
        shell, 'Error from server (Forbidden): secrets is forbidden', 1
    )
    assert proc.stdout.strip() == "RC:1"


def _bash(snippet: str) -> subprocess.CompletedProcess:
    """Run a snippet with shell-lib.sh sourced and the real PATH (grep is needed)."""
    return subprocess.run(
        [BASH, "-c", 'set -euo pipefail\n. "$1"\n%s' % snippet, "bash", str(LIB)],
        capture_output=True,
        text=True,
    )


class TestCapturedMatch:
    """The pipefail hazard the helper exists to remove."""

    def test_a_piped_grep_q_turns_a_match_into_a_failure(self):
        """The mistake: grep exits on the first hit, the producer takes SIGPIPE,
        and pipefail returns its status — so a PASS reads as a FAIL."""
        proc = _bash('if yes abc | grep -q abc; then echo MATCH; else echo "RC:$?"; fi')
        assert proc.stdout.strip().startswith("RC:"), proc.stdout

    def test_the_helper_reports_the_same_match_as_a_match(self):
        proc = _bash('out=$(printf "abc\\n"); if captured_match "$out" abc; '
                     'then echo MATCH; else echo "RC:$?"; fi')
        assert proc.stdout.strip() == "MATCH", proc.stdout + proc.stderr

    def test_an_absent_pattern_is_a_clean_non_match(self):
        proc = _bash('out=$(printf "abc\\n"); if captured_match "$out" zzz; '
                     'then echo MATCH; else echo "NOMATCH:$?"; fi')
        assert proc.stdout.strip() == "NOMATCH:1", proc.stdout + proc.stderr

    def test_a_pattern_starting_with_a_dash_is_not_read_as_an_option(self):
        proc = _bash('out=$(printf -- "--verbose\\n"); '
                     'if captured_match "$out" -- ; then echo MATCH; else echo "RC:$?"; fi')
        assert proc.stdout.strip() == "MATCH", proc.stdout + proc.stderr


class TestProbeWrappers:
    """`url_contains` / `ssh_contains` separate a failed fetch from a non-match."""

    def _stub(self, tmp_path, name: str, body: str) -> dict:
        stub = tmp_path / name
        stub.write_text("#!%s\n%s" % (BASH, body))
        stub.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = "%s:%s" % (tmp_path, env["PATH"])
        return env

    def _run(self, snippet: str, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, "-c", 'set -euo pipefail\n. "$1"\n%s' % snippet, "bash", str(LIB)],
            capture_output=True, text=True, env=env,
        )

    def test_a_matching_body_passes(self, tmp_path):
        env = self._stub(tmp_path, "curl", 'printf "hello world\\n"\n')
        proc = self._run(
            'if url_contains http://x hello; then echo MATCH; else echo "RC:$?"; fi', env
        )
        assert proc.stdout.strip() == "MATCH", proc.stdout + proc.stderr

    def test_a_failed_fetch_is_not_a_clean_non_match(self, tmp_path):
        """CRITICAL mutation: dropping the `|| return 1` would make an
        unreachable endpoint indistinguishable from a body that lacks the
        pattern, and both would read as a plain non-match."""
        env = self._stub(tmp_path, "curl", "exit 7\n")
        proc = self._run(
            'if url_contains http://x hello; then echo MATCH; else echo "RC:$?"; fi', env
        )
        assert proc.stdout.strip() == "RC:1", proc.stdout + proc.stderr

    def test_an_absent_pattern_fails(self, tmp_path):
        env = self._stub(tmp_path, "curl", 'printf "hello\\n"\n')
        proc = self._run(
            'if url_contains http://x zzz; then echo MATCH; else echo "RC:$?"; fi', env
        )
        assert proc.stdout.strip() == "RC:1", proc.stdout + proc.stderr

    def test_ssh_contains_matches_command_output(self, tmp_path):
        env = self._stub(tmp_path, "ssh", 'printf "active\\n"\n')
        proc = self._run(
            'if ssh_contains u@h "systemctl is-active x" "^active"; '
            'then echo MATCH; else echo "RC:$?"; fi', env
        )
        assert proc.stdout.strip() == "MATCH", proc.stdout + proc.stderr

    def test_ssh_contains_reports_a_failed_command(self, tmp_path):
        env = self._stub(tmp_path, "ssh", "exit 255\n")
        proc = self._run(
            'if ssh_contains u@h "true" "^active"; then echo MATCH; else echo "RC:$?"; fi',
            env,
        )
        assert proc.stdout.strip() == "RC:1", proc.stdout + proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
