#!/usr/bin/env bash
# Molecule contract check for the rendered archive-backupctl, kept as a real
# *.sh file so it is shellcheck-lintable. Structural only: the container has no
# ZFS, so this pins that inventory reached SRC_LIST intact and the derivations.
set -euo pipefail

s="${1:-/usr/local/sbin/archive-backupctl}"
expected_file="${2:-}"
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

# Round-trip against the inventory the caller rendered from: a dropped, added or
# mangled entry fails, and no dataset name is hard-coded in this library test.
if [ -n "$expected_file" ]; then
  [ -f "$expected_file" ] || { echo >&2 "expected-source list $expected_file is missing"; exit 1; }
  if ! diff -u <(grep -v '^[[:space:]]*$' "$expected_file" | sort -u) \
               <(printf '%s\n' "$src_list" | sort -u); then
    echo >&2 "SRC_LIST does not match the inventory it was rendered from"
    exit 1
  fi
fi

# Retention is rendered from the role defaults; an unset/empty var would emit a
# bare `KEEP_RECENT=` and prune every snapshot on the first run.
grep -qE '^KEEP_RECENT=[0-9]+$' "$s" || { echo >&2 "KEEP_RECENT did not render as an integer"; exit 1; }
grep -qE '^KEEP_MONTHLY=[0-9]+$' "$s" || { echo >&2 "KEEP_MONTHLY did not render as an integer"; exit 1; }

# Restore labels are basenames, so they must be unique across SRC_LIST. The
# script's own startup check cannot run in this ZFS-less container.
dup="$(printf '%s\n' "$bn" | sort | uniq -d)"
[ -z "$dup" ] || { echo >&2 "duplicate SRC_LIST basenames (ambiguous restore/backup target): $dup"; exit 1; }

# Re-seed type->arm coupling. The pins are scoped to the re-seed loop with
# comments stripped, so a stray comment carrying a pinned token cannot mask a
# reverted statement.
reseed_loop="$(awk '/while IFS= read -r snap; do/{f=1} f; /done <<< "\$snap_list"/{f=0}' "$s" \
  | grep -vE '^[[:space:]]*#' | sed -E 's/[[:space:];]#.*$//')"
# The `|| true` must stay: it routes a get error to the fail-loud else rather
# than to set -e.
printf '%s\n' "$reseed_loop" | grep -Eq 'dtype=.*zfs get +-H +-o +value +type .*\|\| true' \
  || { echo >&2 "guarded dtype capture not found in re-seed loop"; exit 1; }
awk '/\[\[ "\$dtype" == "volume" \]\]; then/{f=1} f && /^[[:space:]]*recv_opts=/{print; exit}' "$s" \
  | grep -qF 'recv_opts=( -o readonly=on )' || { echo >&2 "volume arm not coupled to readonly-only"; exit 1; }
awk '/\[\[ "\$dtype" == "filesystem" \]\]; then/{f=1} f && /^[[:space:]]*recv_opts=/{print; exit}' "$s" \
  | grep -qF 'recv_opts=( "${RECV_SAFE_OPTS[@]}" )' || { echo >&2 "filesystem arm not coupled to RECV_SAFE_OPTS"; exit 1; }
printf '%s\n' "$reseed_loop" | grep -qF 'Re-seed aborted: cannot determine type of' \
  || { echo >&2 "unknown-type abort missing from re-seed loop"; exit 1; }
# The receive must CONSUME recv_opts, and keep -s (resumable, so the token path
# can resume) and -u (received datasets stay unmounted).
printf '%s\n' "$reseed_loop" | grep -Eq 'zfs receive +-s +-u +"\$\{recv_opts\[@\]\}"' \
  || { echo >&2 "in-loop re-seed receive not \`zfs receive -s -u \"\${recv_opts[@]}\"\`"; exit 1; }

# Exit-code contract between the per-dataset child and cmd_run: each code means
# something different to the metrics, so a collapsed branch is a real defect.
awk '/log "DEFER \$\{src\}/{f=1} f && /return/{print; exit}' "$s" \
  | grep -qF 'return 75' || { echo >&2 "vzdump-quiesce timeout does not return 75 (DEFER)"; exit 1; }
grep -Eq 'rc" -eq 75' "$s" \
  || { echo >&2 "cmd_run does not branch on the DEFER exit code 75"; exit 1; }
# Missing-source SKIP must exit 73, not 0 — an rc-0 SKIP would refresh the
# dataset's last-success timestamp for a vanished source.
grep -qF 'return 73; }' "$s" \
  || { echo >&2 "missing-source SKIP does not return 73"; exit 1; }
grep -Eq 'rc" -eq 73' "$s" \
  || { echo >&2 "cmd_run does not branch on the missing-source exit code 73"; exit 1; }
# A replicated-but-unpruned dataset must exit 76: without it a retention failure
# reaches the parent only through the marker file, which no exit code guarantees.
grep -qF 'exit 76' "$s" \
  || { echo >&2 "cmd_run_one does not exit 76 when pruning failed"; exit 1; }
grep -Eq 'rc" -eq 76' "$s" \
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
