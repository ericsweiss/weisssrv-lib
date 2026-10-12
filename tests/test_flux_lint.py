"""scripts/flux-lint.sh: the render/scan/kubeconform program, end to end.

Real bash over a fixture cluster tree with stub kustomize/kubeconform/envsubst/
flux binaries. The last case holds the flux stub to the real CLI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "flux-lint.sh"
RENDER_SCRIPT = REPO / "scripts" / "flux-render.sh"
SKIPPED_SCRIPT = REPO / "scripts" / "kubeconform-skipped.py"
CHILD_SCRIPT = REPO / "scripts" / "flux-child-kustomizations.py"

CONFIGMAP = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-versions
data:
  k3s_version: v1.37.1+k3s1
  flux_version: 2.9.0
  app_version: 1.2.3
  blank_key: ""
"""

FLUX_KS = """\
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: %(name)s
spec:
  prune: true
%(extra)s  path: %(path)s
"""
GIT_REPOSITORY = """\
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: flux-system
"""
NAMESPACE = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: demo\n"

# Enough of flux's Go envsubst to decide these fixtures: `$${` is its escape,
# every other `${...}` is a variable whose leading identifier must be set.
FLUX_STUB = r'''#!/bin/sh
case "$1" in
  --version) echo "flux version ${STUB_FLUX_VERSION:-2.9.0}"; exit 0 ;;
esac
exec python3 -c '
import os
import re
import sys

text = sys.stdin.read().replace("$${", "\x00")
for match in re.finditer(r"\$\{([^}]*)\}", text):
    token = match.group(1)
    name = re.match(r"[A-Za-z_][A-Za-z0-9_]*", token)
    if not name:
        sys.exit("failed to parse variable: ${%s}" % token)
    if name.group(0) not in os.environ:
        sys.exit("variable not set: %s" % name.group(0))
'
'''

KUSTOMIZE_STUB = r'''#!/bin/sh
[ "$1" = build ] || { echo "kustomize: unsupported: $*" >&2; exit 2; }
target="$STUB_RENDERS/$(printf '%s' "$2" | tr / _).yaml"
if [ ! -f "$target" ]; then
  echo "accumulating resources: no such tree $2" >&2
  exit 1
fi
cat "$target"
'''

KUBECONFORM_STUB = '''#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
files = [a for a in args if not a.startswith("-") and os.path.exists(a)]
if not files:
    # Like kubeconform, a stream with no kinded document validates nothing.
    if "kind:" not in sys.stdin.read():
        print("Summary: 0 resource found parsing stdin - Valid: 0, Invalid: 0, Errors: 0, Skipped: 0")
        raise SystemExit(0)
# kubeconform parses a file by its extension and reports an unknown one as no
# file at all, with a clean summary.
elif not files[0].endswith((".yaml", ".yml", ".json")):
    print("Summary: 0 resource found in 0 file - Valid: 0, Invalid: 0, Errors: 0, Skipped: 0")
    raise SystemExit(0)
if "-output" in args:
    print(os.environ.get("STUB_KC_JSON", '{"resources": [{"status": "statusValid"}]}'))
    raise SystemExit(0)
print(os.environ.get(
    "STUB_KC_SUMMARY",
    "Summary: 1 resource found - Valid: 1, Invalid: 0, Errors: 0, Skipped: 0"))
raise SystemExit(int(os.environ.get("STUB_KC_RC", "0")))
'''

ENVSUBST_STUB = '#!/bin/sh\ncat\n'


