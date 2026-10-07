"""A role default dropped or flipped since the last release is named in MIGRATING.md.

Removals plus scalar and container defaults, except version/image pins and Jinja
renders, whose values move on every bump.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest
import yaml
from _helpers import git, newest_release_tag, release_tags, require_git_checkout, require_tags

REPO = Path(__file__).resolve().parent.parent
COLLECTION = REPO / "ansible_collections" / "weisssrv" / "infra"
MIGRATING = COLLECTION / "MIGRATING.md"
ROLES = COLLECTION / "roles"
ROLE_PREFIX = "ansible_collections/weisssrv/infra/roles/"


def _values_at(tag: str) -> dict[str, dict]:
    """Whole `defaults/main.yml` mappings per role, as of one git tag."""
    paths = [
        path
        for path in git(REPO, "ls-tree", "-r", "--name-only", tag).splitlines()
        if path.startswith(ROLE_PREFIX) and path.endswith("/defaults/main.yml")
    ]
    out = {}
    for path in paths:
        role = path[len(ROLE_PREFIX):].split("/")[0]
        out[role] = yaml.safe_load(git(REPO, "show", f"{tag}:{path}")) or {}
    return out


def _values_now() -> dict[str, dict]:
    """The same, from the working tree, so an uncommitted change is caught."""
    return {
        path.parent.parent.name: yaml.safe_load(path.read_text()) or {}
        for path in sorted(ROLES.glob("*/defaults/main.yml"))
    }


@functools.lru_cache(maxsize=2)
def _defaults_at(tag: str) -> dict[str, frozenset[str]]:
    """Top-level `defaults/main.yml` keys per role, as of one git tag."""
    return {role: frozenset(values) for role, values in _values_at(tag).items()}


def _defaults_now() -> dict[str, frozenset[str]]:
    return {role: frozenset(values) for role, values in _values_now().items()}


def _mask_fences(text: str) -> str:
    """Same text, length preserved, with `#` comments inside ``` fences blanked.

    A YAML comment in an example block otherwise reads as a heading and truncates
    the section it sits in.
    """
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
        out.append(" " + line[1:] if in_fence and line.startswith("#") else line)
    return "\n".join(out)


def _sections(text: str) -> dict[str, str]:
    heads = list(re.finditer(r"(?m)^# (.+)$", _mask_fences(text)))
    return {
        head.group(1).strip(): text[head.end(): (
            heads[i + 1].start() if i + 1 < len(heads) else len(text))]
        for i, head in enumerate(heads)
    }


def current_migration_text(text: str) -> str:
    """The open `Unreleased` body plus the newest titled release section."""
    sections = _sections(text)
    titled = [name for name in sections if re.fullmatch(r"v\d+\.\d+\.\d+", name)]
    body = sections.get("Unreleased (next release)", "")
    return body + (sections[titled[0]] if titled else "")


def missing_entries(baseline, current, migration_text: str) -> list[str]:
    """Keys a role dropped since the baseline that the migration text ignores."""
    missing = []
    for role, keys in sorted(baseline.items()):
        for key in sorted(keys - set(current.get(role, ()))):
            if not re.search(r"\b%s\b" % re.escape(key), migration_text):
                missing.append(f"{role}: {key}")
    return missing


# A pin bump is not a behaviour change, and a Jinja default renders per host.
PIN_SUFFIXES = ("_version", "_sha", "_checksum", "_digest", "_image", "_tag")


def flipped_entries(baseline, current, migration_text: str) -> list[str]:
    """Defaults whose value changed since the baseline unnamed in the text."""
    flipped = []
    for role, values in sorted(baseline.items()):
        now = current.get(role) or {}
        for key, old in sorted(values.items()):
            if key not in now or key.endswith(PIN_SUFFIXES):
                continue
            new = now[key]
            if repr(old) == repr(new) or "{{" in repr(old) + repr(new):
                continue
            if not re.search(r"\b%s\b" % re.escape(key), migration_text):
                flipped.append(f"{role}: {key}")
    return flipped


@pytest.fixture(scope="module")
def baseline_tag() -> str:
    require_git_checkout(REPO)
    tags = release_tags(REPO)
    require_tags(tags, "MIGRATING-entry")
    return newest_release_tag(tags)


@pytest.fixture(scope="module")
def baseline(baseline_tag: str) -> dict[str, frozenset[str]]:
    return _defaults_at(baseline_tag)


@pytest.fixture(scope="module")
def baseline_values(baseline_tag: str) -> dict[str, dict]:
    return _values_at(baseline_tag)


class TestRemovedDefaultsAreMigrated:
    """Every role variable removed since the last release has a MIGRATING entry."""

    def test_the_baseline_is_not_empty(self, baseline):
        roles, keys = len(baseline), sum(len(v) for v in baseline.values())
        assert roles >= 30 and keys >= 200, (
            "the baseline tag yielded %d roles / %d default keys, too few to be "
            "real — this gate would pass vacuously" % (roles, keys)
        )

    def test_every_removed_default_is_named(self, baseline):
        missing = missing_entries(
            baseline, _defaults_now(), current_migration_text(MIGRATING.read_text())
        )
        assert not missing, (
            "these role defaults were removed since the last release and no "
            "MIGRATING.md entry names them — add one under the newest titled "
            "section or `# Unreleased (next release)`, saying what a consumer "
            "does instead: %s" % missing
        )

    def test_every_flipped_default_is_named(self, baseline_values):
        flipped = flipped_entries(
            baseline_values,
            _values_now(),
            current_migration_text(MIGRATING.read_text()),
        )
        assert not flipped, (
            "these role defaults changed value since the last release and no "
            "MIGRATING.md entry names them — add one saying what a consumer "
            "sets to keep today's behaviour: %s" % flipped
        )

    def test_an_unnamed_removal_is_reported(self):
        baseline = {"widget": frozenset({"widget_enabled", "widget_gone"})}
        current = {"widget": frozenset({"widget_enabled"})}
        assert missing_entries(baseline, current, "Nothing yet.\n") == ["widget: widget_gone"]
        assert missing_entries(baseline, current, "`widget_gone` is removed.\n") == []

    def test_an_unnamed_flip_is_reported(self):
        baseline = {"widget": {"widget_flag": True}}
        current = {"widget": {"widget_flag": False}}
        assert flipped_entries(baseline, current, "Nothing yet.\n") == ["widget: widget_flag"]
        assert flipped_entries(
            baseline, current, "`widget_flag` now defaults to false.\n"
        ) == []

    def test_an_unnamed_string_flip_is_reported(self):
        baseline = {"widget": {"widget_bind": "0.0.0.0"}}
        current = {"widget": {"widget_bind": "127.0.0.1"}}
        assert flipped_entries(baseline, current, "Nothing yet.\n") == ["widget: widget_bind"]
        assert flipped_entries(
            baseline, current, "`widget_bind` now listens on localhost.\n"
        ) == []

    def test_a_pin_bump_is_not_reported(self):
        baseline = {"widget": {"widget_version": "1.2.3", "widget_image": "a:1"}}
        current = {"widget": {"widget_version": "1.2.4", "widget_image": "a:2"}}
        assert flipped_entries(baseline, current, "Nothing yet.\n") == []

    def test_a_jinja_default_is_not_reported(self):
        baseline = {"widget": {"widget_host": "{{ inventory_hostname }}"}}
        current = {"widget": {"widget_host": "{{ ansible_fqdn }}"}}
        assert flipped_entries(baseline, current, "Nothing yet.\n") == []
