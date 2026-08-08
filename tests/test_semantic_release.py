"""Tests for scripts/semantic-release.py (conventional-commit release planning).

Run via `pytest tests` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "semantic-release.py"

_spec = importlib.util.spec_from_file_location("semantic_release", _SCRIPT)
assert _spec and _spec.loader
sr = importlib.util.module_from_spec(_spec)
# @dataclass resolves annotations through sys.modules[cls.__module__].
sys.modules[_spec.name] = sr
_spec.loader.exec_module(sr)

RS = sr.RECORD_SEP
FS = sr.FIELD_SEP

# GitLab (and now GitHub Actions) inject these into every job, and main() reads
# them for whichever platform is selected: an unscrubbed CI_COMMIT_SHA silently
# overrides the fake HEAD the main() tests pin, so the release-creation and
# crash-recovery assertions fail in the pipeline and pass locally. Scrub the
# whole set so the suite behaves identically in and out of CI, on either forge;
# a test that wants one sets it explicitly.
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


def log(*records):
    """Build `git log --format=%H%x1f%B%x1e` output from (sha, message) pairs."""
    return "".join("%s%s%s%s" % (sha, FS, message, RS) for sha, message in records)


def test_parse_commit_plain():
    commit = sr.parse_commit("abc1234def", "feat: add the pr-agent template")
    assert commit.type == "feat"
    assert commit.scope == ""
    assert commit.summary == "add the pr-agent template"
    assert commit.breaking is False
    assert commit.short_sha == "abc1234d"


def test_parse_commit_scope_and_bang():
    commit = sr.parse_commit("f" * 40, "refactor(ci)!: rename the tags input")
    assert (commit.type, commit.scope, commit.breaking) == ("refactor", "ci", True)


def test_parse_commit_breaking_trailer():
    commit = sr.parse_commit("a" * 40, "fix(cli): drop --legacy\n\nBREAKING CHANGE: --legacy is gone")
    assert commit.breaking is True
    assert commit.breaking_notes == ["--legacy is gone"]


def test_parse_commit_hyphenated_breaking_trailer():
    commit = sr.parse_commit("a" * 40, "fix: x\n\nBREAKING-CHANGE: y")
    assert commit.breaking_notes == ["y"]


def test_parse_commit_rejects_non_conventional():
    assert sr.parse_commit("a" * 40, "Merge branch 'x' into 'main'") is None
    assert sr.parse_commit("a" * 40, "update stuff") is None
    assert sr.parse_commit("a" * 40, "") is None


def test_parse_commit_normalizes_type_case():
    assert sr.parse_commit("a" * 40, "Feat: x").type == "feat"


def test_parse_log_skips_unparseable_records():
    commits = sr.parse_log(log(("a1", "feat: one"), ("b2", "not conventional"), ("c3", "fix: two")))
    assert [(c.sha, c.type) for c in commits] == [("a1", "feat"), ("c3", "fix")]


def test_bump_level_precedence():
    assert sr.bump_level(sr.parse_log(log(("a", "docs: x")))) is None
    assert sr.bump_level(sr.parse_log(log(("a", "fix: x")))) == "patch"
    assert sr.bump_level(sr.parse_log(log(("a", "perf: x")))) == "patch"
    assert sr.bump_level(sr.parse_log(log(("a", "refactor: x")))) == "patch"
    assert sr.bump_level(sr.parse_log(log(("a", "fix: x"), ("b", "feat: y")))) == "minor"
    assert sr.bump_level(sr.parse_log(log(("a", "feat: x"), ("b", "chore!: y")))) == "major"


def test_latest_version_tag_orders_numerically():
    tags = ["v0.9.0", "v0.10.0", "v0.2.1", "not-a-tag", "v1.0", "0.3.0"]
    assert sr.latest_version_tag(tags) == "v0.10.0"


def test_latest_version_tag_honours_prefix():
    assert sr.latest_version_tag(["v1.0.0", "rel-2.0.0"], prefix="rel-") == "rel-2.0.0"
    assert sr.latest_version_tag([], prefix="v") is None


def test_next_version():
    assert sr.next_version("0.1.1", "patch") == "0.1.2"
    assert sr.next_version("0.1.1", "minor") == "0.2.0"
    assert sr.next_version("1.2.3", "major") == "2.0.0"
    assert sr.next_version(None, "minor", initial="0.1.0") == "0.1.0"


def test_applied_level_demotes_breaking_while_zero_major():
    assert sr.applied_level("major", "0.2.0") == "minor"
    assert sr.applied_level("major", "0.2.0", major_on_zero=True) == "major"
    assert sr.applied_level("major", "1.2.0") == "major"
    assert sr.applied_level("patch", "0.2.0") == "patch"
    # First release: the initial version applies, nothing to demote.
    assert sr.applied_level("major", None) == "major"


def test_plan_release_breaking_on_zero_stays_in_initial_development():
    log_output = log(("a" * 8, "feat!: rename an input"))
    demoted = sr.plan_release(["v0.2.0"], log_output)
    assert (demoted.tag, demoted.level) == ("v0.3.0", "minor")
    strict = sr.plan_release(["v0.2.0"], log_output, major_on_zero=True)
    assert (strict.tag, strict.level) == ("v1.0.0", "major")
    # Past 1.0 the demotion never applies.
    assert sr.plan_release(["v1.4.0"], log_output).tag == "v2.0.0"


def test_render_notes_groups_and_leads_with_breaking():
    commits = sr.parse_log(
        log(
            ("a" * 8, "feat(ci): pr-agent template"),
            ("b" * 8, "fix: typo"),
            ("c" * 8, "feat(cli)!: drop --legacy"),
            ("d" * 8, "docs: contract"),
        )
    )
    notes = sr.render_notes(commits, compare_url="https://git.example/-/compare/v1...v2")
    assert notes.index("### Breaking changes") < notes.index("### Features")
    assert notes.index("### Features") < notes.index("### Fixes") < notes.index("### Documentation")
    assert "- **ci**: pr-agent template (aaaaaaaa)" in notes
    assert "[Full changes](https://git.example/-/compare/v1...v2)" in notes


def test_render_notes_uses_breaking_trailer_text():
    commits = sr.parse_log(log(("a" * 8, "fix(cli): x\n\nBREAKING CHANGE: --legacy is gone")))
    assert "- **cli**: --legacy is gone (aaaaaaaa)" in sr.render_notes(commits)


def test_plan_release_end_to_end():
    plan = sr.plan_release(
        ["v0.1.0", "v0.1.1"],
        log(("a" * 8, "feat(ci): new template"), ("b" * 8, "chore: noise")),
        compare_url_template="https://git.example/-/compare/{previous}...{tag}",
    )
    assert (plan.released, plan.level, plan.previous_tag, plan.tag) == (True, "minor", "v0.1.1", "v0.2.0")
    assert "### Features" in plan.notes
    assert "compare/v0.1.1...v0.2.0" in plan.notes


def test_plan_release_no_releasable_commits():
    plan = sr.plan_release(["v0.1.1"], log(("a", "docs: only docs")))
    assert plan.released is False
    assert plan.tag is None
    assert plan.previous_tag == "v0.1.1"


def test_plan_release_first_release_uses_initial_version():
    plan = sr.plan_release([], log(("a", "feat: first")), initial_version="0.1.0")
    assert (plan.tag, plan.previous_tag) == ("v0.1.0", None)
    # No previous tag -> no compare link to render.
    assert "Full changes" not in plan.notes


def test_create_release_posts_tag_and_ref():
    captured = {}

    def fake_request(url, token, token_header, payload):
        captured.update(url=url, token=token, header=token_header, payload=payload)
        return {"_links": {"self": "https://git.example/releases/v0.2.0"}}

    plan = sr.plan_release(["v0.1.1"], log(("a" * 8, "feat: x")))
    sr.create_release(
        "https://git.example/api/v4",
        "42",
        "tok",
        plan,
        ref="deadbeef",
        request=fake_request,
    )
    assert captured["url"] == "https://git.example/api/v4/projects/42/releases"
    assert captured["header"] == "JOB-TOKEN"
    assert captured["payload"]["tag_name"] == "v0.2.0"
    assert captured["payload"]["ref"] == "deadbeef"
    assert captured["payload"]["description"] == plan.notes


def test_create_release_escapes_path_style_project_id():
    captured = {}

    def fake_request(url, token, token_header, payload):
        captured["url"] = url
        return {}

    plan = sr.plan_release([], log(("a", "feat: x")))
    sr.create_release("https://git.example/api/v4/", "eric/weisssrv-lib", "t", plan, "sha", request=fake_request)
    assert captured["url"] == "https://git.example/api/v4/projects/eric%2Fweisssrv-lib/releases"


def test_main_writes_plan_json_and_skips_release(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sr, "git", lambda args, repo_dir=".": "" if args[0] == "tag" else log(("a", "docs: x")))
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["released"] is False
    assert "nothing to release" in capsys.readouterr().out


def test_main_dry_run_creates_nothing(monkeypatch, capsys):
    monkeypatch.setattr(
        sr,
        "git",
        lambda args, repo_dir=".": "v0.1.0\n" if args[0] == "tag" else log(("a" * 8, "feat: x")),
    )

    def explode(*a, **kw):
        raise AssertionError("dry run must not call the API")

    monkeypatch.setattr(sr, "create_release", explode)
    assert sr.main(["--dry-run"]) == 0
    assert "v0.1.0 -> v0.2.0 (minor bump)" in capsys.readouterr().out


# --- api_request: the only code that actually talks to GitLab -----------------

class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def recording_opener(body: bytes = b"{}"):
    """Fake urlopen: captures the Request it is handed, returns `body`."""
    seen = {}

    def opener(request, timeout=None):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        seen["data"] = request.data
        seen["token"] = request.get_header("Job-token")
        seen["content_type"] = request.get_header("Content-type")
        # urllib capitalises header names, so the keys read `Job-token`,
        # `Content-type`, `X-github-api-version`.
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        return FakeResponse(body)

    return opener, seen


def through(requester, opener):
    """The `request` seam wired to a REAL requester over a fake urlopen.

    Lets a create_release/get_release test assert on the wire request the caller
    would actually make — URL, headers and body together — instead of stopping
    at the seam.
    """

    def call(url, token, token_header, payload):
        return requester(url, token, token_header, payload, opener=opener)

    return call


def test_api_request_posts_json_with_the_token_header():
    opener, seen = recording_opener(b'{"_links": {"self": "u"}}')
    result = sr.api_request(
        "https://git.example/api/v4/projects/42/releases", "tok", "JOB-TOKEN",
        {"tag_name": "v0.2.0"}, opener=opener,
    )
    assert result == {"_links": {"self": "u"}}
    assert seen["method"] == "POST"
    assert seen["token"] == "tok"
    assert seen["content_type"] == "application/json"
    assert json.loads(seen["data"].decode()) == {"tag_name": "v0.2.0"}
    assert seen["timeout"] == 60


def test_api_request_gets_when_there_is_no_payload():
    opener, seen = recording_opener(b'{"tag_name": "v0.2.0"}')
    assert sr.api_request("https://git.example/x", "tok", "PRIVATE-TOKEN", None, opener=opener) == {
        "tag_name": "v0.2.0"
    }
    assert seen["method"] == "GET"
    assert seen["data"] is None
    assert seen["content_type"] is None


def test_api_request_empty_body_is_an_empty_dict():
    opener, _ = recording_opener(b"")
    assert sr.api_request("https://git.example/x", "t", "JOB-TOKEN", None, opener=opener) == {}


def _http_error(code: int, body: bytes = b'{"message": "denied"}'):
    return urllib.error.HTTPError("https://git.example/x", code, "err", {}, io.BytesIO(body))


def test_api_request_propagates_http_error_with_its_body():
    def opener(request, timeout=None):
        raise _http_error(403)

    with pytest.raises(urllib.error.HTTPError) as exc:
        sr.api_request("https://git.example/x", "t", "JOB-TOKEN", {"a": 1}, opener=opener)
    assert exc.value.code == 403
    assert b"denied" in exc.value.read()


def test_api_request_propagates_url_error():
    def opener(request, timeout=None):
        raise urllib.error.URLError("name or service not known")

    with pytest.raises(urllib.error.URLError):
        sr.api_request("https://git.example/x", "t", "JOB-TOKEN", {"a": 1}, opener=opener)


def test_api_request_propagates_a_timeout():
    def opener(request, timeout=None):
        raise TimeoutError("timed out")

    with pytest.raises(OSError):
        sr.api_request("https://git.example/x", "t", "JOB-TOKEN", {"a": 1}, opener=opener)


def test_get_release_returns_none_on_404():
    def request(url, token, token_header, payload):
        assert payload is None
        raise _http_error(404)

    assert sr.get_release("https://git.example/api/v4", "42", "t", "v0.2.0", request=request) is None


def test_get_release_reraises_other_http_errors():
    def request(url, token, token_header, payload):
        raise _http_error(500)

    with pytest.raises(urllib.error.HTTPError):
        sr.get_release("https://git.example/api/v4", "42", "t", "v0.2.0", request=request)


def test_get_release_escapes_the_tag_in_the_url():
    seen = {}

    def request(url, token, token_header, payload):
        seen["url"] = url
        return {"tag_name": "v0.2.0"}

    sr.get_release("https://git.example/api/v4/", "eric/lib", "t", "v0.2.0", request=request)
    assert seen["url"] == "https://git.example/api/v4/projects/eric%2Flib/releases/v0.2.0"


def test_the_gitlab_wire_request_is_unchanged_by_the_github_backend():
    """The assertion that must never move: what a GitLab consumer puts on the
    wire. Every field here predates --platform — URL, the single JOB-TOKEN
    header, the annotated-tag message, `ref`, `description`, the 60s timeout."""
    opener, seen = recording_opener()
    plan = sr.plan_release(["v0.1.1"], log(("a" * 8, "feat: x")))
    sr.create_release(
        "https://git.example/api/v4",
        "42",
        "tok",
        plan,
        "deadbeef",
        request=through(sr.api_request, opener),
    )
    assert seen["method"] == "POST"
    assert seen["url"] == "https://git.example/api/v4/projects/42/releases"
    assert seen["headers"] == {"Job-token": "tok", "Content-type": "application/json"}
    assert json.loads(seen["data"].decode()) == {
        "tag_name": "v0.2.0",
        "ref": "deadbeef",
        "name": "v0.2.0",
        "tag_message": "v0.2.0\n\n%s" % plan.notes,
        "description": plan.notes,
    }
    assert seen["timeout"] == 60


# --- github_api_request: the same two calls against GitHub --------------------

def test_github_api_request_posts_json_with_bearer_auth():
    opener, seen = recording_opener(b'{"html_url": "u"}')
    result = sr.github_api_request(
        "https://api.github.test/repos/acme/widget/releases", "ghs_tok",
        payload={"tag_name": "v0.2.0"}, opener=opener,
    )
    assert result == {"html_url": "u"}
    assert seen["method"] == "POST"
    assert seen["headers"] == {
        "Authorization": "Bearer ghs_tok",
        "Accept": "application/vnd.github+json",
        "X-github-api-version": "2022-11-28",
        "Content-type": "application/json",
    }
    # The GitLab header never appears — the token reaches GitHub once, and only
    # in the header GitHub authenticates.
    assert seen["token"] is None
    assert json.loads(seen["data"].decode()) == {"tag_name": "v0.2.0"}
    assert seen["timeout"] == 60


def test_github_api_request_gets_when_there_is_no_payload():
    opener, seen = recording_opener(b'{"tag_name": "v0.2.0"}')
    assert sr.github_api_request("https://api.github.test/x", "tok", opener=opener) == {
        "tag_name": "v0.2.0"
    }
    assert seen["method"] == "GET"
    assert seen["data"] is None
    assert seen["content_type"] is None
    assert seen["headers"]["Accept"] == "application/vnd.github+json"


def test_github_api_request_ignores_the_gitlab_token_header():
    """Both requesters are called through the same 4-argument seam, so the
    GitLab-only argument has to be harmless rather than absent."""
    opener, seen = recording_opener()
    sr.github_api_request("https://api.github.test/x", "tok", "PRIVATE-TOKEN", opener=opener)
    assert "PRIVATE-TOKEN" not in seen["headers"] and "Private-token" not in seen["headers"]
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_github_api_request_empty_body_is_an_empty_dict():
    opener, _ = recording_opener(b"")
    assert sr.github_api_request("https://api.github.test/x", "t", opener=opener) == {}


def test_github_api_request_propagates_http_error_with_its_body():
    def opener(request, timeout=None):
        raise _http_error(422, b'{"message": "Validation Failed"}')

    with pytest.raises(urllib.error.HTTPError) as exc:
        sr.github_api_request("https://api.github.test/x", "t", payload={"a": 1}, opener=opener)
    assert exc.value.code == 422
    assert b"Validation Failed" in exc.value.read()


def test_github_api_request_propagates_url_error():
    def opener(request, timeout=None):
        raise urllib.error.URLError("name or service not known")

    with pytest.raises(urllib.error.URLError):
        sr.github_api_request("https://api.github.test/x", "t", payload={"a": 1}, opener=opener)


def test_github_api_request_propagates_a_timeout():
    def opener(request, timeout=None):
        raise TimeoutError("timed out")

    with pytest.raises(OSError):
        sr.github_api_request("https://api.github.test/x", "t", payload={"a": 1}, opener=opener)


def test_create_release_posts_the_github_payload():
    opener, seen = recording_opener(b'{"html_url": "https://github.test/acme/widget/releases/v0.2.0"}')
    plan = sr.plan_release(["v0.1.1"], log(("a" * 8, "feat: x")))
    sr.create_release(
        "https://api.github.test/",
        "acme/widget",
        "ghs_tok",
        plan,
        "deadbeef",
        request=through(sr.github_api_request, opener),
        platform="github",
    )
    # owner/repo is TWO path segments — the slash must survive quoting.
    assert seen["url"] == "https://api.github.test/repos/acme/widget/releases"
    assert json.loads(seen["data"].decode()) == {
        "tag_name": "v0.2.0",
        "target_commitish": "deadbeef",
        "name": "v0.2.0",
        "body": plan.notes,
    }
    # GitHub's Releases API writes a LIGHTWEIGHT ref: there is nowhere to put a
    # tag message, and the GitLab field names would be silently ignored.
    assert "tag_message" not in json.loads(seen["data"].decode())
    assert "ref" not in json.loads(seen["data"].decode())


def test_get_release_probes_the_github_tags_endpoint():
    seen = {}

    def request(url, token, token_header, payload):
        assert payload is None
        seen["url"] = url
        return {"tag_name": "v0.2.0"}

    sr.get_release(
        "https://api.github.test", "acme/widget", "t", "v0.2.0", request=request, platform="github"
    )
    assert seen["url"] == "https://api.github.test/repos/acme/widget/releases/tags/v0.2.0"


def test_get_release_returns_none_on_404_from_github():
    """A tag with no PUBLISHED Release 404s on GitHub exactly as it does on
    GitLab, so the crash-recovery probe needs no platform-specific reading."""

    def request(url, token, token_header, payload):
        raise _http_error(404, b'{"message": "Not Found"}')

    assert (
        sr.get_release(
            "https://api.github.test", "acme/widget", "t", "v0.2.0",
            request=request, platform="github",
        )
        is None
    )


def test_get_release_reraises_other_http_errors_from_github():
    def request(url, token, token_header, payload):
        raise _http_error(401)

    with pytest.raises(urllib.error.HTTPError):
        sr.get_release(
            "https://api.github.test", "acme/widget", "t", "v0.2.0",
            request=request, platform="github",
        )


def test_the_platform_picks_the_requester_and_gitlab_is_the_default(monkeypatch):
    """The seam that turns --platform into an actual API call. Called without a
    platform — as every pre-existing consumer does — it is the GitLab one."""
    calls = []
    monkeypatch.setattr(sr, "api_request", lambda *a, **kw: calls.append("gitlab") or {})
    monkeypatch.setattr(sr, "github_api_request", lambda *a, **kw: calls.append("github") or {})
    plan = sr.plan_release(["v0.1.1"], log(("a" * 8, "feat: x")))

    sr.create_release("https://git.example/api/v4", "42", "t", plan, "sha")
    sr.get_release("https://git.example/api/v4", "42", "t", "v0.1.1")
    sr.create_release("https://api.github.test", "acme/widget", "t", plan, "sha", platform="github")
    sr.get_release("https://api.github.test", "acme/widget", "t", "v0.1.1", platform="github")

    assert calls == ["gitlab", "gitlab", "github", "github"]


# --- main(): the release-creation half ----------------------------------------

def fake_git(tags, logs, head="deadbeef"):
    """Stub for sr.git covering the three commands main() runs.

    Deliberately no `rev-list`: recovery no longer asks whether the orphan tag is
    on HEAD, so a surviving call would fail here rather than pass silently."""

    def _git(args, repo_dir="."):
        if args[0] == "tag":
            return "\n".join(tags) + "\n"
        if args[0] == "log":
            return logs.get(args[-1], "")
        if args[0] == "rev-parse":
            return head + "\n"
        raise AssertionError("unexpected git call: %r" % (args,))

    return _git


API = ["--api-url", "https://git.example/api/v4", "--project-id", "42"]


def released_tag(*_a, **_kw):
    """get_release stub for the normal case: the previous tag has its Release."""
    return {"tag_name": "v0.1.1"}


def test_main_creates_the_release_and_only_then_reports_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    monkeypatch.setattr(sr, "get_release", released_tag)
    captured = {}

    def fake_create(api_url, project_id, token, plan, ref, token_header="JOB-TOKEN", platform="gitlab"):
        captured.update(tag=plan.tag, ref=ref, token=token, header=token_header, platform=platform)
        return {"_links": {"self": "https://git.example/releases/v0.2.0"}}

    monkeypatch.setattr(sr, "create_release", fake_create)
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), *API]) == 0
    # `platform` is the DEFAULT, not a flag this call passed: an accidental flip
    # of that default would retarget every existing consumer's release.
    assert captured == {
        "tag": "v0.2.0",
        "ref": "deadbeef",
        "token": "tok",
        "header": "JOB-TOKEN",
        "platform": "gitlab",
    }
    payload = json.loads(out.read_text())
    assert (payload["released"], payload["tag"], payload["dry_run"]) == (True, "v0.2.0", False)
    assert "Created release" in capsys.readouterr().out


def test_main_failed_release_never_claims_the_tag_exists(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    monkeypatch.setattr(sr, "get_release", released_tag)

    def boom(*a, **kw):
        raise _http_error(403, b"insufficient scope")

    monkeypatch.setattr(sr, "create_release", boom)
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), *API]) == 1
    payload = json.loads(out.read_text())
    # `artifacts: when: always` publishes this file even on failure.
    assert payload["released"] is False
    assert "403" in payload["error"]
    assert "release creation failed (HTTP 403" in capsys.readouterr().err


def test_main_dry_run_artifact_is_marked_as_such(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), "--dry-run"]) == 0
    payload = json.loads(out.read_text())
    assert (payload["released"], payload["dry_run"], payload["tag"]) == (False, True, "v0.2.0")


def test_main_without_credentials_fails_and_records_why(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RELEASE_TOKEN", raising=False)
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), *API]) == 1
    payload = json.loads(out.read_text())
    assert payload["released"] is False and "missing token" in payload["error"]
    assert "need $RELEASE_TOKEN" in capsys.readouterr().err


def recording_create(monkeypatch):
    """Replace create_release with a recorder; returns the list of calls."""
    calls = []

    def fake_create(api_url, project_id, token, plan, ref, token_header="JOB-TOKEN", platform="gitlab"):
        calls.append(
            {
                "tag": plan.tag,
                "version": plan.version,
                "notes": plan.notes,
                "ref": ref,
                "platform": platform,
            }
        )
        return {}

    monkeypatch.setattr(sr, "create_release", fake_create)
    return calls


def test_main_recovers_a_tag_that_has_no_release(tmp_path, monkeypatch, capsys):
    """A run that died between the tag and the Release halves of the one API call
    leaves an empty commit range — which would read as "nothing to release" forever."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(
        sr,
        "git",
        fake_git(
            ["v0.1.1", "v0.2.0"],
            {"v0.2.0..HEAD": "", "v0.1.1..v0.2.0": log(("a" * 8, "feat: x"))},
        ),
    )
    monkeypatch.setattr(sr, "get_release", lambda *a, **kw: None)
    calls = recording_create(monkeypatch)
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), *API]) == 0
    assert [c["tag"] for c in calls] == ["v0.2.0"]
    assert calls[0]["version"] == "0.2.0"
    assert "### Features" in calls[0]["notes"]
    assert json.loads(out.read_text())["released"] is True
    assert "exists with no Release" in capsys.readouterr().out


