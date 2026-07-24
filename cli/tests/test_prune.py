"""Tests for the `prune` command."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from weisssrv_lib_cli import prune
from weisssrv_lib_cli import kustomization as kz


def _flux(root: Path, name: str) -> Path:
    return root / "kubernetes" / "flux" / name


def _kustomization(root: Path) -> str:
    return _flux(root, "kustomization.yaml").read_text(encoding="utf-8")


def _docs(root: Path, name: str):
    return [d for d in yaml.safe_load_all(_flux(root, name).read_text()) if d]


class TestSecrets:
    def test_removes_manifest_and_reference(self, scaffold):
        prune.prune(scaffold, ["secrets"])
        assert not _flux(scaffold, "externalsecret.yaml").exists()
        assert "externalsecret.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_removes_deployment_env_block(self, scaffold):
        prune.prune(scaffold, ["secrets"])
        dep = _flux(scaffold, "deployment.yaml").read_text()
        assert "secretKeyRef" not in dep
        assert "changeme-app-secrets" not in dep
        # The rest of the deployment still parses.
        loaded = yaml.safe_load(dep)
        container = loaded["spec"]["template"]["spec"]["containers"][0]
        assert "env" not in container


class TestMetrics:
    def test_removes_servicemonitor(self, scaffold):
        prune.prune(scaffold, ["metrics"])
        assert not _flux(scaffold, "servicemonitor.yaml").exists()
        assert "servicemonitor.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_removes_observability_scrape_policy(self, scaffold):
        prune.prune(scaffold, ["metrics"])
        names = {(d.get("metadata") or {}).get("name") for d in _docs(scaffold, "networkpolicy.yaml")}
        assert "allow-scrape-from-observability" not in names
        # The other policies survive.
        assert "default-deny" in names
        assert "allow-ingress-from-traefik" in names


class TestReplicas:
    def test_pdb_removed(self, scaffold):
        prune.prune(scaffold, ["pdb"])
        assert not _flux(scaffold, "pdb.yaml").exists()
        assert "pdb.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_single_replica_sets_one_and_drops_pdb(self, scaffold):
        prune.prune(scaffold, ["single-replica"])
        assert not _flux(scaffold, "pdb.yaml").exists()
        dep = yaml.safe_load(_flux(scaffold, "deployment.yaml").read_text())
        assert dep["spec"]["replicas"] == 1


class TestExternalIngress:
    def test_fresh_scaffold_refuses_and_leaves_files_intact(self, scaffold):
        # On a fresh scaffold the only ACTIVE documents are the public route +
        # cert; dropping them would empty both files while they stay listed in
        # the kustomization. prune must REFUSE and touch nothing.
        before_ir = _flux(scaffold, "ingressroute.yaml").read_text()
        before_cert = _flux(scaffold, "certificate.yaml").read_text()
        with pytest.raises(prune.PruneError) as exc:
            prune.prune(scaffold, ["external-ingress"])
        assert "wire internal-ingress" in str(exc.value)
        assert _flux(scaffold, "ingressroute.yaml").read_text() == before_ir
        assert _flux(scaffold, "certificate.yaml").read_text() == before_cert

    def test_internal_only_workflow(self, scaffold):
        # The meaningful path: wire internal first, then drop the public docs —
        # leaving exactly the internal IngressRoute + Certificate active.
        from weisssrv_lib_cli import wire

        wire.wire(scaffold, ["internal-ingress"])
        prune.prune(scaffold, ["external-ingress"])
        routes = _docs(scaffold, "ingressroute.yaml")
        certs = _docs(scaffold, "certificate.yaml")
        assert [r["metadata"]["name"] for r in routes] == ["changeme-app-internal"]
        assert [c["metadata"]["name"] for c in certs] == ["changeme-app-tls-internal"]


class TestGenericAndErrors:
    def test_manifest_feature(self, scaffold):
        prune.prune(scaffold, ["manifest:servicemonitor"])
        assert not _flux(scaffold, "servicemonitor.yaml").exists()
        assert "servicemonitor.yaml" not in kz.list_resources(_kustomization(scaffold))

    def test_unknown_feature_raises(self, scaffold):
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["bogus"])

    def test_idempotent(self, scaffold):
        prune.prune(scaffold, ["secrets"])
        second = prune.prune(scaffold, ["secrets"])
        assert second == []

    def test_hpa_file_removed(self, scaffold):
        prune.prune(scaffold, ["hpa"])
        assert not _flux(scaffold, "hpa.yaml").exists()
