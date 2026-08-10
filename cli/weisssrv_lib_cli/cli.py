"""Argument parsing + command dispatch for weisssrv-new-project."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, cluster, prune, rename, verify, wire


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
    p_rename.add_argument(
        "--ci",
        choices=prune.CI_SHAPES,
        help="also select the CI shape to keep, dropping the others "
        "(equivalent to a following `prune ci:<shape>`; see docs/CI-SHAPES.md)",
    )
    _root_arg(p_rename)

    p_prune = sub.add_parser(
        "prune",
        help="drop unwanted components "
        f"({', '.join(prune.FEATURES)}, manifest:<file>, "
        f"or ci:<{'|'.join(prune.CI_SHAPES)}>)",
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

    p_cluster = sub.add_parser(
        "new-cluster",
        help="render a cluster template with copier",
        description=(
            "Render a weisssrv cluster template with copier — by default "
            f"{cluster.CLUSTER_TEMPLATE_URL}, whose tags are the supported "
            "sources. Needs the `cluster` extra "
            "(pip install 'weisssrv-lib-cli[cluster]')."
        ),
    )
    p_cluster.add_argument(
        "source",
        help=f"copier template: a VCS URL (e.g. {cluster.CLUSTER_TEMPLATE_URL}) "
        "or a local template path",
    )
    p_cluster.add_argument(
        "destination", type=Path, help="directory to render into (absent or empty)"
    )
    p_cluster.add_argument(
        "--vcs-ref", help="template tag/branch/commit to render (git sources only)"
    )
    p_cluster.add_argument(
        "--data",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="answer one template question non-interactively (repeatable)",
    )
    p_cluster.add_argument(
        "--defaults",
        action="store_true",
        help="take the template default for every unanswered question",
    )
    p_cluster.add_argument(
        "--pretend", action="store_true", help="render without writing anything"
    )
    p_cluster.add_argument(
        "--trust",
        action="store_true",
        help="allow the template to run tasks/jinja extensions (copier --trust)",
    )

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


def _new_cluster(args) -> int:
    try:
        dest = cluster.render(
            args.source,
            args.destination,
            vcs_ref=args.vcs_ref,
            data=cluster.parse_data(args.data),
            defaults=args.defaults,
            pretend=args.pretend,
            trust=args.trust,
        )
    except cluster.ClusterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except cluster.RenderError as exc:
        print(f"error: copier failed: {exc}", file=sys.stderr)
        return 1
    print(f"{'would render' if args.pretend else 'rendered'} {args.source} -> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "new-cluster":
        return _new_cluster(args)

    root: Path = args.root

    if args.command == "rename":
        # Everything is validated before any file is touched: the ci: prune is
        # preflighted here so its refusals cannot leave the tree renamed but
        # unpruned.
        try:
            if args.ci:
                prune.validate(root, [f"ci:{args.ci}"])
            changed = rename.rename(root, args.app_slug, args.gitlab_group)
        except (rename.RenameError, prune.PruneError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _report("updated", changed, root)
        print(f"Renamed to app='{args.app_slug}' group='{args.gitlab_group}'.")
        if args.ci:
            try:
                dropped = prune.prune(root, [f"ci:{args.ci}"])
            except prune.PruneError as exc:  # pragma: no cover - argparse gates it
                print(f"error: {exc}", file=sys.stderr)
                return 2
            _report("pruned", dropped, root)
            print(f"CI shape: {args.ci}.")
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
