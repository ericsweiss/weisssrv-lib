"""Argument parsing + command dispatch for weisssrv-new-project."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, prune, rename, verify, wire


def _root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="project root (default: current directory)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="weisssrv-new-project",
        description="Scaffold a weisssrv cluster tenant repo "
        "(rename / prune / wire / verify).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    p_rename = sub.add_parser(
        "rename", help="replace changeme-app / changeme-group placeholders"
    )
    p_rename.add_argument("app_slug", help="app slug (a DNS label: lowercase, digits, hyphens)")
    p_rename.add_argument("gitlab_group", help="GitLab namespace path (may be nested)")
    _root_arg(p_rename)

    p_prune = sub.add_parser(
        "prune",
        help="drop unwanted components "
        f"({', '.join(prune.FEATURES)}, or manifest:<file>)",
    )
    p_prune.add_argument("features", nargs="+", help="one or more features to remove")
    _root_arg(p_prune)

    p_wire = sub.add_parser(
        "wire", help=f"enable opt-in components ({', '.join(wire.FEATURES)})"
    )
    p_wire.add_argument("features", nargs="+", help="one or more features to enable")
    _root_arg(p_wire)

    p_verify = sub.add_parser("verify", help="sanity-check a generated project")
    p_verify.add_argument(
        "--no-kustomize",
        action="store_true",
        help="skip the `kustomize build` check",
    )
    _root_arg(p_verify)

    return p


def _report(action: str, changed: list[Path], root: Path) -> None:
    if not changed:
        print(f"{action}: no changes (already applied?)")
        return
    for p in changed:
        try:
            shown = p.relative_to(root)
        except ValueError:
            shown = p
        print(f"  {action} {shown}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root

    if args.command == "rename":
        try:
            changed = rename.rename(root, args.app_slug, args.gitlab_group)
        except rename.RenameError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _report("updated", changed, root)
        print(f"Renamed to app='{args.app_slug}' group='{args.gitlab_group}'.")
        return 0

    if args.command == "prune":
        try:
            changed = prune.prune(root, args.features)
        except prune.PruneError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _report("pruned", changed, root)
        return 0

    if args.command == "wire":
        try:
            changed = wire.wire(root, args.features)
        except wire.WireError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _report("wired", changed, root)
        return 0

    if args.command == "verify":
        ok, problems = verify.verify(root, run_kustomize=not args.no_kustomize)
        for prob in problems:
            print(f"  - {prob}")
        if ok:
            print("verify: OK")
            return 0
        print("verify: FAILED")
        return 1

    return 2  # unreachable (subparsers required)


if __name__ == "__main__":
    raise SystemExit(main())
