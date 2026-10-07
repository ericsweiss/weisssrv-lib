"""swap-clean must predict an impossible escalation, not stop guests for it.

The estimate counts what stopping a guest frees now, its resident memory plus
the swap it holds, and the target is re-read as each guest stops.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nas_storage"
TEMPLATE = ROLE / "templates" / "swap-clean.sh.j2"

GUESTS = ["157:immich:120", "153:gitlab:300"]
# The role's own defaults are the context: a hand-written stand-in would let a
# renamed default pass here while the real render fails.
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
MARGIN_KB = int(DEFAULTS["nas_storage_swap_clean_margin_mb"]) * 1024

# The feasibility gate, as the rendered script spells it. Replacing these lines
# is the mutation the unreachable cases are measured against.
GATE_LINES = (
    '    compute_attainable_headroom "$avail_kb"\n'
    '    if [ "$attainable_kb" -lt "$need_kb" ]; then\n'
)

# The live resident reading the estimate rests on. Swapping it for the guest's
# configured memory (maxmem) is the mutation the ballooned case is measured
# against.
RESIDENT_PARSE = (
    '  bytes="$({ qm status "$1" --verbose 2>/dev/null || true; }'
    " | awk '$1 == \"mem:\" { print $2; exit }')\"\n"
)
CONFIGURED_PARSE = RESIDENT_PARSE.replace('mem:', 'maxmem:')

# The two re-reads that keep need_kb current as guests stop. Dropping them is the
# mutation the reachable case is measured against.
LOOP_REFRESH = (
    "        swap_used_kb=$(swap_used_now_kb)\n"
    "        need_kb=$(( swap_used_kb + margin_kb ))\n"
)
GATE_REFRESH = (
    "\n  swap_used_kb=$(swap_used_now_kb)\n  need_kb=$(( swap_used_kb + margin_kb ))\n"
)

# The live shape: 33 GB of swap in use, ARC-shrink headroom 9 GB short of it.
SWAP_TOTAL_KB = 34_000_000
SWAP_FREE_KB = 1_000_000
AVAIL_KB = 26_000_000
NEED_KB = SWAP_TOTAL_KB - SWAP_FREE_KB + MARGIN_KB

DRIVER = r"""
set -uo pipefail
# shellcheck disable=SC1091
W="__W__"
source "$W/swap-clean-lib.sh" || true
set +e

PROM_DIR="$W"
PROM_FILE="$W/swap_clean.prom"
arc_path="$W/absent/arc_max"
FSTAB="$W/fstab"
PROC_ROOT="$W/proc"

logger() { :; }
sleep() { :; }
systemctl() { return 1; }
swapoff() { echo "swapoff $*" >> "$W/sys.log"; }
swapon() { echo "swapon $*" >> "$W/sys.log"; }
field_kb() { cat "$W/meminfo.$1"; }

qm() {
  local vmid rss swp
  case "$1" in
    status)
      vmid="$2"
      echo "status: $(cat "$W/state.$vmid" 2>/dev/null || echo absent)"
      if [ "${3:-}" = "--verbose" ]; then
        if [ -f "$W/maxmem.$vmid" ]; then
          echo "maxmem: $(( $(cat "$W/maxmem.$vmid") * 1048576 ))"
        fi
        if [ -f "$W/mem.$vmid" ]; then
          echo "mem: $(( $(cat "$W/mem.$vmid") * 1024 ))"
        fi
        if [ -f "$W/pid.$vmid" ]; then
          echo "pid: $(cat "$W/pid.$vmid")"
        fi
      fi
      ;;
    config)
      cat "$W/config.$2" 2>/dev/null || return 1
      ;;
    shutdown)
      vmid="$2"
      echo "shutdown $vmid" >> "$W/qm.log"
      echo stopped > "$W/state.$vmid"
      rss="$(cat "$W/rss.$vmid")"
      swp="$(cat "$W/swap.$vmid")"
      echo $(( $(cat "$W/meminfo.MemAvailable") + rss )) > "$W/meminfo.MemAvailable"
      echo $(( $(cat "$W/meminfo.SwapFree") + swp )) > "$W/meminfo.SwapFree"
      ;;
    start)
      echo "start $2" >> "$W/qm.log"
      echo running > "$W/state.$2"
      ;;
  esac
}

