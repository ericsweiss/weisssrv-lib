"""Tests for scripts/version-bump-mr.py (the idempotent bot-MR manager).

Run via `pytest tests` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "version-bump-mr.py"

_spec = importlib.util.spec_from_file_location("version_bump_mr", _SCRIPT)
assert _spec and _spec.loader
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)

# GitLab injects these into every job and main()'s argparse defaults read them at
# call time (--target-branch from $CI_DEFAULT_BRANCH, --api-url, --project-id,
# the remote URL from $CI_SERVER_HOST/$CI_PROJECT_PATH, the description's
# pipeline link). Scrub them so the suite behaves identically in and out of a
# pipeline; a test that wants one sets it explicitly.
CI_ENV = (
    "CI",
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
)


@pytest.fixture(autouse=True)
def _scrub_ci_env(monkeypatch):
    for name in CI_ENV:
        monkeypatch.delenv(name, raising=False)


def test_changed_paths_ignores_untracked_by_default():
    porcelain = " M ansible/all.yml\0M  kubernetes/x.yaml\0?? version-report.json\0"
    assert bot.changed_paths(porcelain) == ["ansible/all.yml", "kubernetes/x.yaml"]
    assert "version-report.json" in bot.changed_paths(porcelain, include_untracked=True)


def test_changed_paths_takes_the_new_name_of_a_rename():
    """`-z` drops the ` -> ` and reverses the pair: the record's own field is the
    NEW path and the next field is the origin, which is not a change of its own."""
    assert bot.changed_paths("R  new/a.yml\0old/a.yml\0 M z.yml\0") == ["new/a.yml", "z.yml"]


def test_changed_paths_dedupes_and_sorts():
    porcelain = " M b.yml\0 M a.yml\0MM b.yml\0"
    assert bot.changed_paths(porcelain) == ["a.yml", "b.yml"]


def test_changed_paths_keeps_special_characters_verbatim():
    """`-z` never quotes, so nothing has to be un-quoted — the path is handed to
    `git add` exactly as git spelled it."""
    porcelain = ' M with space.yml\0 M ansible/rôle/main.yml\0 M has"quote.yml\0'
    assert bot.changed_paths(porcelain) == [
        "ansible/rôle/main.yml",
        'has"quote.yml',
        "with space.yml",
    ]


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


def test_remote_tree_raises_when_the_fetch_itself_fails(tmp_path):
    """A fetch blip must not read as "branch absent" — that force-pushes a
    freshly-timestamped commit and re-notifies the MR on every transient failure."""
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(work)], check=True)
    with pytest.raises(bot.GitRemoteError, match="git fetch of bot/version-bumps failed"):
        bot.remote_tree(str(work), "bot/version-bumps", str(tmp_path / "not-a-remote.git"))


def test_redact_replaces_every_known_secret():
    bot._SECRETS[:] = ["glpat-secret", ""]
    try:
        assert bot.redact("https://gitlab-ci-token:glpat-secret@host/x.git") == (
            "https://gitlab-ci-token:***@host/x.git"
        )
    finally:
        bot._SECRETS[:] = []


# --- quick-action / fence neutralization --------------------------------------

def test_build_description_neutralizes_quick_actions_in_the_report():
    """Report text is third-party (registry names, upstream error bodies) and the
    description is POSTed to a bot that must never merge."""
    body = bot.build_description([], "", report="sonarr 4.0.1 -> 4.0.2\n/close\n/merge")
    assert "\n/close" not in body and "\n/merge" not in body
    assert " /close" in body and " /merge" in body


def test_build_description_fence_outlives_backticks_in_the_report():
    body = bot.build_description([], "", report="see ```\n/merge")
    fence = "`" * 4
    assert body.count(fence) == 2
    assert " /merge" in body


def test_build_description_truncates_on_a_line_boundary():
    report = "\n".join("line %d" % i for i in range(50))
    body = bot.build_description([], "", report=report, max_report_chars=20)
    assert "truncated" in body
    report_block = body.split("### Report\n\n", 1)[1]
    for line in report_block.splitlines():
        assert line == "```" or line.startswith(("line ", "…"))


# --- main(): the orchestration nothing else covers ----------------------------

class FakeClient:
    """Stands in for GitLabClient inside main(); records every API call."""

    def __init__(self, open_mrs=()):
        self.open_mrs = list(open_mrs)
        self.calls = []

    def list_merge_requests(self, source_branch, target_branch):
        self.calls.append(("list", source_branch, target_branch))
        return self.open_mrs

    def create_merge_request(self, payload):
        self.calls.append(("create", payload))
        return {"iid": 1, "web_url": "https://git.example/-/merge_requests/1"}

    def update_merge_request(self, iid, payload):
        self.calls.append(("update", iid, payload))
        return {}


OPEN_MR = {"iid": 5, "state": "opened", "source_branch": "bot/version-bumps", "target_branch": "main"}


@pytest.fixture()
def repo(tmp_path):
    """A work tree with one committed pin plus the bare remote the bot pushes to."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(work)], check=True)
    bot.git(["config", "user.email", "bot@example.com"], str(work))
    bot.git(["config", "user.name", "bot"], str(work))
    (work / "pins.yml").write_text("sonarr: 4.0.1\n")
    bot.git(["add", "-A"], str(work))
    bot.git(["commit", "--quiet", "-m", "init"], str(work))
    return work, remote


