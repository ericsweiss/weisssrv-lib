#!/usr/bin/env python3
"""Guard against vacuous `assert: that:` conditions across every `that:` the
collection ships: an entry that parses as a mapping instead of a string, and an
empty `that:` list."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COLLECTION = REPO / "ansible_collections" / "weisssrv" / "infra"

# Both spellings: a single `.yaml` file added to a scenario would otherwise slip
# out of the gate silently.
_YAML_SUFFIXES = ("yml", "yaml")


def _molecule_files() -> list[Path]:
    """Every YAML file under a role scenario dir or the shared molecule tasks."""
    files: list[Path] = []
    for suffix in _YAML_SUFFIXES:
        files += (COLLECTION / "roles").glob(f"*/molecule/**/*.{suffix}")
        files += (COLLECTION / "molecule-shared").rglob(f"*.{suffix}")
    return sorted(set(files))


def _task_files() -> list[Path]:
    """Every YAML file under a role's own `tasks/` tree."""
    files: list[Path] = []
    for suffix in _YAML_SUFFIXES:
        files += (COLLECTION / "roles").glob(f"*/tasks/**/*.{suffix}")
    return sorted(set(files))


def _audited_files() -> list[Path]:
    return sorted(set(_molecule_files()) | set(_task_files()))


def _scan(text: str, label: str) -> tuple[list[str], int]:
    """Offending `that:` entries in `text` as `label:line`, and how many seen.
    Walks the compose-level node tree so each offender keeps its own line and
    its YAML type."""
    offenders: list[str] = []
    seen = 0

    def visit(node) -> None:
        nonlocal seen
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.value == "that":
                    seen += 1
                    if isinstance(value, yaml.SequenceNode):
                        children = value.value
                        if not children:
                            offenders.append(
                                f"{label}:{value.start_mark.line + 1} (no conditions)"
                            )
                    else:
                        children = [value]
                    for child in children:
                        if not isinstance(child, yaml.ScalarNode):
                            offenders.append(f"{label}:{child.start_mark.line + 1}")
                        elif not child.value.strip():
                            offenders.append(
                                f"{label}:{child.start_mark.line + 1} (no conditions)"
                            )
                visit(value)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                visit(item)

    for document in yaml.compose_all(text):
        if document is not None:
            visit(document)
    return offenders, seen


def _bad_that_entries(text: str, label: str) -> list[str]:
    return _scan(text, label)[0]


BAD = """\
- name: Assert
  ansible.builtin.assert:
    that:
      - (out.content | b64decode) is search('TZ: America/Los_Angeles')
"""

EMPTY_FLOW = """\
- name: Assert
  ansible.builtin.assert:
    that: []
"""

EMPTY_SCALAR = """\
- name: Assert
  ansible.builtin.assert:
    that:
"""

GOOD = """\
- name: Assert
  ansible.builtin.assert:
    that:
      - "(out.content | b64decode) is search('TZ: America/Los_Angeles')"
      - out.stat.exists
"""


def test_detector_flags_an_unquoted_colon_bearing_entry():
    assert _bad_that_entries(BAD, "bad.yml") == ["bad.yml:4"]


def test_detector_flags_an_empty_condition_list():
    assert _bad_that_entries(EMPTY_FLOW, "bad.yml") == ["bad.yml:3 (no conditions)"]


def test_detector_flags_a_that_key_with_nothing_under_it():
    assert _bad_that_entries(EMPTY_SCALAR, "bad.yml") == ["bad.yml:3 (no conditions)"]


def test_detector_accepts_quoted_and_plain_entries():
    assert _bad_that_entries(GOOD, "good.yml") == []


def test_audited_files_are_discovered():
    """Each glob has its own floor, so one going dark cannot hide in a combined
    total and leave the gate always passing."""
    molecule = _molecule_files()
    assert len(molecule) >= 150, f"molecule glob matched only {len(molecule)} files"

    shared = COLLECTION / "molecule-shared"
    assert [p for p in molecule if shared in p.parents], (
        "the molecule-shared glob matched nothing"
    )

    tasks = _task_files()
    assert len(tasks) >= 50, f"role tasks glob matched only {len(tasks)} files"

    # The floors above only prove files are reaching the detector, not that any
    # of them carry the construct it inspects.
    with_that = sum(1 for p in _audited_files() if _scan(p.read_text(), str(p))[1])
    assert with_that > 40, f"only {with_that} audited files contain an assert `that:`"


@pytest.mark.parametrize(
    "path", _audited_files(), ids=lambda p: str(p.relative_to(COLLECTION))
)
def test_no_vacuous_assert_conditions(path: Path):
    offenders = _bad_that_entries(path.read_text(), str(path.relative_to(REPO)))
    assert not offenders, (
        "vacuous assert `that:` — an entry that parsed as a non-string is always "
        "truthy (wrap the whole entry in double quotes), and an empty condition "
        "list asserts nothing: " + ", ".join(offenders)
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
