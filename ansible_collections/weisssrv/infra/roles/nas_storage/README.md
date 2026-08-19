# weisssrv.infra.nas_storage

Configures a ZFS NAS host: dataset properties and scrub timers, NFS exports,
Samba shares, a MergerFS union of a hot and a bulk media tier, a media mover, a
nightly swap reset, SMART monitoring, ZFS replication to a detachable archive
pool, a Proxmox cluster-config archive, and a backup-artifact collector.

**It never creates or destroys ZFS pools, and never creates datasets** — those
are manual. The role verifies the declared datasets exist and enforces their
properties; a missing one fails the deploy.

## What this role manages

### ZFS
- Dataset property enforcement (compare-then-set, so `changed` means changed).
  Property values in `nas_storage_zfs_pools[].properties` must be written in the
  human-readable form `zfs get -H -o value` prints (`1M`, not `1048576`).
- Scrub timers (`zfs-scrub-<schedule>@<pool>.timer`) and `zfs-auto-snapshot`.
- Optional ARC cap: `nas_storage_zfs_arc_max_bytes` is handed to
  `weisssrv.infra.zfs_arc_cap`, which renders `/etc/modprobe.d/zfs.conf` and
  rebuilds the initramfs so the cap applies at early boot before the pools
  import, and writes `/sys/module/zfs/parameters/zfs_arc_max` so a change takes
  effect immediately. Empty (the default) leaves ARC alone and the file
  unmanaged.
- Pools that are not imported are skipped, not failed — a detachable archive
  pool's normal state is exported.

### NFS
- Server packages, `/etc/exports` (see `nas_storage_exports` below), bind mounts
  from each dataset into the export tree, and a client-facing readiness probe on
  port 2049 (`nfs-server.service` is `oneshot`, so its unit state proves nothing).
- **Mounted-dataset guard**: every ZFS-backed `bind_source` must be a mountpoint
  before the role touches it. Without it, an unmounted or key-locked dataset
  leaves a bare root-filesystem directory that would be created, bound and then
  exported as if it held data. `bind_source_check` points the guard at the
  parent dataset when the bind source is a subdirectory of one.
- **Which sources are ZFS-backed** is `nas_storage_zfs_mount_roots` (default
  `/mnt/tank`, `/mnt/ssd`, `/mnt/nvme`) — a source *inside* one of those roots
  (not the root itself) gets the guard above *and* the
  `x-systemd.requires=zfs-mount*` boot ordering on its fstab entry; anything else
  gets a plain bind with neither. An empty roots list classifies **nothing** as
  ZFS-backed.
- **`zfs:` overrides that derivation** per export (and per MergerFS union), in
  both directions: `zfs: true` applies the guard and the ordering to a source the
  roots do not cover, `zfs: false` declares a plain bind deliberately. Both
  protections otherwise fail open, so the role asserts up front that every
  export's `bind_source` is under a root, a declared MergerFS target, or carries
  a `zfs:` key — a source on a differently named pool is then a deploy failure
  rather than a silently unguarded export. The value must be an actual
  **boolean**: it is consumed through `| bool`, which maps anything else to
  `false` instead of erroring, so `zfs: ""` (a var that rendered empty) or
  `zfs: "maybe"` would classify the source as non-ZFS rather than fail.
- **nfsd ordering drop-in**: when any export's bind source is listed in
  `nas_storage_encrypted_bind_sources`, nfsd is ordered after
  `zfs-mount-encrypted.service` and given `RequiresMountsFor` on those binds.
  nfsd is a single daemon, so this delays plaintext exports too — an accepted
  trade against serving encrypted exports off empty mountpoints.
- Per-app subdirectories under the appdata export (`nas_storage_appdata_dirs`),
  owned `nas_storage_appdata_owner`:`nas_storage_appdata_group` to match the
  export's `all_squash,anonuid=…,anongid=…`, created only after the guard above.

### Samba
Server packages, the `nas` user, and one share per `nas_storage_samba_shares`
entry. SMB3 with `smb encrypt = required` on every connection, and smbd bound
to 445 only (`smb ports`, `disable netbios`) so the encrypted-only posture holds
at the socket instead of relying on the firewall to close 139. The password
comes from `nas_storage_samba_password` — which defaults to the
`SAMBA_NAS_PASSWORD` environment variable — is never passed on argv, and is
reset only on a confirmed auth failure; a transport error is ridden out.

