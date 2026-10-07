#!/usr/bin/env python3
"""Tests for scripts/check-runbook-anchors.py.

Each case is the mistake the gate must not make: a dangling anchor anywhere in
the observability tree, including a ruler's rule file outside `rules/`.
"""
from __future__ import annotations

import pytest
from script_loader import load_script

mod = load_script("check-runbook-anchors.py")

DOC = """\
# Runbooks

## Node is down

Text.

## Node is down

A repeated heading, so this one slugs to `node-is-down-1`.

```
## Fenced Heading
```
"""

BASE = mod.BASE_PLACEHOLDER


def _tree(tmp_path, rules_rel: str, url: str, doc: str = DOC):
    rules = tmp_path / "observability"
    docs = tmp_path / "docs"
    target = rules / rules_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "groups:\n"
        "  - name: g\n"
        "    rules:\n"
        "      - alert: A\n"
        "        annotations:\n"
        f"          runbook_url: {url}\n"
    )
    docs.mkdir(exist_ok=True)
    (docs / "12-runbooks.md").write_text(doc)
    return rules, docs


class TestSlugAndAnchors:
    def test_inline_markup_is_dropped(self):
        assert mod.slug("The `foo` *bar* step") == "the-foo-bar-step"

    def test_a_markdown_link_keeps_only_its_text(self):
        assert mod.slug("See [docs](x.md) here") == "see-docs-here"

    def test_a_repeated_heading_gets_a_numeric_suffix(self, tmp_path):
        doc = tmp_path / "d.md"
        doc.write_text(DOC)
        found = mod.anchors(doc)
        assert {"node-is-down", "node-is-down-1"} <= found

    def test_a_fenced_heading_is_not_an_anchor(self, tmp_path):
        doc = tmp_path / "d.md"
        doc.write_text(DOC)
        assert "fenced-heading" not in mod.anchors(doc)


class TestDangling:
    def test_a_resolving_anchor_is_clean(self, tmp_path):
        rules, docs = _tree(
            tmp_path, "rules/node.yaml", f"{BASE}12-runbooks.md#node-is-down"
        )
        assert mod.dangling(rules, docs) == []

    def test_a_dangling_anchor_outside_rules_is_reported(self, tmp_path):
        """The widening that matters: a Loki ruler's rules live under loki/."""
        rules, docs = _tree(
            tmp_path, "loki/rules/alerts.yaml", f"{BASE}12-runbooks.md#no-such-section"
        )
        problems = mod.dangling(rules, docs)
        assert len(problems) == 1
        assert "no section anchored #no-such-section" in problems[0]
        assert "loki/rules/alerts.yaml" in problems[0]

    def test_a_missing_doc_is_reported(self, tmp_path):
        rules, docs = _tree(tmp_path, "loki/alerts.yml", f"{BASE}99-absent.md#x")
        assert "does not exist" in mod.dangling(rules, docs)[0]

    def test_a_url_without_the_placeholder_is_reported(self, tmp_path):
        rules, docs = _tree(tmp_path, "rules/node.yaml", "docs/12-runbooks.md#node-is-down")
        assert "must start with" in mod.dangling(rules, docs)[0]

    def test_an_external_url_is_skipped(self, tmp_path):
        rules, docs = _tree(tmp_path, "rules/node.yaml", "https://example.invalid/runbook")
        assert mod.dangling(rules, docs) == []

    def test_a_bare_doc_with_no_anchor_is_clean(self, tmp_path):
        rules, docs = _tree(tmp_path, "rules/node.yaml", f"{BASE}12-runbooks.md")
        assert mod.dangling(rules, docs) == []


