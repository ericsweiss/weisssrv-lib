"""End-to-end tests of the CLI dispatch layer (weisssrv_lib_cli.cli.main)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from weisssrv_lib_cli.cli import main
from weisssrv_lib_cli import kustomization as kz, tree


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

    def test_rename_alone_leaves_both_ci_shapes(self, scaffold):
        assert main(["rename", "recipe-box", "eric", "--root", str(scaffold)]) == 0
        assert (scaffold / tree.GITLAB_CI).is_file()
        assert (scaffold / tree.GITHUB_WORKFLOWS).is_dir()

    @pytest.mark.parametrize(
        "shape,keeps,drops",
        [
            ("gitlab", [tree.GITLAB_CI], [tree.GITHUB_WORKFLOWS]),
            ("github", [tree.GITHUB_WORKFLOWS], [tree.GITLAB_CI]),
            ("none", [], [tree.GITLAB_CI, tree.GITHUB_WORKFLOWS]),
        ],
    )
    def test_rename_with_ci_selects_in_one_call(self, scaffold, shape, keeps, drops):
        rc = main(
            ["rename", "recipe-box", "eric", "--ci", shape, "--root", str(scaffold)]
        )
        assert rc == 0
        assert "changeme-app" not in (scaffold / "README.md").read_text()
        for rel in keeps:
            assert (scaffold / rel).exists()
        for rel in drops:
            assert not (scaffold / rel).exists()
        # One invocation must leave a project `verify` calls clean.
        assert main(["verify", "--no-kustomize", "--root", str(scaffold)]) == 0

    def test_unknown_ci_shape_exits_2_before_renaming(self, scaffold, capsys):
        # argparse rejects the choice at PARSE time — nothing is mutated.
        with pytest.raises(SystemExit) as exc:
            main(["rename", "recipe-box", "eric", "--ci", "bogus", "--root", str(scaffold)])
        assert exc.value.code == 2
        assert "changeme-app" in (scaffold / "README.md").read_text()
        assert (scaffold / tree.GITLAB_CI).is_file()
        assert (scaffold / tree.GITHUB_WORKFLOWS).is_dir()

    def test_invalid_slug_with_ci_prunes_nothing(self, scaffold):
        rc = main(
            ["rename", "Bad_Slug", "eric", "--ci", "none", "--root", str(scaffold)]
        )
        assert rc == 2
        assert (scaffold / tree.GITLAB_CI).is_file()
        assert (scaffold / tree.GITHUB_WORKFLOWS).is_dir()


class TestPruneWireCommands:
    def test_prune_secrets(self, scaffold):
        rc = main(["prune", "secrets", "--root", str(scaffold)])
        assert rc == 0
        assert not _flux(scaffold, "externalsecret.yaml").exists()

    def test_prune_unknown_returns_2(self, scaffold):
        rc = main(["prune", "nope", "--root", str(scaffold)])
        assert rc == 2

    def test_prune_ci_shape(self, scaffold):
        rc = main(["prune", "ci:github", "--root", str(scaffold)])
        assert rc == 0
        assert not (scaffold / tree.GITLAB_CI).exists()
        assert (scaffold / tree.GITHUB_WORKFLOWS).is_dir()

    def test_prune_unknown_ci_shape_returns_2(self, scaffold, capsys):
        rc = main(["prune", "ci:bogus", "--root", str(scaffold)])
        assert rc == 2
        assert "unknown CI shape" in capsys.readouterr().err
        assert (scaffold / tree.GITLAB_CI).is_file()

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
        assert (
            main(["rename", "recipe-box", "eric", "--ci", "gitlab", "--root", str(scaffold)])
            == 0
        )
        assert main(["verify", "--no-kustomize", "--root", str(scaffold)]) == 0

    def test_verify_flags_a_project_that_never_selected_a_ci_shape(self, scaffold, capsys):
        assert main(["rename", "recipe-box", "eric", "--root", str(scaffold)]) == 0
        assert main(["verify", "--no-kustomize", "--root", str(scaffold)]) == 1
        assert "both CI shapes are present" in capsys.readouterr().out


class TestFullFlow:
    def test_rename_prune_wire_verify(self, scaffold):
        assert (
            main(["rename", "recipe-box", "eric/apps", "--ci", "gitlab", "--root", str(scaffold)])
            == 0
        )
        assert main(["prune", "metrics", "single-replica", "--root", str(scaffold)]) == 0
        assert main(["wire", "sso", "--root", str(scaffold)]) == 0
        assert main(["verify", "--no-kustomize", "--root", str(scaffold)]) == 0
        # metrics pruned, single-replica applied, sso wired.
        assert not _flux(scaffold, "servicemonitor.yaml").exists()
        dep = yaml.safe_load(_flux(scaffold, "deployment.yaml").read_text())
        assert dep["spec"]["replicas"] == 1