def _write(path: Path, text: str, *, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


def build_tree(tmp_path: Path) -> Path:
    """A minimal cluster repo: one stage, a skippable document, a built root."""
    cluster = tmp_path / "kubernetes" / "clusters" / "demo"
    _write(cluster / "apps.yaml",
           FLUX_KS % {"name": "apps", "path": "./kubernetes/apps", "extra": ""})
    _write(cluster / "repo.yaml", GIT_REPOSITORY)
    _write(cluster / "kustomization.yaml", "resources:\n  - apps.yaml\n")
    _write(cluster / "flux-system" / "kustomization.yaml", "resources: []\n")
    _write(cluster / "flux-system" / "gotk-components.yaml", NAMESPACE)
    _write(tmp_path / "kubernetes" / "infrastructure" / "sources"
           / "versions-configmap.yaml", CONFIGMAP)
    renders = tmp_path / "renders"
    _write(renders / "kubernetes_apps.yaml", NAMESPACE)
    _write(renders / "kubernetes_clusters_demo.yaml", NAMESPACE)
    bin_dir = tmp_path / "bin"
    for name, body in (("flux", FLUX_STUB), ("kustomize", KUSTOMIZE_STUB),
                       ("kubeconform", KUBECONFORM_STUB), ("envsubst", ENVSUBST_STUB)):
        _write(bin_dir / name, body, mode=0o755)
    return tmp_path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_tree(tmp_path)


def run(tree: Path, *extra: str, env: dict | None = None,
        render: str | None = None) -> subprocess.CompletedProcess:
    """flux-lint.sh in substitute mode over the fixture tree."""
    if render is not None:
        _write(tree / "renders" / "kubernetes_apps.yaml", render)
    full = {
        "PATH": "%s:%s:/usr/bin:/bin" % (tree / "bin", Path(sys.executable).parent),
        "HOME": os.environ.get("HOME", str(tree)),
        "STUB_RENDERS": str(tree / "renders"),
    }
    full.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT),
         "--cluster-dir", "kubernetes/clusters/demo",
         "--versions-configmap",
         "kubernetes/infrastructure/sources/versions-configmap.yaml",
         "--render-script", str(RENDER_SCRIPT),
         "--skipped-script", str(SKIPPED_SCRIPT),
         "--crd-catalog-ref", "deadbeef", *extra],
        cwd=tree, env=full, capture_output=True, text=True,
    )


class TestTheHarnessCanBeGreen:
    """Vacuity guard: every failure case below is worthless without this."""

    def test_a_clean_tree_passes(self, tree):
        proc = run(tree, render=NAMESPACE + "---\n" + NAMESPACE.replace(
            "demo", "v${app_version}"))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Building + validating" in proc.stdout
        assert "Building cluster root" in proc.stdout


class TestSubstitutionFloors:
    def test_a_bash_modifier_fails_the_strict_render(self, tree):
        """The LIB-05 case: GNU envsubst ignores `${conf%/*}`, and neither
        text scan can see it, so only flux's own --strict rejects it."""
        proc = run(tree, render=NAMESPACE.replace("demo", "x${conf%/*}"))
        assert proc.returncode == 1
        assert "fails Flux's strict substitution" in proc.stdout
        assert "variable not set: conf" in proc.stdout

    def test_a_positional_reference_is_named_by_the_prescan(self, tree):
        """`${1}` is eaten silently in-cluster, and the scan names the line."""
        proc = run(tree, render=NAMESPACE.replace("demo", "x${1}"))
        assert proc.returncode == 1
        assert "envsubst mishandles" in proc.stdout
        assert "x${1}" in proc.stdout

    def test_an_unknown_key_fails_as_missing(self, tree):
        proc = run(tree, render=NAMESPACE.replace("demo", "x${nope}"))
        assert proc.returncode == 1
        assert "reference keys not in versions-configmap" in proc.stdout
        assert "- nope" in proc.stdout

    def test_an_escaped_reference_is_exempt_from_every_scan(self, tree):
        """A Grafana panel variable survives as `$${var}` and is not a key."""
        proc = run(tree, render=NAMESPACE.replace("demo", "x$${nope}"))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_a_blank_value_fails(self, tree):
        """envsubst renders `image: foo:${x}` as `foo:`, which yamllint and
        kubeconform both pass, so the broken reference surfaces in-cluster."""
        proc = run(tree, render=NAMESPACE.replace("demo", "x${blank_key}"))
        assert proc.returncode == 1
        assert "present but EMPTY" in proc.stdout
        assert "- blank_key" in proc.stdout


