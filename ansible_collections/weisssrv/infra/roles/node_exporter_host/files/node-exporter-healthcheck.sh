#!/usr/bin/env bash
# Liveness gate for prometheus-node-exporter.
#
# It exists because the exporter can become a ZOMBIE (/proc/<pid>/status
# State: Z) with its listening socket still open and nothing accepting: systemd
# sees a live main PID, reports "active (running)" indefinitely, and no Restart=
# policy fires. A WatchdogSec cannot cover it either — the Debian unit is
# Type=simple and node_exporter does not sd_notify — so the only reliable signal
# is the one Prometheus itself uses: an HTTP GET of /metrics.
#
# Usage: node-exporter-healthcheck.sh [--probe-only] [PORT]
#   --probe-only  exit 0/1 on the probe result and never restart (tests)
set -uo pipefail

PROBE_ONLY=0
if [ "${1:-}" = "--probe-only" ]; then
    PROBE_ONLY=1
    shift
fi
PORT="${1:-9101}"
UNIT=prometheus-node-exporter
TEXTFILE_DIR="${NODE_EXPORTER_TEXTFILE_DIR:-/var/lib/node_exporter}"

# The full scrape runs every collector (a SMART collector walks every disk), so
# allow a generous timeout and a second attempt: a slow scrape must not be read
# as a dead exporter.
probe() {
    curl -fsS --max-time 20 -o /dev/null "http://127.0.0.1:${PORT}/metrics"
}

if probe; then
    exit 0
fi
sleep 5
if probe; then
    exit 0
fi

[ "$PROBE_ONLY" -eq 1 ] && exit 1

# Only act on a unit systemd still believes is up. If an operator stopped it
# deliberately, restarting here would fight them.
if ! systemctl is-active --quiet "$UNIT"; then
    exit 0
fi

logger -t node-exporter-healthcheck -p daemon.err \
    "/metrics on :${PORT} unanswered twice while ${UNIT} reports active — restarting"
systemctl restart "$UNIT"
rc=$?

# Leave a scrapable trace: the journal line above ships to the log stack, but
# this gauge makes a silent-restart loop visible in Prometheus/Grafana as well.
if [ -d "$TEXTFILE_DIR" ]; then
    tmp="${TEXTFILE_DIR}/node_exporter_healthcheck.prom.$$"
    {
        echo "# HELP node_exporter_healthcheck_last_restart_timestamp_seconds Unix time of the last healthcheck-triggered node_exporter restart."
        echo "# TYPE node_exporter_healthcheck_last_restart_timestamp_seconds gauge"
        echo "node_exporter_healthcheck_last_restart_timestamp_seconds $(date +%s)"
    } >"$tmp" && mv -f "$tmp" "${TEXTFILE_DIR}/node_exporter_healthcheck.prom"
fi

exit "$rc"
