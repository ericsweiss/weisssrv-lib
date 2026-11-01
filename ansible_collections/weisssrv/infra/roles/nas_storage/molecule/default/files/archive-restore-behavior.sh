#!/usr/bin/env bash
# Molecule behavioural check for archive-backupctl's restore paths and the
# per-dataset monthly retention, driven against a stateful zfs stub: the
# container has no ZFS, so the stub records every call the script makes.
set -euo pipefail

EXCLUDED="${1:?path to an archive-backupctl rendered WITH exclusions}"
OVERRIDES="${2:?path to an archive-backupctl rendered with keep-monthly overrides}"
PLAIN="${3:-/usr/local/sbin/archive-backupctl}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
rm -f /tmp/archive-override-expanded

fail() { echo >&2 "FAIL: $*"; exit 1; }

for script in "$EXCLUDED" "$OVERRIDES" "$PLAIN"; do
  [ -f "$script" ] || fail "not rendered: $script"
  bash -n "$script" || fail "syntax error in $script"
  sed 's/^main "\$@"$/# main disabled for behavior test/' "$script" \
    > "$WORK/$(basename "$script").lib"
  grep -q 'main disabled for behavior test' "$WORK/$(basename "$script").lib" \
    || fail "could not disable the entrypoint of $script"
done