main
"""


def _render(**overrides: object) -> str:
    context = {
        **{k: v for k, v in DEFAULTS.items() if isinstance(v, (str, int, bool))},
        "ansible_managed": "Ansible managed",
        "nas_storage_zfs_arc_max_bytes": 12884901888,
        "nas_storage_backup_artifact_metrics_dir": "/var/lib/node_exporter/textfile_collector",
        "nas_storage_swap_clean_stop_guests": GUESTS,
    }
    context.update(overrides)
    env = ansible_env(
        loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)), keep_trailing_newline=True
    )
    rendered = env.get_template(TEMPLATE.name).render(**context)
    assert "{{" not in rendered, "the rendered script still carries Jinja"
    return rendered


@pytest.fixture(scope="module")
def rendered() -> str:
    return _render()


def _library(work: Path, script: str) -> None:
    """The rendered script with its entrypoint disabled, so the driver calls main."""
    body = script.replace('main "$@"', "# entrypoint disabled for the driver")
    assert "# entrypoint disabled" in body, 'the entrypoint is no longer `main "$@"`'
    (work / "swap-clean-lib.sh").write_text(body, encoding="utf-8")


def _guest(work: Path, vmid: str, memory_mb: int, rss_kb: int, swap_kb: int) -> None:
    """One running candidate: configured memory, plus what stopping it releases.

    The live readings the estimate uses are the same two figures: `mem:` is the
    resident memory, and the process `VmSwap` the swap a stop hands back.
    """
    (work / ("state.%s" % vmid)).write_text("running\n", encoding="utf-8")
    (work / ("config.%s" % vmid)).write_text(
        "name: g%s\nmemory: %d\n" % (vmid, memory_mb), encoding="utf-8"
    )
    (work / ("maxmem.%s" % vmid)).write_text("%d\n" % memory_mb, encoding="utf-8")
    (work / ("mem.%s" % vmid)).write_text("%d\n" % rss_kb, encoding="utf-8")
    (work / ("rss.%s" % vmid)).write_text("%d\n" % rss_kb, encoding="utf-8")
    (work / ("swap.%s" % vmid)).write_text("%d\n" % swap_kb, encoding="utf-8")
    (work / ("pid.%s" % vmid)).write_text("%s\n" % vmid, encoding="utf-8")
    status = work / "proc" / vmid / "status"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        "Name:\tkvm\nVmRSS:\t%d kB\nVmSwap:\t%d kB\n" % (rss_kb, swap_kb),
        encoding="utf-8",
    )


def _meminfo(work: Path, avail_kb: int, swap_total_kb: int, swap_free_kb: int) -> None:
    (work / "meminfo.MemAvailable").write_text("%d\n" % avail_kb, encoding="utf-8")
    (work / "meminfo.SwapTotal").write_text("%d\n" % swap_total_kb, encoding="utf-8")
    (work / "meminfo.SwapFree").write_text("%d\n" % swap_free_kb, encoding="utf-8")


def _unreachable_host(work: Path) -> None:
    """Two candidates whose whole configured memory cannot cover the shortfall."""
    _meminfo(work, AVAIL_KB, SWAP_TOTAL_KB, SWAP_FREE_KB)
    _guest(work, "157", memory_mb=512, rss_kb=400_000, swap_kb=100_000)
    _guest(work, "153", memory_mb=512, rss_kb=400_000, swap_kb=100_000)


def _reachable_host(work: Path) -> None:
    """The first candidate alone releases most of the swap, so one stop suffices."""
    _meminfo(work, AVAIL_KB, SWAP_TOTAL_KB, SWAP_FREE_KB)
    _guest(work, "157", memory_mb=12288, rss_kb=3_000_000, swap_kb=25_000_000)
    _guest(work, "153", memory_mb=16384, rss_kb=1_000_000, swap_kb=2_000_000)


def _resident_host(work: Path) -> None:
    """One fully resident candidate: its RAM alone covers the target."""
    _meminfo(work, AVAIL_KB, SWAP_TOTAL_KB, SWAP_FREE_KB)
    _guest(work, "157", memory_mb=16384, rss_kb=10_000_000, swap_kb=0)
    _guest(work, "153", memory_mb=16384, rss_kb=1_000_000, swap_kb=0)


def _ballooned_host(work: Path) -> None:
    """Two 16 GB candidates barely resident.

    Their configured memory reaches the target twice over. What stopping them
    actually frees does not come close.
    """
    _meminfo(work, AVAIL_KB, SWAP_TOTAL_KB, SWAP_FREE_KB)
    _guest(work, "157", memory_mb=16384, rss_kb=500_000, swap_kb=100_000)
    _guest(work, "153", memory_mb=16384, rss_kb=500_000, swap_kb=100_000)


def _run(work: Path, script: str) -> subprocess.CompletedProcess:
    _library(work, script)
    (work / "fstab").write_text("", encoding="utf-8")
    driver = work / "driver.sh"
    driver.write_text(DRIVER.replace("__W__", str(work)), encoding="utf-8")
    return subprocess.run(
        ["env", "-i", "bash", str(driver)], capture_output=True, text=True, check=False
    )


def _probe(work: Path, body: str) -> str:
    """The driver with `main` replaced by helper calls, for a direct reading."""
    probe = work / "probe.sh"
    probe.write_text(
        DRIVER.replace("__W__", str(work)).replace("\nmain\n", "\n%s" % body),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["env", "-i", "bash", str(probe)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return proc.stdout


def _qm_calls(work: Path) -> list[str]:
    log = work / "qm.log"
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def _prom(work: Path) -> str:
    return (work / "swap_clean.prom").read_text(encoding="utf-8")


def test_the_render_carries_the_feasibility_gate(rendered) -> None:
    assert GATE_LINES in rendered, "the escalation no longer pre-checks attainable headroom"
    assert "skipping without stopping guests" in rendered
    assert 'skip_reason="escalation unreachable"' in rendered


def test_an_unreachable_goal_stops_no_guest(tmp_path, rendered) -> None:
    _unreachable_host(tmp_path)
    proc = _run(tmp_path, rendered)
    assert proc.returncode == 0, proc.stderr
    assert _qm_calls(tmp_path) == [], "the escalation stopped a guest for an unreachable goal"
    assert "escalation cannot reach %dKB" % NEED_KB in proc.stdout, proc.stdout
    assert "even after stopping all 2 candidates" in proc.stdout, proc.stdout
    assert not (tmp_path / "sys.log").exists(), "swap was cycled on an unreachable run"


def test_an_unreachable_goal_records_a_skipped_run(tmp_path, rendered) -> None:
    _unreachable_host(tmp_path)
    _run(tmp_path, rendered)
    prom = _prom(tmp_path)
    assert "swap_clean_last_run_skipped 1" in prom, prom
    assert 'swap_clean_skip_reason_info{reason="escalation unreachable"} 1' in prom, prom
    assert "swap_clean_last_run_success 0" in prom, prom
    assert "swap_clean_guests_stopped_count 0" in prom, prom


def test_dropping_the_gate_stops_guests_again(tmp_path, rendered) -> None:
    """The mutation: without the gate the same host loses both guests for nothing."""
    _unreachable_host(tmp_path)
    proc = _run(tmp_path, rendered.replace(GATE_LINES, "    if false; then\n"))
    assert proc.returncode == 0, proc.stderr
    assert _qm_calls(tmp_path).count("shutdown") == 2, proc.stdout
    assert "ABORT: MemAvailable" in proc.stdout, proc.stdout


def test_a_reachable_goal_stops_only_what_it_needs(tmp_path, rendered) -> None:
    """need_kb is re-read as guests stop, so the swap they release ends the loop."""
    _reachable_host(tmp_path)
    proc = _run(tmp_path, rendered)
    assert proc.returncode == 0, proc.stderr
    calls = _qm_calls(tmp_path)
    assert calls.count("shutdown") == 1, (calls, proc.stdout)
    assert "153" not in calls, "the second guest was stopped after the goal was met"
    assert calls.count("start") == 1, "the stopped guest was not restarted"
    assert "swapoff -a" in (tmp_path / "sys.log").read_text(encoding="utf-8")
    prom = _prom(tmp_path)
    assert "swap_clean_last_run_success 1" in prom, prom
    assert "swap_clean_guests_stopped_count 1" in prom, prom
    assert "swap_clean_last_run_skipped 0" in prom, prom


def test_a_stale_need_stops_every_candidate_and_aborts(tmp_path, rendered) -> None:
    """The mutation: measured against the pre-stop swap, the loop can never finish."""
    _reachable_host(tmp_path)
    stale = rendered.replace(LOOP_REFRESH, "").replace(GATE_REFRESH, "\n")
    assert LOOP_REFRESH not in stale and GATE_REFRESH not in stale, "the re-reads moved"
    proc = _run(tmp_path, stale)
    assert _qm_calls(tmp_path).count("shutdown") == 2, proc.stdout
    assert "ABORT: MemAvailable" in proc.stdout, proc.stdout
    assert "swap_clean_last_run_success 0" in _prom(tmp_path)


def test_a_resident_goal_proceeds_without_counting_swap(tmp_path, rendered) -> None:
    """A fully resident candidate covers the target on its RAM alone."""
    _resident_host(tmp_path)
    proc = _run(tmp_path, rendered)
    assert proc.returncode == 0, proc.stderr
    calls = _qm_calls(tmp_path)
    assert calls.count("shutdown") == 1, (calls, proc.stdout)
    assert "153" not in calls, "the second guest was stopped after the goal was met"
    assert "swapoff -a" in (tmp_path / "sys.log").read_text(encoding="utf-8")
    assert "swap_clean_last_run_success 1" in _prom(tmp_path)


def test_a_ballooned_candidate_cannot_promise_its_configured_memory(tmp_path, rendered) -> None:
    """The finding: 32 GB configured, barely resident, so the goal is unreachable."""
    _ballooned_host(tmp_path)
    proc = _run(tmp_path, rendered)
    assert proc.returncode == 0, proc.stderr
    assert _qm_calls(tmp_path) == [], "a ballooned guest was stopped for an unreachable goal"
    assert "escalation cannot reach %dKB" % NEED_KB in proc.stdout, proc.stdout
    assert not (tmp_path / "sys.log").exists(), "swap was cycled on an unreachable run"
    prom = _prom(tmp_path)
    assert 'swap_clean_skip_reason_info{reason="escalation unreachable"} 1' in prom, prom
    assert "swap_clean_guests_stopped_count 0" in prom, prom


def test_estimating_from_configured_memory_stops_ballooned_guests(tmp_path, rendered) -> None:
    """The mutation: counting maxmem makes the same host lose both guests."""
    _ballooned_host(tmp_path)
    assert RESIDENT_PARSE in rendered, "the estimate no longer reads a live `mem:`"
    proc = _run(tmp_path, rendered.replace(RESIDENT_PARSE, CONFIGURED_PARSE))
    assert proc.returncode == 0, proc.stderr
    assert _qm_calls(tmp_path).count("shutdown") == 2, proc.stdout
    assert "ABORT: MemAvailable" in proc.stdout, proc.stdout
    assert "swap_clean_last_run_success 0" in _prom(tmp_path)


def test_the_estimate_reads_the_live_figures(tmp_path, rendered) -> None:
    """Resident memory from `mem:`, swap from the guest process `VmSwap`."""
    _library(tmp_path, rendered)
    _guest(tmp_path, "157", memory_mb=16384, rss_kb=1_500_000, swap_kb=700_000)
    out = _probe(tmp_path, "guest_memory_kb 157\nguest_swap_kb 157\n")
    assert out.split() == ["1500000", "700000"], out


def test_a_guest_without_a_live_reading_counts_nothing(tmp_path, rendered) -> None:
    """No `mem:` and no pid means no promised headroom, configured memory or not."""
    _library(tmp_path, rendered)
    (tmp_path / "state.157").write_text("running\n", encoding="utf-8")
    (tmp_path / "maxmem.157").write_text("2048\n", encoding="utf-8")
    (tmp_path / "config.157").write_text("name: g157\nmemory: 2048\n", encoding="utf-8")
    out = _probe(
        tmp_path,
        "guest_memory_kb 157\nguest_swap_kb 157\nguest_memory_kb 999\nguest_swap_kb 999\n",
    )
    assert out.split() == ["0", "0", "0", "0"], out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