### MergerFS
Unions a hot tier and a bulk tier at one mountpoint, which is then bind-mounted
into the export tree. A union with any branch *inside*
`nas_storage_zfs_mount_roots` gets `nofail` plus an explicit
`zfs-mount.service` anchor — the branches have no `.mount` units of their own,
so `requires-mounts-for` alone would order against nothing. That anchor is
derived from the **branch set**, so it does not depend on `systemd_requires`;
that key contributes only its own `requires-mounts-for` fan-out (and `nofail`
for a non-ZFS union). A branch **equal to**
a mount root (`/mnt/tank` rather than `/mnt/tank/media`) does **not** count as
ZFS-backed; such a union must carry `zfs: true` (or get its own root added), and
the same up-front assert — which checks **every** union, `systemd_requires` or
not — fails the play otherwise rather than shipping an unanchored union that
races the ZFS mounts at boot. `zfs:` must be a **boolean**: it is consumed
through `| bool`, so a value typo would otherwise classify the union non-ZFS
silently, and the assert rejects a non-boolean regardless of which branch
classified the source.

Deriving the anchor from the branch set rewrites two fstab shapes on the first
converge after adoption: a ZFS-branched union that omitted `systemd_requires`
**gains** `nofail` and the anchor, and a union classified **not** ZFS-backed
that declares `systemd_requires` **loses** the anchor pair it used to get
unconditionally, keeping `nofail` and its `requires-mounts-for` entries.

MergerFS options are **not verifiable at runtime**: FUSE exposes only generic
options in `/proc/mounts`, xattrs expose a few (minfreespace, policies), and the
mount-time-only ones (`inodecalc`, `noforget`, `use_ino`, `cache.files`) are
invisible once mounted. fstab is therefore authoritative — correct fstab plus a
live mount is taken as proof the options are applied, and any option change
requires a full remount cycle.

That cycle is: unexport the MergerFS-backed exports (scoped to
`<client>:<path>`, never `exportfs -u -a` — the other exports on this server keep
serving), unmount the binds sourced from the target, unmount and remount
MergerFS, remount the binds, `exportfs -a`. It runs only when the mount is idle:
no established connections on port 2049 and no processes holding the mount.
Otherwise the role prints the manual sequence and leaves the mount alone, and
the new options apply at the next reboot (fstab is already correct). The gate is
global — one busy union blocks the whole cycle, because the unexport and unmount
tasks it guards loop over every union.

The two decisions in that cycle (does anything need a remount;
may it proceed and what must it unexport) live in
`tasks/mergerfs_needs_remount.yml` and `tasks/mergerfs_remount_gate.yml`. A
container has no FUSE, so the molecule scenario drives those two files from
fabricated probe results instead — no-change, fstab-changed, busy-process and
active-client — and asserts the unexport stays scoped to the MergerFS-backed
exports. The option string itself is derived in `tasks/mergerfs_opts.yml` for
the same reason, and the scenario asserts all four anchor shapes (ZFS-branched
and not, each with and without `systemd_requires`).

### Media mover
Timer-driven rsync from the hot tier to the bulk tier for files older than
`nas_storage_media_mover_min_age`. Deliberately deferential, because it shares
the nightly window with backups and scrubs: `nice` + `ionice` set absolute
priority, and the cgroup-v2 `CPUWeight`/`IOWeight` (both 20, below the 100
default) deprioritize it only under contention.

### Swap reset (swap-clean)
Nightly `swapoff -a`/`swapon -a` with ARC shrunk for headroom, for a
memory-tight host whose swap never self-clears. If ARC headroom alone cannot
cover the swap it escalates: gracefully stops as many guests as needed from
`nas_storage_swap_clean_stop_guests` (ordered), does the reclaim, and **always**
restarts every guest it stopped (a single EXIT trap). Guests are only ever
stopped gracefully; one that will not stop within its timeout aborts the reclaim
rather than being forced. Metrics land in `swap_clean.prom` on every exit path.

