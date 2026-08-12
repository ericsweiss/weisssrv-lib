#!/usr/bin/env bash
# Molecule contract check for the rendered restic-offsitectl. STRUCTURAL pins
# for what the ZFS-less/network-less container can't exercise: the zvol
# clone/mount/destroy lifecycle, the full subcommand surface, restic's
# backup/forget flags, and every metric name the alerts consume. Kept as a real
# *.sh (shellcheck-lintable; its quotes don't trip Ansible's arg splitter).
set -euo pipefail

s="${1:-/usr/local/sbin/restic-offsitectl}"
[ -f "$s" ] || { echo >&2 "restic-offsitectl not rendered at $s"; exit 1; }

# The REAL render must be valid bash (CI shellcheck only lints the neutralized template).
bash -n "$s"

# `-- "$1"` so a token starting with '-' (e.g. --exclude-file) is not parsed as
# a grep option.
need() { grep -qF -- "$1" "$s" || { echo >&2 "missing contract token: $1"; exit 1; }; }

# subcommand surface (main dispatch)
need 'run) cmd_run'
need 'restore) cmd_restore'
need 'verify) cmd_verify'
need 'drill) cmd_drill'
need 'snapshots) cmd_snapshots'
need 'prune) cmd_prune'
need 'unlock) cmd_unlock'
need 'status) cmd_status'

# restic backup + retention flags
need '--exclude-file'
need '--exclude-caches'
need '--tag nightly'
need '--one-file-system=false'
need '--keep-daily'
need '--keep-weekly'
need '--keep-monthly'
need '--keep-yearly'
# Floor + pinned retention grouping. Without --keep-last, corruption that
# persists a few days walks every daily restore point out of a bucket with no
# Object Lock; without a pinned --group-by, changing the source list forks a new
# retention group and strands the old group's snapshots.
need '--keep-last'
need '--group-by'
need '--prune'
# Blast-radius ceiling: the destructive forget must be preceded by a --dry-run
# whose delete-set size is compared against FORGET_MAX_REMOVE, so a keep-policy
# or --group-by change cannot walk history out of the repository before anyone
# reads a snapshot list.
need 'FORGET_MAX_REMOVE'
need '--dry-run'
need 'REFUSING to prune'
# First-run idempotent init (the `cat config` probe itself is pinned as a
# --no-lock call in the repository-lock block below).
need 'repo_init_if_needed'
need 'restic init'

# Repository-lock handling: an interrupted run leaves a lock that wedges every
# exclusive operation (forget/prune, check) until it is reaped. The reaper's own
# probes MUST be --no-lock: restic takes a read lock even for `cat`/`list` and
# does not ignore stale locks while acquiring, so a locking probe would wait out
# --retry-lock and never reap the very lock it was called for.
need '--retry-lock'
need 'reap_stale_locks'
need '--no-lock'
need 'restic_ro list locks'
need 'restic_ro cat lock'
need 'restic_ro cat config'
need 'restic unlock'

# The ceiling refusal must NOT reuse a code restic itself can exit with (2 is
# restic's "go runtime error"), or a crashed prune reads as a deliberate refusal
# and the nightly unit reports healthy.
need 'FORGET_RC_CEILING=90'

# freshness guard (never upload a stale tree)
need 'FRESH_MAX_AGE_H'
need 'snap_age_seconds'
need 'stale-source'

# already-uploaded short-circuit: both triggers (the archive job's OnSuccess=
# and the fallback timer) fire nightly, so the second one must skip. The skip is
# conditional on every source being PRESENT and FRESH — dropping that condition
# would silently skip a total snapshot failure instead of aborting loudly.
need 'source_snap_epochs'
need 'last_backup_epoch'
need 'already-uploaded'
need '--force'

# zvol clone/mount/destroy lifecycle + EXIT-trap cleanup
# These MUST survive even though ZVOL_SOURCES is empty in molecule: reverting the
# clone/destroy/noload/EXIT-trap logic would strand clones on the real host.
need 'trap cleanup EXIT'
need 'zfs clone'
need 'zfs destroy -r'
need 'udevadm settle'
need '_CLONES_TO_DESTROY'
need '_ZVOL_MOUNTS_TO_UMOUNT'
# The in-loop mount must consume $fstype/$mopts (type/opts not hardcoded); the
# literal we search for intentionally contains unexpanded $-vars.
# shellcheck disable=SC2016
need 'mount -t "$fstype" -o "$mopts"'
need 'clone-failed'
need 'mount-failed'
# Stale-clone destroy must be gated on the dataset's ORIGIN pointing at our
# zvol — an unrelated dataset sharing the derived name must be refused, never
# recursively destroyed (data-loss guard).
need 'zfs get -H -o value origin'
need 'clone-conflict'

# Restore-drill sampling. The container has no repository to drill, so the
# selection rules are pinned structurally: a size floor (without it the sample
# is the estate's 1-byte marker files), per-source buckets drawn round-robin
# (without them one source is proven and the rest are not), and a coverage floor
# that fails the drill.
need 'DRILL_MIN_BYTES'
need 'DRILL_MIN_SOURCES'
# The literals below intentionally contain unexpanded $-vars.
# shellcheck disable=SC2016
need 'cand.${idx}'
need 'drill: sampled'
need 'yielded a sampled file, below the required'

# The clone name must derive from the FULL zvol path: two sources under one
# parent would otherwise collide and abort the nightly run.
# shellcheck disable=SC2016
need 'local clone="${zvol}-${ZVOL_CLONE_SUFFIX}"'

# single-instance lock
need 'flock -n 9'

# --- hide-only key contract: restic's default rclone.args carry
# --b2-hard-delete, which maps every delete (incl. the mid-backup lock
# refresh) to b2_delete_file_version — refused by the restricted key. The
# wrapper must strip it on every invocation.
need 'rclone.args="serve restic --stdio"'

# metric names (alert contract)
for m in \
  restic_offsite_last_run_success \
  restic_offsite_last_success_timestamp_seconds \
  restic_offsite_last_run_duration_seconds \
  restic_offsite_last_backup_success \
  restic_offsite_last_backup_timestamp_seconds \
  restic_offsite_last_prune_success \
  restic_offsite_retention_blocked \
  restic_offsite_retention_pending_removals \
  restic_offsite_repo_size_bytes \
  restic_offsite_snapshot_total_bytes \
  restic_offsite_last_verify_success \
  restic_offsite_last_verify_timestamp_seconds \
  restic_offsite_verify_group \
  backup_restore_drill_last_run_seconds \
  backup_restore_drill_last_success_seconds \
  backup_restore_drill_files_compared ; do
  need "$m"
done

echo "restic-offsitectl contract OK"
