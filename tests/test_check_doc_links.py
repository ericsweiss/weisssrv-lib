"""Tests for scripts/check-doc-links.py (the offline Markdown link gate).

Covers link resolution, section citations, the exclusions, file discovery, the
exit codes, and a smoke check on the real repo docs.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent

cdl = load_script("check-doc-links.py")


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _git_init(root: Path, *, commit: bool) -> None:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, env=env)
    if commit:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
        subprocess.run(
            ["git", "-C", str(root), "commit", "-qm", "x", "--no-verify"],
            check=True,
            env=env,
        )


class TestBrokenLinks:
    def test_resolving_link_passes(self, tmp_path: Path):
        _write(tmp_path / "docs" / "01-a.md", "see [b](02-b.md)\n")
        _write(tmp_path / "docs" / "02-b.md", "# B\n")
        assert cdl.broken_links([tmp_path / "docs" / "01-a.md"]) == []

    def test_missing_target_is_broken(self, tmp_path: Path):
        src = _write(tmp_path / "docs" / "01-a.md", "see [gone](99-missing.md)\n")
        broken = cdl.broken_links([src])
        assert len(broken) == 1
        assert broken[0][0] == src
        assert "99-missing.md" in broken[0][1]

    def test_parent_relative_link_resolves(self, tmp_path: Path):
        _write(tmp_path / "README.md", "top\n")
        # A link from docs/ up to the repo root README resolves correctly.
        src = _write(tmp_path / "docs" / "01-a.md", "[root](../README.md)\n")
        assert cdl.broken_links([src]) == []

    def test_anchor_is_stripped_before_resolving(self, tmp_path: Path):
        _write(tmp_path / "docs" / "02-b.md", "# B\n")
        src = _write(tmp_path / "docs" / "01-a.md", "[b](02-b.md#section)\n")
        assert cdl.broken_links([src]) == []

    def test_title_after_target_is_stripped(self, tmp_path: Path):
        _write(tmp_path / "docs" / "02-b.md", "# B\n")
        src = _write(tmp_path / "docs" / "01-a.md", '[b](02-b.md "The B doc")\n')
        assert cdl.broken_links([src]) == []


class TestExclusions:
    def test_urls_ignored(self, tmp_path: Path):
        src = _write(
            tmp_path / "docs" / "01-a.md",
            "[x](https://example.com/nope.md) [y](mailto:a@b.md)\n",
        )
        assert cdl.broken_links([src]) == []

    def test_anchor_only_ignored(self, tmp_path: Path):
        src = _write(tmp_path / "docs" / "01-a.md", "[jump](#heading)\n")
        assert cdl.broken_links([src]) == []

    def test_non_md_target_ignored(self, tmp_path: Path):
        # A dangling non-.md link (image / dir) is out of scope, not a failure.
        src = _write(tmp_path / "docs" / "01-a.md", "[img](diagram.png) [d](../scripts/)\n")
        assert cdl.broken_links([src]) == []


class TestDocFiles:
    def test_fallback_collects_docs_and_top_level_readmes(self, tmp_path: Path):
        # Not a git checkout => docs/ + $CHECK_DOC_LINKS_EXTRA fallback.
        _write(tmp_path / "docs" / "01-a.md", "a\n")
        _write(tmp_path / "docs" / "sub" / "02-b.md", "b\n")
        _write(tmp_path / "README.md", "r\n")
        _write(tmp_path / "CLAUDE.md", "c\n")
        names = {p.name for p in cdl.doc_files(tmp_path)}
        assert {"01-a.md", "02-b.md", "README.md", "CLAUDE.md"} <= names

    def test_fallback_extra_files_are_env_overridable(self, tmp_path: Path, monkeypatch):
        _write(tmp_path / "docs" / "01-a.md", "a\n")
        _write(tmp_path / "TESTING.md", "t\n")
        _write(tmp_path / "README.md", "r\n")
        monkeypatch.setenv("CHECK_DOC_LINKS_EXTRA", "TESTING.md")
        names = {p.name for p in cdl.doc_files(tmp_path)}
        assert "TESTING.md" in names
        assert "README.md" not in names

    def test_tracked_scan_covers_markdown_outside_docs(self, tmp_path: Path):
        # In a git checkout every tracked *.md is scanned, not just docs/.
        _write(tmp_path / "roles" / "foo" / "README.md", "[x](../../docs/01-a.md)\n")
        _write(tmp_path / "docs" / "01-a.md", "a\n")
        _git_init(tmp_path, commit=True)
        names = {str(p.relative_to(tmp_path)) for p in cdl.doc_files(tmp_path)}
        assert "roles/foo/README.md" in names

    def test_git_checkout_with_nothing_tracked_does_not_fall_back(self, tmp_path: Path):
        # An unstaged checkout must report nothing found, not a partial pass
        # over docs/ that leaves the rest of the tree unchecked.
        _write(tmp_path / "docs" / "01-a.md", "a\n")
        _write(tmp_path / "README.md", "r\n")
        _write(tmp_path / "AGENTS.md", "[gone](docs/99-missing.md)\n")
        _git_init(tmp_path, commit=False)
        assert cdl.doc_files(tmp_path) == []


class TestExitCodes:
    def test_no_markdown_is_an_operator_error(self, tmp_path: Path, capsys):
        assert cdl.main(["check-doc-links.py", str(tmp_path)]) == 2
        assert "no Markdown files found" in capsys.readouterr().err

    def test_enumeration_failure_is_reported_not_raised(self, tmp_path: Path, capsys, monkeypatch):
        def boom(root):
            raise RuntimeError(f"cannot enumerate tracked Markdown under {root}: git")

        monkeypatch.setattr(cdl, "_tracked_markdown", boom)
        _git_init(tmp_path, commit=False)
        assert cdl.main(["check-doc-links.py", str(tmp_path)]) == 2
        assert "cannot enumerate tracked Markdown" in capsys.readouterr().err

    def test_broken_link_exits_one(self, tmp_path: Path):
        _write(tmp_path / "docs" / "01-a.md", "[gone](99-missing.md)\n")
        assert cdl.main(["check-doc-links.py", str(tmp_path)]) == 1


class TestBacktickedPaths:
    def test_resolving_repo_path_passes(self, tmp_path: Path):
        _write(tmp_path / "scripts" / "tool.sh", "#!/bin/sh\n")
        src = _write(tmp_path / "docs" / "01-a.md", "run `scripts/tool.sh` first\n")
        assert cdl.rotted_paths([src], tmp_path) == []

    def test_rotted_repo_path_is_reported(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        src = _write(tmp_path / "docs" / "01-a.md", "run `scripts/gone.sh` first\n")
        assert cdl.rotted_paths([src], tmp_path) == [(src, "scripts/gone.sh")]

    def test_directory_token_resolves(self, tmp_path: Path):
        (tmp_path / "scripts" / "lib").mkdir(parents=True)
        src = _write(tmp_path / "docs" / "01-a.md", "see `scripts/lib/`\n")
        assert cdl.rotted_paths([src], tmp_path) == []

    def test_foreign_and_placeholder_tokens_are_left_alone(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        src = _write(
            tmp_path / "docs" / "01-a.md",
            "`/etc/hosts` `../elsewhere/x.yml` `scripts/<name>.sh` "
            "`kubernetes/apps/x.yaml` `a b/c`\n",
        )
        assert cdl.rotted_paths([src], tmp_path) == []

    def test_a_path_relative_to_the_citing_document_resolves(self, tmp_path: Path):
        _write(tmp_path / "docs" / "ref" / "detail.md", "# d\n")
        src = _write(tmp_path / "docs" / "01-a.md", "see `ref/detail.md`\n")
        assert cdl.rotted_paths([src], tmp_path) == []

    def test_a_path_resolving_under_neither_base_is_reported(self, tmp_path: Path):
        (tmp_path / "docs" / "ref").mkdir(parents=True)
        src = _write(tmp_path / "docs" / "01-a.md", "see `ref/gone.md`\n")
        assert cdl.rotted_paths([src], tmp_path) == [(src, "ref/gone.md")]

    def test_ignore_patterns_exempt_a_token(self, tmp_path: Path, monkeypatch):
        (tmp_path / "scripts").mkdir()
        src = _write(tmp_path / "docs" / "01-a.md", "`scripts/consumer-only.yml`\n")
        monkeypatch.setenv("CHECK_DOC_LINKS_IGNORE", "scripts/consumer-*.yml")
        assert cdl.rotted_paths([src], tmp_path) == []

    def test_pass_is_opt_in(self, tmp_path: Path, monkeypatch):
        (tmp_path / "scripts").mkdir()
        _write(tmp_path / "docs" / "01-a.md", "`scripts/gone.sh`\n")
        monkeypatch.delenv("CHECK_DOC_LINKS_PATHS", raising=False)
        assert cdl.main(["check-doc-links.py", str(tmp_path)]) == 0
        monkeypatch.setenv("CHECK_DOC_LINKS_PATHS", "1")
        assert cdl.main(["check-doc-links.py", str(tmp_path)]) == 1


class TestSectionCitations:
    """`<doc> § <Heading>` pointers resolve against the real headings."""

    def _tree(self, tmp_path: Path, citing: str) -> Path:
        _write(tmp_path / "docs" / "07-flux.md", "# Flux\n\n## Rotating a secret\n")
        return _write(tmp_path / "docs" / "01-a.md", citing)

    def test_a_citation_naming_a_real_heading_passes(self, tmp_path: Path):
        src = self._tree(tmp_path, "see `docs/07-flux.md` § Rotating a secret.\n")
        assert cdl.dangling_sections([src], tmp_path) == []

    def test_a_citation_naming_a_heading_that_does_not_exist_fails(self, tmp_path: Path):
        """The rot this arm exists for: the heading was renamed, the pointer was
        not, and the link arm never looks past the filename."""
        src = self._tree(tmp_path, "see `docs/07-flux.md` § Rotating a token.\n")
        found = cdl.dangling_sections([src], tmp_path)
        assert len(found) == 1
        assert found[0][2] == "Rotating a token."
        assert "rotating a secret" in found[0][3]

    def test_a_numbered_prefix_resolves_to_the_document(self, tmp_path: Path):
        src = self._tree(tmp_path, "see (docs/07 § Rotating a token)\n")
        assert len(cdl.dangling_sections([src], tmp_path)) == 1

    def test_prose_continuing_past_the_heading_is_not_part_of_it(self, tmp_path: Path):
        src = self._tree(
            tmp_path, "see `docs/07-flux.md` § Rotating a secret, then redeploy.\n"
        )
        assert cdl.dangling_sections([src], tmp_path) == []

    def test_a_heading_cut_short_by_a_line_wrap_still_resolves(self, tmp_path: Path):
        src = self._tree(tmp_path, "see `docs/07-flux.md` § Rotating a\nsecret now.\n")
        assert cdl.dangling_sections([src], tmp_path) == []

    def test_a_bare_word_is_not_treated_as_a_document(self, tmp_path: Path):
        """"the role README § Metrics" names no path, so there is nothing to
        resolve and prose cannot red the gate."""
        src = self._tree(tmp_path, "see the role README § Metrics.\n")
        assert cdl.dangling_sections([src], tmp_path) == []

    def test_an_unresolvable_document_is_left_alone(self, tmp_path: Path):
        src = self._tree(tmp_path, "see `docs/99-gone.md` § Anything.\n")
        assert cdl.dangling_sections([src], tmp_path) == []

    def test_a_template_source_resolves_before_the_repos_own_doc(self, tmp_path: Path):
        """A template repo carries its own docs and a generated repo's; the
        nearest tree wins, so the two do not cross over."""
        _write(tmp_path / "docs" / "runbooks.md", "# Runbooks\n")
        _write(
            tmp_path / "template" / "docs" / "runbooks.md.jinja",
            "# Runbooks\n\n## Registry pull-through cache\n",
        )
        src = _write(
            tmp_path / "template" / "apps" / "r.md",
            "see `docs/runbooks.md` § Registry pull-through cache.\n",
        )
        assert cdl.dangling_sections([src], tmp_path) == []

    def test_prose_running_on_without_punctuation_still_resolves(self, tmp_path: Path):
        """Nothing bounds the citation, so only the words it shares with the
        heading can identify the section."""
        _write(
            tmp_path / "docs" / "07-flux.md",
            "# Flux\n\n## Rotating a secret: the ordered path\n",
        )
        src = _write(
            tmp_path / "docs" / "01-a.md",
            "see `docs/07-flux.md` § Rotating a secret needs the token first\n",
        )
        assert cdl.dangling_sections([src], tmp_path) == []

    def test_two_shared_words_are_not_enough(self, tmp_path: Path):
        """`Rotating a token` shares `rotating a` with `Rotating a secret`;
        accepting that would let every renamed section through."""
        src = self._tree(tmp_path, "see `docs/07-flux.md` § Rotating a token now\n")
        assert len(cdl.dangling_sections([src], tmp_path)) == 1

    def test_a_dangling_citation_exits_one(self, tmp_path: Path):
        self._tree(tmp_path, "see `docs/07-flux.md` § Rotating a token.\n")
        assert cdl.main(["check-doc-links.py", str(tmp_path)]) == 1

    def test_the_arm_can_be_turned_off(self, tmp_path: Path, monkeypatch):
        """A template repo whose citations point at the generated tree opts out
        rather than rewording every pointer."""
        self._tree(tmp_path, "see `docs/07-flux.md` § Rotating a token.\n")
        monkeypatch.setenv("CHECK_DOC_LINKS_SECTIONS", "0")
        assert cdl.main(["check-doc-links.py", str(tmp_path)]) == 0


class TestRealRepo:
    def test_repo_docs_have_no_broken_links(self):
        # The gate must be green on the tree it ships with (preventive check).
        files = cdl.doc_files(REPO)
        assert files, "expected repo docs to be discovered"
        broken = cdl.broken_links(files)
        assert broken == [], f"broken links in repo docs: {broken}"
