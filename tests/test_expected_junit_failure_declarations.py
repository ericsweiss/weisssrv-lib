"""Every `expected-junit-failures.txt` line still names a real task, in every
scenario, so a stale declaration cannot keep the pipeline green.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles"

# The consuming script owns the line grammar, including the ` ::<n>` count form.
_SANITIZE = load_script("sanitize-junit-expected-failures.py")


def _declarations() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for path in sorted(ROLES.glob("*/molecule/*/expected-junit-failures.txt")):
        for pattern, _count in _SANITIZE.load_expectations(path):
            out.append((path, pattern))
    return out


class _AnsibleLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Ansible's `!vault` and friends."""


_AnsibleLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _task_names(declaration: Path) -> list[str]:
    """Every `name:` in the role's tasks/handlers and the declaring scenario.

    Names, not raw text, and a task file reached through `include_role` or
    `import_role` counts as the role's own.
    """
    scenario = declaration.parent
    role = scenario.parent.parent
    names: list[str] = []
    included_roles: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("name"), str):
                names.append(node["name"])
            for key in ("include_role", "import_role",
                        "ansible.builtin.include_role", "ansible.builtin.import_role"):
                spec = node.get(key)
                if isinstance(spec, dict) and isinstance(spec.get("name"), str):
                    included_roles.add(spec["name"].rsplit(".", 1)[-1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    def scan(directory: Path) -> None:
        if not directory.is_dir():
            return
        for yml in sorted(directory.rglob("*.yml")) + sorted(directory.rglob("*.yaml")):
            try:
                documents = yaml.load_all(yml.read_text(errors="replace"),
                                          Loader=_AnsibleLoader)
                for document in documents:
                    walk(document)
            except yaml.YAMLError:
                continue

    for directory in (role / "tasks", role / "handlers", scenario):
        scan(directory)
    for other in sorted(included_roles):
        if other != role.name:
            scan(role.parent / other / "tasks")
    return names


def test_the_repo_ships_declarations_to_check():
    assert _declarations(), "no expected-junit-failures.txt found — the gate is vacuous"


def stale_for(declaration: Path, line: str) -> bool:
    return not any(line in name for name in _task_names(declaration))


def test_every_declared_expectation_still_names_a_task():
    stale = [
        f"{path.relative_to(REPO)}: {line!r}"
        for path, line in _declarations()
        if stale_for(path, line)
    ]
    assert not stale, (
        "declared negative-path expectation no longer matches any task name:\n  "
        + "\n  ".join(stale)
    )


def test_a_stale_declaration_is_reported(tmp_path):
    """Mutation: the comparison must report a name no task carries any more."""
    role = tmp_path / "fakerole"
    (role / "tasks").mkdir(parents=True)
    (role / "tasks" / "main.yml").write_text(
        "- name: A task that exists\n  ansible.builtin.debug: {}\n"
    )
    scenario = role / "molecule" / "default"
    scenario.mkdir(parents=True)
    declaration = scenario / "expected-junit-failures.txt"
    declaration.write_text("A task that was renamed away\n")
    assert stale_for(declaration, "A task that was renamed away")
    assert not stale_for(declaration, "A task that exists")


def test_a_declaration_matching_only_a_comment_is_stale(tmp_path):
    """A renamed-away task whose old name survives in a comment is still stale."""
    role = tmp_path / "fakerole"
    (role / "tasks").mkdir(parents=True)
    (role / "tasks" / "main.yml").write_text(
        "# A task that was renamed away\n"
        "- name: A task that exists\n  ansible.builtin.debug: {}\n"
    )
    scenario = role / "molecule" / "default"
    scenario.mkdir(parents=True)
    declaration = scenario / "expected-junit-failures.txt"
    declaration.write_text("A task that was renamed away\n")
    assert stale_for(declaration, "A task that was renamed away")


def test_a_counted_declaration_parses_to_its_pattern(tmp_path):
    """The ` ::<n>` count form resolves to the pattern half, not the raw line."""
    role = tmp_path / "fakerole"
    (role / "tasks").mkdir(parents=True)
    (role / "tasks" / "main.yml").write_text(
        "- name: A task that exists\n  ansible.builtin.debug: {}\n"
    )
    scenario = role / "molecule" / "default"
    scenario.mkdir(parents=True)
    declaration = scenario / "expected-junit-failures.txt"
    declaration.write_text("A task that exists ::2\n")
    assert _SANITIZE.load_expectations(declaration) == [("A task that exists", 2)]
    assert not stale_for(declaration, "A task that exists")
    assert stale_for(declaration, "A task that exists ::2")


# The script reads a trailing ` ::<n>` as the count; any other tail stays part of
# the pattern, so a typo there matches nothing.
_COUNT_SUFFIX = re.compile(r"\s::\s*\S+$")


def _declared_lines(declaration: Path) -> list[str]:
    """The lines the script treats as declarations, stripped of comments."""
    return [
        line.strip()
        for line in declaration.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _patterns(declaration: Path) -> list[str]:
    return [pattern for pattern, _count in _SANITIZE.load_expectations(declaration)]


def duplicated_in(declaration: Path) -> list[str]:
    """Patterns declared more than once in one file.

    The script credits a hit to every entry that matches, so a repeated pattern
    counts each testcase twice and no declared count can be right.
    """
    patterns = _patterns(declaration)
    return sorted({pattern for pattern in patterns if patterns.count(pattern) > 1})


def nested_in(declaration: Path) -> list[tuple[str, str]]:
    """Pairs where one pattern is a substring of another in the same file.

    Every testcase the longer pattern matches is credited to the shorter one as
    well, so the two counts cannot both describe their own guard.
    """
    patterns = _patterns(declaration)
    return sorted(
        (short, long)
        for short in patterns
        for long in patterns
        if short != long and short in long
    )


def malformed_counts_in(declaration: Path) -> list[str]:
    """Lines ending in a count-shaped suffix the script will not read as one."""
    bad: list[str] = []
    for line in _declared_lines(declaration):
        if not _COUNT_SUFFIX.search(line):
            continue
        pattern, separator, count = line.rpartition(" ::")
        if not (separator and pattern.strip() and count.strip().isdigit() and int(count) > 0):
            bad.append(line)
    return bad


def test_no_pattern_is_declared_twice_in_one_file():
    offenders = [
        f"{path.relative_to(REPO)}: {pattern!r}"
        for path in sorted(ROLES.glob("*/molecule/*/expected-junit-failures.txt"))
        for pattern in duplicated_in(path)
    ]
    assert not offenders, (
        "a repeated declaration counts every matching testcase twice:\n  "
        + "\n  ".join(offenders)
    )


def test_no_pattern_absorbs_another_in_the_same_file():
    offenders = [
        f"{path.relative_to(REPO)}: {short!r} is inside {long!r}"
        for path in sorted(ROLES.glob("*/molecule/*/expected-junit-failures.txt"))
        for short, long in nested_in(path)
    ]
    assert not offenders, (
        "the shorter pattern also matches the longer one's testcases, so neither "
        "count describes one guard:\n  " + "\n  ".join(offenders)
    )


def test_every_count_suffix_parses_as_a_count():
    offenders = [
        f"{path.relative_to(REPO)}: {line!r}"
        for path in sorted(ROLES.glob("*/molecule/*/expected-junit-failures.txt"))
        for line in malformed_counts_in(path)
    ]
    assert not offenders, (
        "a ` ::<n>` suffix needs a positive integer; otherwise the suffix stays "
        "part of the pattern and matches nothing:\n  " + "\n  ".join(offenders)
    )


def _declaration(tmp_path: Path, text: str) -> Path:
    scenario = tmp_path / "fakerole" / "molecule" / "default"
    scenario.mkdir(parents=True, exist_ok=True)
    declaration = scenario / "expected-junit-failures.txt"
    declaration.write_text(text)
    return declaration


def test_a_repeated_declaration_is_reported(tmp_path):
    """Mutation: the same pattern twice is double-counted, so it must be caught."""
    assert duplicated_in(_declaration(tmp_path, "Guard A\nGuard A ::2\n")) == ["Guard A"]
    assert duplicated_in(_declaration(tmp_path, "Guard A\nGuard B\n")) == []


def test_a_pattern_that_absorbs_another_is_reported(tmp_path):
    """Mutation: a prefix pattern swallows the longer pattern's testcases."""
    assert nested_in(_declaration(tmp_path, "Guard\nGuard on the second NIC\n")) == [
        ("Guard", "Guard on the second NIC")
    ]
    assert nested_in(_declaration(tmp_path, "Guard one\nGuard two\n")) == []


def test_a_count_suffix_that_cannot_parse_is_reported(tmp_path):
    """Mutation: ::0 and ::two silently become part of the pattern."""
    assert malformed_counts_in(_declaration(tmp_path, "Guard A ::0\n")) == ["Guard A ::0"]
    assert malformed_counts_in(_declaration(tmp_path, "Guard A ::two\n")) == ["Guard A ::two"]
    assert malformed_counts_in(_declaration(tmp_path, "Guard A ::2\n")) == []
    assert malformed_counts_in(_declaration(tmp_path, "Guard A :: 2\n")) == []
    assert malformed_counts_in(_declaration(tmp_path, "Guard A\n")) == []
