#!/bin/sh
# Managed by Ansible node_exporter_host role.
# Writes textfile metrics so CorosyncWedged / PmxcfsStale can catch corosync
# alive but wedged with pmxcfs no longer replicating. Metrics + alerts: README.

# A hung or killed top yields an unparseable sample; the guard below exits 1
# rather than publishing cpu=0, the opposite of what CorosyncWedged looks for.
# The textfile is written to .tmp and renamed, so no half-written read.

set -eu

# C locale so top prints %CPU as "99.5", not "99,5": a comma is an
# unparseable sample, which fails the run and stales the sentinel.
export LC_ALL=C

OUT_DIR="${1:-/var/lib/node_exporter}"
OUT="$OUT_DIR/corosync_health.prom"
TMP="$OUT.tmp"

# The role creates the dir, but an OUT_DIR override pointing elsewhere
# would otherwise kill the script before the sentinel is written.
mkdir -p "$OUT_DIR"

# corosync CPU% via top -bn2: iteration 1 is an init pass reporting 0.0% for
# every PID, so count "PID" header lines and read only the second sample.
cpu=0
pid=""
# set +e: corosync absent, exiting mid-sample, or a top timeout must emit
# cpu=0, not kill the run under set -eu.
set +e
pid=$(pidof corosync 2>/dev/null)
if [ -n "$pid" ]; then
    pid="${pid%% *}"
    # Two iterations, 1s interval; on procps-ng `top -b` the %CPU column is
    # column 9 (PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND).
    cpu=$(top -bn2 -p "$pid" -d 1 2>/dev/null \
        | awk -v p="$pid" '/^ *PID/{c++; next} c==2 && $1==p {print $9; exit}')
fi
set -e

# corosync running but unsampleable: keep the previous textfile and fail, so
# the sentinel goes stale and CorosyncHealthCollectorStale fires instead of
# publishing a healthy-looking cpu=0.
if [ -n "$pid" ]; then
    case "$cpu" in
        ''|*[!0-9.]*)
            echo "failed to sample corosync CPU for PID $pid" >&2
            rm -f "$TMP"
            exit 1
            ;;
    esac
fi

# pmxcfs manager_status mtime; the script runs as root, so stat always works.
# mtime=0 means the file is absent, and PmxcfsStale fires on it by design, so a
# deleted file cannot suppress the staleness signal.
mtime=0
if [ -e /etc/pve/ha/manager_status ]; then
    mtime=$(stat -c %Y /etc/pve/ha/manager_status)
fi

cat > "$TMP" <<EOF
# HELP proxmox_corosync_cpu_percent CPU% of the corosync process (procps-ng top -bn2 second sample). Sustained values near 100% across many minutes indicate a wedged corosync.
# TYPE proxmox_corosync_cpu_percent gauge
proxmox_corosync_cpu_percent ${cpu}
# HELP proxmox_pmxcfs_manager_status_mtime_seconds Unix mtime of /etc/pve/ha/manager_status as seen by this node. Comparing against time() detects pmxcfs split-brain (stale local view). 0 means the file does not exist on this host (e.g. HA disabled).
# TYPE proxmox_pmxcfs_manager_status_mtime_seconds gauge
proxmox_pmxcfs_manager_status_mtime_seconds ${mtime}
# HELP proxmox_corosync_health_collector_last_success_seconds Unix time the textfile collector last completed a successful sample. Staleness here means the collector itself (not corosync / pmxcfs) is broken — treat as a meta-failure.
# TYPE proxmox_corosync_health_collector_last_success_seconds gauge
proxmox_corosync_health_collector_last_success_seconds $(date +%s)
EOF

mv "$TMP" "$OUT"
