#!/usr/bin/env python3
"""python-tests' `apt_packages` and flux-lint's substitute arm need root.
On a non-root runner each stops with an error naming the input to change,
before apt-get runs and fails on its lock."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from script_loader import REPO, SCRIPTS, load_path

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

JOB = "$[[ inputs.job_name ]]"


def _before_script(relative: str, marker: str) -> str:
    """The template's before_script entry that carries `marker`."""
    job = ci_yaml.load_ci(REPO / relative)[JOB]
    found = [s for s in job["before_script"] if marker in s]
    assert len(found) == 1, f"{relative}: expected one before_script entry with {marker!r}"
    return found[0]


def _stub_path(tmp_path: Path, uid: int) -> str:
    """A PATH whose `id -u` prints `uid` and whose apt-get records its call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "id").write_text(f"#!/bin/sh\necho {uid}\n")
    (bin_dir / "apt-get").write_text(f'#!/bin/sh\necho "$*" >> "{tmp_path}/apt.log"\nexit 0\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}"


def _run(script: str, tmp_path: Path, uid: int, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "PATH": _stub_path(tmp_path, uid), **env},
    )


class TestPythonTestsApt:
    SCRIPT = _before_script("ci/test/python-tests.yml", "APT_PACKAGES")

    def test_a_non_root_runner_is_refused_before_apt_runs(self, tmp_path):
        done = _run(self.SCRIPT, tmp_path, 1000, APT_PACKAGES="git jq")
        assert done.returncode == 1
        assert "apt_packages needs a root runner" in done.stderr
        assert not (tmp_path / "apt.log").exists()

    def test_a_root_runner_installs_the_packages(self, tmp_path):
        done = _run(self.SCRIPT, tmp_path, 0, APT_PACKAGES="git jq")
        assert done.returncode == 0, done.stderr
        assert "git jq" in (tmp_path / "apt.log").read_text()

    def test_no_packages_needs_no_root(self, tmp_path):
        done = _run(self.SCRIPT, tmp_path, 1000, APT_PACKAGES="")
        assert done.returncode == 0, done.stderr
        assert not (tmp_path / "apt.log").exists()

    def test_a_failed_apt_update_stops_the_job(self, tmp_path):
        """errexit skips the left side of `&&`, so each apt step fails on its own."""
        script = f'set -eo pipefail\napt-get() {{ [ "$1" != update ]; }}\n{self.SCRIPT}\necho continued'
        done = _run(script, tmp_path, 0, APT_PACKAGES="git jq")
        assert done.returncode == 1
        assert "apt-get update failed" in done.stderr
        assert "continued" not in done.stdout

    def test_a_failed_apt_install_stops_the_job(self, tmp_path):
        script = f'set -eo pipefail\napt-get() {{ [ "$1" = update ]; }}\n{self.SCRIPT}\necho continued'
        done = _run(script, tmp_path, 0, APT_PACKAGES="git jq")
        assert done.returncode == 1
        assert "apt-get install of git jq failed" in done.stderr
        assert "continued" not in done.stdout

    def test_no_apt_step_hides_behind_an_and_chain(self):
        assert not [line for line in self.SCRIPT.splitlines() if "apt-get" in line and "&&" in line]


def test_an_all_default_python_tests_include_runs_tag_less():
    """`tags: []` is the default, so the defaults never reach the root guard."""
    inputs = ci_yaml.spec_inputs((REPO / "ci/test/python-tests.yml").read_text(encoding="utf-8"))
    assert inputs["tags"]["default"] == []
    assert inputs["apt_packages"]["default"] == ""
    assert not inputs["image"]["default"].endswith("-slim"), "the default image must ship git"


def test_flux_lint_documents_the_root_tag_its_default_needs():
    """flux-lint's default `substitute: true` reaches the root guard."""
    inputs = ci_yaml.spec_inputs((REPO / "ci/validate/flux-lint.yml").read_text(encoding="utf-8"))
    assert inputs["substitute"]["default"] is True
    assert "root" in inputs["tags"]["description"].lower()


class TestFluxLintSubstitute:
    SCRIPT = _before_script("ci/validate/flux-lint.yml", "SUBSTITUTE")

    def test_a_non_root_runner_is_refused_before_apt_runs(self, tmp_path):
        done = _run(self.SCRIPT, tmp_path, 1000, SUBSTITUTE="true")
        assert done.returncode == 1
        assert "substitute: true needs a root runner" in done.stderr
        assert not (tmp_path / "apt.log").exists()

    def test_a_root_runner_passes_the_guard(self, tmp_path):
        """Past the guard the arm reaches apt; the stubbed pip then stops it."""
        bin_dir = tmp_path / "pipbin"
        bin_dir.mkdir()
        (bin_dir / "pip").write_text("#!/bin/sh\nexit 3\n")
        (bin_dir / "pip").chmod(0o755)
        script = f'export PATH="{bin_dir}:$PATH"\n{self.SCRIPT}'
        done = _run(script, tmp_path, 0, SUBSTITUTE="true")
        assert "needs a root runner" not in done.stderr
        assert "gettext-base" in (tmp_path / "apt.log").read_text()
        assert done.returncode == 3
