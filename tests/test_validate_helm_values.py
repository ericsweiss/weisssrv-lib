#!/usr/bin/env python3
"""scripts/validate-helm-values.py resolves placeholders and picks the right
HelmRelease document, tested without the network-bound `helm` path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from script_loader import SCRIPTS, load_path, load_script

_SRC = SCRIPTS / "validate-helm-values.py"
vhv = load_script("validate-helm-values.py")


# --- substitute() ----------------------------------------------------------

@pytest.mark.parametrize("text,versions,expected,missing", [
    ("tag: ${foo}", {"foo": "1.2.3"}, "tag: 1.2.3", []),
    ("tag: ${bar}", {"foo": "1.2.3"}, "tag: ${bar}", ["bar"]),
    ("${a}-${b}", {"a": "x"}, "x-${b}", ["b"]),
    ("${z} ${a} ${z}", {}, "${z} ${a} ${z}", ["a", "z"]),
    ("no placeholders here", {"foo": "1"}, "no placeholders here", []),
], ids=["known", "unknown-left-literal", "mixed", "missing-deduped-sorted", "noop"])
def test_substitute(text, versions, expected, missing):
    assert vhv.substitute(text, versions) == (expected, missing)


# --- load_versions() -------------------------------------------------------

class TestLoadVersions:
    def _write_cm(self, tmp_path: Path, body: str) -> Path:
        # load_versions() resolves the ConfigMap path relative to repo_root.
        cm = tmp_path / vhv.DEFAULT_VERSIONS_CONFIGMAP
        cm.parent.mkdir(parents=True, exist_ok=True)
        cm.write_text(body)
        return tmp_path

    def test_returns_stringified_data(self, tmp_path):
        root = self._write_cm(
            tmp_path,
            "apiVersion: v1\nkind: ConfigMap\ndata:\n  foo: 1.2.3\n  num: 7\n",
        )
        versions = vhv.load_versions(str(root))
        assert versions == {"foo": "1.2.3", "num": "7"}

    @pytest.mark.parametrize("body", [
        "apiVersion: v1\nkind: ConfigMap\ndata: {}\n",
        "apiVersion: v1\nkind: ConfigMap\n",
        "- not\n- a\n- mapping\n",
    ], ids=["empty-data", "absent-data", "non-mapping"])
    def test_raises_on_unusable_data(self, tmp_path, body):
        with pytest.raises(SystemExit):
            vhv.load_versions(str(self._write_cm(tmp_path, body)))


@pytest.mark.parametrize("versions,expected", [
    ({"k3s_version": "v1.36.2+k3s1"}, "1.36.2"),
    ({"k3s_version": "1.36.2"}, "1.36.2"),
], ids=["v-prefix-k3s-suffix", "plain-semver"])
def test_derive_kube_version(versions, expected):
    assert vhv.derive_kube_version(versions) == expected


@pytest.mark.parametrize("versions", [{}, {"k3s_version": "garbage"}], ids=["absent", "unparseable"])
def test_an_unusable_k3s_version_is_an_error(versions):
    with pytest.raises(SystemExit, match="k3s_version"):
        vhv.derive_kube_version(versions)


@pytest.mark.parametrize(
    ("spec", "meta", "expected"),
    [
        ({"releaseName": "fixed", "targetNamespace": "ns"}, {"name": "app"}, "fixed"),
        ({"targetNamespace": "ns"}, {"name": "app"}, "ns-app"),
        ({}, {"name": "app"}, "app"),
        ({}, {}, "registry-name"),
        ({"targetNamespace": "n" * 30}, {"name": "a" * 30},
         "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnn-aaaaaaaaa-d565fe94a777"),
    ],
    ids=["release-name-wins", "target-namespace-prefix", "plain-name", "registry-fallback",
         "shortened-past-53"],
)
def test_the_release_name_is_the_one_flux_installs(spec, meta, expected):
    name = vhv.flux_release_name(spec, meta, "registry-name")
    assert name == expected and len(name) <= 53


# --- extract_helmrelease_from_text() ---------------------------------------

class TestExtractHelmRelease:
    def test_returns_first_helmrelease(self, tmp_path):
        manifest = tmp_path / "release.yaml"
        manifest.write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cm\n"
            "---\n"
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            "metadata:\n  name: first\n"
            "---\n"
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            "metadata:\n  name: second\n"
        )
        hr = vhv.extract_helmrelease_from_text(manifest.read_text(), str(manifest))
        assert hr["metadata"]["name"] == "first"

    def test_raises_when_none_present(self, tmp_path):
        manifest = tmp_path / "release.yaml"
        manifest.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cm\n")
        with pytest.raises(SystemExit):
            vhv.extract_helmrelease_from_text(manifest.read_text(), str(manifest))

    def test_unparseable_yaml_names_the_source(self, tmp_path):
        """The substituted text has no mark, so only `source` identifies it."""
        manifest = tmp_path / "release.yaml"
        manifest.write_text("kind: HelmRelease\nspec: [unclosed\n")
        with pytest.raises(SystemExit) as excinfo:
            vhv.extract_helmrelease_from_text(manifest.read_text(), str(manifest))
        assert str(manifest) in str(excinfo.value)
        assert "not parseable YAML" in str(excinfo.value)

    def test_skips_non_dict_docs(self, tmp_path):
        # A non-dict doc (e.g. a list) must be skipped, not raise AttributeError.
        manifest = tmp_path / "release.yaml"
        manifest.write_text(
            "- a\n- list\n"
            "---\n"
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            "metadata:\n  name: only\n"
        )
        hr = vhv.extract_helmrelease_from_text(manifest.read_text(), str(manifest))
        assert hr["metadata"]["name"] == "only"

    def test_text_substitution_preserves_quoted_string_type(self):
        # Substituting the raw text (like Flux) must keep a quoted placeholder a
        # string even when the value is numeric — not re-parse it as a number.
        text = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
            'spec:\n  values:\n    tag: "${v}"\n'
        )
        rendered, missing = vhv.substitute(text, {"v": "1.36"})
        assert missing == []
        hr = vhv.extract_helmrelease_from_text(rendered, "inline")
        assert hr["spec"]["values"]["tag"] == "1.36"
        assert isinstance(hr["spec"]["values"]["tag"], str)


class TestLoadReleases:
    """The release list is consumer data; a malformed one must fail loudly rather
    than silently validating nothing."""

    def _write(self, tmp_path: Path, body: str) -> str:
        p = tmp_path / "releases.yaml"
        p.write_text(body)
        return str(p)

    def test_plain_list_accepted(self, tmp_path):
        path = self._write(
            tmp_path,
            "- name: traefik\n  manifest: k/release.yaml\n  chart: traefik\n"
            "  repo_name: traefik\n  repo_url: https://example.invalid/charts\n",
        )
        assert vhv.load_releases(path)[0]["chart"] == "traefik"

    def test_releases_key_mapping_accepted(self, tmp_path):
        path = self._write(
            tmp_path,
            "releases:\n  - name: traefik\n    manifest: k/release.yaml\n"
            "    chart: traefik\n    repo_name: traefik\n"
            "    repo_url: https://example.invalid/charts\n",
        )
        assert len(vhv.load_releases(path)) == 1

    def test_empty_list_raises(self, tmp_path):
        with pytest.raises(SystemExit):
            vhv.load_releases(self._write(tmp_path, "[]\n"))

    def test_missing_required_key_raises(self, tmp_path):
        path = self._write(tmp_path, "- name: traefik\n  chart: traefik\n")
        with pytest.raises(SystemExit):
            vhv.load_releases(path)


# --- refusals that keep the gate honest about what it rendered ---------------


def _release(tmp_path: Path, body: str) -> dict:
    manifest = tmp_path / "release.yaml"
    manifest.write_text(body)
    return {
        "name": "app", "manifest": "release.yaml", "chart": "app",
        "repo_name": "app", "repo_url": "https://example.invalid/charts",
    }


HR_BODY = """apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: app, namespace: ns}
spec:
  chart: {spec: {version: 1.0.0}}
  values: {}
