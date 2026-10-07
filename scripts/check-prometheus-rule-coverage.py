#!/usr/bin/env python3
"""Account for every shipped alert against the promtool unit tests.

The corpus is the release's inline rules, the PrometheusRule CRs and the Loki
ruler files; each alertname needs a test or an exemption. docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

MANIFEST_GLOBS = ("*.yaml", "*.yml")
TEST_SUFFIX = ".test.yaml"
# A test file may be a template in a consumer that renders per-answer tests.
TEST_TEMPLATE_SUFFIX = ".test.yaml.jinja"

# `alertname: Foo` is promtool's own key in alert_rule_test, so the corpus and
# the tests are matched on the one spelling both sides already use.
_ALERTNAME = re.compile(r"^\s*(?:-\s*)?alertname:\s*[\"']?([A-Za-z0-9_.-]+)", re.M)


class OperatorError(RuntimeError):
    """Input or invocation the gate cannot act on — exit 2, never exit 1."""


def parse_untested(values: List[str]) -> Dict[str, str]:
    """`ALERTNAME=REASON` pairs. A reason is mandatory: an unexplained
    exemption is an alert nobody has ever evaluated."""
    untested: Dict[str, str] = {}
    for raw in values or []:
        name, sep, reason = raw.partition("=")
        if not sep or not name.strip() or not reason.strip():
            raise OperatorError(f"--untested takes ALERTNAME=REASON, got {raw!r}")
        untested[name.strip()] = reason.strip()
    return untested


def _group_alerts(groups: object) -> Set[str]:
    """Alertnames in a promtool-shaped `groups:` list."""
    found: Set[str] = set()
    for group in groups if isinstance(groups, list) else []:
        for rule in (group or {}).get("rules") or []:
            if isinstance(rule, dict) and rule.get("alert"):
                found.add(str(rule["alert"]))
    return found


def _docs(path: Path) -> List[dict]:
    try:
        raw = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    return [d for d in raw if isinstance(d, dict)]


def corpus(
    root: Path,
    release: Path | None,
    manifest_dirs: List[str],
    loki_rules_dirs: List[str],
) -> Set[str]:
    """Every alertname this cluster ships, from all three declaration shapes."""
    found: Set[str] = set()

    if release is not None:
        release_path = root / release
        if not release_path.is_file():
            raise OperatorError(
                f"--release names {release}, which does not exist — point it at "
                "the kube-prometheus-stack manifest, or pass --no-release"
            )
        for doc in _docs(release_path):
            values = (doc.get("spec") or {}).get("values") or {}
            for entry in (values.get("additionalPrometheusRulesMap") or {}).values():
                found |= _group_alerts((entry or {}).get("groups"))

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
            if "PrometheusRule" not in text:
                continue
            for doc in _docs(path):
                if doc.get("kind") == "PrometheusRule":
                    found |= _group_alerts((doc.get("spec") or {}).get("groups"))

    for loki_dir in loki_rules_dirs:
        tree = root / loki_dir
        if not tree.is_dir():
            raise OperatorError(
                f"--loki-rules-dir names {loki_dir!r}, which is not a directory"
            )
        for path in sorted({p for glob in MANIFEST_GLOBS for p in tree.rglob(glob)}):
            for doc in _docs(path):
                # A Loki ruler file is a bare `groups:` document; the same file
                # shipped inside a ConfigMap is reached by the walk above.
                if "groups" in doc and "kind" not in doc:
                    found |= _group_alerts(doc.get("groups"))

    return found


def tested_alerts(root: Path, tests_dir: str) -> Tuple[Set[str], int]:
    """Alertnames any promtool test file exercises, and the file count."""
    tree = root / tests_dir
    if not tree.is_dir():
        raise OperatorError(
            f"--tests-dir names {tests_dir!r}, which is not a directory — "
            "the gate cannot tell a tested alert from an untested one"
        )
    found: Set[str] = set()
    files = sorted(
        p for p in tree.rglob("*")
        if p.is_file()
        and (p.name.endswith(TEST_SUFFIX) or p.name.endswith(TEST_TEMPLATE_SUFFIX))
    )
    for path in files:
        found.update(
            _ALERTNAME.findall(path.read_text(encoding="utf-8", errors="replace"))
        )
    return found, len(files)


def check(
    root: Path,
    release: Path | None,
    manifest_dirs: List[str],
    loki_rules_dirs: List[str],
    tests_dir: str,
    untested: Dict[str, str],
) -> Tuple[List[str], int, int, int]:
    """(problems, alerts shipped, alerts tested, test files)."""
    shipped = corpus(root, release, manifest_dirs, loki_rules_dirs)
    if not shipped:
        raise OperatorError(
            "no alert rule found in the release, the PrometheusRule CRs or the "
            "Loki ruler files — a coverage gate that checks nothing is not a gate"
        )
    tested, n_files = tested_alerts(root, tests_dir)

    problems: List[str] = []
    for name in sorted(shipped - tested - set(untested)):
        problems.append(
            f"{name}: shipped but no `{TEST_SUFFIX}` under {tests_dir}/ evaluates "
            "it — add a promtool test, or pass --untested "
            f"{name}=<reason> to record the gap"
        )
    for name in sorted(set(untested) & tested):
        problems.append(
            f"{name}: listed as untested but {tests_dir}/ now evaluates it — "
            "drop the --untested entry"
        )
    for name in sorted(set(untested) - shipped):
        problems.append(
            f"{name}: listed as untested but no rule declares it — drop the "
            "stale entry, or fix the alertname"
        )
    for name in sorted(tested - shipped):
        problems.append(
            f"{name}: evaluated by a test under {tests_dir}/ but no rule declares "
            "it — the test passes against nothing (a renamed or deleted alert)"
        )
    return problems, len(shipped), len(shipped & tested), n_files


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promtool unit-test coverage for the shipped alert corpus"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--tests-dir", required=True,
        help="directory holding the `*.test.yaml` promtool suites",
    )
    parser.add_argument(
        "--release", type=Path, default=None,
        help="kube-prometheus-stack manifest carrying additionalPrometheusRulesMap",
    )
    parser.add_argument(
        "--no-release", action="store_true",
        help="this consumer declares every rule as a PrometheusRule CR",
    )
    parser.add_argument(
        "--manifest-dir", action="append", default=None,
        help="tree to walk for PrometheusRule CRs, repeatable (default: kubernetes)",
    )
    parser.add_argument(
        "--loki-rules-dir", action="append", default=[],
        help="directory of bare `groups:` Loki ruler files, repeatable",
    )
    parser.add_argument(
        "--untested", action="append", default=[], metavar="ALERTNAME=REASON",
        help="alert shipped without a promtool test, with its reason",
    )
    args = parser.parse_args(argv)

    if (args.release is None) == (not args.no_release):
        print(
            "ERROR: pass exactly one of --release <manifest> or --no-release — "
            "an unset release silently drops every inline rule group from the corpus",
            file=sys.stderr,
        )
        return 2

    try:
        problems, n_shipped, n_tested, n_files = check(
            args.repo_root,
            None if args.no_release else args.release,
            args.manifest_dir or ["kubernetes"],
            args.loki_rules_dir,
            args.tests_dir,
            parse_untested(args.untested),
        )
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if problems:
        print("ERROR: alert unit-test coverage is unaccounted for:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        "Alert coverage OK — %d of %d shipped alert(s) evaluated by %d test file(s), "
        "the rest declared untested." % (n_tested, n_shipped, n_files)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
