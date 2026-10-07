"""A gate whose PyYAML or Jinja2 is missing exits 2, never 1.

exit 1 is a policy finding, so run-render-gates.sh and version-check's
soft_fail_exit_codes would report a broken dependency install as a finding.
"""
from __future__ import annotations

import ast
import subprocess
import sys

import pytest
from script_loader import SCRIPTS

YAML_MODULES = {"yaml", "jinja2"}


def _module_scope_imports(tree):
    """Top-level imports, including the ones inside a top-level try/except."""
    for node in tree.body:
        yield node
        if isinstance(node, ast.Try):
            yield from node.body


def _imports_a_yaml_module(tree) -> bool:
    for node in _module_scope_imports(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in YAML_MODULES for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in YAML_MODULES:
                return True
    return False


def _is_executable_gate(tree) -> bool:
    """A `__main__` block, which the importable helper modules do not have."""
    return any(
        isinstance(node, ast.If) and "__name__" in ast.dump(node.test)
        for node in tree.body
    )


def _gates() -> list[str]:
    """Every runnable gate importing PyYAML or Jinja2 at module scope.

    Derived, so the next such gate cannot be left out of the parametrisation.
    """
    names = []
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text())
        if _imports_a_yaml_module(tree) and _is_executable_gate(tree):
            names.append(path.name)
    return names


GATES = _gates()


def _poisoned_path(tmp_path, *modules):
    """A sys.path entry whose named modules raise ImportError on import."""
    for name in modules:
        (tmp_path / ("%s.py" % name)).write_text('raise ImportError("poisoned")\n')
    return str(tmp_path)


def _run(script, path_entry):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": path_entry, "PATH": "/usr/bin:/bin"},
    )


def test_the_gate_list_is_not_empty():
    assert GATES, "derivation found no gates — the AST walk is broken"


@pytest.mark.parametrize("script", GATES)
def test_a_missing_pyyaml_is_an_operator_error(script, tmp_path):
    result = _run(script, _poisoned_path(tmp_path, "yaml"))
    assert result.returncode == 2, (
        "%s exited %d with no PyYAML — exit 1 reads as a finding.\n%s"
        % (script, result.returncode, result.stderr)
    )
    assert "required" in result.stderr


def test_a_missing_jinja2_is_an_operator_error(tmp_path):
    """check-role-inputs.py is the one gate that also needs Jinja2."""
    result = _run("check-role-inputs.py", _poisoned_path(tmp_path, "jinja2"))
    assert result.returncode == 2, result.stderr
    assert "Jinja2 required" in result.stderr
