"""Render-smoke for the ci/ templates: substitute each spec input's DEFAULT the
way GitLab does (textual interpolation), then assert the result still parses —
as YAML, and as shell for every script line.

This is the gate ci-templates-parse cannot be: a template whose rendered pip
line read `black<26.5.0` was valid YAML and valid bash syntax, yet ran a
redirect from a file named 26.5.0. Hence the third check: an input default
carrying shell metacharacters must reach the shell through a job `variables:`
entry (expansion results are never re-scanned for operators), not by raw
interpolation into a script line.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml


class _GitLabLoader(yaml.SafeLoader):
    """SafeLoader that understands GitLab's `!reference [job, key]` tag."""


_GitLabLoader.add_constructor(
    "!reference", lambda loader, node: loader.construct_sequence(node)
)


def _load(text: str) -> object:
    return yaml.load(text, Loader=_GitLabLoader)


_LIB_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES = sorted((_LIB_ROOT / "ci").rglob("*.yml"))
_INPUT_RE = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9_]+)\s*\]\]")
# Redirection/list operators, plus the two command-substitution forms: a default
# carrying `$(id)` or a backtick run parses clean under `bash -n` and reads as
# valid YAML, yet EXECUTES at job time exactly like `black<26.5.0` redirected.
_SHELL_META = re.compile(r"[<>|;&`]|\$\(")


def _split_docs(text: str) -> tuple[dict | None, str]:
    """Return (spec mapping or None, raw body text). The ci/templates/
    fragments ship hidden jobs with no spec header — they render as-is."""
    docs = text.split("\n---\n", 1)
    if len(docs) == 1:
        return None, text
    head = _load(docs[0])
    if isinstance(head, dict) and "spec" in head:
        return head, docs[1]
    return None, text


# A REQUIRED input (no `default:`) has no library-side value to render, and
# substituting "" produces a shape no consumer can ever produce: an empty job
# name, an array key rendered as a bare scalar. Stand-ins by declared type, so a
# mandatory-input template is smoke-rendered the way it is actually used.
_PLACEHOLDER_BY_TYPE = {
    "array": ["placeholder"],
    "boolean": True,
    "number": 1,
}
_PLACEHOLDER_STRING = "placeholder"


def _default_for(name: str, spec: dict) -> object:
    inputs = spec["spec"]["inputs"]
    assert name in inputs, f"undeclared input interpolated: {name}"
    meta = inputs[name] or {}
    if "default" in meta:
        return meta["default"]
    return _PLACEHOLDER_BY_TYPE.get(meta.get("type", "string"), _PLACEHOLDER_STRING)


def _render(body: str, spec: dict) -> str:
    def sub(m: re.Match) -> str:
        default = _default_for(m.group(1), spec)
        if isinstance(default, (list, dict, bool)):
            return json.dumps(default)
        return str(default)

    return _INPUT_RE.sub(sub, body)


def _script_lines(rendered: dict) -> list[str]:
    lines: list[str] = []
    for job in rendered.values():
        if not isinstance(job, dict):
            continue
        for key in ("before_script", "script", "after_script"):
            part = job.get(key)
            if isinstance(part, list):
                lines.extend(str(x) for x in part)
            elif isinstance(part, str):
                lines.append(part)
    return lines


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: str(p.relative_to(_LIB_ROOT)))
def test_template_renders(path: Path) -> None:
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    rendered_text = _render(body, spec) if spec else body
    assert not _INPUT_RE.search(rendered_text), "unsubstituted interpolation survived"

    rendered = _load(rendered_text)
    assert isinstance(rendered, dict)

    script = "\n".join(_script_lines(rendered))
    if script:
        proc = subprocess.run(
            ["bash", "-n"], input=script, capture_output=True, text=True
        )
        assert proc.returncode == 0, f"rendered script does not parse:\n{proc.stderr}"


def test_deploy_templates_never_default_needs() -> None:
    """A deploy job's gate must be stated by the consumer, never defaulted.

    The only value the library could pick is `[]`, and in GitLab `needs: []`
    means "start at pipeline creation, ignore stage order" — an ungated
    `ansible-playbook` against live infrastructure. So `needs` stays required.
    """
    checked = 0
    for path in sorted((_LIB_ROOT / "ci" / "deploy").glob("*.yml")):
        spec, _body = _split_docs(path.read_text(encoding="utf-8"))
        if spec is None:
            continue
        meta = spec["spec"]["inputs"].get("needs")
        if meta is None:
            continue
        checked += 1
        assert "default" not in meta, (
            f"{path.name}: `needs` must stay a REQUIRED input — every default "
            "the library could pick bypasses the validation gate"
        )
    assert checked, "no ci/deploy template declares a `needs` input any more"


_ARRAY_MARKER = "__ARRAY_INPUT_{}__"


def _array_inputs(spec: dict) -> set[str]:
    """The names of every input the spec declares as `type: array`."""
    return {
        name
        for name, meta in spec["spec"]["inputs"].items()
        if (meta or {}).get("type") == "array"
    }


def _render_with_array_markers(body: str, spec: dict, arrays: set[str]) -> str:
    """Render normally, except array inputs, which become a bare scalar marker.

    Only the array sites differ from `_render`'s output, so the two documents
    have the same shape and a path found in one resolves in the other.
    """

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in arrays:
            return _ARRAY_MARKER.format(name)
        default = _default_for(name, spec)
        if isinstance(default, (list, dict, bool)):
            return json.dumps(default)
        return str(default)

    return _INPUT_RE.sub(sub, body)


