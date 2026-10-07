#!/usr/bin/env python3
"""Every CI template input is named in docs/INCLUDE-CONTRACT.md, either in a
per-template table row or under "Conventions shared by every template".
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "INCLUDE-CONTRACT.md"
CI = REPO / "ci"

sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import NullTagCILoader as CILoader  # noqa: E402

# Templates without a heading of their own are documented in a grouped section.
GROUP_SECTIONS = {
    "ci/templates/": "Shared fragments (ci/templates/)",
    "ci/deploy/": "Deploy templates (ci/deploy/)",
    "ci/github/": "GitHub workflow examples (ci/github/, ci/release/)",
}
SHARED_SECTIONS = ("Conventions shared by every template", "Resource inputs")


def sections(text: str) -> Dict[str, str]:
    """`## heading` -> that section's body."""
    out = {}
    for part in re.split(r"^## ", text, flags=re.M)[1:]:
        out[part.splitlines()[0].strip()] = part
    return out


def owning_section(relative_path: str, known: Dict[str, str]) -> str:
    """The heading that must carry this template's input rows."""
    if relative_path in known:
        return relative_path
    for prefix, heading in GROUP_SECTIONS.items():
        if relative_path.startswith(prefix):
            return heading
    return relative_path


def templates() -> Dict[str, Dict[str, object]]:
    """Consumer-facing templates -> their declared inputs.

    `ci/internal/` carries no input-stability guarantee and the `*.example.*`
    files are vendored Actions workflows, not includes.
    """
    found = {}
    # Both spellings: a template added as `.yaml` would otherwise slip out of
    # this gate silently.
    for path in sorted({p for suffix in ("yml", "yaml") for p in CI.rglob(f"*.{suffix}")}):
        relative = path.relative_to(REPO).as_posix()
        if relative.startswith("ci/internal/") or ".example." in path.name:
            continue
        documents = list(yaml.load_all(path.read_text(), Loader=CILoader))
        spec = (documents[0] or {}).get("spec") if documents else None
        found[relative] = ((spec or {}).get("inputs") or {})
    return found


def test_there_are_templates_to_check() -> None:
    """Vacuity guard: a glob that matched nothing would pass every case below."""
    assert len(templates()) > 10


def undocumented_inputs(relative_path: str, inputs, doc_text: str):
    """Input names missing from this template's own section and the shared ones."""
    known = sections(doc_text)
    heading = owning_section(relative_path, known)
    if heading not in known:
        return None, heading
    body = known[heading] + "".join(known.get(s, "") for s in SHARED_SECTIONS)
    return sorted(name for name in inputs if "`%s`" % name not in body), heading


@pytest.mark.parametrize("relative_path", sorted(templates()))
def test_every_input_is_documented(relative_path: str) -> None:
    doc_text = DOC.read_text()
    undocumented, heading = undocumented_inputs(
        relative_path, templates()[relative_path], doc_text)
    assert undocumented is not None, (
        "docs/INCLUDE-CONTRACT.md has no `## %s` section for %s. Add one, or "
        "group the template under an existing heading."
        % (heading, relative_path)
    )
    assert not undocumented, (
        "%s declares inputs that its `## %s` section never names: %s. Add a row "
        "there, or — for an input every template takes — to 'Conventions shared "
        "by every template'."
        % (relative_path, heading, ", ".join(undocumented))
    )


def test_an_input_documented_only_in_another_section_is_reported(tmp_path) -> None:
    """The gate can FAIL: a name that collides with another template's input."""
    doc_text = (
        "## ci/lint/one.yml\n\n| `image` | ... |\n\n"
        "## ci/lint/two.yml\n\n| `stage` | ... |\n"
    )
    undocumented, _ = undocumented_inputs(
        "ci/lint/two.yml", {"image": {}, "stage": {}}, doc_text)
    assert undocumented == ["image"]


ADOPTION_TABLE = r"^\| Template \| lib \(self\).*?(?=\n\n)"


