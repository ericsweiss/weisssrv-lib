"""Every `expected-junit-failures.txt` line still names a real task, in every
scenario, so a stale declaration cannot keep the pipeline green.
"""
from __future__ import annotations

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
