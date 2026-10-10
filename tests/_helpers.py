"""Shared read-only helpers for the test suite (not itself a test module)."""
from __future__ import annotations

import base64
import functools
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml


def require_tags(tags, gate_name: str) -> None:
    """Stop a tag-driven gate that found no tags instead of passing vacuously.

    Skipping is right for a tagless local clone; under $CI it means the job
    fetched no tags, so the gate would certify a check it never ran.
    """
    if tags:
        return
    if os.environ.get("CI"):
        pytest.fail(
            "no version tag in this checkout — the %s gate cannot run. The job "
            'needs the tags: set GIT_DEPTH: "0" on it (the release job already '
            "does), or fetch them explicitly." % gate_name
        )
    pytest.skip("no version tag in this checkout")


def git(repo: Path, *args: str) -> str:
    """`git -C <repo> <args>` stdout; raises on a non-zero exit."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def require_git_checkout(repo: Path) -> None:
    """Skip a gate that needs history when the tree is not a git checkout."""
    if shutil.which("git") is None or not (repo / ".git").exists():
        pytest.skip("not a git checkout")


def require_tool(name: str, gate_name: str, install_hint: str = "") -> None:
    """Stop a binary-driven gate that has no binary instead of skipping in CI.

    Skipping is right for a workstation without the tool; under $CI it means the
    job never installed it, so the gate would certify a check it never ran.
    """
    if shutil.which(name):
        return
    if os.environ.get("CI"):
        pytest.fail(
            "%s is not on PATH — the %s gate cannot run. %s"
            % (name, gate_name, install_hint or "Install it in the job.")
        )
    pytest.skip(f"{name} not on PATH")


def template_input_default(template: Path, name: str) -> str:
    """The `default:` of one spec:inputs entry (the first YAML document)."""
    spec = next(yaml.safe_load_all(template.read_text()))
    inputs = (spec or {}).get("spec", {}).get("inputs", {})
    assert name in inputs, f"{template} has no input {name!r}"
    return str(inputs[name]["default"])


@functools.lru_cache(maxsize=1)
def _role_inputs_gate():
    from script_loader import load_script

    return load_script("check-role-inputs.py")


def ansible_bool(value) -> bool:
    """Ansible's `| bool`, taken from the gate that already implements it.

    Python's bool() reads "false" as True, the opposite of what Ansible renders.
    """
    return _role_inputs_gate()._ansible_bool(value)


def _ternary(value, yes, no, na=None):
    """Ansible's `ternary`, including its three-argument None arm."""
    if value is None and na is not None:
        return na
    return yes if ansible_bool(value) else no


def ansible_env(**kwargs) -> jinja2.Environment:
    """A Jinja environment carrying the Ansible-only filters and tests.

    Registered once here so a test cannot spell a filter differently from the
    role it is checking. Defaults to ChainableUndefined, as Ansible does.
    """
    kwargs.setdefault("undefined", jinja2.ChainableUndefined)
    env = jinja2.Environment(**kwargs)
    env.filters["bool"] = ansible_bool
    env.filters["ternary"] = _ternary
    env.filters["from_json"] = json.loads
    env.filters["to_nice_yaml"] = lambda value, indent=2: yaml.safe_dump(
        value, default_flow_style=False, indent=indent)
    env.filters["b64decode"] = lambda v: base64.b64decode(v).decode()
    env.filters["b64encode"] = lambda v: base64.b64encode(str(v).encode()).decode()
    # Ansible's `quote` filter is shlex.quote.
    env.filters["quote"] = shlex.quote
    env.tests["search"] = lambda value, pattern: re.search(pattern, str(value)) is not None
    return env
