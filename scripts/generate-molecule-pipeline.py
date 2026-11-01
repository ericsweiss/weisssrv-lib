#!/usr/bin/env python3
"""Emit a GitLab child pipeline running only the molecule scenarios an MR affects.

Derives the scenario universe from the CI file's parallel:matrix and the role
dependency map from the repo, failing loudly rather than under-selecting.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard mirrors sibling scripts
    print("ERROR: PyYAML required: pip install pyyaml (or brew install python && pip3 install pyyaml)", file=sys.stderr)
    raise SystemExit(2) from None

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ci_yaml import CILoader  # noqa: E402  (resolved from this script's directory)

# --- Configuration ---

REPO = Path(__file__).resolve().parent.parent

# Repo-relative locations, overridable by env (same variable names as
# check-molecule-matrix-coverage.sh). Empty/unset falls back to the default.
CI_FILE_NAME = os.environ.get("CI_FILE") or ".gitlab-ci.yml"
ROLES_PREFIX = (os.environ.get("ROLES_DIR") or "ansible/roles").strip("/")

CI_FILE = REPO / CI_FILE_NAME
ROLES_DIR = REPO / ROLES_PREFIX

# The self-contained job file the emitted child `include: local:`s.
MOLECULE_JOBS_INCLUDE = (
    os.environ.get("MOLECULE_JOBS_INCLUDE") or ".gitlab/ci/molecule-jobs.gitlab-ci.yml"
)
# The hidden job the child extends. It carries the script but no matrix and no
# rules, so the child supplies the narrowed matrix and runs unconditionally.
MOLECULE_JOB_EXTENDS = ".molecule-test-job"

# The no-op job emitted when nothing is affected (a GitLab trigger job fails on
# an empty child pipeline, so we always emit at least one trivially-green job).
NOOP_JOB_NAME = "molecule-none-affected"
# Trivially-green no-op job image (any tiny pinned base works).
NOOP_IMAGE = "alpine:3.23"

# Helper scripts and image build contexts every scenario runs on.
_FIXED_TRIGGER_FILES = (
    "scripts/molecule-retry.sh",
    "scripts/generate-molecule-pipeline.py",
    "scripts/sanitize-junit-expected-failures.py",
)
_FIXED_TRIGGER_PREFIXES = (
    "docker/molecule-test/",
    # Includes the CI image's requirements.txt, the pip pins both suites run on.
    "docker/molecule-ci/",
)


def _global_triggers(roles_prefix: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """CRITICAL: files and prefixes that force the FULL matrix.

    The roles tree root is neither a role path nor a scenario path, so without
    these a change to galaxy.yml selects nothing at all and the MR ships
    untested. A trigger only bites when the plan job was CREATED, so a new
    entry needs the same path in the plan job's `changes` list.
    """
    root = PurePosixPath(roles_prefix).parent
    files = {
        str(root / "requirements.yml"),
        str(root / "galaxy.yml"),
        CI_FILE_NAME,
        MOLECULE_JOBS_INCLUDE,
        *_FIXED_TRIGGER_FILES,
    }
    prefixes = (
        str(root / "meta") + "/",
        str(root / "plugins") + "/",
        str(root / "molecule-shared") + "/",
        *_FIXED_TRIGGER_PREFIXES,
    )
    return frozenset(files), prefixes


GLOBAL_TRIGGER_FILES, GLOBAL_TRIGGER_PREFIXES = _global_triggers(ROLES_PREFIX)


# include_role / import_role — matched on the final dotted component so
# ansible.builtin.include_role, ansible.legacy.import_role, etc. all count.
_INCLUDE_ROLE_KEYS = frozenset({"include_role", "import_role"})


class CoverageError(RuntimeError):
    """A changed role has no matrix entry — a coverage bug, fail loud."""


def _load_yaml(path: Path):
    """Parse a YAML file (SafeLoader semantics); None when the path is absent.

    Malformed or unreadable present files raise: skipping one could drop edges
    from the derived graph and under-select tests.
    """
    try:
        with path.open() as f:
            return yaml.load(f, Loader=CILoader)
    except yaml.YAMLError as e:
        raise CoverageError(f"{path}: YAML parse failed: {e}") from e
    except FileNotFoundError:
        return None
    except OSError as e:
        raise CoverageError(f"{path}: unreadable: {e}") from e


# --- Matrix parsing (single source of truth = the CI file) ---

def parse_molecule_matrix(ci_path: Path = CI_FILE) -> dict[str, list[str]]:
    """Parse the molecule-tests parallel:matrix into role -> sorted scenarios.

    A missing or malformed matrix raises.
    """
    ci = _load_yaml(ci_path)
    if not isinstance(ci, dict):
        raise RuntimeError(f"could not parse {ci_path} as a YAML mapping")
    job = ci.get("molecule-tests")
    if not isinstance(job, dict):
        raise RuntimeError(f"{ci_path}: job 'molecule-tests' missing or not a mapping")
    parallel = job.get("parallel")
    if not isinstance(parallel, dict):
        raise RuntimeError(f"{ci_path}: molecule-tests.parallel missing or not a mapping")
    matrix = parallel.get("matrix")
    if not isinstance(matrix, list) or not matrix:
        raise RuntimeError(f"{ci_path}: molecule-tests.parallel.matrix missing or empty")

    role_scenarios: dict[str, set[str]] = defaultdict(set)
    for entry in matrix:
        if not isinstance(entry, dict):
            continue
        role = entry.get("ROLE")
        scenario = entry.get("SCENARIO")
        if isinstance(role, str) and isinstance(scenario, str):
            role_scenarios[role].add(scenario)
    if not role_scenarios:
        raise RuntimeError(f"{ci_path}: molecule-tests matrix yielded no ROLE/SCENARIO pairs")
    return {r: sorted(sc) for r, sc in role_scenarios.items()}


# --- Dependency-graph derivation (from the repo, not hardcoded) ---


def _yaml_files(root: Path):
    """Every YAML file under root — both extensions, so a dependency moved into
    a .yaml file can never silently drop out of the derived graph."""
    yield from root.rglob("*.yml")
    yield from root.rglob("*.yaml")

def _collect_include_role_names(node, out: set[str]) -> None:
    """Recursively collect literal include_role/import_role `name:` values.

    Walking the parsed structure keeps a task's own sibling `name:` from being
    read as the included role. Templated names are skipped.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                isinstance(key, str)
                and key.split(".")[-1] in _INCLUDE_ROLE_KEYS
                and isinstance(value, dict)
            ):
                name = value.get("name")
                if isinstance(name, str) and "{{" not in name:
                    out.add(name.strip())
            _collect_include_role_names(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_include_role_names(item, out)


def collection_role_prefix(roles_dir: Path) -> str:
    """The own-collection FQCN prefix (``"<ns>.<name>."``) for a roles dir, else "".

    Derived from the ``ansible_collections/<ns>/<name>/roles/`` layout, so the
    classic ``ansible/roles`` layout still works and no namespace is hardcoded.
    """
    parts = roles_dir.resolve().parts
    if len(parts) >= 4 and parts[-1] == "roles" and parts[-4] == "ansible_collections":
        return f"{parts[-3]}.{parts[-2]}."
    return ""


def _strip_collection_prefix(names: set[str], prefix: str) -> set[str]:
    """Reduce own-collection FQCN references to bare role names.

    Bare names pass through, and a foreign namespace is left intact so the
    known-roles filter rejects it instead of aliasing it onto a local role.
    """
    if not prefix:
        return set(names)
    return {n[len(prefix):] if n.startswith(prefix) else n for n in names}


def _meta_dependencies(meta_path: Path) -> set[str]:
    """Role names from a role's meta/main.yml `dependencies:` (str or {role/name})."""
    deps: set[str] = set()
    data = _load_yaml(meta_path)
    if not isinstance(data, dict):
        return deps
    for item in data.get("dependencies") or []:
        if isinstance(item, str):
            deps.add(item)
        elif isinstance(item, dict):
            name = item.get("role") or item.get("name")
            if isinstance(name, str):
                deps.add(name)
    return deps


def build_role_graph(
    roles_dir: Path = ROLES_DIR,
    known_roles: set[str] | None = None,
    collection_prefix: str | None = None,
    *,
    unknown_out: dict[str, list[str]] | None = None,
) -> dict[str, set[str]]:
    """Map consumer_role -> set(provider roles it depends on).

    Scans meta dependencies and include_role usages outside molecule/, filtered
    to on-disk roles. `unknown_out` collects pruned unqualified references.
    """
    if known_roles is None:
        known_roles = {p.name for p in roles_dir.iterdir() if p.is_dir()}
    if collection_prefix is None:
        collection_prefix = collection_role_prefix(roles_dir)
    graph: dict[str, set[str]] = {}
    for role_dir in sorted(roles_dir.iterdir()):
        if not role_dir.is_dir():
            continue
        role = role_dir.name
        providers: set[str] = set()
        meta = role_dir / "meta" / "main.yml"
        if meta.is_file():
            providers |= _meta_dependencies(meta)
        for yml in _yaml_files(role_dir):
            if "/molecule/" in yml.as_posix():
                continue
            _collect_include_role_names(_load_yaml(yml), providers)
        providers = _strip_collection_prefix(providers, collection_prefix)
        if unknown_out is not None:
            unknown = sorted(
                p for p in providers
                if "." not in p and p not in known_roles and p != role
            )
            if unknown:
                unknown_out[role] = unknown
        providers = {p for p in providers if p in known_roles and p != role}
        if providers:
            graph[role] = providers
    return graph


# --- Path classification ---

def is_global_trigger(path: str, roles_prefix: str = ROLES_PREFIX) -> bool:
    """True if a change to `path` forces the full matrix."""
    files, prefixes = _global_triggers(roles_prefix)
    if path in files:
        return True
    return any(path.startswith(prefix) for prefix in prefixes)


def classify_role_path(
    path: str, matrix_roles: set[str], roles_prefix: str = ROLES_PREFIX
) -> str | None:
    """Return the role a changed path belongs to, or None if it isn't a role path.

    Raises CoverageError for <roles-dir>/<name>/... where <name> is not in the
    molecule matrix (a role missing from the matrix is a coverage bug).
    """
    head = roles_prefix + "/"
    if not path.startswith(head):
        return None
    rest = path[len(head):].split("/")
    # A file directly under the roles dir (e.g. README.md) is not a role path.
    if len(rest) < 2:
        return None
    name = rest[0]
    if name in matrix_roles:
        return name
    raise CoverageError(
        f"changed path {path!r} is under {roles_prefix}/{name}/, but {name!r} has "
        f"no entry in the molecule-tests matrix in {CI_FILE_NAME}. Add its "
        "ROLE/SCENARIO entry (and a molecule scenario) — a role missing from the "
        "matrix would ship untested."
    )


# --- Selection ---

class Selection:
    """Result of computing the affected set."""

    def __init__(self, scenarios: set[tuple[str, str]], full: bool = False):
        self.scenarios = scenarios          # {(role, scenario)}
        self.full = full                    # a global trigger selected everything

    @property
    def empty(self) -> bool:
        return not self.scenarios


def _closure(seed: set[str], adjacency: dict[str, set[str]]) -> set[str]:
    """Transitive closure of `seed` under `adjacency` (includes the seed)."""
    result = set(seed)
    stack = list(seed)
    while stack:
        for nxt in adjacency.get(stack.pop(), ()):  # noqa: PLW2901
            if nxt not in result:
                result.add(nxt)
                stack.append(nxt)
    return result


def compute_affected(
    changed_files: list[str],
    *,
    matrix: dict[str, list[str]],
    role_deps: dict[str, set[str]],
    roles_prefix: str = ROLES_PREFIX,
) -> Selection:
    """Compute the affected scenarios for a changed-file set.

    Pure over its inputs, so it is unit-testable with synthetic graphs.
    """
    matrix_roles = set(matrix)
    all_scenarios = {(r, s) for r, scen in matrix.items() for s in scen}

    # Global trigger -> everything (first, so it short-circuits role/coverage checks).
    if any(is_global_trigger(f, roles_prefix) for f in changed_files):
        return Selection(set(all_scenarios), full=True)

    # consumer graph -> reverse (provider -> direct consumers) for molecule fan-out.
    consumers_of: dict[str, set[str]] = defaultdict(set)
    for consumer, providers in role_deps.items():
        for provider in providers:
            consumers_of[provider].add(consumer)

    changed_roles: set[str] = set()
    for path in changed_files:
        role = classify_role_path(path, matrix_roles, roles_prefix)
        if role is not None:
            changed_roles.add(role)

    # Every role that transitively CONSUMES a changed role must run.
    scenarios: set[tuple[str, str]] = set()
    for role in _closure(changed_roles, consumers_of):
        scen = matrix.get(role)
        if not scen:
            raise CoverageError(
                f"role {role!r} was selected but has no molecule-tests scenarios in "
                "the CI file (coverage bug: refusing to silently drop it)."
            )
        for scenario in scen:
            scenarios.add((role, scenario))
    return Selection(scenarios)


def select(
    changed_files: list[str],
    *,
    repo: Path = REPO,
    roles_prefix: str = ROLES_PREFIX,
    ci_file: str = CI_FILE_NAME,
) -> Selection:
    """Build the derived data from `repo` and compute the affected set."""
    matrix = parse_molecule_matrix(repo / ci_file)
    roles_dir = repo / roles_prefix
    if not roles_dir.is_dir():
        # A mistyped $ROLES_DIR would otherwise derive an empty graph and
        # silently under-select every scenario.
        raise RuntimeError(f"roles dir {roles_prefix!r} not found under {repo}")
    role_deps = build_role_graph(roles_dir)
    return compute_affected(
        changed_files, matrix=matrix, role_deps=role_deps, roles_prefix=roles_prefix
    )


# --- Rendering ---

_HEADER = (
    "---\n"
    "# AUTO-GENERATED by generate-molecule-pipeline.py — targeted molecule\n"
    "# child pipeline for this MR's diff. Do NOT edit by hand.\n"
)


# Every emitted job carries explicit `rules: [when: always]`. A rule-less job
# inherits the legacy implicit `only: branches, tags`, which no MR-ref child job
# matches, so the child pipeline would be empty and the trigger would fail.
_ALWAYS_RULES = [{"when": "always"}]


def render_child_pipeline(selection: Selection) -> str:
    """Render the GitLab child-pipeline YAML for a Selection."""
    if selection.empty:
        # A GitLab trigger job fails on an empty pipeline, so emit one
        # trivially-green job. Self-contained (no include needed).
        doc = {
            "stages": ["test"],
            NOOP_JOB_NAME: {
                "stage": "test",
                "image": NOOP_IMAGE,
                "script": [
                    "echo 'No molecule scenarios affected by this MR diff; nothing to run.'"
                ],
                "rules": [dict(r) for r in _ALWAYS_RULES],
            },
        }
        return _HEADER + yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)

    doc = {
        "include": [{"local": MOLECULE_JOBS_INCLUDE}],
        "stages": ["test"],
        "molecule-tests": {
            "extends": MOLECULE_JOB_EXTENDS,
            "rules": [dict(r) for r in _ALWAYS_RULES],
            "parallel": {
                "matrix": [
                    {"ROLE": role, "SCENARIO": scenario}
                    for role, scenario in sorted(selection.scenarios)
                ]
            },
        },
    }
    return _HEADER + yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)


