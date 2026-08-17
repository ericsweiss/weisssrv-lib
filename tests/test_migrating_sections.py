"""MIGRATING.md's release sections stay bound to the declared version.

Carved out of the retired test_docs_registry.py (the consumer registry it
gated moved into the consumers with the vendored-manifest inversion); this
half gates library-owned state and stays.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
MIGRATING = REPO / "ansible_collections" / "weisssrv" / "infra" / "MIGRATING.md"
GALAXY = REPO / "ansible_collections" / "weisssrv" / "infra" / "galaxy.yml"


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
