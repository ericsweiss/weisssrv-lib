#!/usr/bin/env python3
"""The DinD bootstrap step aborts when a downloaded binary fails its sha256.

Parity of the step across the two templates: tests/test_pin_parity.py.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from _helpers import require_tool

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from ci_yaml import CILoader as _CILoader  # noqa: E402

DOCKER_DIND_TEMPLATE = REPO / "ci" / "templates" / "docker-dind.yml"
DOCKER_BUILD_TEMPLATE = REPO / "ci" / "build" / "docker-build.yml"

# curl is stubbed, so the payloads are these fixed bytes.
CLI_PAYLOAD = b"docker-cli-tarball"
BUILDX_PAYLOAD = b"docker-buildx-binary"


def bootstrap_step(path: Path) -> str:
    """The first `before_script` entry of a DinD-shaped template."""
    documents = [
        doc for doc in yaml.load_all(path.read_text(), Loader=_CILoader) if doc
    ]
    job = list(documents[-1].values())[0]
    return job["before_script"][0]


def write_stubs(bindir: Path, marker: Path) -> None:
    """Stand-ins for the commands the step shells out to."""
    stubs = {
        "dpkg": "#!/bin/sh\necho amd64\n",
        "curl": (
            "#!/bin/sh\n"
            'for arg in "$@"; do\n'
            '  case "$prev" in -o) out="$arg" ;; esac\n'
            "  prev=$arg\n"
            "done\n"
            'case "$out" in\n'
            f'  */docker.tgz) printf %s {CLI_PAYLOAD.decode()!r} > "$out" ;;\n'
            f'  *) printf %s {BUILDX_PAYLOAD.decode()!r} > "$out" ;;\n'
            "esac\n"
        ),
        "tar": f'#!/bin/sh\necho tar >> "{marker}"\n',
        "docker": "#!/bin/sh\nexit 0\n",
    }
    for name, body in stubs.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)


def run_step(tmp_path: Path, cli_sha: str) -> subprocess.CompletedProcess:
    """The step under stubs, with `cli_sha` as the expected CLI checksum."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "tar-ran"
    write_stubs(bindir, marker)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path / "home"),
        # mktemp honours TMPDIR, keeping the step's downloads out of the real /tmp.
        "TMPDIR": str(tmp_path),
        "DOCKER_CLI_VERSION": "27.5.1",
        "DOCKER_CLI_SHA256_AMD64": cli_sha,
        "DOCKER_CLI_SHA256_ARM64": cli_sha,
        "BUILDX_VERSION": "v0.35.0",
        "BUILDX_SHA256_AMD64": hashlib.sha256(BUILDX_PAYLOAD).hexdigest(),
        "BUILDX_SHA256_ARM64": hashlib.sha256(BUILDX_PAYLOAD).hexdigest(),
    }
    return subprocess.run(
        ["bash", "-c", bootstrap_step(DOCKER_DIND_TEMPLATE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestBootstrapStepFailsClosed:
    @pytest.mark.parametrize("template", [DOCKER_DIND_TEMPLATE, DOCKER_BUILD_TEMPLATE])
    def test_the_step_enables_errexit_first(self, template):
        first = bootstrap_step(template).splitlines()[0].strip()
        assert first == "set -eo pipefail", f"{template.name}: {first!r}"

    @pytest.mark.parametrize("template", [DOCKER_DIND_TEMPLATE, DOCKER_BUILD_TEMPLATE])
    def test_the_step_downloads_into_a_private_directory(self, template):
        step = bootstrap_step(template)
        assert "dl=$(mktemp -d)" in step, f"{template.name}: no per-job download dir"
        assert "/tmp/docker" not in step, f"{template.name}: fixed /tmp download path"

    def test_a_mismatched_cli_checksum_aborts_before_tar(self, tmp_path):
        require_tool(
            "sha256sum",
            "docker-dind checksum bootstrap",
            "Install coreutils in the test job image.",
        )
        result = run_step(tmp_path, "0" * 64)
        assert result.returncode != 0, result.stdout + result.stderr
        assert not (tmp_path / "tar-ran").exists(), "tar ran on an unverified tarball"

    def test_matching_checksums_reach_the_daemon_probe(self, tmp_path):
        require_tool(
            "sha256sum",
            "docker-dind checksum bootstrap",
            "Install coreutils in the test job image.",
        )
        result = run_step(tmp_path, hashlib.sha256(CLI_PAYLOAD).hexdigest())
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "tar-ran").exists()