# The stub keeps datasets as "<name> <type>" lines, set mountpoints as
# "<name> <value>" lines and archive snapshots one per line; every call lands
# in $CALLS for the assertions below.
cat > "$WORK/stub.sh" <<'STUB'
zpool() { return 0; }
# The effective mountpoint: the nearest set value on the dataset or an ancestor.
_mp_of() {
  local d="$1" v
  while :; do
    v="$(awk -v n="$d" '$1 == n { v = $2 } END { print v }' "$ST/mp")"
    [ -z "$v" ] || { echo "$v"; return; }
    [ "${d%/*}" != "$d" ] || break
    d="${d%/*}"
  done
  echo "/mnt/$1"
}
_take_run_lock_or_die() { :; }
zfs() {
  local sub="$1" last="${!#}"
  shift
  echo "zfs $sub $*" >> "$CALLS"
  case "$sub" in
    list)
      case "$*" in
        *"-t snapshot"*)
          [ -z "${FIX_SNAP_FAIL:-}" ] || { echo "cannot open '${last}': I/O error" >&2; return 1; }
          grep "^${last}@" "$ST/snaps" || true ;;
        *"-t volume"*) awk -v r="$last" '$2 == "volume" && index($1, r "/") == 1 { print $1 }' "$ST/ds" ;;
        *"-o name -t filesystem"*)
          awk -v r="$last" '$2 == "filesystem" && ($1 == r || index($1, r "/") == 1) { print $1 }' "$ST/ds" ;;
        *"-t filesystem"*)
          awk -v r="$last" -v lg="${FIX_LEGACY:-}" '$2 == "filesystem" && ($1 == r || index($1, r "/") == 1) {
              print $1 "\t" ($1 == lg ? "legacy" : "/mnt/" $1) "\tyes" }' "$ST/ds" ;;
        *"-r "*) awk -v r="$last" '$1 == r || index($1, r "/") == 1 { print $1 }' "$ST/ds" ;;
        *) awk -v n="$last" '$1 == n { f = 1; print $1 } END { exit (f ? 0 : 1) }' "$ST/ds" || return 1 ;;
      esac ;;
    get)
      case "$*" in
        *"canmount,keystatus,mounted"*)
          local ks=- d m
          case "${FIX_KEYSTATUS:-}" in *unavailable*) ks=unavailable ;; esac
          while IFS= read -r d; do
            m=no; grep -qxF "$d" "$ST/mounted" && m=yes
            printf '%s\tcanmount\ton\n%s\tkeystatus\t%s\n%s\tmounted\t%s\n%s\tmountpoint\t%s\n' \
              "$d" "$d" "$ks" "$d" "$m" "$d" "$(_mp_of "$d")"
          done < <(awk -v r="$last" '$2 == "filesystem" && ($1 == r || index($1, r "/") == 1) { print $1 }' "$ST/ds") ;;
        *"name,value,source -r -t filesystem mountpoint"*)
          awk -v r="$last" -v fx="${FIX_CHILD_MP:-}" 'BEGIN { split(fx, kv, "="); }
            $2 == "filesystem" && ($1 == r || index($1, r "/") == 1) {
              if ($1 == kv[1]) print $1 "\t" kv[2] "\tlocal"; else print $1 "\t/mnt/" $1 "\tinherited from tank" }' "$ST/ds" ;;
        *"value,source mountpoint"*) printf '%s\n' "${FIX_MP:-/mnt/tank/share	inherited from tank}" ;;
        *" type "*) awk -v n="$last" '$1 == n { print $2 }' "$ST/ds" ;;
        *encryption*) cat "${FIX_ENC:-/dev/null}" || return ;;
        *keystatus*) printf '%s\n' "${FIX_KEYSTATUS:--}" ;;
        *mountpoint*) echo /mnt/tank/share ;;
      esac ;;
    set)
      case "$1" in
        mountpoint=*)
          awk -v n="$2" '$1 != n' "$ST/mp" > "$ST/mp.new" && mv "$ST/mp.new" "$ST/mp"
          echo "$2 ${1#mountpoint=}" >> "$ST/mp" ;;
      esac ;;
    inherit)
      [ "$1" != mountpoint ] || { awk -v n="$2" '$1 != n' "$ST/mp" > "$ST/mp.new" && mv "$ST/mp.new" "$ST/mp"; } ;;
    rename)
      [ -z "${FIX_RENAME_FAIL:-}" ] || { echo "cannot rename '$1': dataset is busy" >&2; return 1; }
      local f
      for f in ds mp; do
        awk -v o="$1" -v n="$2" '{ if ($1 == o || index($1, o "/") == 1) $1 = n substr($1, length(o) + 1); print }' \
          "$ST/$f" > "$ST/$f.new" && mv "$ST/$f.new" "$ST/$f"
      done ;;
    destroy)
      local f
      for f in ds mp; do
        awk -v r="$last" '!($1 == r || index($1, r "/") == 1)' "$ST/$f" > "$ST/$f.new" && mv "$ST/$f.new" "$ST/$f"
      done ;;
    receive)
      cat > /dev/null
      if [ -n "${FIX_RECV_FAIL:-}" ]; then
        [ "$FIX_RECV_FAIL" = partial ] && printf '%s filesystem\n' "$last" >> "$ST/ds"
        echo "cannot receive new filesystem stream: out of space" >&2
        return 1
      fi
      if awk -v n="$last" '$1 == n { f = 1 } END { exit (f ? 0 : 1) }' "$ST/ds"; then
        echo "cannot receive new filesystem stream: destination exists" >&2
        return 1
      fi
      printf '%s filesystem\n%s/media filesystem\n%s/vol volume\n' "$last" "$last" "$last" >> "$ST/ds" ;;
    # Only -R is a recursive mount, and it skips a none or legacy mountpoint;
    # any other flag is rejected as zfs does.
    mount)
      case "$1" in
        -R)
          case "${FIX_KEYSTATUS:-}" in *unavailable*) return 1 ;; esac
          local d
          awk -v r="$2" '$2 == "filesystem" && ($1 == r || index($1, r "/") == 1) { print $1 }' "$ST/ds" \
            | grep -Ev -- "${FIX_NOMOUNT:-^$}" | while IFS= read -r d; do
              case "$(_mp_of "$d")" in none|legacy) ;; *) echo "$d" ;; esac
            done >> "$ST/mounted" || true ;;
        -*) echo "invalid option '${1#-}'" >&2; return 2 ;;
        *) echo "$1" >> "$ST/mounted" ;;
      esac ;;
  esac
  return 0
}
fuser() {
  if [ -n "${FIX_FUSER_ERR:-}" ]; then echo "$FIX_FUSER_ERR" >&2; return 1; fi
  [ -z "${FIX_FUSER_HEADER:-}" ] || echo "${!#}:" >&2
  [ "${FIX_HELD:-}" = "${!#}" ]
}
exportfs() {
  if [ -n "${FIX_EXPORTFS_ERR:-}" ]; then echo "$FIX_EXPORTFS_ERR" >&2; return 1; fi
  [ -z "${FIX_EXPORTS:-}" ] || printf '%s\n' "$FIX_EXPORTS"
}
# A path's device is the dataset mounted at its longest /mnt/<dataset> prefix,
# after FIX_BINDS ("<bind>=<source>") maps a bind target to its source. A -S
# lookup answers FIX_LEGACY_TARGET, the fstab mount of the FIX_LEGACY dataset.
findmnt() {
  case "$*" in
    *"-S "*) [ -n "${FIX_LEGACY_TARGET:-}" ] || return 1; printf '%s\n' "$FIX_LEGACY_TARGET"; return 0 ;;
  esac
  local p="${!#}" b="${FIX_BINDS%%=*}" src="${FIX_BINDS#*=}"
  if [ -n "${FIX_BINDS:-}" ] && { [ "$p" = "$b" ] || [ "${p#"$b"/}" != "$p" ]; }; then
    p="${src}${p#"$b"}"
  fi
  awk -v p="$p" '$2 == "filesystem" { m = "/mnt/" $1
      if ((p == m || index(p, m "/") == 1) && length(m) > length(best)) best = m }
    END { print (best == "" ? " 0:1" : " 0:" best) }' "$ST/ds"
}
STUB

# fresh <dir>: one source tree (with an excluded child and a zvol) plus its
# archive copy carrying two archsync snapshots.
fresh() {
  rm -rf "$1"
  mkdir -p "$1/dev/zvol/tank/share" "$1/dev/zvol/ssd/appdata"
  : > "$1/dev/zvol/tank/share/vol"
  : > "$1/dev/zvol/ssd/appdata/cache"
  cat > "$1/ds" <<'DS'
tank filesystem
tank/share filesystem
tank/share/media filesystem
tank/share/vol volume
ssd filesystem
ssd/appdata filesystem
ssd/appdata/cache volume
archive filesystem
archive/share filesystem
archive/share/media filesystem
archive/appdata filesystem
DS
  printf '%s\n' archive/share@archsync-20260101-000000 archive/share@archsync-20260102-000000 \
    archive/appdata@archsync-20260102-000000 > "$1/snaps"
  : > "$1/calls"
  : > "$1/mounted"
  : > "$1/mp"
}

