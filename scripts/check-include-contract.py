#!/usr/bin/env python3
"""Hold a consumer pipeline to the input contract of the library files it includes.

Flags an undeclared `inputs:` key, an omitted default-less (REQUIRED) input, and a
job whose resolved `stage:` the pipeline does not declare. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

try:
    from ci_yaml import CILoader, jobs, parse_ci  # noqa: E402
except ImportError:
    print(
        "ERROR: ci_yaml.py must sit next to this script — vendor both "
        "(see scripts/vendorable-paths.yml).",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# `$[[ inputs.job_name ]]`, GitLab's interpolation inside an included file.
_INPUT_REF = re.compile(r"\$\[\[\s*inputs\.([A-Za-z0-9_-]+)\s*(?:\|[^\]]*)?\]\]")

# GitLab's stages when a pipeline declares none, plus the two that always exist.
_IMPLICIT_STAGES = ("build", "test", "deploy")
_ALWAYS_STAGES = (".pre", ".post")

# The stage GitLab gives a job that declares none. Leaving it unresolved would
# hide the job from the stage arm, and `test` is the stage a pipeline with a
# custom `stages:` list is most likely to have dropped.
_IMPLICIT_STAGE = "test"

# `pages` is a job GitLab stages on its own, so a `pages` key with no `stage:`
# must not be read as an implicit-`test` job.
_NOT_SCANNED_AS_A_JOB = ("pages",)

# Include keys naming a file outside both checkouts: the contract cannot be read.
UNREADABLE_INCLUDE_KEYS = ("remote", "component", "template")


class OperatorError(RuntimeError):
    """Input or invocation the gate cannot act on — exit 2, never exit 1."""


def _load_docs(path: Path) -> List[dict]:
    """Every mapping document in a CI file, `spec:` header first when present."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OperatorError(f"{path} could not be read: {exc}") from exc
    try:
        return [d for d in yaml.load_all(text, Loader=CILoader) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        raise OperatorError(f"{path} is not parseable: {exc}") from exc


def declared_inputs(docs: List[dict]) -> Tuple[Dict[str, dict], List[dict]]:
    """(`spec.inputs` declarations, the job documents).

    The `spec:` header is optional: a template with no inputs is legal and its
    first document already holds jobs.
    """
    header = docs[0] if docs and "spec" in docs[0] else None
    spec = (header or {}).get("spec") or {}
    raw = spec.get("inputs") if isinstance(spec, dict) else None
    inputs = {
        name: (value if isinstance(value, dict) else {})
        for name, value in (raw if isinstance(raw, dict) else {}).items()
    }
    return inputs, (docs[1:] if header is not None else docs)


def declared_stages(doc: dict) -> set:
    """The stages a pipeline's jobs may name."""
    stages = doc.get("stages")
    if not isinstance(stages, list):
        stages = _IMPLICIT_STAGES
    return {str(s) for s in stages} | set(_ALWAYS_STAGES)


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


def resolve(value: object, inputs: Dict[str, dict], passed: dict) -> object:
    """`value` with its `$[[ inputs.* ]]` references substituted, or None.

    None means an input the include neither passes nor defaults: GitLab rejects
    the pipeline, which the REQUIRED-input arm reports on its own.
    """
    if not isinstance(value, str):
        return value
    unresolved = False

    def repl(match: "re.Match") -> str:
        nonlocal unresolved
        name = match.group(1)
        if name in passed:
            return str(passed[name])
        default = inputs.get(name, {}).get("default")
        if default is None:
            unresolved = True
            return ""
        return str(default)

    resolved = _INPUT_REF.sub(repl, value)
    return None if unresolved else resolved


def _input_problems(rel: str, inputs: Dict[str, dict], passed: dict) -> List[str]:
    problems = [
        f"{rel} declares no input {key!r}" for key in passed if key not in inputs
    ]
    # GitLab treats a default-less input as REQUIRED: an omitted one fails
    # pipeline creation, so the gate reports it rather than passing.
    problems += [
        f"{rel} requires input {key!r}, but the include passes none"
        for key, declaration in sorted(inputs.items())
        if key not in passed and "default" not in declaration
    ]
    return problems


def _stage_problems(
    rel: str, job_docs: List[dict], inputs: Dict[str, dict], passed: dict, stages: set
) -> List[str]:
    problems: List[str] = []
    for doc in job_docs:
        for name, body in jobs(doc).items():
            if name in _NOT_SCANNED_AS_A_JOB:
                continue
            if "stage" in body:
                stage = resolve(body["stage"], inputs, passed)
            elif "extends" in body:
                # The stage comes from the extended job, possibly in another
                # file; resolving that chain is out of scope.
                continue
            else:
                stage = _IMPLICIT_STAGE
            if stage is None or stage in stages:
                continue
            job = resolve(name, inputs, passed) or name
            problems.append(
                f"{rel}: job {job!r} resolves to stage {str(stage)!r}, which the "
                "pipeline does not declare"
            )
    return problems


def _included_files(
    entry: dict, repo_root: Path, lib_root: Path | None
) -> Tuple[Path | None, List[str]]:
    """(the checkout the entry's files live in, their relative paths)."""
    if "local" in entry:
        files = entry["local"]
        base = repo_root
    elif "project" in entry and "file" in entry:
        if lib_root is None:
            raise OperatorError(
                f"include of {entry.get('project')!r} needs a library checkout "
                "(--lib-path or $WEISSSRV_LIB_PATH)"
            )
        files = entry["file"]
        base = lib_root
    else:
        return None, []
    return base, [str(f).lstrip("/") for f in (files if isinstance(files, list) else [files])]


def check_pipeline(
    pipeline: Path, repo_root: Path, lib_root: Path | None
) -> Tuple[List[str], List[str], int]:
    """(contract failures, includes not contract-checked, includes checked)."""
    doc = parse_ci(_read(pipeline), loader=CILoader)
    stages = declared_stages(doc)
    problems: List[str] = []
    unreadable: List[str] = []
    checked = 0
    for entry in include_entries(doc):
        if any(key in entry for key in UNREADABLE_INCLUDE_KEYS):
            unreadable.append(
                f"{pipeline.name}: {entry!r} names a file outside both checkouts"
            )
            continue
        base, relpaths = _included_files(entry, repo_root, lib_root)
        if base is None:
            unreadable.append(f"{pipeline.name}: {entry!r} has no readable file")
            continue
        passed = entry.get("inputs") if isinstance(entry.get("inputs"), dict) else {}
        for rel in relpaths:
            source = base / rel
            if not source.is_file():
                raise OperatorError(f"{rel} is not in {base}")
            inputs, job_docs = declared_inputs(_load_docs(source))
            problems += _input_problems(rel, inputs, passed)
            problems += _stage_problems(rel, job_docs, inputs, passed, stages)
            checked += 1
    return problems, unreadable, checked


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OperatorError(f"{path} could not be read: {exc}") from exc


def resolve_lib_root(explicit: str | None) -> Path | None:
    if explicit:
        root = Path(explicit)
        if not root.is_dir():
            raise OperatorError(f"--lib-path {explicit} is not a directory")
        return root
    env = os.environ.get("WEISSSRV_LIB_PATH")
    return Path(env) if env else None


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check a pipeline against the input contract of its includes"
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
    args = parser.parse_args(argv)

    problems: List[str] = []
    unreadable: List[str] = []
    checked = 0
    try:
        lib_root = resolve_lib_root(args.lib_path)
        for name in args.ci_file or [".gitlab-ci.yml"]:
            pipeline = args.repo_root / name
            if not pipeline.is_file():
                raise OperatorError(f"{pipeline} is not a file")
            found, skipped, count = check_pipeline(pipeline, args.repo_root, lib_root)
            problems += found
            unreadable += skipped
            checked += count
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for note in unreadable:
        print(f"  not contract-checked: {note}")

    if problems:
        print("ERROR: include-contract violations:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    if not checked:
        print(
            "ERROR: no include resolved to a readable file, so the gate inspected "
            "nothing — check --ci-file and --lib-path",
            file=sys.stderr,
        )
        return 2

    print("Include contract OK — %d included file(s) checked." % checked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
