"""End-to-end tests of the CLI dispatch layer (weisssrv_lib_cli.cli.main)."""
from __future__ import annotations

from pathlib import Path

import yaml

from weisssrv_lib_cli.cli import main
from weisssrv_lib_cli import kustomization as kz


def _flux(root: Path, name: str) -> Path:
    return root / "kubernetes" / "flux" / name


class TestRenameCommand:
    def test_rename_ok(self, scaffold):
        rc = main(["rename", "recipe-box", "eric", "--root", str(scaffold)])
        assert rc == 0
        assert "changeme-app" not in (scaffold / "README.md").read_text()

    def test_invalid_slug_returns_2(self, scaffold, capsys):
        rc = main(["rename", "Bad_Slug", "eric", "--root", str(scaffold)])
        assert rc == 2
        assert "error:" in capsys.readouterr().err


class TestPruneWireCommands:
    def test_prune_secrets(self, scaffold):
        rc = main(["prune", "secrets", "--root", str(scaffold)])
        assert rc == 0
        assert not _flux(scaffold, "externalsecret.yaml").exists()

    def test_prune_unknown_returns_2(self, scaffold):
        rc = main(["prune", "nope", "--root", str(scaffold)])
        assert rc == 2

    def test_wire_hpa(self, scaffold):
        rc = main(["wire", "hpa", "--root", str(scaffold)])
        assert rc == 0
        k = _flux(scaffold, "kustomization.yaml").read_text()
        assert "hpa.yaml" in kz.list_resources(k)


class TestVerifyCommand:
    def test_verify_fresh_fails(self, scaffold):
        rc = main(["verify", "--no-kustomize", "--root", str(scaffold)])
        assert rc == 1

    def test_rename_then_verify_ok(self, scaffold):
        assert main(["rename", "recipe-box", "eric", "--root", str(scaffold)]) == 0
        assert main(["verify", "--no-kustomize", "--root", str(scaffold)]) == 0


class TestFullFlow:
    def test_rename_prune_wire_verify(self, scaffold):
        assert main(["rename", "recipe-box", "eric/apps", "--root", str(scaffold)]) == 0
        assert main(["prune", "metrics", "single-replica", "--root", str(scaffold)]) == 0
        assert main(["wire", "sso", "--root", str(scaffold)]) == 0
        assert main(["verify", "--no-kustomize", "--root", str(scaffold)]) == 0
        # metrics pruned, single-replica applied, sso wired.
        assert not _flux(scaffold, "servicemonitor.yaml").exists()
        dep = yaml.safe_load(_flux(scaffold, "deployment.yaml").read_text())
        assert dep["spec"]["replicas"] == 1
