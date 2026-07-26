#!/usr/bin/env python3
"""Keep exactly one open bot MR in sync with the working tree's version bumps.

Run after a consumer-supplied command has rewritten the repo's version pins
(scheduled pipeline). Three outcomes, all idempotent:

  bumps present, branch content changed -> force-push the bot branch, then
                                           create the MR or refresh the open one
  bumps present, branch content identical -> nothing (no push, no MR churn, so a
                                           weekly run does not re-notify)
  no bumps, an open bot MR exists        -> close it

It NEVER merges. Untracked files (report artifacts a check command drops) are
ignored, so only tracked pins are committed.

Stdlib only: `git` for the branch, urllib for the MR API. The decision helpers
(`changed_paths`, `select_open_mr`, `build_description`) and `GitLabClient` (which
takes an injectable transport) are unit-tested without a repo or a server.

Usage (see ci/maintenance/version-bump-bot.yml):
  version-bump-mr.py --title "chore(deps): version bumps" --paths "ansible/ kubernetes/"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence


def changed_paths(porcelain: str, include_untracked: bool = False) -> List[str]:
    """Tracked paths modified in `git status --porcelain` output (renames -> new path)."""
    paths = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        status, entry = line[:2], line[3:]
        if status == "??" and not include_untracked:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        paths.append(entry.strip().strip('"'))
    return sorted(set(paths))


def select_open_mr(
    merge_requests: Sequence[dict], source_branch: str, target_branch: str
) -> Optional[dict]:
    """The bot's own open MR (lowest iid if the API ever returns more than one)."""
    matches = [
        mr
        for mr in merge_requests
        if mr.get("state") == "opened"
        and mr.get("source_branch") == source_branch
        and mr.get("target_branch") == target_branch
    ]
    return sorted(matches, key=lambda mr: mr.get("iid", 0))[0] if matches else None


def build_description(
    paths: Sequence[str],
    diffstat: str,
    report: str = "",
    pipeline_url: str = "",
    max_report_chars: int = 4000,
) -> str:
    """MR body: what changed, the diffstat, and the check command's report."""
    blocks = [
        "Automated version-pin bump. This MR is refreshed by the scheduled bot "
        "pipeline and is **never merged automatically** — review it like any other MR."
    ]
    if paths:
        blocks.append("### Files\n\n" + "\n".join("- `%s`" % p for p in paths))
    if diffstat.strip():
        blocks.append("### Diffstat\n\n```\n%s\n```" % diffstat.strip())
    if report.strip():
        body = report.strip()
        if len(body) > max_report_chars:
            body = body[:max_report_chars] + "\n… truncated, see the job artifact."
        blocks.append("### Report\n\n```\n%s\n```" % body)
    if pipeline_url:
        blocks.append("Produced by %s" % pipeline_url)
    return "\n\n".join(blocks) + "\n"


class GitLabClient:
    """Minimal Merge Requests API client (list / create / update)."""

    def __init__(
        self,
        api_url: str,
        project_id: str,
        token: str,
        token_header: str = "PRIVATE-TOKEN",
        transport: Optional[Callable] = None,
    ) -> None:
        self.base = "%s/projects/%s" % (
            api_url.rstrip("/"),
            urllib.parse.quote(str(project_id), safe=""),
        )
        self.token = token
        self.token_header = token_header
        self.transport = transport or self._urlopen

    def _urlopen(self, url: str, method: str, headers: Dict[str, str], payload: Optional[dict]):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode()
        return json.loads(body) if body else {}

    def _call(self, path: str, method: str = "GET", payload: Optional[dict] = None, query: str = ""):
        headers = {self.token_header: self.token}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        url = self.base + path + (("?" + query) if query else "")
        return self.transport(url, method, headers, payload)

    def list_merge_requests(self, source_branch: str, target_branch: str) -> List[dict]:
        query = urllib.parse.urlencode(
            {"state": "opened", "source_branch": source_branch, "target_branch": target_branch}
        )
        return self._call("/merge_requests", query=query) or []

    def create_merge_request(self, payload: dict) -> dict:
        return self._call("/merge_requests", method="POST", payload=payload)

    def update_merge_request(self, iid: int, payload: dict) -> dict:
        return self._call("/merge_requests/%d" % iid, method="PUT", payload=payload)


# Token values that must never reach the job log — git echoes the push URL on
# failure, and CI variable masking only covers variables the project masked.
_SECRETS: List[str] = []


def redact(text: str) -> str:
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "***")
    return text


