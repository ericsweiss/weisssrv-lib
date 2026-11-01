#!/usr/bin/env python3
"""The shellcheck template's defaults lint a layout a consumer has, its
`changes:` default fires on every file those defaults lint, and an empty
`find_dir` fails a repo that has ansible/roles.
"""
from __future__ import annotations

import os
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

import pytest
from script_loader import REPO, SCRIPTS, load_path

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

TEMPLATE = REPO / "ci" / "lint" / "shellcheck.yml"
_INPUT_RE = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9_]+)\s*\]\]")


def _defaults() -> dict:
    inputs = ci_yaml.spec_inputs(TEMPLATE.read_text(encoding="utf-8"))
    return {name: (meta or {}).get("default") for name, meta in inputs.items()}


def _fires(path: str, entry: str) -> bool:
    """GitLab's `**/` also matches zero directories."""
    return fnmatch(path, entry) or fnmatch(path, entry.replace("**/", ""))


def uncovered(direct_globs: str, changes: list) -> list:
    """Direct globs no `changes:` entry fires on, probed with a sample path."""
    return [
        glob for glob in direct_globs.split()
        if not any(_fires(glob.replace("*", "sample"), entry) for entry in changes)
    ]


def test_the_defaults_name_no_ansible_roles_tree() -> None:
    defaults = _defaults()
    assert defaults["find_dir"] == ""
    assert not any("ansible" in g for g in defaults["direct_globs"].split())


def test_the_changes_default_covers_the_direct_globs() -> None:
    defaults = _defaults()
    assert uncovered(defaults["direct_globs"], defaults["changes"]) == []


def test_an_uncovered_glob_is_reported() -> None:
    assert uncovered("scripts/*.sh tools/*.sh", ["scripts/**/*"]) == ["tools/*.sh"]


def _job_script(**overrides: str) -> str:
    """The job's script blocks joined as the runner runs them, inputs at their defaults."""
    values = {**_defaults(), **overrides}
    body = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)[1]
    rendered = _INPUT_RE.sub(lambda m: str(values[m.group(1)]), body)
    job = next(iter(ci_yaml.parse_ci(rendered).values()))
    return "set -e\n" + "\n".join(job["script"])


def _run(tmp_path: Path, layout: list[str], **overrides: str) -> subprocess.CompletedProcess:
    """The job in a checkout holding `layout`, with a shellcheck stub that passes."""
    stub = tmp_path / "bin" / "shellcheck"
    stub.parent.mkdir()
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    checkout = tmp_path / "checkout"
    for rel in ["scripts/run.sh", *layout]:
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        (checkout / rel).write_text("#!/bin/sh\n")
    env = {**os.environ, "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["sh", "-c", _job_script(**overrides)], cwd=checkout, env=env,
                          capture_output=True, text=True, check=False)


def test_an_empty_find_dir_fails_a_repo_that_has_ansible_roles(tmp_path: Path) -> None:
    result = _run(tmp_path, ["ansible/roles/demo/files/hook.sh"])
    assert result.returncode == 1, result.stdout
    assert "pass find_dir: ansible/roles so its role shell is linted" in result.stdout


@pytest.mark.parametrize("layout", [[], ["ansible_collections/ns/col/roles/demo/files/hook.sh"]],
                         ids=["no-ansible-tree", "installed-collections-only"])
def test_an_empty_find_dir_skips_a_repo_without_ansible_roles(tmp_path: Path, layout) -> None:
    result = _run(tmp_path, layout)
    assert result.returncode == 0, result.stdout
    assert "no find_dir set" in result.stdout


def test_a_set_find_dir_lints_the_role_shell(tmp_path: Path) -> None:
    result = _run(tmp_path, ["ansible_collections/demo/files/hook.sh"],
                  find_dir="ansible_collections")
    assert result.returncode == 0, result.stdout
    assert "ShellCheck passed (2 file(s) checked)" in result.stdout
