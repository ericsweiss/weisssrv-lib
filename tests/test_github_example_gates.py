#!/usr/bin/env python3
"""The GitHub example workflow's three floors fail on the tree they describe.

ruff, kustomize and kubeconform all exit 0 on nothing, so each floor is checked
in the step's own shell. Version pins live in tests/test_pin_parity.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / "ci" / "github" / "ci.example.yml"

STUB = "#!/bin/sh\nexit 0\n"


def _run_script(job: str, step_name: str) -> str:
    """The `run:` body of one named step of one job."""
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    assert job in jobs, f"{WORKFLOW} has no job {job!r}"
    matches = [
        step["run"]
        for step in jobs[job]["steps"]
        if step.get("name") == step_name and "run" in step
    ]
    assert len(matches) == 1, f"expected one {job}/{step_name!r} run step"
    return matches[0]


SKIP_FREE_SUMMARY = "Summary: 1 resource found - Valid: 1, Invalid: 0, Errors: 0, Skipped: 0"


def _workflow_env(name: str) -> str:
    """One `env:` entry, resolved to the literal a consumer that sets no
    repository variable runs."""
    value = str(yaml.safe_load(WORKFLOW.read_text())["env"][name])
    match = re.fullmatch(r"\$\{\{\s*vars\.\w+\s*\|\|\s*'([^']+)'\s*\}\}", value)
    return match.group(1) if match else value


def _stub_bin(tmp_path: Path, *names: str, kustomize_output: str = "",
              kubeconform_summary: str = SKIP_FREE_SUMMARY) -> Path:
    """A PATH directory holding a stub per name."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in names:
        script = bin_dir / name
        if name == "kustomize":
            script.write_text(f"#!/bin/sh\nprintf '%s' '{kustomize_output}'\n")
        elif name == "kubeconform":
            script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{kubeconform_summary}'\n")
        else:
            script.write_text(STUB)
        script.chmod(0o755)
    return bin_dir


def _bash(script: str, cwd: Path, bin_dir: Path, **env: str):
    environ = dict(os.environ)
    environ["PATH"] = f"{bin_dir}:{environ['PATH']}"
    environ.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env=environ,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(autouse=True)
def _needs_bash():
    if not shutil.which("bash"):
        pytest.skip("bash not available")


class TestPythonLintFloor:
    """ruff exits 0 on a directory holding no Python file."""

    SCRIPT = ("python-lint", "Lint scripts/")

    def test_fails_when_no_python_is_covered(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "helper.sh").write_text("#!/bin/sh\n")
        result = _bash(
            _run_script(*self.SCRIPT), tmp_path, _stub_bin(tmp_path, "ruff")
        )
        assert result.returncode != 0
        assert "ruff matched no Python file" in result.stdout

    def test_passes_when_a_python_file_is_covered(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "gate.py").write_text("x = 1\n")
        result = _bash(
            _run_script(*self.SCRIPT), tmp_path, _stub_bin(tmp_path, "ruff")
        )
        assert result.returncode == 0, result.stderr