class TestSimpleMode:
    def _run(self, tree: Path, *extra: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": "%s:%s:/usr/bin:/bin" % (tree / "bin", Path(sys.executable).parent),
            "HOME": os.environ.get("HOME", str(tree)),
            "STUB_RENDERS": str(tree / "renders"),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), "--mode", "simple",
             "--kustomize-path", "kubernetes/apps", "--k8s-version", "1.36.0",
             "--crd-catalog-ref", "deadbeef", *extra],
            cwd=tree, env=env, capture_output=True, text=True,
        )

    def test_the_render_reaches_kubeconform(self, tree):
        """kubeconform reads the render on stdin: handed a bare temp path it
        parses nothing and still exits 0."""
        proc = self._run(tree)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "1 resource found" in proc.stdout
        assert "0 resource found" not in proc.stdout

    def test_a_render_with_no_document_fails(self, tree):
        _write(tree / "renders" / "kubernetes_apps.yaml", "# nothing\n")
        proc = self._run(tree)
        assert proc.returncode == 1
        assert "rendered no document" in proc.stdout


class TestNothingPassesOnNothing:
    def test_a_stage_that_renders_nothing_fails(self, tree):
        """CRITICAL in-cluster: kubeconform exits 0 on an empty stream, and the
        Kustomization's prune then deletes every object that stage applied."""
        proc = run(tree, render="")
        assert proc.returncode == 1
        assert "kubernetes/apps rendered no resources" in proc.stdout

    def test_a_stage_that_renders_only_comments_fails(self, tree):
        """kubeconform exits 0 on a stream with no resource in it."""
        proc = run(tree, render="# nothing here\n---\n# still nothing\n")
        assert proc.returncode == 1
        assert "kubernetes/apps rendered no resources" in proc.stdout

    def test_a_caller_owned_corpus_starts_empty(self, tree):
        """Manifests left by an earlier run must not satisfy this run's floor."""
        corpus = tree / "corpus.yaml"
        corpus.write_text("kind: Namespace\nmetadata:\n  name: stale\n")
        proc = run(tree, "--render-all", str(corpus), render="# nothing\n")
        assert proc.returncode == 1
        assert "rendered no resources" in proc.stdout
        assert "stale" not in corpus.read_text()

    def test_a_render_larger_than_a_pipe_buffer_passes(self, tree):
        """The floor reads the whole stream: an early exit on the first kinded
        document would SIGPIPE the writer and red a fine stage."""
        big = "---\n".join(
            NAMESPACE.replace("demo", f"demo-{i:05d}") for i in range(3000)
        )
        assert len(big) > 128 * 1024
        proc = run(tree, render=big)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_a_stage_path_outside_the_repository_fails(self, tree):
        """Flux reconciles inside the repository, so a path that leaves it is
        not this cluster's stage however well it builds."""
        outside = tree.parent / "elsewhere" / "apps"
        outside.mkdir(parents=True)
        _write(tree / "renders" / "kubernetes_apps.yaml", NAMESPACE)
        apps = tree / "kubernetes" / "clusters" / "demo" / "apps.yaml"
        apps.write_text(apps.read_text().replace("./kubernetes/apps", "../elsewhere/apps"))
        proc = run(tree)
        assert proc.returncode == 1
        assert "outside the repository" in proc.stdout

    def test_an_absolute_stage_path_fails(self, tree):
        apps = tree / "kubernetes" / "clusters" / "demo" / "apps.yaml"
        apps.write_text(apps.read_text().replace("./kubernetes/apps", str(tree / "kubernetes" / "apps")))
        proc = run(tree)
        assert proc.returncode == 1
        assert "absolute spec.path" in proc.stdout

    def test_a_cluster_dir_with_no_kustomization_fails_on_the_corpus(self, tree):
        """The per-stage floor cannot fire when no stage was iterated at all."""
        (tree / "kubernetes" / "clusters" / "demo" / "apps.yaml").unlink()
        proc = run(tree)
        assert proc.returncode == 1
        assert "rendered any manifest" in proc.stdout

    def test_a_failed_build_is_quoted_and_fails(self, tree):
        _write(tree / "kubernetes" / "clusters" / "demo" / "gone.yaml",
               FLUX_KS % {"name": "gone", "path": "kubernetes/gone", "extra": ""})
        proc = run(tree)
        assert proc.returncode == 1
        assert "kustomize build failed for kubernetes/gone" in proc.stdout
        assert "no such tree" in proc.stdout


