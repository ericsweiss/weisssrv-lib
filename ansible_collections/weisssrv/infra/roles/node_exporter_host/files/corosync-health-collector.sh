#!/bin/sh
# Managed by Ansible node_exporter_host role.
#
# Writes Prometheus metrics to the node_exporter textfile collector so a
# CorosyncWedged / PmxcfsStale alert can catch a failure class host-up alerting
# misses: corosync alive but wedged at high CPU, with pmxcfs no longer
# replicating — a silent split-brain that can run for weeks. Metric table +
# companion alerts: README "Corosync + pmxcfs health collector".
#
# NO `|| true` on the top / stat calls, deliberately: a hung top would then
# return empty and the script would emit cpu=0 — the exact opposite of the
# signal CorosyncWedged looks for. Letting set -eu kill the script instead
# leaves the textfile untouched so the staleness alerts fire.
#
# Writes to .tmp then renames so node_exporter never reads a half-written file.

set -eu

# Force C locale so procps-ng top emits %CPU as "99.5" (period), not
# "99,5" (comma) under de_DE/fr_FR/etc. The normalisation below treats
# any non-[0-9.] character as a parse failure → cpu=0, which would
# silently mask a real wedge on any host where someone ran
# `dpkg-reconfigure locales` and changed LC_NUMERIC.
export LC_ALL=C

OUT_DIR="${1:-/var/lib/node_exporter}"
OUT="$OUT_DIR/corosync_health.prom"
TMP="$OUT.tmp"

# corosync CPU% via top -bn2. The first iteration is just an init pass
# (always reports 0.0% for every PID); the real measurement is iteration 2.
# Count occurrences of the "PID" header line so we know which sample we're
# reading and only emit on the second.
cpu=0
pid=""
# `set +e` around pidof + top to handle two transient cases without poisoning
# the textfile:
#   - corosync not running on a cluster host (host being rebuilt, etc.)
#   - corosync exited between pidof and top — a tiny race, but real
#   - top hit TimeoutStartSec=15s and was killed
# In any of these we want to emit cpu=0 rather than let set -eu kill the
# script and corrupt the metric. The CorosyncHealthCollectorStale meta-alert
# still catches whole-script failure via the last_success sentinel below.
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

# Normalize: an empty or non-numeric cpu would emit a malformed Prometheus
# line ("proxmox_corosync_cpu_percent " with a trailing space and no value),
# which node_exporter's textfile parser rejects — taking ALL three metrics
# in this file down with it, including the staleness sentinel. Force a
# numeric value here so a partial top failure degrades to cpu=0 instead.
case "$cpu" in
    ''|*[!0-9.]*) cpu=0 ;;
esac

# pmxcfs manager_status mtime. File is mode 0640 group www-data; this script
# runs as root (User=root in the .service), so stat works whenever the file
# exists. mtime=0 means the file doesn't exist (e.g. HA not configured on
# this node) — intentionally not a special-cased "no signal": PmxcfsStale
# fires on mtime=0 by design (time() - 0 >> 600), which catches both
# legitimate HA-disabled hosts (operator silences) and accidental/deliberate
# file deletes that would otherwise suppress the staleness signal.
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
