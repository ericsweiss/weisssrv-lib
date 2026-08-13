#!/usr/bin/env python3
"""Unit tests for scripts/check-taskfile.sh.

check-taskfile.sh is the taskfile-smoke CI gate: it asserts every
scripts/<name>.{sh,py} referenced by a Taskfile exists on disk, plus each
`dotenv:` target ($CHECK_TASKFILE_DOTENV, default scripts/hosts.env).

The script derives REPO_ROOT from its own location, so each test builds a
throwaway repo (a copy of the script under <root>/scripts/ + a fixture
Taskfile) to control which scripts / dotenv files are present.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-taskfile.sh"


def _make_repo(tmp_path: Path, taskfile_text: str,
               scripts: tuple[str, ...] = (), hosts_env: bool = False) -> Path:
    """Build a throwaway repo: <root>/scripts/check-taskfile.sh (copied) plus
    the named fixture scripts and (optionally) hosts.env, and <root>/Taskfile.yml
    with the given text. Returns the repo root."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "check-taskfile.sh")
    for name in scripts:
        (root / "scripts" / name).write_text("# fixture\n")
    if hosts_env:
        (root / "scripts" / "hosts.env").write_text("FOO=bar\n")
    (root / "Taskfile.yml").write_text(taskfile_text)
    return root


def _run(root: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(root / "scripts" / "check-taskfile.sh"),
         str(root / "Taskfile.yml")],
        capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )


class TestScriptReferences:
    def test_existing_reference_passes(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n      - bash scripts/foo.sh\n",
            scripts=("foo.sh",),
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr
        assert "OK:" in res.stdout

    def test_missing_reference_fails(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n      - python3 scripts/gone.py\n",
        )
        res = _run(root)
        assert res.returncode == 1
        assert "scripts/gone.py" in res.stderr

    def test_one_missing_among_many_fails(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n"
            "      - bash scripts/foo.sh\n"
            "      - bash scripts/missing.sh\n",
            scripts=("foo.sh",),
        )
        res = _run(root)
        assert res.returncode == 1
        assert "scripts/missing.sh" in res.stderr

    def test_missing_taskfile_fails(self, tmp_path: Path):
        # The `[ -f "$TASKFILE" ]` guard: a Taskfile path that does not exist
        # must exit 1 with a clear message, not silently pass on an empty grep.
        root = tmp_path / "repo"
        (root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, root / "scripts" / "check-taskfile.sh")
        # No Taskfile.yml written — _run points at the non-existent path.
        res = _run(root)
        assert res.returncode == 1
        assert "not found" in res.stderr.lower()


class TestHostsEnvDotenv:
    def test_dotenv_present_passes(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "dotenv: ['scripts/hosts.env']\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=True,
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr

    def test_dotenv_referenced_but_missing_fails(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "dotenv: ['scripts/hosts.env']\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=False,
        )
        res = _run(root)
        assert res.returncode == 1
        assert "hosts.env" in res.stderr

    def test_multiline_list_form_is_matched(self, tmp_path: Path):
        # go-task also accepts the YAML multi-line list form; the bare-path
        # matcher (not a same-line `dotenv:` match) must catch it too.
        root = _make_repo(
            tmp_path,
            "dotenv:\n  - scripts/hosts.env\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=False,
        )
        res = _run(root)
        assert res.returncode == 1
        assert "hosts.env" in res.stderr

    def test_dotenv_target_is_env_overridable(self, tmp_path: Path):
        # $CHECK_TASKFILE_DOTENV replaces the default target list, so a
        # consumer with a differently-named dotenv file is still gated.
        root = _make_repo(
            tmp_path,
            "dotenv: ['config/site.env']\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
        )
        res = _run(root, env={"CHECK_TASKFILE_DOTENV": "config/site.env"})
        assert res.returncode == 1
        assert "config/site.env" in res.stderr

    def test_default_target_not_required_when_overridden(self, tmp_path: Path):
        root = _make_repo(
            tmp_path,
            "dotenv: ['scripts/hosts.env']\ntasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=False,
        )
        res = _run(root, env={"CHECK_TASKFILE_DOTENV": "config/site.env"})
        assert res.returncode == 0, res.stderr

    def test_absent_but_unreferenced_passes(self, tmp_path: Path):
        # hosts.env is only required when the Taskfile references it as a dotenv
        # target. A Taskfile that never mentions it must PASS with hosts.env
        # absent — the check must not demand a file nothing depends on. (Guards
        # the `grep -q ... &&` short-circuit: no reference => no requirement.)
        root = _make_repo(
            tmp_path,
            "tasks:\n  a:\n    cmds:\n      - echo hi\n",
            hosts_env=False,
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr
        assert "OK:" in res.stdout


class TestIncludes:
    """Included fragments carry their own scripts/ references."""

    def _repo(self, tmp_path: Path, includes: str, fragment: str, scripts=()) -> Path:
        root = _make_repo(tmp_path, "version: '3'\nincludes:\n" + includes, scripts=scripts)
        (root / "taskfiles").mkdir()
        (root / "taskfiles" / "lint.yml").write_text(fragment)
        return root

    def test_a_missing_script_inside_an_include_fails(self, tmp_path: Path):
        root = self._repo(
            tmp_path,
            "  lint:\n    taskfile: taskfiles/lint.yml\n",
            "tasks:\n  a:\n    cmds:\n      - python3 scripts/gone.py\n",
        )
        res = _run(root)
        assert res.returncode == 1
        assert "lint.yml references missing scripts/gone.py" in res.stderr

    def test_the_shorthand_include_form_is_followed(self, tmp_path: Path):
        root = self._repo(
            tmp_path,
            "  lint: taskfiles/lint.yml\n",
            "tasks:\n  a:\n    cmds:\n      - python3 scripts/gone.py\n",
        )
        assert _run(root).returncode == 1

    def test_a_present_script_inside_an_include_passes(self, tmp_path: Path):
        root = self._repo(
            tmp_path,
            "  lint:\n    taskfile: taskfiles/lint.yml\n",
            "tasks:\n  a:\n    cmds:\n      - bash scripts/foo.sh\n",
            scripts=("foo.sh",),
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr

    def test_a_missing_include_target_fails(self, tmp_path: Path):
        root = _make_repo(tmp_path, "version: '3'\nincludes:\n  lint: taskfiles/absent.yml\n")
        res = _run(root)
        assert res.returncode == 1
        assert "Taskfile not found" in res.stderr

    def test_an_include_cycle_terminates(self, tmp_path: Path):
        root = self._repo(
            tmp_path,
            "  lint:\n    taskfile: taskfiles/lint.yml\n",
            "version: '3'\nincludes:\n  root: ../Taskfile.yml\ntasks: {}\n",
        )
        res = subprocess.run(
            ["bash", str(root / "scripts" / "check-taskfile.sh"), str(root / "Taskfile.yml")],
            capture_output=True, text=True, timeout=30, env=dict(os.environ),
        )
        assert res.returncode == 0, res.stderr

    def test_an_includes_key_inside_a_task_is_not_followed(self, tmp_path: Path):
        """Only a column-0 `includes:` block declares fragments."""
        root = _make_repo(
            tmp_path,
            "version: '3'\ntasks:\n  a:\n    includes:\n      x: nope.yml\n    cmds:\n"
            "      - echo hi\n",
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
