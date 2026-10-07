#!/usr/bin/env python3
"""The deploy base aborts on a failed ansible.cfg copy or a stale collection.

Without the first every extending job deploys with Ansible's own defaults;
without the second a non-busting cache deploys the previous release's roles.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import CILoader as _CILoader  # noqa: E402

TEMPLATE = REPO / "ci" / "deploy" / "deploy-base.yml"

PLACEHOLDER = re.compile(r"\$\[\[ inputs\.(\w+) \]\]")

TMP = Path("/tmp")
COPY_GLOB = "ansible-*.cfg"


def _documents() -> tuple[dict, dict]:
    spec, body = (
        doc for doc in yaml.load_all(TEMPLATE.read_text(), Loader=_CILoader) if doc
    )
    return spec, body


def _step(*needles: str) -> str:
    """The single `before_script` entry containing every needle."""
    _, body = _documents()
    steps = list(body.values())[0]["before_script"]
    matches = [
        step
        for step in steps
        if isinstance(step, str) and all(needle in step for needle in needles)
    ]
    assert len(matches) == 1, f"expected one step matching {needles}, found {len(matches)}"
    return matches[0]


def ansible_cfg_step() -> str:
    """The `before_script` entry that copies ansible.cfg to a private path."""
    return _step("ANSIBLE_CONFIG", "install -m 600")


def verify_pins_step() -> str:
    """The `before_script` entry that checks the install against the pins."""
    return _step("ansible-galaxy", "collection", "list")


def resolve(step: str, **overrides: str) -> str:
    """The step with every `$[[ inputs.x ]]` replaced by its default."""
    spec, _ = _documents()
    values = {
        name: overrides.get(name, definition.get("default", ""))
        for name, definition in spec["spec"]["inputs"].items()
    }
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], step)


def resolved_step(**overrides: str) -> str:
    return resolve(ansible_cfg_step(), **overrides)


def run_step(ansible_dir: Path) -> subprocess.CompletedProcess:
    """The step, followed by an assertion that the copy has content."""
    script = resolved_step(ansible_dir=str(ansible_dir)) + '\ntest -s "$ANSIBLE_CONFIG"\n'
    before = set(TMP.glob(COPY_GLOB))
    try:
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        # An abort exits before the trap is registered, so the copy leaks here.
        for leftover in set(TMP.glob(COPY_GLOB)) - before:
            leftover.unlink(missing_ok=True)


REQUIREMENTS = textwrap.dedent(
    """\
    collections:
      - name: git+https://example.invalid/org/lib.git#/ansible_collections/acme/infra
        type: git
        version: v1.2.3
      - name: ansible.posix
        version: ">=2.1.0,<3.0.0"
    """
)


def _fake_bin(tmp_path: Path, listing: dict) -> Path:
    """A PATH dir with a canned `ansible-galaxy` and this interpreter as python3."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    galaxy = bin_dir / "ansible-galaxy"
    galaxy.write_text(f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(listing)}\nJSON\n")
    galaxy.chmod(0o755)
    python3 = bin_dir / "python3"
    python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python3.chmod(0o755)
    return bin_dir


def run_verify(tmp_path: Path, listing: dict) -> subprocess.CompletedProcess:
    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    (ansible_dir / "requirements.yml").write_text(REQUIREMENTS)
    bin_dir = _fake_bin(tmp_path, listing)
    return subprocess.run(
        ["bash", "-c", resolve(verify_pins_step(), ansible_dir=str(ansible_dir))],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
    )


def test_the_step_enables_errexit_first() -> None:
    first = ansible_cfg_step().splitlines()[0].strip()
    assert first == "set -eo pipefail", first


def test_the_fragment_names_its_own_image() -> None:
    """A consumer whose pipeline default is Alpine, or has none, would otherwise
    get a base whose apt and pip steps cannot run."""
    spec, body = _documents()
    job = list(body.values())[0]
    assert job.get("image") == "$[[ inputs.image ]]", job.get("image")
    default = spec["spec"]["inputs"]["image"]["default"]
    assert default.startswith("python:"), (
        f"image default {default!r} must ship pip: the fragment pip-installs ansible"
    )


def test_a_missing_ansible_cfg_aborts_the_step(tmp_path) -> None:
    result = run_step(tmp_path / "no-such-ansible-dir")
    assert result.returncode != 0, result.stdout + result.stderr


def test_a_real_ansible_cfg_is_copied(tmp_path) -> None:
    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    (ansible_dir / "ansible.cfg").write_text("[defaults]\nhost_key_checking = False\n")
    result = run_step(ansible_dir)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_install_is_followed_by_a_pin_check() -> None:
    steps = list(_documents()[1].values())[0]["before_script"]
    install = next(i for i, step in enumerate(steps) if "ansible-galaxy collection install" in str(step))
    assert steps.index(verify_pins_step()) > install


def test_a_matching_install_passes(tmp_path) -> None:
    result = run_verify(
        tmp_path,
        {"/cache": {"acme.infra": {"version": "1.2.3"}, "ansible.posix": {"version": "2.1.0"}}},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ansible.posix" in result.stdout, result.stdout


def test_a_stale_install_fails_and_names_both_versions(tmp_path) -> None:
    result = run_verify(tmp_path, {"/cache": {"acme.infra": {"version": "0.9.9"}}})
    assert result.returncode == 1, result.stdout + result.stderr
    for expected in ("acme.infra", "1.2.3", "0.9.9"):
        assert expected in result.stderr, result.stderr


def test_a_missing_collection_fails(tmp_path) -> None:
    result = run_verify(tmp_path, {"/cache": {}})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "(absent)" in result.stderr, result.stderr
