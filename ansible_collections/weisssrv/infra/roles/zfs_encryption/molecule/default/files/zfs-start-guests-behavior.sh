#!/usr/bin/env bash
# Behavioral check for the rendered zfs-start-encrypted-guests.sh. Grepping the
# text cannot tell an inverted guard from a correct one, so each arm is run
# against stub systemctl/mountpoint/udevadm/qm/pct binaries.
set -euo pipefail

SRC="${1:-/usr/local/sbin/zfs-start-encrypted-guests.sh}"
[ -x "$SRC" ] || { echo >&2 "guest-start script not rendered at $SRC"; exit 1; }

# /tmp is a noexec tmpfs in this scenario, so the stub bin dir needs a root
# that can exec.
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
WORK="$(mktemp -d "${WORK_ROOT}/zfs-start-guests-behavior.XXXXXX")"
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
stub udevadm <<'EOF'
#!/bin/sh
exit 0
EOF
stub systemctl <<'EOF'
#!/bin/sh
[ "$(cat "$STUB_WORK/mount-unit")" = active ]
EOF
stub mountpoint <<'EOF'
#!/bin/sh
[ "$(cat "$STUB_WORK/pmxcfs")" = mounted ]
EOF
stub qm <<'EOF'
#!/bin/sh
echo "qm $*" >> "$STUB_WORK/qm.log"
case "$1" in
  status) echo "status: stopped" ;;
  start) exit "$(cat "$STUB_WORK/qm-start-rc")" ;;
esac
exit 0
EOF
stub pct <<'EOF'
#!/bin/sh
echo "pct $*" >> "$STUB_WORK/qm.log"
exit 0
EOF

export STUB_WORK="$WORK"
echo active > "$WORK/mount-unit"
echo mounted > "$WORK/pmxcfs"
echo 0 > "$WORK/qm-start-rc"
: > "$WORK/qm.log"

run() {
    : > "$WORK/qm.log"
    set +e
    PATH="$BIN:$PATH" "$SRC" >/dev/null 2>&1
    RC=$?
    set -e
}

# The rendered cohort drives which guest ids the arms below expect.
VMID="$(sed -n 's/^VMIDS=(\([0-9]*\).*/\1/p' "$SRC" | head -1)"
[ -n "$VMID" ] || fail "the rendered script declares no VMIDS to drive"

# Arm 1: the mount anchor is not active — nothing may start, and the unit must
# retry rather than report a cold pool as started.
echo inactive > "$WORK/mount-unit"
run
[ "$RC" -eq 1 ] || fail "an inactive zfs-mount-encrypted.service gave rc $RC, want 1"
grep -q "qm start" "$WORK/qm.log" && fail "a guest was started before the mount anchor was active"
echo active > "$WORK/mount-unit"

# Arm 2: pmxcfs not mounted — every guest would read as unprovisioned.
echo unmounted > "$WORK/pmxcfs"
run
[ "$RC" -eq 1 ] || fail "an unmounted /etc/pve gave rc $RC, want 1"
grep -q "qm start" "$WORK/qm.log" && fail "a guest was started with /etc/pve unmounted"
echo mounted > "$WORK/pmxcfs"

# Arm 3: no config file — the guest is skipped and the run still succeeds.
rm -f "/etc/pve/qemu-server/${VMID}.conf"
run
[ "$RC" -eq 0 ] || fail "an unprovisioned guest gave rc $RC, want 0"
grep -q "qm start" "$WORK/qm.log" && fail "an unprovisioned guest was started"

# Arm 4: config present and the guest stopped — it must be started.
mkdir -p /etc/pve/qemu-server
printf 'name: molecule\n' > "/etc/pve/qemu-server/${VMID}.conf"
run
[ "$RC" -eq 0 ] || fail "a provisioned stopped guest gave rc $RC, want 0"
grep -qx "qm start ${VMID}" "$WORK/qm.log" || fail "a provisioned stopped guest was not started"

# Arm 5: a failing start keeps the unit retrying.
echo 1 > "$WORK/qm-start-rc"
run
[ "$RC" -eq 1 ] || fail "a failed qm start gave rc $RC, want 1"
echo 0 > "$WORK/qm-start-rc"
rm -f "/etc/pve/qemu-server/${VMID}.conf"

echo "zfs-start-encrypted-guests behavior OK"