# drive <render> <dir> <shell>: run <shell> with the render sourced over the stub.
drive() {
  local lib st="$2" body="$3"
  lib="$WORK/$(basename "$1").lib"
  env -i PATH="${DRIVE_PATH:-$PATH}" ST="$st" CALLS="$st/calls" \
    FIX_MP="${FIX_MP:-}" FIX_ENC="${FIX_ENC:-}" FIX_KEYSTATUS="${FIX_KEYSTATUS:-}" \
    FIX_HELD="${FIX_HELD:-}" FIX_RECV_FAIL="${FIX_RECV_FAIL:-}" FIX_NOMOUNT="${FIX_NOMOUNT:-}" \
    FIX_FUSER_ERR="${FIX_FUSER_ERR:-}" FIX_FUSER_HEADER="${FIX_FUSER_HEADER:-}" \
    FIX_EXPORTS="${FIX_EXPORTS:-}" FIX_LEGACY="${FIX_LEGACY:-}" FIX_LEGACY_TARGET="${FIX_LEGACY_TARGET:-}" \
    FIX_EXPORTFS_ERR="${FIX_EXPORTFS_ERR:-}" FIX_BINDS="${FIX_BINDS:-}" \
    FIX_CHILD_MP="${FIX_CHILD_MP:-}" FIX_SNAP_FAIL="${FIX_SNAP_FAIL:-}" \
    FIX_RENAME_FAIL="${FIX_RENAME_FAIL:-}" bash -c '
    # shellcheck disable=SC1090,SC1091
    source "$0" || true
    set +e
    # shellcheck disable=SC1091
    source "$ST/../stub.sh"
    RESTORE_BASE="$ST/restore"
    ZVOL_DEV_DIR="$ST/dev/zvol"
    '"$body"'
  ' "$lib" > "$st/out" 2>&1
}

line_of() { grep -n -m1 -- "$2" "$1" | cut -d: -f1; }

# --- Safe restore: a new -restore-<ts> sibling, never -F, mounted under the base.
S="$WORK/safe"; fresh "$S"
drive "$EXCLUDED" "$S" '_restore_one archive/share safe; echo "rc=$?"'
grep -q '^rc=0$' "$S/out" || fail "safe restore: $(cat "$S/out")"
grep -q '^zfs send -R archive/share@archsync-20260102-000000$' "$S/calls" \
  || fail "safe restore must send the newest archive snapshot: $(cat "$S/calls")"
grep -Eq '^zfs receive -u tank/share-restore-[0-9]{8}-[0-9]{6}$' "$S/calls" \
  || fail "safe restore must receive into a new -restore-<ts> dataset: $(cat "$S/calls")"
grep -q '^zfs receive .*-F' "$S/calls" && fail "safe restore must never receive -F"
grep -Eq '^zfs (rename|destroy) ' "$S/calls" && fail "safe restore must not touch the live dataset"
grep -Eq "^zfs set mountpoint=$S/restore/share/[0-9]{8}-[0-9]{6} tank/share-restore-" "$S/calls" \
  || fail "safe restore must mount under the restore base: $(cat "$S/calls")"
grep -q 'Mounted restore tree at:' "$S/out" || fail "safe restore must report its mountpoint"
grep -Eq '^zfs mount -R tank/share-restore-[0-9]{8}-[0-9]{6}$' "$S/calls" \
  || fail "safe restore must mount the tree with -R: $(cat "$S/calls")"

# --- Force restore over an existing dataset: rename aside, then a plain receive.
F="$WORK/force"; fresh "$F"
drive "$EXCLUDED" "$F" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=0$' "$F/out" || fail "force restore: $(cat "$F/out")"
grep -Eq '^zfs rename tank/share tank/share-pre-restore-[0-9]{8}-[0-9]{6}$' "$F/calls" \
  || fail "force restore must rename the existing dataset aside: $(cat "$F/calls")"
grep -Eq '^zfs set mountpoint=none tank/share-pre-restore-' "$F/calls" \
  || fail "the set-aside copy must be unmounted"
grep -Eq '^zfs inherit mountpoint tank/share-pre-restore-[0-9-]+/media$' "$F/calls" \
  || fail "the set-aside children must follow the unmounted root"
grep -q '^zfs receive -u tank/share$' "$F/calls" \
  || fail "force restore must receive into the freed name without -F: $(cat "$F/calls")"
grep -q '^zfs receive .*-F' "$F/calls" && fail "force restore must not rely on receive -F"
grep -q '^zfs destroy' "$F/calls" && fail "force restore must keep the previous tree by default"
[ "$(line_of "$F/calls" '^zfs rename ')" -lt "$(line_of "$F/calls" '^zfs receive ')" ] \
  || fail "the rename must precede the receive"
grep -q "Destroy it with 'zfs destroy -r tank/share-pre-restore-" "$F/out" \
  || fail "force restore must say how to drop the set-aside copy: $(cat "$F/out")"

# Per-child prop resets: an inherited mountpoint follows the parent again,
# filesystem children get canmount back, a zvol child is left alone.
grep -q '^zfs inherit -r readonly tank/share$' "$F/calls" || fail "readonly must be cleared tree-wide"
grep -q '^zfs set canmount=on tank/share$' "$F/calls" || fail "the root must be mountable"
grep -q '^zfs inherit mountpoint tank/share$' "$F/calls" \
  || fail "an inherited mountpoint must be inherited again: $(cat "$F/calls")"
