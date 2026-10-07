#!/usr/bin/env python3
"""Unit tests for scripts/supervised-apply-guard.sh.

The guard is driven by subprocess with a pty for the terminal arm, so the
non-tty refusal and the auto-approve refusal are both exercised for real.
"""
from __future__ import annotations

import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "scripts" / "supervised-apply-guard.sh"


def _run(args: list[str], stdin_text: str = "") -> subprocess.CompletedProcess:
    """Run the guard with a pipe on stdin, so `-t 0` is false."""
    return subprocess.run(
        ["bash", str(GUARD), *args],
        input=stdin_text, capture_output=True, text=True,
    )


def _run_on_tty(args: list[str], typed: str, env: dict | None = None) -> tuple[int, str]:
    """Run the guard with a real pty on stdin and feed it `typed`."""
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(GUARD), *args],
        stdin=child, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env={**os.environ, **(env or {})},
    )
    os.close(child)
    os.write(parent, typed.encode())
    out, _ = proc.communicate(timeout=30)
    os.close(parent)
    return proc.returncode, out


def test_too_few_arguments_exits_2():
    res = _run(["terraform:apply"])
    assert res.returncode == 2
    assert "usage:" in res.stderr


def test_non_tty_is_refused():
    """CI must never reach the prompt: a piped stdin would answer it."""
    res = _run(["terraform:apply", "the UniFi network"])
    assert res.returncode == 2
    assert "run it from a terminal" in res.stderr


def test_auto_approve_is_refused_on_a_tty():
    code, out = _run_on_tty(
        ["terraform:apply", "the UniFi network", "-auto-approve"], "apply\n"
    )
    assert code == 2
    assert "-auto-approve is refused" in out


def test_equals_spelling_of_auto_approve_is_refused():
    code, out = _run_on_tty(
        ["terraform:apply", "the network", "-auto-approve=true"], "apply\n"
    )
    assert code == 2
    assert "-auto-approve is refused" in out


def test_double_dash_spelling_of_auto_approve_is_refused():
    code, out = _run_on_tty(
        ["terraform:apply", "the network", "--auto-approve"], "apply\n"
    )
    assert code == 2
    assert "-auto-approve is refused" in out


def test_typing_the_confirm_word_passes():
    code, out = _run_on_tty(["terraform:apply", "the network"], "apply\n")
    assert code == 0, out
    assert "this rewrites the network" in out


def test_typing_anything_else_aborts():
    code, out = _run_on_tty(["terraform:apply", "the network"], "yes\n")
    assert code == 1
    assert "Aborted." in out


def test_bare_enter_aborts():
    code, out = _run_on_tty(["terraform:apply", "the network"], "\n")
    assert code == 1
    assert "Aborted." in out


def test_custom_confirm_word_is_honoured():
    env = {"SUPERVISED_APPLY_CONFIRM_WORD": "renumber"}
    code, out = _run_on_tty(["unifi:apply", "VLAN 10"], "apply\n", env)
    assert code == 1, out
    code, out = _run_on_tty(["unifi:apply", "VLAN 10"], "renumber\n", env)
    assert code == 0, out


def test_empty_confirm_word_exits_2():
    """An empty word would make a bare Enter an approval."""
    code, out = _run_on_tty(
        ["unifi:apply", "VLAN 10"], "\n", {"SUPERVISED_APPLY_CONFIRM_WORD": ""}
    )
    assert code == 2
    assert "would approve" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
