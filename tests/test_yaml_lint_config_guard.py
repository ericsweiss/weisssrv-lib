#!/usr/bin/env python3
"""The yaml-lint job refuses `-d relaxed` when the repo ships its own profile.

`-d relaxed` is yamllint's profile, so a consumer that vendors one and forgets
`-c` gets a green job that applied different rules.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import CILoader as _CILoader  # noqa: E402

TEMPLATE = REPO / "ci" / "lint" / "yaml-lint.yml"
PLACEHOLDER = re.compile(r"\$\[\[ inputs\.(\w+) \]\]")

# Every name the guard treats as a shipped profile.
PROFILE_NAMES = (
    "lint/yamllint-relaxed.yml",
    "lint/yamllint-self.yml",
    "lint/yamllint.yml",
    ".yamllint",
    ".yamllint.yml",
    ".yamllint.yaml",
)


def _documents() -> tuple[dict, dict]:
    return tuple(
        doc for doc in yaml.load_all(TEMPLATE.read_text(), Loader=_CILoader) if doc
    )


def script(**overrides: str) -> str:
    """The job's single `script` entry with every `$[[ inputs.x ]]` resolved."""
    spec, body = _documents()
    steps = list(body.values())[0]["script"]
    assert len(steps) == 1, f"expected one script entry, found {len(steps)}"
    values = {
        name: str(overrides.get(name, definition.get("default", "")))
        for name, definition in spec["spec"]["inputs"].items()
    }
    # GitLab renders a boolean input unquoted; the guard compares the text.
    values = {k: "true" if v == "True" else "false" if v == "False" else v
              for k, v in values.items()}
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], steps[0])


def run(tmp_path: Path, profiles: tuple[str, ...] = (), **overrides: str):
    """The script in a tree holding `profiles`, with yamllint stubbed to pass."""
    for name in profiles:
        profile = tmp_path / name
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("extends: relaxed\n")
    (tmp_path / ".gitlab-ci.yml").write_text("---\nstages: [lint]\n")
    stub = tmp_path / "yamllint-stub"
    stub.write_text('#!/bin/sh\nexit 0\n')
    stub.chmod(0o755)
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", script(**overrides)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "YAMLLINT_BIN": str(stub)},
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestTheGuardFails:
    """The negative cases: a shipped profile the job does not point at."""

    def test_default_config_with_a_shipped_profile_is_refused(self, tmp_path):
        result = run(tmp_path, profiles=("lint/yamllint-relaxed.yml",),
                     targets=".gitlab-ci.yml")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "lint/yamllint-relaxed.yml exists" in result.stdout
        assert '-c lint/yamllint-relaxed.yml' in result.stdout

    def test_every_discovered_profile_name_trips_it(self, tmp_path):
        for index, name in enumerate(PROFILE_NAMES):
            tree = tmp_path / str(index)
            tree.mkdir()
            result = run(tree, profiles=(name,), targets=".gitlab-ci.yml")
            assert result.returncode == 1, f"{name} did not trip the guard"


class TestTheGuardStaysOut:
    """A job that points at a config, ships none, or opts out still runs."""

    def test_an_explicit_config_passes(self, tmp_path):
        result = run(tmp_path, profiles=("lint/yamllint-relaxed.yml",),
                     config="-c lint/yamllint-relaxed.yml", targets=".gitlab-ci.yml")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_no_shipped_profile_passes(self, tmp_path):
        result = run(tmp_path, targets=".gitlab-ci.yml")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_require_config_false_opts_out(self, tmp_path):
        result = run(tmp_path, profiles=("lint/yamllint-relaxed.yml",),
                     require_config="false", targets=".gitlab-ci.yml")
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_guard_runs_before_any_linting(tmp_path):
    """A refused config never reaches yamllint, so no target is linted."""
    result = run(tmp_path, profiles=(".yamllint",), targets=".gitlab-ci.yml")
    assert result.returncode == 1
    assert "YAMLLINT_BIN" not in result.stdout
    assert "yamllint-stub" not in result.stdout


def test_every_library_caller_passes_a_config():
    """The library's own include cannot rely on the default it now rejects."""
    text = (REPO / ".gitlab-ci.yml").read_text()
    assert "-c lint/yamllint-self.yml" in text
