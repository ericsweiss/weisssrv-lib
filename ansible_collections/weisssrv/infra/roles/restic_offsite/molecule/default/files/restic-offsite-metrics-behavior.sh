#!/usr/bin/env bash
# Molecule behavioral check for the network-free helpers in the rendered
# restic-offsitectl: metric preservation, the freshness parser, the retention
# ceiling/parse guards, the deep-verify cursor and the stale-lock reaper. The
# contract-assert pins prove the code EXISTS; this executes it, driving restic
# through a stub. Runs on the target via ansible.builtin.script.
set -euo pipefail

s="${1:-/usr/local/sbin/restic-offsitectl}"
[ -f "$s" ] || { echo >&2 "restic-offsitectl not rendered at $s"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo >&2 "FAIL: $*"; exit 1; }

# Source the script with its entrypoint disabled so the functions are callable.
sed 's/^main "\$@"$/# main disabled for behavior test/' "$s" > "$WORK/lib.sh"
grep -q 'main disabled for behavior test' "$WORK/lib.sh" \
  || fail "could not disable restic-offsitectl entrypoint"
# shellcheck disable=SC1091
source "$WORK/lib.sh"

# Redirect the metric files into the temp dir.
PROM_FILE="$WORK/restic_offsite.prom"
VERIFY_PROM_FILE="$WORK/restic_offsite_verify.prom"

# --- run metrics: a failed run preserves the last-success timestamps ----------
printf 'restic_offsite_last_success_timestamp_seconds 1650000000\n' > "$PROM_FILE"
printf 'restic_offsite_last_backup_timestamp_seconds 1650000001\n' >> "$PROM_FILE"
write_prom_metrics 0 42 0 0
grep -qx 'restic_offsite_last_run_success 0' "$PROM_FILE" \
  || fail "failed run did not record success=0"
grep -qx 'restic_offsite_last_backup_success 0' "$PROM_FILE" \
  || fail "failed run did not record backup success=0"
grep -qx 'restic_offsite_last_success_timestamp_seconds 1650000000' "$PROM_FILE" \
  || fail "failed run did not preserve the last-success timestamp"
grep -qx 'restic_offsite_last_backup_timestamp_seconds 1650000001' "$PROM_FILE" \
  || fail "failed run did not preserve the last-backup timestamp"

# A success run writes a fresh timestamp + the size gauges.
write_prom_metrics 1 7 123 45
grep -qx 'restic_offsite_last_run_success 1' "$PROM_FILE" || fail "success not written"
grep -qx 'restic_offsite_repo_size_bytes 123' "$PROM_FILE" || fail "repo size gauge missing"
grep -qx 'restic_offsite_snapshot_total_bytes 45' "$PROM_FILE" || fail "snapshot size gauge missing"

# --- backup vs retention split ------------------------------------------------
# A blocked prune must not read as a failed upload, and a run that never reached
# the prune stage must not overwrite the last known retention state.
backup_success=1
prune_attempted=1
prune_success=0
retention_blocked=1
retention_pending=25
write_prom_metrics 1 9 1 1
grep -qx 'restic_offsite_last_backup_success 1' "$PROM_FILE" || fail "backup success not recorded"
grep -qx 'restic_offsite_last_prune_success 0' "$PROM_FILE" || fail "prune failure not recorded"
grep -qx 'restic_offsite_retention_blocked 1' "$PROM_FILE" || fail "retention block not recorded"
grep -qx 'restic_offsite_retention_pending_removals 25' "$PROM_FILE" || fail "pending count not recorded"
prune_attempted=0
write_prom_metrics 0 9 0 0
grep -qx 'restic_offsite_retention_pending_removals 25' "$PROM_FILE" \
  || fail "a run that never pruned overwrote the retention state"

# --- verify metrics + rotating deep-verify cursor ----------------------------
printf 'restic_offsite_last_verify_timestamp_seconds 1640000000\n' > "$VERIFY_PROM_FILE"
write_verify_metrics 0
grep -qx 'restic_offsite_last_verify_success 0' "$VERIFY_PROM_FILE" \
  || fail "verify failure did not record success=0"
grep -qx 'restic_offsite_last_verify_timestamp_seconds 1640000000' "$VERIFY_PROM_FILE" \
  || fail "verify failure did not preserve the last-verify timestamp"

rm -f "$VERIFY_PROM_FILE"
[ "$(next_verify_group)" = "1" ] || fail "the first deep verify must read group 1"
write_verify_metrics 1 1
[ "$(next_verify_group)" = "2" ] || fail "the cursor did not advance after a successful group"
write_verify_metrics 0 ""
[ "$(next_verify_group)" = "2" ] \
  || fail "a failed verify advanced the cursor — that group would be skipped for a full cycle"
write_verify_metrics 1 "$VERIFY_GROUPS"
[ "$(next_verify_group)" = "1" ] || fail "the cursor did not wrap to 1"

