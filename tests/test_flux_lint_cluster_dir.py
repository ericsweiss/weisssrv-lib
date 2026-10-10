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


def _before_script() -> str:
    job = ci_yaml.parse_ci(TEMPLATE.read_text(encoding="utf-8"))["$[[ inputs.job_name ]]"]
    return job["before_script"][0]


def _strict_block() -> str:
    """The loop's `flux envsubst --strict` check, `if` through `fi`."""
    match = re.search(r"\n(  if ! STRICT_ERR=.*?\n  fi\n)", _script(), re.S)
    assert match, "the strict-substitution check is gone from the render loop"
    return match.group(1)


class TestStrictSubstitution:
    """Flux's Go envsubst reads forms GNU envsubst does not, so its own
    --strict is what decides whether the post-build will reconcile."""

    def test_it_runs_before_the_gnu_envsubst_render(self):
        script = _script()
        assert script.index("flux envsubst --strict") < script.index(
            'RENDERED=$(printf \'%s\\n\' "$RAW" | envsubst'
        )

    def test_the_cheap_pre_scan_is_still_there(self):
        """The `${`-shape scan names the offending line; --strict does not."""
        assert "MALFORMED=$(printf" in _script()

    def test_the_substitute_arm_installs_the_pinned_flux_cli(self):
        before = _before_script()
        assert 'fetch_verified flux.tar.gz "$FLUX_URL" "$FLUX_SHA256"' in before
        # The non-root tenant arm has no envsubst step, so it must not pay for
        # the download: the `else` half downloads kubeconform and kustomize only.
        tenant = before.split("else", 1)[1]
        assert "flux" not in tenant

    def _run(self, tmp_path: Path, returncode: int, message: str = ""):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "flux"
        # The stub must drain stdin: under pipefail a SIGPIPE'd printf would
        # fail the pipeline whatever flux returned.
        stub.write_text(
            "#!/bin/sh\ncat >/dev/null\n"
            f"printf '%s\\n' '{message}' >&2\nexit {returncode}\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        script = (
            'set -eo pipefail\nFAILED=0\nRAW="kind: ConfigMap"\n'
            "SRCPATH=kubernetes/apps\nfor _ in 1; do\n"
            + _strict_block()
            + "done\nexit $FAILED\n"
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        )

    def test_a_rejected_render_fails_the_job_and_quotes_flux(self, tmp_path):
        result = self._run(tmp_path, 1, "variable not set: conf")
        assert result.returncode == 1
        assert "kubernetes/apps fails Flux's strict substitution" in result.stdout
        assert "variable not set: conf" in result.stdout

    def test_an_accepted_render_leaves_the_job_green(self, tmp_path):
        result = self._run(tmp_path, 0)
        assert result.returncode == 0, result.stderr
        assert "strict substitution" not in result.stdout
