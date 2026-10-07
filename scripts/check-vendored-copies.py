#!/usr/bin/env python3
"""Gate a consumer repo's vendored copies of weisssrv-lib files against a checkout.

An unavailable library checkout is an operator error, never a skip; exits 0
clean, 1 on drift, 2 on an operator error. Contract: docs/SCRIPTS.md.
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
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

MANIFEST_RELPATH = "scripts/vendored-manifest.yml"
OFFER_RELPATH = "scripts/vendorable-paths.yml"

# Whole-line comment markers by suffix, for the comment-only-fork check. A
# suffix in neither map is never compared this way, so an unknown format is
# never reported as a prose-only fork.
_COMMENT_MARKERS = {
    ".cfg": ("#",),
    ".conf": ("#",),
    ".go": ("//",),
    ".hcl": ("#", "//"),
    ".ini": ("#",),
    ".j2": ("#",),
    ".js": ("//",),
    ".py": ("#",),
    ".sh": ("#",),
    ".tf": ("#", "//"),
    ".toml": ("#",),
    ".ts": ("//",),
    ".txt": ("#",),
    ".yaml": ("#",),
    ".yml": ("#",),
}


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    Last-key-wins would silently drop every entry in a duplicated section,
    ungating declared copies with no visible signal.
    """

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:
                # A sequence/mapping used as a key: the promised clean operator
                # error, not a hashability traceback.
                raise yaml.constructor.ConstructorError(
                    None, None, f"unhashable mapping key {key!r}", key_node.start_mark
                ) from None
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate mapping key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep)


class Entry:
    """One registered copy: a library path, a consumer path, and its kind."""

    def __init__(
        self,
        lib: str,
        consumer: str,
        reason: str = "",
        reconciled: str = "",
        comment_only: bool = False,
    ):
        self.lib = lib
        self.consumer = consumer
        self.reason = reason
        self.reconciled = reconciled
        self.comment_only = comment_only


def _validate_relpath(value: str, kind: str) -> str:
    """A manifest path must stay a plain repo-relative path.

    An absolute path or `..` component would gate files outside the tree.
    The symlink variant of the same escape is caught at check time.
    """
    text = str(value)
    path = Path(text)
    # Canonical spelling only: `scripts/./tool.py` and `scripts/tool.py` would
    # otherwise alias one destination past the duplicate check. NUL never
    # belongs in a path handed to the filesystem or git.
    if (
        not text.strip()
        or "\x00" in text
        or path.is_absolute()
        or ".." in path.parts
        or text != path.as_posix()
    ):
        raise ValueError(
            f"{kind} entry path {value!r} is not a canonical repo-relative path — "
            "absolute paths and `..` gate files outside the repository, and "
            "`.` segments or doubled slashes alias a destination past the "
            "duplicate check"
        )
    return text


def parse_entries(raw: list, kind: str) -> list[Entry]:
    entries: list[Entry] = []
    for item in raw or []:
        if isinstance(item, str):
            # A bare string cannot carry the mandatory `reason:` — the short
            # form would silently bypass exactly the field forks must declare.
            if kind == "forked":
                raise ValueError(f"forked entry {item!r} must be a mapping with a `reason:`")
            _validate_relpath(item, kind)
            entries.append(Entry(item, item))
            continue
        if not isinstance(item, dict) or not item.get("lib"):
            raise ValueError(f"{kind} entry needs a `lib:` path: {item!r}")
        # Unknown keys are typos with consequences: `reconciled_sha265` would
        # silently disable the reconciliation guard it meant to arm.
        allowed = {"lib", "consumer"}
        if kind == "forked":
            allowed.update({"reason", "reconciled_sha256", "comment_only"})
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(
                f"{kind} entry {item['lib']!r} has unknown keys: "
                f"{', '.join(sorted(map(str, unknown)))}"
            )
        reason = str(item.get("reason") or "").strip()
        if kind == "forked" and not reason:
            raise ValueError(f"forked entry {item['lib']} has no `reason:`")
        comment_only = item.get("comment_only", False)
        if not isinstance(comment_only, bool):
            raise ValueError(
                f"forked entry {item['lib']} has a non-boolean `comment_only:` "
                f"({comment_only!r}) — the key declares a fact, so it is true or false"
            )
        entries.append(
            Entry(
                _validate_relpath(item["lib"], kind),
                _validate_relpath(item.get("consumer") or item["lib"], kind),
                reason,
                str(item.get("reconciled_sha256") or ""),
                comment_only,
            )
        )
    return entries