class TestFluxLintEmptyRender:
    """An emptied `resources:` list renders nothing and Flux prunes the lot."""

    SCRIPT = ("flux-lint", "Render + validate")

    def _result(self, tmp_path, rendered: str, summary: str = SKIP_FREE_SUMMARY,
                allowed_skips: str = "0"):
        bin_dir = _stub_bin(
            tmp_path, "kustomize", "kubeconform", kustomize_output=rendered,
            kubeconform_summary=summary,
        )
        runner_temp = tmp_path / "runner"
        runner_temp.mkdir()
        return _bash(
            _run_script(*self.SCRIPT),
            tmp_path,
            bin_dir,
            RUNNER_TEMP=str(runner_temp),
            K8S_VERSION="1.36.0",
            K8S_VERSION_OVERRIDE="1.36.0",
            ALLOWED_SKIPS=allowed_skips,
            CRD_CATALOG_REF=_workflow_env("CRD_CATALOG_REF"),
        )

    def test_fails_when_kubeconform_skipped_a_resource(self, tmp_path):
        result = self._result(
            tmp_path, "kind: ConfigMap\n",
            summary="Summary: 2 resources found - Valid: 1, Skipped: 1",
        )
        assert result.returncode != 0
        assert "against no schema" in result.stdout

    def test_passes_a_skip_within_allowed_skips(self, tmp_path):
        result = self._result(
            tmp_path, "kind: ConfigMap\n", allowed_skips="1",
            summary="Summary: 2 resources found - Valid: 1, Skipped: 1",
        )
        assert result.returncode == 0, result.stderr

    def test_fails_on_a_non_integer_allowed_skips(self, tmp_path):
        """`[ -gt ]` on a non-integer errors instead of comparing, and an error
        inside an `if` condition does not trip `set -e`, so the ceiling has to
        be validated before the comparison."""
        result = self._result(
            tmp_path, "kind: ConfigMap\n", allowed_skips="two",
            summary="Summary: 2 resources found - Valid: 1, Skipped: 1",
        )
        assert result.returncode != 0
        assert "ALLOWED_SKIPS must be a non-negative integer, got 'two'" in result.stdout

    def test_fails_on_a_negative_allowed_skips(self, tmp_path):
        result = self._result(
            tmp_path, "kind: ConfigMap\n", allowed_skips="-1",
            summary="Summary: 2 resources found - Valid: 1, Skipped: 1",
        )
        assert result.returncode != 0
        assert "ALLOWED_SKIPS must be a non-negative integer, got '-1'" in result.stdout

    def test_passes_a_zero_ceiling_against_zero_skips(self, tmp_path):
        """The shipped fallback is `0`, which the validation accepts."""
        result = self._result(tmp_path, "kind: ConfigMap\n", allowed_skips="0")
        assert result.returncode == 0, result.stderr

    def test_fails_when_the_summary_carries_no_skipped_field(self, tmp_path):
        """The unreachable-catalog signature: no field at all, not a zero."""
        result = self._result(
            tmp_path, "kind: ConfigMap\n", summary="Summary: 2 resources found",
        )
        assert result.returncode != 0
        assert "against no schema" in result.stdout

    def test_fails_on_an_empty_render(self, tmp_path):
        result = self._result(tmp_path, "")
        assert result.returncode != 0
        assert "rendered no document" in result.stdout

    def test_passes_on_a_render_holding_a_document(self, tmp_path):
        result = self._result(tmp_path, "kind: ConfigMap\n")
        assert result.returncode == 0, result.stderr

    def test_announces_an_unset_k8s_version(self, tmp_path):
        bin_dir = _stub_bin(
            tmp_path, "kustomize", "kubeconform", kustomize_output="kind: ConfigMap\n"
        )
        runner_temp = tmp_path / "runner"
        runner_temp.mkdir()
        result = _bash(
            _run_script(*self.SCRIPT),
            tmp_path,
            bin_dir,
            RUNNER_TEMP=str(runner_temp),
            K8S_VERSION="1.36.0",
            K8S_VERSION_OVERRIDE="",
            ALLOWED_SKIPS="0",
            CRD_CATALOG_REF=_workflow_env("CRD_CATALOG_REF"),
        )
        assert result.returncode == 0, result.stderr
        assert "K8S_VERSION not set" in result.stdout


class TestManifestGatesFloor:
    """A repo shipping no gate script must fail, not report success."""

    SCRIPT = ("manifest-gates", "Run the manifest gates")

    def test_fails_when_no_gate_is_shipped(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        result = _bash(
            _run_script(*self.SCRIPT),
            tmp_path,
            _stub_bin(tmp_path, "true"),
            MANIFEST_ROOT="kubernetes/flux",
        )
        assert result.returncode != 0
        assert "no manifest gate ran" in result.stdout

    def test_runs_a_shipped_gate_and_reports_its_failure(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "check-kustomization.py").write_text(
            "import sys\nsys.exit(1)\n"
        )
        result = _bash(
            _run_script(*self.SCRIPT),
            tmp_path,
            _stub_bin(tmp_path, "true"),
            MANIFEST_ROOT="kubernetes/flux",
        )
        assert result.returncode != 0
        assert "check-kustomization.py" in result.stdout

    def test_passes_the_config_flag_when_the_policy_data_ships(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "netpol-except.yaml").write_text("{}\n")
        (scripts / "check-netpol-except-parity.py").write_text(
            "import sys\nprint(' '.join(sys.argv[1:]))\n"
        )
        result = _bash(
            _run_script(*self.SCRIPT),
            tmp_path,
            _stub_bin(tmp_path, "true"),
            MANIFEST_ROOT="kubernetes/flux",
        )
        assert result.returncode == 0, result.stderr
        assert "--config scripts/netpol-except.yaml kubernetes/flux" in result.stdout
