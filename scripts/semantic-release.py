#!/usr/bin/env python3
"""Cut a GitLab release from the conventional commits since the last version tag.

Decides the bump (feat -> minor, fix/perf/refactor -> patch, `!` or a BREAKING
CHANGE trailer -> major), renders notes grouped by type, and creates the tag and
the Release in ONE Releases API call — that endpoint creates the tag from `ref`
when `tag_name` does not exist yet, which is the only tag-write a CI_JOB_TOKEN
can perform (the Tags API is read-only for job tokens).

No releasable commit -> no release, exit 0. Re-running on an already-released
commit is therefore a no-op.

Stdlib only. The decision path is `plan_release(tags, log_output, ...)`: it takes
raw `git` output and returns the plan, so it is testable without a repo or a
server.

Usage (see ci/release/semantic-release.yml):
  scripts/semantic-release.py --dry-run
  scripts/semantic-release.py --output release.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

# git log record/field separators — safe against any commit-message content.
RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"
LOG_FORMAT = "%H" + FIELD_SEP + "%B" + RECORD_SEP

HEADER_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?:[ \t]+(?P<summary>.+?)[ \t]*$"
)
BREAKING_TRAILER_RE = re.compile(r"^BREAKING[ -]CHANGE:[ \t]*(?P<text>.+?)[ \t]*$", re.MULTILINE)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Types absent from this map appear in the notes but never trigger a release on
# their own (docs, ci, build, test, chore, style, revert).
BUMP_BY_TYPE = {"feat": "minor", "fix": "patch", "perf": "patch", "refactor": "patch"}
LEVEL_RANK = {"patch": 0, "minor": 1, "major": 2}

SECTIONS = (
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactors"),
    ("docs", "Documentation"),
    ("ci", "CI"),
    ("build", "Build"),
    ("test", "Tests"),
    ("style", "Style"),
    ("chore", "Chores"),
    ("revert", "Reverts"),
)


@dataclass
class Commit:
    sha: str
    type: str
    scope: str
    breaking: bool
    summary: str
    breaking_notes: List[str] = field(default_factory=list)

    @property
    def short_sha(self) -> str:
        return self.sha[:8]


@dataclass
class Plan:
    released: bool
    level: Optional[str]
    previous_tag: Optional[str]
    tag: Optional[str]
    version: Optional[str]
    notes: str
    commits: List[Commit]


def parse_commit(sha: str, message: str) -> Optional[Commit]:
    """Parse one commit into a Commit, or None when it is not conventional."""
    lines = message.strip().splitlines()
    if not lines:
        return None
    header = HEADER_RE.match(lines[0].strip())
    if not header:
        return None
    body = "\n".join(lines[1:])
    notes = [m.group("text") for m in BREAKING_TRAILER_RE.finditer(body)]
    return Commit(
        sha=sha,
        type=header.group("type").lower(),
        scope=(header.group("scope") or "").strip(),
        breaking=bool(header.group("breaking")) or bool(notes),
        summary=header.group("summary"),
        breaking_notes=notes,
    )


def parse_log(log_output: str) -> List[Commit]:
    """Parse `git log --format=<LOG_FORMAT>` output; non-conventional commits drop out."""
    commits = []
    for record in log_output.split(RECORD_SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, _, message = record.partition(FIELD_SEP)
        commit = parse_commit(sha.strip(), message)
        if commit:
            commits.append(commit)
    return commits


def bump_level(commits: Sequence[Commit]) -> Optional[str]:
    """Highest release level the commits demand, or None when none is releasable."""
    level = None
    for commit in commits:
        candidate = "major" if commit.breaking else BUMP_BY_TYPE.get(commit.type)
        if candidate and (level is None or LEVEL_RANK[candidate] > LEVEL_RANK[level]):
            level = candidate
    return level


def latest_version_tag(tags: Sequence[str], prefix: str = "v") -> Optional[str]:
    """Highest `<prefix>MAJOR.MINOR.PATCH` tag; tags in any other shape are ignored."""
    best = None
    best_key = ()
    for tag in tags:
        if not tag.startswith(prefix):
            continue
        match = SEMVER_RE.match(tag[len(prefix):])
        if not match:
            continue
        key = tuple(int(part) for part in match.groups())
        if best is None or key > best_key:
            best, best_key = tag, key
    return best


def applied_level(level: str, current: Optional[str], major_on_zero: bool = False) -> str:
    """Demote a breaking change to MINOR while the version is 0.x.

    Matches the documented pre-1.0 allowance: leaving initial development stays a
    deliberate call (`major_on_zero`), not something a `feat!:` subject triggers.
    """
    if level != "major" or major_on_zero or current is None:
        return level
    match = SEMVER_RE.match(current)
    return "minor" if match and match.group(1) == "0" else level


def next_version(current: Optional[str], level: str, initial: str = "0.1.0") -> str:
    """Apply `level` to a bare `MAJOR.MINOR.PATCH` string."""
    if current is None:
        return initial
    match = SEMVER_RE.match(current)
    if not match:
        raise ValueError("not a semver version: %s" % current)
    major, minor, patch = (int(part) for part in match.groups())
    if level == "major":
        return "%d.0.0" % (major + 1)
    if level == "minor":
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def render_notes(commits: Sequence[Commit], compare_url: Optional[str] = None) -> str:
    """Release notes grouped by commit type, breaking changes first."""
    blocks = []
    breaking = [c for c in commits if c.breaking]
    if breaking:
        lines = ["### Breaking changes", ""]
        for commit in breaking:
            for note in commit.breaking_notes or [commit.summary]:
                lines.append("- %s (%s)" % (_prefixed(commit, note), commit.short_sha))
        blocks.append("\n".join(lines))

    grouped: Dict[str, List[Commit]] = {}
    for commit in commits:
        grouped.setdefault(commit.type, []).append(commit)
    for type_name, heading in SECTIONS:
        group = grouped.pop(type_name, [])
        if not group:
            continue
        lines = ["### %s" % heading, ""]
        lines += ["- %s (%s)" % (_prefixed(c, c.summary), c.short_sha) for c in group]
        blocks.append("\n".join(lines))
    for type_name in sorted(grouped):
        lines = ["### %s" % type_name, ""]
        lines += ["- %s (%s)" % (_prefixed(c, c.summary), c.short_sha) for c in grouped[type_name]]
        blocks.append("\n".join(lines))

    if compare_url:
        blocks.append("[Full changes](%s)" % compare_url)
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _prefixed(commit: Commit, text: str) -> str:
    return "**%s**: %s" % (commit.scope, text) if commit.scope else text


def plan_release(
    tags: Sequence[str],
    log_output: str,
    tag_prefix: str = "v",
    initial_version: str = "0.1.0",
    compare_url_template: Optional[str] = None,
    major_on_zero: bool = False,
) -> Plan:
    """Turn raw `git tag` + `git log` output into the release decision.

    `compare_url_template` is formatted with `previous` and `tag`; it is dropped
    when there is no previous tag to compare against.
    """
    previous = latest_version_tag(tags, tag_prefix)
    commits = parse_log(log_output)
    level = bump_level(commits)
    if level is None:
        return Plan(False, None, previous, None, None, "", commits)
    current = previous[len(tag_prefix):] if previous else None
    level = applied_level(level, current, major_on_zero)
    version = next_version(current, level, initial_version)
    tag = tag_prefix + version
    compare_url = (
        compare_url_template.format(previous=previous, tag=tag)
        if compare_url_template and previous
        else None
    )
    return Plan(True, level, previous, tag, version, render_notes(commits, compare_url), commits)


def api_request(
    url: str,
    token: str,
    token_header: str = "JOB-TOKEN",
    payload: Optional[dict] = None,
    opener: Callable = urllib.request.urlopen,
) -> dict:
    """POST (or GET when payload is None) a GitLab API call and return the JSON body."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header(token_header, token)
    if data:
        request.add_header("Content-Type", "application/json")
    with opener(request, timeout=60) as response:
        body = response.read().decode()
    return json.loads(body) if body else {}