grep -q '^zfs set mountpoint=/mnt/' "$F/calls" && fail "an inherited mountpoint must not be hard-coded"
grep -q '^zfs set canmount=on tank/share/media$' "$F/calls" || fail "filesystem children must be mountable"
grep -q '^zfs inherit mountpoint tank/share/media$' "$F/calls" || fail "filesystem children must inherit the mountpoint"
grep -q 'tank/share/vol$' <(grep -E '^zfs (set canmount|inherit mountpoint)' "$F/calls") \
  && fail "a zvol child takes neither canmount nor mountpoint"
grep -q 'Restored tank/share, mounted at' "$F/out" || fail "force restore must report the mount: $(cat "$F/out")"
grep -q '^zfs mount -R tank/share$' "$F/calls" || fail "force restore must mount the tree with -R"
grep -Eq '^zfs mount -[^R]' "$F/calls" && fail "zfs mount takes -R for a recursive mount, never another flag"

# A local mountpoint is put back verbatim.
L="$WORK/local"; fresh "$L"
FIX_MP=$'/srv/share\tlocal' drive "$EXCLUDED" "$L" '_restore_one archive/share force'
grep -q '^zfs set mountpoint=/srv/share tank/share$' "$L/calls" \
  || fail "a local mountpoint must be restored: $(cat "$L/calls")"

# A none root mountpoint comes back as none, and a tree that never mounts succeeds.
L="$WORK/local-none"; fresh "$L"
FIX_MP=$'none\tlocal' drive "$EXCLUDED" "$L" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=0$' "$L/out" || fail "a none mountpoint must not read as a failed mount: $(cat "$L/out")"
grep -q '^zfs set mountpoint=none tank/share$' "$L/calls" || fail "a none mountpoint must be restored"
grep -q 'did not mount' "$L/out" && fail "a none mountpoint must not be reported as unmounted"

# A child's own mountpoint is set again after the children are reset to inherit.
L="$WORK/child-mp"; fresh "$L"
FIX_CHILD_MP=tank/share/media=/srv/media drive "$EXCLUDED" "$L" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=0$' "$L/out" || fail "a force restore with a child mountpoint: $(cat "$L/out")"
[ "$(line_of "$L/calls" '^zfs inherit mountpoint tank/share/media$')" -lt "$(line_of "$L/calls" '^zfs set mountpoint=/srv/media tank/share/media$')" ] \
  || fail "a child's local mountpoint must be set again: $(cat "$L/calls")"
grep -qx 'tank/share/media /srv/media' "$L/mp" || fail "the child must end at its own mountpoint: $(cat "$L/mp")"

# A recorded child the restored tree lacks is warned about, not failed.
L="$WORK/child-mp-gone"; fresh "$L"
echo 'tank/share/gone filesystem' >> "$L/ds"
FIX_CHILD_MP=tank/share/gone=/srv/gone drive "$EXCLUDED" "$L" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=0$' "$L/out" || fail "a missing recorded child must not fail the restore: $(cat "$L/out")"
grep -q 'WARNING: tank/share/gone had mountpoint=/srv/gone but is not in the restored tree' "$L/out" \
  || fail "a missing recorded child must be warned about: $(cat "$L/out")"

# A missing target is created with the same plain receive and inherits its mountpoint.
M="$WORK/missing"; fresh "$M"
grep -v '^tank/share' "$M/ds" > "$M/ds.new" && mv "$M/ds.new" "$M/ds"
drive "$EXCLUDED" "$M" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=0$' "$M/out" || fail "restore into a missing target: $(cat "$M/out")"
grep -Eq '^zfs (rename|destroy) ' "$M/calls" && fail "a missing target has nothing to set aside"
grep -q '^zfs inherit mountpoint tank/share$' "$M/calls" || fail "a new target must inherit its mountpoint"

# --- A failed receive after the rename puts the previous tree back.
B="$WORK/rollback"; fresh "$B"
FIX_RECV_FAIL=1 drive "$EXCLUDED" "$B" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$B/out" || fail "a failed receive must fail the restore: $(cat "$B/out")"
grep -Eq '^zfs rename tank/share-pre-restore-[0-9-]+ tank/share$' "$B/calls" \
  || fail "a failed receive must rename the previous tree back: $(cat "$B/calls")"
{ grep -qx 'tank/share filesystem' "$B/ds" && grep -qx 'tank/share/media filesystem' "$B/ds"; } \
  || fail "the previous tree must be back under its name: $(cat "$B/ds")"
grep -q 'pre-restore' "$B/ds" && fail "no set-aside copy may remain after the rollback"
[ "$(grep -c '^zfs inherit mountpoint tank/share$' "$B/calls")" -eq 1 ] \
  || fail "the rolled-back root must take its inherited mountpoint again: $(cat "$B/calls")"
grep -q 'Rolled back: tank/share holds the previous tree again' "$B/out" || fail "the rollback must be logged"
grep -q 'Destroy it with' "$B/out" && fail "a failed restore must never advise destroying the previous tree"

B="$WORK/rollback-partial"; fresh "$B"
FIX_RECV_FAIL=partial FIX_MP=$'/srv/share\tlocal' \
  drive "$EXCLUDED" "$B" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$B/out" || fail "a partial receive must fail the restore"
[ "$(line_of "$B/calls" '^zfs destroy -r tank/share$')" -lt "$(line_of "$B/calls" '^zfs rename tank/share-pre-restore-')" ] \
  || fail "the partial tree must be destroyed before the rename back: $(cat "$B/calls")"
