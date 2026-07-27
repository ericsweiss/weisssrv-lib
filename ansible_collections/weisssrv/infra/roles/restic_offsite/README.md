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
| `restic_offsite_cache_dir` | **must sit on an encrypted dataset**: the cache holds repository PATHS in plaintext even though the blobs are client-encrypted |
| `restic_offsite_sources` | `[{name, mountpoint}]`, empty by default |
| `restic_offsite_zvol_sources` | `[{name, zvol, fstype, mount_opts}]`, empty by default |
| `restic_offsite_excludes` | restic patterns against `<bind_root>/<source>/…`, empty by default |
| `restic_offsite_repo_password` | restic repository password |
| `restic_offsite_b2_key_id` / `restic_offsite_b2_application_key` | rclone B2 credentials |

## How it reads a consistent snapshot

restic never reads the live datasets. For each source it binds the newest
`archsync-*` snapshot (created by `archive-backupctl`) at a **stable path**
(`/mnt/restic-src/<name>`) so restic's parent-snapshot optimization re-reads
only changed files instead of re-hashing ~1 TB nightly:

- **File-walkable datasets** (`restic_offsite_sources`) — `mount --bind -o ro`
  the `.zfs/snapshot/<prefix>-*` subtree.
- **File-bearing data zvols** (`restic_offsite_zvol_sources`) — a file walk
  can't see a live zvol, so the control script **clones** the newest snapshot
  to a throwaway
  sibling zvol and mounts its ext4 read-only (`ro,noload`) at
  `/run/restic-offsite/<name>`. An **EXIT trap** unmounts + destroys every clone
  so a crashed run never strands one.

A **freshness guard** aborts the run (metric `success=0`, no upload) if any
source's newest snapshot is older than `restic_offsite_freshness_max_age_h`
(default 26h) — B2 must never upload a stale tree.

## What is NOT offsited

Whatever `restic_offsite_sources` omits and `restic_offsite_excludes` filters.
Typical omissions: hypervisor image dumps (poor dedup, huge, already covered by
the local archive), bulk non-sensitive media, and metrics/log stores with their
own retention. A dataset with zvol-backed children appears in the file walk as
empty mountpoint dirs — back those up via logical dumps that land inside a
walked source.

## Control script — `restic-offsitectl`

`run` (timer/OnSuccess target), `restore <name> [snap] [dir]`,
`verify [--full]` (`restic check`), `snapshots`, `prune`, `status`. Single-
instance `flock`; run/restore/verify/prune share the lock.

## Metrics (node_exporter textfile)

`/var/lib/node_exporter/restic_offsite.prom`:
`restic_offsite_last_run_success`, `restic_offsite_last_success_timestamp_seconds`
(preserved on failure), `restic_offsite_last_run_duration_seconds`,
`restic_offsite_repo_size_bytes`, `restic_offsite_snapshot_total_bytes`.
`restic_offsite_verify.prom`: `restic_offsite_last_verify_success`,
`restic_offsite_last_verify_timestamp_seconds`. Alerts `ResticOffsiteFailed` /
`ResticOffsiteStale` live in the kube-prometheus-stack rules.

## Security (three independent at-rest layers)

1. Local dataset encryption on the source pool (`aes-256-gcm`).
2. Archive replication (raw `zfs send -w` — encrypted-at-rest blobs, no key).
3. Offsite: B2 holds **restic client-side ciphertext** (repo password =
   `restic_offsite_repo_password`); server-side encryption is a redundant extra.
   rclone deletes by *hiding*, and a bucket lifecycle rule expires hidden
   versions, so a capability-restricted key (no `deleteFiles`) still prunes.

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
installed-version probe vacuously true and silently skip the pinned install.

## Molecule

Hermetic: a **local restic file repo** (no B2/network), `bind_mode: bind`
against a fake `.zfs/snapshot` tree, and no zvol sources (no ZFS in the
container). Exercises a real `run` (freshness guard →
backup → metrics), the stale-source abort (`success=0`), and statically pins the
zvol-clone / subcommand / restic-flag / metric-name contract.