def create_release(
    api_url: str,
    project_id: str,
    token: str,
    plan: Plan,
    ref: str,
    token_header: str = "JOB-TOKEN",
    request: Callable = api_request,
) -> dict:
    """Create the tag (from `ref`) and the GitLab Release in one call."""
    url = "%s/projects/%s/releases" % (api_url.rstrip("/"), urllib.parse.quote(str(project_id), safe=""))
    payload = {
        "tag_name": plan.tag,
        "ref": ref,
        "name": plan.tag,
        "tag_message": "%s\n\n%s" % (plan.tag, plan.notes),
        "description": plan.notes,
    }
    return request(url, token, token_header, payload)


def git(args: Sequence[str], repo_dir: str = ".") -> str:
    result = subprocess.run(
        ["git", "-C", repo_dir] + list(args),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return result.stdout


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--tag-prefix", default="v")
    parser.add_argument("--initial-version", default="0.1.0")
    parser.add_argument(
        "--major-on-zero",
        action="store_true",
        help="Let a breaking change cut 1.0.0 from a 0.x version (default: bump MINOR).",
    )
    parser.add_argument(
        "--ref", default=os.environ.get("CI_COMMIT_SHA", ""), help="Commit the tag is created from."
    )
    parser.add_argument("--api-url", default=os.environ.get("CI_API_V4_URL", ""))
    parser.add_argument("--project-id", default=os.environ.get("CI_PROJECT_ID", ""))
    parser.add_argument("--token-env", default="RELEASE_TOKEN")
    parser.add_argument("--token-header", default="JOB-TOKEN", choices=["JOB-TOKEN", "PRIVATE-TOKEN"])
    parser.add_argument("--output", default="", help="Write the plan as JSON to this path.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; create nothing.")
    args = parser.parse_args(argv)

    project_url = os.environ.get("CI_PROJECT_URL", "")
    tags = git(["tag", "--list"], args.repo_dir).split()
    previous = latest_version_tag(tags, args.tag_prefix)
    # A shallow clone can lack the previous tag's commit; the job sets GIT_DEPTH: 0.
    log_range = "%s..HEAD" % previous if previous else "HEAD"
    plan = plan_release(
        tags,
        git(["log", "--no-merges", "--format=" + LOG_FORMAT, log_range], args.repo_dir),
        args.tag_prefix,
        args.initial_version,
        project_url + "/-/compare/{previous}...{tag}" if project_url else None,
        args.major_on_zero,
    )

    if args.output:
        with open(args.output, "w") as handle:
            json.dump(
                {
                    "released": plan.released,
                    "level": plan.level,
                    "previous_tag": plan.previous_tag,
                    "tag": plan.tag,
                    "version": plan.version,
                    "notes": plan.notes,
                },
                handle,
                indent=2,
            )

    if not plan.released:
        print("No releasable commits since %s — nothing to release." % (plan.previous_tag or "the start of history"))
        return 0

    print("%s -> %s (%s bump)\n" % (plan.previous_tag or "(no tag)", plan.tag, plan.level))
    print(plan.notes)
    if args.dry_run:
        print("--dry-run: no release created.")
        return 0

    token = os.environ.get(args.token_env, "")
    if not token or not args.api_url or not args.project_id:
        print(
            "ERROR: need $%s, $CI_API_V4_URL and $CI_PROJECT_ID to create the release." % args.token_env,
            file=sys.stderr,
        )
        return 1
    ref = args.ref or git(["rev-parse", "HEAD"], args.repo_dir).strip()
    try:
        release = create_release(
            args.api_url, args.project_id, token, plan, ref, args.token_header
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        print("ERROR: release creation failed (HTTP %s): %s" % (exc.code, detail), file=sys.stderr)
        return 1
    print("Created release %s" % release.get("_links", {}).get("self", plan.tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
