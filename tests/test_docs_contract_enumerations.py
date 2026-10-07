#!/usr/bin/env python3
"""Enumerations in docs/ that restate a fact the tree already holds.

Each case re-derives the list and fails when the prose drifts.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Dict, Set

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CI = REPO / "ci"
INCLUDE_CONTRACT = REPO / "docs" / "INCLUDE-CONTRACT.md"

sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import NullTagCILoader as CILoader  # noqa: E402


def load_comment_length_gate():
    """The gate ships as a hyphenated filename, so import it by path."""
    path = REPO / "scripts" / "check-comment-length.py"
    spec = importlib.util.spec_from_file_location("check_comment_length", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def excluded_stems() -> Set[str]:
    """DEFAULT_EXCLUDES as the names the docs spell, e.g. `.tmp`, `*.egg-info`."""
    return {pattern.rsplit("/", 1)[0]
            for pattern in load_comment_length_gate().DEFAULT_EXCLUDES}


# The sentence in each doc that enumerates the always-excluded set.
EXCLUDE_SENTENCES = {
    "docs/SCRIPTS.md": r"Flags win over the config;.*?repo did not author\.",
    "docs/INCLUDE-CONTRACT.md":
        r"- It excludes tool caches and scratch trees on its own.*?normal setting\.",
}


def missing_from_sentence(text: str, pattern: str) -> Set[str]:
    """Excluded names the matched sentence does not spell."""
    match = re.search(pattern, text, re.S)
    assert match, "the always-excluded sentence is gone or reworded: %s" % pattern
    body = match.group(0)
    return {stem for stem in excluded_stems() if "`%s`" % stem not in body}


def test_the_default_excludes_set_is_not_empty() -> None:
    """Vacuity guard: an empty set would pass every case below."""
    assert len(excluded_stems()) > 5


@pytest.mark.parametrize("relative_path", sorted(EXCLUDE_SENTENCES))
def test_each_doc_enumerates_every_default_exclude(relative_path: str) -> None:
    """A doc that omits one reads as a gate that walks that tree."""
    missing = missing_from_sentence(
        (REPO / relative_path).read_text(), EXCLUDE_SENTENCES[relative_path])
    assert not missing, (
        "%s's always-excluded list omits %s, which check-comment-length.py's "
        "DEFAULT_EXCLUDES holds." % (relative_path, ", ".join(sorted(missing)))
    )


def test_a_doc_that_drops_an_exclude_is_reported() -> None:
    """The gate can FAIL: a sentence missing `.tmp` and `.worktrees`."""
    text = ("- It excludes tool caches and scratch trees on its own (`.git`, "
            "`.venv`) and skips any suffix it has no comment syntax for, so "
            "`paths: \".\"` is the normal setting.\n")
    missing = missing_from_sentence(
        text, EXCLUDE_SENTENCES["docs/INCLUDE-CONTRACT.md"])
    assert {".tmp", ".worktrees"} <= missing


def template_inputs() -> Dict[str, Dict[str, object]]:
    """Consumer-facing template path -> its declared `spec:inputs`."""
    found = {}
    for path in sorted({p for suffix in ("yml", "yaml")
                        for p in CI.rglob("*.%s" % suffix)}):
        relative = path.relative_to(REPO).as_posix()
        if relative.startswith("ci/internal/") or ".example." in path.name:
            continue
        documents = list(yaml.load_all(path.read_text(), Loader=CILoader))
        spec = (documents[0] or {}).get("spec") if documents else None
        found[relative] = (spec or {}).get("inputs") or {}
    return found


RESOURCE_INPUTS = ("job_memory_limit", "job_memory_request", "job_cpu_request")


def section_body(text: str, heading: str) -> str:
    match = re.search(r"^## %s\n(.*?)(?=^## )" % re.escape(heading),
                      text, re.S | re.M)
    assert match, "docs/INCLUDE-CONTRACT.md has no `## %s` section" % heading
    return match.group(1)


def resource_input_claim(text: str) -> tuple:
    """The section's stated count, the stems in its default table, and the
    template paths it says take none of the three."""
    body = section_body(text, "Resource inputs")
    count = re.search(r"That is (\d+) templates\.", body)
    assert count, "the `## Resource inputs` count sentence is gone or reworded"
    table = re.findall(r"^\| \d.*?\| (.*?) \|$", body, re.M)
    stems = {name.strip() for row in table for name in row.split(",")}
    sentence = re.search(r"take none of the three[^:]*:(.*?)\. Passing", body, re.S)
    exempt = set(re.findall(r"`(ci/[\w./-]+\.ya?ml)`",
                            sentence.group(1) if sentence else ""))
    return int(count.group(1)), stems, exempt


def test_the_resource_inputs_section_matches_the_templates() -> None:
    """A template left out reads as running at the runner default, and one
    wrongly listed as exempt sends a consumer into an unknown-input failure.
    """
    declared = {relative for relative, inputs in template_inputs().items()
                if "job_memory_limit" in inputs}
    count, stems, exempt = resource_input_claim(INCLUDE_CONTRACT.read_text())
    assert count == len(declared), (
        "the section says %d templates take the resource inputs; %d do"
        % (count, len(declared))
    )
    assert {Path(relative).stem for relative in declared} <= stems, (
        "the per-class default table omits %s"
        % sorted({Path(r).stem for r in declared} - stems)
    )
    for relative in sorted(exempt):
        inputs = template_inputs().get(relative)
        assert inputs is not None, (
            "the section names %s, which this repo does not ship" % relative)
        taken = [name for name in RESOURCE_INPUTS if name in inputs]
        assert not taken, (
            "the section lists %s as taking none of the resource inputs, but it "
            "declares %s" % (relative, ", ".join(taken))
        )


def test_a_stale_resource_inputs_count_is_reported() -> None:
    """The gate can FAIL: a count and a table that have fallen behind."""
    text = ("## Resource inputs\n\nThat is 2 templates.\n\n"
            "| Limit | Templates |\n| --- | --- |\n| 512Mi | yaml-lint |\n\n"
            "## Next\n")
    count, stems, exempt = resource_input_claim(text)
    declared = {relative for relative, inputs in template_inputs().items()
                if "job_memory_limit" in inputs}
    assert (count, stems, exempt) == (2, {"yaml-lint"}, set())
    assert count != len(declared)


LEGEND = r"○ = extracted here.*?(?=\n\n)"


def unadopted_paths(text: str) -> Set[str]:
    """Template paths the adoption table marks ○ in at least one column."""
    table = re.search(r"^\| Template \| lib \(self\).*?(?=\n\n)",
                      text, re.S | re.M)
    assert table, "the 'Who includes what' table is gone or reworded"
    out = set()
    for row in table.group(0).splitlines():
        if "○" not in row:
            continue
        for prefix, group, suffix in re.findall(
                r"`(ci/[\w./-]*?)\{([\w,.-]+)\}([\w./-]*)`", row):
            out.update(prefix + name + suffix for name in group.split(","))
        out.update(re.findall(r"`(ci/[\w./-]+\.ya?ml)`", row))
    return out


def test_the_legend_accounts_for_every_unadopted_row() -> None:
    """The legend reads as the complete explanation of the ○ marks, so a row it
    never mentions looks like an oversight rather than recorded state.
    """
    text = INCLUDE_CONTRACT.read_text()
    legend = re.search(LEGEND, text, re.S)
    assert legend, "the ○ legend is gone or reworded"
    missing = sorted(relative for relative in unadopted_paths(text)
                     if Path(relative).stem not in legend.group(0))
    assert not missing, (
        "docs/INCLUDE-CONTRACT.md's ○ legend never mentions %s. Say why each is "
        "unadopted, or correct the table." % ", ".join(missing)
    )


def test_a_legend_missing_a_row_is_reported() -> None:
    """The gate can FAIL: a ○ row the legend does not name."""
    text = ("| Template | lib (self) | weisssrv |\n| --- | :-: | :-: |\n"
            "| [`ci/lint/one.yml`](#a) | ● | ● |\n"
            "| [`ci/lint/two.yml`](#b) | | ○ |\n\n"
            "○ = extracted here, not yet adopted: nothing takes `one` yet.\n\n"
            "trailing prose\n")
    legend = re.search(LEGEND, text, re.S)
    assert unadopted_paths(text) == {"ci/lint/two.yml"}
    assert "two" not in legend.group(0)
