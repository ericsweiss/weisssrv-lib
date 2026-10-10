#!/usr/bin/env bash
# Molecule behavioural check for the archive per-child exclusion seam: the
# rendered EXCLUDE_LIST, the `-X` derivation and capability probe, the
# archive-copy reconcile, and the source-side snapshot and retention arms.
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

# An exclusion whose archive-side twin already exists is reconciled, never
# refused: `send -R -X` + `receive -F` leaves that copy in place, so the run is
# safe either way and the flag only decides whether the copy is destroyed.
WARN_OUT="$WORK/warn.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() { case "$1" in destroy) echo "DESTROY ${*:2}" ;; esac; return 0; }
  reconcile_excluded_archive_copies
  echo "rc=$?"
' >"$WARN_OUT" 2>&1
grep -q '^rc=0$' "$WARN_OUT" \
  || fail "an existing excluded twin must not refuse the run: $(cat "$WARN_OUT")"
grep -q 'WARNING:.*archive/share/scratch' "$WARN_OUT" \
  || fail "the warning must name the orphaned archive copy"
grep -q 'WARNING:.*archive/appdata/cache' "$WARN_OUT" \
  || fail "the warning must name every orphaned archive copy"
grep -q 'nas_storage_archive_backup_exclude_destroy_ok' "$WARN_OUT" \
  || fail "the warning must name the opt-in variable"
grep -q 'never reclaimed' "$WARN_OUT" \
  || fail "the warning must say the orphan's space is not reclaimed"
if grep -q 'FATAL' "$WARN_OUT"; then
  fail "a reconciling run must not refuse: $(cat "$WARN_OUT")"
fi
if grep -q '^DESTROY' "$WARN_OUT"; then
  fail "without the opt-in no archive copy may be destroyed"
fi

# The orphans left in place are counted, so write_prom_metrics can publish them.
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() { return 0; }
  reconcile_excluded_archive_copies >/dev/null
  [ "$EXCLUDED_ORPHANS" -eq 2 ] || exit 61
  [ "$_EXCLUDE_RECONCILED" -eq 1 ] || exit 62
  exit 0
' || fail "the reconcile must count the orphans it leaves (exit $?)"

# The probe records the zfs the leave-in-place behaviour was observed on.
PROBE_OUT="$WORK/probe.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() {
    case "$1" in
      version) echo "zfs-2.3.1"; echo "zfs-kmod-2.3.1" ;;
      *) echo "usage: zfs send [-R [-X dataset[,dataset]...]] snapshot" >&2; return 2 ;;
    esac
  }
  assert_send_exclude_supported
  echo "rc=$?"
' >"$PROBE_OUT" 2>&1
grep -q '^rc=0$' "$PROBE_OUT" || fail "the probe must pass on a zfs with -X: $(cat "$PROBE_OUT")"
grep -q "send -X' supported by zfs-2.3.1" "$PROBE_OUT" \
  || fail "the probe must log the zfs version it observed: $(cat "$PROBE_OUT")"

# Nothing on the archive pool yet: the reconcile has nothing to do.
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() { return 1; }
  reconcile_excluded_archive_copies || exit 51
  exit 0
' || fail "an absent excluded twin must not block the run (exit $?)"

# With the opt-in each existing copy is destroyed recursively and logged.
OPTED_OUT="$WORK/opted.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$OPTED")"'.lib" || true
  set +e
  zfs() { case "$1" in destroy) echo "DESTROY ${*:2}" ;; esac; return 0; }
  reconcile_excluded_archive_copies
  echo "rc=$?"
' >"$OPTED_OUT" 2>&1
grep -q '^rc=0$' "$OPTED_OUT" || fail "the opt-in must let the run proceed: $(cat "$OPTED_OUT")"
grep -qx 'DESTROY -r archive/share/scratch' "$OPTED_OUT" \
  || fail "the opt-in must destroy the excluded archive copy recursively"
grep -qx 'DESTROY -r archive/appdata/cache' "$OPTED_OUT" \
  || fail "the opt-in must destroy every excluded archive copy"
[ "$(grep -c 'Exclusion: destroying archive copy ' "$OPTED_OUT")" -eq 2 ] \
  || fail "the opt-in must log one line per destroyed copy: $(cat "$OPTED_OUT")"
if grep -q 'FATAL\|WARNING' "$OPTED_OUT"; then
  fail "the opt-in run must neither refuse nor warn: $(cat "$OPTED_OUT")"
fi

# A failed destroy fails the run: the stream will not clear the copy either.
DESTROY_FAIL="$WORK/destroy-fail.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$OPTED")"'.lib" || true
  set +e
  zfs() { case "$1" in destroy) return 1 ;; esac; return 0; }
  reconcile_excluded_archive_copies
  echo "rc=$?"
' >"$DESTROY_FAIL" 2>&1
grep -q '^rc=1$' "$DESTROY_FAIL" || fail "a failed destroy must fail the run: $(cat "$DESTROY_FAIL")"
grep -q 'FATAL:.*archive/share/scratch' "$DESTROY_FAIL" \
  || fail "a failed destroy must name the copy it could not remove"

