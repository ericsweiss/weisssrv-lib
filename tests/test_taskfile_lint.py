#!/usr/bin/env python3
"""Contract tests for the taskfiles/lint.yml go-task fragment.

The fragment is a consumer's local mirror of the lint CI templates, so the
exit-code contract matters: `doc-links` must skip only when the checker is
absent, and fail when the checker fails.

The tests execute each task's command body with `sh` (go-task's default
interpreter is POSIX-sh compatible) against throwaway scripts.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

TASKFILE = Path(__file__).resolve().parent.parent / "taskfiles" / "lint.yml"


def _cmd(task: str, **subs: str) -> str:
    """Return `task`'s single command body with `{{.VAR}}` placeholders filled."""
    cmds = yaml.safe_load(TASKFILE.read_text())["tasks"][task]["cmds"]
    assert len(cmds) == 1, f"{task} is expected to hold exactly one command"
    body = cmds[0]
    for name, value in subs.items():
        body = body.replace("{{." + name + "}}", value)
    return body


def _run(body: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", "-c", body], cwd=cwd, capture_output=True, text=True)


def _checker(tmp_path: Path, exit_code: int) -> Path:
    script = tmp_path / "check-doc-links.py"
    script.write_text(f"import sys\nprint('checker ran')\nsys.exit({exit_code})\n")
    return script


def test_doc_links_fails_when_the_checker_fails(tmp_path: Path) -> None:
    _checker(tmp_path, 1)
    result = _run(_cmd("doc-links", DOC_LINK_SCRIPT="check-doc-links.py"), tmp_path)
    assert result.returncode != 0
    assert "checker ran" in result.stdout
    assert "skipping" not in result.stdout


def test_doc_links_passes_when_the_checker_passes(tmp_path: Path) -> None:
    _checker(tmp_path, 0)
    result = _run(_cmd("doc-links", DOC_LINK_SCRIPT="check-doc-links.py"), tmp_path)
    assert result.returncode == 0
    assert "checker ran" in result.stdout


def test_doc_links_skips_when_the_checker_is_absent(tmp_path: Path) -> None:
    result = _run(_cmd("doc-links", DOC_LINK_SCRIPT="check-doc-links.py"), tmp_path)
    assert result.returncode == 0
    assert "skipping" in result.stdout


def test_every_task_is_declared_with_a_description() -> None:
    tasks = yaml.safe_load(TASKFILE.read_text())["tasks"]
    assert set(tasks) == {"default", "yamllint", "shellcheck", "doc-links"}
    for name, spec in tasks.items():
        assert spec.get("desc"), f"{name} has no desc"
