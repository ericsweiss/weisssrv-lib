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
FIRST = {"released": True, "tag": "v0.1.0", "previous_tag": None, "notes": "x"}
ALL_IMAGES = ("molecule-ci", "molecule-test", "ansible-deploy")


class TestPublishImageTags:
    def test_no_release_is_a_no_op(self, tmp_path):
        res, log = _retag(tmp_path, release={"released": False, "tag": None})
        assert res.returncode == 0, res.stderr
        assert "push" not in log

    def test_the_pipeline_build_is_preferred(self, tmp_path):
        present = [f"{IMAGE}/{i}:{SHA}" for i in ALL_IMAGES] + [f"{IMAGE}/{i}:latest" for i in ALL_IMAGES]
        res, log = _retag(tmp_path, release=RELEASED, present=present)
        assert res.returncode == 0, res.stderr
        assert f"tag {IMAGE}/molecule-ci:{SHA} {IMAGE}/molecule-ci:v1.2.0" in log

    def test_an_absent_build_falls_back_to_latest(self, tmp_path):
        present = [f"{IMAGE}/{i}:latest" for i in ALL_IMAGES]
        res, log = _retag(tmp_path, release=RELEASED, present=present)
        assert res.returncode == 0, res.stderr
        assert f"tag {IMAGE}/ansible-deploy:latest {IMAGE}/ansible-deploy:v1.2.0" in log

    def test_a_failed_pull_never_falls_back(self, tmp_path):
        """A registry error on the pipeline's own build must not publish :latest."""
        present = [f"{IMAGE}/{i}:latest" for i in ALL_IMAGES]
        res, log = _retag(tmp_path, release=RELEASED, present=present,
                          errors=[f"{IMAGE}/molecule-ci:{SHA}"])
        assert res.returncode == 1
        assert "unauthorized" in res.stderr
        assert "push" not in log

    def test_a_missing_image_after_the_first_release_fails(self, tmp_path):
        res, log = _retag(tmp_path, release=RELEASED)
        assert res.returncode == 1
        assert "no registry.example/lib/molecule-ci image to retag" in res.stderr

    def test_the_first_release_may_lack_images(self, tmp_path):
        res, log = _retag(tmp_path, release=FIRST)
        assert res.returncode == 0, res.stderr
        assert res.stdout.count("WARNING: no") == len(ALL_IMAGES)

