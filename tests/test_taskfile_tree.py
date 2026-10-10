#!/usr/bin/env python3
"""Unit tests for scripts/taskfile_tree.py, the Taskfile `includes:` flattener.

Each test writes a throwaway Taskfile tree and asserts the map a consumer's
gates read from it, including the `- task:` reference rewriting.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from script_loader import load_script  # noqa: E402

taskfile_tree = load_script("taskfile_tree.py")


def _tree(root: Path, root_body: str, included: dict[str, str] | None = None) -> Path:
    (root / "taskfiles").mkdir(parents=True, exist_ok=True)
    (root / "Taskfile.yml").write_text(root_body, encoding="utf-8")
    for name, body in (included or {}).items():
        (root / "taskfiles" / name).write_text(body, encoding="utf-8")
    return root


def test_include_paths_reads_both_entry_shapes(tmp_path):
    """go-task accepts a bare path and a `taskfile:` mapping; a reader that
    knows one walks past every namespace written the other way."""
    _tree(
        tmp_path,
        "includes:\n"
        "  flux: taskfiles/flux.yml\n"
        "  k3s:\n"
        "    taskfile: taskfiles/k3s.yml\n",
        {"flux.yml": "tasks: {}\n", "k3s.yml": "tasks: {}\n"},
    )
    paths = taskfile_tree.include_paths(tmp_path)
    assert paths == {
        "flux": tmp_path / "taskfiles/flux.yml",
        "k3s": tmp_path / "taskfiles/k3s.yml",
    }


def test_taskfile_paths_starts_at_the_root_file(tmp_path):
    _tree(
        tmp_path,
        "includes:\n  flux: taskfiles/flux.yml\n",
        {"flux.yml": "tasks: {}\n"},
    )
    assert taskfile_tree.taskfile_paths(tmp_path) == [
        tmp_path / "Taskfile.yml",
        tmp_path / "taskfiles/flux.yml",
    ]


def test_included_tasks_are_namespaced(tmp_path):
    _tree(
        tmp_path,
        "includes:\n  flux: taskfiles/flux.yml\ntasks:\n  lint:\n    cmds: [echo root]\n",
        {"flux.yml": "tasks:\n  lint:\n    cmds: [echo flux]\n"},
    )
    tasks = taskfile_tree.load_tasks(tmp_path)
    assert set(tasks) == {"lint", "flux:lint"}


def test_a_bare_ref_resolves_inside_its_namespace(tmp_path):
    _tree(
        tmp_path,
        "includes:\n  flux: taskfiles/flux.yml\n",
        {
            "flux.yml": "tasks:\n"
            "  lint:\n"
            "    cmds:\n"
            "      - task: render\n"
            "    deps: [sync-versions]\n"
            "  render:\n"
            "    cmds: [echo]\n"
            "  sync-versions:\n"
            "    cmds: [echo]\n"
        },
    )
    task = taskfile_tree.load_tasks(tmp_path)["flux:lint"]
    assert task["cmds"][0]["task"] == "flux:render"
    assert task["deps"] == ["flux:sync-versions"]


def test_a_leading_colon_ref_is_root_relative(tmp_path):
    """`- task: :hosts:sync` crosses namespaces; qualifying it inside the
    caller's namespace would name a task that does not exist."""
    _tree(
        tmp_path,
        "includes:\n  flux: taskfiles/flux.yml\n",
        {
            "flux.yml": "tasks:\n"
            "  lint:\n"
            "    cmds:\n"
            "      - task: ':hosts:sync'\n"
            "    deps:\n"
            "      - task: ':lint'\n"
        },
    )
    task = taskfile_tree.load_tasks(tmp_path)["flux:lint"]
    assert task["cmds"][0]["task"] == "hosts:sync"
    assert task["deps"][0]["task"] == "lint"


def test_rewriting_does_not_mutate_the_loaded_document(tmp_path):
    """Two namespaces may include the same fragment; an in-place rewrite would
    qualify the second read with the first namespace."""
    _tree(
        tmp_path,
        "includes:\n"
        "  a: taskfiles/shared.yml\n"
        "  b: taskfiles/shared.yml\n",
        {"shared.yml": "tasks:\n  run:\n    cmds:\n      - task: helper\n"},
    )
    tasks = taskfile_tree.load_tasks(tmp_path)
    assert tasks["a:run"]["cmds"][0]["task"] == "a:helper"
    assert tasks["b:run"]["cmds"][0]["task"] == "b:helper"


def test_task_names_leaves_out_internal_templates(tmp_path):
    """An internal task is a template `task --list` never offers, so a gate
    comparing it against operator-facing names would report a phantom."""
    _tree(
        tmp_path,
        "tasks:\n"
        "  lint:\n    cmds: [echo]\n"
        "  _app-status:\n    internal: true\n    cmds: [echo]\n",
    )
    assert taskfile_tree.task_names(tmp_path) == {"lint"}


def test_an_empty_task_body_is_a_mapping(tmp_path):
    """`tasks: {name:}` parses to None, and `.get("internal")` on it raises."""
    _tree(tmp_path, "tasks:\n  placeholder:\n")
    assert taskfile_tree.load_tasks(tmp_path) == {"placeholder": {}}
    assert taskfile_tree.task_names(tmp_path) == {"placeholder"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
