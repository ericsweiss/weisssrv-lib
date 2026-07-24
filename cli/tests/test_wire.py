"""Tests for the `wire` command."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from weisssrv_lib_cli import wire
from weisssrv_lib_cli import kustomization as kz


def _flux(root: Path, name: str) -> Path:
    return root / "kubernetes" / "flux" / name


def _docs(root: Path, name: str):
    return [d for d in yaml.safe_load_all(_flux(root, name).read_text()) if d]


class TestHpa:
    def test_added_to_kustomization(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        k = _flux(scaffold, "kustomization.yaml").read_text()
        assert "hpa.yaml" in kz.list_resources(k)

    def test_resource_uncommented_and_valid(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        docs = _docs(scaffold, "hpa.yaml")
        assert len(docs) == 1
        assert docs[0]["kind"] == "HorizontalPodAutoscaler"

    def test_deployment_replicas_dropped(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        dep = yaml.safe_load(_flux(scaffold, "deployment.yaml").read_text())
        assert "replicas" not in dep["spec"]

    def test_vpa_memory_only(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        vpa = yaml.safe_load(_flux(scaffold, "vpa.yaml").read_text())
        cp = vpa["spec"]["resourcePolicy"]["containerPolicies"][0]
        assert cp["controlledResources"] == ["memory"]


class TestInternalIngress:
    def test_internal_route_and_cert_activated(self, scaffold):
        wire.wire(scaffold, ["internal-ingress"])
        routes = _docs(scaffold, "ingressroute.yaml")
        certs = _docs(scaffold, "certificate.yaml")
        # Now BOTH the public and internal variants are active.
        assert len(routes) == 2
        assert len(certs) == 2
        internal = [r for r in routes if r["metadata"]["name"].endswith("-internal")]
        assert internal, "internal IngressRoute should be active"


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

    def test_hpa_idempotent_kustomization(self, scaffold):
        wire.wire(scaffold, ["hpa"])
        wire.wire(scaffold, ["hpa"])
        # hpa.yaml listed exactly once.
        k = _flux(scaffold, "kustomization.yaml").read_text()
        assert kz.list_resources(k).count("hpa.yaml") == 1
