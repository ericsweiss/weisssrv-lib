"""ci/validate/flux-lint.yml holds the flux CLI equal to the cluster's release.

`envsubst --strict` is authoritative only while the CLI is the release
kustomize-controller runs, and the versions ConfigMap states that release.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml
from script_loader import SCRIPTS, load_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "validate" / "flux-lint.yml"

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

_INPUT_RE = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9_]+)\s*\]\]")

CONFIGMAP = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-versions
data:
  k3s_version: v1.36.4+k3s1
"""


def _rendered_script() -> str:
    head, body = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)
    inputs = yaml.safe_load(head)["spec"]["inputs"]
    rendered = _INPUT_RE.sub(lambda m: str(inputs[m.group(1)].get("default", "")), body)
    return ci_yaml.parse_ci(rendered)["flux-lint"]["script"][0]


def _run(tmp_path: Path, *, cli: str, declared: str | None) -> subprocess.CompletedProcess:
    """The substitute arm with a stub flux CLI reporting `cli`."""
    bin_dir = tmp_path / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "flux").write_text(f"#!/bin/sh\necho 'flux version {cli}'\n", encoding="utf-8")
    (bin_dir / "flux").chmod(0o755)
    configmap = CONFIGMAP + (f"  flux_version: {declared}\n" if declared else "")
    (tmp_path / "versions.yaml").write_text(configmap, encoding="utf-8")
    (tmp_path / "cluster").mkdir(exist_ok=True)
    # The render script's python3 must be the interpreter running the tests,
    # the one that has PyYAML; the job image's /usr/bin/python3 does not.
    env = {
        "PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "CI_PROJECT_DIR": str(tmp_path),
        "SUBSTITUTE": "true",
        "CLUSTER_DIR": str(tmp_path / "cluster"),
        "VERSIONS_CONFIGMAP": str(tmp_path / "versions.yaml"),
        "FLUX_RENDER_SCRIPT": str(REPO / "scripts" / "flux-render.sh"),
        "K8S_VERSION_INPUT": "1.36.0",
        "ALLOWED_SKIPS": "0",
        "CRD_CATALOG_REF": "deadbeef",
    }
    return subprocess.run(
        ["bash", "-c", _rendered_script()],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )


def test_a_mismatched_flux_cli_fails_the_job(tmp_path):
    proc = _run(tmp_path, cli="2.9.0", declared="2.7.1")
    assert proc.returncode == 1
    assert "flux CLI 2.9.0 != " in proc.stdout
    assert "flux_version 2.7.1" in proc.stdout


def test_a_leading_v_on_the_declared_version_still_matches(tmp_path):
    """The ConfigMap spells a version either way, and a false mismatch would
    fail every cluster that writes the `v`."""
    proc = _run(tmp_path, cli="2.9.0", declared="v2.9.0")
    assert "ERROR: flux CLI" not in proc.stdout
    assert "matches the versions-configmap flux_version" in proc.stdout


def test_a_matching_flux_cli_passes_the_guard(tmp_path):
    proc = _run(tmp_path, cli="2.9.0", declared="2.9.0")
    assert "ERROR: flux CLI" not in proc.stdout
    assert "flux CLI 2.9.0 matches" in proc.stdout


def test_a_configmap_without_flux_version_says_the_pin_is_ungated(tmp_path):
    proc = _run(tmp_path, cli="2.9.0", declared=None)
    assert "ERROR: flux CLI" not in proc.stdout
    assert "declares no flux_version" in proc.stdout


def test_the_job_installs_the_cli_from_the_same_input(tmp_path):
    """The guard compares the INSTALLED CLI, so the input it is installed from
    is the one an operator moves."""
    head = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)[0]
    inputs = yaml.safe_load(head)["spec"]["inputs"]
    assert 'FLUX_VERSION: "$[[ inputs.flux_version ]]"' in TEMPLATE.read_text()
    assert inputs["flux_version"]["default"]
