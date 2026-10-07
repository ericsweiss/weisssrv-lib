#!/usr/bin/env python3
"""Tests for node_exporter_host's zpool-status collector.

`zpool status` records an error against the vdev that saw it, so the collector
takes the max over the table, not the sum. A stub `zpool` on PATH feeds it."""

import datetime
import shutil
import subprocess
import sys
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
    / "zpool-status-collector.sh"
)
SH = shutil.which("sh") or "/bin/sh"

# ZFS-8000-9P: a checksum error corrected by raidz redundancy increments the
# LEAF only, so every row above it still reads 0.
RAIDZ2_STATUS = """\
  pool: tank
 state: ONLINE
status: One or more devices has experienced an unrecoverable error.
  scan: scrub repaired 0B in 01:02:03 with 0 errors on Sun Sep  7 04:05:06 2025
config:

\tNAME         STATE     READ WRITE CKSUM
\ttank         ONLINE       0     0     0
\t  raidz2-0   ONLINE       0     0     0
\t    sda      ONLINE       0     0     0
\t    sdb      ONLINE       0     0     3
\t    sdc      ONLINE       0     0     0
\tlogs
\t  nvme0n1    ONLINE       0     0     0

errors: No known data errors
"""

SUFFIXED_STATUS = RAIDZ2_STATUS.replace(
    "\t    sdb      ONLINE       0     0     3",
    "\t    sdb      ONLINE       0     0  1.2K",
)

# The same error propagated all the way up, which is what a pool-level fault
# looks like: the max must still report it once, not three times.
PROPAGATED_STATUS = RAIDZ2_STATUS.replace(
    "\ttank         ONLINE       0     0     0",
    "\ttank         ONLINE       0     0     3",
).replace(
    "\t  raidz2-0   ONLINE       0     0     0",
    "\t  raidz2-0   ONLINE       0     0     3",
)

# A DEGRADED table: zpool appends a free-text note after CKSUM on exactly the
# rows that carry errors, and an NF-relative read of the counters skips them.
DEGRADED_STATUS = """\
  pool: tank
 state: DEGRADED
status: One or more devices is being resilvered.
  scan: resilver in progress since Sun Sep  7 04:05:06 2025
config:

\tNAME             STATE     READ WRITE CKSUM
\ttank             DEGRADED     0     0     0
\t  raidz2-0       DEGRADED     0     0     0
\t    replacing-0  DEGRADED     0     0     0
\t      old        UNAVAIL      0     0     0  corrupted data
\t      sdb        ONLINE       0     0     3  (resilvering)
\t    sdc          FAULTED      0     0    38  too many errors

errors: No known data errors
"""


# A Monday-completed scan. `Mon ` carries no " on ", so the scrub-date pattern
# has to stop at " errors on " and keep the weekday.
MONDAY_STATUS = RAIDZ2_STATUS.replace(
    "errors on Sun Sep  7 04:05:06 2025", "errors on Mon Sep 22 03:00:01 2025",
)
MONDAY_SCAN_DATE = "Mon Sep 22 03:00:01 2025"

# The collector's sed and `date -d` are GNU: BSD sed parses neither `\|` nor the
# escaped group, so the end-to-end scrub-date assertions need GNU sed on PATH.
GNU_SED = "GNU sed" in subprocess.run(
    ["sed", "--version"], capture_output=True, text=True, check=False,
).stdout


def _date_stub(capture: Path) -> str:
    """A `date` that records its -d argument and parses it strictly.

    Strict on purpose: a scan date that lost its weekday fails the parse, which
    is what turns a silent `scrub_ts=0` into a red test.
    """
    return (
        "#!%s\n"
        "import datetime, sys\n"
        "argv = sys.argv[1:]\n"
        "if argv[:1] == ['-d']:\n"
        "    open(%r, 'w').write(argv[1])\n"
        "    try:\n"
        "        stamp = datetime.datetime.strptime(\n"
        "            ' '.join(argv[1].split()), '%%a %%b %%d %%H:%%M:%%S %%Y')\n"
        "    except ValueError:\n"
        "        sys.exit(1)\n"
        "    print(int(stamp.timestamp()))\n"
        "else:\n"
        "    print(1700000000)\n"
    ) % (sys.executable, str(capture))