grep -q '^zfs set mountpoint=/srv/share tank/share$' "$B/calls" \
  || fail "the rollback must restore a local mountpoint verbatim"

# A partial tree left with nothing to roll back to is this run's own, so it is destroyed.
B="$WORK/missing-partial"; fresh "$B"
grep -v '^tank/share' "$B/ds" > "$B/ds.new" && mv "$B/ds.new" "$B/ds"
FIX_RECV_FAIL=partial drive "$EXCLUDED" "$B" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$B/out" || fail "a partial restore into a missing target must fail: $(cat "$B/out")"
grep -q 'nothing to roll back to' "$B/out" || fail "a missing target must say nothing is left: $(cat "$B/out")"
[ "$(grep -c '^zfs destroy -r tank/share$' "$B/calls")" -eq 1 ] \
  || fail "the partial tree must be destroyed after the failed receive: $(cat "$B/calls")"
grep -q '^tank/share ' "$B/ds" && fail "no partial tree may remain: $(cat "$B/ds")"
grep -q 'Destroyed the partial tank/share this run received' "$B/out" || fail "the cleanup must be logged"

# A child's own mountpoint is set again by the rollback, after the rename back.
B="$WORK/rollback-child-mp"; fresh "$B"
FIX_RECV_FAIL=1 FIX_CHILD_MP=tank/share/media=/srv/media \
  drive "$EXCLUDED" "$B" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$B/out" || fail "a failed receive must fail the restore: $(cat "$B/out")"
grep -Eq '^zfs inherit mountpoint tank/share-pre-restore-[0-9-]+/media$' "$B/calls" \
  || fail "the aside child must be reset to inherit: $(cat "$B/calls")"
[ "$(line_of "$B/calls" '^zfs rename tank/share-pre-restore-')" -lt "$(line_of "$B/calls" '^zfs set mountpoint=/srv/media tank/share/media$')" ] \
  || fail "the rollback must set the child's local mountpoint again: $(cat "$B/calls")"
grep -q '^zfs mount -R tank/share$' "$B/calls" || fail "the rollback must mount the tree"
grep -q 'Rolled back: tank/share holds the previous tree again' "$B/out" || fail "the rollback must be logged"

# A rolled-back tree that does not mount is reported, never logged as rolled back.
B="$WORK/rollback-nomount"; fresh "$B"
FIX_RECV_FAIL=1 FIX_NOMOUNT='^tank/share/media$' \
  drive "$EXCLUDED" "$B" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$B/out" || fail "an incomplete rollback must fail: $(cat "$B/out")"
grep -q 'did not mount: tank/share/media' "$B/out" || fail "the rollback must name the unmounted filesystem: $(cat "$B/out")"
grep -q 'ROLLBACK INCOMPLETE: tank/share' "$B/out" || fail "an incomplete rollback must say so"
grep -q 'Rolled back:' "$B/out" && fail "an incomplete rollback must not report success"

# A failed rename leaves the target untouched, and no rollback runs.
B="$WORK/rename-fail"; fresh "$B"
FIX_RENAME_FAIL=1 drive "$EXCLUDED" "$B" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$B/out" || fail "a failed rename must fail the restore: $(cat "$B/out")"
grep -q 'tank/share was not renamed and is untouched' "$B/out" || fail "a failed rename must say the target is untouched: $(cat "$B/out")"
grep -Eq 'nothing to roll back to|rolling back' "$B/out" && fail "a failed rename must not report a rollback"
grep -Eq '^zfs (send|receive|destroy) ' "$B/calls" && fail "a failed rename must change nothing else"

# A failed safe receive destroys the partial tree it left.
B="$WORK/safe-partial"; fresh "$B"
FIX_RECV_FAIL=partial drive "$EXCLUDED" "$B" '_restore_one archive/share safe; echo "rc=$?"'
grep -q '^rc=1$' "$B/out" || fail "a failed safe receive must fail: $(cat "$B/out")"
grep -Eq '^zfs destroy -r tank/share-restore-[0-9]{8}-[0-9]{6}$' "$B/calls" \
  || fail "a failed safe receive must destroy its partial tree: $(cat "$B/calls")"
grep -q 'Destroyed the partial tank/share-restore-' "$B/out" || fail "the cleanup must be logged"
grep -q 'restore-' "$B/ds" && fail "no partial restore tree may remain: $(cat "$B/ds")"

# --- A named target with nothing to restore fails; a failed listing is not a skip.
X="$WORK/skip-named"; fresh "$X"
grep -v '^archive/appdata@' "$X/snaps" > "$X/snaps.new" && mv "$X/snaps.new" "$X/snaps"
drive "$EXCLUDED" "$X" '( _cmd_restore_impl safe restore appdata ); echo "rc=$?"'
grep -q '^rc=1$' "$X/out" || fail "a named target with no snapshot must fail: $(cat "$X/out")"
grep -q 'SKIP restore: no archsync- snapshots on archive/appdata' "$X/out" || fail "the skip must be logged"
grep -q 'nothing was restored for appdata' "$X/out" || fail "the run must name the skipped target"

