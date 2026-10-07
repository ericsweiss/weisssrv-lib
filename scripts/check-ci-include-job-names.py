#!/usr/bin/env python3
"""Resolve every `needs: optional: true` job name to a job something creates.

GitLab ignores an optional need naming no job, so a library rename or a
non-default `job_name` input turns the dependency off. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

try:
    from ci_yaml import CILoader, Reference, jobs, parse_ci  # noqa: E402
except ImportError:
    print(
        "ERROR: ci_yaml.py must sit next to this script — vendor both "
        "(see scripts/vendorable-paths.yml).",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# `$[[ inputs.job_name ]]`, GitLab's interpolation in an included file's keys.
_INPUT_REF = re.compile(r"\$\[\[\s*inputs\.([A-Za-z0-9_-]+)\s*(?:\|[^\]]*)?\]\]")

# Include keys this gate cannot read: the job set lives outside both checkouts.
UNREADABLE_INCLUDE_KEYS = ("remote", "component", "template")


class OperatorError(RuntimeError):
    """Input or invocation the gate cannot act on — exit 2, never exit 1."""


def parse_extra_jobs(values: List[str]) -> Dict[str, str]:
    """`JOB=REASON` pairs for a job created by a source this gate cannot read."""
    extra: Dict[str, str] = {}
    for raw in values or []:
        name, sep, reason = raw.partition("=")
        if not sep or not name.strip() or not reason.strip():
            raise OperatorError(f"--extra-job takes JOB=REASON, got {raw!r}")
        extra[name.strip()] = reason.strip()
    return extra


def _load(path: Path) -> dict:
    try:
        return parse_ci(path.read_text(encoding="utf-8"), loader=CILoader)
    except (OSError, UnicodeDecodeError) as exc:
        raise OperatorError(f"{path} could not be read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise OperatorError(f"{path} is not parseable: {exc}") from exc


def include_entries(doc: dict) -> List[dict]:
    """The `include:` block as a list of mappings, bare strings wrapped."""
    raw = doc.get("include") or []
    if isinstance(raw, dict):
        raw = [raw]
    elif isinstance(raw, str):
        raw = [{"local": raw}]
    elif not isinstance(raw, list):
        return []
    entries: List[dict] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(item)
        elif isinstance(item, str):
            entries.append({"local": item})
    return entries


def spec_inputs(path: Path) -> Dict[str, object]:
    """An included file's declared input defaults, keyed by input name."""
    try:
        docs = [
            d for d in yaml.load_all(path.read_text(encoding="utf-8"), Loader=CILoader)
            if isinstance(d, dict)
        ]
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    for doc in docs:
        spec = doc.get("spec")
        if isinstance(spec, dict) and isinstance(spec.get("inputs"), dict):
            return {
                name: (value or {}).get("default") if isinstance(value, dict) else None
                for name, value in spec["inputs"].items()
            }
    return {}


def resolve_key(key: str, supplied: dict, defaults: Dict[str, object]) -> str | None:
    """A job key with its `$[[ inputs.* ]]` references resolved, or None.

    None means an input the include neither passes nor defaults: GitLab would
    reject the pipeline, so the key names no job this gate can credit.
    """
    unresolved = False

    def repl(match: "re.Match") -> str:
        nonlocal unresolved
        name = match.group(1)
        if isinstance(supplied, dict) and name in supplied:
            return str(supplied[name])
        if name in defaults and defaults[name] is not None:
            return str(defaults[name])
        unresolved = True
        return ""

    resolved = _INPUT_REF.sub(repl, key)
    return None if unresolved else resolved


def created_jobs(
    doc: dict, path: Path, repo_root: Path, lib_root: Path | None
) -> Tuple[Set[str], List[str]]:
    """Job names this file and its resolvable includes create, plus what could
    not be read."""
    created = {
        name for name in jobs(doc)
        # A key still carrying an unresolved interpolation is not a job name.
        if "$[[" not in name
    }
    unreadable: List[str] = []

    for entry in include_entries(doc):
        supplied = entry.get("inputs") if isinstance(entry.get("inputs"), dict) else {}
        if any(key in entry for key in UNREADABLE_INCLUDE_KEYS):
            unreadable.append(
                f"{path.name}: include {entry!r} is not a file in either checkout"
            )
            continue
        if "local" in entry:
            base, files = repo_root, entry["local"]
        elif "project" in entry and "file" in entry:
            if lib_root is None:
                unreadable.append(
                    f"{path.name}: include of {entry.get('project')} needs a "
                    "library checkout (--lib-path or $WEISSSRV_LIB_PATH)"
                )
                continue
            base, files = lib_root, entry["file"]
        else:
            unreadable.append(f"{path.name}: include {entry!r} has no readable file")
            continue
        for relpath in files if isinstance(files, list) else [files]:
            included = base / str(relpath).lstrip("/")
            if not included.is_file():
                unreadable.append(
                    f"{path.name}: included file {relpath} is not in {base}"
                )
                continue
            try:
                included_doc = parse_ci(
                    included.read_text(encoding="utf-8"), loader=CILoader
                )
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                unreadable.append(f"{path.name}: {relpath} is not parseable: {exc}")
                continue
            defaults = spec_inputs(included)
            for key in jobs(included_doc):
                resolved = resolve_key(key, supplied, defaults)
                if resolved is None:
                    unreadable.append(
                        f"{relpath}: job key {key!r} needs an input the include "
                        "neither passes nor defaults"
                    )
                else:
                    created.add(resolved)
    return created, unreadable


