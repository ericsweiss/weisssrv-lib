#!/usr/bin/env bash
# Guest-smoke helpers a consumer's per-guest verify-*.sh sources. Apart from the
# two counters, sourcing only loads shell-lib.sh from the same directory.
# Contract: weisssrv-lib docs/SCRIPTS.md - Shell helpers.

# shell-lib.sh supplies the ssh bound and its warning. A parameter expansion finds
# this file's directory, so sourcing runs no external tool.
_smoke_lib_dir="${BASH_SOURCE[0]%/*}"
[ "$_smoke_lib_dir" != "${BASH_SOURCE[0]}" ] || _smoke_lib_dir=.
# shellcheck source=scripts/shell-lib.sh
. "$_smoke_lib_dir/shell-lib.sh" || {
    echo "smoke-lib.sh: shell-lib.sh must sit in the same directory" >&2
    return 2
}

SMOKE_PASS=0
SMOKE_FAIL=0

# Echo the numeric HTTP status of a HEAD request, or 000 when nothing answered.
# Always returns 0: a bare pipeline here would kill an errexit caller.
http_status() {
    local status
    status=$(curl -sI --max-time "${SMOKE_LIB_CURL_MAX_TIME:-10}" "$1" 2>/dev/null | head -1 |
        grep -oE 'HTTP/[0-9.]+ [0-9]+' | grep -oE '[0-9]+$') || true
    printf '%s\n' "${status:-000}"
}

# 2xx or 3xx.
check_http_ok() {
    local status
    status=$(http_status "$1")
    [ "$status" -ge 200 ] && [ "$status" -lt 400 ]
}

# 401 counts: it proves a container registry is running without credentials.
check_registry_ok() {
    local status
    status=$(http_status "$1")
    [ "$status" = "401" ] || { [ "$status" -ge 200 ] && [ "$status" -lt 400 ]; }
}

# Anything below 500 that actually answered — a 404 means the service is up
# with no content published.
check_http_below_500() {
    local status
    status=$(http_status "$1")
    [ "$status" != "000" ] && [ "$status" -lt 500 ]
}

check_tcp_port() {
    nc -z -w 5 "$1" "$2" 2>/dev/null
}

# Bounded, non-interactive ssh: a stalled guest or a prompt fails the probe.
# SMOKE_LIB_SSH_MAX_TIME bounds the whole command when timeout or gtimeout exists.
_smoke_ssh() {
    timeout_cmd "${SMOKE_LIB_SSH_MAX_TIME:-30}" ssh -o ConnectTimeout=5 -o BatchMode=yes \
        -o ServerAliveInterval=5 -o ServerAliveCountMax=2 "$@"
}

# Needles are literal, unlike shell-lib.sh's BRE url_contains/ssh_contains. Each
# helper returns 0 on a match, 1 on none and 2 when the fetch, connection or
# command failed; a here-string read means no SIGPIPE can invert a match.
smoke_url_contains() {
    local out
    out=$(curl -s --max-time "${SMOKE_LIB_CURL_MAX_TIME:-10}" "$1" 2>/dev/null) || return 2
    grep -qF -- "$2" <<<"$out"
}

smoke_ssh_contains() {
    local out
    shell_lib_warn_unbounded
    out=$(_smoke_ssh "$1" "$2" 2>/dev/null) || return 2
    grep -qF -- "$3" <<<"$out"
}

# smoke_ssh_contains with a deliberate extended regex, for an anchored needle.
smoke_ssh_matches() {
    local out
    shell_lib_warn_unbounded
    out=$(_smoke_ssh "$1" "$2" 2>/dev/null) || return 2
    grep -qE -- "$3" <<<"$out"
}

smoke_check() {
    local label="$1"
    shift
    printf '%s... ' "$label"
    if "$@"; then
        echo "PASS"
        SMOKE_PASS=$((SMOKE_PASS + 1))
    else
        echo "FAIL"
        SMOKE_FAIL=$((SMOKE_FAIL + 1))
    fi
}

# A probe that may legitimately not be there yet: PASS counts, a miss prints the
# reason and counts as neither pass nor fail.
smoke_optional() {
    local label="$1" reason="$2"
    shift 2
    printf '%s... ' "$label"
    if "$@"; then
        echo "PASS"
        SMOKE_PASS=$((SMOKE_PASS + 1))
    else
        echo "SKIP ($reason)"
    fi
}

smoke_summary() {
    echo ""
    echo "=== Results: $SMOKE_PASS passed, $SMOKE_FAIL failed ==="
    [ "$SMOKE_FAIL" -eq 0 ]
}
