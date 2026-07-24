"""Tests for the `verify` command."""
from __future__ import annotations

from pathlib import Path

from weisssrv_lib_cli import rename, verify


def _flux(root: Path, name: str) -> Path:
    return root / "kubernetes" / "flux" / name


class TestTokens:
    def test_fresh_scaffold_flags_tokens(self, scaffold):
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("placeholder token" in p for p in problems)

    def test_renamed_scaffold_passes(self, scaffold):
        rename.rename(scaffold, "recipe-box", "eric")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert ok, problems


class TestStructure:
    def test_missing_listed_resource_flagged(self, scaffold):
        rename.rename(scaffold, "recipe-box", "eric")
        _flux(scaffold, "service.yaml").unlink()  # listed but now missing
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("service.yaml" in p and "missing" in p for p in problems)

    def test_orphan_manifest_flagged(self, scaffold):
        rename.rename(scaffold, "recipe-box", "eric")
        _flux(scaffold, "orphan.yaml").write_text("---\nkind: ConfigMap\n")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert not ok
        assert any("orphan.yaml" in p and "not referenced" in p for p in problems)

    def test_optin_hpa_not_flagged_as_orphan(self, scaffold):
        # hpa.yaml ships on disk but is opt-in (not in resources) — must NOT be
        # reported as an orphan.
        rename.rename(scaffold, "recipe-box", "eric")
        ok, problems = verify.verify(scaffold, run_kustomize=False)
        assert ok, problems
        assert not any("hpa.yaml" in p for p in problems)


class TestKustomizeNote:
    def test_kustomize_missing_is_advisory(self, scaffold, monkeypatch):
        rename.rename(scaffold, "recipe-box", "eric")
        # Force the "kustomize not on PATH" branch regardless of the host.
        monkeypatch.setattr(verify.shutil, "which", lambda _: None)
        ok, problems = verify.verify(scaffold, run_kustomize=True)
        assert ok
        assert any(p.startswith("NOTE:") for p in problems)

    def test_kustomize_build_failure_is_a_hard_problem(self, scaffold, monkeypatch):
        rename.rename(scaffold, "recipe-box", "eric")
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
