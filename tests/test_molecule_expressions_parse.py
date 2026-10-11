"""Every Jinja expression parses on its own path, and keeps its backreferences.

A YAML plain scalar swallows ` #` as a comment and a stray quote unbalances one.
A lost regex backreference raises nothing at all, so it needs its own gate.
"""
from __future__ import annotations

import functools
import re
import warnings
from pathlib import Path

import jinja2
import pytest
import yaml
from jinja2.lexer import TOKEN_STRING, TOKEN_VARIABLE_BEGIN, TOKEN_VARIABLE_END

COLLECTION = Path(__file__).resolve().parent.parent / "ansible_collections" / "weisssrv" / "infra"
EXPRESSION_KEYS = {"that", "when", "failed_when", "changed_when", "until"}
DECODED_ESCAPE = "invalid escape sequence"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


class _PreEscapingLexer(jinja2.lexer.Lexer):
    """Doubles the backslashes in a `{{ }}` string constant the way Ansible's
    own lexer does, so Jinja hands the filter the literal as written."""

    def tokeniter(self, *args, **kwargs):
        inside = False
        for lineno, token, value in super().tokeniter(*args, **kwargs):
            if token == TOKEN_VARIABLE_BEGIN:
                inside = True
            elif token == TOKEN_VARIABLE_END:
                inside = False
            elif inside and token == TOKEN_STRING:
                value = value.replace("\\", "\\\\")
            yield lineno, token, value


class _TemplateEnvironment(jinja2.Environment):
    """The environment a `{{ }}` scalar is compiled in."""

    @functools.cached_property
    def lexer(self) -> _PreEscapingLexer:
        return _PreEscapingLexer(self)


_ENV = jinja2.Environment()
_TEMPLATE_ENV = _TemplateEnvironment()


def _environment(text: str) -> jinja2.Environment:
    """A conditional is compiled with the backslash pre-escaping off, so its
    literals are decoded as Python escapes and a regex escape is doubled."""
    return _TEMPLATE_ENV if "{{" in text else _ENV


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
        + list(roles.glob("*/defaults/*.yml"))
        + list(roles.glob("*/vars/*.yml"))
    )


def _problems(path: Path) -> list[str]:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    problems = []
    for where, text in _expressions(document):
        source = text if "{{" in text else "{{ " + text + " }}"
        # A decoded escape is an error: jinja2 raises the warning as its own
        # syntax error, so the site names the literal that needs doubling.
        with warnings.catch_warnings():
            warnings.filterwarnings("error", message=DECODED_ESCAPE)
            try:
                _environment(text).parse(source)
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


# `{{ }}` and a conditional reach Jinja through different lexers: a template
# processes no escapes, a conditional processes Python's. So a regex
# backreference needs ONE backslash in a template and TWO in a conditional.
_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", re.S)
_BACKREF = re.compile(r"(?<!\\)(\\+)(\d|g<)")


def _lost_backreferences(path: Path) -> list[str]:
    """Literals whose `\\1` / `\\g<1>` group reference does not survive its
    own evaluation path, so the replacement is a literal backslash instead."""
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    lost = []
    for where, text in _expressions(document):
        template = "{{" in text
        regions = re.findall(r"\{\{.*?\}\}", text, re.S) if template else [text]
        for region in regions:
            for literal in _LITERAL.findall(region):
                for run, following in _BACKREF.findall(literal):
                    wanted = 1 if template else 2
                    if len(run) != wanted and (template or len(run) % 2):
                        lost.append(
                            "%s: %s wants %d backslash(es) before %r, has %d: %s"
                            % (where, "a template" if template else "a conditional",
                               wanted, following, len(run), literal[:60])
                        )
    return lost


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(COLLECTION)))
def test_every_backreference_survives_its_evaluation_path(path: Path) -> None:
    assert _lost_backreferences(path) == []


def _one_task(tmp_path: Path, expression: str, key: str, check) -> list[str]:
    task = tmp_path / "task.yml"
    task.write_text(
        "- hosts: all\n  tasks:\n    - ansible.builtin.assert:\n"
        "        %s:\n          - %s\n" % (key, expression),
        encoding="utf-8",
    )
    return check(task)


def _one_expression(tmp_path: Path, expression: str, key: str = "that") -> list[str]:
    return _one_task(tmp_path, expression, key, _lost_backreferences)


def test_a_doubled_backreference_in_a_template_is_reported(tmp_path: Path) -> None:
    """The proxmox_ha case: a scalar that keeps its backslashes hands the
    template lexer `\\1`, and regex_replace writes a literal backslash
    instead of the captured group."""
    assert _one_expression(
        tmp_path, """'{{ x | regex_replace("^(.*)$", "\\\\1:0") }}'""", key="when")


def test_a_single_backreference_in_a_template_passes(tmp_path: Path) -> None:
    assert not _one_expression(
        tmp_path, """'{{ x | regex_replace("^(.*)$", "\\1:0") }}'""", key="when")


def test_a_single_backreference_in_a_conditional_is_reported(tmp_path: Path) -> None:
    """The conditional lexer reads `\\1` as the octal escape chr(1)."""
    assert _one_expression(tmp_path, "y in (x | map('regex_replace', 'a', '\\1') | list)")


def test_a_doubled_backreference_in_a_conditional_passes(tmp_path: Path) -> None:
    assert not _one_expression(
        tmp_path, "y in (x | map('regex_replace', 'a', '\\\\1') | list)")


def test_a_plain_escape_is_not_mistaken_for_a_backreference(tmp_path: Path) -> None:
    """`\\s` and `\\.` are not group references and are correct either way."""
    assert not _one_expression(tmp_path, "x is search('a\\s+b[.]c')")
    assert not _one_expression(tmp_path, "x is search('a\\\\s+b')")


