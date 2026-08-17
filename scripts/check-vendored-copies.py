#!/usr/bin/env python3
"""Gate a consumer repo's vendored copies of weisssrv-lib files.

The copy relationship is recorded where the copies live: each consumer ships a
manifest (scripts/vendored-manifest.yml by convention) naming the library files
it vendors and the forks it deliberately maintains. The library knows nothing
about its consumers — it publishes an OFFER list (scripts/vendorable-paths.yml)
of the paths it supports vendoring, and this engine, which any consumer runs
against a library checkout at its pinned ref.

Two relationships:

  vendored  byte-identical. A drifted copy means the library's fix is simply
            absent here, and the next re-vendor silently reverts whatever was
            edited locally. Both directions fail.
  forked    deliberately divergent. Asserted to still DIFFER (a converged fork
            belongs under `vendored`), and — when the entry records
            `reconciled_sha256` — that the library side has not moved since the
            fork was last reconciled. Without that, a fork list documents a
            divergence without noticing the upstream change it needs to absorb.

Manifest schema (the consumer owns this file; the library never reads it):

  vendored:
    - scripts/check-doc-links.py            # same path both sides
    - lib: lint/ruff.toml                   # paths differ
      consumer: ruff.toml
  forked:
    - lib: lint/editorconfig
      consumer: .editorconfig
      reason: Per-repo file-type sections on a shared base.   # required
      reconciled_sha256: <sha of the library blob last absorbed>

Library blobs are read at `--ref` when given (`git show <ref>:<path>`). The
fallback to the checkout's working tree is decided ONCE, per REF, not per path:
when the ref does not resolve the tag has not been cut yet (it is cut after the
library MR merges), so a pre-release run compares against the branch it will be
tagged from and says so. When the ref DOES resolve, a path missing at it fails —
a file the library added after the tag is not shipped by that release, and
silently comparing it against a newer working tree would green-light a copy the
consumer's pin cannot deliver. That failure names its DIRECTION: a path still in
the library working tree means the pin lags a manifest addition (bump it), not
that the library dropped the file (drop the entry).

When the library ships its offer list at the ref under test, every manifest
`lib:` path must appear in it: vendoring an unoffered file is how a consumer
ends up depending on library internals no release contract covers. A ref that
predates the offer list skips that arm (and says so) rather than failing
history retroactively.

There is no skip-when-missing path. An unavailable library checkout is an
operator error (exit 2), because a gate that quietly disables itself is not one.

  check-vendored-copies.py [--manifest FILE] [--repo-root DIR]
                           [--lib-path DIR] [--ref GIT_REF] [--list]
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

MANIFEST_RELPATH = "scripts/vendored-manifest.yml"
OFFER_RELPATH = "scripts/vendorable-paths.yml"


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys.

    PyYAML's default keeps the LAST duplicate: a manifest with two `vendored:`
    sections silently drops every entry in the first one — ungating declared
    copies with no visible signal. A duplicate is always an editing accident,
    so it is an error, not a merge.
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

    def __init__(self, lib: str, consumer: str, reason: str = "", reconciled: str = ""):
        self.lib = lib
        self.consumer = consumer
        self.reason = reason
        self.reconciled = reconciled


def _validate_relpath(value: str, kind: str) -> str:
    """A manifest path must stay a plain repo-relative path.

    An absolute path or a `..` component would make the gate read files
    outside the tree it claims to gate — and falsely certify a copy that is
    not in the repository at all. Rejected loudly at parse time; the symlink
    variant of the same escape is caught at check time, where resolution is
    possible.
    """
    text = str(value)
    path = Path(text)
    # Canonical spelling only: pathlib collapses `.` segments and doubled
    # slashes, so `scripts/./tool.py` and `scripts/tool.py` would otherwise
    # count as two distinct destinations and dodge the duplicate check. NUL
    # never belongs in a path handed to the filesystem or git.
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
            allowed.update({"reason", "reconciled_sha256"})
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(
                f"{kind} entry {item['lib']!r} has unknown keys: "
                f"{', '.join(sorted(map(str, unknown)))}"
            )
        reason = str(item.get("reason") or "").strip()
        if kind == "forked" and not reason:
            raise ValueError(f"forked entry {item['lib']} has no `reason:`")
        entries.append(
            Entry(
                _validate_relpath(item["lib"], kind),
                _validate_relpath(item.get("consumer") or item["lib"], kind),
                reason,
                str(item.get("reconciled_sha256") or ""),
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
    """The library's bytes for `relpath` — at `ref` when it resolved, else the
    working tree. `ref` is already known to resolve; a missing path at a
    resolving ref is None, i.e. this release does not ship the file.

    Absence and failure are different answers: `git show` exits non-zero for
    both a path the release does not carry and a repository that cannot serve
    the blob, and reading the second as the first would silently disable
    whatever arm asked. `git ls-tree` confirms which one it was — empty
    output on a clean run is genuine absence; anything else raises.
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
    """The library's offer list at `ref` — None only when a RESOLVING ref
    predates it.

    That is the one historical case worth tolerating. With no ref the compare
    target is the working tree, and a working tree that ships this engine
    ships the offer list beside it — absence there means a broken checkout,
    and returning None would silently disable the membership arm. A malformed
    file is an error at any ref.
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
        # A resolving ref without the offer list is only HISTORY if the engine
        # at that ref predates the offer list too. A release whose engine
        # names the file but does not ship it is broken, and skipping there
        # would certify unoffered paths.
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

    Present in the library working tree means the manifest gained it AHEAD of
    the tag the consumer pins: that release does not ship it, and the fix is to
    bump the pin at adoption. Reporting it as "no longer ships" sends whoever
    reads it to delete an entry the library just added.
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

    Bytes read through a link are not the committed artifact: git stores the
    link's target text, so a working-tree read and `git show` disagree — on
    either side of the comparison.
    """
    probe = root
    for part in Path(relpath).parts:
        probe = probe / part
        if probe.is_symlink():
            return probe
    return None