@pytest.fixture(autouse=True)
def _clean_secrets():
    yield
    bot._SECRETS[:] = []


def _bump(work):
    """What the consumer's check_command does: rewrite a tracked pin."""
    (work / "pins.yml").write_text("sonarr: 4.0.2\n")


def _run_main(repo, monkeypatch, open_mrs=(), paths=None, repo_dir=None):
    work, remote = repo
    monkeypatch.setenv("BOT_TOKEN", "glpat-tok")
    client = FakeClient(open_mrs)
    monkeypatch.setattr(bot, "GitLabClient", lambda *a, **kw: client)
    argv = [
        "--repo-dir", str(repo_dir or work),
        "--remote-url", str(remote),
        "--target-branch", "main",
        "--api-url", "https://git.example/api/v4",
        "--project-id", "42",
    ]
    if paths is not None:
        argv += ["--paths", paths]
    rc = bot.main(argv)
    return rc, client


def _committed(work):
    """Paths in HEAD's commit, read NUL-terminated so `core.quotepath` cannot
    re-quote what the staging path had to keep raw."""
    return [
        name
        for name in bot.git(["show", "--name-only", "--format=", "-z", "HEAD"], str(work)).split("\0")
        if name
    ]


def test_main_commits_tracked_pins_only(repo, monkeypatch):
    """The documented contract: a report artifact the check command drops stays
    untracked and out of the commit the bot pushes."""
    work, _ = repo
    _bump(work)
    (work / "version-report.json").write_text("{}\n")

    rc, client = _run_main(repo, monkeypatch)

    assert rc == 0
    committed = bot.git(["show", "--name-only", "--format=", "HEAD"], str(work)).split()
    assert committed == ["pins.yml"]
    payload = [c for c in client.calls if c[0] == "create"][0][1]
    assert "version-report.json" not in payload["description"]
    assert "pins.yml" in payload["description"]


def test_main_stages_bumps_when_a_paths_entry_has_no_tracked_files(repo, monkeypatch):
    """`git add -- reports/` aborts rc 128 on a pathspec matching no TRACKED file,
    while `git status --` tolerates it — so a --paths list mixing a real tree with
    an artifact-only directory detected the bumps and then died before committing."""
    work, _ = repo
    _bump(work)
    (work / "reports").mkdir()
    (work / "reports" / "version-report.json").write_text("{}\n")

    rc, client = _run_main(repo, monkeypatch, paths="pins.yml reports/")

    assert rc == 0
    committed = bot.git(["show", "--name-only", "--format=", "HEAD"], str(work)).split()
    assert committed == ["pins.yml"]
    assert [c[0] for c in client.calls] == ["list", "create"]


def test_main_stages_a_pin_whose_path_git_would_c_quote(repo, monkeypatch):
    """`git status --porcelain` C-quotes any path with a non-ASCII byte
    (`"ansible/r\\303\\264le/pins.yml"`); stripping the quotes without decoding the
    escapes hands `git add` a literal that matches nothing — rc 128, the same
    abort the detected-list staging was written to remove. `-z` never quotes."""
    work, _ = repo
    role = work / "rôle"
    role.mkdir()
    (role / "pins.yml").write_text("sonarr: 4.0.1\n")
    bot.git(["add", "-A"], str(work))
    bot.git(["commit", "--quiet", "-m", "add the non-ascii pin"], str(work))
    (role / "pins.yml").write_text("sonarr: 4.0.2\n")

    rc, client = _run_main(repo, monkeypatch, paths="rôle")

    assert rc == 0
    assert _committed(work) == ["rôle/pins.yml"]
    assert [c[0] for c in client.calls] == ["list", "create"]


