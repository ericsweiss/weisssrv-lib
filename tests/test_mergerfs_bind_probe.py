"""The MergerFS bind probe never reports an idle union it could not check.

An empty answer from a tool that did not run would unexport and unmount unions
that still have active NFS clients.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = (REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nas_storage"
          / "files" / "mergerfs-bind-probe.sh")

FINDMNT = """#!/bin/sh
case "$*" in
  *--first-only*) echo "0:42" ;;
  *--list*) printf '0:42 /union\\n0:42 /export/media\\n' ;;
esac
"""


# ss with a single state filter prints no State column; -H drops the header.
SS_HEADER = "Recv-Q Send-Q Local Address:Port Peer Address:Port Process"


def _ss(*rows: str) -> str:
    """An ss stub that prints the real single-state header unless -H is given."""
    body = "".join("echo '%s'\n" % row for row in rows)
    return ('#!/bin/sh\ncase " $* " in *" -H"*) ;; *) echo \'%s\' ;; esac\n%s'
            % (SS_HEADER, body))


# PATH is the whole point here: the real findmnt and ss must not be reachable,
# so the fixture PATH holds only the stubs plus the tools the script pipes to.
NEEDED = ("awk", "head", "sh")


def _bin(tmp_path: Path, **stubs: str) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in NEEDED:
        real = shutil.which(name)
        assert real, "%s is required to run this suite" % name
        (bin_dir / name).symlink_to(real)
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(bin_dir)
    return env


BASH = shutil.which("bash") or "/bin/bash"


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(SCRIPT), "check", "/union"],
                          env=env, capture_output=True, text=True)


def test_an_idle_union_is_reported_idle(tmp_path):
    env = _bin(tmp_path, findmnt=FINDMNT, ss=_ss())
    proc = _run(env)
    assert proc.returncode == 0, proc.stderr
    assert "NFS_IDLE" in proc.stdout


def test_an_active_client_blocks_the_cycle(tmp_path):
    env = _bin(tmp_path, findmnt=FINDMNT,
               ss=_ss("0 0 10.0.0.1:2049 10.0.0.9:801"))
    proc = _run(env)
    assert proc.returncode == 1
    assert "ACTIVE_NFS_CLIENTS" in proc.stdout


BROKEN_LIST = FINDMNT.replace("*--list*) printf '0:42 /union\\n0:42 /export/media\\n' ;;",
                              "*--list*) exit 1 ;;")


@pytest.mark.parametrize(
    ("stubs", "removed", "stderr"),
    [
        # Without ss the probe cannot tell "no clients" from "I could not look".
        pytest.param({"findmnt": FINDMNT, "ss": _ss()}, "ss", "ss is not available", id="absent-ss"),
        pytest.param({"findmnt": FINDMNT, "ss": "#!/bin/sh\nexit 2\n"}, None, "ss failed",
                     id="failing-ss"),
        # findmnt present but unable to list mounts must not read as a clean union.
        pytest.param({"findmnt": BROKEN_LIST, "ss": _ss()}, None, "findmnt failed",
                     id="failing-findmnt-list"),
        pytest.param({"findmnt": FINDMNT}, "findmnt", "findmnt is not available",
                     id="absent-findmnt"),
    ],
)
def test_a_probe_that_could_not_look_is_refused(tmp_path, stubs, removed, stderr):
    env = _bin(tmp_path, **stubs)
    if removed:
        (tmp_path / "bin" / removed).unlink()
    proc = _run(env)
    assert proc.returncode == 3
    assert "NFS_IDLE" not in proc.stdout
    assert "NO_NFS_BIND_EXPORTS" not in proc.stdout
    assert stderr in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
