#!/usr/bin/env python3
"""Gate a consumer repo's vendored copies of weisssrv-lib files.

The copy relationship is recorded once, in the library
(scripts/vendored-paths.yml), and read from there by every consumer — so a file
the library starts or stops shipping reaches all three gates at the next bump
instead of being re-listed by hand in each of them.

Two relationships:

  vendored  byte-identical. A drifted copy means the library's fix is simply
            absent here, and the next re-vendor silently reverts whatever was
            edited locally. Both directions fail.
  forked    deliberately divergent. Asserted to still DIFFER (a converged fork
            belongs under `vendored`), and — when the entry records
            `reconciled_sha256` — that the library side has not moved since the
            fork was last reconciled. Without that, a fork list documents a
            divergence without noticing the upstream change it needs to absorb.

Library blobs are read at `--ref` when given (`git show <ref>:<path>`). The
fallback to the checkout's working tree is decided ONCE, per REF, not per path:
when the ref does not resolve the tag has not been cut yet (it is cut after the
library MR merges), so a pre-release run compares against the branch it will be
tagged from and says so. When the ref DOES resolve, a path missing at it fails —
a file the library added after the tag is not shipped by that release, and
silently comparing it against a newer working tree would green-light a copy the
consumer's pin cannot deliver. That failure names its DIRECTION: a path still in
the library working tree means the pin lags a registry addition (bump it), not
that the library dropped the file (drop the entry).

There is no skip-when-missing path. An unavailable library checkout is an
operator error (exit 2), because a gate that quietly disables itself is not one.

  check-vendored-copies.py --consumer <name> [--repo-root DIR]
                           [--lib-path DIR] [--registry FILE]
                           [--ref GIT_REF] [--list]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

REGISTRY_RELPATH = "scripts/vendored-paths.yml"


class Entry:
    """One registered copy: a library path, a consumer path, and its kind."""

    def __init__(self, lib: str, consumer: str, reason: str = "", reconciled: str = ""):
        self.lib = lib
        self.consumer = consumer
        self.reason = reason
        self.reconciled = reconciled


def parse_entries(raw: list, kind: str) -> list[Entry]:
    entries: list[Entry] = []
    for item in raw or []:
        if isinstance(item, str):
            entries.append(Entry(item, item))
            continue
        if not isinstance(item, dict) or not item.get("lib"):
            raise ValueError(f"{kind} entry needs a `lib:` path: {item!r}")
        reason = str(item.get("reason") or "").strip()
        if kind == "forked" and not reason:
            raise ValueError(f"forked entry {item['lib']} has no `reason:`")
        entries.append(
            Entry(
                item["lib"],
                item.get("consumer") or item["lib"],
                reason,
                str(item.get("reconciled_sha256") or ""),
            )
        )
    return entries


def load_registry(path: Path, consumer: str) -> tuple[list[Entry], list[Entry]]:
    with path.open() as f:
        doc = yaml.safe_load(f)
    consumers = (doc or {}).get("consumers")
    if not isinstance(consumers, dict):
        raise ValueError(f"{path} has no `consumers:` mapping")
    if consumer not in consumers:
        raise ValueError(
            f"{path} has no entry for {consumer!r} (known: {', '.join(sorted(consumers))})"
        )
    block = consumers[consumer] or {}
    return (
        parse_entries(block.get("vendored"), "vendored"),
        parse_entries(block.get("forked"), "forked"),
    )


def ref_resolves(lib_root: Path, ref: str | None) -> bool:
    """Whether `ref` names a commit in the library checkout."""
    if not ref:
        return False
    return (
        subprocess.run(
            ["git", "-C", str(lib_root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True,
        ).returncode
        == 0
    )


def lib_blob(lib_root: Path, relpath: str, ref: str | None) -> bytes | None:
    """The library's bytes for `relpath` — at `ref` when it resolved, else the
    working tree. `ref` is already known to resolve; a missing path at a
    resolving ref is None, i.e. this release does not ship the file."""
    if ref:
        result = subprocess.run(
            ["git", "-C", str(lib_root), "show", f"{ref}:{relpath}"],
            capture_output=True,
        )
        return result.stdout if result.returncode == 0 else None
    path = lib_root / relpath
    return path.read_bytes() if path.is_file() else None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def missing_blob_problem(lib_root: Path, entry: Entry, ref: str | None, kind: str) -> str:
    """Why a registered path has no blob at `ref` — the two causes invert the fix.

    Present in the library working tree means the registry gained it AFTER the
    tag the consumer pins: that release does not ship it, and the fix is to bump
    the pin at adoption. Reporting it as "no longer ships" sends whoever reads it
    to delete an entry the library just added.
    """
    if ref and (lib_root / entry.lib).is_file():
        return (
            f"{entry.consumer}: {entry.lib} is registered and in the library working tree, "
            f"but {ref} does not carry it — that release does not ship it yet. Bump the pin "
            f"once a tag containing it exists; do not drop the entry or the copy."
        )
    if kind == "vendored":
        return f"{entry.consumer}: the library no longer ships {entry.lib} — drop the entry or the copy"
    return f"{entry.consumer}: forked from {entry.lib}, which the library no longer ships"


def check(
    repo_root: Path, lib_root: Path, vendored: list[Entry], forked: list[Entry], ref: str | None
) -> list[str]:
    problems: list[str] = []
    for entry in vendored:
        upstream = lib_blob(lib_root, entry.lib, ref)
        local = repo_root / entry.consumer
        if upstream is None:
            problems.append(missing_blob_problem(lib_root, entry, ref, "vendored"))
            continue
        if not local.is_file():
            problems.append(f"{entry.consumer}: registered as vendored but missing here")
            continue
        if local.read_bytes() != upstream:
            problems.append(f"{entry.consumer}: drifted from {entry.lib} — re-vendor it")

    for entry in forked:
        upstream = lib_blob(lib_root, entry.lib, ref)
        local = repo_root / entry.consumer
        if upstream is None:
            problems.append(missing_blob_problem(lib_root, entry, ref, "forked"))
            continue
        if not local.is_file():
            problems.append(f"{entry.consumer}: registered as a fork but missing here")
            continue
        if local.read_bytes() == upstream:
            problems.append(
                f"{entry.consumer}: identical to {entry.lib} — move the entry to `vendored`"
            )
            continue
        if entry.reconciled and _sha(upstream) != entry.reconciled:
            problems.append(
                f"{entry.consumer}: {entry.lib} changed since this fork was last reconciled — "
                f"absorb the change, then set reconciled_sha256 to {_sha(upstream)}"
            )
    return problems


def resolve_lib_root(explicit: str | None, repo_root: Path) -> Path:
    """The library checkout. An explicit path is taken as given if it exists."""
    if explicit:
        path = Path(explicit)
        if path.is_dir():
            return path
    else:
        env = os.environ.get("WEISSSRV_LIB_PATH")
        candidate = Path(env) if env else repo_root.parent / "weisssrv-lib"
        if (candidate / REGISTRY_RELPATH).is_file():
            return candidate
    print(
        "ERROR: no weisssrv-lib checkout found (pass --lib-path, set $WEISSSRV_LIB_PATH, or "
        f"place one at {repo_root.parent / 'weisssrv-lib'}). This gate never skips.",
        file=sys.stderr,
    )
    # Exit 2, not 1: a missing checkout is a misconfigured gate, not drift.
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate a consumer's vendored copies of weisssrv-lib files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--consumer", required=True, help="key under `consumers:` in the registry")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--lib-path", help="library checkout (default: $WEISSSRV_LIB_PATH)")
    parser.add_argument("--registry", type=Path, help="overrides <lib>/" + REGISTRY_RELPATH)
    parser.add_argument("--ref", help="git ref to read library blobs at")
    parser.add_argument("--list", action="store_true", help="print the registered paths and exit")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    lib_root = resolve_lib_root(args.lib_path, repo_root)
    registry = args.registry or (lib_root / REGISTRY_RELPATH)
    try:
        vendored, forked = load_registry(registry, args.consumer)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.list:
        for entry in vendored:
            print(f"vendored\t{entry.consumer}\t{entry.lib}")
        for entry in forked:
            print(f"forked\t{entry.consumer}\t{entry.lib}\t{entry.reason}")
        return 0

    # One ref decision for the whole run: mixing blobs from two library versions
    # is what makes a per-path fallback silently wrong.
    ref = args.ref if ref_resolves(lib_root, args.ref) else None
    if args.ref and ref is None:
        print(
            f"note: {args.ref} does not resolve in {lib_root} — comparing against the "
            "working tree (the release tag is cut after the library MR merges).",
            file=sys.stderr,
        )

    problems = check(repo_root, lib_root, vendored, forked, ref)
    if problems:
        print(f"Vendored-copy drift in {args.consumer}:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"OK — {len(vendored)} vendored copy/copies identical, {len(forked)} declared fork(s) "
        "still reconciled"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
