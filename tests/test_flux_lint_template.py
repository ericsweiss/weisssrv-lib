"""ci/validate/flux-lint.yml runs scripts/flux-lint.sh rather than its own copy.

tests/test_flux_lint.py proves the program; this asserts the wiring — both arms
reach the script, and the job keeps what `extra_validation` reads.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from script_loader import SCRIPTS, load_path
from test_flux_lint import NAMESPACE, _write, build_tree

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "validate" / "flux-lint.yml"
SCRIPT = REPO / "scripts" / "flux-lint.sh"

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

_INPUT_RE = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9_]+)\s*\]\]")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path)


def _spec_and_body() -> tuple[dict, str]:
    head, body = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)
    return yaml.safe_load(head)["spec"]["inputs"], body


def _rendered_script() -> str:
    """The job's script with every input replaced by its declared default."""
    inputs, body = _spec_and_body()
    rendered = _INPUT_RE.sub(lambda m: str(inputs[m.group(1)].get("default", "")), body)
    return ci_yaml.parse_ci(rendered)["flux-lint"]["script"][0]


def _before_script() -> str:
    job = ci_yaml.parse_ci(TEMPLATE.read_text(encoding="utf-8"))["$[[ inputs.job_name ]]"]
    return job["before_script"][0]


class TestTheProgramLivesInOneFile:
    """The strict-render gap opened because this program existed three times;
    a second copy here would reopen it."""

    @pytest.mark.parametrize("fragment", [
        "flux envsubst --strict",
        "kustomize build",
        "kubeconform",
        "yaml.safe_load_all",
        "FLUX_ENVSUBST_VARS",
    ])
    def test_the_job_script_carries_no_piece_of_the_render_loop(self, fragment):
        assert fragment not in _rendered_script(), (
            "%r belongs in scripts/flux-lint.sh, which both this job and a "
            "consumer's flux:lint task run" % fragment
        )

    @pytest.mark.parametrize("fragment", [
        "flux envsubst --strict", "kustomize build", "kubeconform",
    ])
    def test_the_script_does_carry_it(self, fragment):
        """Mutation guard: the assertions above pass vacuously if the program
        is nowhere."""
        assert fragment in SCRIPT.read_text(encoding="utf-8")

    def test_both_arms_invoke_the_script(self):
        script = _rendered_script()
        assert script.count('bash "$FLUX_LINT_SCRIPT"') == 2
        assert "--mode simple" in script

    def test_a_consumer_that_has_not_vendored_it_is_told_so(self):
        script = _rendered_script()
        assert '[ -f "$FLUX_LINT_SCRIPT" ]' in script
        assert "scripts/vendorable-paths.yml" in script

    def test_the_script_path_is_a_consumer_tree_input(self):
        inputs, _body = _spec_and_body()
        assert inputs["flux_lint_script"]["default"] == "scripts/flux-lint.sh"


class TestTheJobKeepsWhatExtraValidationReads:
    """`extra_validation` is consumer shell injected into THIS shell, so the
    corpus, the schema version and the ConfigMap exports stay job state."""

    def test_the_corpus_and_schema_version_are_exported_before_the_script(self):
        script = _rendered_script()
        assert "export RENDER_ALL" in script
        assert "export K8S_VER" in script
        assert script.index("export RENDER_ALL") < script.index(
            'bash "$FLUX_LINT_SCRIPT" \\')

    def test_the_configmap_exports_are_evaluated_in_the_job(self):
        assert 'eval "$VARS"' in _rendered_script()

    def test_a_failing_script_does_not_skip_it(self):
        script = _rendered_script()
        assert 'bash "$FLUX_LINT_SCRIPT" \\' in script
        assert "--render-all \"$RENDER_ALL\" || FAILED=1" in script
        assert script.rstrip().endswith("exit $FAILED")


