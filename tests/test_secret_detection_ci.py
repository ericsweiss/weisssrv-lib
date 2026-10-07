#!/usr/bin/env python3
"""The secret scan runs on every pipeline source that can reach the privileged
image build, so no consumer can build a tree the scan never saw.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SCAN = REPO / "ci" / "security" / "secret-detection.yml"
BUILD = REPO / "ci" / "build" / "docker-build.yml"

SCAN_JOB = "secret_detection"
BUILD_JOB = "$[[ inputs.job_name ]]"

PIPELINE_SOURCE = re.compile(r'\$CI_PIPELINE_SOURCE\s*==\s*"([a-z_]+)"')
DEFAULT_BRANCH = '$CI_COMMIT_BRANCH == "$[[ inputs.default_branch ]]"'


def _conditions(template: Path, job: str) -> list[str]:
    _, body = yaml.safe_load_all(template.read_text())
    return [rule["if"] for rule in body[job]["rules"]]


def _sources(template: Path, job: str) -> set[str]:
    """Pipeline sources the job's rules name explicitly."""
    return {
        source
        for condition in _conditions(template, job)
        for source in PIPELINE_SOURCE.findall(condition)
    }


def test_there_are_rules_to_check() -> None:
    """Vacuity guard: an empty rule list would satisfy every case below."""
    assert _sources(SCAN, SCAN_JOB)
    assert _sources(BUILD, BUILD_JOB)


def test_the_scan_covers_every_source_the_build_runs_on() -> None:
    uncovered = _sources(BUILD, BUILD_JOB) - _sources(SCAN, SCAN_JOB)
    assert uncovered == set(), (
        "ci/build/docker-build.yml creates a privileged job on pipeline "
        f"source(s) {sorted(uncovered)} that secret_detection skips"
    )


def test_both_run_on_a_default_branch_push() -> None:
    """Neither names `push` as a source; both gate the branch instead."""
    assert DEFAULT_BRANCH in _conditions(SCAN, SCAN_JOB)
    assert DEFAULT_BRANCH in _conditions(BUILD, BUILD_JOB)


def _job(template: Path, job: str) -> dict:
    _, body = yaml.safe_load_all(template.read_text())
    return body[job]


def test_the_build_declares_no_needs() -> None:
    """CRITICAL: consumers order the privileged build AFTER secret detection by
    stage alone. A `needs:` here would make the build ignore stage order and
    start before the scan of the tree it builds.
    """
    assert "needs" not in _job(BUILD, BUILD_JOB), (
        "ci/build/docker-build.yml declares `needs:`, which releases the "
        "privileged DinD job from its stage and from secret_detection"
    )
