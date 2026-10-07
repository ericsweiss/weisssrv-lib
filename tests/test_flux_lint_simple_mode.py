"""ci/validate/flux-lint.yml simple mode (substitute=false) cannot pass on nothing.

The arm is run as real bash against stub kustomize/kubeconform binaries, so an
empty render, a failed validation and a vacuous skip count each fail the job.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml
from script_loader import SCRIPTS, load_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "validate" / "flux-lint.yml"

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

_INPUT_RE = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9_]+)\s*\]\]")

TWO_DOCS = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: demo\n"


def _spec_and_body() -> tuple[dict, str]:
    head, body = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)
    return yaml.safe_load(head)["spec"]["inputs"], body


def _rendered_script() -> str:
    """The job's script with every input replaced by its declared default."""
    inputs, body = _spec_and_body()
    rendered = _INPUT_RE.sub(lambda m: str(inputs[m.group(1)].get("default", "")), body)
    job = ci_yaml.parse_ci(rendered)["flux-lint"]
    return job["script"][0]


def _run(tmp_path: Path, *, render: str, summary: str, kubeconform_rc: int = 0,
         allowed_skips: str = "0") -> subprocess.CompletedProcess:
    """Run the simple arm with stub tools; returns the completed process."""
    bin_dir = tmp_path / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "kustomize").write_text(
        '#!/bin/sh\ncat "$STUB_RENDER"\n', encoding="utf-8"
    )
    (bin_dir / "kubeconform").write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$STUB_SUMMARY"\nexit "$STUB_RC"\n',
        encoding="utf-8",
    )
    for tool in ("kustomize", "kubeconform"):
        (bin_dir / tool).chmod(0o755)
    (tmp_path / "render.yaml").write_text(render, encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "CI_PROJECT_DIR": str(tmp_path),
        "SUBSTITUTE": "false",
        "K8S_VERSION_INPUT": "",
        "KUSTOMIZE_PATH": "kubernetes/flux",
        "ALLOWED_SKIPS": allowed_skips,
        "CRD_CATALOG_REF": "deadbeef",
        "STUB_RENDER": str(tmp_path / "render.yaml"),
        "STUB_SUMMARY": summary,
        "STUB_RC": str(kubeconform_rc),
    }
    return subprocess.run(
        ["bash", "-c", _rendered_script()],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )


def test_a_clean_render_passes(tmp_path):
    """Vacuity guard: the stub harness must be able to produce a green run."""
    proc = _run(tmp_path, render=TWO_DOCS, summary="Summary: 1 resource found - Valid: 1, Invalid: 0, Errors: 0, Skipped: 0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_empty_render_fails(tmp_path):
    proc = _run(tmp_path, render="", summary="Summary: 0 resources found - Skipped: 0")
    assert proc.returncode == 1
    assert "rendered no document" in proc.stdout


def test_a_render_holding_only_comments_fails(tmp_path):
    """An emptied `resources:` list still renders kustomize's own preamble."""
    proc = _run(tmp_path, render="# nothing here\n", summary="Summary: 0 resources found - Skipped: 0")
    assert proc.returncode == 1
    assert "rendered no document" in proc.stdout


def test_a_failing_kubeconform_fails_and_reports(tmp_path):
    proc = _run(
        tmp_path, render=TWO_DOCS, kubeconform_rc=1,
        summary="demo.yaml - Namespace demo is invalid: nope",
    )
    assert proc.returncode == 1
    assert "is invalid" in proc.stdout


def test_a_skipped_resource_fails_at_the_default(tmp_path):
    proc = _run(tmp_path, render=TWO_DOCS, summary="Summary: 3 resources found - Valid: 1, Invalid: 0, Errors: 0, Skipped: 2")
    assert proc.returncode == 1
    assert "against no schema" in proc.stdout


def test_a_skipped_resource_passes_within_allowed_skips(tmp_path):
    proc = _run(
        tmp_path, render=TWO_DOCS, allowed_skips="2",
        summary="Summary: 3 resources found - Valid: 1, Invalid: 0, Errors: 0, Skipped: 2",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_summary_without_a_skipped_field_fails(tmp_path):
    """An unreachable catalog is the case this covers: no field, not a zero."""
    proc = _run(tmp_path, render=TWO_DOCS, summary="Summary: 3 resources found - Valid: 3")
    assert proc.returncode == 1
    assert "against no schema" in proc.stdout


def test_a_non_numeric_allowed_skips_fails(tmp_path):
    proc = _run(
        tmp_path, render=TWO_DOCS, allowed_skips="many",
        summary="Summary: 1 resource found - Skipped: 0",
    )
    assert proc.returncode == 1
    assert "non-negative integer" in proc.stdout


def test_substitute_mode_requires_cluster_dir():
    """cluster_dir now carries a default, so the substitute arm has to check it."""
    script = _rendered_script()
    assert 'cluster_dir is required when substitute=true' in script
    inputs, _body = _spec_and_body()
    assert inputs["cluster_dir"]["default"] == "", (
        "cluster_dir is defaulted so the simple arm need not pass an inert value"
    )


@pytest.mark.parametrize("name", ["kustomize_path", "allowed_skips", "cluster_dir"])
def test_each_arm_scoped_input_says_which_arm_reads_it(name):
    inputs, _body = _spec_and_body()
    assert re.search(r"\(substitute=(true|false)\)", inputs[name]["description"]), name
