#!/usr/bin/env python3
"""Tests for node_exporter_host's corosync-health collector.

An unsampleable corosync must fail the run so the sentinel goes stale, rather
than publishing a healthy-looking cpu=0. Stub `pidof` and `top` on PATH.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "ansible_collections"
    / "weisssrv"
    / "infra"
    / "roles"
    / "node_exporter_host"
    / "files"
    / "corosync-health-collector.sh"
)
SH = shutil.which("sh") or "/bin/sh"

TOP_SAMPLE = """\
  PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
 1234 root      20   0  100000  10000   5000 S   0.0   0.1   0:00.00 corosync
  PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
 1234 root      20   0  100000  10000   5000 S  12.5   0.1   0:00.00 corosync
"""


def _stubs(tmp_path: Path, pidof: str, top: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("pidof", pidof), ("top", top)):
        stub = bin_dir / name
        stub.write_text("#!%s\n%s\n" % (SH, body))
        stub.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, pidof: str, top: str) -> subprocess.CompletedProcess:
    bin_dir = _stubs(tmp_path, pidof, top)
    # A directory that does not exist yet: the OUT_DIR seam the role overrides.
    out_dir = tmp_path / "textfile"
    return subprocess.run(
        [SH, str(SCRIPT), str(out_dir)],
        capture_output=True,
        text=True,
        env={"PATH": "%s:%s" % (bin_dir, "/usr/bin:/bin:/usr/sbin:/sbin")},
        check=False,
    )


def _metrics(out_dir: Path) -> dict:
    metrics = {}
    for line in (out_dir / "corosync_health.prom").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, value = line.rpartition(" ")
        metrics[name] = value
    return metrics


def test_the_collector_is_executable_shell() -> None:
    assert SCRIPT.exists()
    subprocess.run([SH, "-n", str(SCRIPT)], check=True)


def test_a_running_corosync_publishes_the_second_sample(tmp_path) -> None:
    proc = _run(tmp_path, "echo 1234", "cat <<'EOF'\n%sEOF" % TOP_SAMPLE)
    assert proc.returncode == 0, proc.stderr
    metrics = _metrics(tmp_path / "textfile")
    assert metrics["proxmox_corosync_cpu_percent"] == "12.5"
    assert "proxmox_pmxcfs_manager_status_mtime_seconds" in metrics
    assert "proxmox_corosync_health_collector_last_success_seconds" in metrics


def test_an_absent_corosync_publishes_zero(tmp_path) -> None:
    proc = _run(tmp_path, "exit 1", "exit 1")
    assert proc.returncode == 0, proc.stderr
    assert _metrics(tmp_path / "textfile")["proxmox_corosync_cpu_percent"] == "0"


def test_an_unparseable_sample_fails_instead_of_publishing_zero(tmp_path) -> None:
    """The stale sentinel is the signal; a published 0 would read as healthy."""
    proc = _run(tmp_path, "echo 1234", "echo 'not a top table'")
    assert proc.returncode == 1
    assert "failed to sample corosync CPU" in proc.stderr
    assert not (tmp_path / "textfile" / "corosync_health.prom").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
