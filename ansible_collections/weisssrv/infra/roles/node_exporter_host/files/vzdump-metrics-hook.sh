#!/usr/bin/env bash
# vzdump hookscript publishing nightly-backup health metrics to the
# node_exporter textfile collector. Always exits 0, no set -e: a metrics-write
# failure must never abort a backup. See README "vzdump backup metrics".
set -uo pipefail

PHASE="${1:-}"
# vzdump owns $1 (the phase), so the textfile dir travels in a defaults file.
# shellcheck source=/dev/null
[ -r /etc/default/vzdump-metrics-hook ] && . /etc/default/vzdump-metrics-hook
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter}"
PROM="${TEXTFILE_DIR}/vzdump_backup.prom"
# Per-run markers: .failed means a guest aborted this run, .guests counts the
# guests started. Keyed on the vzdump job id so concurrent runs stay separate.
RUN_KEY="${VZDUMP_JOBID:-${PPID:-0}}"
FAIL_MARKER="/run/vzdump-metrics-hook.${RUN_KEY}.failed"
GUEST_MARKER="/run/vzdump-metrics-hook.${RUN_KEY}.guests"

case "$PHASE" in
  job-start)
    rm -f "$FAIL_MARKER" "$GUEST_MARKER" 2>/dev/null || true
    exit 0
    ;;
  backup-start)
    echo x >> "$GUEST_MARKER" 2>/dev/null || true
    exit 0
    ;;
  backup-abort)
    : > "$FAIL_MARKER" 2>/dev/null || true
    exit 0
    ;;
  job-end)
    if [ -e "$FAIL_MARKER" ]; then SUCCESS=0; else SUCCESS=1; fi
    ;;
  job-abort)
    SUCCESS=0
    ;;
  *) exit 0 ;;
esac

guests=0
if [ -r "$GUEST_MARKER" ]; then
  guests="$(wc -l < "$GUEST_MARKER" | tr -d '[:space:]')"
fi
rm -f "$FAIL_MARKER" "$GUEST_MARKER" 2>/dev/null || true

now="$(date +%s)"

# Preserve the last successful run timestamp across a failed run. With no
# previous value, emit no timestamp series at all: a 0 reads as "last success
# in 1970" to every staleness rule.
if [ "$SUCCESS" -eq 1 ]; then
  last_success="$now"
else
  last_success="$(sed -n 's/^vzdump_backup_last_success_timestamp_seconds \([0-9][0-9]*\)$/\1/p' "$PROM" 2>/dev/null || true)"
fi

# Atomic write so node_exporter never scrapes a half-written file.
{
  tmp="$(mktemp "${PROM}.XXXXXX")" || exit 0
  cat > "$tmp" <<EOF
# HELP vzdump_backup_last_run_success Whether the last Proxmox vzdump job run backed up every guest (1) or had a failure (0).
# TYPE vzdump_backup_last_run_success gauge
vzdump_backup_last_run_success ${SUCCESS}
# HELP vzdump_backup_guests Guests the last Proxmox vzdump job run on this node started backing up.
# TYPE vzdump_backup_guests gauge
vzdump_backup_guests ${guests}
EOF
  if [ -n "$last_success" ]; then
    cat >> "$tmp" <<EOF
# HELP vzdump_backup_last_success_timestamp_seconds Unix time of the last fully successful Proxmox vzdump job run.
# TYPE vzdump_backup_last_success_timestamp_seconds gauge
vzdump_backup_last_success_timestamp_seconds ${last_success}
EOF
  fi
  chmod 0644 "$tmp"
  mv -f "$tmp" "$PROM"
} || true

exit 0