class TestDocumentTriage:
    def test_another_kind_is_skipped_quietly(self, tree):
        proc = run(tree)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "SKIP: kubernetes/clusters/demo/repo.yaml is a " \
            "source.toolkit.fluxcd.io/v1 GitRepository" in proc.stdout

    def test_a_pathless_kustomization_fails_the_run(self, tree):
        """Flux reconciles the repository root when spec.path is absent, which
        this gate cannot build, so it is an error and not a skip."""
        _write(tree / "kubernetes" / "clusters" / "demo" / "rootish.yaml",
               "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"
               "metadata:\n  name: rootish\nspec:\n  prune: true\n")
        proc = run(tree)
        assert proc.returncode == 1
        assert "no spec.path" in proc.stdout

    def test_two_kustomizations_in_one_file_fail(self, tree):
        body = FLUX_KS % {"name": "a", "path": "kubernetes/apps", "extra": ""}
        _write(tree / "kubernetes" / "clusters" / "demo" / "pair.yaml",
               body + "---\n" + body.replace("name: a", "name: b"))
        proc = run(tree)
        assert proc.returncode == 1
        assert "failed to read" in proc.stdout

    def test_a_yml_suffix_is_iterated_too(self, tree):
        """Flux reconciles either suffix, so a one-suffix glob would leave a
        whole stage's tree ungated."""
        _write(tree / "kubernetes" / "clusters" / "demo" / "extra.yml",
               FLUX_KS % {"name": "extra", "path": "kubernetes/gone", "extra": ""})
        proc = run(tree)
        assert proc.returncode == 1
        assert "kustomize build failed for kubernetes/gone" in proc.stdout

    def test_the_child_enumerator_builds_the_stages_in_dependson_order(self, tree):
        _write(tree / "kubernetes" / "clusters" / "demo" / "apps.yaml",
               FLUX_KS % {"name": "apps", "path": "kubernetes/apps",
                          "extra": "  dependsOn:\n    - name: base\n"})
        _write(tree / "kubernetes" / "clusters" / "demo" / "base.yaml",
               FLUX_KS % {"name": "base", "path": "kubernetes/base", "extra": ""})
        _write(tree / "renders" / "kubernetes_base.yaml", NAMESPACE)
        proc = run(tree, "--child-kustomizations", str(CHILD_SCRIPT))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        order = [line.split()[4] for line in proc.stdout.splitlines()
                 if line.startswith("=== Building + validating ")]
        assert order == ["base", "apps"]


class TestClusterRoot:
    def test_a_cluster_dir_resolving_outside_the_repository_fails(self, tree, tmp_path):
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        shutil.copytree(tree / "kubernetes" / "clusters" / "demo", outside)
        link = tree / "kubernetes" / "clusters" / "linked"
        link.symlink_to(outside)
        proc = run(tree, "--cluster-dir", "kubernetes/clusters/linked")
        assert proc.returncode == 1
        assert "outside the repository" in proc.stdout

    def test_a_pre_bootstrap_tree_may_skip_the_root_build(self, tree):
        (tree / "kubernetes" / "clusters" / "demo" / "flux-system"
         / "gotk-components.yaml").unlink()
        proc = run(tree, "--require-cluster-root", "false")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Skipping cluster root" in proc.stdout

    def test_a_pre_bootstrap_tree_still_builds_the_root_by_default(self, tree):
        """A bootstrapped consumer that loses its flux-system content must fail
        rather than quietly validate less."""
        (tree / "kubernetes" / "clusters" / "demo" / "flux-system"
         / "gotk-components.yaml").unlink()
        proc = run(tree)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Building cluster root" in proc.stdout