class TestMain:
    def _argv(self, rules, docs, *extra):
        return ["check-runbook-anchors.py", "--rules-dir", str(rules), "--docs-dir", str(docs),
                *extra]

    def test_a_clean_tree_exits_zero(self, tmp_path, capsys):
        rules, docs = _tree(tmp_path, "loki/alerts.yaml", f"{BASE}12-runbooks.md#node-is-down")
        assert mod.main(self._argv(rules, docs)) == 0
        assert "resolve to a real section" in capsys.readouterr().out

    def test_a_dangling_anchor_exits_one(self, tmp_path, capsys):
        rules, docs = _tree(tmp_path, "loki/alerts.yaml", f"{BASE}12-runbooks.md#gone")
        assert mod.main(self._argv(rules, docs)) == 1
        assert "do not resolve" in capsys.readouterr().out

    def test_a_tree_with_no_annotation_is_an_operator_error(self, tmp_path, capsys):
        rules, docs = _tree(tmp_path, "rules/node.yaml", f"{BASE}12-runbooks.md")
        (rules / "rules/node.yaml").write_text("groups: []\n")
        assert mod.main(self._argv(rules, docs)) == 2
        assert "no runbook_url annotations found" in capsys.readouterr().err

    def test_a_missing_rules_dir_is_an_operator_error(self, tmp_path, capsys):
        _, docs = _tree(tmp_path, "rules/node.yaml", f"{BASE}12-runbooks.md")
        assert mod.main(self._argv(tmp_path / "absent", docs)) == 2
        assert "rules directory not found" in capsys.readouterr().err

    def test_a_missing_docs_dir_is_an_operator_error(self, tmp_path, capsys):
        rules, _ = _tree(tmp_path, "rules/node.yaml", f"{BASE}12-runbooks.md")
        assert mod.main(self._argv(rules, tmp_path / "absent")) == 2
        assert "docs directory not found" in capsys.readouterr().err

    def test_a_custom_placeholder_is_honoured(self, tmp_path):
        rules, docs = _tree(tmp_path, "rules/node.yaml", "${runbooks}/12-runbooks.md#gone")
        argv = self._argv(rules, docs, "--base-placeholder", "${runbooks}/")
        assert mod.main(argv) == 1


@pytest.mark.parametrize("extension", ["yaml", "yml"])
def test_both_yaml_extensions_are_scanned(tmp_path, extension):
    rules, docs = _tree(tmp_path, f"loki/alerts.{extension}", f"{BASE}12-runbooks.md#gone")
    assert len(mod.dangling(rules, docs)) == 1


class TestSectionPointers:
    """`(docs/NN § Heading)` pointers inside an alert's own text."""

    def _tree(self, tmp_path, description: str):
        rules = tmp_path / "observability" / "rules"
        rules.mkdir(parents=True)
        (rules / "node.yaml").write_text(
            "groups:\n"
            "  - name: g\n"
            "    rules:\n"
            "      - alert: NodeDown\n"
            "        annotations:\n"
            f"          runbook_url: {BASE}12-runbooks.md#node-is-down\n"
            f"          description: >-\n            {description}\n"
        )
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "12-runbooks.md").write_text(DOC)
        return tmp_path / "observability", docs

    def test_a_pointer_naming_a_real_heading_passes(self, tmp_path):
        rules, docs = self._tree(tmp_path, "drain it first (docs/12 § Node is down).")
        assert mod.dangling_sections(rules, docs) == []

    def test_a_pointer_naming_a_missing_heading_fails_with_the_alertname(self, tmp_path):
        """The rot this arm exists for: the heading was renamed and the pointer
        in the alert text was not, with nothing red."""
        rules, docs = self._tree(tmp_path, "drain it first (docs/12 § Node is sad).")
        problems = mod.dangling_sections(rules, docs)
        assert len(problems) == 1
        assert "NodeDown" in problems[0]
        assert "Node is sad" in problems[0]
        assert "12-runbooks.md" in problems[0]

    def test_a_pointer_wrapped_across_lines_resolves(self, tmp_path):
        rules, docs = self._tree(
            tmp_path, "drain it first (docs/12 §Node\n            is down)."
        )
        assert mod.dangling_sections(rules, docs) == []

    def test_a_pointer_at_a_document_that_does_not_exist_is_left_alone(self, tmp_path):
        rules, docs = self._tree(tmp_path, "see (docs/99 § Anything).")
        assert mod.dangling_sections(rules, docs) == []

    def test_a_missing_section_exits_one(self, tmp_path, capsys):
        rules, docs = self._tree(tmp_path, "drain it first (docs/12 § Node is sad).")
        argv = ["check-runbook-anchors.py", "--rules-dir", str(rules),
                "--docs-dir", str(docs)]
        assert mod.main(argv) == 1
        assert "name a missing section" in capsys.readouterr().out
