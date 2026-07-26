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
    head = yaml.safe_load(docs[0])
    if isinstance(head, dict) and "spec" in head:
        return head, docs[1]
    return None, text


def _default_for(name: str, spec: dict) -> object:
    inputs = spec["spec"]["inputs"]
    assert name in inputs, f"undeclared input interpolated: {name}"
    return inputs[name].get("default", "")


def _render(body: str, spec: dict) -> str:
    def sub(m: re.Match) -> str:
        default = _default_for(m.group(1), spec)
        if isinstance(default, (list, bool)):
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

    rendered = yaml.safe_load(rendered_text)
    assert isinstance(rendered, dict)

    script = "\n".join(_script_lines(rendered))
    if script:
        proc = subprocess.run(
            ["bash", "-n"], input=script, capture_output=True, text=True
        )
        assert proc.returncode == 0, f"rendered script does not parse:\n{proc.stderr}"


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


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: str(p.relative_to(_LIB_ROOT)))
def test_metachar_defaults_are_variable_routed(path: Path) -> None:
    """An input default containing shell operators must not be interpolated
    into a script line — the rendered text would carry a live `<`/`>`/`|`
    the shell parses BEFORE any expansion. Routing through `variables:` is
    the safe form; assert every such input uses it."""
    spec, body = _split_docs(path.read_text(encoding="utf-8"))
    if spec is None:
        return
    body_yaml = yaml.safe_load(_INPUT_RE.sub(lambda m: f"__INPUT_{m.group(1)}__", body))

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
