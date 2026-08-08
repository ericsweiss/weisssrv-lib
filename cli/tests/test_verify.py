"""Tests for the `verify` command."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from weisssrv_lib_cli import prune, rename, tree, verify


def _flux(root: Path, name: str) -> Path:
    return root / "kubernetes" / "flux" / name


def _configured(root: Path, ci: str = "gitlab") -> None:
    """A fully set-up project: placeholders renamed AND a CI shape selected."""
    rename.rename(root, "recipe-box", "eric")
    prune.prune(root, [f"ci:{ci}"])


class TestTokens:
    def test_fresh_scaffold_flags_tokens(self, scaffold):
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("placeholder token" in p for p in problems)

    def test_renamed_and_ci_selected_scaffold_passes(self, scaffold):
        _configured(scaffold)
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert ok, problems


class TestCiShape:
    def test_unselected_project_is_flagged(self, scaffold):
        # The template ships all three shapes; a project that renamed but never
        # ran the selector still has both — a GitHub mirror would run duplicate
        # gates. verify must say so.
        rename.rename(scaffold, "recipe-box", "eric")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("both CI shapes are present" in p for p in problems)

    @pytest.mark.parametrize("shape", ["gitlab", "github", "none"])
    def test_every_selected_shape_verifies_clean(self, scaffold, shape):
        _configured(scaffold, shape)
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert ok, problems
        assert not any("CI shape" in p for p in problems)

    def test_leftover_gitlab_ruleset_is_flagged(self, scaffold):
        # A hand-deleted .gitlab-ci.yml that leaves the Secret-Detection ruleset
        # behind is a half-applied selection, not a valid `none` shape.
        _configured(scaffold, "none")
        leftover = scaffold / tree.GITLAB_CI_EXTRA[0]
        leftover.parent.mkdir(parents=True, exist_ok=True)
        leftover.write_text("[secrets]\n")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("left over from the gitlab CI shape" in p for p in problems)

    def test_gitlab_shape_missing_its_ruleset_is_flagged(self, scaffold):
        _configured(scaffold, "gitlab")
        (scaffold / tree.GITLAB_CI_EXTRA[0]).unlink()
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("gitlab CI shape is selected but" in p for p in problems)

    def test_symlinked_gitlab_ruleset_is_flagged(self, scaffold, tmp_path):
        # verify and prune must agree about the SAME tree. verify used
        # `.exists()`, which follows symlinks, so a symlinked ruleset verified
        # clean while `prune ci:gitlab` called the shape unsatisfiable and
        # refused to keep it — one of the two was lying. git tracks the link,
        # not a runnable file at that path.
        _configured(scaffold, "gitlab")
        ruleset = scaffold / tree.GITLAB_CI_EXTRA[0]
        target = tmp_path / "elsewhere.toml"
        target.write_text("[secrets]\n")
        ruleset.unlink()
        ruleset.symlink_to(target)
        assert ruleset.exists(), "precondition: the link resolves, so .exists() is True"
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("not a regular file" in p for p in problems), problems

    def test_dangling_leftover_gitlab_ruleset_is_flagged(self, scaffold, tmp_path):
        # A BROKEN link is neither a regular file nor absent. Without the
        # is_symlink() arm it would fall through both branches and be reported
        # as a clean `none` tree.
        _configured(scaffold, "none")
        leftover = scaffold / tree.GITLAB_CI_EXTRA[0]
        leftover.parent.mkdir(parents=True, exist_ok=True)
        if leftover.exists() or leftover.is_symlink():
            leftover.unlink()
        leftover.symlink_to(tmp_path / "does-not-exist.toml")
        assert not leftover.exists() and leftover.is_symlink()
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("left over from the gitlab CI shape" in p for p in problems), problems

    @pytest.mark.parametrize(
        "leftover",
        [
            None,  # empty dir
            ".gitkeep",  # a file, but GitHub runs nothing from it
            "README.md",
        ],
    )
    def test_workflows_dir_without_a_runnable_workflow_is_a_leftover(
        self, scaffold, leftover
    ):
        # GitHub runs regular .yml/.yaml only. Counting "any file" as the shape
        # let verify pass a project whose CI would never execute.
        _configured(scaffold, "gitlab")
        wf = scaffold / tree.GITHUB_WORKFLOWS
        wf.mkdir(parents=True)
        if leftover:
            (wf / leftover).write_text("x\n")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("no runnable workflow" in p for p in problems)
        # It runs nothing, so it must NOT read as a second shape.
        assert not any("both CI shapes" in p for p in problems)

    def test_malformed_gitlab_ci_is_named_not_reported_as_gone(
        self, scaffold, tmp_path
    ):
        # A symlinked .gitlab-ci.yml is not the gitlab shape (prune refuses to
        # keep it, so verify must agree) — but the operator has to be told the
        # file is malformed, not that it is missing when it is sitting there.
        real = tmp_path / "elsewhere.yml"
        real.write_text("stages: [x]\n")
        (scaffold / tree.GITLAB_CI).unlink()
        (scaffold / tree.GITLAB_CI).symlink_to(real)
        shutil.rmtree(scaffold / tree.GITHUB_WORKFLOWS)
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("is not a regular file" in p for p in problems)

    def test_symlinked_workflow_does_not_count_as_a_shape(self, scaffold, tmp_path):
        # Actions does not follow a symlink out of the workspace.
        _configured(scaffold, "gitlab")
        real = tmp_path / "ci.yml"
        real.write_text("name: x\n")
        wf = scaffold / tree.GITHUB_WORKFLOWS
        wf.mkdir(parents=True)
        (wf / "ci.yml").symlink_to(real)
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("no runnable workflow" in p for p in problems)


class TestStructure:
    def test_missing_listed_resource_flagged(self, scaffold):
        _configured(scaffold)
        _flux(scaffold, "service.yaml").unlink()  # listed but now missing
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("service.yaml" in p and "missing" in p for p in problems)

    def test_orphan_manifest_flagged(self, scaffold):
        _configured(scaffold)
        _flux(scaffold, "orphan.yaml").write_text("---\nkind: ConfigMap\n")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("orphan.yaml" in p and "not referenced" in p for p in problems)

    def test_optin_hpa_not_flagged_as_orphan(self, scaffold):
        # hpa.yaml ships on disk but is opt-in (not in resources) — must NOT be
        # reported as an orphan.
        _configured(scaffold)
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert ok, problems
        assert not any("hpa.yaml" in p for p in problems)


class TestKustomizeNote:
    def test_kustomize_missing_is_advisory(self, scaffold, monkeypatch):
        _configured(scaffold)
        # Force the "kustomize not on PATH" branch regardless of the host.
        monkeypatch.setattr(verify.shutil, "which", lambda _: None)
        ok, problems = verify.verify(scaffold, run_kustomize=True)
        assert ok
        assert any(p.startswith("NOTE:") for p in problems)

    def test_kustomize_build_failure_is_a_hard_problem(self, scaffold, monkeypatch):
        _configured(scaffold)
        monkeypatch.setattr(verify.shutil, "which", lambda _: "/usr/bin/kustomize")

        class _Result:
            returncode = 1
            stderr = "Error: build failed\n"

        monkeypatch.setattr(verify.subprocess, "run", lambda *a, **k: _Result())
        ok, problems = verify.verify(scaffold, run_kustomize=True)
        assert not ok
        assert any("kustomize build failed" in p for p in problems)


class TestBrokenScaffold:
    def test_missing_flux_dir_early_returns(self, scaffold):
        import shutil as _sh

        _sh.rmtree(scaffold / "kubernetes" / "flux")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("missing directory" in p for p in problems)

    def test_missing_kustomization_early_returns(self, scaffold):
        _flux(scaffold, "kustomization.yaml").unlink()
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("kustomization.yaml" in p and "missing" in p for p in problems)
