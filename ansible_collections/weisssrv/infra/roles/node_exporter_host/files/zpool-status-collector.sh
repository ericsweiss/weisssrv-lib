#!/bin/sh
# Managed by Ansible node_exporter_host role.
# Writes per-pool ZFS health metrics to the node_exporter textfile collector,
# via .tmp and rename. Metrics, rationale and alerts: role README.

set -eu
export LC_ALL=C

OUT_DIR="${1:-/var/lib/node_exporter}"
OUT="$OUT_DIR/zfs_pool_status.prom"
TMP="$OUT.tmp"
# The role creates the dir, but an OUT_DIR override pointing elsewhere
# would otherwise kill the script before the sentinel is written.
mkdir -p "$OUT_DIR"

# 0 says nothing was measured. Without it a host whose pools all vanished emits
# a fresh sentinel and no per-pool series, which reads as healthy.
success=1

{
    printf '# HELP zfs_pool_status_health_code Pool health from zpool status: 0=ONLINE 1=DEGRADED 2=other (FAULTED/UNAVAIL/SUSPENDED/...).\n'
    printf '# TYPE zfs_pool_status_health_code gauge\n'
    printf '# HELP zfs_pool_status_errors_total Pool-level READ/WRITE/CKSUM error counters from zpool status. Non-zero with the pool still ONLINE is the silent-corruption signature.\n'
    printf '# TYPE zfs_pool_status_errors_total gauge\n'
    printf '# HELP zfs_pool_status_data_errors Number of entries in the zpool status -v permanent-error list.\n'
    printf '# TYPE zfs_pool_status_data_errors gauge\n'
    printf '# HELP zfs_pool_status_last_scrub_seconds Unix time the last scrub completed (0 = no completed scrub recorded).\n'
    printf '# TYPE zfs_pool_status_last_scrub_seconds gauge\n'
    printf '# HELP zfs_pool_status_allocated_bytes Allocated space in the pool in bytes (zpool list -Hp alloc).\n'
    printf '# TYPE zfs_pool_status_allocated_bytes gauge\n'
    printf '# HELP zfs_pool_status_size_bytes Total pool size in bytes (zpool list -Hp size).\n'
    printf '# TYPE zfs_pool_status_size_bytes gauge\n'

    if command -v zpool >/dev/null 2>&1; then
        set +e
        pools=$(zpool list -H -o name 2>/dev/null)
        pools_rc=$?
        set -e
        { [ $pools_rc -eq 0 ] && [ -n "$pools" ]; } || success=0
        # Intentional word-splitting of the pool-name list:
        # shellcheck disable=SC2086
        for pool in $pools; do
            # `set +e` per pool: a pool that disappears mid-loop (export,
            # device yank) must not kill the whole collector run.
            set +e
            status=$(zpool status -v "$pool" 2>/dev/null)
            rc=$?
            set -e
            [ $rc -ne 0 ] && continue

            health=$(printf '%s\n' "$status" | awk -F': *' '/^ *state:/{print $2; exit}')
            case "$health" in
                ONLINE)   code=0 ;;
                DEGRADED) code=1 ;;
                *)        code=2 ;;
            esac

            # Max over the config rows, not a sum: ZFS propagates an error to
            # both the leaf and its parent vdev, which a sum double-counts.
            # Counts can be suffixed (1.2K): keep the integer part.
            totals=$(printf '%s\n' "$status" | awk '
                /^config:/ { in_cfg=1; next }
                in_cfg && /^errors:/ { in_cfg=0 }
                in_cfg && /^[[:space:]]+/ && NF >= 5 {
                    # Positional, not NF-relative: zpool appends a note
                    # ("(resilvering)", "too many errors") after CKSUM on the
                    # rows that matter, and NF-relative reads skip them.
                    r=$3; w=$4; c=$5
                    if (r ~ /^[0-9]/ && w ~ /^[0-9]/ && c ~ /^[0-9]/) {
                        sub(/[^0-9].*$/, "", r); sub(/[^0-9].*$/, "", w); sub(/[^0-9].*$/, "", c)
                        if (r+0 > rs) rs = r+0
                        if (w+0 > ws) ws = w+0
                        if (c+0 > cs) cs = c+0
                    }
                }
                END { printf "%d %d %d\n", rs+0, ws+0, cs+0 }')
            # Positional assignment, not eval; $1 (OUT_DIR) was consumed at
            # the top. Intentional word-splitting of three integers:
            # shellcheck disable=SC2086
            set -- $totals
            read_e=${1:-0}; write_e=${2:-0}; cksum_e=${3:-0}

            # Permanent-error list length: indented dataset:<object> entries
            # after the "errors:" marker. Blank lines are neutral, since zpool
            # separates the marker from the entries with one.
            data_errors=$(printf '%s\n' "$status" | awk '
                /^errors: Permanent errors/ {f=1; next}
                f && /^[[:space:]]*$/ {next}
                f && /^[[:space:]]+[^[:space:]]/ {c++; next}
                f {f=0}
                END {print c+0}')

            # Last-scan completion: a finished scrub or resilver both count,
            # and a scan in progress counts as now, so ZFSPoolScrubStale fires
            # only when no scan has run for the alert window.
            scrub_ts=0
            scrub_date=$(printf '%s\n' "$status" | sed -n 's/.*\(scrub repaired\|resilvered\).* on \(.*\)$/\2/p' | head -1)
            if [ -n "$scrub_date" ]; then
                set +e
                scrub_ts=$(date -d "$scrub_date" +%s 2>/dev/null)
                set -e
                case "$scrub_ts" in ''|*[!0-9]*) scrub_ts=0 ;; esac
            elif printf '%s\n' "$status" | grep -Eq 'scrub in progress|resilver in progress'; then
                scrub_ts=$(date +%s)
            fi

            # Capacity gauges from `-Hp` raw bytes, so the ZFSPoolSpace ratio
            # is exact. Same `set +e` guard as above; a faulted pool printing
            # "-" falls back to 0 and is skipped at emit time.
            set +e
            cap=$(zpool list -Hpo alloc,size "$pool" 2>/dev/null)
            set -e
            # Intentional word-splitting of two integers:
            # shellcheck disable=SC2086
            set -- $cap
            alloc_bytes=${1:-0}; size_bytes=${2:-0}
            case "$alloc_bytes" in ''|*[!0-9]*) alloc_bytes=0 ;; esac
            case "$size_bytes" in ''|*[!0-9]*) size_bytes=0 ;; esac

            printf 'zfs_pool_status_health_code{pool="%s"} %d\n' "$pool" "$code"
            printf 'zfs_pool_status_errors_total{pool="%s",type="read"} %d\n' "$pool" "$read_e"
            printf 'zfs_pool_status_errors_total{pool="%s",type="write"} %d\n' "$pool" "$write_e"
            printf 'zfs_pool_status_errors_total{pool="%s",type="cksum"} %d\n' "$pool" "$cksum_e"
            printf 'zfs_pool_status_data_errors{pool="%s"} %d\n' "$pool" "$data_errors"
            printf 'zfs_pool_status_last_scrub_seconds{pool="%s"} %d\n' "$pool" "$scrub_ts"
            if [ "$size_bytes" -gt 0 ]; then
                printf 'zfs_pool_status_allocated_bytes{pool="%s"} %d\n' "$pool" "$alloc_bytes"
                printf 'zfs_pool_status_size_bytes{pool="%s"} %d\n' "$pool" "$size_bytes"
            fi
        done
    else
        success=0
    fi

    printf '# HELP zfs_pool_status_collector_success 1 when zpool was runnable and reported at least one pool.\n'
    printf '# TYPE zfs_pool_status_collector_success gauge\n'
    printf 'zfs_pool_status_collector_success %d\n' "$success"
    printf '# HELP zfs_pool_status_collector_last_success_seconds Unix time the zpool textfile collector last completed. Staleness means the collector itself is broken — treat as a meta-failure.\n'
    printf '# TYPE zfs_pool_status_collector_last_success_seconds gauge\n'
    printf 'zfs_pool_status_collector_last_success_seconds %s\n' "$(date +%s)"
} > "$TMP"

mv "$TMP" "$OUT"
