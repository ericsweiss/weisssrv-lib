#!/usr/bin/env python3
"""Hold the helm-values release registry to the HelmReleases on disk.

Every HelmRelease is listed for rendering or excluded with a reason, each entry
names a real manifest and its own chart. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

# Matches validate-helm-values.py: repo_name/repo_url are optional, being
# resolved from the manifest's own sourceRef when a release omits them.
REQUIRED_RELEASE_KEYS = ("name", "manifest", "chart")

MANIFEST_GLOBS = ("*.yaml", "*.yml")


class OperatorError(RuntimeError):
    """Input or invocation the gate cannot act on — exit 2, never exit 1."""


def load_registry(path: Path) -> Tuple[List[dict], Dict[str, str]]:
    """The registry's `releases:` list and its `excluded:` path/reason map."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise OperatorError(f"{path} could not be read: {exc}") from exc
    if not isinstance(doc, dict):
        raise OperatorError(f"{path} must be a mapping with a `releases:` list")
    releases = doc.get("releases")
    if not isinstance(releases, list) or not releases:
        raise OperatorError(f"{path} must hold a non-empty `releases:` list")
    if not all(isinstance(r, dict) for r in releases):
        raise OperatorError(f"{path}: every `releases:` entry must be a mapping")
    excluded = doc.get("excluded") or {}
    if not isinstance(excluded, dict):
        raise OperatorError(f"{path}: `excluded:` must be a path/reason mapping")
    return releases, {str(k): str(v or "") for k, v in excluded.items()}


def helmrelease_docs(text: str) -> List[dict]:
    """Every HelmRelease document in one manifest's text, parse failures aside."""
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        # A manifest the registry excludes for being unparseable must not crash
        # the walk; an unexcluded one is reported by the coverage arm instead.
        return []
    return [d for d in docs if isinstance(d, dict) and d.get("kind") == "HelmRelease"]


def found_manifests(
    root: Path, manifest_dirs: List[str]
) -> Tuple[Set[str], Dict[str, List[dict]]]:
    """Paths holding a HelmRelease, and the documents each one holds."""
    paths: Set[str] = set()
    docs: Dict[str, List[dict]] = {}
    for manifest_dir in manifest_dirs:
        tree = root / manifest_dir
        if not tree.is_dir():
            raise OperatorError(
                f"manifest directory {manifest_dir!r} does not exist — "
                "the gate has no corpus to inspect"
            )
        for path in sorted({p for glob in MANIFEST_GLOBS for p in tree.rglob(glob)}):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "HelmRelease" not in text:
                continue
            releases = helmrelease_docs(text)
            if releases:
                rel = path.relative_to(root).as_posix()
                paths.add(rel)
                docs[rel] = releases
    return paths, docs


def _chart_problems(entry: dict, docs: List[dict], rel: str) -> List[str]:
    """A listed entry whose chart or repo does not match the manifest it names.

    Only the first HelmRelease is compared: it is the one `helm template`
    renders for that entry.
    """
    problems: List[str] = []
    spec = ((docs[0].get("spec") or {}).get("chart") or {}).get("spec") or {}
    chart = spec.get("chart")
    if chart and str(chart) != str(entry["chart"]):
        problems.append(
            f"{rel}: registry entry {entry['name']!r} declares chart "
            f"{entry['chart']!r} but the manifest renders {chart!r} — the entry "
            "validates a chart the cluster does not run"
        )
    source = str(((spec.get("sourceRef") or {}).get("name")) or "")
    repo_name = entry.get("repo_name")
    if repo_name and source and str(repo_name) != source:
        problems.append(
            f"{rel}: registry entry {entry['name']!r} declares repo_name "
            f"{repo_name!r} but the manifest's sourceRef names {source!r} — the "
            "version would resolve from the wrong HelmRepository"
        )
    return problems


def check(
    root: Path, registry: Path, manifest_dirs: List[str]
) -> Tuple[List[str], int, int]:
    """(problems, listed entries, HelmRelease manifests found)."""
    releases, excluded = load_registry(registry)
    found, docs = found_manifests(root, manifest_dirs)
    if not found:
        raise OperatorError(
            "no HelmRelease found under "
            + ", ".join(f"{d}/" for d in manifest_dirs)
            + " — a coverage gate that checks nothing is not a gate"
        )

    problems: List[str] = []
    listed: Set[str] = set()
    seen_names: Set[str] = set()
    for entry in releases:
        missing = [k for k in REQUIRED_RELEASE_KEYS if not entry.get(k)]
        if missing:
            problems.append(
                f"{registry.name}: release entry {entry!r} is missing "
                f"{', '.join(missing)}"
            )
            continue
        name = str(entry["name"])
        if name in seen_names:
            problems.append(
                f"{registry.name}: two release entries are named {name!r} — "
                "one of them is never rendered"
            )
        seen_names.add(name)
        rel = str(entry["manifest"])
        if rel in listed:
            problems.append(
                f"{registry.name}: {rel} is listed twice — drop the duplicate"
            )
        listed.add(rel)
        if not (root / rel).is_file():
            problems.append(
                f"{registry.name}: release {name!r} names {rel}, which does not "
                "exist — the entry renders nothing"
            )
        elif rel in docs:
            problems += _chart_problems(entry, docs[rel], rel)
        else:
            problems.append(
                f"{registry.name}: release {name!r} names {rel}, which holds no "
                "HelmRelease document"
            )

    for rel in sorted(found - listed - set(excluded)):
        problems.append(
            f"{rel}: holds a HelmRelease that is neither in {registry.name} "
            "`releases:` nor in its `excluded:` map — its spec.values are "
            "rendered by nothing, so a typo there reaches the cluster"
        )

    for rel in sorted(set(excluded) - found):
        problems.append(
            f"{registry.name}: `excluded:` names {rel}, which holds no "
            "HelmRelease — drop the stale exclusion"
        )

    for rel in sorted(set(excluded) & found):
        if not excluded[rel].strip():
            problems.append(
                f"{registry.name}: `excluded:` entry {rel} carries no reason — "
                "an unexplained exclusion is an untested release"
            )
        if rel in listed:
            problems.append(
                f"{registry.name}: {rel} is both listed and excluded — "
                "the exclusion is dead"
            )

    return problems, len(releases), len(found)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HelmRelease coverage for the helm-values release registry"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--releases", type=Path, default=None,
        help="registry file (default: <repo-root>/scripts/helm-values-releases.yaml)",
    )
    parser.add_argument(
        "--manifest-dir", action="append", default=None,
        help="manifest tree to walk, repeatable (default: kubernetes)",
    )
    args = parser.parse_args(argv)

    root = args.repo_root
    registry = args.releases or (root / "scripts" / "helm-values-releases.yaml")
    try:
        problems, n_listed, n_found = check(
            root, registry, args.manifest_dir or ["kubernetes"]
        )
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if problems:
        print(
            "ERROR: the helm-values release registry disagrees with the "
            "HelmReleases on disk:", file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        "Helm-values coverage OK — %d listed release(s) cover the %d HelmRelease "
        "manifest(s) on disk, exclusions included." % (n_listed, n_found)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
