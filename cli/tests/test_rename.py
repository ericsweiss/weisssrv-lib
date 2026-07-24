"""Tests for the `rename` command."""
from __future__ import annotations

from pathlib import Path

import pytest

from weisssrv_lib_cli import rename
from weisssrv_lib_cli import tree


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


class TestValidation:
    @pytest.mark.parametrize("slug", ["recipe-box", "app", "a1", "my-app-2"])
    def test_valid_slugs(self, slug):
        assert tree.valid_slug(slug)

    @pytest.mark.parametrize("slug", ["Recipe", "-app", "app-", "a_b", "app.x", ""])
    def test_invalid_slugs(self, slug):
        assert not tree.valid_slug(slug)

    @pytest.mark.parametrize("group", ["eric", "eric/apps", "team.a/sub_group-1"])
    def test_valid_groups(self, group):
        assert tree.valid_group(group)

    @pytest.mark.parametrize("group", ["Eric", "/eric", "eric/", "a//b", ""])
    def test_invalid_groups(self, group):
        assert not tree.valid_group(group)

    def test_bad_slug_raises(self, scaffold):
        with pytest.raises(rename.RenameError):
            rename.rename(scaffold, "Bad_Slug", "eric")

    def test_bad_group_raises(self, scaffold):
        with pytest.raises(rename.RenameError):
            rename.rename(scaffold, "recipe-box", "Bad/Group/")


class TestSubstitution:
    def test_tokens_replaced_everywhere(self, scaffold):
        changed = rename.rename(scaffold, "recipe-box", "eric/apps")
        assert changed  # some files changed
        # No placeholder tokens survive anywhere.
        for p in tree.tracked_files(scaffold):
            assert "changeme-app" not in p.read_text(encoding="utf-8")
            assert "changeme-group" not in p.read_text(encoding="utf-8")

    def test_image_path_uses_both(self, scaffold):
        rename.rename(scaffold, "recipe-box", "eric/apps")
        dep = _read(scaffold, "kubernetes/flux/deployment.yaml")
        assert "registry.git.ericsweiss.com/eric/apps/recipe-box:" in dep

    def test_readme_and_codeowners(self, scaffold):
        rename.rename(scaffold, "recipe-box", "eric")
        assert "recipe-box" in _read(scaffold, "README.md")
        assert "@eric" in _read(scaffold, "CODEOWNERS")

    def test_nested_group_in_codeowners(self, scaffold):
        rename.rename(scaffold, "recipe-box", "eric/apps")
        assert "@eric/apps" in _read(scaffold, "CODEOWNERS")

    def test_idempotent_second_run_changes_nothing(self, scaffold):
        rename.rename(scaffold, "recipe-box", "eric")
        again = rename.rename(scaffold, "recipe-box", "eric")
        assert again == []
