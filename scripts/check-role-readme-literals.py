#!/usr/bin/env python3
"""Fail a role README that documents one site's address or domain.

Scans each <roles-dir>/<role>/README.md; exits 0 clean, 1 on a literal, 2 on an
operator error. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROLES_DIR = "ansible_collections/weisssrv/infra/roles"

# 192.168.0.0/16 is the range a home router hands out, so a literal from it in a
# role README is one site's wiring rather than an illustrative address.
HOME_IPV4 = re.compile(r"(?<![\w.])192\.168\.\d{1,3}\.\d{1,3}(?!\.?\d)")

# `pve-<word>-<nn>` is one site's host naming, not a product spelling, so it
# does not catch pve-firewall, pve-cluster or pve-root-ca.
SITE_HOSTNAME = re.compile(r"\bpve-[a-z]+-\d{2}\b")


def findings_for(readme: Path, domains: tuple, extra: tuple = ()) -> list:
    """Site literals in one README, as `line: text` strings."""
    patterns = (HOME_IPV4, SITE_HOSTNAME) + tuple(extra)
    out = []
    for number, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
        for pattern in patterns:
            for match in pattern.finditer(line):
                out.append(f"{number}: {match.group(0)}")
        lowered = line.lower()
        for domain in domains:
            if domain in lowered:
                out.append(f"{number}: {domain}")
    return out


def check(roles_dir: Path, domains: tuple, extra: tuple = ()) -> "tuple[int, list]":
    """(number of READMEs examined, findings) for every role under `roles_dir`."""
    findings = []
    readmes = sorted(roles_dir.glob("*/README.md"))
    for readme in readmes:
        for hit in findings_for(readme, domains, extra):
            findings.append(f"{readme.parent.name}/README.md:{hit}")
    return len(readmes), findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Role READMEs carry example values, never one site's wiring.",
    )
    parser.add_argument(
        "--roles-dir", type=Path, default=Path(DEFAULT_ROLES_DIR),
        help=f"collection roles directory (default: {DEFAULT_ROLES_DIR})",
    )
    parser.add_argument(
        "--site-domain", action="append", default=[],
        help="a domain no role README may name (repeatable)",
    )
    parser.add_argument(
        "--no-site-domains", action="store_true",
        help="this consumer has no site domain, so run the address arms only",
    )
    parser.add_argument(
        "--site-literal", action="append", default=[],
        help="an extra regex no role README may match (repeatable); this is "
             "where a site whose addressing is not 192.168/16 supplies its own",
    )
    parser.add_argument(
        "--no-site-addresses", action="store_true",
        help="this consumer addresses out of 192.168/16, so the built-in "
             "address arm already covers it",
    )
    args = parser.parse_args(argv)

    if not args.roles_dir.is_dir():
        print(f"ERROR: roles dir not found: {args.roles_dir}", file=sys.stderr)
        return 2
    domains = tuple(d.lower() for d in args.site_domain)
    if not domains and not args.no_site_domains:
        print(
            "ERROR: no --site-domain given, so the domain arm would check "
            "nothing; pass --no-site-domains if this consumer has none",
            file=sys.stderr,
        )
        return 2
    if not args.site_literal and not args.no_site_addresses:
        print(
            "ERROR: no --site-literal given, so the address arm only covers "
            "192.168.x.y; pass your own site range or --no-site-addresses if "
            "192.168/16 is it",
            file=sys.stderr,
        )
        return 2
    try:
        extra = tuple(re.compile(pattern) for pattern in args.site_literal)
    except re.error as exc:
        print(f"ERROR: bad --site-literal regex: {exc}", file=sys.stderr)
        return 2
    try:
        examined, findings = check(args.roles_dir, domains, extra)
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not examined:
        print(f"ERROR: no role README under {args.roles_dir}", file=sys.stderr)
        return 2

    if findings:
        print(f"ERROR: {len(findings)} site literal(s) in role READMEs:")
        for line in findings:
            print(f"  {line}")
        print("  Replace each with an illustrative address or example.com.")
        return 1

    scope = "no 192.168.x.y address and no pve-<role>-nn hostname"
    if args.site_literal:
        scope += " and nothing matching: " + ", ".join(args.site_literal)
    if domains:
        scope += " and none of: " + ", ".join(domains)
    print(f"OK: {examined} role README(s) carry {scope}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
