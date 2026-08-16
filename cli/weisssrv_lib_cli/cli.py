"""Argument parsing + command dispatch for weisssrv-new-project."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, templates

# Both subcommands render a copier template and differ only in which published
# one they name, so the flags are declared once.
_COMMANDS = {
    "new-cluster": (
        templates.CLUSTER_TEMPLATE_URL,
        "cluster",
        "render a cluster template with copier",
    ),
    "new-app": (
        templates.APP_TEMPLATE_URL,
        "tenant app",
        "render an app (tenant) template with copier",
    ),
}


def _add_render_command(sub, name: str) -> None:
    url, subject, help_text = _COMMANDS[name]
    parser = sub.add_parser(
        name,
        help=help_text,
        description=(
            f"Render a weisssrv {subject} template with copier — by default "
            f"{url}, whose tags are the supported sources. Needs the `cluster` "
            "extra (pip install 'weisssrv-lib-cli[cluster]')."
        ),
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=url,
        help=(
            "copier template: a VCS URL or a local template path "
            f"(default: {url})"
        ),
    )
    parser.add_argument(
        "destination", type=Path, help="directory to render into (absent or empty)"
    )
    parser.add_argument(
        "--vcs-ref", help="template tag/branch/commit to render (git sources only)"
    )
    parser.add_argument(
        "--data",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="answer one template question non-interactively (repeatable)",
    )
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="take the template default for every unanswered question",
    )
    parser.add_argument(
        "--pretend", action="store_true", help="render without writing anything"
    )
    parser.add_argument(
        "--trust",
        action="store_true",
        help="allow the template to run tasks/jinja extensions (copier --trust)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="weisssrv-new-project",
        description="Render a weisssrv copier template into a new repo.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    for name in _COMMANDS:
        _add_render_command(sub, name)
    return p


def _render(args) -> int:
    published = _COMMANDS[args.command][0]
    try:
        dest = templates.render(
            args.source,
            args.destination,
            vcs_ref=args.vcs_ref,
            data=templates.parse_data(args.data),
            defaults=args.defaults,
            pretend=args.pretend,
            trust=args.trust,
            published=published,
        )
    except templates.MissingCopierError as exc:
        # Its own code: an unusable environment is not a usage error, and a
        # calling script has to tell the two apart to know what to fix.
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except templates.TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except templates.RenderError as exc:
        print(f"error: copier failed: {exc}", file=sys.stderr)
        return 1
    print(f"{'would render' if args.pretend else 'rendered'} {args.source} -> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in _COMMANDS:
        return _render(args)

    return 2  # unreachable (subparsers required)


if __name__ == "__main__":
    raise SystemExit(main())