def test_main_recovers_an_orphan_tag_that_no_longer_sits_on_head(tmp_path, monkeypatch, capsys):
    """One commit landing after the half-failed run used to orphan the tag forever:
    the range was non-empty again, so the recovery branch was skipped and the next
    tag was cut over commits that appear in no release notes at all."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(
        sr,
        "git",
        fake_git(
            ["v0.1.1", "v0.2.0"],
            {
                "v0.2.0..HEAD": log(("b" * 8, "fix: later work")),
                "v0.1.1..v0.2.0": log(("a" * 8, "feat: the orphaned work")),
            },
        ),
    )
    monkeypatch.setattr(sr, "get_release", lambda *a, **kw: None)
    calls = recording_create(monkeypatch)
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), *API]) == 0

    # The backfill goes first, from the tag's OWN range, and is created from the
    # tag rather than HEAD; only then is the new tag cut from HEAD.
    assert [c["tag"] for c in calls] == ["v0.2.0", "v0.2.1"]
    assert calls[0]["ref"] == "v0.2.0"
    assert "the orphaned work" in calls[0]["notes"]
    assert "the orphaned work" not in calls[1]["notes"]
    assert calls[1]["ref"] == "deadbeef"
    assert "later work" in calls[1]["notes"]

    payload = json.loads(out.read_text())
    assert (payload["released"], payload["tag"], payload["recovered"]) == (True, "v0.2.1", "v0.2.0")
    assert "exists with no Release" in capsys.readouterr().out


def orphan_git(monkeypatch):
    """The half-failed state with new work on top: v0.2.0 is tagged but has no
    Release, and a fix landed after it."""
    monkeypatch.setattr(
        sr,
        "git",
        fake_git(
            ["v0.1.1", "v0.2.0"],
            {
                "v0.2.0..HEAD": log(("b" * 8, "fix: later work")),
                "v0.1.1..v0.2.0": log(("a" * 8, "feat: the orphaned work")),
            },
        ),
    )
    monkeypatch.setattr(sr, "get_release", lambda *a, **kw: None)


def failing_create(monkeypatch, fail_tag, code=403, body=b"denied"):
    """create_release that records every tag it is asked for and raises for one."""
    calls = []

    def fake_create(api_url, project_id, token, plan, ref, token_header="JOB-TOKEN", platform="gitlab"):
        calls.append(plan.tag)
        if plan.tag == fail_tag:
            raise _http_error(code, body)
        return {}

    monkeypatch.setattr(sr, "create_release", fake_create)
    return calls


def test_main_attributes_a_failed_backfill_to_the_tag_that_failed(tmp_path, monkeypatch, capsys):
    """The backfill POST and the new tag's POST are different calls about
    different tags: an artifact that blames the new tag for the OLD tag's failure
    sends whoever reads it (published `when: always`) to the wrong tag."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    orphan_git(monkeypatch)
    calls = failing_create(monkeypatch, "v0.2.0", body=b"tag is protected")
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *API]) == 1

    # The new tag was never attempted — the run stops at the repair.
    assert calls == ["v0.2.0"]
    payload = json.loads(out.read_text())
    assert payload["released"] is False
    assert payload["tag"] == "v0.2.1"
    assert "backfill of v0.2.0 failed" in payload["error"]
    assert "tag is protected" in payload["error"]
    assert "recovered" not in payload
    err = capsys.readouterr().err
    assert "backfilling the missing Release for v0.2.0 failed" in err
    assert "the new tag v0.2.1 was NOT cut" in err


