"""Tests for scripts/version-bump-mr.py (the idempotent bot-MR manager).

Run via `pytest tests` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "version-bump-mr.py"

_spec = importlib.util.spec_from_file_location("version_bump_mr", _SCRIPT)
assert _spec and _spec.loader
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)


def test_changed_paths_ignores_untracked_by_default():
    porcelain = " M ansible/all.yml\nM  kubernetes/x.yaml\n?? version-report.json\n"
    assert bot.changed_paths(porcelain) == ["ansible/all.yml", "kubernetes/x.yaml"]
    assert "version-report.json" in bot.changed_paths(porcelain, include_untracked=True)


def test_changed_paths_takes_the_new_name_of_a_rename():
    assert bot.changed_paths("R  old/a.yml -> new/a.yml\n") == ["new/a.yml"]


def test_changed_paths_dedupes_sorts_and_unquotes():
    porcelain = ' M b.yml\n M a.yml\nMM b.yml\n M "with space.yml"\n'
    assert bot.changed_paths(porcelain) == ["a.yml", "b.yml", "with space.yml"]


def test_changed_paths_empty():
    assert bot.changed_paths("") == []


def test_select_open_mr_matches_branch_pair():
    mrs = [
        {"iid": 9, "state": "opened", "source_branch": "other", "target_branch": "main"},
        {"iid": 7, "state": "closed", "source_branch": "bot/version-bumps", "target_branch": "main"},
        {"iid": 5, "state": "opened", "source_branch": "bot/version-bumps", "target_branch": "main"},
        {"iid": 6, "state": "opened", "source_branch": "bot/version-bumps", "target_branch": "main"},
    ]
    assert bot.select_open_mr(mrs, "bot/version-bumps", "main")["iid"] == 5
    assert bot.select_open_mr(mrs, "bot/version-bumps", "release") is None
    assert bot.select_open_mr([], "bot/version-bumps", "main") is None


def test_build_description_contents():
    body = bot.build_description(
        ["ansible/all.yml"],
        " ansible/all.yml | 2 +-",
        report="sonarr 4.0.1 -> 4.0.2",
        pipeline_url="https://git.example/-/pipelines/1",
    )
    assert "never merged automatically" in body
    assert "- `ansible/all.yml`" in body
    assert "ansible/all.yml | 2 +-" in body
    assert "sonarr 4.0.1 -> 4.0.2" in body
    assert "https://git.example/-/pipelines/1" in body


def test_build_description_truncates_a_long_report():
    body = bot.build_description([], "", report="x" * 100, max_report_chars=10)
    assert "truncated" in body
    assert "x" * 11 not in body


def test_build_description_omits_empty_sections():
    body = bot.build_description([], "", report="", pipeline_url="")
    assert "### Files" not in body and "### Diffstat" not in body and "### Report" not in body


class FakeTransport:
    def __init__(self, response=None):
        self.calls = []
        self.response = response if response is not None else []

    def __call__(self, url, method, headers, payload):
        self.calls.append({"url": url, "method": method, "headers": headers, "payload": payload})
        return self.response


def client(transport):
    return bot.GitLabClient("https://git.example/api/v4", "eric/weisssrv", "tok", transport=transport)


def test_list_merge_requests_query_and_auth_header():
    transport = FakeTransport([{"iid": 1}])
    assert client(transport).list_merge_requests("bot/version-bumps", "main") == [{"iid": 1}]
    call = transport.calls[0]
    assert call["url"].startswith("https://git.example/api/v4/projects/eric%2Fweisssrv/merge_requests?")
    assert "state=opened" in call["url"]
    assert "source_branch=bot%2Fversion-bumps" in call["url"]
    assert call["method"] == "GET"
    assert call["headers"]["PRIVATE-TOKEN"] == "tok"
    assert call["payload"] is None


def test_create_merge_request_posts_json():
    transport = FakeTransport({"web_url": "u"})
    client(transport).create_merge_request({"title": "t"})
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["payload"] == {"title": "t"}
    assert call["headers"]["Content-Type"] == "application/json"


def test_update_merge_request_targets_the_iid():
    transport = FakeTransport({})
    client(transport).update_merge_request(12, {"state_event": "close"})
    call = transport.calls[0]
    assert call["url"].endswith("/merge_requests/12")
    assert call["method"] == "PUT"


def test_remote_tree_is_empty_until_the_branch_is_pushed(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(work)], check=True)
    bot.git(["config", "user.email", "bot@example.com"], str(work))
    bot.git(["config", "user.name", "bot"], str(work))
    (work / "pins.yml").write_text("sonarr: 4.0.1\n")
    bot.git(["add", "-A"], str(work))
    bot.git(["commit", "--quiet", "-m", "init"], str(work))

    assert bot.remote_tree(str(work), "bot/version-bumps", str(remote)) == ""

    (work / "pins.yml").write_text("sonarr: 4.0.2\n")
    bot.git(["checkout", "-B", "bot/version-bumps"], str(work))
    bot.git(["commit", "--quiet", "-am", "chore(deps): bump"], str(work))
    bot.push_branch(str(work), "bot/version-bumps", str(remote))

    tree = bot.git(["rev-parse", "HEAD^{tree}"], str(work)).strip()
    # Same tree back from the remote -> a re-run pushes nothing and leaves the MR alone.
    assert bot.remote_tree(str(work), "bot/version-bumps", str(remote)) == tree


def test_redact_replaces_every_known_secret():
    bot._SECRETS[:] = ["glpat-secret", ""]
    try:
        assert bot.redact("https://gitlab-ci-token:glpat-secret@host/x.git") == (
            "https://gitlab-ci-token:***@host/x.git"
        )
    finally:
        bot._SECRETS[:] = []