def _local_file(repo_root: Path, entry: Entry, problems: list[str]) -> Path | None:
    """The entry's path inside the repo, or None (with a problem recorded)
    when it escapes it.

    Parse-time validation already rejects absolute paths and `..`; what is
    left is the symlink variant. One that resolves OUTSIDE the repository
    would have the gate certify bytes it does not actually gate — and even an
    in-repo symlink is not the committed artifact: `read_bytes()` follows it
    while git stores the target text, so a clone-side reader and `git show`
    would disagree with what this run certified.
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

    In a working-tree compare the pre-tag pass would certify the link
    TARGET's bytes and the pinned-ref compare would then read the committed
    link text. At a resolving ref, `git show` returns that target text
    directly — a byte mismatch would surface, but as a misleading "drifted —
    re-vendor it", sending whoever reads it to copy the link text into the
    consumer. Both cases get the same named finding instead.
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
        if (candidate / "scripts" / "check-vendored-copies.py").is_file():
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
    parser.add_argument(
        "--manifest",
        type=Path,
        help=f"consumer manifest (default: <repo-root>/{MANIFEST_RELPATH})",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--lib-path", help="library checkout (default: $WEISSSRV_LIB_PATH)")
    parser.add_argument("--ref", help="git ref to read library blobs at")
    parser.add_argument("--list", action="store_true", help="print the manifest's paths and exit")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    manifest = args.manifest or (repo_root / MANIFEST_RELPATH)
    try:
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
    if args.ref and ref is None:
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

    try:
        problems = check(repo_root, lib_root, vendored, forked, ref, offered)
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
    print(
        f"OK — {len(vendored)} vendored copy/copies identical, {len(forked)} declared fork(s) "
        "still reconciled"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