def test_main_records_a_backfill_that_succeeded_before_the_new_tag_failed(
    tmp_path, monkeypatch, capsys
):
    """A Release for the orphan WAS published; the artifact has to say so even
    though the run failed afterwards, or the next run re-derives the repair from
    a state that no longer needs it."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    orphan_git(monkeypatch)
    calls = failing_create(monkeypatch, "v0.2.1", body=b"insufficient scope")
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *API]) == 1

    assert calls == ["v0.2.0", "v0.2.1"]
    payload = json.loads(out.read_text())
    assert payload["released"] is False
    assert payload["recovered"] == "v0.2.0"
    assert "insufficient scope" in payload["error"]
    assert "backfill" not in payload["error"]
    out_text, err = capsys.readouterr()
    assert "Backfilled the missing Release for v0.2.0." in out_text
    assert "release creation failed (HTTP 403" in err


def test_main_announces_the_backfill_only_after_it_succeeded(tmp_path, monkeypatch, capsys):
    """The announcement used to be printed at PLAN time ("… — creating it."), so a
    run that then died at the backfill left that claim in the log unqualified.
    A failed repair may state what it found, never what it did."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    orphan_git(monkeypatch)
    failing_create(monkeypatch, "v0.2.0")
    assert sr.main(["--output", str(tmp_path / "release.json"), *API]) == 1
    out_text = capsys.readouterr().out
    assert "exists with no Release" in out_text  # the finding, which is true
    assert "Backfilled" not in out_text and "creating it" not in out_text