class TestArmScopedInputs:
    @pytest.mark.parametrize("name", ["kustomize_path", "allowed_skips", "cluster_dir"])
    def test_each_arm_scoped_input_says_which_arm_reads_it(self, name):
        inputs, _body = _spec_and_body()
        assert re.search(r"\(substitute=(true|false)\)", inputs[name]["description"]), name

    def test_substitute_mode_requires_cluster_dir(self):
        """cluster_dir carries a default, so the substitute arm has to check it."""
        assert "cluster_dir is required when substitute=true" in _rendered_script()
        inputs, _body = _spec_and_body()
        assert inputs["cluster_dir"]["default"] == "", (
            "cluster_dir is defaulted so the simple arm need not pass an inert value"
        )

    def test_the_substitute_arm_installs_the_pinned_flux_cli(self):
        before = _before_script()
        assert 'fetch_verified flux.tar.gz "$FLUX_URL" "$FLUX_SHA256"' in before
        # The non-root tenant arm substitutes nothing, so it must not pay for
        # the download: the `else` half takes kubeconform and kustomize only.
        assert "flux" not in before.split("else", 1)[1]

    def test_the_cli_is_installed_from_the_input_the_guard_compares(self):
        inputs, _body = _spec_and_body()
        assert 'FLUX_VERSION: "$[[ inputs.flux_version ]]"' in TEMPLATE.read_text()
        assert inputs["flux_version"]["default"]


class TestTheWiringActuallyRuns:
    """Text assertions cannot tell a correct argv from a plausible one."""

    def _run(self, tree: Path, **env) -> subprocess.CompletedProcess:
        full = {
            "PATH": "%s:%s:/usr/bin:/bin" % (tree / "bin", Path(sys.executable).parent),
            "HOME": os.environ.get("HOME", str(tree)),
            "STUB_RENDERS": str(tree / "renders"),
            "FLUX_LINT_SCRIPT": str(SCRIPT),
            "CRD_CATALOG_REF": "deadbeef",
            "K8S_VERSION_INPUT": "",
        }
        full.update(env)
        return subprocess.run(["bash", "-c", _rendered_script()],
                              cwd=tree, env=full, capture_output=True, text=True)

    def test_the_substitute_arm_lints_the_cluster_dir(self, tree):
        proc = self._run(
            tree, SUBSTITUTE="true",
            CLUSTER_DIR="kubernetes/clusters/demo",
            VERSIONS_CONFIGMAP=(
                "kubernetes/infrastructure/sources/versions-configmap.yaml"),
            FLUX_RENDER_SCRIPT=str(REPO / "scripts" / "flux-render.sh"),
            SKIPPED_SCRIPT=str(REPO / "scripts" / "kubeconform-skipped.py"),
            EXPECTED_SKIPPED_FILE="", REQUIRE_CLUSTER_ROOT="true",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Building + validating kubernetes/clusters/demo/apps.yaml" in proc.stdout
        assert "Building cluster root" in proc.stdout

    def test_the_substitute_arm_fails_on_a_rejected_render(self, tree):
        _write(tree / "renders" / "kubernetes_apps.yaml",
               NAMESPACE.replace("demo", "x${conf%/*}"))
        proc = self._run(
            tree, SUBSTITUTE="true",
            CLUSTER_DIR="kubernetes/clusters/demo",
            VERSIONS_CONFIGMAP=(
                "kubernetes/infrastructure/sources/versions-configmap.yaml"),
            FLUX_RENDER_SCRIPT=str(REPO / "scripts" / "flux-render.sh"),
            SKIPPED_SCRIPT=str(REPO / "scripts" / "kubeconform-skipped.py"),
            EXPECTED_SKIPPED_FILE="", REQUIRE_CLUSTER_ROOT="true",
        )
        assert proc.returncode == 1
        assert "fails Flux's strict substitution" in proc.stdout

    def test_the_tenant_arm_builds_the_one_pinned_path(self, tree):
        proc = self._run(tree, SUBSTITUTE="false",
                         CI_PROJECT_DIR=str(tree),
                         KUSTOMIZE_PATH="kubernetes/apps", ALLOWED_SKIPS="0")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "=== kustomize build kubernetes/apps ===" in proc.stdout

    def test_the_tenant_arm_fails_on_an_empty_render(self, tree):
        _write(tree / "renders" / "kubernetes_apps.yaml", "# nothing here\n")
        proc = self._run(tree, SUBSTITUTE="false",
                         CI_PROJECT_DIR=str(tree),
                         KUSTOMIZE_PATH="kubernetes/apps", ALLOWED_SKIPS="0")
        assert proc.returncode == 1
        assert "rendered no document" in proc.stdout

    def test_a_missing_script_is_reported_before_anything_runs(self, tree):
        proc = self._run(tree, SUBSTITUTE="false",
                         FLUX_LINT_SCRIPT="scripts/flux-lint.sh",
                         CI_PROJECT_DIR=str(tree),
                         KUSTOMIZE_PATH="kubernetes/apps", ALLOWED_SKIPS="0")
        assert proc.returncode == 1
        assert "scripts/flux-lint.sh not found" in proc.stdout
