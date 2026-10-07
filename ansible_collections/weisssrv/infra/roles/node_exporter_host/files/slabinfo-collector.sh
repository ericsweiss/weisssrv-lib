#!/bin/sh
# Managed by the node_exporter_host role. Publishes per-cache kernel slab usage
# from /proc/slabinfo for an allowlist of caches, writing .tmp then renaming so
# node_exporter never reads a half-written file. Metrics: README.

set -eu
export LC_ALL=C

OUT_DIR="${1:-/var/lib/node_exporter}"
[ $# -gt 0 ] && shift
OUT="$OUT_DIR/node_slabinfo.prom"
TMP="$OUT.tmp"
mkdir -p "$OUT_DIR"

# Allowlist: script arguments win, else SLAB_CACHES from the defaults file the
# role renders. An empty list emits the sentinel only, never every cache.
# SLABINFO_CONF is a test seam; the unit never sets it.
CONF="${SLABINFO_CONF:-/etc/default/slabinfo-collector}"
if [ $# -eq 0 ] && [ -r "$CONF" ]; then
    # shellcheck source=/dev/null
    . "$CONF"
    # shellcheck disable=SC2086
    set -- ${SLAB_CACHES:-}
fi

# SLABINFO is a test seam; the unit never sets it.
SLABINFO="${SLABINFO:-/proc/slabinfo}"

success=1
# Kernels built without CONFIG_SLUB_DEBUG, and every unprivileged container,
# have no readable slabinfo. Report the meta-failure rather than an absent
# file, which reads the same as a timer that never fired.
[ -r "$SLABINFO" ] || success=0

{
    printf '# HELP node_slab_objects Live objects in the slab cache (/proc/slabinfo active_objs).\n'
    printf '# TYPE node_slab_objects gauge\n'
    printf '# HELP node_slab_object_bytes Live bytes in the slab cache (active_objs * objsize).\n'
    printf '# TYPE node_slab_object_bytes gauge\n'
    printf '# HELP node_slab_pages Pages backing the slab cache (num_slabs * pagesperslab).\n'
    printf '# TYPE node_slab_pages gauge\n'
    printf '# HELP node_slab_cache_present 1 when the named cache exists in /proc/slabinfo.\n'
    printf '# TYPE node_slab_cache_present gauge\n'

    if [ "$success" -eq 1 ]; then
        for cache in "$@"; do
            [ -n "$cache" ] || continue
            # Columns: name active_objs num_objs objsize objperslab pagesperslab
            # : tunables ... : slabdata active_slabs num_slabs sharedavail, so
            # num_slabs is $(NF-1) on the full 16-column line.
            series="$(awk -v cache="$cache" '
                $1 == cache && NF >= 6 {
                    printf "node_slab_objects{cache=\"%s\"} %d\n", cache, $2
                    printf "node_slab_object_bytes{cache=\"%s\"} %d\n", cache, $2 * $4
                    if (NF >= 16)
                        printf "node_slab_pages{cache=\"%s\"} %d\n", cache, $(NF - 1) * $6
                    exit
                }' "$SLABINFO" || true)"
            # A typo or a renamed/merged cache emits no series at all, which
            # reads exactly like a leak that stopped.
            if [ -n "$series" ]; then
                printf '%s\n' "$series"
                printf 'node_slab_cache_present{cache="%s"} 1\n' "$cache"
            else
                printf 'node_slab_cache_present{cache="%s"} 0\n' "$cache"
            fi
        done
    fi

    printf '# HELP node_slabinfo_collector_success 1 when slabinfo was readable this run.\n'
    printf '# TYPE node_slabinfo_collector_success gauge\n'
    printf 'node_slabinfo_collector_success %d\n' "$success"
    if [ "$success" -eq 1 ]; then
        printf '# HELP node_slabinfo_collector_last_success_seconds Unix time the slabinfo collector last completed. Staleness means the collector itself is broken.\n'
        printf '# TYPE node_slabinfo_collector_last_success_seconds gauge\n'
        printf 'node_slabinfo_collector_last_success_seconds %s\n' "$(date +%s)"
    fi
} > "$TMP"

mv "$TMP" "$OUT"