def test_main_still_releases_when_the_recovery_probe_itself_fails(tmp_path, monkeypatch, capsys):
    """The probe is a repair CHECK, not a precondition: a 502 on it must not cost
    a release the POST would have created."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))

    def boom(*a, **kw):
        raise _http_error(502, b"bad gateway")

    monkeypatch.setattr(sr, "get_release", boom)
    calls = recording_create(monkeypatch)
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *API]) == 0

    assert [c["tag"] for c in calls] == ["v0.2.0"]
    payload = json.loads(out.read_text())
    assert payload["released"] is True
    # The skipped repair is in the artifact, not only in the job log.
    assert payload["recovery_check"] == "failed"
    assert "recovered" not in payload
    err = capsys.readouterr().err
    assert "could not check whether v0.1.1 has a Release" in err
    assert "re-run this job" in err


def test_main_survives_an_unreachable_api_on_the_recovery_probe(tmp_path, monkeypatch):
    """URLError/timeouts arrive by a different route than HTTPError and must be
    treated the same — the probe is best-effort in every failure mode."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))

    def boom(*a, **kw):
        raise urllib.error.URLError("name or service not known")

    monkeypatch.setattr(sr, "get_release", boom)
    calls = recording_create(monkeypatch)
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *API]) == 0
    assert [c["tag"] for c in calls] == ["v0.2.0"]
    assert json.loads(out.read_text())["recovery_check"] == "failed"


