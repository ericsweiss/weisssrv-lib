"""Every Jinja expression in the collection's playbooks and task files parses.

A YAML plain scalar swallows ` #` as a comment and a stray quote unbalances an
expression; both reach CI as a red converge or verify instead of a parse error.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import jinja2
import pytest
import yaml

COLLECTION = Path(__file__).resolve().parent.parent / "ansible_collections" / "weisssrv" / "infra"
EXPRESSION_KEYS = {"that", "when", "failed_when", "changed_when", "until"}


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
_ENV = jinja2.Environment()


def _expressions(value, path=""):
    if isinstance(value, str):
        if "{{" in value or path.rsplit(".", 1)[-1] in EXPRESSION_KEYS:
            yield path, value
    elif isinstance(value, list):
        for item in value:
            yield from _expressions(item, path)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _expressions(item, f"{path}.{key}" if path else str(key))


def _files():
    roles = COLLECTION / "roles"
    return sorted(
        list(roles.glob("*/molecule/*/*.yml"))
        + list(roles.glob("*/tasks/*.yml"))
        + list(roles.glob("*/handlers/*.yml"))
    )


def _problems(path: Path) -> list[str]:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    problems = []
    for where, text in _expressions(document):
        source = text if "{{" in text else "{{ " + text + " }}"
        # A regex escape such as `\.` inside a Jinja string literal is the
        # collection's idiom; the lexer keeps it and only notes the escape.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="invalid escape sequence")
            try:
                _ENV.parse(source)
            except jinja2.TemplateSyntaxError as error:
                problems.append(f"{where}: {error.message}: {text[:80]!r}")
    return problems


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(COLLECTION)))
def test_every_expression_parses(path: Path) -> None:
    assert _problems(path) == []


def test_the_scan_reaches_the_scenarios() -> None:
    assert any("molecule" in str(p) for p in _files())


def test_a_swallowed_comment_is_reported(tmp_path: Path) -> None:
    """The unquoted ` #` form that reached CI: YAML keeps only the text before it."""
    broken = tmp_path / "verify.yml"
    broken.write_text(
        "- hosts: all\n  tasks:\n    - ansible.builtin.assert:\n"
        "        that:\n          - x is search('a {  # note')\n",
        encoding="utf-8",
    )
    assert _problems(broken)
