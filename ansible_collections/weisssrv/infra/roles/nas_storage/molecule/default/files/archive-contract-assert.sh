#!/usr/bin/env bash
# Molecule contract check for the rendered archive-backupctl. A real *.sh file so
# it is shellcheck-lintable and its quotes don't trip Ansible's argument splitter.
# STRUCTURAL only: the container has no ZFS, so the zvol-vs-filesystem receive is
# not exercised. MAP/RMAP, the lock list and the restore targets are all derived
# from SRC_LIST, so only SRC_LIST membership and the root allow-list are pinned.
set -euo pipefail

s="${1:-/usr/local/sbin/archive-backupctl}"
[ -f "$s" ] || { echo >&2 "archive-backupctl not rendered at $s"; exit 1; }

# The CI shellcheck job lints the neutralized template only; this validates the
# REAL render's syntax.
bash -n "$s"

# SRC_LIST entries (full paths) — array-element lines only, so a future quoted
# token inside an in-block comment is not mis-parsed as a dataset.
src_list="$(awk '/^SRC_LIST=\(/{f=1; next} f && /^\)/{f=0} f' "$s" \
  | grep -E '^[[:space:]]*"' | grep -oE '"[^"]+"' | tr -d '"')"
bn="$(printf '%s\n' "$src_list" | sed 's#.*/##')"
[ "$(printf '%s\n' "$bn" | grep -c .)" -ge 1 ] || { echo >&2 "no SRC_LIST datasets parsed"; exit 1; }

# Match the FULL path: a basename match would also accept a wrong pool.
printf '%s\n' "$src_list" | grep -qx 'tank/immich-data' || { echo >&2 "tank/immich-data missing from SRC_LIST"; exit 1; }

# ssd/k3s-etcd carries the off-node etcd snapshot copies the k3s DR chain needs.
printf '%s\n' "$src_list" | grep -qx 'ssd/k3s-etcd' || { echo >&2 "ssd/k3s-etcd missing from SRC_LIST"; exit 1; }

# SRC_LIST roots must be filesystems: the -R receives apply -o mountpoint/canmount
# unconditionally, which ZFS rejects on a zvol. No ZFS here to probe the type, so
# the roots are pinned to an allow-list a human extends after confirming the type.
expected_roots="tank/share tank/backups tank/nextcloud-data tank/proxmox tank/immich-data ssd/appdata ssd/databases ssd/k3s-etcd"
while IFS= read -r root; do
  [ -n "$root" ] || continue
  case " $expected_roots " in
    *" $root "*) ;;
    *) echo >&2 "SRC_LIST root '$root' not in the known-filesystem allow-list — confirm it is a filesystem (not a zvol; see the SRC_LIST invariant) before adding it here"; exit 1 ;;
  esac
done <<< "$src_list"

# Retention is rendered from the role defaults; an unset/empty var would emit a
# bare `KEEP_RECENT=` and prune every snapshot on the first run.
grep -qE '^KEEP_RECENT=[0-9]+$' "$s" || { echo >&2 "KEEP_RECENT did not render as an integer"; exit 1; }
grep -qE '^KEEP_MONTHLY=[0-9]+$' "$s" || { echo >&2 "KEEP_MONTHLY did not render as an integer"; exit 1; }

# Restore labels are basenames, so they must be unique across SRC_LIST. The
# script's own startup check cannot run in this ZFS-less container.
dup="$(printf '%s\n' "$bn" | sort | uniq -d)"
[ -z "$dup" ] || { echo >&2 "duplicate SRC_LIST basenames (ambiguous restore/backup target): $dup"; exit 1; }

# Re-seed type->arm coupling. Scope the pins to the re-seed loop with comments
# stripped, so a stray comment carrying a pinned token cannot mask a reverted
# statement.
reseed_loop="$(awk '/while IFS= read -r snap; do/{f=1} f; /done <<< "\$snap_list"/{f=0}' "$s" \
  | grep -vE '^[[:space:]]*#' | sed -E 's/[[:space:];]#.*$//')"
