"""Fixtures shared across the test suite."""

import pytest

# Both forges inject these into every job, and the release tooling reads them at
# call time, so an unscrubbed CI_COMMIT_SHA (or BOT_TOKEN) would override what a
# test pins. A test that wants one sets it explicitly.

# CI is absent on purpose: tests/_helpers.py's require_tool/require_tags read it
# to choose fail-vs-skip, and the self-tests over them set it with monkeypatch.
CI_ENV = (
    "GITLAB_CI",
    "CI_COMMIT_SHA",
    "CI_COMMIT_REF_NAME",
    "CI_API_V4_URL",
    "CI_PROJECT_ID",
    "CI_PROJECT_URL",
    "CI_PIPELINE_URL",
    "CI_SERVER_HOST",
    "CI_PROJECT_PATH",
    "CI_DEFAULT_BRANCH",
    "RELEASE_TOKEN",
    "BOT_TOKEN",
    "GITHUB_ACTIONS",
    "GITHUB_API_URL",
    "GITHUB_REPOSITORY",
    "GITHUB_SERVER_URL",
    "GITHUB_SHA",
    "GITHUB_TOKEN",
)


@pytest.fixture(autouse=True)
def _scrub_ci_env(monkeypatch):
    for name in CI_ENV:
        monkeypatch.delenv(name, raising=False)
