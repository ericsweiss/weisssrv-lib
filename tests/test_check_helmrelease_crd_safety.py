"""scripts/check-helmrelease-crd-safety.py keeps a chart's CRDs across an uninstall."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from script_loader import REPO, load_script

gate = load_script("check-helmrelease-crd-safety.py")
EXAMPLE = REPO / "examples" / "helmrelease-crd-safety.example.yaml"


def _release(name, namespace="flux-system", **spec):
    body = {"chart": {"spec": {"chart": name}}}
    body.update(spec)
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": name, "namespace": namespace},
        "spec": body,
    }


KEEP_VALUES = {
    "values": {"crds": {"annotations": {"helm.sh/resource-policy": "keep"}}},
    "install": {"strategy": {"name": "RetryOnFailure"}},
}
POST_RENDERER = {
    "postRenderers": [
        {
            "kustomize": {
                "patches": [
                    {
                        "target": {"kind": "CustomResourceDefinition"},
                        "patch": (
                            "apiVersion: apiextensions.k8s.io/v1\n"
                            "kind: CustomResourceDefinition\n"
                            "metadata:\n  name: ignored\n  annotations:\n"
                            "    helm.sh/resource-policy: keep\n"
                        ),
                    }
                ]
            }
        }
    ],
    "install": {"strategy": {"name": "RetryOnFailure"}},
}


def _policy(tmp_path: Path, **sections) -> str:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(sections), encoding="utf-8")
    return str(path)


def test_a_declared_keeper_with_both_flags_passes(tmp_path):
    docs = [_release("eso", **KEEP_VALUES)]
    policy = gate.load_policy(_policy(tmp_path, crd_keepers={"flux-system/HelmRelease/eso": "values"}))
    assert gate.violations(docs, policy) == []


def test_a_keeper_that_lost_the_keep_annotation_fails(tmp_path):
    """Mutation case: the chart-version change that deletes every CR."""
    docs = [_release("eso", install={"strategy": {"name": "RetryOnFailure"}})]
    policy = gate.load_policy(_policy(tmp_path, crd_keepers={"flux-system/HelmRelease/eso": "values"}))
    found = gate.violations(docs, policy)
    assert any("resource-policy" in line for line in found)


def test_a_keeper_that_remediates_by_uninstalling_fails(tmp_path):
    docs = [_release("eso", values=KEEP_VALUES["values"])]
    policy = gate.load_policy(_policy(tmp_path, crd_keepers={"flux-system/HelmRelease/eso": "values"}))
    found = gate.violations(docs, policy)
    assert any("RetryOnFailure" in line for line in found)


def test_a_bare_string_install_strategy_is_not_read_as_configured(tmp_path):
    """Kubernetes prunes a shape the CRD does not define, so it counts as absent."""
    assert gate.install_strategy({"spec": {"install": {"strategy": "RetryOnFailure"}}}) is None
    assert gate.install_strategy({"spec": {"install": {"strategy": {"name": "X"}}}}) == "X"


def test_the_post_renderer_shape_is_accepted(tmp_path):
    docs = [_release("metallb", **POST_RENDERER)]
    policy = gate.load_policy(
        _policy(tmp_path, crd_keepers={"flux-system/HelmRelease/metallb": "postRenderer"})
    )
    assert gate.violations(docs, policy) == []


def test_a_patch_targeting_another_kind_does_not_count(tmp_path):
    docs = [_release("metallb", **POST_RENDERER)]
    docs[0]["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["target"]["kind"] = "Deployment"
    policy = gate.load_policy(
        _policy(tmp_path, crd_keepers={"flux-system/HelmRelease/metallb": "postRenderer"})
    )
    assert gate.violations(docs, policy)


def test_the_keep_flag_shape_needs_no_retry_strategy(tmp_path):
    docs = [_release("cert-manager", values={"crds": {"keep": True}})]
    policy = gate.load_policy(
        _policy(tmp_path, crd_keepers={"flux-system/HelmRelease/cert-manager": "keep"})
    )
    assert gate.violations(docs, policy) == []
    docs[0]["spec"]["values"] = {"crds": {"keep": False}}
    assert gate.violations(docs, policy)


def test_a_release_in_neither_list_fails(tmp_path):
    """A chart that starts shipping CRDs cannot arrive undeclared."""
    docs = [_release("newcomer")]
    policy = gate.load_policy(
        _policy(tmp_path, no_crds={"flux-system/HelmRelease/other": "ships no CRD"})
    )
    found = gate.violations(docs, policy)
    assert any("neither crd_keepers nor no_crds" in line for line in found)


def test_a_declared_release_absent_from_the_corpus_fails(tmp_path):
    """A stale key would otherwise make its guard check nothing."""
    docs = [_release("eso", **KEEP_VALUES)]
    policy = gate.load_policy(
        _policy(
            tmp_path,
            crd_keepers={"flux-system/HelmRelease/eso": "values"},
            no_crds={"flux-system/HelmRelease/renamed": "ships no CRD"},
        )
    )
    found = gate.violations(docs, policy)
    assert any("absent from the corpus" in line for line in found)


def test_a_missing_required_extra_arg_fails(tmp_path):
    docs = [
        _release("external-dns", values={"extraArgs": ["--source=service"]}),
    ]
    policy = gate.load_policy(
        _policy(
            tmp_path,
            no_crds={"flux-system/HelmRelease/external-dns": "ships no CRD"},
            required_extra_args={
                "flux-system/HelmRelease/external-dns": ["--annotation-prefix=x/"]
            },
        )
    )
    found = gate.violations(docs, policy)
    assert any("annotation-prefix" in line for line in found)
    docs[0]["spec"]["values"]["extraArgs"].append("--annotation-prefix=x/")
    assert gate.violations(docs, policy) == []


@pytest.mark.parametrize(
    "sections,message",
    [
        ({"crd_keepers": {"a/HelmRelease/b": "nonsense"}}, "is not one of"),
        ({"no_crds": {"a/HelmRelease/b": "  "}}, "has no reason"),
        (
            {"crd_keepers": {"a/HelmRelease/b": "values"}, "no_crds": {"a/HelmRelease/b": "r"}},
            "declared in both",
        ),
        ({"required_extra_args": {"a/HelmRelease/b": []}}, "non-empty list"),
        ({"crd_keepers": {}, "no_crds": {}}, "declares no HelmRelease"),
    ],
)
def test_an_unusable_policy_is_an_operator_error(tmp_path, sections, message):
    with pytest.raises(ValueError, match=message):
        gate.load_policy(_policy(tmp_path, **sections))


def test_the_shipped_example_loads(tmp_path):
    policy = gate.load_policy(EXAMPLE)
    assert policy.crd_keepers and policy.no_crds and policy.required_extra_args


def test_main_exits_2_on_a_corpus_without_a_helmrelease(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("kind: ConfigMap\nmetadata:\n  name: x\n"))
    code = gate.main(["--policy-config", str(EXAMPLE)])
    assert code == 2
    assert "no HelmRelease in the corpus" in capsys.readouterr().err