class TestUnvalidatedKinds:
    SKIPPED_JSON = json.dumps(
        {"resources": [{"status": "statusSkipped", "kind": "Widget",
                        "version": "example.com/v1"}]})

    def test_a_skipped_kind_is_reported_but_tolerated_without_a_baseline(self, tree):
        proc = run(tree, env={"STUB_KC_JSON": self.SKIPPED_JSON})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "example.com/v1/Widget" in proc.stdout

    def test_a_baseline_turns_the_tracker_into_a_floor(self, tree):
        """An unreachable catalog degrades the run to core kinds, and the
        baseline is what makes that a failure instead of a green job."""
        baseline = _write(tree / "expected-skipped.txt", "example.com/v1/Gizmo\n")
        proc = run(tree, "--expected-skipped", str(baseline),
                   env={"STUB_KC_JSON": self.SKIPPED_JSON})
        assert proc.returncode == 1
        assert "example.com/v1/Widget" in proc.stdout


class TestFluxCliPin:
    def test_a_mismatched_cli_fails_the_run(self, tree):
        proc = run(tree, env={"STUB_FLUX_VERSION": "2.7.1"})
        assert proc.returncode == 1
        assert "flux CLI 2.7.1 != " in proc.stdout
        assert "flux_version 2.9.0" in proc.stdout

    def test_the_check_can_be_waived(self, tree):
        proc = run(tree, "--check-flux-version", "false",
                   env={"STUB_FLUX_VERSION": "2.7.1"})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "flux CLI" not in proc.stdout


class TestSchemaVersion:
    def test_it_is_derived_from_the_configmap_and_says_so(self, tree):
        proc = run(tree)
        assert "kubeconform -kubernetes-version: 1.37.0 " \
            "(from versions-configmap k3s_version)" in proc.stdout

    def test_an_explicit_version_wins(self, tree):
        proc = run(tree, "--k8s-version", "1.30.0")
        assert "kubeconform -kubernetes-version: 1.30.0\n" in proc.stdout

    def test_an_empty_version_flag_still_derives(self, tree):
        """The CI template passes its `k8s_version` input through unguarded."""
        proc = run(tree, "--k8s-version", "")
        assert "from versions-configmap k3s_version" in proc.stdout


class TestOperatorErrors:
    def _run(self, tree, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args], cwd=tree, capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin"})

    def test_a_missing_cluster_dir_is_an_operator_error(self, tree):
        proc = self._run(tree, "--crd-catalog-ref", "deadbeef")
        assert proc.returncode == 2
        assert "--cluster-dir is required" in proc.stderr

    def test_a_missing_catalog_is_an_operator_error(self, tree):
        proc = self._run(tree, "--cluster-dir", "kubernetes/clusters/demo")
        assert proc.returncode == 2
        assert "--crd-catalog" in proc.stderr

    def test_an_unknown_flag_prints_the_usage(self, tree):
        proc = self._run(tree, "--nope")
        assert proc.returncode == 2
        assert "Usage: flux-lint.sh" in proc.stderr


@pytest.mark.skipif(shutil.which("flux") is None, reason="no flux CLI installed")
@pytest.mark.parametrize(
    ("body", "rejected"),
    [("x${conf%/*}", True), ("x${app_version}", False), ("x$${conf%/*}", False)],
)
def test_the_real_flux_cli_agrees_with_the_stub(body: str, rejected: bool):
    """The stub decides every strict case above, so it has to be the real
    CLI's verdict and not a convenient approximation."""
    env = dict(os.environ, app_version="1.2.3")
    env.pop("conf", None)
    proc = subprocess.run(["flux", "envsubst", "--strict"],
                          input=NAMESPACE.replace("demo", body),
                          capture_output=True, text=True, env=env)
    assert (proc.returncode != 0) is rejected, proc.stdout + proc.stderr