"""


class TestSubstitutionFormsItCannotEvaluate:
    """Flux resolves ${var:=default}; this gate does not, so it must refuse
    rather than render an object Flux would render differently."""

    @pytest.mark.parametrize(
        "text", ["tag: ${foo:=bar}", "tag: ${foo:-bar}", "tag: ${foo:1}", "tag: ${foo/a/b}"]
    )
    def test_detected(self, text):
        assert vhv.unsupported_substitution(text)

    def test_plain_placeholder_is_supported(self):
        assert vhv.unsupported_substitution("tag: ${foo}") is None

    def test_validate_release_refuses(self, tmp_path, capsys):
        rel = _release(tmp_path, HR_BODY.replace("version: 1.0.0", 'version: "${v:=1}"'))
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is False
        assert "cannot evaluate" in capsys.readouterr().out


class TestValuesFrom:
    def test_validate_release_refuses(self, tmp_path, capsys):
        body = HR_BODY.replace(
            "  values: {}\n",
            "  valuesFrom:\n    - kind: ConfigMap\n      name: extra\n  values: {}\n",
        )
        rel = _release(tmp_path, body)
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is False
        assert "valuesFrom is not rendered by this gate" in capsys.readouterr().out


class TestSubprocessTimeouts:
    """A stalled chart repo must fail with a message, not hang the CI job."""

    def test_run_tool_reports_a_timeout(self, monkeypatch, capsys):
        def boom(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=["helm", "template"], timeout=1)

        monkeypatch.setattr(vhv.subprocess, "run", boom)
        assert vhv._run_tool(["helm", "template", "x"], 1, "app") is None
        assert "timed out after 1s" in capsys.readouterr().out

    def test_validate_release_fails_on_a_render_timeout(self, tmp_path, monkeypatch, capsys):
        def boom(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=["helm", "template"], timeout=1)

        monkeypatch.setattr(vhv.subprocess, "run", boom)
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is False
        assert "timed out" in capsys.readouterr().out


class TestRenderedVpaCapPolicy:
    """The cap rule's chart-rendered blind spot: the VPA sits in the repo, its
    target is rendered by the chart, so only this gate can compare them."""

    VPA = """apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: app}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: app}
  updatePolicy: {updateMode: Auto}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        maxAllowed: {memory: 512Mi}