# --- CLI ---

def _git_changed_files(base: str, repo: Path = REPO) -> list[str]:
    """`git diff --name-only <base>...HEAD` (three-dot: HEAD vs the merge-base)."""
    out = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", f"{base}...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def galaxy_change_is_version_only(diff_text: str) -> bool:
    """Whether a unified galaxy.yml diff touches nothing but `version:`.

    Every release MR bumps that key, and a full 43-job matrix for a version
    lineage edit is pure cost.
    """
    touched = []
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "@@", "diff ", "index ")):
            continue
        if not line.startswith(("+", "-")):
            continue
        body = line[1:].strip()
        if not body or body.startswith("#"):
            continue
        key = body.split(":", 1)[0].strip() if ":" in body else body
        touched.append(key)
    return bool(touched) and set(touched) == {"version"}


def _galaxy_diff(base: str, path: str, repo: Path = REPO) -> str:
    """`git diff -U0 <base>...HEAD -- <path>`, or "" when git cannot answer."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "-U0", f"{base}...HEAD", "--", path],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return ""
    return out.stdout


def _read_paths(stream) -> list[str]:
    return [line.strip() for line in stream if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "examples:\n"
            '  generate-molecule-pipeline.py "$CI_MERGE_REQUEST_DIFF_BASE_SHA" -o molecule-child.yml\n'
            "  printf '%s\\n' ansible/roles/foo/tasks/main.yml | generate-molecule-pipeline.py\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("base", nargs="?", help="base SHA/ref; runs `git diff --name-only <base>...HEAD`")
    parser.add_argument(
        "--changed-files-from",
        metavar="FILE",
        help='read newline-separated changed paths from FILE ("-" = stdin)',
    )
    parser.add_argument("-o", "--output", help="write the child pipeline YAML here (default: stdout)")
    parser.add_argument("--repo", type=Path, default=REPO, help="repo root (default: the script's repo)")
    args = parser.parse_args(argv)

    base = args.base
    if args.changed_files_from:
        if args.changed_files_from == "-":
            changed = _read_paths(sys.stdin)
        else:
            with open(args.changed_files_from) as f:
                changed = _read_paths(f)
    elif base:
        try:
            changed = _git_changed_files(base, args.repo)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: git diff against {base!r} failed: {e.stderr.strip()}", file=sys.stderr)
            return 1
    elif not sys.stdin.isatty():
        changed = _read_paths(sys.stdin)
    else:
        parser.error("no changed files: pass a base SHA, --changed-files-from, or pipe paths on stdin")

    galaxy = str(PurePosixPath(ROLES_PREFIX).parent / "galaxy.yml")
    if base and galaxy in changed:
        if galaxy_change_is_version_only(_galaxy_diff(base, galaxy, args.repo)):
            changed = [c for c in changed if c != galaxy]
            print(
                f"note: {galaxy} changed only its `version:`; not treating it as a "
                "full-matrix trigger",
                file=sys.stderr,
            )

    try:
        selection = select(changed, repo=args.repo)
    except RuntimeError as e:  # CoverageError included
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    rendered = render_child_pipeline(selection)
    if args.output:
        Path(args.output).write_text(rendered)
        tag = "FULL matrix" if selection.full else f"{len(selection.scenarios)} scenario(s)"
        print(f"Wrote {args.output} ({tag})", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
