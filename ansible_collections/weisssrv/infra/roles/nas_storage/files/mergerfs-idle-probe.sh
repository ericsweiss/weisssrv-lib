#!/usr/bin/env bash
# Reports whether any process holds a MergerFS union, for the remount gate.
# Output and exit codes: the nas_storage README.
set -uo pipefail

union="${1:-}"
[ -n "$union" ] || { echo >&2 "usage: $0 <union mountpoint>"; exit 2; }

# Without fuser the probe cannot tell "nothing holds it" from "I could not
# look"; a missing binary would read as an idle union and unmount it.
command -v fuser >/dev/null 2>&1 || {
  echo "NO_FUSER: cannot determine whether $union is in use"
  exit 3
}
for tool in mountpoint findmnt; do
  command -v "$tool" >/dev/null 2>&1 || { echo "PROBE_ERROR: $tool is not installed"; exit 3; }
done

# mountpoint stats the path, which fails on a disconnected FUSE union;
# findmnt reads the mount table, so it tells a dead union from an absent one.
if ! mountpoint -q "$union" 2>/dev/null; then
  if findmnt -rn --mountpoint "$union" >/dev/null 2>&1; then
    echo "PROBE_ERROR: $union is mounted but not responding"
    exit 3
  fi
  echo "NOT_MOUNTED: $union"
  exit 4
fi

# fuser exits 1 both for "no holders" and for a failed stat, such as a wedged
# FUSE union; empty stderr or the bare "<path>:" header means it looked.
errfile="$(mktemp)" || { echo "PROBE_ERROR: cannot create a temporary file"; exit 3; }
trap 'rm -f "$errfile"' EXIT
holders="$(fuser -m "$union" 2>"$errfile")"
rc=$?
if [ -n "${holders//[[:space:]]/}" ]; then
  echo "IN_USE:"
  fuser -vm "$union" 2>&1 | head -20
  exit 1
fi

errors="$(tr -d '[:space:]' <"$errfile")"
if [ "$rc" -ne 0 ] && [ -n "$errors" ] && [ "$errors" != "${union//[[:space:]]/}:" ]; then
  echo "PROBE_ERROR: fuser failed on $union"
  head -5 "$errfile"
  exit 3
fi

echo "IDLE"
exit 0
