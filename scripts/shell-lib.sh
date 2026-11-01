#!/usr/bin/env bash
# Shell helpers other scripts source. Function-only: no top-level side effects,
# so sourcing is safe under a caller's `set -e`.
# Contract: weisssrv-lib docs/SCRIPTS.md - Shell helpers.

# Warn once per shell when neither timeout(1) nor gtimeout(1) exists. A helper
# that captures a probe calls it before the capture, so the warning reaches
# stderr and the guard persists in the caller's shell.
shell_lib_warn_unbounded() {
    if command -v timeout >/dev/null 2>&1 || command -v gtimeout >/dev/null 2>&1; then
        return 0
    fi
    if [ -z "${_SHELL_LIB_TIMEOUT_WARNED:-}" ]; then
        echo "warning: neither timeout(1) nor gtimeout(1) found — probes run UNBOUNDED (macOS: brew install coreutils)" >&2
        _SHELL_LIB_TIMEOUT_WARNED=1
    fi
}

# Run "$@" under a hard wall-clock bound (first arg = seconds). Prefers
# `timeout`, then `gtimeout`; with neither present the command runs unbounded,
# guarded only by the caller's own ssh timeouts.
timeout_cmd() {
    local seconds="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$seconds" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$seconds" "$@"
    else
        shell_lib_warn_unbounded
        "$@"
    fi
}

# SSH reachability probe under a wall-clock backstop. ConnectTimeout bounds the
# TCP connect, ServerAlive* trips a dead channel, timeout_cmd catches a host
# that connects then stalls. Usage: ssh_probe "$host" "true".
ssh_probe() {
    timeout_cmd 6 ssh -o ConnectTimeout=2 -o BatchMode=yes \
        -o ServerAliveInterval=2 -o ServerAliveCountMax=2 "$@"
}

# Read one object as JSON: 0 with the JSON on stdout, 3 when the server says
# NotFound, 1 with kubectl's stderr for any other failure. Only stdout is JSON.
# Usage: json="$(kubectl_read secret x -n ns)" || rc=$?
kubectl_read() {
    local out err errf rc=0
    errf="$(mktemp)" || { echo "kubectl_read: cannot create a temporary file" >&2; return 1; }
    out="$(kubectl get "$@" -o json 2>"$errf")" || rc=$?
    err="$(cat "$errf")"
    rm -f "$errf"
    if [ "$rc" -eq 0 ]; then
        [ -z "$err" ] || printf '%s\n' "$err" >&2
        printf '%s' "$out"
        return 0
    fi
    case "$err" in
        *"(NotFound)"*) return 3 ;;
    esac
    printf '%s\n' "$err" >&2
    return 1
}

# Test a captured value against a BRE: 0 on a match, 1 on none. A here-string
# has no writer process, so the `cmd | grep -q` SIGPIPE under pipefail cannot
# turn an early match in a large value into 141.
captured_match() {
    grep -q -- "$2" <<<"$1"
}

# url_contains <url> <pattern>: fetch a URL and test its body against a BRE.
# 0 on a match, 1 on none, 2 when the fetch failed, so an unreachable endpoint
# never reads as a body without the pattern. SHELL_LIB_CURL_MAX_TIME bounds it.
url_contains() {
    local out
    out=$(curl -s --max-time "${SHELL_LIB_CURL_MAX_TIME:-10}" "$1" 2>/dev/null) || return 2
    captured_match "$out" "$2"
}

# ssh_contains <user@host> <command> <pattern>: run a command through ssh_probe
# and test its output against a BRE. 0 on a match, 1 on none, 2 when the
# command or the connection failed.
ssh_contains() {
    local out
    shell_lib_warn_unbounded
    out=$(ssh_probe "$1" "$2" 2>/dev/null) || return 2
    captured_match "$out" "$3"
}
