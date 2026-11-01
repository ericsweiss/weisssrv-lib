#!/usr/bin/env python3
"""A template never pre-selects a runner: a job that cannot run tag-less
requires `tags`, every other `tags` defaults to `[]`. The library's pipeline
passes its privileged tag only where a job needs it, never a redundant `[]`."""
from __future__ import annotations

import pytest
from script_loader import REPO, SCRIPTS, load_path

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

LIB_CI = REPO / ".gitlab-ci.yml"
PRIVILEGED_TAG = ["infrastructure"]
# Includes of the library's own pipeline whose jobs need the privileged runner.
PRIVILEGED_INCLUDES = {"/ci/build/docker-build.yml"}
# Templates whose job fails on any tag-less runner, so `tags` has no default
# and an include that omits it is refused when the pipeline is created.
REQUIRED_TAGS = {
    "ci/build/docker-build.yml",
    "ci/deploy/cluster-verify-base.yml",
    "ci/deploy/deploy-base.yml",
}
# Templates whose `tags` text discusses root: the job or an input mode needs
# root or DinD, or the template splits root-safe and privileged jobs.
ROOT_NOTED = {
    "ci/build/docker-build.yml",
    "ci/deploy/cluster-verify-base.yml",
    "ci/deploy/deploy-base.yml",
    "ci/internal/molecule-matrix.gitlab-ci.yml",
    "ci/maintenance/version-bump-bot.yml",
    "ci/maintenance/version-check.yml",
    "ci/test/python-tests.yml",
    "ci/validate/flux-lint.yml",
    "ci/validate/terraform-drift-plan.yml",
}


def tags_inputs() -> dict:
    """Template path -> its declared `tags` input, for every ci/ include."""
    found = {}
    for path in sorted({p for suffix in ("yml", "yaml") for p in (REPO / "ci").rglob(f"*.{suffix}")}):
        if ".example." in path.name:
            continue
        inputs = ci_yaml.spec_inputs(path.read_text(encoding="utf-8"))
        if "tags" in inputs:
            found[path.relative_to(REPO).as_posix()] = inputs["tags"] or {}
    return found


TAGS = tags_inputs()


def test_there_are_tags_inputs_to_check() -> None:
    """Vacuity guard: a glob that matched nothing would pass every case below."""
    assert len(TAGS) > 15
    assert "ci/internal/molecule-matrix.gitlab-ci.yml" in TAGS


@pytest.mark.parametrize("relative_path", sorted(TAGS))
def test_tags_default_to_no_runner(relative_path: str) -> None:
    spec = TAGS[relative_path]
    assert spec.get("type") == "array", f"{relative_path}: `tags` is not an array input"
    if relative_path in REQUIRED_TAGS:
        assert "default" not in spec, (
            f"{relative_path}: `tags` defaults to {spec.get('default')!r}, but the job "
            "fails on a tag-less runner. Leave it required so GitLab refuses an "
            "include that omits it."
        )
        assert "required" in spec.get("description", ""), (
            f"{relative_path}: a required `tags` input says so in its description"
        )
        return
    assert spec.get("default") == [], (
        f"{relative_path}: `tags` defaults to {spec.get('default')!r}. A library "
        "template never pre-selects a runner; default it to [] and let the "
        "consumer pass its privileged tag."
    )


def test_every_required_tags_template_needs_root() -> None:
    """A required tag is only for a job no tag-less runner can run."""
    assert REQUIRED_TAGS <= ROOT_NOTED
    assert REQUIRED_TAGS <= set(TAGS)


@pytest.mark.parametrize("relative_path", sorted(TAGS))
def test_tags_description_names_root_only_where_a_job_needs_it(relative_path: str) -> None:
    """A root-safe job's `tags` text offers no privileged-runner choice."""
    text = " ".join((TAGS[relative_path].get("description") or "").split())
    assert text.startswith("Runner tag(s)"), f"{relative_path}: `tags` lacks the standard opening"
    mentions_root = "root" in text or "privileged" in text or "Docker-in-Docker" in text
    assert mentions_root == (relative_path in ROOT_NOTED), (
        f"{relative_path}: `tags` description {'mentions' if mentions_root else 'omits'} root, "
        "but ROOT_NOTED says otherwise. Name root only where this job needs it."
    )


def _local_includes(doc: dict) -> list:
    return [e for e in doc["include"] if isinstance(e, dict) and "local" in e]


def redundant_tags(doc: dict) -> list:
    """Local includes that pass `tags: []`, which every template already defaults to."""
    return [e["local"] for e in _local_includes(doc) if (e.get("inputs") or {}).get("tags") == []]


def unprivileged(doc: dict) -> list:
    """Privileged-runner includes that do not pass the privileged tag."""
    return [
        e["local"] for e in _local_includes(doc)
        if e["local"] in PRIVILEGED_INCLUDES and (e.get("inputs") or {}).get("tags") != PRIVILEGED_TAG
    ]


def test_the_lib_pipeline_passes_no_redundant_tags() -> None:
    assert redundant_tags(ci_yaml.parse_ci(LIB_CI.read_text())) == []


def test_the_lib_pipeline_tags_every_privileged_include() -> None:
    doc = ci_yaml.parse_ci(LIB_CI.read_text())
    assert any(e["local"] in PRIVILEGED_INCLUDES for e in _local_includes(doc))
    assert unprivileged(doc) == []


def test_the_include_checks_fail_on_a_mistagged_pipeline() -> None:
    """Both checks can fail: an explicit [] and a DinD build left untagged."""
    doc = {"include": [
        {"local": "/ci/lint/yaml-lint.yml", "inputs": {"tags": []}},
        {"local": "/ci/build/docker-build.yml", "inputs": {"job_name": "build"}},
    ]}
    assert redundant_tags(doc) == ["/ci/lint/yaml-lint.yml"]
    assert unprivileged(doc) == ["/ci/build/docker-build.yml"]

