#!/usr/bin/env bash
# Verifies a MergerFS union: mounted, carrying its control file, its fstab line
# holding every required option, and each named bind on the union's device.
# Usage, output and exit codes: the nas_storage README.
set -uo pipefail

usage="usage: $0 <union mountpoint> <required options> [bind ...]"
[ "$#" -ge 2 ] && [ -n "$1" ] || { echo >&2 "$usage"; exit 2; }
union="$1"
required="$2"
shift 2
fstab="${FSTAB:-/etc/fstab}"

mountpoint -q "$union" 2>/dev/null || { echo "STATUS: NOT_MOUNTED"; exit 1; }
[ -e "$union/.mergerfs" ] || {
  echo "STATUS: NOT_MERGERFS - mount exists but no .mergerfs control file"
  exit 1
}

# Field compare, not a regex: a target with metacharacters or a shared prefix
# must not match another union's line.
fstab_opts="$(awk -v t="$union" '$1 !~ /^#/ && $2 == t { print $4 }' "$fstab")"
[ -n "$fstab_opts" ] || { echo "STATUS: NO_FSTAB_ENTRY"; exit 1; }
echo "FSTAB_OPTS: $fstab_opts"

# Whole comma-separated tokens: a substring test reads "ro" inside "remount-ro".
missing=""
IFS=$', \t\n' read -r -a wanted <<<"$required"
for opt in ${wanted[@]+"${wanted[@]}"}; do
  case ",${fstab_opts//$'\n'/,}," in
    *",$opt,"*) ;;
    *) missing="${missing}${opt} " ;;
  esac
done
if [ -n "$missing" ]; then
  echo "STATUS: FSTAB_MISSING_OPTIONS - ${missing% }"
  exit 1
fi

if [ "$#" -gt 0 ]; then
  union_dev="$(findmnt --noheadings --first-only -o MAJ:MIN --mountpoint "$union" 2>/dev/null)"
  for bind in "$@"; do
    bind_dev="$(findmnt --noheadings --first-only -o MAJ:MIN --mountpoint "$bind" 2>/dev/null || true)"
    if [ -z "$union_dev" ] || [ "$bind_dev" != "$union_dev" ]; then
      echo "STATUS: BIND_NOT_ON_UNION - $bind is not on $union (${bind_dev:-unmounted})"
      exit 1
    fi
  done
fi

echo "STATUS: OK - MergerFS mounted with correct fstab options"
exit 0
