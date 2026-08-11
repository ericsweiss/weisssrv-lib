# restic_offsite

Nightly **offsite** backup to **Backblaze B2** using **restic** (client-side
encryption) over **rclone**. Intended to run on the storage host, chained
`OnSuccess=` after the local archive replication so the offsite copy is a
consistent point-in-time that a known-good local replication just produced.

Companion to `weisssrv.infra.nas_storage`'s `archive-backupctl` (local
pool-to-pool ZFS replication, raw `zfs send -w`): that is the *local* DR copy;
this is the *offsite* one.

## Required inputs

Everything describing WHAT to back up is site data and has no safe default —
the role asserts the first two when enabled:

| Variable | Meaning |
|---|---|
| `restic_offsite_repo` | e.g. `rclone:b2:<bucket>/<path>` |
| `restic_offsite_cache_dir` | **must sit on an encrypted dataset**, and outside any snapshotted or replicated one — see below |
| `restic_offsite_sources` | `[{name, mountpoint}]`, empty by default |
| `restic_offsite_zvol_sources` | `[{name, zvol, fstype, mount_opts}]`, empty by default |
| `restic_offsite_excludes` | restic patterns against `<bind_root>/<source>/…`, empty by default |
| `restic_offsite_repo_password` | restic repository password |
| `restic_offsite_b2_key_id` / `restic_offsite_b2_application_key` | rclone B2 credentials |

### Where the cache goes

`restic_offsite_cache_dir` has two independent constraints:

- **Encrypted.** The cache holds the repository index — including file
  **paths** — in plaintext, even though every blob in B2 is client-encrypted.
- **Not snapshotted, not replicated.** It is high-churn, regenerable, and worth
  nothing in a restore, so snapshotting it just pins dead space and replicating
  it just ships it over and over.

The second constraint is about **ZFS**, and `--exclude-caches` does not satisfy
it: restic's `CACHEDIR.TAG` keeps the cache out of the restic *upload*, and has
no bearing on `zfs-auto-snapshot` or on a `zfs send` of the dataset it sits in.

Two ways to satisfy it, and which one is available depends on the layout:

- If the cache can be **excluded from the replication source list**, do that —
  no new dataset, no move.
- If replication is a raw `zfs send -w` of the parent dataset, exclusion is not
  expressible (a raw send is dataset-granular — a directory inside it cannot be
  left out), so the cache has to move to its own dataset with
  `com.sun:auto-snapshot=false` that the source list omits. Relocating needs no
  migration beyond deleting the old directory: restic rebuilds the cache.
- If the send is **recursive** (`-R`) and every encrypted dataset is inside a
  replicated encryption root, neither form is available without minting a new
  encryption root — disproportionate for a regenerable cache. Keep the cache
  inside the encrypted root and accept the replication churn as a documented
  exception: the encryption constraint is the hard one, this one is only waste.

## How it reads a consistent snapshot

restic never reads the live datasets. For each source it binds the newest
`<prefix>-*` snapshot (created by the archive job) at a **stable path**
(`<restic_offsite_bind_root>/<name>`) so restic's parent-snapshot optimization
re-reads only changed files instead of re-hashing the whole estate:

- **File-walkable datasets** (`restic_offsite_sources`) — `mount --bind -o ro`
  the `.zfs/snapshot/<prefix>-*` subtree.
- **File-bearing data zvols** (`restic_offsite_zvol_sources`) — a file walk
  can't see a live zvol, so the control script **clones** the newest snapshot to
  a throwaway sibling zvol and mounts its filesystem read-only (`ro,noload`) at
  `<restic_offsite_zvol_mount_root>/<name>`. An **EXIT trap** unmounts +
  destroys every clone so a crashed run never strands one. A pre-existing
  dataset with the derived clone name is destroyed **only** if its `origin`
  proves it is our clone; anything else aborts the run.

A **freshness guard** aborts the run (metric `success=0`, no upload) if any
source's newest snapshot is older than `restic_offsite_freshness_max_age_h`
(default 26h) — the offsite copy must never be a stale tree.

## What is NOT offsited

Whatever `restic_offsite_sources` omits and `restic_offsite_excludes` filters.
Typical omissions: hypervisor image dumps (poor dedup, huge, already covered by
the local archive), bulk non-sensitive media, and metrics/log stores with their
own retention. A dataset with zvol-backed children appears in the file walk as
empty mountpoint dirs — back those up via logical dumps that land inside a
walked source.

## Control script — `restic-offsitectl`

`run [--force]` (timer/OnSuccess target), `restore <name> [snap] [dir]`,
`verify [--full|--auto-subset]`, `drill`, `snapshots`,
`prune [--max-remove N]`, `unlock`, `status`. Single-instance `flock`; every
subcommand shares the lock.