# A conditional's literals are decoded as Python escapes, and `\b` and friends
# decode silently to a control character where the regex wants its own meaning.
_DECODED = re.compile(r"(?<!\\)(\\+)([.^$*+?()\[\]{}|/sSdDwWbBAZzGafv-])")


def _decoded_escapes(path: Path) -> list[str]:
    """Conditional literals whose regex escape sits behind an odd run of
    backslashes, so the lexer decodes it instead of delivering it."""
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    decoded = []
    for where, text in _expressions(document):
        if "{{" in text:
            continue
        for literal in _LITERAL.findall(text):
            for run, following in _DECODED.findall(literal):
                if len(run) % 2:
                    decoded.append(
                        "%s: %r before %r survives only undecoded, double it: %s"
                        % (where, "\\" * len(run), following, literal[:60])
                    )
    return decoded


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(COLLECTION)))
def test_every_conditional_escape_reaches_the_filter(path: Path) -> None:
    assert _decoded_escapes(path) == []


def _one_problem(tmp_path: Path, expression: str, key: str = "that") -> list[str]:
    return _one_task(tmp_path, expression, key, _problems)


def _one_doubled(tmp_path: Path, expression: str, key: str = "when") -> list[str]:
    return _one_task(tmp_path, expression, key, _doubled_escapes)


def test_a_decoded_escape_in_a_conditional_is_reported(tmp_path: Path) -> None:
    """A conditional decodes Python escapes, so a single-backslash regex escape
    reaches the filter by accident and the lexer's own note is the failure."""
    assert _one_problem(tmp_path, "x is search('a\\.b')")


def test_a_doubled_escape_in_a_conditional_passes(tmp_path: Path) -> None:
    assert not _one_problem(tmp_path, "x is search('a\\\\.b')")


def test_a_single_escape_in_a_template_passes(tmp_path: Path) -> None:
    """A `{{ }}` scalar is pre-escaped, so one backslash is what the filter
    receives and doubling it would break the pattern."""
    assert not _one_problem(tmp_path, "'{{ x | regex_search(\"a\\.b\") }}'", key="when")


def test_a_decoded_escape_in_a_statement_is_reported(tmp_path: Path) -> None:
    """A `{% %}` statement is not pre-escaped, so its literals decode."""
    assert _one_problem(
        tmp_path, "'{% if x is search(\"a\\.b\") %}y{% endif %}'", key="when")


def test_a_silently_decoded_word_boundary_is_reported(tmp_path: Path) -> None:
    """`\\b` raises no note at all: it decodes to a backspace where the regex
    wants a word boundary."""
    task = tmp_path / "conditional.yml"
    task.write_text(
        "- hosts: all\n  tasks:\n    - ansible.builtin.assert:\n"
        "        that:\n          - x is search('\\bword\\b')\n",
        encoding="utf-8",
    )
    assert _decoded_escapes(task)


def test_a_doubled_word_boundary_passes(tmp_path: Path) -> None:
    task = tmp_path / "conditional.yml"
    task.write_text(
        "- hosts: all\n  tasks:\n    - ansible.builtin.assert:\n"
        "        that:\n          - x is search('\\\\bword\\\\b')\n",
        encoding="utf-8",
    )
    assert _decoded_escapes(task) == []


def test_a_real_newline_escape_is_left_alone(tmp_path: Path) -> None:
    """`\\n` means the same to the decoder and to the regex."""
    task = tmp_path / "conditional.yml"
    task.write_text(
        "- hosts: all\n  tasks:\n    - ansible.builtin.assert:\n"
        "        that:\n          - x is search('a\\nb')\n",
        encoding="utf-8",
    )
    assert _decoded_escapes(task) == []


# The mirror of the arm above: a template's literals are pre-escaped, so a
# doubled regex escape reaches the filter behind a literal backslash.


def _doubled_escapes(path: Path) -> list[str]:
    """Template literals whose regex escape sits behind an EVEN run of
    backslashes, so the filter receives the backslash and not the escape."""
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    doubled = []
    for where, text in _expressions(document):
        if "{{" not in text:
            continue
        for region in re.findall(r"\{\{.*?\}\}", text, re.S):
            for literal in _LITERAL.findall(region):
                for run, following in _DECODED.findall(literal):
                    if not len(run) % 2:
                        doubled.append(
                            "%s: %r before %r reaches the filter as a backslash, "
                            "halve it: %s"
                            % (where, "\\" * len(run), following, literal[:60])
                        )
    return doubled


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(COLLECTION)))
def test_every_template_escape_reaches_the_filter(path: Path) -> None:
    assert _doubled_escapes(path) == []


def test_a_doubled_escape_in_a_template_is_reported(tmp_path: Path) -> None:
    """The vfio_passthrough case: a scalar that keeps its backslashes hands the
    pre-escaping lexer two, and the pattern matches a backslash it never wants."""
    assert _one_doubled(tmp_path, """'{{ x | regex_replace("\\\\{.*$", "") }}'""")


def test_a_single_escape_in_a_template_is_accepted(tmp_path: Path) -> None:
    assert not _one_doubled(tmp_path, """'{{ x | regex_replace("\\{.*$", "") }}'""")


def test_a_doubled_escape_outside_a_template_region_is_left_alone(tmp_path: Path) -> None:
    """Only a `{{ }}` region is pre-escaped; surrounding text is output, and a
    `{% %}` statement decodes like a conditional."""
    assert not _one_doubled(tmp_path, """'a\\\\.b {{ x }}'""")