def _marker_sites(node: object, arrays: set[str], path: tuple = ()):
    """Yield (path, input name) for every value that is exactly one marker."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _marker_sites(value, arrays, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _marker_sites(value, arrays, path + (index,))
    elif isinstance(node, str):
        for name in arrays:
            if node.strip() == _ARRAY_MARKER.format(name):
                yield path, name


def _at(node: object, path: tuple) -> object:
    for step in path:
        node = node[step]
    return node


def _array_sites(path: Path) -> list[tuple[tuple, str, object]]:
    """(path, input name, rendered value) for every array interpolation site."""
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    if spec is None:
        return []
    arrays = _array_inputs(spec)
    if not arrays:
        return []
    marked = _load(_render_with_array_markers(body, spec, arrays))
    rendered = _load(_render(body, spec))
    return [
        (site, name, _at(rendered, site)) for site, name in _marker_sites(marked, arrays)
    ]


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: str(p.relative_to(_LIB_ROOT)))
def test_array_inputs_render_as_sequences(path: Path) -> None:
    """An input typed `array` must reach YAML as a sequence at EVERY site.

    A required array with no placeholder renders as a bare scalar (YAML null) —
    which parses fine and hides that the real job would carry a list. A null is
    the shape being guarded, so there is no `is not None` escape hatch here.

    The sites are DERIVED from the spec rather than named by hand, so the ones
    that are not job-level keys are covered too: `changes:` inside a `rules:`
    entry, and `key_files` under `cache: key:`.
    """
    for site, name, value in _array_sites(path):
        where = ".".join(str(step) for step in site)
        assert isinstance(value, list), (
            f"{path.name}: array input `{name}` rendered as {value!r} at {where} — "
            "an array interpolation site must reach YAML as a sequence"
        )


def test_the_array_site_walker_finds_sites_to_check() -> None:
    """The guard above is per-template and silently passes on a template with no
    array inputs, so prove the walker locates real sites somewhere in ci/ —
    otherwise a broken walker would green every template at once."""
    found = {
        (path.name, name)
        for path in _TEMPLATES
        for _site, name, _value in _array_sites(path)
    }
    assert found, "no array interpolation site found in any ci/ template"


@pytest.mark.parametrize(
    "default",
    [
        "black<26.5.0",           # the redirect that started this
        "out > /tmp/x",
        "a | b",
        "a; b",
        "a && b",
        "$(id -u)",               # command substitution
        "`id -u`",
    ],
)
def test_shell_meta_covers_every_way_a_default_reaches_the_shell_live(default: str) -> None:
    assert _SHELL_META.search(default), f"{default!r} must be treated as risky"


@pytest.mark.parametrize("default", ["black", "3.11", "git jq", "$CI_COMMIT_SHA", "--check --diff"])
def test_shell_meta_does_not_flag_inert_defaults(default: str) -> None:
    """A bare `$VAR` is expanded, not re-scanned for operators — interpolating it
    is the normal, safe form, so flagging it would make the gate unusable."""
    assert not _SHELL_META.search(default)


# A dependency list is a consumer-supplied VALUE, so what matters is what it can
# contain, not what this repo happens to default it to. Every ecosystem here has
# a ceiling syntax carrying `<` (`black<26.5.0`, `pkg<1.2`), so these inputs are
# routed through `variables:` whatever their default looks like.
_DEPENDENCY_LIST_RE = re.compile(r"(packages|_extra)$")


def _dependency_list_inputs(spec: dict) -> set[str]:
    return {
        name
        for name in spec["spec"]["inputs"]
        if _DEPENDENCY_LIST_RE.search(name)
    }


def test_the_dependency_list_pattern_matches_the_inputs_it_names() -> None:
    """A pattern that matched nothing would green every template at once."""
    found = {
        (path.name, name)
        for path in _TEMPLATES
        for name in _dependency_list_inputs(_split_docs(path.read_text(encoding="utf-8"))[0] or {"spec": {"inputs": {}}})
    }
    assert {name for _template, name in found} == {
        "apt_packages",
        "pip_packages",
        "pip_extra",
    }, found


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: str(p.relative_to(_LIB_ROOT)))
def test_dependency_list_inputs_are_variable_routed(path: Path) -> None:
    """A benign DEFAULT is not a reason to interpolate a package list.

    test_metachar_defaults_are_variable_routed only inspects inputs whose
    default already carries an operator, so `apt_packages: "git"` looked safe
    while accepting `pkg<1.2` from a consumer and handing the shell a live `<`.
    """
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    if spec is None:
        return
    names = _dependency_list_inputs(spec)
    if not names:
        return

    body_yaml = _load(_INPUT_RE.sub(lambda m: f"__INPUT_{m.group(1)}__", body))
    script = "\n".join(_script_lines(body_yaml))
    for name in names:
        assert f"__INPUT_{name}__" not in script, (
            f"input '{name}' is a dependency list and is interpolated into a "
            f"script line in {path.name}; route it through variables:"
        )


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: str(p.relative_to(_LIB_ROOT)))
def test_metachar_defaults_are_variable_routed(path: Path) -> None:
    """An input default containing shell operators must not be interpolated
    into a script line — the rendered text would carry a live `<`/`>`/`|`
    the shell parses BEFORE any expansion. Routing through `variables:` is
    the safe form; assert every such input uses it."""
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    if spec is None:
        return
    body_yaml = _load(_INPUT_RE.sub(lambda m: f"__INPUT_{m.group(1)}__", body))

    risky = {
        name
        for name, meta in spec["spec"]["inputs"].items()
        if isinstance(meta.get("default"), str) and _SHELL_META.search(meta["default"])
    }
    if not risky:
        return

    script = "\n".join(_script_lines(body_yaml))
    for name in risky:
        assert f"__INPUT_{name}__" not in script, (
            f"input '{name}' (default contains a shell operator) is interpolated "
            f"into a script line in {path.name}; route it through variables:"
        )
