#!/usr/bin/env python3
"""Fail a role whose defaults/main.yml declares a variable its README never names.

Scans each <roles-dir>/<role>; exits 0 clean, 1 on an undocumented variable, 2
on an operator error. Contract: docs/SCRIPTS.md.
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

DEFAULT_ROLES_DIR = "ansible_collections/weisssrv/infra/roles"


def declared_keys(defaults: Path) -> list:
    """Top-level keys in a defaults/main.yml; [] when it holds only comments.

    Any other non-mapping document is an operator error: reading no keys out of
    it would certify the role clean.
    """
    try:
        doc = yaml.safe_load(defaults.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"{defaults}: {exc}") from exc
    if doc is None:
        return []
    if not isinstance(doc, dict):
        raise RuntimeError(f"{defaults}: top level is {type(doc).__name__}, not a mapping")
    return list(doc.keys())


def _names(key: str, text: str, table_only: bool = False) -> bool:
    """Whether `text` names `key` as a whole word.

    A bare substring test lets a longer name document a shorter one, the shape a
    half-finished rename leaves. `table_only` narrows the search to table rows.
    """
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])"
    if table_only:
        return any(
            re.search(pattern, line) for line in text.splitlines()
            if line.lstrip().startswith("|")
        )
    return re.search(pattern, text) is not None


def undocumented(role: Path, table_only: bool = False) -> list:
    """Variables `role` defaults but its README never names."""
    defaults = role / "defaults" / "main.yml"
    readme = role / "README.md"
    if not defaults.is_file():
        return []
    if not readme.is_file():
        raise RuntimeError(f"{role.name}: defaults/main.yml with no README.md")
    text = readme.read_text()
    return [
        key for key in declared_keys(defaults)
        if not _names(key, text, table_only=table_only)
    ]


def check(roles_dir: Path, table_only: bool = False) -> "tuple[int, list]":
    """(number of roles whose defaults declared a variable, findings).

    A comment-only defaults file declares nothing, so it does not count toward
    the floor: a roles dir holding only such files compared no variable at all.
    """
    findings = []
    examined = 0
    for role in sorted(p for p in roles_dir.iterdir() if p.is_dir()):
        defaults = role / "defaults" / "main.yml"
        if not defaults.is_file() or not declared_keys(defaults):
            continue
        examined += 1
        missing = undocumented(role, table_only=table_only)
        if missing:
            findings.append(f"{role.name}: {', '.join(missing)}")
    return examined, findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Every role default is named in that role's README.",
        epilog="A variable named anywhere in the README passes; --table-only "
               "accepts only a row of the README's variables table.",
    )
    parser.add_argument(
        "--roles-dir", type=Path, default=Path(DEFAULT_ROLES_DIR),
        help=f"collection roles directory (default: {DEFAULT_ROLES_DIR})",
    )
    parser.add_argument(
        "--table-only", action="store_true",
        help="require a variables-table row, not a mention in prose",
    )
    args = parser.parse_args(argv)

    if not args.roles_dir.is_dir():
        print(f"ERROR: roles dir not found: {args.roles_dir}", file=sys.stderr)
        return 2
    try:
        examined, findings = check(args.roles_dir, table_only=args.table_only)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not examined:
        print(
            f"ERROR: no role under {args.roles_dir} declares a defaults/main.yml "
            "with any variable in it",
            file=sys.stderr,
        )
        return 2

    if findings:
        print(f"ERROR: {len(findings)} role(s) default a variable their README omits:")
        for line in findings:
            print(f"  {line}")
        print("  Add it to the role README's variables table, or drop the default.")
        return 1

    print(f"OK: {examined} role(s) document every variable they default.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
