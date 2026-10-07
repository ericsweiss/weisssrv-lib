#!/usr/bin/env python3
"""The kubectl install step aborts when the downloaded binary fails its sha256.

The version pin itself is held by tests/test_pin_parity.py.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from _helpers import require_tool

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import CILoader as _CILoader  # noqa: E402

TEMPLATE = REPO / "ci" / "deploy" / "kubectl-setup.yml"

# curl is stubbed, so the payload is these fixed bytes.
PAYLOAD = b"kubectl-binary"

PLACEHOLDER = re.compile(r"\$\[\[ inputs\.(\w+) \]\]")


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


def install_step() -> str:
    """The `before_script` entry that downloads and verifies kubectl."""
    return _step("sha256sum -c", "install -m 0755")


def jq_step() -> str:
    """The `before_script` entry that makes jq available."""
    return _step("command -v jq")


def resolved_step(**overrides: str) -> str:
    """The step with every `$[[ inputs.x ]]` replaced by its default."""
    spec, _ = _documents()
    values = {
        name: overrides.get(name, definition.get("default", ""))
        for name, definition in spec["spec"]["inputs"].items()
    }
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], install_step())


def run_jq_step(tmp_path: Path, present: tuple[str, ...]) -> subprocess.CompletedProcess:
    """The jq step with only `present` on PATH; package managers log their argv."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in present:
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{tmp_path}/calls"\n')
        stub.chmod(0o755)
    return subprocess.run(
        # bash by absolute path: PATH holds only the stubs, so the step sees no
        # package manager the test did not put there.
        [shutil.which("bash") or "/bin/bash", "-c", jq_step()],
        env={"PATH": str(bindir)},
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestJqIsAvailableToEveryConsumer:
    """Everything that reads `kubectl -o json` needs jq, and this fragment is the
    one step every kubectl consumer passes through."""

    def test_the_step_enables_errexit_first(self) -> None:
        assert jq_step().splitlines()[0].strip() == "set -eo pipefail"

    def test_an_image_that_already_ships_jq_installs_nothing(self, tmp_path) -> None:
        result = run_jq_step(tmp_path, ("jq", "apt-get", "apk"))
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (tmp_path / "calls").exists(), (tmp_path / "calls").read_text()

    def test_a_debian_image_installs_jq_with_apt(self, tmp_path) -> None:
        result = run_jq_step(tmp_path, ("apt-get",))
        assert result.returncode == 0, result.stdout + result.stderr
        calls = (tmp_path / "calls").read_text()
        assert "apt-get update" in calls and "install" in calls, calls

    def test_an_alpine_image_installs_jq_with_apk(self, tmp_path) -> None:
        result = run_jq_step(tmp_path, ("apk",))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "apk add" in (tmp_path / "calls").read_text()

    def test_an_image_with_no_package_manager_fails_loudly(self, tmp_path) -> None:
        result = run_jq_step(tmp_path, ())
        assert result.returncode != 0, result.stdout
        assert "add jq to the job image" in result.stderr, result.stderr


def write_stubs(bindir: Path, marker: Path) -> None:
    """Stand-ins for the commands the step shells out to."""
    stubs = {
        "curl": (
            "#!/bin/sh\n"
            'for arg in "$@"; do\n'
            '  case "$prev" in -o) out="$arg" ;; esac\n'
            "  prev=$arg\n"
            "done\n"
            f'printf %s {PAYLOAD.decode()!r} > "$out"\n'
        ),
        "install": f'#!/bin/sh\necho install >> "{marker}"\n',
    }
    for name, body in stubs.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)


def run_step(tmp_path: Path, sha: str) -> subprocess.CompletedProcess:
    """The step under stubs, with `sha` as the expected checksum."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "install-ran"
    write_stubs(bindir, marker)
    return subprocess.run(
        ["bash", "-c", resolved_step(kubectl_sha256=sha)],
        env={"PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin"},
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestInstallStepFailsClosed:
    def test_the_step_enables_errexit_first(self) -> None:
        first = install_step().splitlines()[0].strip()
        assert first == "set -eo pipefail", first

    def test_the_step_downloads_into_a_private_directory(self) -> None:
        step = install_step()
        assert "dl=$(mktemp -d)" in step, "no per-job download dir"
        assert "/tmp/kubectl" not in step, "fixed /tmp download path"

    def test_a_mismatched_checksum_aborts_before_install(self, tmp_path) -> None:
        require_tool(
            "sha256sum",
            "kubectl-setup checksum bootstrap",
            "Install coreutils in the test job image.",
        )
        result = run_step(tmp_path, "0" * 64)
        assert result.returncode != 0, result.stdout + result.stderr
        assert not (tmp_path / "install-ran").exists(), (
            "install ran on an unverified binary"
        )

    def test_a_matching_checksum_reaches_install(self, tmp_path) -> None:
        require_tool(
            "sha256sum",
            "kubectl-setup checksum bootstrap",
            "Install coreutils in the test job image.",
        )
        result = run_step(tmp_path, hashlib.sha256(PAYLOAD).hexdigest())
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "install-ran").exists()
