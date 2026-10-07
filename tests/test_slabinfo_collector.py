#!/usr/bin/env python3
"""Tests for node_exporter_host's slabinfo collector.

Asserts the right /proc/slabinfo columns and allowlist-only publication. A
fixture stands in via the script's SLABINFO seam, so no Linux host is needed.
"""

import shutil
import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "ansible_collections"
    / "weisssrv"
    / "infra"
    / "roles"
    / "node_exporter_host"
    / "files"
    / "slabinfo-collector.sh"
)
SH = shutil.which("sh") or "/bin/sh"

# Real shape: name active_objs num_objs objsize objperslab pagesperslab
# : tunables ... : slabdata active_slabs num_slabs sharedavail
SLABINFO = """\
slabinfo - version: 2.1
# name            <active_objs> <num_objs> <objsize> <objperslab> <pagesperslab> \
: tunables <limit> <batchcount> <sharedfactor> : slabdata <active_slabs> <num_slabs> <sharedavail>
skbuff_ext_cache    1200   1300    192   21    2 : tunables    0    0    0 : slabdata     61     62      0
dentry             50000  51000    192   21    1 : tunables    0    0    0 : slabdata   2429   2430      0
"""


def _run(
    tmp_path: Path,
    *args: str,
    slabinfo: str | None = SLABINFO,
    conf: Path | None = None,
) -> dict:
    out_dir = tmp_path / "textfile"
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    # Pinned even when unset, so a deployed host's real allowlist cannot leak in.
    env["SLABINFO_CONF"] = str(conf if conf is not None else tmp_path / "absent-conf")
    if slabinfo is None:
        env["SLABINFO"] = str(tmp_path / "absent")
    else:
        fixture = tmp_path / "slabinfo"
        fixture.write_text(slabinfo)
        env["SLABINFO"] = str(fixture)
    proc = subprocess.run(
        [SH, str(SCRIPT), str(out_dir), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    metrics = {}
    for line in (out_dir / "node_slabinfo.prom").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, value = line.rpartition(" ")
        metrics[name] = value
    return metrics


def test_the_collector_is_valid_shell() -> None:
    assert SCRIPT.exists()
    subprocess.run([SH, "-n", str(SCRIPT)], check=True)


def test_one_cache_reports_objects_bytes_and_pages(tmp_path: Path) -> None:
    """objsize and pagesperslab sit in different columns from the counts, and a
    column slip still produces plausible-looking numbers."""
    metrics = _run(tmp_path, "skbuff_ext_cache")
    assert metrics['node_slab_objects{cache="skbuff_ext_cache"}'] == "1200"
    # active_objs * objsize
    assert metrics['node_slab_object_bytes{cache="skbuff_ext_cache"}'] == str(1200 * 192)
    # num_slabs * pagesperslab
    assert metrics['node_slab_pages{cache="skbuff_ext_cache"}'] == str(62 * 2)


def test_only_allowlisted_caches_are_published(tmp_path: Path) -> None:
    """The allowlist is the cardinality bound: publishing the whole file would
    add a few hundred series per host without anyone noticing."""
    metrics = _run(tmp_path, "skbuff_ext_cache")
    assert not [name for name in metrics if 'cache="dentry"' in name]


def test_an_unknown_cache_is_silently_absent(tmp_path: Path) -> None:
    """A cache renamed by a kernel upgrade must not fail the run, but an absent
    series alone reads exactly like a leak that stopped."""
    metrics = _run(tmp_path, "cache_that_does_not_exist")
    assert metrics["node_slabinfo_collector_success"] == "1"
    assert not [name for name in metrics if name.startswith("node_slab_objects")]
    assert metrics['node_slab_cache_present{cache="cache_that_does_not_exist"}'] == "0"


def test_an_unreadable_slabinfo_reports_a_meta_failure(tmp_path: Path) -> None:
    """LXC guests and CONFIG_SLUB_DEBUG-less kernels have none. Success 0 tells
    that apart from a timer that never fired, which looks identical."""
    metrics = _run(tmp_path, "skbuff_ext_cache", slabinfo=None)
    assert metrics["node_slabinfo_collector_success"] == "0"
    # The sentinel records the last SUCCESS, so a failed run must not stamp it.
    assert "node_slabinfo_collector_last_success_seconds" not in metrics


def test_no_allowlist_publishes_no_cache_series(tmp_path: Path) -> None:
    metrics = _run(tmp_path)
    assert metrics["node_slabinfo_collector_success"] == "1"
    assert not [name for name in metrics if name.startswith("node_slab_objects")]
    assert not [name for name in metrics if name.startswith("node_slab_cache_present")]


def test_the_allowlist_from_the_conf_file_publishes_cache_series(tmp_path: Path) -> None:
    """The unit passes no arguments, so the conf file is the production path."""
    conf = tmp_path / "slabinfo-collector"
    conf.write_text('SLAB_CACHES="skbuff_ext_cache"\n')
    metrics = _run(tmp_path, conf=conf)
    assert metrics["node_slabinfo_collector_success"] == "1"
    assert metrics['node_slab_objects{cache="skbuff_ext_cache"}'] == "1200"
    assert metrics['node_slab_cache_present{cache="skbuff_ext_cache"}'] == "1"


def test_a_two_cache_conf_value_word_splits_into_two_series(tmp_path: Path) -> None:
    conf = tmp_path / "slabinfo-collector"
    conf.write_text('SLAB_CACHES="skbuff_ext_cache dentry"\n')
    metrics = _run(tmp_path, conf=conf)
    assert metrics['node_slab_objects{cache="skbuff_ext_cache"}'] == "1200"
    assert metrics['node_slab_objects{cache="dentry"}'] == "50000"


def test_a_present_cache_publishes_a_presence_one(tmp_path: Path) -> None:
    metrics = _run(tmp_path, "skbuff_ext_cache")
    assert metrics['node_slab_cache_present{cache="skbuff_ext_cache"}'] == "1"
    assert "node_slabinfo_collector_last_success_seconds" in metrics
