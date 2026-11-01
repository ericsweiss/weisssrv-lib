"""The MergerFS idle probe never reports an idle union it could not check.

An IDLE answer from a probe without fuser, on an unmounted path or from a
failed fuser would let the remount cycle unmount a union that processes hold.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = (REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nas_storage"
          / "files" / "mergerfs-idle-probe.sh")

# PATH holds only the stubs plus the tools the script pipes to, so the real
# fuser is never reachable.
NEEDED = ("head", "sh", "tr", "mktemp", "rm")
MOUNTED = "#!/bin/sh\nexit 0\n"
FAILS = "#!/bin/sh\nexit 1\n"
BASH = shutil.which("bash") or "/bin/bash"


def _env(tmp_path: Path, fuser: str | None, mountpoint: str | None = MOUNTED,
         findmnt: str | None = FAILS) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in NEEDED:
        real = shutil.which(name)
        assert real, f"{name} is required to run this suite"
        (bin_dir / name).symlink_to(real)
    for name, body in (("fuser", fuser), ("mountpoint", mountpoint), ("findmnt", findmnt)):
        if body is not None:
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(bin_dir)
    return env


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(SCRIPT), *args], check=False, env=env,
                          capture_output=True, text=True)


@pytest.mark.parametrize(
    ("fuser", "union"),
    [
        pytest.param(FAILS, "/union", id="unheld"),
        pytest.param('#!/bin/sh\necho "/union:" >&2\nexit 1\n', "/union", id="path-header-only"),
        pytest.param('#!/bin/sh\necho "fuser: warning: something odd" >&2\nexit 0\n', "/union",
                     id="warning-on-success"),
        pytest.param('#!/bin/sh\necho "$2:" >&2\nexit 1\n', "/mnt/my union", id="header-with-space"),
    ],
)
def test_a_union_fuser_looked_at_and_found_unheld_is_idle(tmp_path, fuser, union):
    proc = _run(_env(tmp_path, fuser), union)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "IDLE"


def test_a_held_union_is_in_use(tmp_path):
    fuser = '#!/bin/sh\ncase "$1" in -m) echo " 1234c";; -vm) echo "root 1234 ..c.. sleep" >&2;; esac\n'
    proc = _run(_env(tmp_path, fuser), "/union")
    assert proc.returncode == 1
    assert "IN_USE" in proc.stdout
    assert "sleep" in proc.stdout


def test_an_absent_fuser_is_not_an_idle_union(tmp_path):
    proc = _run(_env(tmp_path, None), "/union")
    assert proc.returncode == 3
    assert "NO_FUSER" in proc.stdout
    assert "IDLE" not in proc.stdout


def test_a_missing_argument_is_a_usage_error(tmp_path):
    proc = _run(_env(tmp_path, FAILS))
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_an_unmounted_union_is_not_idle(tmp_path):
    proc = _run(_env(tmp_path, FAILS, FAILS), "/union")
    assert proc.returncode == 4
    assert proc.stdout.strip() == "NOT_MOUNTED: /union"


@pytest.mark.parametrize(
    ("fuser", "mountpoint", "findmnt", "expected"),
    [
        pytest.param(
            '#!/bin/sh\necho "Cannot stat file /union: Transport endpoint is not connected" >&2\nexit 1\n',
            MOUNTED, FAILS,
            "PROBE_ERROR: fuser failed on /union\n"
            "Cannot stat file /union: Transport endpoint is not connected",
            id="failed-fuser",
        ),
        pytest.param(FAILS, None, FAILS, "PROBE_ERROR: mountpoint is not installed",
                     id="absent-mountpoint"),
        pytest.param(FAILS, FAILS, '#!/bin/sh\necho "/union mergerfs pool"\nexit 0\n',
                     "PROBE_ERROR: /union is mounted but not responding", id="disconnected-union"),
        pytest.param(FAILS, MOUNTED, None, "PROBE_ERROR: findmnt is not installed",
                     id="absent-findmnt"),
    ],
)
def test_a_probe_that_could_not_look_is_a_probe_error(tmp_path, fuser, mountpoint, findmnt,
                                                     expected):
    proc = _run(_env(tmp_path, fuser, mountpoint, findmnt), "/union")
    assert proc.returncode == 3
    assert proc.stdout.strip() == expected