def test_main_handles_a_repo_dir_below_the_repo_root(repo, monkeypatch):
    """`git status --porcelain` always answers in repo-root-relative paths while
    `git add` resolves pathspecs against the CWD, so staging the detected list
    from a subdirectory --repo-dir needs the `:(top)` anchor to match at all."""
    work, _ = repo
    sub = work / "sub"
    sub.mkdir()
    (sub / "pins.yml").write_text("sonarr: 4.0.1\n")
    bot.git(["add", "-A"], str(work))
    bot.git(["commit", "--quiet", "-m", "add the nested pin"], str(work))
    (sub / "pins.yml").write_text("sonarr: 4.0.2\n")

    rc, client = _run_main(repo, monkeypatch, paths="pins.yml", repo_dir=sub)

    assert rc == 0
    # The root pins.yml is untouched: --paths is still resolved from --repo-dir.
    assert _committed(work) == ["sub/pins.yml"]
    assert [c[0] for c in client.calls] == ["list", "create"]


def test_main_closes_the_stale_mr_when_there_are_no_bumps(repo, monkeypatch):
    rc, client = _run_main(repo, monkeypatch, open_mrs=[OPEN_MR])
    assert rc == 0
    assert ("update", 5, {"state_event": "close"}) in client.calls
    assert not [c for c in client.calls if c[0] == "create"]


def test_main_with_no_bumps_and_no_mr_does_nothing(repo, monkeypatch):
    rc, client = _run_main(repo, monkeypatch)
    assert rc == 0
    assert [c[0] for c in client.calls] == ["list"]


def test_main_opens_the_mr_when_a_prior_run_pushed_but_died(repo, monkeypatch):
    """Crash recovery: the branch already carries these exact bumps but no MR was
    created. Nothing to push — but the MR still has to be opened."""
    work, _ = repo
    _bump(work)
    assert _run_main(repo, monkeypatch)[0] == 0

    # A later scheduled run: fresh checkout of the target, same bumps re-applied.
    bot.git(["checkout", "--quiet", "main"], str(work))
    _bump(work)
    pushes = []
    monkeypatch.setattr(bot, "push_branch", lambda *a: pushes.append(a))
    rc, client = _run_main(repo, monkeypatch)

    assert rc == 0
    assert pushes == []
    assert [c[0] for c in client.calls] == ["list", "create"]


def test_main_leaves_an_identical_branch_with_an_open_mr_untouched(repo, monkeypatch):
    work, _ = repo
    _bump(work)
    assert _run_main(repo, monkeypatch)[0] == 0

    bot.git(["checkout", "--quiet", "main"], str(work))
    _bump(work)
    pushes = []
    monkeypatch.setattr(bot, "push_branch", lambda *a: pushes.append(a))
    rc, client = _run_main(repo, monkeypatch, open_mrs=[OPEN_MR])

    assert rc == 0
    assert pushes == []
    assert [c[0] for c in client.calls] == ["list"]


def test_main_pushes_and_refreshes_when_the_bumps_changed(repo, monkeypatch):
    work, remote = repo
    _bump(work)
    assert _run_main(repo, monkeypatch)[0] == 0

    bot.git(["checkout", "--quiet", "main"], str(work))
    (work / "pins.yml").write_text("sonarr: 4.0.3\n")
    rc, client = _run_main(repo, monkeypatch, open_mrs=[OPEN_MR])

    assert rc == 0
    assert [c[0] for c in client.calls] == ["list", "update"]
    assert client.calls[1][1] == 5
    assert "sonarr" not in client.calls[1][2]["title"]
    pushed = bot.git(["rev-parse", "HEAD^{tree}"], str(work)).strip()
    assert bot.remote_tree(str(work), "bot/version-bumps", str(remote)) == pushed


def test_main_dry_run_touches_no_remote(repo, monkeypatch):
    work, remote = repo
    _bump(work)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setattr(bot, "GitLabClient", lambda *a, **kw: pytest.fail("dry run built a client"))
    rc = bot.main(["--repo-dir", str(work), "--remote-url", str(remote), "--dry-run"])
    assert rc == 0
    assert bot.remote_tree(str(work), "bot/version-bumps", str(remote)) == ""
