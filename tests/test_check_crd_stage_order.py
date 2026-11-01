"""Tests for scripts/check-crd-stage-order.py."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from script_loader import load_script

mod = load_script("check-crd-stage-order.py")
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "crd-sources.example.yaml"


def _stage(name: str, path: str, depends=(), wait=True, health=None) -> dict:
    spec = {"path": path, "wait": wait, "sourceRef": {"kind": "GitRepository", "name": "flux-system"}}
    if depends:
        spec["dependsOn"] = [{"name": d} for d in depends]
    if health:
        spec["healthChecks"] = health
    return {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": spec,
    }


def _release(name: str, namespace: str, chart: str, **spec) -> dict:
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"chart": {"spec": {"chart": chart}}, **spec},
    }


SECRET = {
    "apiVersion": "external-secrets.io/v1",
    "kind": "ExternalSecret",
    "metadata": {"name": "oauth", "namespace": "tailscale"},
}
CRD = {
    "apiVersion": "apiextensions.k8s.io/v1",
    "kind": "CustomResourceDefinition",
    "metadata": {"name": "widgets.example.io"},
    "spec": {"group": "example.io"},
}
WIDGET = {"apiVersion": "example.io/v1", "kind": "Widget", "metadata": {"name": "w", "namespace": "apps"}}


def _write(path: Path, *docs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump_all(docs))


def _tree(tmp_path: Path, controllers=(), configs=(), configs_wait_on_controllers=True,
          controllers_wait=True) -> Path:
    """sources <- controllers <- configs, with a kustomization.yaml in each."""
    cluster = tmp_path / "kubernetes" / "clusters" / "demo"
    deps = ("controllers",) if configs_wait_on_controllers else ()
    _write(cluster / "controllers.yaml", _stage("controllers", "./kubernetes/controllers",
                                                wait=controllers_wait))
    _write(cluster / "configs.yaml", _stage("configs", "./kubernetes/configs", deps))
    for stage, docs in (("controllers", controllers), ("configs", configs)):
        base = tmp_path / "kubernetes" / stage
        files = []
        for index, doc in enumerate(docs):
            _write(base / f"doc{index}.yaml", doc)
            files.append(f"doc{index}.yaml")
        _write(base / "kustomization.yaml", {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "resources": files,
        })
    return tmp_path


def _run(root: Path, *argv: str) -> int:
    return mod.main(["--repo-root", str(root), "--cluster-dir", "kubernetes/clusters/demo", *argv])


def test_a_cr_in_a_later_stage_passes(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    assert _run(root) == 0
    assert "1 custom resource(s)" in capsys.readouterr().out


def test_a_cr_beside_the_release_that_installs_its_crd_fails(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets"), SECRET])
    assert _run(root) == 1
    err = capsys.readouterr().err
    assert "tailscale/ExternalSecret/oauth" in err
    assert "eso/HelmRelease/eso" in err


def test_a_cr_in_an_unordered_sibling_stage_fails(tmp_path):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET], configs_wait_on_controllers=False)
    assert _run(root) == 1


def test_an_earlier_stage_that_does_not_wait_does_not_count(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET], controllers_wait=False)
    assert _run(root) == 1
    assert "not waited for" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("check", "code"),
    [
        ({"kind": "HelmRelease", "name": "eso", "namespace": "eso"}, 0),
        ({"kind": "HelmRelease", "name": "eso"}, 0),  # the release's own namespace
        ({"kind": "HelmRelease", "name": "other", "namespace": "eso"}, 1),
        ({"kind": "HelmRelease", "name": "eso", "namespace": "other"}, 1),
        ({"kind": "Kustomization", "name": "eso", "namespace": "eso"}, 1),
    ],
)
def test_only_a_health_check_on_the_release_counts_as_waiting(tmp_path, capsys, check, code):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET], controllers_wait=False)
    stage = _stage("controllers", "./kubernetes/controllers", wait=False, health=[check])
    _write(root / "kubernetes/clusters/demo/controllers.yaml", stage)
    assert _run(root) == code
    assert ("not waited for" in capsys.readouterr().err) == bool(code)


def test_a_crd_manifest_in_an_earlier_unwaited_stage_passes(tmp_path):
    """kustomize-controller applies the CRD before the stage turns Ready, wait or not."""
    root = _tree(tmp_path, controllers=[CRD], configs=[WIDGET], controllers_wait=False)
    assert _run(root) == 0


def test_a_crd_manifest_in_the_same_stage_passes(tmp_path):
    root = _tree(tmp_path, controllers=[CRD, WIDGET])
    assert _run(root) == 0


def test_a_crd_manifest_only_in_a_later_stage_fails(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[WIDGET], configs=[CRD])
    assert _run(root) == 1
    assert "apps/Widget/w (example.io)" in capsys.readouterr().err


@pytest.mark.parametrize("values", [
    {"installCRDs": False},
    {"crds": {"enabled": False}},
    {"crds": {"create": False}},
    {"crds": {"install": False}},
])
def test_values_that_turn_the_crds_off_remove_the_release_as_a_source(tmp_path, capsys, values):
    off = _release("eso", "eso", "external-secrets", values=values)
    root = _tree(tmp_path, controllers=[off, SECRET], configs=[CRD, WIDGET])
    assert _run(root, "--allow-unresolved") == 0
    assert "external-secrets.io: used by controllers" in capsys.readouterr().err


def test_install_crds_skip_removes_the_release_as_a_source(tmp_path):
    skip = _release("eso", "eso", "external-secrets", install={"crds": "Skip"})
    root = _tree(tmp_path, controllers=[skip, SECRET, CRD, WIDGET])
    assert _run(root, "--allow-unresolved") == 0


ISSUER = {
    "apiVersion": "cert-manager.io/v1",
    "kind": "ClusterIssuer",
    "metadata": {"name": "letsencrypt"},
}


def test_cert_manager_with_default_values_installs_no_crd(tmp_path, capsys):
    plain = _release("cert-manager", "cert-manager", "cert-manager")
    root = _tree(tmp_path, controllers=[plain, CRD], configs=[ISSUER, WIDGET])
    assert _run(root, "--allow-unresolved") == 0
    captured = capsys.readouterr()
    assert "cert-manager.io: used by configs" in captured.err
    assert "1 custom resource(s)" in captured.out
    assert _run(root) == 1


@pytest.mark.parametrize("values", [{"crds": {"enabled": True}}, {"installCRDs": True}])
def test_cert_manager_installs_its_crds_once_opted_in(tmp_path, capsys, values):
    opted = _release("cert-manager", "cert-manager", "cert-manager", values=values)
    root = _tree(tmp_path, controllers=[opted], configs=[ISSUER])
    assert _run(root) == 0
    assert "1 custom resource(s)" in capsys.readouterr().out
    beside = _tree(tmp_path / "beside", controllers=[opted, ISSUER])
    assert _run(beside) == 1


def test_a_mapped_chart_replaces_the_built_in_opt_in_rule(tmp_path):
    plain = _release("cert-manager", "cert-manager", "cert-manager")
    root = _tree(tmp_path, controllers=[plain], configs=[ISSUER])
    assert _run(root, "--crd-group", "cert-manager=cert-manager.io") == 0


def test_traefik_crds_with_default_values_serves_no_hub_group(tmp_path, capsys):
    crds = _release("traefik-crds", "traefik", "traefik-crds")
    hub = {"apiVersion": "hub.traefik.io/v1alpha1", "kind": "API",
           "metadata": {"name": "a", "namespace": "apps"}}
    root = _tree(tmp_path, controllers=[crds, CRD], configs=[hub, WIDGET])
    assert _run(root, "--allow-unresolved") == 0
    assert "hub.traefik.io: used by configs" in capsys.readouterr().err


def test_the_map_names_a_chart_the_heuristic_does_not_know(tmp_path):
    operator = _release("w", "apps", "widget-operator")
    ordered = _tree(tmp_path / "a", controllers=[operator], configs=[WIDGET])
    assert _run(ordered) == 1  # unmapped
    assert _run(ordered, "--allow-unresolved") == 2  # so nothing resolved
    assert _run(ordered, "--crd-group", "widget-operator=example.io") == 0
    beside = _tree(tmp_path / "b", controllers=[operator, WIDGET])
    assert _run(beside, "--crd-group", "widget-operator=example.io") == 1


def test_a_release_entry_in_the_map_is_authoritative(tmp_path):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets"), SECRET],
                 configs=[CRD, WIDGET])
    source = tmp_path / "crd-sources.yaml"
    source.write_text(yaml.safe_dump({"releases": {"eso/HelmRelease/eso": []}}))
    # The map says ESO installs nothing, so only the Widget/CRD pair is checked:
    # configs holds both, which passes.
    assert _run(root, "--crd-source", str(source), "--allow-unresolved") == 0


def test_an_unmapped_group_fails_unless_allowed(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET, WIDGET])
    assert _run(root) == 1
    captured = capsys.readouterr()
    assert "ERROR: 1 custom resource group(s) unchecked" in captured.err
    assert "example.io: used by configs" in captured.err
    assert "CRD stage order OK" not in captured.out
    assert _run(root, "--allow-unresolved") == 0
    captured = capsys.readouterr()
    assert "WARNING: 1 custom resource group(s) unchecked" in captured.err
    assert "CRD stage order OK" in captured.out
    source = tmp_path / "crd-sources.yaml"
    source.write_text(yaml.safe_dump({"external_groups": {"example.io": "a k3s add-on"}}))
    assert _run(root, "--crd-source", str(source)) == 0
    assert "unchecked" not in capsys.readouterr().err


def test_a_dot_directory_stage_path_resolves_by_prefix(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")])
    _write(root / ".flux/stage/doc0.yaml", SECRET)
    _write(root / "kubernetes/clusters/demo/configs.yaml",
           _stage("configs", "./.flux/stage", ("controllers",)))
    assert _run(root) == 0
    assert "1 custom resource(s)" in capsys.readouterr().out
    assert mod.stage_dir(root, "./.flux/stage") == (root / ".flux/stage").resolve()
    assert mod.stage_dir(root, "/kubernetes/configs") == (root / "kubernetes/configs").resolve()
    assert mod.stage_dir(root, ".") == root.resolve()


def test_an_exemption_passes_and_is_reported(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets"), SECRET])
    assert _run(root, "--exempt", "tailscale/ExternalSecret/oauth=moved next") == 0
    assert "exempt (moved next)" in capsys.readouterr().out


def test_a_stage_corpus_replaces_the_walk(tmp_path):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    rendered = tmp_path / "controllers.rendered.yaml"
    _write(rendered, _release("eso", "eso", "external-secrets"), SECRET)
    assert _run(root, "--stage-corpus", f"controllers={rendered}") == 1


def test_a_nested_stage_under_a_plain_directory_is_not_read_twice(tmp_path):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")])
    plain = root / "kubernetes/plain"
    _write(plain / "a.yaml", WIDGET)
    _write(plain / "late/secret.yaml", SECRET)
    _write(root / "kubernetes/clusters/demo/plain.yaml", _stage("plain", "./kubernetes/plain"))
    _write(root / "kubernetes/clusters/demo/late.yaml",
           _stage("late", "./kubernetes/plain/late", ("controllers",)))
    _write(root / "kubernetes/clusters/demo/widgets.yaml",
           _stage("widgets", "./kubernetes/widgets"))
    _write(root / "kubernetes/widgets/crd.yaml", CRD)
    # The SECRET sits under plain/ but only the ordered late stage applies it.
    assert _run(root, "--exempt", "apps/Widget/w=unordered on purpose") == 0


def _kustomization(directory: Path, resources=(), components=()) -> None:
    body = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "resources": list(resources)}
    if components:
        body["components"] = list(components)
    _write(directory / "kustomization.yaml", body)


def test_an_installer_two_stages_back_counts_through_dependson(tmp_path, capsys):
    """sources <- controllers <- configs: configs waits on sources only transitively."""
    root = _tree(tmp_path, configs=[SECRET])
    cluster = root / "kubernetes/clusters/demo"
    _write(cluster / "sources.yaml", _stage("sources", "./kubernetes/sources"))
    _write(cluster / "controllers.yaml",
           _stage("controllers", "./kubernetes/controllers", ("sources",)))
    _write(root / "kubernetes/sources/eso.yaml", _release("eso", "eso", "external-secrets"))
    _kustomization(root / "kubernetes/sources", ["eso.yaml"])
    assert _run(root) == 0
    assert "1 custom resource(s) across 3 stage(s)" in capsys.readouterr().out


def test_a_chartref_release_installs_its_source_chart_groups(tmp_path, capsys):
    release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "eso", "namespace": "eso"},
        "spec": {"chartRef": {"kind": "OCIRepository", "name": "external-secrets"}},
    }
    root = _tree(tmp_path, controllers=[release, SECRET])
    assert _run(root) == 1
    assert "tailscale/ExternalSecret/oauth" in capsys.readouterr().err


def test_a_cr_added_by_a_component_is_checked(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")])
    base = root / "kubernetes/controllers"
    _write(base / "comp/secret.yaml", SECRET)
    _write(base / "comp/kustomization.yaml", {
        "apiVersion": "kustomize.config.k8s.io/v1alpha1", "kind": "Component",
        "resources": ["secret.yaml"],
    })
    _kustomization(base, ["doc0.yaml"], ["comp"])
    assert _run(root) == 1
    assert "tailscale/ExternalSecret/oauth" in capsys.readouterr().err


def test_a_remote_resource_is_skipped_beside_a_local_file(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    _kustomization(root / "kubernetes/configs",
                   ["https://github.com/x/y//base", "github.com/x/y//base", "doc0.yaml"])
    assert _run(root) == 0
    assert "1 custom resource(s)" in capsys.readouterr().out


def test_a_duplicate_stage_name_is_an_operator_error(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    _write(root / "kubernetes/clusters/demo/twin.yaml",
           _stage("configs", "./kubernetes/configs"))
    assert _run(root) == 2
    assert "duplicate name 'configs'" in capsys.readouterr().err


def test_a_kustomize_config_kustomization_in_the_cluster_dir_is_not_a_stage(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    _kustomization(root / "kubernetes/clusters/demo", ["controllers.yaml", "configs.yaml"])
    assert _run(root) == 0
    assert "1 custom resource(s) across 2 stage(s)" in capsys.readouterr().out


def test_a_dependson_naming_no_stage_in_the_dir_is_ignored(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    _write(root / "kubernetes/clusters/demo/configs.yaml",
           _stage("configs", "./kubernetes/configs", ("controllers", "flux-system")))
    assert _run(root) == 0
    assert "1 custom resource(s) across 2 stage(s)" in capsys.readouterr().out


def test_two_stages_on_one_plain_path_both_read_its_documents(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")])
    _write(root / "kubernetes/plain/secret.yaml", SECRET)
    cluster = root / "kubernetes/clusters/demo"
    _write(cluster / "configs.yaml", _stage("configs", "./kubernetes/plain", ("controllers",)))
    _write(cluster / "twin.yaml", _stage("twin", "./kubernetes/plain", ("controllers",)))
    assert _run(root) == 0
    assert "2 custom resource(s) across 3 stage(s)" in capsys.readouterr().out


def test_bootstrap_crds_in_the_cluster_dir_precede_every_stage(tmp_path):
    root = _tree(tmp_path, controllers=[WIDGET])
    _write(root / "kubernetes/clusters/demo/flux-system/gotk-components.yaml", CRD)
    assert _run(root) == 2  # the Widget is bootstrap-installed, so nothing is checked
    root = _tree(tmp_path, controllers=[WIDGET, _release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    assert _run(root) == 0


@pytest.mark.parametrize(
    ("spec", "group"),
    [({"group": "example.io"}, "example.io"), ({}, ""), (None, ""), ("malformed", "")],
)
def test_a_crd_group_reads_spec_group_or_nothing(spec, group):
    doc = {"kind": "CustomResourceDefinition"}
    if spec is not None:
        doc["spec"] = spec
    assert mod._crd_group(doc) == group


def test_a_dependency_cycle_is_an_operator_error(tmp_path, capsys):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    _write(root / "kubernetes/clusters/demo/controllers.yaml",
           _stage("controllers", "./kubernetes/controllers", ("configs",)))
    assert _run(root) == 2
    assert "cycle" in capsys.readouterr().err


def test_a_missing_stage_path_is_an_operator_error(tmp_path):
    root = _tree(tmp_path, configs=[SECRET])
    _write(root / "kubernetes/clusters/demo/ghost.yaml", _stage("ghost", "./kubernetes/ghost"))
    assert _run(root) == 2


def test_a_missing_cluster_dir_is_an_operator_error(tmp_path):
    assert _run(tmp_path) == 2


def test_nothing_resolved_is_an_operator_error(tmp_path, capsys):
    root = _tree(tmp_path, configs=[WIDGET])
    assert _run(root, "--allow-unresolved") == 2
    assert "checked nothing" in capsys.readouterr().err


def test_a_malformed_crd_source_is_an_operator_error(tmp_path):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    bad = tmp_path / "bad.yaml"
    for body in ({"charts": {"x": "not-a-list"}}, {"chart": {}},
                 {"releases": {"eso": []}}, {"external_groups": {"a.io": ""}}):
        bad.write_text(yaml.safe_dump(body))
        assert _run(root, "--crd-source", str(bad)) == 2
    for flag in ("no-equals", "chart=", "=example.io", " = "):
        assert _run(root, "--crd-group", flag) == 2
    assert _run(root, "--stage-corpus", "nosuchstage=x.yaml") == 2


@pytest.mark.parametrize("body", ["", "charts: {}\n---\ncharts: {}\n"])
def test_a_crd_source_that_is_not_one_document_is_an_operator_error(tmp_path, capsys, body):
    root = _tree(tmp_path, controllers=[_release("eso", "eso", "external-secrets")],
                 configs=[SECRET])
    source = tmp_path / "crd-sources.yaml"
    source.write_text(body)
    assert _run(root, "--crd-source", str(source)) == 2
    assert "expected one mapping document" in capsys.readouterr().err


def test_a_repeated_crd_group_flag_is_trimmed_and_recorded_once(tmp_path):
    flags = [" widget-operator = example.io ", "widget-operator=example.io"]
    assert mod.load_crd_sources(None, flags).charts["widget-operator"] == ["example.io"]
    root = _tree(tmp_path, controllers=[_release("w", "apps", "widget-operator")],
                 configs=[WIDGET])
    assert _run(root, *(arg for flag in flags for arg in ("--crd-group", flag))) == 0


def test_the_example_crd_source_loads(tmp_path):
    sources = mod.load_crd_sources(str(EXAMPLE), [])
    assert sources.charts["widget-operator"] == ["widgets.example.io"]
    assert "example/HelmRelease/split-crds" in sources.releases
    assert "helm.cattle.io" in sources.external
