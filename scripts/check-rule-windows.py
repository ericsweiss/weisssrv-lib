#!/usr/bin/env python3
"""An alert's `for:` hold must be shorter than its accumulating lookback window.

`count_over_time` and `increase` decay out of their own window, so a hold at or
past it never completes for a single burst. Exit codes: docs/SCRIPTS.md.
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

SUFFIXES = (".yaml", ".yml")

# Functions that count over their window and fall back out of it. A rate or an
# avg_over_time expresses a sustained condition, where a longer hold is the
# point, so they stay out.
ACCUMULATING = (
    "count_over_time",
    "sum_over_time",
    "increase",
    "delta",
    "idelta",
    "changes",
    "resets",
)
_CALL_RE = re.compile(r"\b(%s)\s*\(" % "|".join(ACCUMULATING))
_DURATION_RE = re.compile(r"(\d+)(ms|[smhdwy])")
_UNIT_SECONDS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
    "y": 31536000.0,
}
_QUOTES = "\"'`"


class OperatorError(RuntimeError):
    """Bad input or a scan that inspected nothing — exit 2, never exit 1."""


def to_seconds(value: object) -> float:
    """A Prometheus/Loki duration ('15m', '1h30m', '500ms') in seconds."""
    text = str(value).strip()
    total = 0.0
    consumed = 0
    for amount, unit in _DURATION_RE.findall(text):
        total += int(amount) * _UNIT_SECONDS[unit]
        consumed += len(amount) + len(unit)
    if not text or consumed != len(text) or total <= 0:
        raise OperatorError("unparseable duration %r" % value)
    return total


def accumulating_windows(expr: str) -> list[str]:
    """Every range-selector duration an accumulating call in `expr` reads.

    Quoted spans are skipped, so a Loki line filter such as `|= "ipset[ips]"`
    is not read as a selector.
    """
    windows: list[str] = []
    text = str(expr)
    for call in _CALL_RE.finditer(text):
        index = call.end()
        depth = 1
        while index < len(text) and depth:
            char = text[index]
            if char in _QUOTES:
                index = _skip_quoted(text, index)
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "[":
                closing = text.find("]", index)
                if closing == -1:
                    raise OperatorError("unterminated range selector in %r" % expr)
                # A subquery is `[range:step]`; only the range bounds the decay.
                windows.append(text[index + 1:closing].split(":", 1)[0].strip())
                index = closing
            index += 1
    return windows


def _skip_quoted(text: str, index: int) -> int:
    """The index just past the string literal opening at `index`."""
    quote = text[index]
    index += 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    raise OperatorError("unterminated string literal in %r" % text)


def parse_allow(values: list[str] | None) -> dict[str, str]:
    """`ALERT=REASON` pairs. An unexplained exemption is a hole."""
    allowed: dict[str, str] = {}
    for raw in values or []:
        name, sep, reason = raw.partition("=")
        if not sep or not name.strip() or not reason.strip():
            raise OperatorError("--allow takes ALERT=REASON, got %r" % raw)
        allowed[name.strip()] = reason.strip()
    return allowed


def rule_groups(doc: dict) -> list[dict]:
    """Rule groups in a PrometheusRule CR or a plain ruler file."""
    if doc.get("kind") == "PrometheusRule":
        groups = (doc.get("spec") or {}).get("groups")
    else:
        groups = doc.get("groups")
    return [g for g in groups or [] if isinstance(g, dict)]


def _documents(paths: list[Path]) -> list[tuple[Path, dict]]:
    found: list[tuple[Path, dict]] = []
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                p for p in sorted(path.rglob("*")) if p.is_file() and p.suffix in SUFFIXES
            )
        elif path.is_file():
            files.append(path)
        else:
            raise OperatorError("no such file or directory: %s" % path)
    for path in files:
        try:
            raw = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError) as exc:
            # An unparseable file would drop its rules out of the walk, which
            # reads as a pass.
            raise OperatorError("%s: %s" % (path, exc)) from exc
        found.extend((path, doc) for doc in raw if isinstance(doc, dict))
    return found


def check(paths: list[Path], allow: dict[str, str] | None = None) -> tuple[list[str], int]:
    """(findings, rules examined) over the rule files under `paths`."""
    allow = allow or {}
    findings: list[str] = []
    examined = 0
    for path, doc in _documents(paths):
        for group in rule_groups(doc):
            for rule in group.get("rules") or []:
                if not isinstance(rule, dict) or not rule.get("alert"):
                    continue
                hold = rule.get("for")
                if hold is None:
                    continue
                windows = accumulating_windows(rule.get("expr", ""))
                if not windows:
                    continue
                examined += 1
                name = str(rule["alert"])
                if name in allow:
                    print("  %s exempt (%s)" % (name, allow[name]))
                    continue
                hold_seconds = to_seconds(hold)
                tightest = min(windows, key=to_seconds)
                if hold_seconds < to_seconds(tightest):
                    continue
                findings.append(
                    "%s: %s has `for: %s` against a [%s] lookback. The expression "
                    "goes false on the evaluation where the hold would elapse, so a "
                    "single burst never notifies. Shorten the hold well inside the "
                    "window, widen the window, or exempt the alert with a reason."
                    % (path, name, hold, tightest)
                )
    return findings, examined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths", nargs="+", type=Path,
        help="rule files, or directories searched for *.yaml / *.yml",
    )
    parser.add_argument(
        "--allow", action="append", default=[], metavar="ALERT=REASON",
        help="alert whose hold is deliberately at or past its window; repeatable",
    )
    args = parser.parse_args(argv)

    try:
        allow = parse_allow(args.allow)
        findings, examined = check(args.paths, allow)
        if not examined:
            raise OperatorError(
                "no alert with both a `for:` hold and an accumulating lookback "
                "(%s) was examined — check the paths, which are the only thing "
                "this gate reads" % ", ".join(ACCUMULATING)
            )
    except OperatorError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    if findings:
        print("ERROR: alert holds that outlast their own lookback window:", file=sys.stderr)
        for finding in findings:
            print("  - %s" % finding, file=sys.stderr)
        return 1

    print("Rule windows OK: %d alert(s) hold inside their lookback window." % examined)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
