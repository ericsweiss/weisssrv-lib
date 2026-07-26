#!/usr/bin/env bash
# vzdump hookscript publishing Proxmox nightly-backup health metrics
# (vzdump_backup_last_run_success, _last_success_timestamp_seconds) to the
# node_exporter textfile collector. Wired via the jobs.cfg `script` property,
# but deployed to EVERY Proxmox host: a cluster-wide `all` job runs on each
# node for its local guests, so a node missing the hook aborts its own backups.
#
# Invoked as <script> <phase> [args...]. job-end fires even when individual
# guests failed, so a per-run marker records any backup-abort and downgrades
# the job-end verdict.
#
# A metrics-write failure must never abort the backup: always exits 0, no set -e.
set -uo pipefail

PHASE="${1:-}"
TEXTFILE_DIR="/var/lib/node_exporter"
PROM="${TEXTFILE_DIR}/vzdump_backup.prom"
# Per-run marker: its presence means at least one guest aborted this run. Keyed
# on vzdump's job id (falling back to the parent PID) so concurrent or repeated
# runs don't clobber each other's state.
FAIL_MARKER="/run/vzdump-metrics-hook.${VZDUMP_JOBID:-${PPID:-0}}.failed"

case "$PHASE" in
  backup-abort)
    : > "$FAIL_MARKER" 2>/dev/null || true
    exit 0
    ;;
  job-end)
    if [ -e "$FAIL_MARKER" ]; then SUCCESS=0; else SUCCESS=1; fi
    rm -f "$FAIL_MARKER" 2>/dev/null || true
    ;;
  job-abort)
    SUCCESS=0
    rm -f "$FAIL_MARKER" 2>/dev/null || true
    ;;
  *) exit 0 ;;
esac

now="$(date +%s)"

# Preserve the last *successful* run timestamp across a failed run: on failure
# reuse the existing value (0 if none yet); on success stamp now.
if [ "$SUCCESS" -eq 1 ]; then
  last_success="$now"
else
  last_success="$(sed -n 's/^vzdump_backup_last_success_timestamp_seconds \([0-9][0-9]*\)$/\1/p' "$PROM" 2>/dev/null || true)"
  last_success="${last_success:-0}"
fi

# Atomic write so node_exporter never scrapes a half-written file.
{
  tmp="$(mktemp "${PROM}.XXXXXX")" || exit 0
  cat > "$tmp" <<EOF
# HELP vzdump_backup_last_run_success Whether the last Proxmox vzdump job run backed up every guest (1) or had a failure (0).
# TYPE vzdump_backup_last_run_success gauge
vzdump_backup_last_run_success ${SUCCESS}
# HELP vzdump_backup_last_success_timestamp_seconds Unix time of the last fully successful Proxmox vzdump job run.
# TYPE vzdump_backup_last_success_timestamp_seconds gauge
vzdump_backup_last_success_timestamp_seconds ${last_success}
EOF
  chmod 0644 "$tmp"
  mv -f "$tmp" "$PROM"
} || true

exit 0