"""

    def test_sibling_vpas_are_picked_up_and_namespaced(self, tmp_path):
        (tmp_path / "release.yaml").write_text(HR_BODY)
        (tmp_path / "vpa.yaml").write_text(self.VPA)
        found = vhv.release_vpas(str(tmp_path / "release.yaml"), "ns", {})
        assert len(found) == 1
        assert found[0]["metadata"]["namespace"] == "ns"

    def test_an_unreadable_sibling_is_reported_not_skipped(self, tmp_path):
        (tmp_path / "release.yaml").write_text(HR_BODY)
        (tmp_path / "vpa.yaml").write_text("a: [unclosed\n")
        with pytest.raises(vhv.UnreadableSibling, match="vpa.yaml"):
            vhv.release_vpas(str(tmp_path / "release.yaml"), "ns", {})


class TestValidateReleaseSuccessPath:
    """The post-render policy arms, driven through validate_release itself."""

    RENDERED = """apiVersion: apps/v1
kind: Deployment
metadata: {name: app}
spec:
  template:
    spec:
      containers:
        - name: c
          resources:
            limits: {%s}
"""

    def _render(self, monkeypatch, limits: str) -> None:
        def fake(*_args, **_kwargs):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=self.RENDERED % limits, stderr=""
            )

        monkeypatch.setattr(vhv, "_run_tool", fake)

    def test_an_all_clean_render_passes(self, tmp_path, monkeypatch):
        self._render(monkeypatch, "memory: 128Mi")
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is True

    def test_a_chart_rendered_cpu_limit_fails(self, tmp_path, monkeypatch, capsys):
        self._render(monkeypatch, "cpu: 500m")
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is False
        assert "chart-rendered pods set a CPU limit" in capsys.readouterr().out

    def test_the_allowlist_key_names_the_target_namespace(self, tmp_path, monkeypatch):
        """The rendered doc carries no namespace, so only the stamping makes the
        `ns/Deployment/app` allowlist key match."""
        self._render(monkeypatch, "cpu: 500m")
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(
            rel, {}, str(tmp_path), False, "1.30.0",
            cpu_limit_allowlist={"ns/Deployment/app"},
        ) is True

    def test_a_vpa_capping_at_the_rendered_limit_fails(self, tmp_path, monkeypatch, capsys):
        self._render(monkeypatch, "memory: 512Mi")
        (tmp_path / "vpa.yaml").write_text(TestRenderedVpaCapPolicy.VPA)
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is False
        assert "caps memory at or above" in capsys.readouterr().out

    def test_an_unreadable_sibling_fails_the_release(self, tmp_path, monkeypatch, capsys):
        self._render(monkeypatch, "memory: 128Mi")
        (tmp_path / "vpa.yaml").write_bytes(b"kind: \xff\xfe\n")
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is False
        assert "beside the release is unreadable" in capsys.readouterr().out

    def test_a_vpa_capping_below_the_rendered_limit_passes(self, tmp_path, monkeypatch):
        self._render(monkeypatch, "memory: 1Gi")
        (tmp_path / "vpa.yaml").write_text(TestRenderedVpaCapPolicy.VPA)
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(rel, {}, str(tmp_path), False, "1.30.0") is True


class TestKubeconform:
    """--kubeconform is what the shipped example gate config passes."""

    def _capture(self, monkeypatch, kubeconform_rc: int = 0) -> list:
        seen: list = []

        def fake(cmd, *_args, **_kwargs):
            seen.append(cmd)
            if cmd and cmd[0] == "kubeconform":
                return subprocess.CompletedProcess(
                    args=cmd, returncode=kubeconform_rc, stdout="Summary", stderr="bad"
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=TestValidateReleaseSuccessPath.RENDERED % "memory: 128Mi",
                stderr="",
            )

        monkeypatch.setattr(vhv, "_run_tool", fake)
        return seen

    def test_the_kubeconform_argv_carries_the_version_and_catalog_ref(
        self, tmp_path, monkeypatch
    ):
        seen = self._capture(monkeypatch)
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(
            rel, {}, str(tmp_path), True, "1.30.0", crd_catalog_ref="abc123"
        ) is True
        argv = next(cmd for cmd in seen if cmd[0] == "kubeconform")
        assert argv[argv.index("-kubernetes-version") + 1] == "1.30.0"
        assert any("abc123" in part for part in argv)
        assert "{{.Group}}/{{.ResourceKind}}" in " ".join(argv)

    def test_a_failing_kubeconform_fails_the_release(self, tmp_path, monkeypatch, capsys):
        self._capture(monkeypatch, kubeconform_rc=1)
        rel = _release(tmp_path, HR_BODY)
        assert vhv.validate_release(rel, {}, str(tmp_path), True, "1.30.0") is False
        assert "kubeconform failed" in capsys.readouterr().out


VERSIONS_CM = """apiVersion: v1
kind: ConfigMap
metadata: {name: cluster-versions}
data:
  k3s_version: v1.30.0+k3s1