def load_manifest(path: Path) -> tuple[list[Entry], list[Entry]]:
    with path.open() as f:
        doc = yaml.load(f, Loader=_UniqueKeyLoader)
    if not isinstance(doc, dict):
        raise ValueError(f"{path} must contain a mapping")
    # A misspelled section name would otherwise be silently ignored whenever
    # the other section is populated — leaving every copy under it ungated.
    unknown = set(doc) - {"vendored", "forked"}
    if unknown:
        raise ValueError(f"{path} has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    for kind in ("vendored", "forked"):
        if doc.get(kind) is not None and not isinstance(doc[kind], list):
            raise ValueError(f"{path} needs `{kind}:` to be a list")
    if not (doc.get("vendored") or doc.get("forked")):
        raise ValueError(
            f"{path} declares neither `vendored:` nor `forked:` — an empty manifest "
            "gates nothing; delete the file or list the copies"
        )
    vendored = parse_entries(doc.get("vendored"), "vendored")
    forked = parse_entries(doc.get("forked"), "forked")
    # One destination, one entry: two entries writing the same consumer path
    # describe an ambiguous copy relationship, and both could still pass while
    # the bytes happen to match either upstream.
    owners: dict[str, tuple[str, str]] = {}
    for kind, entries in (("vendored", vendored), ("forked", forked)):
        for entry in entries:
            previous = owners.get(entry.consumer)
            if previous is not None:
                raise ValueError(
                    f"{path} lists consumer path {entry.consumer!r} more than once: "
                    f"{previous[0]} from {previous[1]!r}, then {kind} from {entry.lib!r}"
                )
            owners[entry.consumer] = (kind, entry.lib)
    return vendored, forked


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
    """The library's bytes for `relpath` at `ref`, else from the working tree.

    None means the release does not ship the file. A repository that cannot
    serve the blob raises instead, because absence and failure invert the fix.
    """
    if ref:
        result = subprocess.run(
            ["git", "-C", str(lib_root), "show", f"{ref}:{relpath}"],
            capture_output=True,
        )
        if result.returncode == 0:
            return result.stdout
        listed = subprocess.run(
            ["git", "-C", str(lib_root), "ls-tree", ref, "--", relpath],
            capture_output=True,
        )
        if listed.returncode != 0:
            raise ValueError(
                f"git could not inspect {ref}:{relpath} — not a missing file but a "
                f"failing repository: {listed.stderr.decode(errors='replace').strip()}"
            )
        if listed.stdout.strip():
            raise ValueError(
                f"{ref}:{relpath} is in the tree but git show could not serve it — "
                f"treat the checkout as broken: {result.stderr.decode(errors='replace').strip()}"
            )
        return None
    path = lib_root / relpath
    return path.read_bytes() if path.is_file() else None


def load_offer(lib_root: Path, ref: str | None) -> set[str] | None:
    """The library's offer list at `ref`, None when a resolving ref predates it.

    With no ref the compare target is the working tree, where absence means a
    broken checkout and raises. A malformed file is an error at any ref.
    """
    if ref is None and _symlink_component(lib_root, OFFER_RELPATH) is not None:
        raise ValueError(
            f"{OFFER_RELPATH} is a symlink in the library working tree — the offer "
            "must be committed content, not bytes read through a link"
        )
    raw = lib_blob(lib_root, OFFER_RELPATH, ref)
    if raw is None:
        if ref is None:
            raise ValueError(
                f"{OFFER_RELPATH} is missing from the library working tree — a tree "
                "that ships this engine ships the offer list beside it; the "
                "membership arm must not silently skip"
            )
        # A resolving ref without the offer list is history only if the engine
        # at that ref predates it too. An engine that names the file without
        # shipping it is a broken release, and skipping would certify unoffered paths.
        engine = lib_blob(lib_root, "scripts/check-vendored-copies.py", ref)
        if engine is not None and OFFER_RELPATH.encode() in engine:
            raise ValueError(
                f"{ref} ships an engine that reads {OFFER_RELPATH} but not the file "
                "itself — a broken release; the membership arm must not silently skip"
            )
        return None
    # Same loader as the manifest: a duplicate `vendorable:` key would
    # silently discard the first section's offers under last-key-wins.
    doc = yaml.load(raw, Loader=_UniqueKeyLoader)
    # Any non-mapping document (a bare list, a scalar) is the same operator
    # error as a missing `vendorable:` key — reported, never a traceback.
    paths = doc.get("vendorable") if isinstance(doc, dict) else None
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError(f"{OFFER_RELPATH} needs a `vendorable:` list of paths")
    # The offer is a set of library-relative paths — hold it to the same
    # repo-relative rule as manifest entries.
    for p in paths:
        _validate_relpath(p, "vendorable")
    return set(paths)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def missing_blob_problem(lib_root: Path, entry: Entry, ref: str | None, kind: str) -> str:
    """Why a registered path has no blob at `ref` — the two causes invert the fix.

    Present in the library working tree means the manifest gained it ahead of
    the pinned tag: bump the pin rather than drop the entry.
    """
    if ref and (lib_root / entry.lib).is_file():
        return (
            f"{entry.consumer}: {entry.lib} is in the manifest and in the library working tree, "
            f"but {ref} does not carry it — that release does not ship it yet. Bump the pin "
            f"once a tag containing it exists; do not drop the entry or the copy."
        )
    if kind == "vendored":
        return f"{entry.consumer}: the library no longer ships {entry.lib} — drop the entry or the copy"
    return f"{entry.consumer}: forked from {entry.lib}, which the library no longer ships"


def _symlink_component(root: Path, relpath: str) -> Path | None:
    """First symlink component under root along relpath, or None.

    Git stores a link's target text, so a working-tree read and `git show`
    disagree on either side of the comparison.
    """
    probe = root
    for part in Path(relpath).parts:
        probe = probe / part
        if probe.is_symlink():
            return probe
    return None


def _local_file(repo_root: Path, entry: Entry, problems: list[str]) -> Path | None:
    """The entry's path inside the repo, or None with a problem recorded.

    Parse-time validation rejects absolute paths and `..`; what is left is the
    symlink variant, whose bytes are not the committed artifact either.
    """
    local = repo_root / entry.consumer
    link = _symlink_component(repo_root, entry.consumer)
    if link is not None:
        problems.append(
            f"{entry.consumer}: {link.relative_to(repo_root)} is a symlink — "
            "the gate certifies committed file content, and git stores a "
            "symlink's target text, not the bytes read through it"
        )
        return None
    try:
        local.resolve().relative_to(repo_root.resolve())
    except (ValueError, OSError, RuntimeError):
        problems.append(
            f"{entry.consumer}: resolves outside the repository — the gate only "
            "certifies files inside the tree it runs against"
        )
        return None
    return local


def _lib_side_symlink_problem(
    lib_root: Path, entry: Entry, ref: str | None, problems: list[str]
) -> bool:
    """Library-side reads must not go through a symlink on either path.

    Either compare mode would otherwise report a misleading "drifted" finding
    instead of naming the link.
    """
    if ref is None:
        link = _symlink_component(lib_root, entry.lib)
        if link is None:
            return False
        problems.append(
            f"{entry.consumer}: library-side {entry.lib} is a symlink in the working "
            "tree — the pre-tag compare would certify the link target's bytes, and "
            "the pinned-ref compare reads the committed link text instead"
        )
        return True
    listed = subprocess.run(
        ["git", "-C", str(lib_root), "ls-tree", ref, "--", entry.lib],
        capture_output=True,
        text=True,
    )
    # Mode 120000 is a committed symlink. A failing ls-tree is left for
    # lib_blob, which already discriminates absence from a broken repository.
    if listed.returncode == 0 and listed.stdout.startswith("120000"):
        problems.append(
            f"{entry.consumer}: library-side {entry.lib} is a committed symlink at "
            f"{ref} — git serves the link's target text, not vendorable content"
        )
        return True
    return False


def parse_scans(values: list[str]) -> list[tuple[str, str]]:
    """`CONSUMER_DIR=LIB_PREFIX` pairs for the unregistered-twin scan."""
    scans: list[tuple[str, str]] = []
    for raw in values or []:
        consumer_dir, sep, lib_prefix = raw.partition("=")
        if not sep or not consumer_dir.strip() or not lib_prefix.strip():
            raise ValueError(f"--scan takes CONSUMER_DIR=LIB_PREFIX, got {raw!r}")
        scans.append((
            _validate_relpath(consumer_dir.strip().rstrip("/"), "scan"),
            _validate_relpath(lib_prefix.strip().rstrip("/"), "scan"),
        ))
    return scans


def unregistered_twins(
    repo_root: Path,
    scans: list[tuple[str, str]],
    vendored: list[Entry],
    forked: list[Entry],
    offered: set[str],
) -> list[str]:
    """Files under a scanned tree whose library twin is offered but unregistered.

    The per-path arms judge only what the manifest declares. A declared scan dir
    that does not exist is an operator error, never a silent skip.
    """
    registered = {entry.consumer for entry in vendored + forked}
    problems: list[str] = []
    for consumer_dir, lib_prefix in scans:
        tree = repo_root / consumer_dir
        if not tree.is_dir():
            raise ValueError(
                f"--scan names {consumer_dir!r}, which is not a directory in "
                f"{repo_root} — fix the path; the scan must not silently skip"
            )
        for path in sorted(p for p in tree.rglob("*") if p.is_file()):
            relpath = path.relative_to(tree).as_posix()
            consumer = path.relative_to(repo_root).as_posix()
            if f"{lib_prefix}/{relpath}" not in offered or consumer in registered:
                continue
            problems.append(
                f"{consumer}: {lib_prefix}/{relpath} is offered by the library but "
                "this copy is in no manifest entry — register it as vendored or "
                "forked, or rename it so it is not mistaken for a copy"
            )
    return problems


def code_lines(blob: bytes, markers: tuple) -> list[str] | None:
    """`blob`'s lines with blank and whole-line comment lines dropped.

    None when the bytes are not decodable text. A first-line `#!` is kept: a
    changed interpreter is a code change.
    """
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return None
    kept: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if number == 1 and stripped.startswith("#!"):
            kept.append(line)
            continue
        if not stripped or stripped.startswith(markers):
            continue
        kept.append(line)
    return kept


def diverges_only_in_comments(relpath: str, local: bytes, upstream: bytes) -> bool:
    """Whether a fork differs from the library only in comments and blank lines."""
    markers = _COMMENT_MARKERS.get(Path(relpath).suffix)
    if not markers:
        return False
    mine = code_lines(local, markers)
    theirs = code_lines(upstream, markers)
    return mine is not None and theirs is not None and mine == theirs


def check(
    repo_root: Path,
    lib_root: Path,
    vendored: list[Entry],
    forked: list[Entry],
    ref: str | None,
    offered: set[str] | None,
) -> list[str]:
    problems: list[str] = []
    if offered is not None:
        for entry in vendored + forked:
            if entry.lib not in offered:
                problems.append(
                    f"{entry.consumer}: {entry.lib} is not in the library's {OFFER_RELPATH} — "
                    "vendoring an unoffered file depends on a library internal no release "
                    "contract covers; ask the library to offer it, or drop the copy"
                )

    for entry in vendored:
        if _lib_side_symlink_problem(lib_root, entry, ref, problems):
            continue
        upstream = lib_blob(lib_root, entry.lib, ref)
        local = _local_file(repo_root, entry, problems)
        if local is None:
            continue
        if upstream is None:
            problems.append(missing_blob_problem(lib_root, entry, ref, "vendored"))
            continue
        if not local.is_file():
            problems.append(f"{entry.consumer}: listed as vendored but missing here")
            continue
        if local.read_bytes() != upstream:
            problems.append(f"{entry.consumer}: drifted from {entry.lib} — re-vendor it")

    for entry in forked:
        if _lib_side_symlink_problem(lib_root, entry, ref, problems):
            continue
        upstream = lib_blob(lib_root, entry.lib, ref)
        local = _local_file(repo_root, entry, problems)
        if local is None:
            continue
        if upstream is None:
            problems.append(missing_blob_problem(lib_root, entry, ref, "forked"))
            continue
        if not local.is_file():
            problems.append(f"{entry.consumer}: listed as a fork but missing here")
            continue
        local_bytes = local.read_bytes()
        if local_bytes == upstream:
            problems.append(
                f"{entry.consumer}: identical to {entry.lib} — move the entry to `vendored`"
            )
            continue
        prose_only = diverges_only_in_comments(entry.lib, local_bytes, upstream)
        if prose_only and not entry.comment_only:
            problems.append(
                f"{entry.consumer}: diverges from {entry.lib} only in comments and "
                f"blank lines, so its `reason:` ({entry.reason!r}) no longer describes "
                "real divergence — move the entry to `vendored:` and re-vendor, or set "
                "`comment_only: true` when the header is meant to differ"
            )
        elif entry.comment_only and not prose_only:
            problems.append(
                f"{entry.consumer}: declares `comment_only: true` but diverges from "
                f"{entry.lib} in code — drop the key and let the `reason:` carry it"
            )
        if entry.reconciled and _sha(upstream) != entry.reconciled:
            problems.append(
                f"{entry.consumer}: {entry.lib} changed since this fork was last reconciled — "
                f"absorb the change, then set reconciled_sha256 to {_sha(upstream)}"
            )
    return problems


# This engine's own path: the marker that tells a library checkout from any
# other directory. A wrong --lib-path would otherwise report every entry as
# drift instead of as the operator error it is.
_LIB_MARKER = Path("scripts") / "check-vendored-copies.py"


def resolve_lib_root(explicit: str | None, repo_root: Path) -> Path:
    """The library checkout. An explicit path is validated like an implicit one."""
    if explicit:
        candidate = Path(explicit)
    else:
        env = os.environ.get("WEISSSRV_LIB_PATH")
        candidate = Path(env) if env else repo_root.parent / "weisssrv-lib"
    if (candidate / _LIB_MARKER).is_file():
        return candidate
    print(
        "ERROR: no weisssrv-lib checkout found (pass --lib-path, set $WEISSSRV_LIB_PATH, or "
        f"place one at {repo_root.parent / 'weisssrv-lib'}); the path must carry "
        f"{_LIB_MARKER.as_posix()}. This gate never skips.",
        file=sys.stderr,
    )
    # Exit 2, not 1: a missing checkout is a misconfigured gate, not drift.
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate a consumer's vendored copies of weisssrv-lib files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help=f"consumer manifest (default: <repo-root>/{MANIFEST_RELPATH})",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--lib-path", help="library checkout (default: $WEISSSRV_LIB_PATH)")
    parser.add_argument("--ref", help="git ref to read library blobs at")
    parser.add_argument(
        "--require-ref",
        action="store_true",
        help="exit 2 unless --ref resolves, so the comparison is against the pinned "
             "release and not whatever the library working tree happens to hold",
    )
    parser.add_argument(
        "--scan", action="append", default=[], metavar="CONSUMER_DIR=LIB_PREFIX",
        help="report files under CONSUMER_DIR whose offered library twin at "
             "LIB_PREFIX/<relpath> no manifest entry registers; repeatable",
    )
    parser.add_argument("--list", action="store_true", help="print the manifest's paths and exit")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    manifest = args.manifest or (repo_root / MANIFEST_RELPATH)
    try:
        scans = parse_scans(args.scan)
        vendored, forked = load_manifest(manifest)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # `--list` prints the parsed manifest and nothing else — it must not
    # demand the library checkout the compare arms need.
    if args.list:
        for entry in vendored:
            print(f"vendored\t{entry.consumer}\t{entry.lib}")
        for entry in forked:
            print(f"forked\t{entry.consumer}\t{entry.lib}\t{entry.reason}")
        return 0

    lib_root = resolve_lib_root(args.lib_path, repo_root)

    # One ref decision for the whole run: mixing blobs from two library versions
    # is what makes a per-path fallback silently wrong.
    ref = args.ref if ref_resolves(lib_root, args.ref) else None
    if ref is None:
        # CRITICAL: without a resolving ref the compare target is the library
        # working tree, so a pass says nothing about the pinned release. The
        # verdict below says so, and --require-ref refuses to run at all.
        why = f"{args.ref} does not resolve in {lib_root}" if args.ref else "no --ref given"
        if args.require_ref:
            print(
                f"ERROR: --require-ref: {why} — the comparison would be against the "
                "library working tree, which proves nothing about the pinned release.",
                file=sys.stderr,
            )
            return 2
        if args.ref:
            print(
                f"note: {args.ref} does not resolve in {lib_root} — comparing against the "
                "working tree (the release tag is cut after the library MR merges).",
                file=sys.stderr,
            )

    try:
        offered = load_offer(lib_root, ref)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if offered is None:
        print(
            f"note: {OFFER_RELPATH} does not exist at the library ref under test — "
            "skipping the offer-membership arm (that release predates the offer list).",
            file=sys.stderr,
        )
        if scans:
            print(
                "note: --scan needs the offer list to know which files are twins — "
                "the unregistered-twin scan is skipped at this ref too.",
                file=sys.stderr,
            )

    try:
        problems = check(repo_root, lib_root, vendored, forked, ref, offered)
        if scans and offered is not None:
            problems += unregistered_twins(
                repo_root, scans, vendored, forked, offered
            )
    except (ValueError, OSError) as exc:
        # A failing git repository or unreadable file is an operator error —
        # never a traceback, never a silent pass.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(f"Vendored-copy drift against {manifest}:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    scanned = (
        f", {len(scans)} tree(s) scanned for unregistered twins" if scans else ""
    )
    verdict = f"OK at {ref}" if ref else "REF UNVERIFIED (library working tree, not a release)"
    print(
        f"{verdict} — {len(vendored)} vendored copy/copies identical, "
        f"{len(forked)} declared fork(s) still reconciled{scanned}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