X="$WORK/skip-all"; fresh "$X"
grep -v '^archive/appdata@' "$X/snaps" > "$X/snaps.new" && mv "$X/snaps.new" "$X/snaps"
drive "$EXCLUDED" "$X" '( _cmd_restore_impl safe restore all ); echo "rc=$?"'
grep -q '^rc=1$' "$X/out" || fail "all with a skipped target must fail: $(cat "$X/out")"
grep -Eq '^zfs receive -u tank/share-restore-' "$X/calls" || fail "all must still restore the other targets"
grep -q 'nothing was restored for .*appdata' "$X/out" || fail "the summary must name the skipped target: $(cat "$X/out")"

X="$WORK/snap-list-fail"; fresh "$X"
FIX_SNAP_FAIL=1 drive "$EXCLUDED" "$X" '_restore_one archive/share safe; echo "rc=$?"'
grep -q '^rc=1$' "$X/out" || fail "a failed snapshot listing must fail: $(cat "$X/out")"
grep -q 'cannot list its snapshots' "$X/out" || fail "the failure must name the listing"
grep -q 'SKIP restore' "$X/out" && fail "a failed listing must not read as a skip"

# --- A filesystem that did not mount fails the restore instead of reporting success.
N="$WORK/nomount"; fresh "$N"
FIX_NOMOUNT='^tank/share/media$' drive "$EXCLUDED" "$N" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$N/out" || fail "an unmounted filesystem must fail the restore: $(cat "$N/out")"
grep -q 'did not mount: tank/share/media' "$N/out" || fail "the failure must name the unmounted filesystem"
grep -q 'Restored tank/share, mounted at' "$N/out" && fail "an incomplete mount must not report success"
grep -q 'The previous tree is kept as tank/share-pre-restore-' "$N/out" \
  || fail "a failed mount must name the kept previous tree"

N="$WORK/nomount-safe"; fresh "$N"
FIX_NOMOUNT='/media$' drive "$EXCLUDED" "$N" '_restore_one archive/share safe; echo "rc=$?"'
grep -q '^rc=1$' "$N/out" || fail "an unmounted safe restore must fail: $(cat "$N/out")"
grep -Eq 'did not mount: tank/share-restore-[0-9-]+/media' "$N/out" || fail "the failure must name the filesystem"
grep -q 'Mounted restore tree at:' "$N/out" && fail "an unmounted safe restore must not report success"

# --- An unreadable encryption property touches nothing.
E="$WORK/enc-fail"; fresh "$E"
FIX_ENC="$E/no-such-fixture" drive "$EXCLUDED" "$E" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=1$' "$E/out" || fail "an unreadable encryption property must fail: $(cat "$E/out")"
grep -q 'encryption unknown' "$E/out" || fail "the failure must say the encryption is unknown"
grep -Eq '^zfs (rename|destroy|send|receive) ' "$E/calls" && fail "an unknown encryption must change nothing"

# --- Key-less raw restore: sent -w, and the mount warning names the unlock.
# The encryption fixture outgrows the pipe buffer with its one encrypted line
# first, so a `| grep -q` probe would read the tree as plaintext.
K="$WORK/keyless"; fresh "$K"
{ echo aes-256-gcm; awk 'BEGIN { for (i = 0; i < 40000; i++) print "off" }'; } > "$K/enc"
FIX_ENC="$K/enc" FIX_KEYSTATUS=$'available\nunavailable' \
  drive "$EXCLUDED" "$K" '_restore_one archive/share force; echo "rc=$?"'
grep -q '^rc=0$' "$K/out" || fail "key-less restore: $(cat "$K/out")"
grep -q '^zfs send -R -w archive/share@' "$K/calls" \
  || fail "an encrypted backup must be sent raw: $(cat "$K/calls")"
grep -q 'contains encrypted key-less datasets' "$K/out" \
  || fail "a key-less restore must warn: $(cat "$K/out")"
grep -q 'zfs load-key -r -L prompt tank/share && zfs mount -R tank/share' "$K/out" \
  || fail "the warning must name the unlock command"
grep -q 'Restored tank/share, mounted at' "$K/out" \
  && fail "a key-less restore must not report a readable mount"

# --- Refusals: every target is checked before anything is touched.
R="$WORK/excluded"; fresh "$R"
drive "$EXCLUDED" "$R" '( _cmd_restore_impl force restore-force appdata ); echo "rc=$?"'
grep -q '^rc=1$' "$R/out" || fail "an excluded child must refuse the restore: $(cat "$R/out")"
grep -q 'REFUSED: ssd/appdata/cache is excluded' "$R/out" || fail "the refusal must name the excluded child"
grep -q -- '--force-excluded' "$R/out" || fail "the refusal must name its override"
grep -q 'README' "$R/out" || fail "the refusal must point at the per-dataset recipe"
grep -Eq '^zfs (rename|destroy|send|receive) ' "$R/calls" && fail "a refused restore must change nothing"

R="$WORK/excluded-all"; fresh "$R"
drive "$EXCLUDED" "$R" '( _cmd_restore_impl force restore-force all ); echo "rc=$?"'
grep -q '^rc=1$' "$R/out" || fail "all must refuse when any target is refused"
grep -Eq '^zfs (rename|destroy|send|receive) ' "$R/calls" \
  && fail "a refused 'all' must not restore the targets checked before the refusal"

