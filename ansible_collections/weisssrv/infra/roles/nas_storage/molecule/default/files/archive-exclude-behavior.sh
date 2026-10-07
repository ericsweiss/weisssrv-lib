#!/usr/bin/env bash
# Molecule behavioural check for the archive per-child exclusion seam: the
# rendered EXCLUDE_LIST, the per-source `-X` derivation, and the fail-loud probe
# for a zfs without `send -X`. Runs on the target via ansible.builtin.script.
set -euo pipefail

EXCLUDED="${1:?path to an archive-backupctl rendered WITH exclusions}"
PLAIN="${2:-/usr/local/sbin/archive-backupctl}"
OPTED="${3:?path to an archive-backupctl rendered with exclusions AND the destroy opt-in}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo >&2 "FAIL: $*"; exit 1; }

for script in "$EXCLUDED" "$PLAIN" "$OPTED"; do
  [ -f "$script" ] || fail "not rendered: $script"
  bash -n "$script" || fail "syntax error in $script"
done

# The render carries the configured children, and the default render carries none.
grep -qF '"tank/share/scratch"' "$EXCLUDED" || fail "tank/share/scratch missing from EXCLUDE_LIST"
grep -qF '"ssd/appdata/cache"' "$EXCLUDED" || fail "ssd/appdata/cache missing from EXCLUDE_LIST"
if awk '/^EXCLUDE_LIST=\(/{f=1; next} f && /^\)/{f=0} f' "$PLAIN" | grep -q '"'; then
  fail "the default render must exclude nothing"
fi

# Every recursive send must consume the derived flags, or the seam is dead code.
[ "$(grep -c 'zfs send -R "${raw_flags\[@\]}" "${EXCLUDE_FLAGS\[@\]}"' "$EXCLUDED")" -eq 3 ] \
  || fail "a recursive send does not pass EXCLUDE_FLAGS"

# The destroy opt-in renders from the variable, and is off by default.
grep -qx 'EXCLUDE_DESTROY_OK=false' "$EXCLUDED" || fail "the destroy opt-in must render false by default"
grep -qx 'EXCLUDE_DESTROY_OK=false' "$PLAIN" || fail "the default render must not opt in to destruction"
grep -qx 'EXCLUDE_DESTROY_OK=true' "$OPTED" || fail "the opt-in render must carry EXCLUDE_DESTROY_OK=true"

for script in "$EXCLUDED" "$PLAIN" "$OPTED"; do
  sed 's/^main "\$@"$/# main disabled for behavior test/' "$script" \
    > "$WORK/$(basename "$script").lib"
  grep -q 'main disabled for behavior test' "$WORK/$(basename "$script").lib" \
    || fail "could not disable the entrypoint of $script"
done

# Exclusions are scoped per source: `zfs send -X` rejects a dataset that is not
# a descendant of the one being sent.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  exclude_flags_for tank/share
  [ "${EXCLUDE_FLAGS[*]}" = "-X tank/share/scratch" ] || exit 31
  exclude_flags_for ssd/appdata
  [ "${EXCLUDE_FLAGS[*]}" = "-X ssd/appdata/cache" ] || exit 32
  exclude_flags_for tank/backups
  [ "${#EXCLUDE_FLAGS[@]}" -eq 0 ] || exit 33
  is_excluded tank/share/scratch || exit 34
  is_excluded tank/share/scratch/nested || exit 35
  is_excluded tank/share/scratchpad && exit 36
  is_excluded tank/share && exit 37
  exit 0
' || fail "per-source -X derivation (exit $?)"

# A zfs without `send -X` must fail the run, never replicate the excluded child.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() { echo "usage: zfs send [-R [-X dataset[,dataset]...]] snapshot" >&2; return 2; }
  assert_send_exclude_supported || exit 41
  zfs() { echo "usage: zfs send [-DLPbcehnpvw] [-i snapshot] snapshot" >&2; return 2; }
  assert_send_exclude_supported && exit 42
  exit 0
' || fail "send -X capability probe (exit $?)"

# With nothing excluded the probe must not care what zfs supports.
env -i bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$PLAIN")"'.lib" || true
  set +e
  zfs() { echo "usage: zfs send [-i snapshot] snapshot" >&2; return 2; }
  assert_send_exclude_supported || exit 43
  exit 0
' || fail "empty exclusion list must not require send -X (exit $?)"

# An exclusion whose archive-side twin already exists refuses the run: receive
# -F would destroy that copy and its snapshot history.
GUARD_OUT="$WORK/guard.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() { return 0; }
  assert_exclude_destroy_opt_in
  echo "rc=$?"
' >"$GUARD_OUT" 2>&1
grep -q '^rc=1$' "$GUARD_OUT" || fail "an existing excluded twin must refuse the run: $(cat "$GUARD_OUT")"
grep -q 'FATAL:.*excluded from this send' "$GUARD_OUT" || fail "the refusal must name the dataset it protects"
grep -q 'nas_storage_archive_backup_exclude_destroy_ok' "$GUARD_OUT" \
  || fail "the refusal must name the opt-in variable"
if grep -q 'WARNING' "$GUARD_OUT"; then
  fail "a refusing run must not also log the destroy warning"
fi

# Nothing on the archive pool yet: the guard has nothing to protect.
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() { return 1; }
  assert_exclude_destroy_opt_in || exit 51
  exit 0
' || fail "an absent excluded twin must not block the run (exit $?)"

# With the opt-in the run proceeds, warning for each copy it destroys.
OPTED_OUT="$WORK/opted.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$OPTED")"'.lib" || true
  set +e
  zfs() { return 0; }
  assert_exclude_destroy_opt_in
  echo "rc=$?"
' >"$OPTED_OUT" 2>&1
grep -q '^rc=0$' "$OPTED_OUT" || fail "the opt-in must let the run proceed: $(cat "$OPTED_OUT")"
grep -q 'WARNING:.*destroyed by receive -F' "$OPTED_OUT" || fail "the opt-in run must warn per destroyed copy"
if grep -q 'FATAL' "$OPTED_OUT"; then
  fail "the opt-in run must not refuse"
fi

echo "archive exclusion seam OK"
