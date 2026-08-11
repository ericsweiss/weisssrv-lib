#!/usr/bin/env bash
# Behavioral check for the rendered zfs-load-key.sh: the verify.yml greps prove
# the code EXISTS, this executes it. The script is run against a stub 1Password
# Connect endpoint plus stub zpool/zfs binaries on PATH, asserting the exit-code
# taxonomy zfs-load-key@.service's RestartPreventExitStatus depends on, and that
# the fetched passphrase reaches `zfs load-key` on stdin.
set -euo pipefail

SRC="${1:-/usr/local/sbin/zfs-load-key.sh}"
PORT="${2:-18099}"
[ -x "$SRC" ] || { echo >&2 "zfs-load-key.sh not rendered at $SRC"; exit 1; }

# The work dir holds the copy of the script under test and the stub binaries it
# execs, so it has to live on a filesystem that permits exec. `mktemp -d` would
# land in /tmp, which this scenario mounts as a Docker tmpfs — noexec by
# default, so a 0755 copy there still fails with rc 126. Probe the candidates
# instead of hardcoding one, and say so loudly if none can exec.
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
WORK="$(mktemp -d "${WORK_ROOT}/zfs-load-key-behavior.XXXXXX")"
BIN="$WORK/bin"
MODE_FILE="$WORK/mode"
REQ_LOG="$WORK/requests.log"
AUTH_LOG="$WORK/auth.log"
CAPTURE="$WORK/loadkey-stdin"
PASSPHRASE='s3cret-pass phrase'
VAULT_ID='aaaaaaaaaaaaaaaaaaaaaaaaaa'

mkdir -p "$BIN"
cleanup() {
    if [ -n "${SERVER_PID:-}" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT
fail() { echo >&2 "FAIL: $*"; exit 1; }

# ---------------------------------------------------------------- stub Connect
# Response shape is driven by $WORK/mode so a single server covers every case.
cat > "$WORK/connect_stub.py" <<PYEOF
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

WORK = os.environ["STUB_WORK"]
VAULT = "${VAULT_ID}"
ITEM = "bbbbbbbbbbbbbbbbbbbbbbbbbb"
PASSPHRASE = os.environ["STUB_PASSPHRASE"]


def mode():
    with open(os.path.join(WORK, "mode")) as fh:
        return fh.read().strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with open(os.path.join(WORK, "requests.log"), "a") as fh:
            fh.write(self.path + "\n")
        with open(os.path.join(WORK, "auth.log"), "a") as fh:
            fh.write(self.headers.get("Authorization", "<none>") + "\n")

        m = mode()
        if m == "auth":
            return self.reply(401, {"message": "unauthorized"})
        if m == "down":
            return self.reply(503, {"message": "unavailable"})

        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if parts == ["v1", "vaults"]:
            return self.reply(200, [] if m == "novault" else [{"id": VAULT}])
        if len(parts) == 4 and parts[3] == "items":
            return self.reply(200, [] if m == "noitem" else [{"id": ITEM}])
        if len(parts) == 5 and parts[3] == "items":
            fields = [] if m == "nofield" else [
                {"id": "password", "label": "passphrase", "value": PASSPHRASE}
            ]
            return self.reply(200, {"id": ITEM, "fields": fields})
        return self.reply(404, {"message": "not found"})


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
PYEOF

echo ok > "$MODE_FILE"
STUB_WORK="$WORK" STUB_PASSPHRASE="$PASSPHRASE" python3 "$WORK/connect_stub.py" "$PORT" &
SERVER_PID=$!
for _ in $(seq 1 50); do
    if curl -sS -o /dev/null "http://127.0.0.1:${PORT}/v1/vaults" 2>/dev/null; then
        break
    fi
    sleep 0.2
done
curl -sS -o /dev/null "http://127.0.0.1:${PORT}/v1/vaults" \
    || fail "stub Connect never came up on ${PORT}"
: > "$REQ_LOG"
: > "$AUTH_LOG"

# ------------------------------------------------------------------ stub tools
cat > "$BIN/zpool" <<'EOF'
#!/bin/bash
# `zpool list -H -o name <pool>`: import state is driven by ZPOOL_STUB_RC.
exit "${ZPOOL_STUB_RC:-0}"
EOF

cat > "$BIN/zfs" <<'EOF'
#!/bin/bash
case "$1" in
  get)
    if [[ "$*" == *keystatus* ]]; then
      echo "${ZFS_STUB_KEYSTATUS:-unavailable}"
      exit 0
    fi
    if [[ "$*" == *encryptionroot* ]]; then
      if [ "${ZFS_STUB_ROOTS_RC:-0}" -ne 0 ]; then
        exit "${ZFS_STUB_ROOTS_RC}"
      fi
      printf '%s\t%s\n' testpool testpool
      exit 0
    fi
    exit 1
    ;;
  load-key)
    cat > "${ZFS_STUB_CAPTURE:-/dev/null}"
    exit "${ZFS_STUB_LOADKEY_RC:-0}"
    ;;
