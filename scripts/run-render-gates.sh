#!/usr/bin/env bash
# Run a consumer's ordered list of corpus gates against a rendered manifest set.
# Config lines are eval'd, so the config file is repo-owned code.
# Contract, config format and exit codes: docs/SCRIPTS.md - run-render-gates.sh.

set -uo pipefail

CONFIG="scripts/render-gates.conf"
FLUX_ENV=""

while [ $# -gt 0 ]; do
    case "$1" in
        --config)
            [ $# -ge 2 ] || { echo "ERROR: --config needs a value" >&2; exit 2; }
            CONFIG="$2"; shift 2 ;;
        --flux-env)
            [ $# -ge 2 ] || { echo "ERROR: --flux-env needs a value" >&2; exit 2; }
            FLUX_ENV="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,4p' "$0"
            echo "Usage: run-render-gates.sh [--config FILE] [--flux-env FILE]; RENDER_ALL must be set."
            exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [ ! -f "$CONFIG" ]; then
    echo "ERROR: no gate list at $CONFIG" >&2
    exit 2
fi
if [ -z "${RENDER_ALL:-}" ] || [ ! -s "$RENDER_ALL" ]; then
    echo "ERROR: RENDER_ALL is unset or empty — a gate run over no corpus is not a gate" >&2
    exit 2
fi

MERGED_CM=""
cleanup() { [ -n "$MERGED_CM" ] && rm -f "$MERGED_CM"; }
trap cleanup EXIT

if [ -n "$FLUX_ENV" ]; then
    if [ -z "${VERSIONS_CONFIGMAP:-}" ]; then
        echo "ERROR: --flux-env needs VERSIONS_CONFIGMAP set" >&2
        exit 2
    fi
    MERGED_CM=$(mktemp)
    if ! bash "$FLUX_ENV" merged-configmap "$VERSIONS_CONFIGMAP" > "$MERGED_CM"; then
        echo "ERROR: could not build a merged configmap from $VERSIONS_CONFIGMAP" >&2
        exit 2
    fi
    if [ ! -s "$MERGED_CM" ]; then
        echo "ERROR: $FLUX_ENV produced an empty merged configmap from $VERSIONS_CONFIGMAP — a gate run over no substitutions is not a gate" >&2
        exit 2
    fi
fi
export MERGED_CM

worst=0
ran=0
# CRITICAL: the config is read on fd 3 so a `::!` gate cannot consume it as its
# own stdin and swallow the rest of the gate list.
while IFS= read -r line <&3; do
    case "$line" in
        ''|'#'*) continue ;;
    esac
    label="${line%%::*}"
    body="${line#*::}"
    no_stdin=0
    case "$body" in
        '!'*) no_stdin=1; body="${body#!}" ;;
    esac
    # Trim the surrounding whitespace the `label :: command` form leaves behind.
    label="$(printf '%s' "$label" | sed 's/[[:space:]]*$//')"
    body="$(printf '%s' "$body" | sed 's/^[[:space:]]*//')"
    if [ -z "$body" ]; then
        echo "ERROR: $CONFIG has a gate line with no command: $line" >&2
        exit 2
    fi
    ran=$((ran + 1))
    echo "=== ${label:-$body} ==="
    rc=0
    if [ "$no_stdin" -eq 1 ]; then
        eval "$body" || rc=$?
    else
        eval "$body" < "$RENDER_ALL" || rc=$?
    fi
    # A gate's rc 2 or more is an operator error, never a policy finding.
    if [ "$rc" -ge 2 ]; then
        echo "ERROR: ${label:-$body} exited $rc — operator error, not a finding" >&2
        worst=2
    elif [ "$rc" -ne 0 ] && [ "$worst" -eq 0 ]; then
        worst=1
    fi
done 3< "$CONFIG"

if [ "$ran" -eq 0 ]; then
    echo "ERROR: $CONFIG lists no gate — nothing was checked" >&2
    exit 2
fi

if [ "$worst" -eq 2 ]; then
    echo "render gates: a gate reported an OPERATOR ERROR ($ran gate(s) run)" >&2
    exit 2
fi

if [ "$worst" -ne 0 ]; then
    echo "render gates FAILED ($ran gate(s) run)" >&2
    exit 1
fi
echo "render gates OK ($ran gate(s))"
