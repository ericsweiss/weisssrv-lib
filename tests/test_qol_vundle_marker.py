#!/usr/bin/env python3
"""Vundle exits 0 on a failed clone, so the qol role reads back a bundle
directory per plugin: an install that cloned nothing must fail the play instead
of writing the marker that makes `creates:` skip it forever."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "qol"
NEOVIM = ROLE / "tasks" / "neovim.yml"
INSTALL_TASK = "Install Vundle plugins"
MARKER_TASK = "Mark Vundle plugins as installed (marker keyed to the plugin-list hash)"


def _tasks() -> list[dict]:
    return yaml.safe_load(NEOVIM.read_text(encoding="utf-8"))


def _task(name: str) -> dict:
    return next(t for t in _tasks() if t.get("name") == name)


def _render(template: str, **context: object) -> str:
    """Render a task string with the Ansible filters it uses."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["quote"] = shlex.quote
    env.filters["regex_replace"] = lambda value, pattern, repl: re.sub(pattern, repl, value)
    return env.from_string(template).render(**context)


def _script(home: Path, plugins: list[str]) -> str:
    cmd = _task(INSTALL_TASK)["ansible.builtin.shell"]["cmd"]
    return _render(cmd, qol_admin_home_resolved=str(home), qol_nvim_plugins=plugins)


def _run(home: Path, plugins: list[str], installs: list[str]) -> subprocess.CompletedProcess:
    """Run the task body with an `nvim` stub that creates `installs` and exits 0."""
    stub_dir = home.parent / "bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    made = "\n".join(f"mkdir -p {shlex.quote(str(home / '.vim/bundle' / name))}" for name in installs)
    (stub_dir / "nvim").write_text(f"#!/bin/sh\n{made}\nexit 0\n", encoding="utf-8")
    (stub_dir / "nvim").chmod(0o755)
    (home / ".vim" / "bundle").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["sh", "-c", _script(home, plugins)],
        capture_output=True,
        text=True,
        env={"PATH": f"{stub_dir}:/usr/bin:/bin"},
    )


def test_an_install_that_cloned_nothing_fails(tmp_path):
    home = tmp_path / "home" / "admin"
    home.mkdir(parents=True)
    proc = _run(home, ["tpope/vim-fugitive"], installs=[])
    assert proc.returncode != 0, proc.stdout
    assert "vim-fugitive" in proc.stderr


def test_an_install_that_cloned_every_plugin_succeeds(tmp_path):
    home = tmp_path / "home" / "admin"
    home.mkdir(parents=True)
    plugins = ["tpope/vim-fugitive", "sheerun/vim-polyglot"]
    proc = _run(home, plugins, installs=["vim-fugitive", "vim-polyglot"])
    assert proc.returncode == 0, proc.stderr


def test_one_missing_plugin_out_of_several_fails(tmp_path):
    home = tmp_path / "home" / "admin"
    home.mkdir(parents=True)
    plugins = ["tpope/vim-fugitive", "sheerun/vim-polyglot"]
    proc = _run(home, plugins, installs=["vim-fugitive"])
    assert proc.returncode != 0
    assert "vim-polyglot" in proc.stderr


@pytest.mark.parametrize(
    ("spec", "directory"),
    [
        ("tpope/vim-fugitive", "vim-fugitive"),
        ("https://github.com/tpope/vim-fugitive.git", "vim-fugitive"),
    ],
)
def test_the_read_back_uses_the_directory_vundle_clones_into(tmp_path, spec, directory):
    """Vundle names the bundle dir after the repo, minus any `.git` suffix."""
    home = tmp_path / "home" / "admin"
    home.mkdir(parents=True)
    assert _run(home, [spec], installs=[directory]).returncode == 0


def test_an_empty_plugin_list_is_not_a_shell_error(tmp_path):
    """`qol_nvim_plugins: []` must leave a valid loop, not `for x in ; do`."""
    home = tmp_path / "home" / "admin"
    home.mkdir(parents=True)
    proc = _run(home, [], installs=[])
    assert proc.returncode == 0, proc.stderr


def test_a_plugin_name_cannot_escape_the_read_back(tmp_path):
    """A hostile plugin entry is quoted into the loop, not interpreted."""
    home = tmp_path / "home" / "admin"
    home.mkdir(parents=True)
    proc = _run(home, ["evil/x; touch /tmp/qol-pwned"], installs=[])
    assert proc.returncode != 0
    assert "touch" not in proc.stdout


def test_the_marker_is_written_only_when_the_install_changed():
    marker = _task(MARKER_TASK)
    assert "qol_vundle_install is changed" in marker["when"]


def test_the_marker_and_the_creates_guard_name_the_same_file():
    """A marker written at another path would never satisfy the guard."""
    install = _task(INSTALL_TASK)["ansible.builtin.shell"]["creates"]
    marker = _task(MARKER_TASK)["ansible.builtin.copy"]["dest"]
    assert install == marker


def test_the_install_retries_a_transient_clone_failure():
    install = _task(INSTALL_TASK)
    assert install["retries"] > 1
    assert install["until"] == "qol_vundle_install is succeeded"
    # Exhaustion must stay fatal: a swallowed failure is the bug under test.
    assert "failed_when" not in install
    assert "ignore_errors" not in install


def test_git_cannot_stall_the_install_on_a_credential_prompt():
    assert _task(INSTALL_TASK)["environment"]["GIT_TERMINAL_PROMPT"] == "0"