# The 2>/dev/null || true must stay: it routes a get error to the fail-loud else
# rather than to set -e.
printf '%s\n' "$reseed_loop" | grep -qF 'dtype="$(zfs get -H -o value type "$ds" 2>/dev/null || true)"' \
  || { echo >&2 "guarded dtype capture not found in re-seed loop"; exit 1; }
awk '/\[\[ "\$dtype" == "volume" \]\]; then/{f=1} f && /^[[:space:]]*recv_opts=/{print; exit}' "$s" \
  | grep -qF 'recv_opts=( -o readonly=on )' || { echo >&2 "volume arm not coupled to readonly-only"; exit 1; }
awk '/\[\[ "\$dtype" == "filesystem" \]\]; then/{f=1} f && /^[[:space:]]*recv_opts=/{print; exit}' "$s" \
  | grep -qF 'recv_opts=( "${RECV_SAFE_OPTS[@]}" )' || { echo >&2 "filesystem arm not coupled to RECV_SAFE_OPTS"; exit 1; }
printf '%s\n' "$reseed_loop" | grep -qF 'Re-seed aborted: cannot determine type of' \
  || { echo >&2 "unknown-type abort missing from re-seed loop"; exit 1; }
# The receive must CONSUME recv_opts, not a fixed opt set.
printf '%s\n' "$reseed_loop" | grep -qF '| zfs receive -s -u "${recv_opts[@]}" "$sub"' \
  || { echo >&2 "in-loop re-seed receive not coupled to recv_opts"; exit 1; }

# The vzdump-quiesce timeout must exit 75 and cmd_run must branch on it
# separately from real failures.
awk '/log "DEFER \$\{src\}/{f=1} f && /return/{print; exit}' "$s" \
  | grep -qF 'return 75' || { echo >&2 "vzdump-quiesce timeout does not return 75 (DEFER)"; exit 1; }
grep -qF 'elif [[ "$rc" -eq 75 ]]; then' "$s" \
  || { echo >&2 "cmd_run does not branch on the DEFER exit code 75"; exit 1; }
# Missing-source SKIP must exit 73, not 0 — an rc-0 SKIP would refresh the
# dataset's last-success timestamp for a vanished source.
grep -qF 'return 73; }' "$s" \
  || { echo >&2 "missing-source SKIP does not return 73"; exit 1; }
grep -qF 'elif [[ "$rc" -eq 73 ]]; then' "$s" \
  || { echo >&2 "cmd_run does not branch on the missing-source exit code 73"; exit 1; }
# A replicated-but-unpruned dataset must exit 76: without it a retention failure
# reaches the parent only through the marker file, which no exit code guarantees.
grep -qF 'exit 76' "$s" \
  || { echo >&2 "cmd_run_one does not exit 76 when pruning failed"; exit 1; }
grep -qF 'elif [[ "$rc" -eq 76 ]]; then' "$s" \
  || { echo >&2 "cmd_run does not branch on the prune-failure exit code 76"; exit 1; }

# Per-dataset metric contract: emitted via the shared gauge helper, filtered to
# current SRC_LIST members, and seeded from the previous run. Behaviour is
# exercised by archive-metrics-behavior.sh; these pins catch a wholesale removal.
grep -qF '_emit_dataset_gauge archive_backup_dataset_last_success_timestamp_seconds' "$s" \
  || { echo >&2 "per-dataset last-success metric not emitted"; exit 1; }
grep -qF '_emit_dataset_gauge archive_backup_dataset_deferred_runs' "$s" \
  || { echo >&2 "per-dataset deferred-runs metric not emitted"; exit 1; }
awk '/_emit_dataset_gauge\(\) \{/{f=1} f && /MAP\[\$ds\]/{print; exit}' "$s" \
  | grep -q 'continue' || { echo >&2 "emit helper lost its SRC_LIST (MAP) orphan filter"; exit 1; }
[ "$(grep -c '_load_prev_dataset_metrics' "$s")" -ge 2 ] \
  || { echo >&2 "_load_prev_dataset_metrics not defined+called (previous-run seeding lost)"; exit 1; }

echo "archive-backupctl contract OK"