`run` carries two guards:

- **Freshness** — refuses to upload a tree whose newest snapshot is older than
  `restic_offsite_freshness_max_age_h` (aborts, `success=0`).
- **Already-uploaded** — skips entirely when the last successful *backup*
  already covers the newest source snapshot AND every source is present and
  fresh. Both triggers (the archive job's `OnSuccess=` and the fallback timer)
  fire every night; without the skip the job runs twice. The
  present-and-fresh condition keeps a total snapshot failure falling through to
  the loud freshness abort instead of being silently skipped. `--force`
  overrides this guard only.

### Retention

`forget` runs the same GFS flags as `prune` (`--keep-last`, daily/weekly/
monthly/yearly, plus a pinned `--group-by`) so the two can never diverge. Before
the destructive pass it dry-runs the identical policy, counts the delete set,
and **refuses** when that exceeds `restic_offsite_forget_max_remove` — a
blast-radius bound for a bucket with no Object Lock, where a forget is
unrecoverable past the hide-lifecycle window. If restic's forget summary no
longer parses at all, the guard refuses rather than degrading to "no ceiling".

A refusal is **not** a backup failure: the snapshot already landed, so the run
still succeeds and records `restic_offsite_retention_blocked 1` with
`restic_offsite_retention_pending_removals`. Clearing it is a deliberate act —
`restic-offsitectl prune --max-remove <N>` after reviewing
`restic-offsitectl snapshots`. The ceiling is intentionally absolute rather than
self-raising; alert on the blocked/pending gauges so a wedged retention is
visible within a day or two.

The refusal exits **90**, not 2. `run_forget` also returns whatever
`restic forget --prune` exits with, and restic documents 2 as a go runtime
error — sharing the code made a *crashed* prune indistinguishable from a
deliberate refusal, so the run recorded `retention_blocked 1`, reported success
and exited 0. Now only 90 is the refusal:

| `run_forget` | `_last_prune_success` | `_retention_blocked` | `_last_run_success` | unit |
|---|:-:|:-:|:-:|:-:|
| `0` — pruned | 1 | 0 | 1 | ok |
| `90` — ceiling refusal | 0 | 1 | 1 | ok |
| `1` — dry-run unusable | 0 | 0 | 0 | **fails** |
| anything else — prune crashed | 0 | 0 | 0 | **fails** |

So `retention_blocked` means a ceiling refusal and nothing else, and a crashed
prune reaches the operator as a failed run rather than as a "raise the ceiling"
suggestion for something the ceiling had nothing to do with.

### Repository locks

An interrupted run leaves a repository lock that wedges every exclusive
operation — `forget`/`prune` and `check` — indefinitely, while plain backups
(shared lock) keep succeeding. Two mitigations: every restic invocation carries
`--retry-lock` (`restic_offsite_retry_lock`), and a pre-flight reaper removes a
lock owned by a **dead PID on this host** that is older than
`restic_offsite_stale_lock_min_age_h`, logging loudly when it does.
`restic-offsitectl unlock` runs the same reaper on demand. rc=11 from restic is
reported as "repository lock" so the journal line is actionable.

The reaper's own probes go through a separate `--no-lock`, no-`--retry-lock`
wrapper (`restic_ro`: `list locks`, `cat lock`, `cat config`, and the read-only
`snapshots`/`stats` in `status`). restic opens the repository with a read lock
even for `cat`, and it does not ignore stale locks while acquiring — so with a
locking probe the exact scenario the reaper exists for, a stale **exclusive**
lock, made `cat lock` wait out the full `--retry-lock` and then fail, the loop
skipped every lock, and nothing was ever reaped. `restic unlock` itself still
goes through the normal wrapper.

### Deep verify

`verify --auto-subset` read-verifies one pack group per run, so the whole repo
is re-read against bit-rot every `restic_offsite_verify_groups` runs at a
fraction of the egress of a full `--read-data`. The group cursor is **persisted**
in the verify metrics file and advances only on success (`next = last % N + 1`),
so a failed or skipped week is retried instead of being dropped for a full
cycle.

### Restore drill

`restic check` proves the repository is internally consistent. It does **not**
prove this host can still get bytes out of it: a rotated repo password, a broken
env file or a restore path that no longer maps all pass `check` and fail on the
day they are needed. `restic-offsitectl drill` — wired to a quarterly
`backup-restore-drill.timer` (`Persistent=true`, so a missed quarter catches up
instead of silently lapsing) — closes that gap:

1. Take the newest snapshot's file list and sort it by size ascending.
2. Sample the smallest files whose comparand on this host **predates the
   snapshot**, up to `restic_offsite_restore_drill_sample_files` and a hard
   `restic_offsite_restore_drill_max_bytes` cap.
3. Restore just those into a temp dir and `cmp` them against the ZFS snapshot
   subtree they were taken from — immutable, so any difference is corruption,
   not churn. (The only tolerated difference is a comparand rewritten *during*
   the drill, detected by an mtime re-read.)

It is deliberately tiny: B2 egress is billed and volume proves nothing extra
here — repo-wide bit-rot is the rotating deep verify's job. A mismatch, a failed
restore, or a run that could sample **nothing** all fail the unit and leave
`backup_restore_drill_last_success_seconds` at its previous value. Set
`restic_offsite_restore_drill_enabled: false` to drop the units (they are
removed, not just left unstarted).

Only file sources are drillable: a zvol source's filesystem is mounted only
during a run, so there is nothing to compare against between runs.

## Metrics (node_exporter textfile)

`restic_offsite.prom`:

| Metric | Meaning |
|---|---|
| `restic_offsite_last_backup_success` / `_last_backup_timestamp_seconds` | did the upload land (written immediately after `restic backup` returns 0) |
| `restic_offsite_last_run_success` / `restic_offsite_last_success_timestamp_seconds` | did the whole run complete without error |
| `restic_offsite_last_prune_success` | did retention apply (preserved when the run never reached the prune stage) |
| `restic_offsite_retention_blocked` / `_retention_pending_removals` | ceiling refusal + the pending delete-set size |
| `restic_offsite_last_run_duration_seconds` | run duration |
| `restic_offsite_repo_size_bytes` / `_snapshot_total_bytes` | repo raw-data size / latest snapshot size |

`restic_offsite_verify.prom`: `restic_offsite_last_verify_success`,
`restic_offsite_last_verify_timestamp_seconds`, `restic_offsite_verify_group`,
`restic_offsite_verify_groups`.

`backup_restore_drill.prom`:

| Metric | Meaning |
|---|---|
| `backup_restore_drill_last_run_seconds` | last drill ATTEMPT (advances on failure too) |
| `backup_restore_drill_last_success_seconds` | last drill in which every compared file matched — the one a staleness alert (~100 days, one quarter plus slack) should read |
| `backup_restore_drill_files_compared` | files byte-compared in the last run; `0` means nothing was proven and the unit failed |

Timestamps are preserved across a failed attempt, so staleness alerts measure
time-since-last-success. Alert the backup pair for "the offsite tier is down"
and the retention/verify/drill metrics separately — conflating them turns a
retention decision into a data-loss page.

## Security (three independent at-rest layers)

1. Local dataset encryption on the source pool (`aes-256-gcm`).
2. Archive replication (raw `zfs send -w` — encrypted-at-rest blobs, no key).
3. Offsite: B2 holds **restic client-side ciphertext** (repo password =
   `restic_offsite_repo_password`); server-side encryption is a redundant extra.
   rclone deletes by *hiding*, and a bucket lifecycle rule expires hidden
   versions, so a capability-restricted key (no `deleteFiles`) still prunes.
   restic's rclone backend otherwise injects `--b2-hard-delete`, which such a
   key refuses — the control script strips it on every invocation.

## Secrets

`restic_offsite_b2_key_id`, `restic_offsite_b2_application_key` and
`restic_offsite_repo_password` are injected by the caller from its secret store.
The env file (`RESTIC_PASSWORD`) and `rclone.conf` (B2 key) render `0600` with
`no_log`, and values containing a single quote or backslash are rejected before
render — systemd's EnvironmentFile parser and shell `source` unescape those
differently, which would silently produce two different passwords.

## Install / versions

`restic` comes from the Debian archive; `restic_offsite_restic_version` is an
**advisory** apt pin (empty = track the distro).

`rclone` does NOT: Debian ships a years-old build, so the role installs
rclone.org's official `.deb` at `restic_offsite_rclone_version`, verified
against `restic_offsite_rclone_deb_sha256`. Both are asserted to be a semantic
version + a 64-hex sha256 before the download — an empty version would make the
installed-version probe vacuously true and silently skip the pinned install. The
downloaded `.deb` is removed again once installed, and any stray
`/usr/local/bin/rclone` shadowing the packaged binary is deleted.

## Molecule

Hermetic: a **local restic file repo** (no B2/network), `bind_mode: bind`
against a fake `.zfs/snapshot` tree, and no zvol sources (no ZFS in the
container). Exercises a real `run` (freshness guard → backup → metrics), the
already-uploaded skip and its `--force` override, a restore round-trip, the
rotating deep-verify cursor, and the stale-source abort (`success=0`).
`files/restic-offsite-metrics-behavior.sh` executes the metric, retention-guard
and stale-lock logic against a stubbed `restic`;
`files/restic-offsite-contract-assert.sh` statically pins the zvol-clone /
subcommand / restic-flag / metric-name contract the container cannot run.