def test_main_does_not_recreate_a_release_that_exists(monkeypatch, capsys):
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(sr, "git", fake_git(["v0.2.0"], {"v0.2.0..HEAD": ""}))
    monkeypatch.setattr(sr, "get_release", lambda *a, **kw: {"tag_name": "v0.2.0"})
    monkeypatch.setattr(sr, "create_release", lambda *a, **kw: pytest.fail("re-released"))
    assert sr.main(API) == 0
    assert "nothing to release" in capsys.readouterr().out


def test_main_does_not_backfill_when_the_previous_tag_has_its_release(tmp_path, monkeypatch, capsys):
    """The no-op half of the widened lookup: a healthy previous tag must still cut
    exactly one release, not a duplicate of itself."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(
        sr, "git", fake_git(["v0.1.1", "v0.2.0"], {"v0.2.0..HEAD": log(("b" * 8, "fix: later work"))})
    )
    monkeypatch.setattr(sr, "get_release", lambda *a, **kw: {"tag_name": "v0.2.0"})
    calls = recording_create(monkeypatch)
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), *API]) == 0
    assert [c["tag"] for c in calls] == ["v0.2.1"]
    assert "recovered" not in json.loads(out.read_text())
    assert "exists with no Release" not in capsys.readouterr().out


# --- main(): the same two endings against GitHub ------------------------------

GITHUB_API = ["--platform", "github", "--api-url", "https://api.github.test", "--project-id", "acme/widget"]


def test_main_reads_the_github_actions_env_and_creates_the_release(tmp_path, monkeypatch, capsys):
    """Nothing but `--platform github` is passed: the API base, the repository,
    the ref and the token env all come from what Actions injects, the way the
    GitLab path reads CI_API_V4_URL/CI_PROJECT_ID/CI_COMMIT_SHA."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_tok")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/widget")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.test")
    monkeypatch.setenv("GITHUB_SHA", "cafebabe")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    monkeypatch.setattr(sr, "get_release", released_tag)
    captured = {}

    def fake_create(api_url, project_id, token, plan, ref, token_header="JOB-TOKEN", platform="gitlab"):
        captured.update(
            api_url=api_url, project_id=project_id, token=token, ref=ref,
            platform=platform, notes=plan.notes,
        )
        return {"html_url": "https://github.test/acme/widget/releases/tag/v0.2.0"}

    monkeypatch.setattr(sr, "create_release", fake_create)
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), "--platform", "github"]) == 0

    assert captured["api_url"] == "https://api.github.test"
    assert captured["project_id"] == "acme/widget"
    assert captured["token"] == "ghs_tok"  # $GITHUB_TOKEN, without --token-env
    assert captured["ref"] == "cafebabe"  # $GITHUB_SHA, not `git rev-parse HEAD`
    assert captured["platform"] == "github"
    # GitHub's compare path has no `/-/` segment.
    assert "https://github.test/acme/widget/compare/v0.1.1...v0.2.0" in captured["notes"]
    assert json.loads(out.read_text())["released"] is True
    assert "Created release https://github.test/acme/widget/releases/tag/v0.2.0" in capsys.readouterr().out


