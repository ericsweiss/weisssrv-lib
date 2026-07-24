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

    def test_keeps_unrelated_plain_env_var(self, scaffold):
        # A consumer that customized the scaffold with an extra plain env var
        # (no secretKeyRef) must keep it: `prune secrets` removes ONLY the env
        # entries bound to the pruned ExternalSecret's target Secret.
        dep_path = _flux(scaffold, "deployment.yaml")
        dep_path.write_text(
            dep_path.read_text(encoding="utf-8").replace(
                "          env:\n            - name: API_KEY\n",
                "          env:\n"
                "            - name: LOG_LEVEL\n"
                "              value: info\n"
                "            - name: API_KEY\n",
            ),
            encoding="utf-8",
        )
        prune.prune(scaffold, ["secrets"])
        dep = dep_path.read_text()
        # The secretKeyRef-backed entry is gone.
        assert "secretKeyRef" not in dep
        assert "changeme-app-secrets" not in dep
        assert "API_KEY" not in dep
        # The user's plain env var survives.
        container = yaml.safe_load(dep)["spec"]["template"]["spec"]["containers"][0]
        env = container["env"]
        assert [e["name"] for e in env] == ["LOG_LEVEL"]
        assert env[0]["value"] == "info"


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

    def test_manifest_empty_name_raises(self, scaffold):
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["manifest:"])

    @pytest.mark.parametrize(
        "evil",
        [
            "manifest:../evil",
            "manifest:../../evil",
            "manifest:/etc/passwd",
            "manifest:sub/evil",
            "manifest:..",
        ],
    )
    def test_manifest_path_traversal_rejected(self, scaffold, evil):
        # A `manifest:` value that escapes kubernetes/flux/ must be refused with
        # NO deletion. Plant a victim one level up (kubernetes/) that the naive
        # `../victim` path would resolve to, and assert it survives.
        victim = scaffold / "kubernetes" / "victim.yaml"
        victim.write_text("i must survive\n")
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, [evil])
        assert victim.exists()
        # A valid feature after the evil one must also not have run (up-front
        # validation refuses the whole request before touching anything).
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["secrets", evil])
        assert _flux(scaffold, "externalsecret.yaml").exists()
        assert victim.exists()

    @pytest.mark.parametrize("feature", ["secrets", "metrics", "pdb", "single-replica", "hpa"])
    def test_idempotent(self, scaffold, feature):
        prune.prune(scaffold, [feature])
        second = prune.prune(scaffold, [feature])
        assert second == []

    def test_hpa_file_removed(self, scaffold):
        prune.prune(scaffold, ["hpa"])
        assert not _flux(scaffold, "hpa.yaml").exists()

    def test_image_build_removes_root_files(self, scaffold):
        (scaffold / "Dockerfile").write_text("FROM scratch\n")
        (scaffold / ".dockerignore").write_text("*\n")
        prune.prune(scaffold, ["image-build"])
        assert not (scaffold / "Dockerfile").exists()
        assert not (scaffold / ".dockerignore").exists()


class TestUpFrontValidation:
    def test_bad_name_after_valid_does_not_mutate(self, scaffold):
        # A typo AFTER a valid feature must raise before touching any file.
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["secrets", "bogus"])
        assert _flux(scaffold, "externalsecret.yaml").exists()
        assert "externalsecret.yaml" in kz.list_resources(_kustomization(scaffold))

    def test_external_ingress_guard_refuses_before_earlier_feature(self, scaffold):
        # metrics is valid, but external-ingress would empty a file on the fresh
        # scaffold — the whole request must refuse without pruning metrics.
        with pytest.raises(prune.PruneError):
            prune.prune(scaffold, ["metrics", "external-ingress"])
        assert _flux(scaffold, "servicemonitor.yaml").exists()
