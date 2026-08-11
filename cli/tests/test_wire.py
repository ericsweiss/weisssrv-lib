"""Tests for the `wire` command."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import needs_optional_layout

from weisssrv_lib_cli import tree, wire
from weisssrv_lib_cli import kustomization as kz


def _flux(root: Path, name: str) -> Path:
    return root / "kubernetes" / "flux" / name


def _docs(root: Path, name: str):
    return [d for d in yaml.safe_load_all(_flux(root, name).read_text()) if d]


def _resources(root: Path) -> list[str]:
    return kz.list_resources(_flux(root, "kustomization.yaml").read_text())


class TestHpa:
    @needs_optional_layout
    def test_optional_manifest_enabled(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        assert tree.HPA_MANIFEST in _resources(scaffold)

    @needs_optional_layout
    def test_enabled_manifest_is_a_valid_hpa(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        docs = _docs(scaffold, tree.HPA_MANIFEST)
        assert len(docs) == 1
        assert docs[0]["kind"] == "HorizontalPodAutoscaler"

    @needs_optional_layout
    def test_deployment_replicas_dropped(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        dep = yaml.safe_load(_flux(scaffold, "deployment.yaml").read_text())
        assert "replicas" not in dep["spec"]

    @needs_optional_layout
    def test_vpa_memory_only(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        vpa = yaml.safe_load(_flux(scaffold, "vpa.yaml").read_text())
        cp = vpa["spec"]["resourcePolicy"]["containerPolicies"][0]
        assert cp["controlledResources"] == ["memory"]

    def test_missing_enable_line_is_reported_not_invented(self, scaffold, capsys):
        # Writing a resource line for a manifest the tree does not have yields a
        # kustomization that cannot build — the failure mode this replaced. The
        # paired edits are held back too: a deployment with no `replicas` and no
        # HPA has nothing setting its replica count.
        kpath = _flux(scaffold, "kustomization.yaml")
        kpath.write_text(
            "---\napiVersion: kustomize.config.k8s.io/v1beta1\n"
            "kind: Kustomization\nresources:\n  - deployment.yaml\n",
            encoding="utf-8",
        )
        wire.wire(scaffold, ["hpa"])
        assert _resources(scaffold) == ["deployment.yaml"]
        assert "warning" in capsys.readouterr().err
        dep = yaml.safe_load(_flux(scaffold, "deployment.yaml").read_text())
        assert "replicas" in dep["spec"]


class TestInternalIngress:
    @needs_optional_layout
    def test_route_and_cert_both_enabled(self, scaffold):
        # The route serves TLS from the secret the certificate issues, so one
        # without the other is a half-wired hostname.
        wire.wire(scaffold, ["internal-ingress"])
        assert set(tree.INTERNAL_INGRESS_MANIFESTS) <= set(_resources(scaffold))

    @needs_optional_layout
    def test_public_variants_stay_enabled(self, scaffold):
        wire.wire(scaffold, ["internal-ingress"])
        resources = _resources(scaffold)
        assert "ingressroute.yaml" in resources and "certificate.yaml" in resources

    @needs_optional_layout
    def test_missing_certificate_manifest_enables_neither(self, scaffold, capsys):
        # The pair is one logical change: enabling the route and THEN finding the
        # certificate has no manifest would persist exactly the half-wired state
        # `prune external-ingress` refuses to act on. Preflight, then write once.
        _flux(scaffold, tree.INTERNAL_INGRESS_MANIFESTS[1]).unlink()
        before = _flux(scaffold, "kustomization.yaml").read_text()
        wire.wire(scaffold, ["internal-ingress"])
        assert _flux(scaffold, "kustomization.yaml").read_text() == before
        assert not set(tree.INTERNAL_INGRESS_MANIFESTS) & set(_resources(scaffold))
        assert "warning" in capsys.readouterr().err

    @needs_optional_layout
    def test_missing_certificate_enable_line_enables_neither(self, scaffold, capsys):
        # Same guarantee when the manifest exists but its `# - optional/…` line
        # was deleted from the kustomization: nothing is invented, and the route
        # is not left enabled on its own.
        kpath = _flux(scaffold, "kustomization.yaml")
        cert = tree.INTERNAL_INGRESS_MANIFESTS[1]
        kpath.write_text(
            "".join(
                line
                for line in kpath.read_text(encoding="utf-8").splitlines(keepends=True)
                if cert not in line
            ),
            encoding="utf-8",
        )
        before = kpath.read_text(encoding="utf-8")
        wire.wire(scaffold, ["internal-ingress"])
        assert kpath.read_text(encoding="utf-8") == before
        assert not set(tree.INTERNAL_INGRESS_MANIFESTS) & set(_resources(scaffold))
        assert "warning" in capsys.readouterr().err

    @needs_optional_layout
    def test_a_half_wired_tree_is_completed_not_refused(self, scaffold):
        # The preflight must not block the repair path: a tree where only the
        # route is active (hand-edited, or wired by an older CLI) still gets the
        # certificate enabled, ending BOTH-active.
        kpath = _flux(scaffold, "kustomization.yaml")
        text, did = kz.uncomment_resource(
            kpath.read_text(encoding="utf-8"), tree.INTERNAL_INGRESS_MANIFESTS[0]
        )
        assert did
        kpath.write_text(text, encoding="utf-8")
        wire.wire(scaffold, ["internal-ingress"])
        assert set(tree.INTERNAL_INGRESS_MANIFESTS) <= set(_resources(scaffold))


class TestSso:
    def test_authentik_middleware_activated(self, scaffold):
        wire.wire(scaffold, ["sso"])
        route = _docs(scaffold, "ingressroute.yaml")[0]
        mws = route["spec"]["routes"][0]["middlewares"]
        names = {m["name"] for m in mws}
        assert "authentik-auth" in names
        assert "hsts-header" in names  # existing middleware preserved


class TestErrors:
    def test_unknown_feature_raises(self, scaffold):
        with pytest.raises(wire.WireError):
            wire.wire(scaffold, ["bogus"])

    def test_bad_name_after_valid_does_not_mutate(self, scaffold):
        # A typo AFTER a valid feature must raise before touching any file.
        k_before = _flux(scaffold, "kustomization.yaml").read_text()
        with pytest.raises(wire.WireError):
            wire.wire(scaffold, ["hpa", "bogus"])
        assert _flux(scaffold, "kustomization.yaml").read_text() == k_before
        assert tree.HPA_MANIFEST not in kz.list_resources(k_before)


class TestIdempotency:
    @needs_optional_layout
    def test_hpa_idempotent_kustomization(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        wire.wire(scaffold, ["hpa"])
        assert _resources(scaffold).count(tree.HPA_MANIFEST) == 1

    @needs_optional_layout
    def test_internal_ingress_idempotent(self, scaffold):
        wire.wire(scaffold, ["internal-ingress"])
        wire.wire(scaffold, ["internal-ingress"])
        resources = _resources(scaffold)
        for name in tree.INTERNAL_INGRESS_MANIFESTS:
            assert resources.count(name) == 1

    def test_sso_idempotent(self, scaffold):
        wire.wire(scaffold, ["sso"])
        wire.wire(scaffold, ["sso"])
        route = _docs(scaffold, "ingressroute.yaml")[0]
        names = [m["name"] for m in route["spec"]["routes"][0]["middlewares"]]
        assert names.count("authentik-auth") == 1


def test_stale_active_line_with_missing_manifest_refuses_paired_edits(scaffold):
    """An active hpa.yaml entry whose manifest was deleted must not strip
    spec.replicas — the HPA will never deploy to take over the count."""
    import shutil
    from weisssrv_lib_cli import tree, wire

    kpath = tree.flux_file(scaffold, tree.KUSTOMIZATION)
    text = kpath.read_text(encoding="utf-8")
    new, did = __import__("weisssrv_lib_cli.kustomization", fromlist=["k"]).uncomment_resource(
        text, tree.HPA_MANIFEST
    )
    assert did
    kpath.write_text(new, encoding="utf-8")
    (tree.flux_file(scaffold, tree.HPA_MANIFEST)).unlink()

    dep_before = tree.flux_file(scaffold, tree.DEPLOYMENT).read_text(encoding="utf-8")
    changed: list = []
    wire._wire_hpa(scaffold, changed)
    assert changed == []
    assert tree.flux_file(scaffold, tree.DEPLOYMENT).read_text(encoding="utf-8") == dep_before
