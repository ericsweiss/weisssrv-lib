#!/usr/bin/env python3
"""Unit tests for scripts/check-taskfile.sh.

The gate asserts every scripts/<name>.{sh,py} a Taskfile references exists, plus
each `dotenv:` target. Each test builds a throwaway repo around a script copy.
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
    # A CLOSED env, like the sibling shell suites: the script reads
    # $CHECK_TASKFILE_DOTENV and $CHECK_TASKFILE_MAX_DEPTH, so an ambient one
    # would quietly change what the default-target cases assert.
    return subprocess.run(
        ["bash", str(root / "scripts" / "check-taskfile.sh"),
         str(root / "Taskfile.yml")],
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], **(env or {})},
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
        # hosts.env is only required when the Taskfile references it as a
        # dotenv target, so a Taskfile that never mentions it passes with
        # hosts.env absent.
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
            capture_output=True, text=True, timeout=30,
            env={"PATH": os.environ["PATH"]},
        )
        assert res.returncode == 0, res.stderr

    def test_a_commented_includes_header_is_still_followed(self, tmp_path: Path):
        """A trailing comment on `includes:` must not disable include following."""
        root = _make_repo(tmp_path, "version: '3'\nincludes:  # shared fragments\n"
                                    "  lint: taskfiles/lint.yml\n")
        (root / "taskfiles").mkdir()
        (root / "taskfiles" / "lint.yml").write_text(
            "tasks:\n  a:\n    cmds:\n      - python3 scripts/gone.py\n"
        )
        res = _run(root)
        assert res.returncode == 1
        assert "scripts/gone.py" in res.stderr

    def test_a_leaf_at_the_depth_cap_is_not_an_error(self, tmp_path: Path):
        """The cap bounds recursion; a chain that ends at it is well-formed."""
        root = self._repo(
            tmp_path,
            "  lint:\n    taskfile: taskfiles/lint.yml\n",
            "tasks:\n  a:\n    cmds:\n      - bash scripts/foo.sh\n",
            scripts=("foo.sh",),
        )
        res = _run(root, env={"CHECK_TASKFILE_MAX_DEPTH": "1"})
        assert res.returncode == 0, res.stderr

    def test_an_include_beyond_the_depth_cap_fails(self, tmp_path: Path):
        root = self._repo(
            tmp_path,
            "  lint:\n    taskfile: taskfiles/lint.yml\n",
            "version: '3'\nincludes:\n  deep: deeper.yml\ntasks: {}\n",
        )
        (root / "taskfiles" / "deeper.yml").write_text("tasks: {}\n")
        res = _run(root, env={"CHECK_TASKFILE_MAX_DEPTH": "1"})
        assert res.returncode == 1
        assert "include depth cap" in res.stderr

    def test_an_includes_key_inside_a_task_is_not_followed(self, tmp_path: Path):
        """Only a column-0 `includes:` block declares fragments."""
        root = _make_repo(
            tmp_path,
            "version: '3'\ntasks:\n  a:\n    includes:\n      x: nope.yml\n    cmds:\n"
            "      - echo hi\n",
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr


class TestTaskReferences:
    """go-task only reports a dangling `task:`/`deps:` entry when the task is
    invoked, so the whole tree is resolved here instead."""

    def _repo(self, tmp_path: Path, root_tasks: str, fragment: str) -> Path:
        root = _make_repo(
            tmp_path,
            "version: '3'\nincludes:\n  flux: taskfiles/flux.yml\n" + root_tasks,
        )
        (root / "taskfiles").mkdir()
        (root / "taskfiles" / "flux.yml").write_text(fragment)
        return root

    ROOT = (
        "tasks:\n"
        "  lint:\n"
        "    cmds:\n"
        "      - task: flux:sync\n"
    )
    FRAGMENT = (
        "tasks:\n"
        "  sync:\n"
        "    cmds:\n"
        "      - echo sync\n"
        "  render:\n"
        "    deps:\n"
        "      - sync\n"
        "    cmds:\n"
        "      - task: :lint\n"
    )

    def test_a_resolvable_tree_passes(self, tmp_path: Path):
        res = _run(self._repo(tmp_path, self.ROOT, self.FRAGMENT))
        assert res.returncode == 0, res.stderr

    def test_a_dangling_cmds_reference_fails(self, tmp_path: Path):
        root = self._repo(
            tmp_path, self.ROOT.replace("flux:sync", "flux:syncc"), self.FRAGMENT
        )
        res = _run(root)
        assert res.returncode == 1
        assert "references flux:syncc" in res.stderr

    def test_a_dangling_bare_deps_string_fails(self, tmp_path: Path):
        root = self._repo(
            tmp_path, self.ROOT, self.FRAGMENT.replace("      - sync\n", "      - syncc\n")
        )
        res = _run(root)
        assert res.returncode == 1
        assert "references flux:syncc" in res.stderr

    def test_a_dangling_deps_mapping_fails(self, tmp_path: Path):
        root = self._repo(
            tmp_path,
            self.ROOT,
            self.FRAGMENT.replace("      - sync\n", "      - task: syncc\n"),
        )
        res = _run(root)
        assert res.returncode == 1
        assert "references flux:syncc" in res.stderr

    def test_a_dangling_inline_deps_list_fails(self, tmp_path: Path):
        root = self._repo(
            tmp_path,
            self.ROOT,
            self.FRAGMENT.replace("    deps:\n      - sync\n", "    deps: [syncc]\n"),
        )
        res = _run(root)
        assert res.returncode == 1
        assert "references flux:syncc" in res.stderr

    def test_a_leading_colon_reference_is_root_relative(self, tmp_path: Path):
        """Without the root-relative rule, `:lint` would resolve to flux:lint."""
        root = self._repo(
            tmp_path, self.ROOT, self.FRAGMENT.replace("- task: :lint", "- task: :linnt")
        )
        res = _run(root)
        assert res.returncode == 1
        assert "references linnt" in res.stderr

    def test_a_bare_reference_qualifies_within_its_namespace(self, tmp_path: Path):
        """A bare `sync` in the root file is not flux:sync."""
        root = self._repo(tmp_path, self.ROOT.replace("flux:sync", "sync"), self.FRAGMENT)
        res = _run(root)
        assert res.returncode == 1
        assert "references sync" in res.stderr

    def test_a_templated_reference_is_skipped(self, tmp_path: Path):
        root = self._repo(
            tmp_path, self.ROOT.replace("flux:sync", "{{.NS}}:status"), self.FRAGMENT
        )
        res = _run(root)
        assert res.returncode == 0, res.stderr

    def test_the_reference_arm_is_env_disableable(self, tmp_path: Path):
        root = self._repo(
            tmp_path, self.ROOT.replace("flux:sync", "flux:syncc"), self.FRAGMENT
        )
        res = _run(root, env={"CHECK_TASKFILE_REFS": "0"})
        assert res.returncode == 0, res.stderr


class TestOrphanFragments:
    """A fragment no include names is inert: `task --list` omits every task
    in it and no other gate fires."""

    def _repo(self, tmp_path: Path, extra_fragment: str | None) -> Path:
        root = _make_repo(
            tmp_path,
            "version: '3'\nincludes:\n  flux: taskfiles/flux.yml\n"
            "tasks:\n  lint:\n    cmds:\n      - echo hi\n",
        )
        (root / "taskfiles").mkdir()
        (root / "taskfiles" / "flux.yml").write_text("tasks:\n  sync:\n    cmds:\n      - echo s\n")
        if extra_fragment:
            (root / "taskfiles" / extra_fragment).write_text(
                "tasks:\n  x:\n    cmds:\n      - echo x\n"
            )
        return root

    def test_a_fully_included_fragment_dir_passes(self, tmp_path: Path):
        res = _run(self._repo(tmp_path, None))
        assert res.returncode == 0, res.stderr

    def test_a_fragment_no_include_reaches_fails(self, tmp_path: Path):
        res = _run(self._repo(tmp_path, "orphan.yml"))
        assert res.returncode == 1
        assert "taskfiles/orphan.yml is not reached" in res.stderr

    def test_a_yaml_suffixed_orphan_is_caught_too(self, tmp_path: Path):
        res = _run(self._repo(tmp_path, "orphan.yaml"))
        assert res.returncode == 1
        assert "taskfiles/orphan.yaml is not reached" in res.stderr

    def test_the_fragment_dir_is_env_overridable(self, tmp_path: Path):
        root = self._repo(tmp_path, "orphan.yml")
        res = _run(root, env={"CHECK_TASKFILE_FRAGMENT_DIR": "nowhere"})
        assert res.returncode == 0, res.stderr

    def test_an_empty_fragment_dir_setting_disables_the_scan(self, tmp_path: Path):
        root = self._repo(tmp_path, "orphan.yml")
        res = _run(root, env={"CHECK_TASKFILE_FRAGMENT_DIR": ""})
        assert res.returncode == 0, res.stderr


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