"""

MAIN_HR = HR_BODY.replace(
    "  chart: {spec: {version: 1.0.0}}\n",
    "  chart:\n    spec:\n      version: 1.0.0\n"
    "      sourceRef: {kind: HelmRepository, name: app}\n",
)

REPO_YAML = """apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata: {name: app}
spec: {url: "https://example.invalid/charts"}
"""


class TestMain:
    """main() is the half of the script no other test drives."""

    def _repo(self, tmp_path: Path, releases: str | None = None) -> Path:
        (tmp_path / "release.yaml").write_text(MAIN_HR)
        (tmp_path / "kubernetes" / "infrastructure" / "sources").mkdir(parents=True)
        (tmp_path / "kubernetes" / "infrastructure" / "sources" / "repo.yaml").write_text(
            REPO_YAML
        )
        (tmp_path / "kubernetes" / "infrastructure" / "sources"
         / "versions-configmap.yaml").write_text(VERSIONS_CM)
        (tmp_path / "helm-values-releases.yaml").write_text(
            releases if releases is not None
            else "releases:\n  - {name: app, manifest: release.yaml, chart: app}\n"
        )
        return tmp_path

    def _stub_helm(self, tmp_path: Path, monkeypatch, repo_add_rc: int = 0) -> None:
        monkeypatch.setattr(vhv.shutil, "which", lambda name: "/usr/bin/%s" % name)

        def fake(cmd, *_args, **_kwargs):
            rc = repo_add_rc if cmd[:3] == ["helm", "repo", "add"] else 0
            return subprocess.CompletedProcess(
                args=cmd, returncode=rc,
                stdout=TestValidateReleaseSuccessPath.RENDERED % "memory: 128Mi",
                stderr="repo add refused",
            )

        monkeypatch.setattr(vhv, "_run_tool", fake)

    def _argv(self, repo: Path) -> list[str]:
        return ["--repo-root", str(repo)]

    def test_a_clean_run_passes(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch)
        assert vhv.main(self._argv(repo)) == 0
        assert "All 1 HelmRelease(s) validated" in capsys.readouterr().out

    def test_a_missing_helm_is_reported(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        monkeypatch.setattr(vhv.shutil, "which", lambda name: None)
        assert vhv.main(self._argv(repo)) == 1
        assert "helm not found" in capsys.readouterr().out

    def test_an_unparseable_helm_repository_names_the_path(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch)
        (repo / "kubernetes" / "infrastructure" / "sources" / "bad.yaml").write_text(
            "a: [1\n"
        )
        assert vhv.main(self._argv(repo)) == 1
        assert "bad.yaml could not be read as YAML" in capsys.readouterr().out

    def test_an_unresolvable_chart_repo_is_reported(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch)
        (repo / "kubernetes" / "infrastructure" / "sources" / "repo.yaml").unlink()
        assert vhv.main(self._argv(repo)) == 1
        assert "cannot resolve the chart repo" in capsys.readouterr().out

    def test_a_failing_repo_add_is_reported(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch, repo_add_rc=1)
        assert vhv.main(self._argv(repo)) == 1
        assert "failed to add/update Helm repo" in capsys.readouterr().out

    def test_kubeconform_without_a_catalog_ref_exits_2(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch)
        monkeypatch.delenv("CRD_CATALOG_REF", raising=False)
        assert vhv.main([*self._argv(repo), "--kubeconform"]) == 2
        assert "CRD_CATALOG_REF" in capsys.readouterr().out

    def test_the_catalog_ref_reaches_kubeconform(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch)
        monkeypatch.setenv("CRD_CATALOG_REF", "abc123")
        seen = []
        monkeypatch.setattr(vhv, "validate_release", lambda *a, **k: seen.append(a) or True)
        assert vhv.main([*self._argv(repo), "--kubeconform"]) == 0
        assert seen[0][-1] == "abc123"

    def test_the_repo_update_names_only_the_release_repos(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch)
        stub, seen = vhv._run_tool, []
        monkeypatch.setattr(vhv, "_run_tool", lambda cmd, *a, **k: seen.append(cmd) or stub(cmd, *a, **k))
        assert vhv.main(self._argv(repo)) == 0
        assert ["helm", "repo", "update", "app"] in seen

    def test_one_failing_release_is_counted(self, tmp_path, monkeypatch, capsys):
        repo = self._repo(tmp_path)
        self._stub_helm(repo, monkeypatch)
        monkeypatch.setattr(vhv, "validate_release", lambda *a, **k: False)
        assert vhv.main(self._argv(repo)) == 1
        assert "1 release(s) failed helm-values validation" in capsys.readouterr().out


def test_a_missing_sibling_script_fails_loudly(tmp_path):
    """Both scripts are offered separately, so vendoring only this one must say
    so rather than raise FileNotFoundError out of the loader."""
    lonely = tmp_path / "validate-helm-values.py"
    shutil.copy(_SRC, lonely)
    with pytest.raises(SystemExit) as excinfo:
        load_path(lonely)
    assert excinfo.value.code == 2


# --- chart repo resolution -------------------------------------------------

HELMREPO = """\
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: bitnami
spec:
  url: https://charts.bitnami.com/bitnami
