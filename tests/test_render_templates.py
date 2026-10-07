"""Render-smoke for the ci/ templates.

Substitutes each spec input the way GitLab does and asserts the result still
parses as YAML and as shell, and that metachar defaults are variable-routed.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
from pathlib import Path

import pytest
from script_loader import SCRIPTS, load_path

# The templates carry `!reference`, which yaml.safe_load cannot read.
ci_yaml = load_path(SCRIPTS / "ci_yaml.py")


def _load(text: str) -> dict:
    return ci_yaml.parse_ci(text)


_LIB_ROOT = Path(__file__).resolve().parents[1]
# Both spellings: a template added as `.yaml` would otherwise slip out of every
# gate below silently.
_YAML_SUFFIXES = ("yml", "yaml")
_TEMPLATES = sorted(
    {p for suffix in _YAML_SUFFIXES for p in (_LIB_ROOT / "ci").rglob(f"*.{suffix}")}
)
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


# A required input has no library-side value to render, and substituting "" is
# a shape no consumer can produce. These stand-ins by declared type render a
# mandatory-input template the way it is actually used.
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


@functools.lru_cache(maxsize=1)
def _fragment_jobs() -> dict:
    """The rendered ci/templates and ci/deploy fragments, so `!reference`
    targets resolve. An unresolved reference would hand `bash -n` the key path
    as a line, which parses clean and checks nothing.
    """
    merged: dict = {}
    for directory in ("templates", "deploy"):
        for path in sorted((_LIB_ROOT / "ci" / directory).rglob("*.yml")):
            spec, body = _split_docs(path.read_text(encoding="utf-8"))
            merged.update(_load(_render(body, spec) if spec else body))
    return merged


def _script_lines(rendered: dict, unresolved: list | None = None) -> list[str]:
    doc = {**_fragment_jobs(), **rendered}
    lines: list[str] = []
    for job in rendered.values():
        if not isinstance(job, dict):
            continue
        for key in ("before_script", "script", "after_script"):
            lines.extend(ci_yaml.script_lines(job, doc, key, unresolved=unresolved))
    return lines


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: str(p.relative_to(_LIB_ROOT)))
def test_template_renders(path: Path) -> None:
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    rendered_text = _render(body, spec) if spec else body
    assert not _INPUT_RE.search(rendered_text), "unsubstituted interpolation survived"

    rendered = _load(rendered_text)
    assert isinstance(rendered, dict)

    unresolved: list = []
    script = "\n".join(_script_lines(rendered, unresolved))
    assert unresolved == [], (
        f"`!reference` targets no ci/ file defines: {unresolved} — the referenced "
        "script never reaches the shell check"
    )
    if script:
        proc = subprocess.run(
            ["bash", "-n"], input=script, capture_output=True, text=True
        )
        assert proc.returncode == 0, f"rendered script does not parse:\n{proc.stderr}"


def test_deploy_templates_never_default_needs() -> None:
    """A deploy job's gate must be stated by the consumer, never defaulted."""
    checked = 0
    deploy = _LIB_ROOT / "ci" / "deploy"
    for path in sorted(
        {p for suffix in _YAML_SUFFIXES for p in deploy.glob(f"*.{suffix}")}
    ):
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
    """An input typed `array` must reach YAML as a sequence at every site."""
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
        "black<26.5.0",
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


# A dependency list is a consumer-supplied value that can carry a version
# ceiling such as `pkg<1.2`, so these inputs route through `variables:`
# whatever their default looks like.
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
    """A dependency list is routed through `variables:` whatever its default
    looks like: a consumer may pass `pkg<1.2`."""
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
    """An input default containing shell operators routes through `variables:`."""
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    if spec is None:
        return
    body_yaml = _load(_INPUT_RE.sub(lambda m: f"__INPUT_{m.group(1)}__", body))

    risky = {
        name
        for name, meta in spec["spec"]["inputs"].items()
        if isinstance((meta or {}).get("default"), str)
        and _SHELL_META.search(meta["default"])
    }
    if not risky:
        return

    script = "\n".join(_script_lines(body_yaml))
    for name in risky:
        assert f"__INPUT_{name}__" not in script, (
            f"input '{name}' (default contains a shell operator) is interpolated "
            f"into a script line in {path.name}; route it through variables:"
        )


def test_the_kubeconfig_is_checked_structurally_after_the_decode() -> None:
    """An empty-secret guard does not catch a field holding base64 of junk."""
    path = _LIB_ROOT / "ci" / "deploy" / "kubectl-setup.yml"
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    script = "\n".join(_script_lines(_load(_render(body, spec) if spec else body)))
    assert "base64 -d > ~/.kube/config" in script
    assert "grep -q '^[[:space:]]*server:' ~/.kube/config" in script
