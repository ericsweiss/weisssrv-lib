#!/usr/bin/env bash
# Driver a consolidated gate job sources: run_check runs one check, records a
# failure and carries on, so one red check never hides the rest.
# Contract: weisssrv-lib docs/SCRIPTS.md - ci-run-check.sh.

# Sourced, so no `set -e` here; the job sets `set -euo pipefail` itself and
# run_check restores errexit after every check.
overall=0
failed=""

run_check() {
    local name="$1"
    local rc=0
    shift
    printf 'section_start:%s:%s[collapsed=true]\r\033[0K== %s\n' "$(date +%s)" "$name" "$name"
    # CRITICAL: not `"$@" || rc=$?` — bash suppresses errexit throughout an
    # AND-OR list, so a check's own `set -e` never fires and a crashed helper
    # reads as a pass. The `set +e`/`set -e` pair is what lets one job report
    # every check instead of stopping at the first failure.
    set +e
    ( set -eo pipefail; "$@" )
    rc=$?
    set -e
    printf 'section_end:%s:%s\r\033[0K\n' "$(date +%s)" "$name"
    if [ "$rc" -ne 0 ]; then
        overall=1
        failed="$failed $name"
        echo "FAILED: $name (rc=$rc)"
    fi
}

# Name every failed check and return the job's exit status. Usage:
# `run_check_summary "sync checks"; exit $?` as the job's last script line.
run_check_summary() {
    [ "$overall" -eq 0 ] || echo "Failed ${1:-checks}:$failed"
    return "$overall"
}
