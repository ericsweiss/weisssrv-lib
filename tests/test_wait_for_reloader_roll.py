#!/usr/bin/env python3
"""scripts/wait-for-reloader-roll.sh fails when the generation never moves.

`kubectl` is a PATH stub that replays a scripted sequence of generations, so
each arm runs without a cluster.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "wait-for-reloader-roll.sh"
)
BASH = shutil.which("bash") or "/bin/bash"

# Prints the next value of GENERATIONS (a space-separated list) on each call,
# repeating the last one once the list runs out. A value of `-` exits non-zero
# with no output, which is the unreadable-generation arm.
STUB_KUBECTL = """\
#!%s
printf 'kubectl %%s\\n' "$*" >> "$TRACE"
n=$(wc -l < "$TRACE" | tr -d ' ')
i=0
for gen in $GENERATIONS; do
    i=$((i + 1))
    [ "$i" -ge "$n" ] && break
done
if [ "$gen" = "-" ]; then
    exit 1
fi
printf '%%s' "$gen"
""" % BASH


@pytest.fixture()
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    trace.write_text("")
    stub = bin_dir / "kubectl"
    stub.write_text(STUB_KUBECTL)
    stub.chmod(0o755)

    def _run(*args, generations="1", **env):
        proc = subprocess.run(
            [BASH, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                "PATH": "%s:%s" % (bin_dir, os.environ.get("PATH", "")),
                "TRACE": str(trace),
                "GENERATIONS": generations,
                **{k: str(v) for k, v in env.items()},
            },
        )
        return proc, trace.read_text().splitlines()

    return _run


def test_a_moved_generation_succeeds(run):
    proc, trace = run("downloads", "qbittorrent", "1", "5", generations="2")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert trace[0].startswith("kubectl get deployment qbittorrent -n downloads")


def test_an_unmoved_generation_fails_at_the_deadline(run):
    """CRITICAL: the caller's `kubectl rollout status` would read the unchanged
    generation as a finished roll, so timing out must be a non-zero exit."""
    proc, _ = run(
        "downloads", "qbittorrent", "1", "0", "provider",
        generations="1",
        RELOADER_POLL_INTERVAL=0,
    )
    assert proc.returncode == 1
    assert "did not roll deployment/qbittorrent" in proc.stdout
    assert "generation still 1" in proc.stdout
    assert "The provider patch applied" in proc.stdout


def test_the_remedy_deletes_the_pod_rather_than_restarting_it(run):
    """`rollout restart` writes an annotation the kustomize-controller reverts,
    undoing the restart; the printed remedy has to be the delete."""
    proc, _ = run(
        "downloads", "qbittorrent", "1", "0", generations="1", RELOADER_POLL_INTERVAL=0
    )
    assert "kubectl delete pod -n downloads" in proc.stdout
    assert "rollout restart" not in proc.stdout


def test_an_unreadable_generation_keeps_polling_then_fails(run):
    """An API blip must not read as a roll: the generation stays unknown and the
    deadline reports the roll that was never observed."""
    proc, trace = run(
        "downloads", "qbittorrent", "1", "0", generations="- -", RELOADER_POLL_INTERVAL=0
    )
    assert proc.returncode == 1
    assert len(trace) >= 1
    assert "generation still 1" in proc.stdout


def test_a_missing_argument_is_a_usage_error(run):
    proc, trace = run("downloads")
    assert proc.returncode != 0
    assert "deployment name required" in proc.stderr
    assert trace == []