# --- freshness parser ---------------------------------------------------------
old_age="$(snap_age_seconds "${SNAP_PREFIX}-20200101-000000")"
[ "$old_age" -gt 100000000 ] || fail "old snapshot age not large ($old_age)"
now_name="${SNAP_PREFIX}-$(date -u +%Y%m%d-%H%M%S)"
new_age="$(snap_age_seconds "$now_name")"
[ "$new_age" -lt 3600 ] || fail "fresh snapshot age not small ($new_age)"
bad_age="$(snap_age_seconds not-a-snapshot)"
[ "$bad_age" = "999999999" ] || fail "unparseable name did not degrade to stale ($bad_age)"

# A source with NO snapshot at all must return empty, not kill the caller under
# set -e (the "stale-source: no snapshot" abort has to stay reachable).
mkdir -p "$WORK/empty-src/.zfs/snapshot"
missing_snap="$(latest_file_snap "$WORK/empty-src")"
[ -z "$missing_snap" ] || fail "latest_file_snap invented a snapshot ($missing_snap)"

# --- retention guard, driven through a stub restic ---------------------------
# PRUNE_RC drives the DESTRUCTIVE pass separately from the dry-run so the
# ceiling sentinel can be told apart from restic's own exit codes.
STUB_MODE=blocked
PRUNE_RC=0
restic() {
  case "$1 ${2:-}" in
    "forget --keep-last")
      # The destructive pass is the one carrying --prune.
      for a in "$@"; do
        if [ "$a" = "--prune" ]; then return "$PRUNE_RC"; fi
      done
      case "$STUB_MODE" in
        blocked) printf 'keep 8 snapshots:\nID\n\nremove 25 snapshots:\nID\n' ;;
        under)   printf 'keep 8 snapshots:\nID\n\nremove 1 snapshots:\nID\n' ;;
        noop)    printf 'keep 8 snapshots:\nID\n' ;;
        reworded) printf 'the retention report has been redesigned\n' ;;
        locked)  echo 'unable to create lock in backend' >&2; return 11 ;;
      esac
      ;;
    *) echo "stub restic $*" ;;
  esac
}

rc=0; run_forget >/dev/null || rc=$?
[ "$rc" = "90" ] || fail "a delete set above the ceiling must return the 90 sentinel (got $rc)"
[ "$rc" = "$FORGET_RC_CEILING" ] || fail "the ceiling sentinel drifted from FORGET_RC_CEILING"
[ "$retention_pending" = "25" ] || fail "pending removals not recorded ($retention_pending)"

STUB_MODE=under; rc=0; run_forget >/dev/null || rc=$?
[ "$rc" = "0" ] || fail "a delete set under the ceiling must prune (got $rc)"
[ "$retention_pending" = "1" ] || fail "pending removals not recorded ($retention_pending)"

STUB_MODE=noop; rc=0; run_forget >/dev/null || rc=$?
[ "$rc" = "0" ] || fail "a no-op forget must prune (got $rc)"
[ "$retention_pending" = "0" ] || fail "no-op forget should report 0 pending ($retention_pending)"

STUB_MODE=reworded; rc=0; out="$(run_forget)" || rc=$?
[ "$rc" = "1" ] || fail "an unparseable forget summary must refuse to prune (got $rc)"
printf '%s\n' "$out" | grep -q 'unrecognised restic forget summary' \
  || fail "the unparseable-summary refusal was not logged"

STUB_MODE=locked; rc=0; out="$(run_forget 2>/dev/null)" || rc=$?
[ "$rc" = "1" ] || fail "a failed dry-run must refuse to prune (got $rc)"
printf '%s\n' "$out" | grep -q 'rc=11 is a repository lock' \
  || fail "rc=11 was not reported as a repository lock"

# A `forget --prune` that CRASHES must surface as restic's own rc, not as the
# ceiling sentinel. rc 2 is restic's documented "go runtime error", which is why
# the sentinel is 90.
STUB_MODE=under; PRUNE_RC=2; rc=0; run_forget >/dev/null || rc=$?
[ "$rc" = "2" ] || fail "a crashed forget --prune must return restic's rc 2 (got $rc)"
PRUNE_RC=0

# --- run verdict + retention gauges per forget rc -----------------------------
# A crashed prune must not read as a deliberate refusal: blocked ONLY on the
# ceiling sentinel, and only the ceiling keeps the run green.
# Redirect to a file, never `$(...)`: a command substitution would run
# apply_forget_rc in a SUBSHELL and none of the gauges it sets would come back.
_reset_run_state() { prune_success=0; retention_blocked=0; run_success=0; retention_pending=7; }
VERDICT_LOG="$WORK/verdict.log"

