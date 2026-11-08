#!/usr/bin/env python3
"""Every waiting Flux stage must outlast the releases beneath it.

A `wait: true` Kustomization fails when its own timeout expires, so each needs an
explicit `spec.timeout` with headroom. Usage and exit codes: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

KUSTOMIZE_API = "kustomize.toolkit.fluxcd.io/"
DURATION_RE = re.compile(r"(\d+)([hms])")
UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}
SUFFIXES = (".yaml", ".yml")


class Vacuous(RuntimeError):
    """The gate could not inspect its subject — exit 2, never exit 1."""


def to_seconds(value: object) -> int:
    """A Flux duration ('90s', '15m', '1h30m') in seconds."""
    total = sum(int(amount) * UNIT_SECONDS[unit] for amount, unit in DURATION_RE.findall(str(value)))
    if total <= 0:
        raise Vacuous(f"unparseable duration {value!r}")
    return total


def _documents(root: Path) -> list[tuple[Path, dict]]:
    """Every mapping document in the YAML files under `root`."""
    found: list[tuple[Path, dict]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        try:
            raw = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError) as exc:
            # An unparseable file would silently drop its stage out of the walk,
            # which reads as a pass.
            raise Vacuous(f"{path}: {exc}") from exc
        found.extend((path, doc) for doc in raw if isinstance(doc, dict))
    return found


def waiting_stages(cluster_dir: Path) -> list[tuple[Path, dict]]:
    """Flux Kustomizations with `wait: true`.

    Selected on `wait` alone: a stage with no explicit timeout, and one with no
    `spec.path`, are the cases this gate exists for and must not drop out.
    """
    stages = []
    for path, doc in _documents(cluster_dir):
        if doc.get("kind") != "Kustomization":
            continue
        if not str(doc.get("apiVersion", "")).startswith(KUSTOMIZE_API):
            continue
        if (doc.get("spec") or {}).get("wait"):
            stages.append((path, doc))
    return stages


def release_timeouts(repo_root: Path, stage_path: object) -> dict[str, int]:
    """The longest declared timeout of every HelmRelease under a stage path.

    An omitted `spec.path` means the source root, which is Flux's own default.
    """
    directory = repo_root / str(stage_path or ".").lstrip("./")
    if not directory.is_dir():
        raise Vacuous(f"stage path {stage_path!r} does not resolve under {repo_root}")
    timeouts: dict[str, int] = {}
    for path, doc in _documents(directory):
        if doc.get("kind") != "HelmRelease":
            continue
        spec = doc.get("spec") or {}
        declared = [spec.get("timeout")]
        for phase in ("install", "upgrade"):
            declared.append((spec.get(phase) or {}).get("timeout"))
        name = "%s:%s" % (path, (doc.get("metadata") or {}).get("name", "?"))
        for value in filter(None, declared):
            timeouts[name] = max(to_seconds(value), timeouts.get(name, 0))
    return timeouts


def too_tight(stage_seconds: int, releases: dict[str, int]) -> list[str]:
    """Releases the stage does not outlast. Equal is too tight: no headroom."""
    return [
        f"{name}'s {seconds}s"
        for name, seconds in sorted(releases.items())
        if stage_seconds <= seconds
    ]


def check(repo_root: Path, cluster_dir: Path) -> tuple[int, list[str]]:
    """(exit code, report lines). 0 clean, 1 a violation, 2 nothing inspected."""
    try:
        stages = waiting_stages(cluster_dir)
        if not stages:
            raise Vacuous(f"no wait:true Flux Kustomization under {cluster_dir}")
        violations: list[str] = []
        compared = 0
        for path, doc in stages:
            spec = doc.get("spec") or {}
            name = (doc.get("metadata") or {}).get("name", path.name)
            timeout = spec.get("timeout")
            if not timeout:
                violations.append(
                    f"  {name} ({path}): wait:true with no explicit spec.timeout — it "
                    f"inherits the interval-derived default, which no reviewer sees"
                )
                continue
            releases = release_timeouts(repo_root, spec.get("path"))
            compared += len(releases)
            tight = too_tight(to_seconds(timeout), releases)
            if tight:
                violations.append(
                    f"  {name} ({path}): timeout {timeout} does not exceed "
                    + ", ".join(tight)
                    + " — a slow but healthy install fails the stage"
                )
        if not compared and not violations:
            raise Vacuous(
                "no stage path holds a HelmRelease with an explicit timeout — the "
                "comparison examined nothing"
            )
    except Vacuous as exc:
        return 2, [f"ERROR: {exc}"]

    if violations:
        return 1, ["Flux stage timeouts too tight:", *violations]
    return 0, [
        f"{len(stages)} waiting stage(s) outlast the {compared} release timeout(s) beneath them."
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flux stage-timeout headroom gate.")
    parser.add_argument("--repo-root", default=".", help="repository root spec.path resolves against")
    parser.add_argument(
        "--cluster-dir",
        default="kubernetes/clusters",
        help="directory holding the stage Kustomizations, relative to --repo-root",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    cluster_dir = repo_root / args.cluster_dir
    if not cluster_dir.is_dir():
        print(f"ERROR: --cluster-dir {cluster_dir} does not exist", file=sys.stderr)
        return 2
    code, report = check(repo_root, cluster_dir)
    print("\n".join(report), file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