# The recursive snapshot covers excluded children too, so the just-created
# snapshot is destroyed on each of them - and on nothing else.
SNAP_OUT="$WORK/snapdrop.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() {
    case "$*" in
      "list -H -o name -r tank/share")
        printf "%s\n" tank/share tank/share/media tank/share/scratch tank/share/scratch/tmp ;;
      "destroy "*) echo "DESTROY $2" ;;
    esac
    return 0
  }
  drop_excluded_snapshots tank/share archsync-20260105-000000
  echo "rc=$?"
' >"$SNAP_OUT" 2>&1
grep -q '^rc=0$' "$SNAP_OUT" || fail "dropping excluded snapshots must succeed: $(cat "$SNAP_OUT")"
grep -qx 'DESTROY tank/share/scratch@archsync-20260105-000000' "$SNAP_OUT" \
  || fail "the excluded child must lose the snapshot just taken"
grep -qx 'DESTROY tank/share/scratch/tmp@archsync-20260105-000000' "$SNAP_OUT" \
  || fail "a descendant of an excluded child must lose it too"
[ "$(grep -c '^DESTROY ' "$SNAP_OUT")" -eq 2 ] \
  || fail "only excluded datasets may lose the snapshot: $(cat "$SNAP_OUT")"

# A failed destroy there is a retention failure, reported not swallowed.
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() {
    case "$*" in
      "list -H -o name -r tank/share") printf "%s\n" tank/share tank/share/scratch ;;
      "destroy "*) return 1 ;;
    esac
    return 0
  }
  drop_excluded_snapshots tank/share archsync-20260105-000000 && exit 61
  exit 0
' || fail "a failed snapshot destroy must be reported (exit $?)"

# Nothing excluded: the drop must not even enumerate the tree.
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$PLAIN")"'.lib" || true
  set +e
  zfs() { exit 62; }
  drop_excluded_snapshots tank/share archsync-20260105-000000 || exit 63
  exit 0
' || fail "an empty exclusion list must make the snapshot drop a no-op (exit $?)"

# Retention: the exclusion IS the policy on the source side (every archsync
# snapshot goes), while the archive-side tree keeps its 3+6 window.
PRUNE_OUT="$WORK/prune.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() {
    local last="${!#}"
    case "$*" in
      "list -H -o name -r tank/share")
        printf "%s\n" tank/share tank/share/media tank/share/scratch ;;
      "list -H -o name -r archive/share")
        printf "%s\n" archive/share archive/share/scratch ;;
      "list -H -t snapshot -o name -S creation -r "*)
        printf "%s@archsync-2026010%s-000000\n" "$last" 5 "$last" 4 "$last" 3 "$last" 2 "$last" 1 ;;
      "destroy "*) echo "DESTROY $last" ;;
    esac
    return 0
  }
  prune_snaps_tree tank/share
  echo "src-rc=$?"
  prune_snaps_tree archive/share
  echo "dst-rc=$?"
' >"$PRUNE_OUT" 2>&1
grep -q '^src-rc=0$' "$PRUNE_OUT" || fail "pruning the source tree must succeed: $(cat "$PRUNE_OUT")"
grep -q '^dst-rc=0$' "$PRUNE_OUT" || fail "pruning the archive tree must succeed: $(cat "$PRUNE_OUT")"
[ "$(grep -c 'DESTROY tank/share/scratch@' "$PRUNE_OUT")" -eq 5 ] \
  || fail "an excluded dataset must keep no archsync snapshot: $(cat "$PRUNE_OUT")"
[ "$(grep -c 'DESTROY tank/share@' "$PRUNE_OUT")" -eq 1 ] \
  || fail "a replicated source must keep its retention window"
[ "$(grep -c 'DESTROY tank/share/media@' "$PRUNE_OUT")" -eq 1 ] \
  || fail "a replicated child must keep its retention window"
[ "$(grep -c 'DESTROY archive/share/scratch@' "$PRUNE_OUT")" -eq 1 ] \
  || fail "an orphaned archive copy must keep its snapshot history"

# The orphan stops receiving snapshots, so it must not be counted when picking
# the incremental base - counting it would force a full re-send every run.
BASE_OUT="$WORK/base.out"
env -i PATH="$PATH" bash -c '
  # shellcheck disable=SC1091
  source "'"$WORK"'/'"$(basename "$EXCLUDED")"'.lib" || true
  set +e
  zfs() {
    local last="${!#}"
    case "$*" in
      "list -H -t snapshot -o name -s creation -r tank/share")
        printf "tank/share@archsync-%s-000000\n" 20260101 20260102 ;;
      "list -H -o name -r archive/share")
        printf "%s\n" archive/share archive/share/media archive/share/scratch ;;
      "list -H -t snapshot -o name -d 1 archive/share/scratch")
        printf "archive/share/scratch@archsync-20260101-000000\n" ;;
      "list -H -t snapshot -o name -d 1 "*)
        printf "%s@archsync-%s-000000\n" "$last" 20260101 "$last" 20260102 ;;
    esac
    return 0
  }
  latest_common_snapshot tank/share archive/share
  echo
' >"$BASE_OUT" 2>&1
grep -qx 'archsync-20260102-000000' "$BASE_OUT" \
  || fail "the incremental base must ignore the excluded child's frozen copy: $(cat "$BASE_OUT")"

echo "archive exclusion seam OK"
