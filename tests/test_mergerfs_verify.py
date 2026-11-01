"""The MergerFS verify script passes only a live union with its fstab options.

Both verify tasks in nas_storage's mergerfs.yml run this script; each case
drives it against a fixture fstab with mountpoint and findmnt stubbed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nas_storage"
SCRIPT = ROLE / "files" / "mergerfs-verify.sh"
TASKS = ROLE / "tasks" / "mergerfs.yml"
BASH = shutil.which("bash") or "/bin/bash"
NEEDED = ("awk",)
MOUNTED = "#!/bin/sh\nexit 0\n"
UNMOUNTED = "#!/bin/sh\nexit 1\n"
OPTS = "allow_other,category.create=mfs,fsname=media"


def _findmnt(devices: dict[str, str]) -> str:
    """A findmnt answering MAJ:MIN for each --mountpoint in `devices`."""
    cases = "".join(f'  "{path}") echo "{dev}" ;;\n' for path, dev in devices.items())
    return ('#!/bin/sh\nwhile [ "$#" -gt 1 ]; do [ "$1" = --mountpoint ] && break; '
            'shift; done\ncase "$2" in\n' + cases + "  *) exit 1 ;;\nesac\n")


def _union(tmp_path: Path, control: bool = True) -> Path:
    union = tmp_path / "union"
    union.mkdir(exist_ok=True)
    if control:
        (union / ".mergerfs").write_text("", encoding="utf-8")
    return union


def _run(tmp_path: Path, fstab: str, *args: str, mountpoint: str = MOUNTED,
         findmnt: str | None = None) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in NEEDED:
        real = shutil.which(name)
        assert real, f"{name} is required to run this suite"
        if not (bin_dir / name).exists():
            (bin_dir / name).symlink_to(real)
    for name, body in (("mountpoint", mountpoint), ("findmnt", findmnt)):
        if body is not None:
            (bin_dir / name).write_text(body, encoding="utf-8")
            (bin_dir / name).chmod(0o755)
    (tmp_path / "fstab").write_text(fstab, encoding="utf-8")
    env = dict(os.environ, PATH=str(bin_dir), FSTAB=str(tmp_path / "fstab"))
    return subprocess.run([BASH, str(SCRIPT), *args], env=env,
                          capture_output=True, text=True)


def _line(target: Path | str, opts: str = OPTS) -> str:
    return f"/mnt/disk* {target} fuse.mergerfs {opts} 0 0\n"


def test_a_union_with_its_options_is_ok(tmp_path):
    union = _union(tmp_path)
    proc = _run(tmp_path, _line(union), str(union), "allow_other category.create=mfs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"FSTAB_OPTS: {OPTS}" in proc.stdout
    assert "STATUS: OK" in proc.stdout


def test_no_required_options_needs_only_the_fstab_line(tmp_path):
    union = _union(tmp_path)
    assert _run(tmp_path, _line(union), str(union), "").returncode == 0


@pytest.mark.parametrize("args", [(), ("/union",), ("", "x")])
def test_a_missing_argument_exits_two(tmp_path, args):
    assert _run(tmp_path, "", *args).returncode == 2


def test_an_unmounted_union_fails(tmp_path):
    union = _union(tmp_path)
    proc = _run(tmp_path, _line(union), str(union), "", mountpoint=UNMOUNTED)
    assert proc.returncode == 1
    assert proc.stdout.strip() == "STATUS: NOT_MOUNTED"


def test_a_mount_without_the_control_file_is_not_mergerfs(tmp_path):
    union = _union(tmp_path, control=False)
    proc = _run(tmp_path, _line(union), str(union), "")
    assert proc.returncode == 1
    assert "STATUS: NOT_MERGERFS" in proc.stdout


@pytest.mark.parametrize("fstab", [
    "#/mnt/disk* {u} fuse.mergerfs commented 0 0\n",
    "  # /mnt/disk* {u} fuse.mergerfs commented 0 0\n",
    "{u} /srv/export none bind,ro 0 0\n",
])
def test_a_commented_or_source_field_line_is_not_read(tmp_path, fstab):
    union = _union(tmp_path)
    proc = _run(tmp_path, fstab.format(u=union), str(union), "")
    assert proc.returncode == 1
    assert "STATUS: NO_FSTAB_ENTRY" in proc.stdout


@pytest.mark.parametrize("name, decoy", [
    ("u.ion", "uXion"),
    ("c++", "cc"),
    ("union", "union/sub"),
])
def test_a_metacharacter_or_prefix_does_not_widen_the_match(tmp_path, name, decoy):
    target = tmp_path / name
    target.mkdir()
    (target / ".mergerfs").write_text("", encoding="utf-8")
    decoy_line = _line(tmp_path / decoy, "decoy")
    ok = _run(tmp_path, decoy_line + _line(target, "real"), str(target), "real")
    assert ok.returncode == 0, ok.stdout
    assert "FSTAB_OPTS: real" in ok.stdout
    assert "NO_FSTAB_ENTRY" in _run(tmp_path, decoy_line, str(target), "").stdout


@pytest.mark.parametrize("required, missing", [
    ("ro", "ro"),
    ("allow_other cache.files=off", "cache.files=off"),
    ("allow_other,dropcacheonclose=true", "dropcacheonclose=true"),
])
def test_a_missing_option_is_named(tmp_path, required, missing):
    union = _union(tmp_path)
    fstab = _line(union, "allow_other,errors=remount-ro,cache.files=off-x")
    proc = _run(tmp_path, fstab, str(union), required)
    assert proc.returncode == 1
    assert proc.stdout.splitlines()[-1] == f"STATUS: FSTAB_MISSING_OPTIONS - {missing}"


def test_binds_on_the_union_device_pass(tmp_path):
    union = _union(tmp_path)
    findmnt = _findmnt({str(union): "0:42", "/srv/a": "0:42", "/srv/b": "0:42"})
    proc = _run(tmp_path, _line(union), str(union), "", "/srv/a", "/srv/b", findmnt=findmnt)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("devices, shown", [
    ({"/srv/b": "0:7"}, "(0:7)"),
    ({}, "(unmounted)"),
])
def test_a_bind_off_the_union_fails(tmp_path, devices, shown):
    union = _union(tmp_path)
    findmnt = _findmnt({str(union): "0:42", "/srv/a": "0:42", **devices})
    proc = _run(tmp_path, _line(union), str(union), "", "/srv/a", "/srv/b", findmnt=findmnt)
    assert proc.returncode == 1
    assert "STATUS: BIND_NOT_ON_UNION - /srv/b" in proc.stdout
    assert shown in proc.stdout


def test_an_unresolvable_union_device_fails_every_bind(tmp_path):
    union = _union(tmp_path)
    proc = _run(tmp_path, _line(union), str(union), "", "/srv/a",
                findmnt=_findmnt({}))
    assert proc.returncode == 1
    assert "BIND_NOT_ON_UNION" in proc.stdout


def test_both_verify_tasks_run_the_script():
    tasks = {t.get("name"): t for t in yaml.safe_load(TASKS.read_text(encoding="utf-8"))}
    for name in ("Verify MergerFS mount status and fstab options",
                 "Verify MergerFS mount after remount cycle"):
        assert tasks[name]["ansible.builtin.script"].startswith("mergerfs-verify.sh "), name
