#!/usr/bin/env python3
"""Tests for node_exporter_host's smartmon collector.

`smartmon_collector_success` is 1 only when the scan succeeded and a listed
device published metrics. A stub `smartctl` on PATH feeds it."""

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

# No identity line and no standby notice: the shape of an unopenable device.
PROBE_FAILURE = "Smartctl open device: failed, no such device\n"


def _run(
    tmp_path: Path,
    scan: str | None,
    report: str = DEVICE_REPORT,
    scan_rc: int = 0,
    probe_rc: int = 0,
    reports: dict[str, str] | None = None,
) -> dict:
    """Run the collector with an optional stub smartctl on PATH.

    `reports` overrides one device's report and exits 0; others get `report`
    and `probe_rc`. `scan_rc` is the exit status of `--scan-open`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if scan is not None:
        report_file = tmp_path / "report.txt"
        report_file.write_text(report)
        for dev, text in (reports or {}).items():
            (tmp_path / ("report-%s.txt" % dev.rpartition("/")[2])).write_text(text)
        scan_file = tmp_path / "scan.txt"
        scan_file.write_text(scan)
        stub = bin_dir / "smartctl"
        stub.write_text(
            "#!%s\n"
            'case "$1" in\n'
            "  --scan-open) cat %s; exit %d ;;\n"
            "esac\n"
            'for a in "$@"; do last=$a; done\n'
            'f=%s/report-${last##*/}.txt\n'
            'if [ -f "$f" ]; then cat "$f"; exit 0; fi\n'
            "cat %s\n"
            "exit %d\n"
            % (SH, scan_file, scan_rc, tmp_path, report_file, probe_rc)
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
    assert metrics["smartmon_collector_devices_scanned"] == "1"
    assert metrics["smartmon_collector_devices_failed"] == "0"
    assert metrics['smartmon_device_active{device="/dev/sda"}'] == "1"
    assert metrics['smartmon_device_smart_healthy{device="/dev/sda"}'] == "1"
    assert metrics['smartmon_temperature_celsius{device="/dev/sda"}'] == "35"
    assert metrics['smartmon_current_pending_sector_count{device="/dev/sda"}'] == "2"
    assert "smartmon_collector_last_success_seconds" in metrics


def test_a_scan_that_enumerated_nothing_reports_no_measurement(tmp_path: Path) -> None:
    """No devices with a fresh sentinel would read as a clean run."""
    metrics = _run(tmp_path, "")
    assert metrics["smartmon_collector_success"] == "0"
    assert metrics["smartmon_collector_devices_scanned"] == "0"
    assert metrics["smartmon_collector_devices_failed"] == "0"
    assert "smartmon_collector_last_success_seconds" in metrics
    assert not [k for k in metrics if k.startswith("smartmon_device_")]


def test_a_failed_scan_reports_no_measurement(tmp_path: Path) -> None:
    """A non-zero --scan-open exit means the device list is not trustworthy."""
    metrics = _run(tmp_path, "/dev/sda -d sat # /dev/sda, ATA device\n", scan_rc=1)
    assert metrics["smartmon_collector_success"] == "0"
    assert metrics['smartmon_device_active{device="/dev/sda"}'] == "1"
    assert metrics["smartmon_collector_devices_scanned"] == "1"
    assert metrics["smartmon_collector_devices_failed"] == "0"


def test_every_probe_failing_reports_no_measurement(tmp_path: Path) -> None:
    """Devices listed but none parsed is the condition the gauge exists for."""
    metrics = _run(
        tmp_path,
        "/dev/sda -d sat # /dev/sda, ATA device\n/dev/sdb -d sat # /dev/sdb, ATA device\n",
        report=PROBE_FAILURE,
    )
    assert metrics["smartmon_collector_success"] == "0"
    assert metrics["smartmon_collector_devices_scanned"] == "2"
    assert metrics["smartmon_collector_devices_failed"] == "2"
    assert not [k for k in metrics if k.startswith("smartmon_device_")]


def test_one_good_device_among_failures_is_a_measured_run(tmp_path: Path) -> None:
    metrics = _run(
        tmp_path,
        "/dev/sda -d sat # /dev/sda, ATA device\n/dev/sdb -d sat # /dev/sdb, ATA device\n",
        report=PROBE_FAILURE,
        reports={"/dev/sda": DEVICE_REPORT},
    )
    assert metrics["smartmon_collector_success"] == "1"
    assert metrics["smartmon_collector_devices_scanned"] == "2"
    assert metrics["smartmon_collector_devices_failed"] == "1"
    assert metrics['smartmon_device_active{device="/dev/sda"}'] == "1"
    assert 'smartmon_device_active{device="/dev/sdb"}' not in metrics


@pytest.mark.skipif(shutil.which("smartctl") is not None, reason="host has smartmontools")
def test_a_host_without_smartctl_reports_no_measurement(tmp_path: Path) -> None:
    metrics = _run(tmp_path, None)
    assert metrics["smartmon_collector_success"] == "0"
    assert metrics["smartmon_collector_devices_scanned"] == "0"
    assert "smartmon_collector_last_success_seconds" in metrics


def test_a_standby_drive_is_still_a_measured_run(tmp_path: Path) -> None:
    """A sleeping drive is a successful cycle: the probe must not wake it."""
    metrics = _run(
        tmp_path,
        "/dev/sda -d sat # /dev/sda, ATA device\n",
        report="Device is in STANDBY mode, exit(2)\n",
        probe_rc=2,
    )
    assert metrics["smartmon_collector_success"] == "1"
    assert metrics["smartmon_collector_devices_scanned"] == "1"
    assert metrics["smartmon_collector_devices_failed"] == "0"
    assert metrics['smartmon_device_active{device="/dev/sda"}'] == "0"