def git(args: Sequence[str], repo_dir: str = ".", check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", repo_dir] + list(args),
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return result.stdout


def push_branch(repo_dir: str, branch: str, remote_url: str) -> None:
    """Force-push the current commit as `branch` — the branch is always a fresh
    re-base on the target, so its history is disposable by design."""
    git(["push", "--force", "--quiet", remote_url, "HEAD:refs/heads/%s" % branch], repo_dir)


def remote_tree(repo_dir: str, branch: str, remote_url: str) -> str:
    """Tree hash of the remote bot branch, or "" when it does not exist."""
    fetched = subprocess.run(
        ["git", "-C", repo_dir, "fetch", "--quiet", remote_url, "refs/heads/%s" % branch],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if fetched.returncode != 0:
        return ""
    return git(["rev-parse", "FETCH_HEAD^{tree}"], repo_dir, check=False).strip()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--branch", default="bot/version-bumps")
    parser.add_argument("--target-branch", default=os.environ.get("CI_DEFAULT_BRANCH", "main"))
    parser.add_argument("--title", default="chore(deps): version bumps")
    parser.add_argument("--commit-message", default="chore(deps): update pinned versions")
    parser.add_argument("--paths", default=".", help="Space-separated paths to stage.")
    parser.add_argument("--labels", default="", help="Comma-separated MR labels.")
    parser.add_argument("--report-path", default="", help="File embedded in the MR description.")
    parser.add_argument("--git-user-name", default="version-bump-bot")
    parser.add_argument("--git-user-email", default="version-bump-bot@noreply.invalid")
    parser.add_argument(
        "--remote-url",
        default="",
        help="Push URL; default builds one from $CI_SERVER_HOST/$CI_PROJECT_PATH and the token.",
    )
    parser.add_argument("--api-url", default=os.environ.get("CI_API_V4_URL", ""))
    parser.add_argument("--project-id", default=os.environ.get("CI_PROJECT_ID", ""))
    parser.add_argument("--token-env", default="BOT_TOKEN")
    parser.add_argument("--dry-run", action="store_true", help="Report the decision; change nothing.")
    args = parser.parse_args(argv)

    repo = args.repo_dir
    paths = args.paths.split() or ["."]
    changed = changed_paths(git(["status", "--porcelain", "--"] + paths, repo))
    token = os.environ.get(args.token_env, "")
    _SECRETS.append(token)

    if not args.dry_run and (not token or not args.api_url or not args.project_id):
        print(
            "ERROR: need $%s, $CI_API_V4_URL and $CI_PROJECT_ID." % args.token_env,
            file=sys.stderr,
        )
        return 1

    client = GitLabClient(args.api_url, args.project_id, token) if not args.dry_run else None
    open_mr = (
        select_open_mr(
            client.list_merge_requests(args.branch, args.target_branch),
            args.branch,
            args.target_branch,
        )
        if client
        else None
    )

    if not changed:
        print("No version bumps in %s." % " ".join(paths))
        if open_mr:
            print("Closing stale bot MR !%s." % open_mr["iid"])
            client.update_merge_request(open_mr["iid"], {"state_event": "close"})
        return 0

    print("Version bumps in:\n" + "\n".join("  " + p for p in changed))
    diffstat = git(["diff", "--stat", "--"] + paths, repo)
    report = ""
    if args.report_path and os.path.exists(args.report_path):
        with open(args.report_path) as handle:
            report = handle.read()
    description = build_description(
        changed, diffstat, report, os.environ.get("CI_PIPELINE_URL", "")
    )

    if args.dry_run:
        print("--dry-run: would push %s and open/refresh the MR.\n\n%s" % (args.branch, description))
        return 0

    remote_url = args.remote_url or "https://gitlab-ci-token:%s@%s/%s.git" % (
        token,
        os.environ.get("CI_SERVER_HOST", ""),
        os.environ.get("CI_PROJECT_PATH", ""),
    )
    git(["config", "user.name", args.git_user_name], repo)
    git(["config", "user.email", args.git_user_email], repo)
    git(["checkout", "-B", args.branch], repo)
    git(["add", "--"] + paths, repo)
    git(["commit", "--quiet", "-m", args.commit_message], repo)

    branch_is_current = (
        remote_tree(repo, args.branch, remote_url) == git(["rev-parse", "HEAD^{tree}"], repo).strip()
    )
    if branch_is_current and open_mr:
        print("Bot branch already carries these exact bumps — leaving it (and the MR) untouched.")
        return 0
    if not branch_is_current:
        # An identical branch with no open MR still needs the MR (re)opened below.
        push_branch(repo, args.branch, remote_url)

    if open_mr:
        print("Refreshing bot MR !%s." % open_mr["iid"])
        client.update_merge_request(
            open_mr["iid"], {"title": args.title, "description": description}
        )
        return 0

    payload = {
        "source_branch": args.branch,
        "target_branch": args.target_branch,
        "title": args.title,
        "description": description,
        "remove_source_branch": True,
    }
    if args.labels:
        payload["labels"] = args.labels
    created = client.create_merge_request(payload)
    print("Opened bot MR %s" % created.get("web_url", created.get("iid", "?")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        body = redact(exc.read().decode(errors="replace"))
        print("ERROR: GitLab API call failed (HTTP %s): %s" % (exc.code, body), file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(
            "ERROR: %s failed: %s" % (redact(" ".join(exc.cmd)), redact(exc.stderr or "")),
            file=sys.stderr,
        )
        sys.exit(1)