def adopted_paths(doc_text: str) -> set:
    """Template paths named in the "Who includes what" table.

    One row may group several templates with a brace list, e.g.
    `ci/templates/{dep-cache,install-1password}.yml`.
    """
    table = re.search(ADOPTION_TABLE, doc_text, re.S | re.M)
    if not table:
        return set()
    out = set()
    for prefix, group, suffix in re.findall(
            r"`(ci/[\w./-]*?)\{([\w,.-]+)\}([\w./-]*)`", table.group(0)):
        out.update(prefix + name + suffix for name in group.split(","))
    out.update(re.findall(r"`(ci/[\w./-]+\.ya?ml)`", table.group(0)))
    return out


@pytest.mark.parametrize("relative_path", sorted(templates()))
def test_every_template_has_an_adoption_row(relative_path: str) -> None:
    """A template absent from the table has no recorded adoption state, so its
    defaults can be changed blind.
    """
    assert relative_path in adopted_paths(DOC.read_text()), (
        "docs/INCLUDE-CONTRACT.md's 'Who includes what' table has no row for "
        "%s. Add one (● adopted, ○ extracted but unadopted, blank for "
        "not applicable) so its defaults have a recorded blast radius."
        % relative_path
    )


def test_a_template_missing_from_the_adoption_table_is_reported() -> None:
    """The gate can FAIL: a table that omits a shipped template."""
    doc_text = (
        "| Template | lib (self) | weisssrv |\n| --- | :-: | :-: |\n"
        "| [`ci/lint/one.yml`](#x) | ● | ● |\n"
        "| [`ci/templates/{two,three}.yml`](#y) | | ○ |\n\n"
        "trailing prose\n"
    )
    found = adopted_paths(doc_text)
    assert found == {"ci/lint/one.yml", "ci/templates/two.yml",
                     "ci/templates/three.yml"}
    assert "ci/lint/four.yml" not in found


# The convention bullet names each template by file stem, e.g. `yaml-lint`.
DEFAULT_BRANCH_BULLET = re.compile(
    r"\*\*`default_branch`[^\n]*\*\*.*?\((\d+) of them:\s*(.*?)\)", re.S)


def declared_default_branch_names(text: str) -> tuple:
    """The count and the template stems the `default_branch` bullet lists."""
    match = DEFAULT_BRANCH_BULLET.search(text)
    assert match, "the default_branch convention bullet is gone or reworded"
    listed = {name.strip()
              for name in re.split(r",\s*", match.group(2).replace("\n", " "))
              if name.strip()}
    return int(match.group(1)), listed


def templates_taking_default_branch() -> set:
    return {Path(relative).stem
            for relative, inputs in templates().items()
            if "default_branch" in inputs}


def test_the_default_branch_bullet_lists_every_template_that_takes_it() -> None:
    """A template left out of the list reads as not needing the input, so a
    consumer on `master`/`trunk` gets a job that stops running after merge.
    """
    listed_count, listed = declared_default_branch_names(DOC.read_text())
    actual = templates_taking_default_branch()
    assert listed == actual, (
        "docs/INCLUDE-CONTRACT.md's `default_branch` convention bullet is "
        "stale. Missing: %s. Listed but no longer declaring the input: %s."
        % (sorted(actual - listed) or "none", sorted(listed - actual) or "none")
    )
    assert listed_count == len(actual), (
        "the bullet says %d templates but lists %d"
        % (listed_count, len(actual))
    )


def test_a_short_default_branch_list_is_reported() -> None:
    """The gate can FAIL: a bullet whose list omits a template that takes it."""
    listed_count, listed = declared_default_branch_names(
        "- **`default_branch` (string, default `main`)** — the branch the "
        "post-merge rule compares against (2 of them:\n  yaml-lint, "
        "shellcheck).\n"
    )
    actual = templates_taking_default_branch()
    assert (listed_count, listed) == (2, {"yaml-lint", "shellcheck"})
    assert listed != actual and "docker-build" in actual - listed