def optional_needs(doc: dict) -> Dict[str, Set[str]]:
    """`{job name: the jobs declaring it as an optional need}`."""
    found: Dict[str, Set[str]] = {}
    for job_name, body in jobs(doc).items():
        needs = body.get("needs")
        if isinstance(needs, Reference):
            needs = needs.resolve(doc, [])
        if isinstance(needs, dict):
            needs = [needs]
        for need in needs if isinstance(needs, list) else []:
            if isinstance(need, Reference):
                need = need.resolve(doc, None)
            if not isinstance(need, dict) or not need.get("optional"):
                continue
            target = need.get("job")
            if isinstance(target, str) and target:
                found.setdefault(target, set()).add(job_name)
    return found


def check(
    repo_root: Path,
    ci_files: List[str],
    lib_root: Path | None,
    extra_jobs: Dict[str, str],
) -> Tuple[List[str], int, int]:
    """(problems, optional needs seen, job names created)."""
    created: Set[str] = set(extra_jobs)
    unreadable: List[str] = []
    needs: Dict[str, Set[str]] = {}

    for relpath in ci_files:
        path = repo_root / relpath
        if not path.is_file():
            raise OperatorError(
                f"--ci-file names {relpath}, which does not exist in {repo_root}"
            )
        doc = _load(path)
        file_created, file_unreadable = created_jobs(doc, path, repo_root, lib_root)
        created |= file_created
        unreadable += file_unreadable
        for target, declarers in optional_needs(doc).items():
            needs.setdefault(target, set()).update(
                f"{relpath}:{d}" for d in declarers
            )

    if not created:
        raise OperatorError(
            "no job names were resolved from "
            + ", ".join(ci_files)
            + " or their includes — the gate has nothing to resolve needs against"
        )

    problems: List[str] = []
    for target in sorted(needs):
        if target in created:
            continue
        declarers = ", ".join(sorted(needs[target]))
        problems.append(
            f"{target!r} is an optional need of {declarers} but no include "
            "creates it — GitLab ignores the need silently, so the gate it "
            "waits for does not block the pipeline"
        )
    if problems and unreadable:
        problems.append(
            "the job set is incomplete because these includes could not be "
            "read: " + "; ".join(sorted(unreadable))
            + " — pass --extra-job JOB=REASON for a job they create"
        )
    for name in sorted(extra_jobs):
        if name not in needs:
            problems.append(
                f"--extra-job names {name}, which no optional need references — "
                "drop the stale entry"
            )
    return problems, len(needs), len(created)


def resolve_lib_root(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.environ.get("WEISSSRV_LIB_PATH")
    return Path(env) if env else None


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve optional `needs:` names against the included job set"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--ci-file", action="append", default=None,
        help="pipeline file to check, repeatable (default: .gitlab-ci.yml)",
    )
    parser.add_argument(
        "--lib-path",
        help="library checkout the `project:` includes resolve in "
             "(default: $WEISSSRV_LIB_PATH)",
    )
    parser.add_argument(
        "--extra-job", action="append", default=[], metavar="JOB=REASON",
        help="job created by a source this gate cannot read, with its reason",
    )
    parser.add_argument(
        "--require-optional-needs", action="store_true",
        help="fail when the pipeline declares no optional need at all",
    )
    args = parser.parse_args(argv)

    try:
        problems, n_needs, n_created = check(
            args.repo_root,
            args.ci_file or [".gitlab-ci.yml"],
            resolve_lib_root(args.lib_path),
            parse_extra_jobs(args.extra_job),
        )
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if problems:
        print("ERROR: optional `needs:` that resolve to no job:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    if not n_needs and args.require_optional_needs:
        print(
            "ERROR: no `needs: optional: true` entry found — this pipeline "
            "declares none, so the gate inspected nothing; drop "
            "--require-optional-needs if that is intended",
            file=sys.stderr,
        )
        return 2

    print(
        "Optional needs OK — %d optional need(s) resolve against %d job name(s)."
        % (n_needs, n_created)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