esac
exit 1
EOF

cat > "$BIN/logger" <<'EOF'
#!/bin/bash
echo "[logger] $*" >&2
EOF
chmod 0755 "$BIN"/*

# The rendered CONNECT_URL points at the real access point; retarget the copy
# under test at the stub. Everything else runs exactly as deployed.
sed "s|^CONNECT_URL=.*|CONNECT_URL=\"http://127.0.0.1:${PORT}\"|" "$SRC" > "$WORK/zfs-load-key.sh"
chmod 0755 "$WORK/zfs-load-key.sh"
grep -q "^CONNECT_URL=\"http://127.0.0.1:${PORT}\"$" "$WORK/zfs-load-key.sh" \
    || fail "could not retarget CONNECT_URL in the rendered script"

# run <expected-rc> <case-name> [env assignments...] — always with the pool arg.
run_case() {
    local want="$1" name="$2" rc=0
    shift 2
    env PATH="$BIN:$PATH" \
        ZFS_ENCRYPTION_ITEM="ZFS Pool testpool Passphrase" \
        ZFS_ENCRYPTION_FIELD=passphrase \
        ZFS_STUB_CAPTURE="$CAPTURE" \
        "$@" \
        "$WORK/zfs-load-key.sh" testpool >/dev/null 2>"$WORK/stderr" || rc=$?
    [ "$rc" = "$want" ] || {
        sed 's/^/    /' "$WORK/stderr" >&2
        fail "$name: expected exit $want, got $rc"
    }
}

# 1 — config error: no item title in env and none on argv.
rc=0
env PATH="$BIN:$PATH" ZFS_STUB_CAPTURE="$CAPTURE" \
    "$WORK/zfs-load-key.sh" testpool >/dev/null 2>&1 || rc=$?
[ "$rc" = 1 ] || fail "missing item title: expected exit 1, got $rc"

# 5 — pool never imported within the fetch timeout.
run_case 5 "pool not imported" ZPOOL_STUB_RC=1

# 0 — key already loaded short-circuits before any Connect call.
echo auth > "$MODE_FILE"          # would exit 7 if Connect were consulted
run_case 0 "key already loaded" ZFS_STUB_KEYSTATUS=available

# 7 — Connect rejects the token.
run_case 7 "Connect 401"

# The token must travel as a bearer header (curl -H @- on stdin, never argv).
grep -q '^Bearer fake-test-token-must-be-non-empty$' "$AUTH_LOG" \
    || fail "stub Connect did not receive the bearer token from the token file"

# 2 — Connect reachable but erroring for the whole fetch timeout.
echo down > "$MODE_FILE"
run_case 2 "Connect unreachable"

# 3 — vault/item/field lookups that resolve to nothing.
echo novault > "$MODE_FILE"
run_case 3 "vault not found"
echo noitem > "$MODE_FILE"
run_case 3 "item title not found"
echo nofield > "$MODE_FILE"
run_case 3 "passphrase field missing"

# 0 — happy path, and the fetched passphrase reaches `zfs load-key` on stdin.
echo ok > "$MODE_FILE"
: > "$CAPTURE"
run_case 0 "key loaded"
[ "$(cat "$CAPTURE")" = "$PASSPHRASE" ] \
    || fail "zfs load-key did not receive the passphrase from Connect"

# 4 — `zfs load-key` rejects the passphrase.
run_case 4 "zfs load-key failed" ZFS_STUB_LOADKEY_RC=1

# 5 — encryption-root enumeration fails (never report success on a locked pool).
run_case 5 "encryptionroot enumeration failed" ZFS_STUB_ROOTS_RC=1

# A 26-char vault id is used verbatim; only a vault NAME costs a lookup.
sed -i "s|^VAULT_REF=.*|VAULT_REF=\"${VAULT_ID}\"|" "$WORK/zfs-load-key.sh"
: > "$REQ_LOG"
run_case 0 "vault uuid skips the name lookup"
if grep -q '^/v1/vaults?' "$REQ_LOG"; then
    fail "a literal vault UUID must not trigger the name-resolution request"
fi

# The Connect response bodies carry the passphrase; nothing may survive in tmpfs.
if find /dev/shm -maxdepth 1 -name 'tmp.*' -print -quit | grep -q .; then
    fail "response temp files left behind in /dev/shm"
fi

echo "zfs-load-key behavior OK (exit codes 0/1/2/3/4/5/7, passphrase delivery, tmpfs hygiene)"
