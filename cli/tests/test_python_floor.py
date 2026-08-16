"""Holds the package to its declared `requires-python` floor.

CI runs the suite on one interpreter (the image's 3.13), and `lint/ruff.toml`
selects no version-gated rules, so nothing else would notice a 3.10+ construct
landing in a package that promises 3.9. This parses every module at the floor's
own grammar instead, via `ast.parse(..., feature_version=...)`.

`feature_version` is necessary but NOT sufficient, and the split matters:

* It rejects the features CPython's parser has an explicit version check for —
  `match`, `except*`, `type X = ...`, PEP 695 type parameters. Those also have
  their own AST node types, walked below so the failure names the node.
* It silently ACCEPTS post-3.9 syntax that reuses existing nodes, because the
  PEG parser has no version check to run. Parenthesized context managers
  (`with (a as x, b as y):`, 3.10) are the case that bites: they produce a plain
  `ast.With` carrying no record of the parentheses. `_parenthesized_with_lines`
  closes that hole off the token stream.
* It has nothing to say about PEP 604 annotations, which are a runtime error
  rather than a syntax error on 3.9 — `from __future__ import annotations` is
  what makes those safe, asserted separately below.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path
from textwrap import dedent

import pytest

CLI_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = CLI_ROOT / "weisssrv_lib_cli"
FLOOR = "3.9"
FEATURE_VERSION = tuple(int(part) for part in FLOOR.split("."))

# Node types the grammar gained after 3.9. `feature_version` already refuses to
# build these, so reaching one means the parse was relaxed; the walk stays
# because it names the offending node instead of a bare "invalid syntax".
POST_39_NODES = tuple(
    node
    for node in (
        getattr(ast, "Match", None),  # 3.10 structural pattern matching
        getattr(ast, "TryStar", None),  # 3.11 except*
        getattr(ast, "TypeAlias", None),  # 3.12 type X = ...
    )
    if node is not None
)


def _modules(root: Path) -> list[Path]:
    """Every module under `root`.

    rglob, not glob: a subpackage added later is held to the floor without
    anyone remembering to widen this.
    """
    return sorted(root.rglob("*.py"))


MODULES = _modules(PACKAGE)


def _parenthesized_with_lines(source: str) -> list[int]:
    """Lines carrying a 3.10-only parenthesized `with` header.

    Inside a `with` header, `as` at paren depth 0 is every 3.9-legal spelling —
    `with a as x, b as y:`, and `with (a) as x:` too, whose parentheses have
    closed by then. `as` at depth > 0 means the parentheses wrap the item list
    itself, which is the 3.10 grammar and a SyntaxError on 3.9.
    """
    tokens = [
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE)
    ]
    lines: list[int] = []
    index = 0
    while index < len(tokens):
        if tokens[index].type == tokenize.NAME and tokens[index].string == "with":
            header_line = tokens[index].start[0]
            depth = 0
            index += 1
            while index < len(tokens):
                token = tokens[index]
                if token.type == tokenize.OP and token.string in "([{":
                    depth += 1
                elif token.type == tokenize.OP and token.string in ")]}":
                    depth -= 1
                elif token.type == tokenize.OP and token.string == ":" and depth == 0:
                    break
                elif token.type == tokenize.NAME and token.string == "as" and depth > 0:
                    lines.append(header_line)
                    break
                index += 1
        index += 1
    return lines


def _violations(path: Path) -> list[str]:
    """Every reason `path` would not run on the floor."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path), feature_version=FEATURE_VERSION)
    except SyntaxError as exc:
        return [f"does not parse as Python {FLOOR}: {exc.msg}"]

    problems = []
    for node in ast.walk(tree):
        if isinstance(node, POST_39_NODES):
            problems.append(f"uses {type(node).__name__}, which {FLOOR} cannot parse")
        # 3.12 generic syntax hangs off the def/class itself.
        if getattr(node, "type_params", None):
            problems.append("uses 3.12 type-parameter syntax")
    problems += [
        f"line {line}: parenthesized context managers are 3.10+"
        for line in _parenthesized_with_lines(source)
    ]
    return problems


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "module_under_test.py"
    path.write_text(dedent(source).lstrip(), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The gate is pointed at the package, and at the declared floor
# --------------------------------------------------------------------------


def test_the_package_has_modules_to_check():
    assert MODULES, "no modules found — the rglob stopped matching the package"


def test_the_module_collection_reaches_subpackages(tmp_path: Path):
    """The regression this guards: a `glob` here checks the top level only, so a
    subpackage lands entirely unchecked and nothing says so."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.py").write_text("", encoding="utf-8")
    (tmp_path / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub" / "deep.py").write_text("", encoding="utf-8")

    found = {path.relative_to(tmp_path).as_posix() for path in _modules(tmp_path)}
    assert found == {"top.py", "sub/__init__.py", "sub/deep.py"}


def test_the_checks_match_the_declared_floor():
    declared = re.search(
        r'^requires-python\s*=\s*">=(\d+\.\d+)"',
        (CLI_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert declared and declared.group(1) == FLOOR, (
        "requires-python moved: update FLOOR and the node list above to the new floor"
    )


# --------------------------------------------------------------------------
# The package itself
# --------------------------------------------------------------------------


def test_no_module_uses_syntax_newer_than_the_floor():
    offenders = {
        path.relative_to(PACKAGE).as_posix(): problems
        for path, problems in ((path, _violations(path)) for path in MODULES)
        if problems
    }
    assert not offenders, f"modules that will not run on Python {FLOOR}: {offenders}"


def _parsed():
    """Trees for the annotation walk, parsed WITHOUT the floor's grammar.

    A module that fails the floor parse is already reported by the test above;
    parsing plainly here keeps that failure from resurfacing as a collection-time
    SyntaxError with nothing to say about annotations.
    """
    return [(path, ast.parse(path.read_text(encoding="utf-8"))) for path in MODULES]


def _annotates(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.AnnAssign)
        or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns)
        or (isinstance(node, ast.arg) and node.annotation)
        for node in ast.walk(tree)
    )


def test_every_annotated_module_defers_annotation_evaluation():
    checked = 0
    for path, tree in _parsed():
        if not _annotates(tree):
            continue
        checked += 1
        futures = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "__future__"
            for alias in node.names
        }
        assert "annotations" in futures, (
            f"{path.name} annotates, so it must carry "
            "`from __future__ import annotations`: `str | None` and `list[str]` "
            "are runtime errors on 3.9 without it"
        )
    assert checked, "no annotated module found — the walk stopped seeing them"


# --------------------------------------------------------------------------
# The gate has teeth: synthetic modules the floor cannot run
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        (
            "3.10 parenthesized context managers",
            """
            def f(a, b):
                with (open(a) as first, open(b) as second):
                    return first, second
            """,
            "parenthesized context managers",
        ),
        (
            "3.10 parenthesized single item with a trailing comma",
            """
            def f(a):
                with (open(a) as first,):
                    return first
            """,
            "parenthesized context managers",
        ),
        (
            "3.10 structural pattern matching",
            """
            def f(x):
                match x:
                    case 1:
                        return "one"
            """,
            f"does not parse as Python {FLOOR}",
        ),
        (
            "3.11 except*",
            """
            def f():
                try:
                    pass
                except* ValueError:
                    pass
            """,
            f"does not parse as Python {FLOOR}",
        ),
        (
            "3.12 type alias",
            "type Alias = int\n",
            f"does not parse as Python {FLOOR}",
        ),
        (
            "3.12 type parameters",
            """
            def f[T](value: T) -> T:
                return value
            """,
            f"does not parse as Python {FLOOR}",
        ),
    ],
)
def test_the_gate_rejects_a_construct_newer_than_the_floor(
    tmp_path: Path, label: str, source: str, expected: str
):
    problems = _violations(_write(tmp_path, source))
    assert problems, f"{label} was accepted at the {FLOOR} floor"
    assert any(expected in problem for problem in problems), (
        f"{label} was rejected, but for the wrong reason: {problems}"
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("a plain with", "with open('a') as first:\n    pass\n"),
        (
            "several items on one header",
            "with open('a') as first, open('b') as second:\n    pass\n",
        ),
        # The parentheses have closed before the `as`, so this is 3.9 grammar —
        # the check must key on the `as`, not on seeing a paren after `with`.
        ("parentheses around one item", "with (open('a')) as first:\n    pass\n"),
        ("a backslash-continued header", "with open('a') as first, \\\n     open('b') as second:\n    pass\n"),
        ("no as at all", "with open('a'):\n    pass\n"),
        ("a tuple context manager", "with (first, second):\n    pass\n"),
        # `as` inside a nested with, at depth 0 of its own header.
        (
            "a nested with",
            "with open('a') as first:\n    with open('b') as second:\n        pass\n",
        ),
    ],
)
def test_the_gate_accepts_the_floor_spellings_of_with(
    tmp_path: Path, label: str, source: str
):
    """The negative control: the check must not read every `with` as 3.10."""
    assert _violations(_write(tmp_path, source)) == [], f"{label} was wrongly rejected"
