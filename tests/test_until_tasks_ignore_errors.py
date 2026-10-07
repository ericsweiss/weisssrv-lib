#!/usr/bin/env python3
"""Ansible marks a task failed when an `until:` loop runs out of retries, even
with `failed_when: false`. A task whose negative outcome is handled below it
therefore needs `ignore_errors: true`; the rest declare EXHAUSTION_IS_FATAL."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COLLECTION = REPO / "ansible_collections" / "weisssrv" / "infra"

_YAML_SUFFIXES = ("yml", "yaml")

# Files known to carry the shape, so the walk cannot quietly stop covering them.
EXPECTED_COVERED = {
    "roles/adguard_home/tasks/api_base_config.yml",
    "roles/k3s/tasks/server.yml",
}

# Tasks where running out of retries IS the failure the play wants, keyed by
# "<path under the collection>: <task name>" with the reason. A reason is
# mandatory: an unexplained entry hides the same trap the gate exists to catch.
EXHAUSTION_IS_FATAL = {
    "roles/nextcloud/tasks/main.yml: Wait for Nextcloud to finish installing "
    "or upgrading": (
        "an install or upgrade that never completes must stop the play"
    ),
}


def _task_files() -> list[Path]:
    files: list[Path] = []
    for suffix in _YAML_SUFFIXES:
        files += (COLLECTION / "roles").glob(f"*/tasks/**/*.{suffix}")
        files += (COLLECTION / "roles").glob(f"*/handlers/**/*.{suffix}")
    return sorted(set(files))


def _is_false(value: object) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def _offenders(doc: object, label: str) -> list[str]:
    """Tasks carrying `until:` and `failed_when: false` but no `ignore_errors`."""
    found: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if "until" in node and _is_false(node.get("failed_when")):
                key = f"{label}: {node.get('name', '<unnamed>')}"
                if not node.get("ignore_errors") and key not in EXHAUSTION_IS_FATAL:
                    found.append(key)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(doc)
    return found


def _covered(doc: object) -> list[str]:
    names: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if "until" in node and _is_false(node.get("failed_when")):
                names.append(str(node.get("name", "<unnamed>")))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(doc)
    return names


def test_every_until_task_with_failed_when_false_ignores_errors() -> None:
    offenders: list[str] = []
    for path in _task_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        offenders += _offenders(doc, str(path.relative_to(COLLECTION)))
    assert offenders == [], (
        "until-exhaustion marks these tasks failed despite failed_when: false; "
        "add ignore_errors: true or drop the retries: " + ", ".join(offenders)
    )


@pytest.mark.parametrize("relative", sorted(EXPECTED_COVERED))
def test_the_known_until_tasks_are_still_in_scope(relative: str) -> None:
    """The walk must keep reaching the files whose guards depend on the rule."""
    doc = yaml.safe_load((COLLECTION / relative).read_text(encoding="utf-8"))
    assert _covered(doc), f"{relative} no longer carries an until/failed_when task"


@pytest.mark.parametrize("key", sorted(EXHAUSTION_IS_FATAL))
def test_every_fatal_exhaustion_entry_carries_a_reason(key: str) -> None:
    assert EXHAUSTION_IS_FATAL[key].strip()


def test_a_missing_ignore_errors_is_reported() -> None:
    doc = yaml.safe_load(
        "- name: Probe\n"
        "  ansible.builtin.uri:\n"
        "    url: https://example.invalid/ping\n"
        "  register: probe\n"
        "  failed_when: false\n"
        "  retries: 3\n"
        "  until: (probe.status | default(-1)) != -1\n"
    )
    assert _offenders(doc, "fixture") == ["fixture: Probe"]
    doc[0]["ignore_errors"] = True
    assert _offenders(doc, "fixture") == []


def test_an_exemption_does_not_travel_to_another_file() -> None:
    """The exemption is path-scoped: the same task name elsewhere is reported."""
    exempt = sorted(EXHAUSTION_IS_FATAL)[0]
    path, _, name = exempt.partition(": ")
    doc = yaml.safe_load(
        f"- name: {name}\n"
        "  ansible.builtin.command: /bin/true\n"
        "  register: probe\n"
        "  failed_when: false\n"
        "  retries: 3\n"
        "  until: probe.rc == 0\n"
    )
    assert _offenders(doc, path) == []
    assert _offenders(doc, "roles/other/tasks/main.yml") == [
        f"roles/other/tasks/main.yml: {name}"
    ]
