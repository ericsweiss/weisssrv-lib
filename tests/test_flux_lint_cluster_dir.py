"""ci/validate/flux-lint.yml — how the cluster-dir loop triages a document.

A Flux Kustomization the job cannot build fails it; another kind is skipped.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from script_loader import SCRIPTS, load_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "validate" / "flux-lint.yml"

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

FLUX_KS = """\
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: %s
spec:
  prune: true
%s"""
GIT_REPOSITORY = """\
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: flux-system
"""


def _script() -> str:
    job = ci_yaml.parse_ci(TEMPLATE.read_text(encoding="utf-8"))["$[[ inputs.job_name ]]"]
    return job["script"][0]


@pytest.fixture(scope="module")
def parser(tmp_path_factory) -> Path:
    """The loop's `python3 -c` document parser, written out as a file."""
    match = re.search(r"python3 -c '\n(.*?)\n' \"\$ks\"", _script(), re.S)
    assert match, "the cluster-dir loop no longer embeds a python3 -c parser"
    path = tmp_path_factory.mktemp("parser") / "parse.py"
    path.write_text(match.group(1) + "\n", encoding="utf-8")
    return path


def _classify(parser: Path, tmp_path: Path, text: str):
    """(exit code, printed lines) for one cluster-dir file."""
    doc = tmp_path / "child.yaml"
    doc.write_text(text, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(parser), str(doc)], capture_output=True, text=True
    )
    return proc.returncode, proc.stdout.splitlines()


def test_a_kustomization_reports_its_path(parser, tmp_path):
    rc, out = _classify(parser, tmp_path, FLUX_KS % ("apps", "  path: ./kubernetes/apps\n"))
    assert (rc, out) == (0, ["Kustomization", "./kubernetes/apps"])


def test_a_pathless_kustomization_is_still_a_kustomization(parser, tmp_path):
    """The shell needs the kind to fail, not a bare empty path it would skip."""
    rc, out = _classify(parser, tmp_path, FLUX_KS % ("apps", ""))
    assert (rc, out) == (0, ["Kustomization", ""])


def test_another_kind_is_named_by_its_api_version(parser, tmp_path):
    rc, out = _classify(parser, tmp_path, GIT_REPOSITORY)
    assert rc == 0
    assert out[0] == "source.toolkit.fluxcd.io/v1 GitRepository"


def test_a_kustomize_native_kustomization_is_not_a_flux_one(parser, tmp_path):
    rc, out = _classify(
        parser, tmp_path,
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n",
    )
    assert rc == 0
    assert out[0] == "kustomize.config.k8s.io/v1beta1 Kustomization"


def test_the_kustomization_in_a_multi_document_file_wins(parser, tmp_path):
    rc, out = _classify(
        parser, tmp_path,
        GIT_REPOSITORY + "---\n" + FLUX_KS % ("apps", "  path: ./kubernetes/apps\n"),
    )
    assert (rc, out) == (0, ["Kustomization", "./kubernetes/apps"])


def test_two_kustomizations_in_one_file_fail(parser, tmp_path):
    rc, _ = _classify(
        parser, tmp_path,
        FLUX_KS % ("a", "  path: ./a\n") + "---\n" + FLUX_KS % ("b", "  path: ./b\n"),
    )
    assert rc != 0


def test_unparsable_yaml_fails(parser, tmp_path):
    rc, _ = _classify(parser, tmp_path, "spec: [\n")
    assert rc != 0


def _branch(condition: str) -> str:
    """Body of the loop's `if <condition>; then ... fi`."""
    match = re.search(
        re.escape(condition) + r"; then\n(.*?)\n  fi\n", _script(), re.S
    )
    assert match, "branch %r not found" % condition
    return match.group(1)


def _fails_the_job(body: str) -> bool:
    return "FAILED=1" in body


def test_a_pathless_kustomization_fails_the_job():
    body = _branch('if [ -z "$SRCPATH" ]')
    assert _fails_the_job(body)
    assert "SKIP" not in body


def test_the_pathless_assertion_is_load_bearing():
    """Mutation: a branch that only reports and continues must be reported."""
    body = _branch('if [ -z "$SRCPATH" ]')
    assert not _fails_the_job(body.replace("FAILED=1", "true"))


def test_only_another_kind_is_skipped_quietly():
    body = _branch('if [ "$KSKIND" != "Kustomization" ]')
    assert "SKIP" in body and "$KSKIND" in body
    assert not _fails_the_job(body)


def test_the_loop_still_reads_the_cluster_dir():
    """Both branch assertions would pass vacuously against an empty loop."""
    assert 'for ks in "$CLUSTER_DIR"/*.yaml; do' in _script()
