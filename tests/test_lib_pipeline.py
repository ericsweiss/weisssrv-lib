"""The library's own pipeline: cancellation, release gating and retagging.

A tag cut here is what every consumer pins, so these assertions hold the
pipeline that cuts it to the gates it claims to run.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from script_loader import REPO, SCRIPTS, load_path

ci_yaml = load_path(SCRIPTS / "ci_yaml.py")

LIB_CI = REPO / ".gitlab-ci.yml"


def _ci(path: Path) -> dict:
    return ci_yaml.parse_ci(path.read_text())


def _include(local: str) -> dict:
    for entry in _ci(LIB_CI)["include"]:
        if isinstance(entry, dict) and entry.get("local") == local:
            return entry
    raise AssertionError(f"{LIB_CI.name} does not include {local}")


def _workflow_rule(fragment: str) -> dict:
    rules = [r for r in _ci(LIB_CI)["workflow"]["rules"] if r.get("if") and fragment in r["if"]]
    assert len(rules) == 1, f"expected one workflow rule matching {fragment!r}"
    return rules[0]


class TestCancellation:
    @pytest.mark.parametrize("fragment", ['$CI_COMMIT_BRANCH == "main"', '$SCHEDULE_TYPE == "full-matrix"'])
    def test_main_and_nightly_pipelines_are_never_cancelled(self, fragment):
        assert _workflow_rule(fragment).get("auto_cancel") == {"on_new_commit": "none"}

    def test_merge_request_pipelines_still_cancel(self):
        assert "auto_cancel" not in _workflow_rule('"merge_request_event"')
        assert _ci(LIB_CI)["workflow"]["auto_cancel"] == {"on_new_commit": "interruptible"}


def test_the_release_job_waits_on_every_earlier_stage():
    """A `needs:` would let the tag start before the gates finish."""
    inputs = _include("/ci/release/semantic-release.yml").get("inputs", {})
    assert _ci(LIB_CI)["stages"][-1] == "release"
    assert "needs" not in inputs
    assert inputs.get("stage", "release") == "release"
    assert "semantic-release" not in _ci(LIB_CI)


def test_the_molecule_matrix_is_included():
    """molecule-tests is only a shell around the matrix the two includes define."""
    _include("/.gitlab/ci/molecule-jobs.gitlab-ci.yml")
    _include("/ci/internal/molecule-matrix.gitlab-ci.yml")
    assert _ci(LIB_CI)["molecule-tests"]["extends"] == ".molecule-test-job"


# --- publish-image-tags, run against a stub docker -------------------------

STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$1" in
  info|login|tag|push) exit 0 ;;
  pull)
    if grep -qxF "$2" "$STUB_ERRORS" 2>/dev/null; then
      echo "Error response from daemon: unauthorized: authentication required" >&2; exit 1
    fi
    grep -qxF "$2" "$STUB_PRESENT" 2>/dev/null && exit 0
    echo "Error response from daemon: manifest for $2 not found: manifest unknown" >&2
    exit 1 ;;
esac
exit 0
"""

IMAGE = "registry.example/lib"
SHA = "abc12345"


def _retag_script() -> str:
    return _ci(LIB_CI)["publish-image-tags"]["script"][0]


def _retag(tmp_path: Path, *, release: dict, present=(), errors=()):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(STUB)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "present").write_text("".join(f"{p}\n" for p in present))
    (tmp_path / "errors").write_text("".join(f"{p}\n" for p in errors))
    (tmp_path / "release.json").write_text(json.dumps(release, indent=2))
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        STUB_LOG=str(tmp_path / "log"),
        STUB_PRESENT=str(tmp_path / "present"),
        STUB_ERRORS=str(tmp_path / "errors"),
        CI_REGISTRY_IMAGE=IMAGE,
        CI_COMMIT_SHORT_SHA=SHA,
        CI_REGISTRY="registry.example",
        CI_REGISTRY_USER="u",
        CI_REGISTRY_PASSWORD="p",
    )
    res = subprocess.run(["sh", "-c", _retag_script()], cwd=tmp_path, env=env,
                         capture_output=True, text=True)
    log = (tmp_path / "log").read_text() if (tmp_path / "log").exists() else ""
    return res, log


RELEASED = {"released": True, "tag": "v1.2.0", "previous_tag": "v1.1.0", "notes": "x"}
ALL_IMAGES = ("molecule-ci", "molecule-test")


@pytest.mark.parametrize("errors, needle", [
    ([f"{IMAGE}/molecule-ci:{SHA}"], "unauthorized"),
    ([], "no registry.example/lib/molecule-ci image to retag"),
], ids=["a_failed_pull_never_falls_back", "a_missing_image_after_the_first_release_fails"])
def test_publish_image_tags_fails_closed(tmp_path, errors, needle):
    """A registry error on the pipeline's own build must not publish :latest, and
    a release after the first must find an image to retag."""
    present = [f"{IMAGE}/{i}:latest" for i in ALL_IMAGES] if errors else []
    res, log = _retag(tmp_path, release=RELEASED, present=present, errors=errors)
    assert res.returncode == 1
    assert needle in res.stderr
    assert "push" not in log