def test_main_names_the_github_env_when_credentials_are_missing(tmp_path, monkeypatch, capsys):
    """The GitLab token env is not a fallback: a repo that still carries a
    RELEASE_TOKEN variable must not release with it in github mode."""
    monkeypatch.setenv("RELEASE_TOKEN", "gitlab-only")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    out = tmp_path / "release.json"
    assert sr.main(["--output", str(out), *GITHUB_API]) == 1
    assert "missing token" in json.loads(out.read_text())["error"]
    assert "need $GITHUB_TOKEN, $GITHUB_API_URL and $GITHUB_REPOSITORY" in capsys.readouterr().err


def test_main_backfills_an_orphan_tag_on_github(tmp_path, monkeypatch, capsys):
    """Crash recovery survives the move to GitHub, where the orphan state is
    reached by different routes — a tag pushed by hand (what shape B did before
    this backend existed), or a Release deleted while GitHub kept its tag —
    and lands in exactly the same place: an empty commit range that reads as
    "nothing to release" forever. Both halves of the repair hold there:
    `/releases/tags/:tag` 404s for a tag with no published Release, and creating
    a Release for an EXISTING tag is a plain create (`target_commitish` is
    documented as unused once the tag exists)."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_tok")
    orphan_git(monkeypatch)
    calls = recording_create(monkeypatch)
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *GITHUB_API]) == 0

    assert [(c["tag"], c["ref"], c["platform"]) for c in calls] == [
        ("v0.2.0", "v0.2.0", "github"),
        ("v0.2.1", "deadbeef", "github"),
    ]
    assert "the orphaned work" in calls[0]["notes"]
    assert "the orphaned work" not in calls[1]["notes"]
    payload = json.loads(out.read_text())
    assert (payload["released"], payload["tag"], payload["recovered"]) == (True, "v0.2.1", "v0.2.0")
    assert "exists with no Release" in capsys.readouterr().out


def test_main_stops_at_a_failed_github_backfill(tmp_path, monkeypatch, capsys):
    """A tag GitHub will not accept a Release for — a draft already holding the
    name, a protected ref — must stop the run against ITS tag, not be blamed on
    the new one, exactly as on GitLab."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_tok")
    orphan_git(monkeypatch)
    calls = failing_create(monkeypatch, "v0.2.0", code=422, body=b'{"message": "Validation Failed"}')
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *GITHUB_API]) == 1

    assert calls == ["v0.2.0"]
    payload = json.loads(out.read_text())
    assert payload["released"] is False and payload["tag"] == "v0.2.1"
    assert "backfill of v0.2.0 failed" in payload["error"] and "422" in payload["error"]
    assert "the new tag v0.2.1 was NOT cut" in capsys.readouterr().err