def _run(tmp_path: Path, status: str) -> dict:
    """Run the collector against a stub zpool and return the parsed .prom."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    status_file = tmp_path / "status.txt"
    status_file.write_text(status)
    stub = bin_dir / "zpool"
    stub.write_text(
        "#!%s\n"
        'case "$1" in\n'
        '  list) case "$2" in -Hpo) printf "1024\\t2048\\n" ;; *) printf "tank\\n" ;; esac ;;\n'
        '  status) cat %s ;;\n'
        "esac\n" % (SH, status_file)
    )
    stub.chmod(0o755)
    date_stub = bin_dir / "date"
    date_stub.write_text(_date_stub(tmp_path / "date-d-arg.txt"))
    date_stub.chmod(0o755)

    out_dir = tmp_path / "textfile"
    env = {"PATH": "%s:%s" % (bin_dir, "/usr/bin:/bin:/usr/sbin:/sbin")}
    proc = subprocess.run(
        [SH, str(SCRIPT), str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    metrics = {}
    for line in (out_dir / "zfs_pool_status.prom").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, value = line.rpartition(" ")
        metrics[name] = value
    return metrics


def test_the_collector_is_executable_shell() -> None:
    assert SCRIPT.exists()
    subprocess.run([SH, "-n", str(SCRIPT)], check=True)


def test_a_leaf_only_error_is_reported(tmp_path: Path) -> None:
    """A redundancy-corrected checksum error lands on the leaf and nowhere else,
    so reading the pool row alone would publish 0 and the alert never fires."""
    metrics = _run(tmp_path, RAIDZ2_STATUS)
    assert metrics['zfs_pool_status_errors_total{pool="tank",type="cksum"}'] == "3"
    assert metrics['zfs_pool_status_errors_total{pool="tank",type="read"}'] == "0"
    assert metrics['zfs_pool_status_errors_total{pool="tank",type="write"}'] == "0"


def test_a_propagated_error_is_reported_once(tmp_path: Path) -> None:
    """Summing pool, group and leaf rows would report 9 for the same 3 errors."""
    metrics = _run(tmp_path, PROPAGATED_STATUS)
    assert metrics['zfs_pool_status_errors_total{pool="tank",type="cksum"}'] == "3"


def test_a_suffixed_counter_keeps_its_integer_part(tmp_path: Path) -> None:
    """Counts above 1000 print as `1.2K`; the metric must still be a number."""
    metrics = _run(tmp_path, SUFFIXED_STATUS)
    assert metrics['zfs_pool_status_errors_total{pool="tank",type="cksum"}'] == "1"


def test_a_row_carrying_a_status_note_still_reports_its_counters(tmp_path: Path) -> None:
    """A resilvering or faulted leaf trails free text after CKSUM. Reading the
    counters from the end of the line publishes 0 for the disk that is failing."""
    metrics = _run(tmp_path, DEGRADED_STATUS)
    assert metrics['zfs_pool_status_errors_total{pool="tank",type="cksum"}'] == "38"
    assert metrics['zfs_pool_status_health_code{pool="tank"}'] == "1"


def test_the_other_pool_series_still_render(tmp_path: Path) -> None:
    metrics = _run(tmp_path, RAIDZ2_STATUS)
    assert metrics['zfs_pool_status_health_code{pool="tank"}'] == "0"
    assert metrics['zfs_pool_status_allocated_bytes{pool="tank"}'] == "1024"
    assert metrics['zfs_pool_status_size_bytes{pool="tank"}'] == "2048"
    assert "zfs_pool_status_collector_last_success_seconds" in metrics


@pytest.mark.parametrize(
    "fragment",
    [
        "if (r+0 > rs) rs = r+0",
        "if (c+0 > cs) cs = c+0",
        "in_cfg && /^[[:space:]]+/",
        "r=$3",
        r".* on \(.*\)$",
    ],
)
def test_the_max_selection_stays_in_the_script(fragment: str) -> None:
    """Pinned so a rewrite to `rs += r`, or back to the pool row alone, fails
    here instead of tripling the count or silently publishing zero."""
    assert fragment in SCRIPT.read_text()


def _run_bare(tmp_path: Path, stub: str | None) -> dict:
    """Run the collector with an optional stub zpool body on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if stub is not None:
        path = bin_dir / "zpool"
        path.write_text("#!%s\n%s" % (SH, stub))
        path.chmod(0o755)
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
    for line in (out_dir / "zfs_pool_status.prom").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, value = line.rpartition(" ")
        metrics[name] = value
    return metrics


def test_a_measured_run_reports_success(tmp_path: Path) -> None:
    metrics = _run(tmp_path, RAIDZ2_STATUS)
    assert metrics["zfs_pool_status_collector_success"] == "1"


@pytest.mark.skipif(shutil.which("zpool") is not None, reason="host has real ZFS")
def test_a_host_without_zpool_reports_no_measurement(tmp_path: Path) -> None:
    """Without the success gauge, "no ZFS here" and "the pools vanished" are the
    same fresh sentinel with no per-pool series."""
    metrics = _run_bare(tmp_path, None)
    assert metrics["zfs_pool_status_collector_success"] == "0"
    assert "zfs_pool_status_collector_last_success_seconds" in metrics


def test_a_host_whose_pools_all_vanished_reports_no_measurement(tmp_path: Path) -> None:
    metrics = _run_bare(tmp_path, 'exit 0\n')
    assert metrics["zfs_pool_status_collector_success"] == "0"
    assert not [k for k in metrics if k.startswith("zfs_pool_status_health_code")]


@pytest.mark.skipif(not GNU_SED, reason="collector's sed expression is GNU BRE")
def test_a_monday_scrub_keeps_its_weekday(tmp_path: Path) -> None:
    """A greedy `on` match eats the weekday, and a wording change then leaves
    `date -d` nothing to parse — published as "never scrubbed"."""
    metrics = _run(tmp_path, MONDAY_STATUS)
    expected = int(
        datetime.datetime.strptime(
            MONDAY_SCAN_DATE, "%a %b %d %H:%M:%S %Y"
        ).timestamp()
    )
    assert (tmp_path / "date-d-arg.txt").read_text() == MONDAY_SCAN_DATE
    assert metrics['zfs_pool_status_last_scrub_seconds{pool="tank"}'] == str(expected)
    assert metrics['zfs_pool_status_last_scrub_seconds{pool="tank"}'] != "0"


@pytest.mark.skipif(not GNU_SED, reason="collector's sed expression is GNU BRE")
def test_a_two_space_day_scrub_keeps_its_weekday(tmp_path: Path) -> None:
    """`zpool` pads a single-digit day, which the strict parse must still take."""
    _run(tmp_path, RAIDZ2_STATUS)
    assert (tmp_path / "date-d-arg.txt").read_text() == "Sun Sep  7 04:05:06 2025"
