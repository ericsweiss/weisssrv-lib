#!/usr/bin/env python3
"""Tests for node_exporter_host's smartmon collector.

A run that enumerated no device publishes `smartmon_collector_success 0`, so it
cannot read as a clean run. A stub `smartctl` on PATH feeds it."""

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
    / "smartmon-collector.sh"
)
SH = shutil.which("sh") or "/bin/sh"

DEVICE_REPORT = """\
Device Model:     FAKE DISK 1000
Serial Number:    SN0123456789
SMART overall-health self-assessment test result: PASSED

ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
194 Temperature_Celsius     0x0022   035   045   000    Old_age   Always       -       35
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       2
"""


def _run(tmp_path: Path, scan: str | None, report: str = DEVICE_REPORT) -> dict:
    """Run the collector with an optional stub smartctl on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if scan is not None:
        report_file = tmp_path / "report.txt"
        report_file.write_text(report)
        scan_file = tmp_path / "scan.txt"
        scan_file.write_text(scan)
        stub = bin_dir / "smartctl"
        stub.write_text(
            "#!%s\n"
            'case "$1" in\n'
            "  --scan-open) cat %s ;;\n"
            "  *) cat %s ;;\n"
            "esac\n" % (SH, scan_file, report_file)
        )
        stub.chmod(0o755)

    out_dir = tmp_path / "textfile"
    proc = subprocess.run(
        [SH, str(SCRIPT), str(out_dir)],
        capture_output=True,
        text=True,
        env={"PATH": "%s:%s" % (bin_dir, "/usr/bin:/bin:/usr/sbin:/sbin")},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    metrics = {}
    for line in (out_dir / "smartmon.prom").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, value = line.rpartition(" ")
        metrics[name] = value
    assert [p.name for p in out_dir.iterdir()] == ["smartmon.prom"], "scan file left behind"
    return metrics


def test_the_collector_is_executable_shell() -> None:
    assert SCRIPT.exists()
    subprocess.run([SH, "-n", str(SCRIPT)], check=True)


def test_a_measured_run_reports_success(tmp_path: Path) -> None:
    metrics = _run(tmp_path, "/dev/sda -d sat # /dev/sda, ATA device\n")
    assert metrics["smartmon_collector_success"] == "1"
    assert metrics['smartmon_device_active{device="/dev/sda"}'] == "1"
    assert metrics['smartmon_device_smart_healthy{device="/dev/sda"}'] == "1"
    assert metrics['smartmon_temperature_celsius{device="/dev/sda"}'] == "35"
    assert metrics['smartmon_current_pending_sector_count{device="/dev/sda"}'] == "2"
    assert "smartmon_collector_last_success_seconds" in metrics


def test_a_scan_that_enumerated_nothing_reports_no_measurement(tmp_path: Path) -> None:
    """No devices with a fresh sentinel would read as a clean run."""
    metrics = _run(tmp_path, "")
    assert metrics["smartmon_collector_success"] == "0"
    assert "smartmon_collector_last_success_seconds" in metrics
    assert not [k for k in metrics if k.startswith("smartmon_device_")]


@pytest.mark.skipif(shutil.which("smartctl") is not None, reason="host has smartmontools")
def test_a_host_without_smartctl_reports_no_measurement(tmp_path: Path) -> None:
    metrics = _run(tmp_path, None)
    assert metrics["smartmon_collector_success"] == "0"
    assert "smartmon_collector_last_success_seconds" in metrics


def test_a_standby_drive_is_still_a_measured_run(tmp_path: Path) -> None:
    """A sleeping drive is a successful cycle: the probe must not wake it."""
    metrics = _run(
        tmp_path,
        "/dev/sda -d sat # /dev/sda, ATA device\n",
        report="Device is in STANDBY mode, exit(2)\n",
    )
    assert metrics["smartmon_collector_success"] == "1"