def test_main_dry_run_on_github_needs_no_token_or_api(monkeypatch, capsys):
    monkeypatch.setattr(
        sr,
        "git",
        lambda args, repo_dir=".": "v0.1.0\n" if args[0] == "tag" else log(("a" * 8, "feat: x")),
    )
    monkeypatch.setattr(sr, "create_release", lambda *a, **kw: pytest.fail("dry run called the API"))
    assert sr.main(["--platform", "github", "--dry-run"]) == 0
    assert "v0.1.0 -> v0.2.0 (minor bump)" in capsys.readouterr().out


def test_compare_url_template_falls_back_to_no_link():
    """A local run has neither forge's web-URL env; the notes then carry no
    compare link rather than a half-formed one."""
    assert sr.compare_url_template("github", "") is None
    assert sr.compare_url_template("gitlab") is None


def test_plan_existing_tag_reuses_the_tags_own_range():
    plan = sr.plan_existing_tag(
        ["v0.1.1", "v0.2.0"],
        "v0.2.0",
        lambda rng: log(("a" * 8, "feat: x")) if rng == "v0.1.1..v0.2.0" else "",
        compare_url_template="https://git.example/-/compare/{previous}...{tag}",
    )
    assert (plan.released, plan.tag, plan.version, plan.previous_tag) == (True, "v0.2.0", "0.2.0", "v0.1.1")
    assert "compare/v0.1.1...v0.2.0" in plan.notes