_reset_run_state; apply_forget_rc 0 > "$VERDICT_LOG"
[ "$prune_success" = "1" ] || fail "a pruned run must record last_prune_success 1"
[ "$retention_blocked" = "0" ] || fail "a pruned run must not record retention_blocked"
[ "$run_success" = "1" ] || fail "a pruned run must succeed"

_reset_run_state; apply_forget_rc 90 > "$VERDICT_LOG"
[ "$prune_success" = "0" ] || fail "a ceiling refusal did not clear last_prune_success"
[ "$retention_blocked" = "1" ] || fail "a ceiling refusal must record retention_blocked 1"
[ "$run_success" = "1" ] || fail "a ceiling refusal must NOT fail the run — the backup landed"
grep -q 'retention blocked' "$VERDICT_LOG" || fail "the ceiling refusal was not logged as blocked"

_reset_run_state; apply_forget_rc 2 > "$VERDICT_LOG"
[ "$retention_blocked" = "0" ] \
  || fail "a CRASHED prune (restic rc 2) was recorded as a ceiling block — the operator would be told to raise the ceiling"
[ "$prune_success" = "0" ] || fail "a crashed prune must record last_prune_success 0"
[ "$run_success" = "0" ] || fail "a crashed prune must FAIL the run"
grep -q 'retention FAILED (rc=2)' "$VERDICT_LOG" \
  || fail "a crashed prune was not logged as a retention failure"

_reset_run_state; apply_forget_rc 1 > "$VERDICT_LOG"
[ "$retention_blocked" = "0" ] || fail "an unusable dry-run must not read as a ceiling block"
[ "$run_success" = "0" ] || fail "an unusable dry-run must fail the run"

# --- stale-lock reaper --------------------------------------------------------
# Same host + dead pid + old enough => unlock. Anything else must be left alone.
#
# The stub models the repository as ACTUALLY HELD EXCLUSIVELY, which is the only
# state the reaper exists for: any LOCKING restic call (the plain `restic`
# wrapper, which also carries --retry-lock) fails the way restic does, and only
# the --no-lock probes (`restic_ro`) can read the lock metadata. A reaper whose
# probes take a lock therefore reaps nothing here — the regression this pins.
DEAD_PID=4194302
if kill -0 "$DEAD_PID" 2>/dev/null; then fail "test pid $DEAD_PID is alive; pick another"; fi
LOCK_HOST="${HOSTNAME:-$(uname -n)}"
LOCK_PID="$DEAD_PID"
LOCK_TIME="$(date -d '48 hours ago' -Is)"
UNLOCKED="$WORK/unlocked"
_lock_meta() {
  printf '{\n  "time": "%s",\n  "exclusive": true,\n  "hostname": "%s",\n  "username": "root",\n  "pid": %s\n}\n' \
    "$LOCK_TIME" "$LOCK_HOST" "$LOCK_PID"
}
restic() {
  case "$1 ${2:-}" in
    # `unlock` removes stale locks and is the ONE locking call that must still
    # work while an exclusive lock is held.
    "unlock ") : > "$UNLOCKED"; echo "successfully removed 1 locks" ;;
    *)
      echo "repository is already locked exclusively by another process" >&2
      return 11
      ;;
  esac
}
restic_ro() {
  case "$1 ${2:-}" in
    "list locks") echo "deadbeef" ;;
    "cat lock") _lock_meta ;;
    "cat config") echo '{"version":2}' ;;
    *) echo "stub restic --no-lock $*" ;;
  esac
}
out="$(reap_stale_locks)"
[ -f "$UNLOCKED" ] || fail "a stale EXCLUSIVE same-host lock was not reaped: $out"
printf '%s\n' "$out" | grep -q 'stale-lock: deadbeef' || fail "the reaped lock was not logged"

# The same exclusive hold must not make repo_init_if_needed think the repository
# is uninitialised and re-run `restic init` (which fails and kills the run).
repo_init_if_needed > "$WORK/init.log" 2>&1
if grep -q 'not initialized' "$WORK/init.log"; then
  fail "an exclusive lock made repo_init_if_needed try to re-init the repository"
fi

rm -f "$UNLOCKED"; LOCK_PID="$$"
reap_stale_locks >/dev/null
if [ -f "$UNLOCKED" ]; then fail "a lock held by a LIVE process was reaped"; fi

rm -f "$UNLOCKED"; LOCK_PID="$DEAD_PID"; LOCK_HOST="some-other-host"
reap_stale_locks >/dev/null
if [ -f "$UNLOCKED" ]; then fail "another host's lock was reaped"; fi

rm -f "$UNLOCKED"; LOCK_HOST="${HOSTNAME:-$(uname -n)}"; LOCK_TIME="$(date -Is)"
reap_stale_locks >/dev/null
if [ -f "$UNLOCKED" ]; then fail "a fresh lock was reaped"; fi

echo "restic-offsite metric/freshness/retention/lock behavior OK"