### Archive backup (archive-backupctl)
Nightly ZFS replication of `nas_storage_archive_backup_sources` into
`nas_storage_archive_backup_pool`, each landing at `<pool>/<basename>`, plus
plug/unplug/restore subcommands and the scrub-timer wiring.

- **Off by default.** The control script rewrites the destination pool's root
  properties (including `mountpoint=none`) and destroys its own snapshots there,
  so it must never run against a pool the site did not nominate. Enabling it
  without both the pool and a non-empty source list fails the role.
- Turning it **off converges**: the units, the schedule and the script are
  removed, so a host cannot be left firing a script the role no longer manages.
- Every source **root** must be a filesystem, not a zvol (zvols are fine as
  children). Basenames must be unique — they are the restore labels.
- `nas_storage_archive_backup_vzdump_target` names the one dataset receiving
  cluster-wide backup writes over NFS; it is snapshotted only after writes under
  its mountpoint quiesce, so a half-written image is never captured. On timeout
  that dataset is deferred to the next run (exit 75) rather than failed. Empty
  disables the guard.
- Retention per dataset: the newest `nas_storage_archive_backup_keep_recent`
  snapshots, plus the newest of each of the last
  `nas_storage_archive_backup_keep_monthly` calendar months. A failed
  `zfs destroy` does not fail the run — replication still succeeded — but it
  sets `archive_backup_last_prune_success 0`, so blocked retention is visible
  before the pool fills. The per-dataset child carries that back as exit 76
  (replicated, not pruned), with a marker file as the fallback for a child that
  dies before returning; the marker is consumed only once the textfile is
  published, so a failed write leaves it for the next run instead of dropping
  the failure it carries.
- **Raw re-seed**: a raw (`-w`) stream cannot continue a base that was received
  non-raw, so a source that has since been encrypted gets a one-time re-seed.
  It sends only the current snapshot of each dataset — `-R` would ship the whole
  source snapshot history and can overflow the destination — into a temp
  sibling, and swaps it in only after a complete receive, so the previous copy
  survives an interrupted re-seed. Within that loop, filesystems receive with
  `-o mountpoint=none,canmount=off` and zvols with `-o readonly=on` only,
  because ZFS rejects a per-dataset mountpoint override on a volume.

### Proxmox cluster-config archive (opt-in)
`nas_storage_pve_cluster_backup_enabled` deploys a nightly tar of `/etc/pve`
into the landing zone. Guest backups do not capture pmxcfs, so users/ACLs/API
tokens, `corosync.conf` and `priv/` (cluster CA, node certs, authkey) otherwise
have no backup at all. The archive holds private key material, so its directory
and every archive are root-only (0700/0600) and it has no NFS export.

Both ends fail closed: it refuses to run when `/etc/pve` is not a mounted pmxcfs
(a stopped `pve-cluster.service` leaves an empty directory that tars into a
valid, tiny archive of nothing) and when the landing-zone dataset is not mounted
(which would write key material onto the root filesystem and report success) —
and in that second case it also stops sizing the archives already under the
unmounted path, so a stale shadow copy cannot report a healthy artefact for a
landing zone nothing can reach.

The archive itself is verified before it is published: it must be non-empty,
readable, **and** contain every path in
`nas_storage_pve_cluster_backup_required_files`. Framing checks alone cannot
tell a cluster-identity backup from a readable archive of nothing, and
publishing that one retires a real archive per night through retention.

### Backup-artifact collector
Independent NAS-side evidence that each app's dump actually **landed** —
an app's own backup metric goes green even when a broken mount or wrong path
means nothing reached the NAS. For each entry in
`nas_storage_backup_artifact_apps` it emits, to the node_exporter textfile dir:

| Series | Meaning |
|---|---|
| `backup_artifact_last_mtime_seconds{app}` | mtime of the newest file matching the app's `pattern` (0 = none) |
| `backup_artifact_last_size_bytes{app}` | size of that file (0 = none, or truncated) |
| `backup_artifact_companion_present{app,file}` | 1/0 per declared `companions` glob |
| `backup_artifact_companion_size_bytes{app,file}` | size of that companion (0 = absent) |
| `backup_artifact_collector_last_success_seconds` | collector sentinel |