"""

MANIFEST = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: redis
spec:
  chart:
    spec:
      chart: redis
      version: "1.2.3"
      sourceRef:
        kind: HelmRepository
        name: bitnami
"""


def _sources(tmp_path: Path) -> Path:
    d = tmp_path / "sources"
    d.mkdir()
    (d / "helmrepositories.yaml").write_text(HELMREPO)
    (d / "notes.txt").write_text("not yaml")
    return d


class TestResolveChartRepo:
    def test_reads_the_url_from_the_sources_dir(self, tmp_path):
        repos = vhv.helm_repositories(str(_sources(tmp_path)))
        assert repos == {"bitnami": "https://charts.bitnami.com/bitnami"}
        assert vhv.resolve_chart_repo(
            {"name": "redis", "manifest": "m.yaml"}, MANIFEST, repos
        ) == ("bitnami", "https://charts.bitnami.com/bitnami")

    def test_a_per_release_override_wins(self, tmp_path):
        repos = vhv.helm_repositories(str(_sources(tmp_path)))
        rel = {
            "name": "redis", "manifest": "m.yaml",
            "repo_name": "mirror", "repo_url": "https://mirror.example/charts",
        }
        assert vhv.resolve_chart_repo(rel, MANIFEST, repos) == (
            "mirror", "https://mirror.example/charts"
        )

    def test_an_unknown_source_ref_fails_loudly(self, tmp_path):
        repos = vhv.helm_repositories(str(_sources(tmp_path)))
        manifest = MANIFEST.replace("name: bitnami", "name: absent")
        result = vhv.resolve_chart_repo(
            {"name": "redis", "manifest": "m.yaml"}, manifest, repos
        )
        assert isinstance(result, str) and "absent" in result

    def test_a_manifest_without_a_source_ref_fails_loudly(self, tmp_path):
        repos = vhv.helm_repositories(str(_sources(tmp_path)))
        manifest = MANIFEST[: MANIFEST.index("      sourceRef:")]
        result = vhv.resolve_chart_repo(
            {"name": "redis", "manifest": "m.yaml"}, manifest, repos
        )
        assert isinstance(result, str) and "sourceRef" in result

    def test_a_missing_sources_dir_is_an_empty_map(self, tmp_path):
        assert vhv.helm_repositories(str(tmp_path / "absent")) == {}

    def test_an_unparseable_source_is_collected_not_skipped(self, tmp_path):
        """Flux rejects the file too, so a silent skip misattributes the failure."""
        sources = _sources(tmp_path)
        (sources / "broken.yaml").write_text("kind: HelmRepository\n\tbad: indent\n")
        unreadable: list = []
        repos = vhv.helm_repositories(str(sources), unreadable)
        assert repos == {"bitnami": "https://charts.bitnami.com/bitnami"}
        assert [path for path, _exc in unreadable] == [str(sources / "broken.yaml")]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