R="$WORK/excluded-ok"; fresh "$R"
FIX_HELD="" drive "$EXCLUDED" "$R" '( _cmd_restore_impl force restore-force appdata --force-excluded ); echo "rc=$?"'
grep -q '^rc=0$' "$R/out" || fail "--force-excluded must let the restore run: $(cat "$R/out")"
grep -q 'WARNING: ssd/appdata/cache is excluded' "$R/out" || fail "the override must still warn"
grep -q '^zfs receive -u ssd/appdata$' "$R/calls" || fail "--force-excluded must restore: $(cat "$R/calls")"

H="$WORK/held"; fresh "$H"
FIX_HELD="$H/dev/zvol/tank/share/vol" drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "a held zvol must refuse the restore: $(cat "$H/out")"
grep -q "REFUSED: .*tank/share/vol is held open" "$H/out" || fail "the refusal must name the held zvol"
grep -q -- '--force-held' "$H/out" || fail "the refusal must name its override"
grep -Eq '^zfs (rename|destroy|send|receive) ' "$H/calls" && fail "a refused restore must change nothing"

H="$WORK/held-fs"; fresh "$H"
FIX_HELD=/mnt/tank/share/media drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "a mounted filesystem in use must refuse the restore: $(cat "$H/out")"
grep -q 'REFUSED: /mnt/tank/share/media is held open' "$H/out" || fail "the refusal must name the filesystem"
grep -Eq '^zfs (rename|destroy|send|receive) ' "$H/calls" && fail "a refused restore must change nothing"

# A legacy mountpoint is found through the mount table, and refuses when it
# cannot be resolved there.
H="$WORK/held-legacy"; fresh "$H"
FIX_LEGACY=tank/share/media FIX_LEGACY_TARGET=/srv/legacy-media FIX_HELD=/srv/legacy-media \
  drive "$EXCLUDED" "$H" '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "a held legacy-mounted filesystem must refuse: $(cat "$H/out")"
grep -q 'REFUSED: /srv/legacy-media is held open' "$H/out" || fail "the refusal must name the legacy mount"
grep -Eq '^zfs (rename|destroy|send|receive) ' "$H/calls" && fail "a refused restore must change nothing"

H="$WORK/legacy-unresolved"; fresh "$H"
FIX_LEGACY=tank/share/media drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "an unresolved legacy mount must refuse: $(cat "$H/out")"
grep -q 'REFUSED: tank/share/media is mounted with mountpoint=legacy at a path that could not be resolved' "$H/out" \
  || fail "the refusal must name the unresolved dataset: $(cat "$H/out")"

H="$WORK/legacy-ok"; fresh "$H"
FIX_LEGACY=tank/share/media FIX_LEGACY_TARGET=/srv/legacy-media drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=0$' "$H/out" || fail "an idle legacy mount must not refuse: $(cat "$H/out")"

H="$WORK/fuser-error"; fresh "$H"
FIX_FUSER_ERR="Specified filename does not exist." drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "a failed holder probe must refuse the restore: $(cat "$H/out")"
grep -q 'could not be checked for holders: Specified filename' "$H/out" || fail "the refusal must carry the fuser error"

# A probe that prints only the "<path>:" header looked and found no holders.
H="$WORK/fuser-header"; fresh "$H"
FIX_FUSER_HEADER=1 drive "$EXCLUDED" "$H" '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=0$' "$H/out" || fail "a header-only fuser probe must not refuse: $(cat "$H/out")"

# fuser cannot see NFS clients, so an exported filesystem refuses on its own.
H="$WORK/exported"; fresh "$H"
FIX_EXPORTS='/mnt/tank/share/media 10.0.0.0/24(rw,sync)' drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "an exported filesystem must refuse the restore: $(cat "$H/out")"
grep -q 'REFUSED: /mnt/tank/share/media is exported over NFS as /mnt/tank/share/media' "$H/out" \
  || fail "the refusal must name the export: $(cat "$H/out")"
grep -q 'exportfs -u' "$H/out" || fail "the refusal must say how to unexport"
grep -Eq '^zfs (rename|destroy|send|receive) ' "$H/calls" && fail "a refused restore must change nothing"

H="$WORK/exported-bind"; fresh "$H"
FIX_EXPORTS='/srv/nfs/share 10.0.0.0/24(rw,sync)' FIX_BINDS=/srv/nfs/share=/mnt/tank/share \
  drive "$EXCLUDED" "$H" '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "a filesystem exported through a bind must refuse: $(cat "$H/out")"
grep -q 'REFUSED: /mnt/tank/share is exported over NFS as /srv/nfs/share' "$H/out" \
  || fail "the refusal must name the bind export: $(cat "$H/out")"

H="$WORK/exported-above"; fresh "$H"
FIX_EXPORTS='/mnt/tank 10.0.0.0/24(rw,crossmnt)' drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "an export above the mountpoint must refuse: $(cat "$H/out")"

H="$WORK/exported-other"; fresh "$H"
FIX_EXPORTS='/mnt/ssd/appdata 10.0.0.0/24(rw,sync)' drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=0$' "$H/out" || fail "an unrelated export must not refuse: $(cat "$H/out")"

H="$WORK/exportfs-error"; fresh "$H"
FIX_EXPORTFS_ERR="exportfs: could not open /var/lib/nfs/etab" drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "an unreadable export table must refuse: $(cat "$H/out")"
grep -q 'the NFS export table could not be read: exportfs: could not open' "$H/out" \
  || fail "the refusal must carry the exportfs error: $(cat "$H/out")"

