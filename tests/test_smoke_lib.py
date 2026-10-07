#!/usr/bin/env python3
"""scripts/smoke-lib.sh keeps its probe classifications and its PASS/FAIL ledger.

`curl`, `ssh` and `nc` are PATH stubs here, so every arm runs without a guest.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "smoke-lib.sh"
# Absolute: the stub dir is prepended to PATH, and an `/usr/bin/env` shebang in
# a stub would still have to resolve the interpreter.
BASH = shutil.which("bash") or "/bin/bash"

# Replays a scripted HTTP exchange: CURL_STATUS_LINE answers -I, CURL_BODY
# answers a plain fetch, and CURL_RC drives the connection-failure arm.
STUB_CURL = """\
#!%s
for arg in "$@"; do
    [ "$arg" = "-sI" ] && head_request=1
done
if [ "${CURL_RC:-0}" -ne 0 ]; then
    exit "${CURL_RC}"
fi
if [ -n "${head_request:-}" ]; then
    printf '%%s\\r\\n' "${CURL_STATUS_LINE:-HTTP/1.1 200 OK}"
else
    printf '%%s' "${CURL_BODY:-}"
fi
""" % BASH

STUB_SSH = """\
#!%s
printf 'ssh %%s\\n' "$*" >> "$TRACE"
printf '%%s' "${SSH_OUT:-}"
exit "${SSH_RC:-0}"
""" % BASH

STUB_NC = """\
#!%s
printf 'nc %%s\\n' "$*" >> "$TRACE"
exit "${NC_RC:-0}"
""" % BASH


@pytest.fixture()
def shell(tmp_path):
    """Run a snippet with smoke-lib.sh sourced and curl/ssh/nc stubbed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    trace.write_text("")
    for name, body in (("curl", STUB_CURL), ("ssh", STUB_SSH), ("nc", STUB_NC)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    def _run(snippet, **env):
        proc = subprocess.run(
            [BASH, "-c", '. "$1"\n%s' % snippet, "bash", str(LIB)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                "PATH": "%s:%s" % (bin_dir, os.environ.get("PATH", "")),
                "TRACE": str(trace),
                **{k: str(v) for k, v in env.items()},
            },
        )
        return proc, trace.read_text().splitlines()

    return _run


def test_sourcing_only_arms_the_counters(shell):
    """A consumer sources it under `set -e` before anything else, so the only
    top-level effect may be the two counters it owns."""
    proc, trace = shell('set -e\necho "$SMOKE_PASS/$SMOKE_FAIL"')
    assert proc.returncode == 0
    assert proc.stdout.strip() == "0/0"
    assert trace == []


def test_http_status_prints_the_code(shell):
    proc, _ = shell("http_status http://h/", CURL_STATUS_LINE="HTTP/2 204 No Content")
    assert proc.stdout.strip() == "204"


def test_http_status_reports_000_and_still_succeeds(shell):
    """CRITICAL: an unreachable endpoint must not abort an errexit caller before
    the probe that wraps it can classify the failure."""
    proc, _ = shell("set -e\nhttp_status http://h/\necho after", CURL_RC=7)
    assert proc.returncode == 0
    assert proc.stdout.split() == ["000", "after"]


@pytest.mark.parametrize(
    "status,ok,registry,below_500",
    [
        ("HTTP/1.1 200 OK", 0, 0, 0),
        ("HTTP/1.1 302 Found", 0, 0, 0),
        ("HTTP/1.1 401 Unauthorized", 1, 0, 0),
        ("HTTP/1.1 404 Not Found", 1, 1, 0),
        ("HTTP/1.1 503 Unavailable", 1, 1, 1),
    ],
)
def test_the_three_http_classifiers_disagree_where_they_should(
    shell, status, ok, registry, below_500
):
    snippet = (
        "check_http_ok http://h/; echo ok=$?\n"
        "check_registry_ok http://h/; echo reg=$?\n"
        "check_http_below_500 http://h/; echo lt500=$?"
    )
    proc, _ = shell(snippet, CURL_STATUS_LINE=status)
    assert proc.stdout.split() == ["ok=%d" % ok, "reg=%d" % registry, "lt500=%d" % below_500]


def test_nothing_answered_fails_every_classifier(shell):
    snippet = (
        "check_http_ok http://h/; echo ok=$?\n"
        "check_registry_ok http://h/; echo reg=$?\n"
        "check_http_below_500 http://h/; echo lt500=$?"
    )
    proc, _ = shell(snippet, CURL_RC=7)
    assert proc.stdout.split() == ["ok=1", "reg=1", "lt500=1"]


def test_check_tcp_port_bounds_its_probe(shell):
    proc, trace = shell("check_tcp_port host 443; echo rc=$?")
    assert proc.stdout.strip() == "rc=0"
    assert trace == ["nc -z -w 5 host 443"]


def test_a_needle_is_literal_not_a_pattern(shell):
    """A `.` in a needle must not match the character that happens to be there:
    a regex needle turns a wrong body into a PASS."""
    proc, _ = shell(
        'smoke_url_contains http://h/ "v1.2"; echo literal=$?\n'
        'smoke_url_contains http://h/ "v1.2"; echo again=$?',
        CURL_BODY="version v1x2",
    )
    assert proc.stdout.split() == ["literal=1", "again=1"]


def test_a_matching_needle_passes(shell):
    proc, _ = shell('smoke_url_contains http://h/ "v1.2"', CURL_BODY="version v1.2 ok")
    assert proc.returncode == 0


def test_a_failed_fetch_is_not_a_clean_non_match(shell):
    """An empty body from a dead endpoint and an empty body from a live one are
    the same string; only the fetch's status separates them."""
    proc, _ = shell('smoke_url_contains http://h/ ""; echo rc=$?', CURL_RC=7)
    assert proc.stdout.strip() == "rc=1"


def test_ssh_contains_is_literal_and_ssh_matches_is_an_ere(shell):
    snippet = (
        'smoke_ssh_contains h "cmd" "a.c"; echo literal=$?\n'
        'smoke_ssh_matches h "cmd" "^a.c$"; echo ere=$?'
    )
    proc, trace = shell(snippet, SSH_OUT="abc")
    assert proc.stdout.split() == ["literal=1", "ere=0"]
    assert trace == ['ssh h cmd', 'ssh h cmd']


def test_an_unreachable_host_fails_both_ssh_helpers(shell):
    snippet = (
        'smoke_ssh_contains h "cmd" ""; echo literal=$?\n'
        'smoke_ssh_matches h "cmd" ""; echo ere=$?'
    )
    proc, _ = shell(snippet, SSH_RC=255)
    assert proc.stdout.split() == ["literal=1", "ere=1"]


def test_smoke_check_records_both_outcomes(shell):
    snippet = (
        "smoke_check pass true\n"
        "smoke_check fail false\n"
        "echo ledger=$SMOKE_PASS/$SMOKE_FAIL"
    )
    proc, _ = shell(snippet)
    assert proc.stdout.split() == ["pass...", "PASS", "fail...", "FAIL", "ledger=1/1"]


def test_smoke_optional_counts_a_miss_as_neither(shell):
    snippet = "smoke_optional opt 'not adopted yet' false\necho ledger=$SMOKE_PASS/$SMOKE_FAIL"
    proc, _ = shell(snippet)
    assert proc.stdout.split() == ["opt...", "SKIP", "(not", "adopted", "yet)", "ledger=0/0"]


def test_smoke_summary_is_the_exit_status(shell):
    clean, _ = shell("smoke_check ok true\nsmoke_summary")
    assert clean.returncode == 0
    dirty, _ = shell("smoke_check bad false\nsmoke_summary")
    assert dirty.returncode == 1
    assert "1 passed, 0 failed" in clean.stdout
    assert "0 passed, 1 failed" in dirty.stdout
