#!/usr/bin/env bash
# Finds the mounts that bind a MergerFS union by mount device id: findmnt's
# SOURCE column holds the union's fuse device string, so a name compare never
# matches. Usage and exit codes: the nas_storage README.
set -euo pipefail

mode="${1:-}"
union="${2:-}"
case "$mode" in
  list | check) ;;
  *) echo >&2 "usage: $0 <list|check> <union mountpoint>"; exit 2 ;;
esac
[ -n "$union" ] || { echo >&2 "usage: $0 <list|check> <union mountpoint>"; exit 2; }

# Without findmnt the probe cannot tell "nothing holds it" from "I could not
# look"; empty stdout would then read as a clean union.
command -v findmnt >/dev/null 2>&1 || {
  echo >&2 "$0: findmnt is not available; the union's binds cannot be resolved"
  exit 3
}

devid="$(findmnt --noheadings --first-only -o MAJ:MIN --mountpoint "$union" 2>/dev/null || true)"
devid="${devid//[[:space:]]/}"
if [ -z "$devid" ]; then
  [ "$mode" = check ] && echo "NOT_MOUNTED"
  exit 0
fi

all_mounts="$(findmnt --list --noheadings -o MAJ:MIN,TARGET 2>/dev/null || true)"
binds="$(printf '%s\n' "$all_mounts" \
  | awk -v d="$devid" -v m="$union" '$1 == d && $2 != m { print $2 }')"

if [ "$mode" = list ]; then
  [ -z "$binds" ] || printf '%s\n' "$binds"
  exit 0
fi

if [ -z "$binds" ]; then
  echo "NO_NFS_BIND_EXPORTS"
  exit 0
fi

echo "BIND_EXPORTS: $binds"

# Without ss the probe cannot tell "no NFS clients" from "I could not look".
command -v ss >/dev/null 2>&1 || {
  echo >&2 "$0: ss is not available; active NFS clients cannot be resolved"
  exit 3
}

rc=0
ss_out="$(ss -tn state established '( sport = :2049 )' 2>/dev/null)" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo >&2 "$0: ss failed (rc=$rc); active NFS clients cannot be resolved"
  exit 3
fi
nfs_connections="$(printf '%s\n' "$ss_out" | grep -v '^State' | head -5 || true)"
if [ -n "$nfs_connections" ]; then
  echo "ACTIVE_NFS_CLIENTS:"
  echo "$nfs_connections"
  exit 1
fi

echo "NFS_IDLE"
exit 0
