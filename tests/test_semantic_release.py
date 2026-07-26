"""Tests for scripts/semantic-release.py (conventional-commit release planning).

Run via `pytest tests` (the python-tests CI job runs this automatically).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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
