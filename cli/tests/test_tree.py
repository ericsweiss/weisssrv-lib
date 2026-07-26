"""Tests for tree file-scanning helpers (the git-tracked scope + rglob fallback)."""
from __future__ import annotations

import subprocess
from pathlib import Path


from weisssrv_lib_cli import tree


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "HOME": str(root), "PATH": __import__("os").environ.get("PATH", "")},
    )


class TestTrackedScope:
    def test_git_tracked_excludes_untracked_and_binary(self, scaffold):
        _git(scaffold, "init", "-q")
        _git(scaffold, "add", "-A")
        # An untracked text file and a tracked binary file.
        (scaffold / "untracked.txt").write_text("not tracked\n")
        (scaffold / "blob.bin").write_bytes(b"\x00\x01\x02NUL")
        _git(scaffold, "add", "blob.bin")

        names = {p.name for p in tree.tracked_files(scaffold)}
        assert "README.md" in names            # tracked text file present
        assert "untracked.txt" not in names     # untracked → excluded
        assert "blob.bin" not in names          # tracked but binary → excluded

    def test_rglob_fallback_without_git(self, scaffold):
        # No .git → falls back to the rglob walk and still finds tracked-shaped files.
        assert not (scaffold / ".git").exists()
        names = {p.name for p in tree.tracked_files(scaffold)}
        assert "README.md" in names
        assert "kustomization.yaml" in names


class TestReadDocumentNames:
    def test_reads_names_via_ruamel(self, scaffold):
        # No PyYAML at runtime — read_document_names uses ruamel's safe loader.
        names = tree.read_document_names(
            scaffold / "kubernetes" / "flux" / "networkpolicy.yaml"
        )
        assert "default-deny" in names