H="$WORK/exported-ok"; fresh "$H"
FIX_EXPORTS='/mnt/tank/share 10.0.0.0/24(rw,sync)' drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share --force-held ); echo "rc=$?"'
grep -q '^rc=0$' "$H/out" || fail "--force-held must let an exported restore run: $(cat "$H/out")"
grep -q 'WARNING: /mnt/tank/share is exported over NFS' "$H/out" || fail "the override must still warn"

H="$WORK/held-ok"; fresh "$H"
FIX_HELD="$H/dev/zvol/tank/share/vol" drive "$EXCLUDED" "$H" \
  '( _cmd_restore_impl force restore-force share --force-held ); echo "rc=$?"'
grep -q '^rc=0$' "$H/out" || fail "--force-held must let the restore run: $(cat "$H/out")"
grep -q 'WARNING: .*tank/share/vol is held open' "$H/out" || fail "the override must still warn"

H="$WORK/idle"; fresh "$H"
drive "$EXCLUDED" "$H" '( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=0$' "$H/out" || fail "an unheld zvol must not refuse: $(cat "$H/out")"

# Without fuser the holders are unknown, which refuses too.
NOFUSER="$WORK/nofuser-bin"; mkdir -p "$NOFUSER"
for f in /usr/bin/* /bin/*; do
  [ "${f##*/}" = fuser ] || ln -sf "$f" "$NOFUSER/" 2>/dev/null || true
done
H="$WORK/nofuser"; fresh "$H"
DRIVE_PATH="$NOFUSER" drive "$EXCLUDED" "$H" \
  'unset -f fuser; ( _cmd_restore_impl force restore-force share ); echo "rc=$?"'
grep -q '^rc=1$' "$H/out" || fail "a host without fuser must refuse: $(cat "$H/out")"
grep -q 'fuser is not installed' "$H/out" || fail "the refusal must say fuser is missing"

# Override flags belong to restore-force only.
U="$WORK/usage"; fresh "$U"
drive "$EXCLUDED" "$U" '( _cmd_restore_impl safe restore share --force-held ); echo "rc=$?"'
grep -q '^rc=2$' "$U/out" || fail "a force flag on a safe restore must be a usage error"
grep -q 'restore-force <target> \[--force-excluded\] \[--force-held\]' "$PLAIN" \
  || fail "the usage text must list the restore-force flags"
grep -q 'pre-restore' "$PLAIN" || fail "the usage text must say the existing dataset is renamed aside"

# --- Per-dataset monthly retention.
P="$WORK/prune"; mkdir -p "$P"
prune_out() {
  drive "$1" "$P" '
    zfs() {
      local last="${!#}"
      case "$*" in
        "list -H -t snapshot -o name -S creation -r "*)
          for m in 10 09 08 07 06 05 04 03 02 01; do echo "${last}@archsync-2025${m}28-000000"; done ;;
        "destroy "*) echo "DESTROY $last" ;;
      esac
      return 0
    }
    for ds in '"$2"'; do prune_snaps_dataset "$ds" retain; echo "$ds kept_monthly=$(keep_monthly_for "$ds")"; done
  '
}
count() { grep -c "^DESTROY $1@" "$P/out" || true; }
prune_out "$OVERRIDES" "tank/proxmox archive/proxmox tank/proxmox/dump tank/share tank/share/media archive/share/media tank/share/mediax"
[ "$(count tank/proxmox)" -eq 7 ] || fail "a 0 override must keep only the recent window: $(cat "$P/out")"
[ "$(count archive/proxmox)" -eq 7 ] || fail "the override must reach the archive copy: $(cat "$P/out")"
[ "$(count tank/proxmox/dump)" -eq 7 ] || fail "the override must reach the subtree"
[ "$(count tank/share)" -eq 1 ] || fail "a dataset with no override keeps the global window"
[ "$(count tank/share/media)" -eq 6 ] || fail "a 1 override keeps one monthly"
[ "$(count archive/share/media)" -eq 6 ] || fail "a child override must reach its archive copy"
[ "$(count tank/share/mediax)" -eq 1 ] || fail "a sibling sharing a name prefix must not take the override"
grep -q '^DESTROY tank/proxmox@archsync-20251028-000000$' "$P/out" && fail "the newest snapshot must survive"
# An override key is shell-quoted in the render, so declaring it runs nothing.
[ ! -e /tmp/archive-override-expanded ] || fail "an override key was expanded when the array was declared"
printf '%s' 'tank/share/q"x$(touch /tmp/archive-override-expanded)' > "$P/key"
drive "$OVERRIDES" "$P" 'k="$(cat "$ST/key")"; keep_monthly_for "$k"; echo; keep_monthly_for "archive/${k#tank/}"'
[ "$(sed -n 1p "$P/out")" = 4 ] || fail "a quoted override key must apply verbatim: $(cat "$P/out")"
[ "$(sed -n 2p "$P/out")" = 4 ] || fail "a quoted override key must reach its archive copy: $(cat "$P/out")"
[ ! -e /tmp/archive-override-expanded ] || fail "an override key was expanded"
prune_out "$PLAIN" "tank/proxmox"
[ "$(count tank/proxmox)" -eq 1 ] || fail "with no overrides the global window applies: $(cat "$P/out")"

echo "archive restore and retention OK"
