"""The release-time registries are prose; these tests keep their claims true.

Nothing in CI parses `docs/CONSUMERS.yml`, so a gate it names as "enforced" can
be renamed or deleted with no signal, and a releaser walking the list before
cutting a tag reads a claim nothing backs. Same for the collection's
MIGRATING.md, whose per-release section is written by a checklist bullet.

Two halves, because the library cannot see its consumers at test time:

* Library-side claims (a `scripts/` gate, a `/ci/` template) are asserted
  unconditionally.
* Consumer-side claims (`weisssrv scripts/test_site_configs.py::test_…`) are
  asserted only when that consumer checkout is a sibling of this repo, and
  skipped otherwise — the pipeline does not clone the consumers.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CONSUMERS = REPO / "docs" / "CONSUMERS.yml"
REGISTRY = REPO / "scripts" / "vendored-paths.yml"
GALAXY = REPO / "ansible_collections" / "weisssrv" / "infra" / "galaxy.yml"
MIGRATING = REPO / "ansible_collections" / "weisssrv" / "infra" / "MIGRATING.md"

CONSUMER_NAMES = ("weisssrv-app-template", "weisssrv-cluster-template", "weisssrv")

# "<consumer> <path>.py[::sym | :sym]" — the shape every enforcement claim uses.
# Longest consumer name first so `weisssrv-app-template` never matches as
# `weisssrv` plus a stray token.
_GATE_RE = re.compile(
    r"\b(%s)\s+((?:[\w.\-]+/)*[\w.\-]+\.py)(?:(::|:)([A-Za-z_]\w*))?"
    % "|".join(CONSUMER_NAMES)
)
# A library path claimed without a consumer prefix. Only `scripts/` — a bare
# `tests/…` in a per-consumer block is that consumer's own suite.
_LIB_PATH_RE = re.compile(r"(?<![\w./-])(scripts/[\w.\-]+\.(?:py|sh|yml))")
_ANY_GATE_FILE_RE = re.compile(r"(?<![\w./-])(?:[\w.\-]+/)*[\w.\-]+\.(?:py|sh)\b")
_CI_TEMPLATE_RE = re.compile(r"(?<![\w./-])(/ci/[\w.\-/]+\.yml)")


@pytest.fixture(scope="module")
def registry() -> dict:
    return yaml.safe_load(CONSUMERS.read_text())


def _enforcement_claims(node, trail=()) -> list[tuple[str, str]]:
    """Every (yaml path, text) under an `enforced` / `enforced_by` key."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = (*trail, str(key))
            if key in ("enforced", "enforced_by") and isinstance(value, str):
                found.append((".".join(here), value))
            else:
                found.extend(_enforcement_claims(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_enforcement_claims(item, (*trail, str(index))))
    return found


def _consumer_root(name: str) -> Path | None:
    """A sibling checkout of `name`, or None when it is not available here."""
    candidate = REPO.parent / name
    return candidate if (candidate / ".git").exists() else None


class TestConsumersRegistry:
    """`enforced:` claims name gates that exist."""

    def test_the_file_parses(self, registry):
        assert set(registry) == {"consumers", "pin_surfaces"}
        assert [c["name"] for c in registry["consumers"]] == [
            "weisssrv",
            "weisssrv-app-template",
            "weisssrv-cluster-template",
        ]

    def test_every_claim_names_at_least_one_gate(self, registry):
        for where, text in _enforcement_claims(registry):
            if text.strip().lower() in ("false", "nothing"):
                continue
            assert _ANY_GATE_FILE_RE.search(text), (
                "%s claims enforcement but names no gate file: %r" % (where, text)
            )

    def test_library_gates_exist(self, registry):
        missing = []
        for where, text in _enforcement_claims(registry):
            # Strip the consumer-qualified refs; what is left is library-relative.
            for path in _LIB_PATH_RE.findall(_GATE_RE.sub(" ", text)):
                if not (REPO / path).exists():
                    missing.append("%s -> %s" % (where, path))
        assert not missing, "CONSUMERS.yml names library gates that do not exist: %s" % missing

    def test_ci_templates_named_anywhere_exist(self, registry):
        missing = []
        for path in _CI_TEMPLATE_RE.findall(yaml.safe_dump(registry)):
            if not (REPO / path.lstrip("/")).exists():
                missing.append(path)
        assert not missing, "CONSUMERS.yml names CI templates that do not exist: %s" % missing

    @pytest.mark.parametrize("consumer", CONSUMER_NAMES)
    def test_consumer_gates_exist(self, registry, consumer):
        root = _consumer_root(consumer)
        if root is None:
            pytest.skip("no sibling checkout of %s" % consumer)
        problems = []
        for where, text in _enforcement_claims(registry):
            for named, path, _sep, symbol in _GATE_RE.findall(text):
                if named != consumer:
                    continue
                target = root / path
                if not target.exists():
                    problems.append("%s -> %s/%s (no such file)" % (where, consumer, path))
                elif symbol and not re.search(r"(?m)^\s*def %s\b" % symbol, target.read_text()):
                    problems.append("%s -> %s/%s::%s (no such function)" % (where, consumer, path, symbol))
        assert not problems, "CONSUMERS.yml enforcement claims are stale: %s" % problems


class TestRegisteredCopies:
    """Every same-named consumer copy is in `scripts/vendored-paths.yml`.

    The registry is the only gate the app template has — it ships no directory
    walk — so an unregistered copy is simply ungated there. A same-named file in
    a consumer's `scripts/` is either a vendored copy or a declared fork; a third
    state does not exist.
    """

    @pytest.mark.parametrize("consumer", CONSUMER_NAMES)
    def test_no_unregistered_same_named_copy(self, consumer):
        root = _consumer_root(consumer)
        if root is None:
            pytest.skip("no sibling checkout of %s" % consumer)
        entries = yaml.safe_load(REGISTRY.read_text())["consumers"][consumer]
        declared = set()
        for kind in ("vendored", "forked"):
            for entry in entries.get(kind) or []:
                if isinstance(entry, str):
                    declared.add(entry)
                else:
                    # `consumer:` only when the two sides differ; else `lib:`.
                    declared.add(entry.get("consumer") or entry["lib"])

        unregistered = []
        for scripts_dir in (root / "scripts", root / "template" / "scripts"):
            if not scripts_dir.is_dir():
                continue
            for copy in sorted(scripts_dir.iterdir()):
                if not copy.is_file() or not (REPO / "scripts" / copy.name).exists():
                    continue
                relative = copy.relative_to(root).as_posix()
                if relative not in declared:
                    unregistered.append(relative)
        assert not unregistered, (
            "%s holds same-named copies of library scripts that vendored-paths.yml "
            "does not register (add them as `vendored`, or as `forked` with a "
            "reason): %s" % (consumer, unregistered)
        )


class TestMigratingSections:
    """The newest titled MIGRATING section is the version being shipped.

    The retitle is a manual release-checklist bullet, and four consecutive
    releases went out without it. galaxy.yml is bumped in the same MR
    (test_ansible_collection.py::TestReleaseLineage), so binding the two makes
    the omission fail at review time instead of on the consumer's next adoption.
    """

    @staticmethod
    def _titled_versions() -> list[str]:
        return re.findall(r"(?m)^# (v\d+\.\d+\.\d+)\s*$", MIGRATING.read_text())

    def test_sections_are_newest_first(self):
        versions = [tuple(int(p) for p in v[1:].split(".")) for v in self._titled_versions()]
        assert versions == sorted(versions, reverse=True), (
            "MIGRATING.md sections must run newest first: %s" % self._titled_versions()
        )

    def test_an_unreleased_heading_is_open(self):
        assert re.search(r"(?m)^# Unreleased \(next release\)\s*$", MIGRATING.read_text()), (
            "MIGRATING.md must always carry an open `# Unreleased (next release)` heading"
        )

    def test_newest_section_matches_the_declared_version(self):
        declared = str(yaml.safe_load(GALAXY.read_text())["version"])
        titled = self._titled_versions()
        assert titled, "MIGRATING.md carries no titled release section"
        assert titled[0] == "v%s" % declared, (
            "galaxy.yml declares %s but MIGRATING.md's newest titled section is %s. "
            "Retitle `# Unreleased (next release)` to `# v%s` and open a fresh empty "
            "one above it — a release with nothing to migrate still gets a section "
            "saying so." % (declared, titled[0], declared)
        )

    def test_every_released_tag_has_a_section(self):
        if shutil.which("git") is None or not (REPO / ".git").exists():
            pytest.skip("not a git checkout")
        tags = subprocess.run(
            ["git", "-C", str(REPO), "tag", "--list", "v*"],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        floor = _collection_floor_tag()
        titled = set(self._titled_versions())
        missing = sorted(
            tag for tag in tags
            if tuple(int(p) for p in tag[1:].split(".")) >= floor and tag not in titled
        )
        assert not missing, (
            "these releases shipped with no MIGRATING.md section — add one per tag, "
            "even if it only says there are no migration steps: %s" % missing
        )


def _collection_floor_tag() -> tuple[int, int, int]:
    """Oldest release the migration record covers: the oldest titled section."""
    versions = re.findall(r"(?m)^# v(\d+\.\d+\.\d+)\s*$", MIGRATING.read_text())
    return min(tuple(int(p) for p in v.split(".")) for v in versions)