# --- run_cli(): failures as one line, not a traceback -------------------------

def test_run_cli_reports_git_stderr(monkeypatch, capsys):
    def boom(args, repo_dir="."):
        raise subprocess.CalledProcessError(128, ["git", "log"], stderr="fatal: bad revision\n")

    monkeypatch.setattr(sr, "git", boom)
    assert sr.run_cli([]) == 1
    assert "fatal: bad revision" in capsys.readouterr().err


def test_run_cli_reports_an_unreachable_api(monkeypatch, capsys):
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    monkeypatch.setattr(sr, "get_release", released_tag)

    def boom(*a, **kw):
        raise urllib.error.URLError("name or service not known")

    monkeypatch.setattr(sr, "create_release", boom)
    assert sr.run_cli(API) == 1
    err = capsys.readouterr().err
    assert "URLError" in err and "Traceback" not in err


# --- Failures that carry no HTTP status --------------------------------------
#
# Only HTTPError has `.code` and `.read()`. A DNS failure, connection reset,
# timeout or non-JSON body arrives as URLError/OSError/JSONDecodeError, and
# before API_ERRORS those escaped main() entirely — so `release.json`, which
# both CI shapes publish `when: always` and which is the only record a failed
# release leaves behind, went unwritten for exactly the failures that are
# hardest to reproduce afterwards.


def test_main_records_a_release_that_failed_without_an_http_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    monkeypatch.setattr(sr, "git", fake_git(["v0.1.1"], {"v0.1.1..HEAD": log(("a" * 8, "feat: x"))}))
    monkeypatch.setattr(sr, "get_release", released_tag)

    def boom(*a, **kw):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(sr, "create_release", boom)
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *API]) == 1

    payload = json.loads(out.read_text())
    assert payload["released"] is False
    assert "URLError" in payload["error"]
    assert "Name or service not known" in payload["error"]
    assert "release creation failed" in capsys.readouterr().err


def test_main_records_a_backfill_that_failed_without_an_http_status(tmp_path, monkeypatch, capsys):
    """Same gap on the repair path, where losing the record costs more: the run
    stops without cutting the new tag, so the artifact is the only thing saying
    which tag needs fixing before a re-run can succeed."""
    monkeypatch.setenv("RELEASE_TOKEN", "tok")
    orphan_git(monkeypatch)

    def boom(*a, **kw):
        raise TimeoutError("timed out")

    monkeypatch.setattr(sr, "create_release", boom)
    out = tmp_path / "release.json"

    assert sr.main(["--output", str(out), *API]) == 1

    payload = json.loads(out.read_text())
    assert payload["released"] is False
    assert payload["tag"] == "v0.2.1"
    assert "backfill of v0.2.0 failed" in payload["error"]
    assert "timed out" in payload["error"]
    assert "the new tag v0.2.1 was NOT cut" in capsys.readouterr().err


def _git_cmd(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_a_higher_tag_on_an_unrelated_branch_does_not_drive_the_release(tmp_path):
    """The one test here that runs real git, because the stub cannot catch this.

    `fake_git` answers every `tag` call with the same list whatever flags it is
    handed, so a stubbed version of this assertion would pass with or without
    `--merged HEAD` — a gate that cannot fail. The tag selected here fixes BOTH
    the next version and the commit range that becomes the release notes, so a
    stray tag on an abandoned line is a wrong release, not a cosmetic detail.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_cmd(["init", "-q", "-b", "main"], repo)
    _git_cmd(["config", "user.email", "test@example.com"], repo)
    _git_cmd(["config", "user.name", "Test"], repo)
    (repo / "f").write_text("1\n")
    _git_cmd(["add", "-A"], repo)
    _git_cmd(["commit", "-qm", "feat: first"], repo)
    _git_cmd(["tag", "v0.1.0"], repo)

    # A spike on its own root, carrying a far higher tag, never merged back.
    _git_cmd(["checkout", "-q", "--orphan", "spike"], repo)
    (repo / "g").write_text("2\n")
    _git_cmd(["add", "-A"], repo)
    _git_cmd(["commit", "-qm", "feat: spike"], repo)
    _git_cmd(["tag", "v9.0.0"], repo)
    _git_cmd(["checkout", "-q", "main"], repo)

    (repo / "f").write_text("2\n")
    _git_cmd(["commit", "-qam", "feat: second"], repo)

    out = tmp_path / "release.json"
    assert sr.main(["--repo-dir", str(repo), "--output", str(out), "--dry-run"]) == 0

    payload = json.loads(out.read_text())
    # v0.2.0, continuing this branch's own line — not v9.1.0 from the spike.
    assert (payload["previous_tag"], payload["tag"]) == ("v0.1.0", "v0.2.0")
