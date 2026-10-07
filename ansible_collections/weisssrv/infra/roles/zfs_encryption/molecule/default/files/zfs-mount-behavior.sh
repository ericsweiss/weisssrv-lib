#!/usr/bin/env bash
# Behavioral check for the rendered zfs-mount-encrypted.sh. The 0/1/2 exit
# taxonomy is what keeps the unit `activating` in its until-loop, so each arm is
# run against stub zpool/zfs binaries rather than grepped for.
set -euo pipefail

SRC="${1:-/usr/local/sbin/zfs-mount-encrypted.sh}"
POOL="${2:-testpool}"
[ -x "$SRC" ] || { echo >&2 "mount script not rendered at $SRC"; exit 1; }

work_root() {
    local candidate probe
    for candidate in /var/tmp /root "${TMPDIR:-/tmp}"; do
        [ -d "$candidate" ] || continue
        probe="$(mktemp "${candidate}/execprobe.XXXXXX" 2>/dev/null)" || continue
        printf '#!/bin/sh\nexit 0\n' > "$probe"
        chmod 0755 "$probe"
        if "$probe" >/dev/null 2>&1; then
            rm -f "$probe"
            printf '%s' "$candidate"
            return 0
        fi
        rm -f "$probe"
    done
    return 1
}
WORK_ROOT="$(work_root)" || {
    echo >&2 "no exec-capable temp directory (every candidate is mounted noexec)"
    exit 1
}
WORK="$(mktemp -d "${WORK_ROOT}/zfs-mount-behavior.XXXXXX")"
BIN="$WORK/bin"
mkdir -p "$BIN"
trap 'rm -rf "$WORK"' EXIT
fail() { echo >&2 "FAIL: $*"; exit 1; }

stub() {
    cat > "$BIN/$1"
    chmod 0755 "$BIN/$1"
}

stub logger <<'EOF'
#!/bin/sh
exit 0
EOF
stub zpool <<'EOF'
#!/bin/sh
[ "$(cat "$STUB_WORK/pool")" = imported ]
EOF
stub zfs <<'EOF'
#!/bin/sh
case "$1 $2" in
  "get -H")
    case "$*" in
      *encryptionroot*) printf '%s\t%s\n' "$STUB_POOL" "$STUB_POOL" ;;
      *keystatus*) cat "$STUB_WORK/keystatus" ;;
    esac
    exit 0 ;;
  "mount -a") exit "$(cat "$STUB_WORK/mount-rc")" ;;
esac
exit 0
EOF

export STUB_WORK="$WORK" STUB_POOL="$POOL"
echo imported > "$WORK/pool"
echo available > "$WORK/keystatus"
echo 0 > "$WORK/mount-rc"

run() {
    set +e
    PATH="$BIN:$PATH" "$SRC" "$POOL" >/dev/null 2>&1
    RC=$?
    set -e
}

# Arm 1: every key loaded and the mount succeeds.
run
[ "$RC" -eq 0 ] || fail "a fully mounted pool gave rc $RC, want 0"

# Arm 2: a locked encryption root keeps the unit activating.
echo unavailable > "$WORK/keystatus"
run
[ "$RC" -eq 1 ] || fail "a locked encryption root gave rc $RC, want 1"
echo available > "$WORK/keystatus"

# Arm 3: a pool that is not imported yet is the same retry, not a hard failure.
echo pending > "$WORK/pool"
run
[ "$RC" -eq 1 ] || fail "an unimported pool gave rc $RC, want 1"
echo imported > "$WORK/pool"

# Arm 4: `zfs mount -a` failing is the distinct exit 2.
echo 1 > "$WORK/mount-rc"
run
[ "$RC" -eq 2 ] || fail "a failed zfs mount -a gave rc $RC, want 2"

echo "zfs-mount-encrypted behavior OK"