`pattern` is mandatory and should be tight. A collector that takes the newest
file of *any* name lets an app's nightly config copies keep the freshness signal
green while no restorable dump lands — a real, hard-to-see failure mode. Files
named `*.tmp`/`*.partial`/`*.part` are excluded on top of the pattern.

`companions` covers files that are not the dump but are required to restore it
(an app's secrets/keys file): keep them out of `pattern`, and alert on
`backup_artifact_companion_present == 0` instead.

An app directory that does not exist emits **no** series (so an `absent()` alert
arm fires); one that exists but is empty emits 0. When
`nas_storage_backup_require_mounted_dataset` is true and the landing zone's
parent dataset is not mounted, no per-app series are emitted at all.

### SMART
`smartd` with an explicit disk list (no `DEVICESCAN`, which aborts extended
self-tests when drives enter standby), staggered long tests per group, and a
deploy-time assert that every member of every imported pool appears in the union
of `nas_storage_smartd_disk_groups` — otherwise a swapped drive is silently
unmonitored.

Groups are `{name, disks, schedule, ata?}`; `schedule` is smartd's `-s` regex
and `ata: false` drops the ATA-only `-o`/`-S` flags (they log warnings on NVMe).
The default builds four groups from the legacy
`nas_storage_smartd_{tank,ssd,nvme,archive}_disks` lists, so an existing
consumer needs no change; a site with a different pool layout sets
`nas_storage_smartd_disk_groups` directly instead of forking the template.

## Variables

**Reference-deployment defaults, deliberately.** Several defaults below name a
pool path (`nas_storage_zfs_mount_roots`, `_appdata_base`,
`_backup_apps_base`), a uid/gid pair (`_appdata_owner`, `_media_gid`) or the
four pool-named smartd groups. The collection's rule is that site data is an
asserted input, and these are the documented exception: they are marked
*reference-deployment* values here and in `defaults/main.yml`, kept because a
site that shares the layout gets a working role with no inventory and one that
does not overrides them wholesale. The classification guard
(`assert_zfs_classification.yml`) is what keeps the mount-roots default from
failing open on a different layout — anything it cannot classify is a hard
failure, not a silent skip. Converting them to asserted inputs is a breaking
change deferred to a future release.

| Variable | Default | Purpose |
|---|---|---|
| `nas_storage_zfs_pools` | *(undefined)* | Pools + datasets + properties to enforce. Undefined skips all ZFS tasks. |
| `nas_storage_zfs_scrub_enabled` | `true` | Enable the per-pool scrub timers. |
| `nas_storage_zfs_scrub_schedule` | `monthly` | Token in `zfs-scrub-<schedule>@<pool>.timer`. |
| `nas_storage_zfs_arc_max_bytes` | `{{ zfs_arc_max_bytes \| default('') }}` | ARC cap in bytes; empty = unmanaged. |
| `nas_storage_zfs_arc_skip_initramfs` | `false` | Passed through as `zfs_arc_cap_skip_initramfs`: render the modprobe.d file but skip `update-initramfs` (no real `/boot`). |
| `nas_storage_exports` | *(undefined)* | NFS exports. Undefined skips all NFS tasks. |
| `nas_storage_zfs_mount_roots` | `/mnt/tank`, `/mnt/ssd`, `/mnt/nvme` | Mount roots whose bind sources count as ZFS-backed (guard + boot ordering). Empty = nothing is ZFS-backed by derivation. |
| `nas_storage_zfs_bind_source_pattern` | derived from the roots | Regex the guard and the fstab opts test against; override only for a layout the roots cannot express. Never-matching when the roots list is empty. |
| `nas_storage_encrypted_bind_sources` | `[]` | Bind sources on encrypted datasets (late mount anchor + nfsd ordering). |
| `nas_storage_media_group` / `_media_gid` | `media` / `2000` | The shared group created by both the NFS and Samba tasks. Reference-deployment values — the gid must match what the site's clients already use. |
| `nas_storage_appdata_base` | `/mnt/ssd/appdata` | Bind source of the appdata export. Reference-deployment path. |
| `nas_storage_appdata_dirs` | `[]` | Per-app subdirectories to create under it. |
| `nas_storage_appdata_owner` / `_group` / `_mode` | `1000` / `{{ nas_storage_media_gid }}` / `0775` | Ownership matching the export's squash ids. |
| `nas_storage_samba_shares` | *(undefined)* | Samba shares. Undefined skips all Samba tasks. |
| `nas_storage_samba_password` | `$SAMBA_NAS_PASSWORD` | Password for the `nas` account; empty leaves it unmanaged. |
| `nas_storage_samba_ports` / `_samba_disable_netbios` | `445` / `true` | smbd listener scope (no 139/NetBIOS). |
| `nas_storage_mergerfs_mounts` | *(undefined)* | MergerFS unions. Undefined skips all MergerFS tasks. |
| `nas_storage_mergerfs_required_opts` | `[]` | fstab options the health probe requires; empty derives them from each union's own `options`. |
| `nas_storage_media_mover_enabled` | `false` | Deploy the media mover. Requires `_src` and `_dst`. |
| `nas_storage_media_mover_src` / `_dst` | *(required when enabled)* | Hot-tier source, bulk-tier destination. |
| `nas_storage_media_mover_min_age` | `12h` | Only files older than this are moved. |
| `nas_storage_media_mover_schedule` | `*-*-* 06:00:00` | Timer. |
| `nas_storage_media_mover_nice` / `_io_class` / `_io_priority` / `_cpu_weight` / `_io_weight` | `10` / `best-effort` / `7` / `20` / `20` | Load shaping. |
| `nas_storage_media_mover_bwlimit` | `""` | rsync `--bwlimit` (e.g. `50m`); empty = unlimited. |
| `nas_storage_swap_clean_enabled` | `false` | Deploy the nightly swap reset. |
| `nas_storage_swap_clean_schedule` | `*-*-* 07:00:00` | Timer; keep it outside the backup window. |
| `nas_storage_swap_clean_stop_guests` | `[]` | Ordered `vmid:name:timeout` escalation candidates. |
| `nas_storage_swap_clean_random_delay` | `1800` | Timer `RandomizedDelaySec`. |
| `nas_storage_swap_clean_min_mb` / `_margin_mb` / `_arc_shrink_mb` | `512` / `2048` / `2048` | Minimum reclaim worth a run, free-RAM headroom required before `swapoff`, and how far the ARC is squeezed to create it. |
| `nas_storage_swap_clean_service_timeout` / `_nice` | `1200` / `10` | Unit `TimeoutStartSec` and `Nice`. |
| `nas_storage_zfs_scrub_enabled` | `true` | Enable the per-pool scrub timers. |
| `nas_storage_zfs_scrub_schedule` | `monthly` | Scrub timer instance (`zfs-scrub-<schedule>@<pool>.timer`). |
| `nas_storage_smartd_enabled` | `true` | Deploy smartd config. |
| `nas_storage_smartd_disk_groups` | four groups from the lists below | `{name, disks, schedule, ata?}` per monitoring group. The four group names are reference-deployment scaffolding; each disk list is empty, so an unused name emits nothing. |
| `nas_storage_smartd_{tank,ssd,nvme,archive}_disks` | `[]` | Explicit by-id disk lists feeding the default groups. |
| `nas_storage_backup_apps_base` | `/mnt/tank/backups/apps` | Landing zone for logical dumps. Reference-deployment path — override for a different pool layout. |
| `nas_storage_backup_require_mounted_dataset` | `not skip_zfs_operations` | Fail-closed mount guard for the landing zone. |
| `nas_storage_backup_artifact_metrics_enabled` | `true` | Deploy the collector + timer. |
| `nas_storage_backup_artifact_metrics_dir` | tracks `node_exporter_host_textfile_dir` | Where every metric-writing wrapper in this role drops its `.prom`. |
| `nas_storage_backup_artifact_apps` | `[]` | `name` + `pattern` (+ optional `companions`) per app. |
| `nas_storage_archive_backup_enabled` | `false` | Deploy archive replication (off converges). |
| `nas_storage_archive_backup_pool` | `""` | Destination pool; its root properties are rewritten. |
| `nas_storage_archive_backup_sources` | `[]` | Datasets replicated recursively. Roots must be filesystems. |
| `nas_storage_archive_backup_vzdump_target` | `""` | Dataset needing the quiesce guard; empty disables it. |
| `nas_storage_archive_backup_keep_recent` / `_keep_monthly` | `3` / `6` | Snapshot retention. |
| `nas_storage_archive_backup_schedule` / `_random_delay` | `*-*-* 06:30:00` / `10m` | Timer. |
| `nas_storage_pve_cluster_backup_enabled` | `false` | Deploy the `/etc/pve` archive. |
| `nas_storage_pve_cluster_backup_src` | `/etc/pve` | Source (pmxcfs mountpoint). |
| `nas_storage_pve_cluster_backup_require_src_mount` | `not skip_zfs_operations` | Fail-closed guard on the source. |
| `nas_storage_pve_cluster_backup_required_files` | `user.cfg`, `corosync.conf`, `pve-root-ca.pem`, `priv/pve-root-ca.key`, `authkey.pub`, `priv/authkey.key` | Paths (relative to `_src`) that must be in the archive before it is published. |
| `nas_storage_pve_cluster_backup_schedule` / `_random_delay` / `_keep` / `_nice` | `*-*-* 02:15:00` / `300` / `14` / `10` | Timer + retention. |
| `nas_storage_nfs_disable_delegations` | `false` | Write (and, per the live gate below, apply) a sysctl.d drop-in with `fs.leases-enable=0` so nfsd grants no NEW NFSv4 delegations. Set on kernels whose nfsd leaks a `file_lock` per GETATTR delegation-conflict check (33 GB unreclaimable slab in 9 days, 2026-08-18); existing delegations persist until returned or reboot. Remove only once the running kernel carries the upstream fix AND a slab watch confirms `file_lock_cache` stays flat. |
| `nas_storage_nfs_apply_delegation_sysctl_live` | `true` | Reconcile the live `fs.leases-enable` value with `sysctl -p` on every enabled run. Test scenarios set `false`: the sysctl is not container-namespaced. |
| `nas_storage_skip_zfs_operations` | `false` | Skip all real-ZFS work (also disables both mount guards). Test use. |
| `nas_storage_skip_mergerfs` | `false` | Skip MergerFS mount management. |
| `nas_storage_skip_nfs_reload` | `false` | Skip `exportfs` reload / nfsd start. |
| `nas_storage_skip_smartd_service` | `false` | Skip enabling/starting smartd. |

## Worked example

```yaml
# Pools are created manually; this only enforces properties on existing datasets.
nas_storage_zfs_pools:
  - name: tank                       # bulk raidz
    datasets:
      - name: tank/media
        properties:
          mountpoint: /mnt/tank/media
          atime: "off"
          compression: zstd
          recordsize: 1M             # human-readable form, as `zfs get` prints it
  - name: ssd                        # app data
  - name: archive                    # detachable replication target

# NFS exports. `path` is the exported directory under the NFSv4 root;
# `bind_source` is bind-mounted onto it. `clients[]` is one entry per CIDR/host
# with a free-form `options` string. A bind_source outside
# nas_storage_zfs_mount_roots that is not a MergerFS target needs an explicit
# `zfs:` — false for a deliberately non-ZFS plain bind (no mounted-dataset
# guard, no boot ordering), true to apply both to a path the roots miss.
#
# Transport encryption (NFSv4 over kernel TLS, via weisssrv.infra.nfs_tls) has
# two scopes: an export-level `xprtsec` applies to every client line, and a
# per-client `xprtsec` overrides it for one client — including a falsy value to
# opt a single client OUT, so a require-TLS client and an appliance that cannot
# speak xprtsec can share one export. A line with no xprtsec is left at the
# server default (none:tls:mtls), which ACCEPTS plaintext. The wire is only
# encrypted when the client MOUNTS with xprtsec=tls, by a name the server
# certificate covers — a wildcard cert has no IP SAN, so an IP mount fails the
# handshake.
nas_storage_exports:
  # NFSv4 pseudo-root (fsid=0): traversal only, so read-only. crossmnt is
  # deliberately absent — it would implicitly export every child filesystem
  # bound under the root with THIS line's options, bypassing the per-child
  # client lists and TLS requirements.
  - path: /export
    clients:
      - spec: "10.0.0.200/29"
        options: "ro,sync,hide,no_subtree_check,fsid=0,sec=sys,root_squash"

  - path: /export/appdata
    bind_source: /mnt/ssd/appdata
    owner: 1000
    group: 2000
    mode: "02775"
    xprtsec: "tls"                   # require TLS on every client line
    clients:
      - spec: "10.0.0.200/29"
        options: "rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=2000,fsid=11"

  - path: /export/media
    bind_source: /mnt/media          # the MergerFS union
    owner: 1000
    group: 2000
    mode: "02775"
    clients:
      - spec: "10.0.0.200/29"
        options: "rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=2000,fsid=20"
        xprtsec: "tls"
      - spec: "10.0.0.154/32"        # appliance: no xprtsec -> plaintext accepted
        options: "ro,sync,no_subtree_check,fsid=20"

  # A per-app landing-zone export: bind_source is a SUBDIR of a dataset, so
  # bind_source_check points the mounted-dataset guard at the parent.
  - path: /export/backups-apps/gitlab
    bind_source: /mnt/tank/backups/apps/gitlab
    bind_source_check: /mnt/tank/backups
    owner: 1000
    group: 2000
    mode: "02775"
    xprtsec: "tls"
    clients:
      - spec: "10.0.0.153/32"
        options: "rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=2000,fsid=97"

nas_storage_encrypted_bind_sources:
  - /mnt/ssd/appdata
  - /mnt/tank/backups/apps/gitlab

nas_storage_samba_shares:
  - name: media
    path: /mnt/tank/media
    comment: Media library
    browseable: true
    read_only: false
    guest_ok: false
    valid_users: "nas"
    force_group: "media"
    create_mask: "0664"
    directory_mask: "2775"

nas_storage_backup_artifact_apps:
  - name: gitlab
    pattern: "*_gitlab_backup.tar"   # the tarball only, never the config copies
    companions:
      - "gitlab-secrets.json"        # required to decrypt a restored database
  - name: home-assistant
    pattern: "Automatic_backup_*.tar"  # full backups only, not addon tars
  - name: pve-cluster
    pattern: "etc-pve-*.tar.gz"

nas_storage_archive_backup_enabled: true
nas_storage_archive_backup_pool: archive
nas_storage_archive_backup_vzdump_target: tank/proxmox
nas_storage_archive_backup_sources:
  - tank/share
  - tank/backups
  - ssd/appdata
```

## Dependencies

- `weisssrv.infra.base` (meta dependency — mail relay for smartd alerts).
- `weisssrv.infra.textfile_collector`, used to ship the artifact collector.
- ZFS pools and datasets already created, manually.
- `nas_storage_samba_password` (default: `SAMBA_NAS_PASSWORD` in the
  environment) for the Samba password to be managed.

## Files

| Path | Purpose |
|---|---|
| `tasks/assert_zfs_classification.yml` | Up-front guard: every export bind source and boot-ordered union is classified ZFS-backed or not, split out so it is testable from fabricated inventory |
| `tasks/zfs.yml` | Dataset properties, scrub timers, ARC cap |
| `tasks/nfs.yml` | Exports, bind mounts, guards, nfsd ordering drop-in |
| `tasks/samba.yml` | Shares, `nas` user, password handling |
| `tasks/mergerfs.yml` | Union mount + the safe remount cycle |
| `tasks/mergerfs_needs_remount.yml` / `mergerfs_remount_gate.yml` | The remount cycle's decision facts, split out so they are testable without FUSE |
| `tasks/media_mover.yml` | Hot-to-bulk mover script/unit/timer |
| `tasks/swap_clean.yml` | Nightly swap reset |
| `tasks/smartd.yml` | SMART config + unmonitored-disk assert |
| `tasks/archive_backup.yml` / `archive_backup_absent.yml` | Archive replication, and its de-provisioning path |
| `tasks/pve_cluster_backup.yml` | `/etc/pve` archive |
| `tasks/backup_metrics.yml` | Backup-artifact collector |
