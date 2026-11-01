# weisssrv.infra.proxmox_backup

Declaratively manages Proxmox storage entries (`storage.cfg`) and vzdump
backup jobs (`jobs.cfg`) via `pvesh`, so the backup configuration lives in git
instead of only in the GUI.

## Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `proxmox_backup_storage` | `[]` | Storage entries to reconcile (schema below) |
| `proxmox_backup_vzdump_jobs` | `[]` | vzdump backup jobs to reconcile (schema below) |

Both files live on the clustered `/etc/pve`, so run this role against exactly
one node. It no-ops while both lists are empty.

## What it manages

- **Storage entries** (`/storage` API):
  - creates missing entries;
  - reconciles `content`, `nodes`, `mountpoint`, `sparse` and `options` on
    drift, comparing comma lists as sets because Proxmox reorders them;
  - **fails loud** when a create-fixed property (`server`, `export`, `path`,
    `pool`) drifts, printing the manual recreation procedure;
  - leaves an undefined `nodes`/`mountpoint`/`sparse` at its live value.
- **vzdump backup jobs** (`/cluster/backup` API):
  - creates missing jobs with the configured `id`;
  - reconciles `storage`, `schedule`, `mode`, `compress`, `enabled`, guest
    selection (`all`, `vmid`, `exclude`), `bwlimit`, `prune-backups`,
    notification and mail settings, and the `script` hookscript path;
  - **warns about** unmanaged live jobs and never deletes them.
- **vzdump metrics hookscript**: a job whose `script` points at
  `/usr/local/bin/vzdump-metrics-hook.sh` gets backup metrics from
  `node_exporter_host`. This role first runs that role with
  `tasks_from: vzdump_hook`, because `pvesh` validates the path at save time.

### Design notes

- A `zfspool` entry's `pool` names the dataset the id allocates in. It is
  create-fixed because an id recreated against the pool root puts every new
  guest disk on the root's ZFS properties, encryption included, with no other
  signal.
- `nodes` keeps a single-host pool off the rest of the cluster.
- Storage entries get no unmanaged-entry warning. `storage.cfg` is shared with
  Proxmox's install-time entries and with entries other roles create, while
  `jobs.cfg` is a namespace this role owns.
- The hookscript is deployed to **every** Proxmox host by `node_exporter_host`.
  A cluster-wide `all` job runs on each node and invokes the hook there.

## Configuration

Variables live in the reconciling node's host vars. Every storage entry needs
`id`, `type` and `content`; every job needs `id`, `storage` and `schedule` —
both are asserted at role entry.

| Job key | Type |
|---|---|
| `id`, `storage`, `schedule`, `mode`, `compress`, `prune_backups`, `notification_mode`, `mailnotification`, `mailto`, `script` | string |
| `all`, `enabled` | bool |
| `vmid`, `exclude` | list of guest ids |
| `bwlimit` | integer, KiB/s |

| Storage key | Type |
|---|---|
| `id`, `type`, `content`, `server`, `export`, `path`, `pool`, `nodes`, `mountpoint`, `options` | string |
| `sparse` | bool |

```yaml
proxmox_backup_storage:
  - id: tank-proxmox
    type: nfs
    server: nas.example.com  # hostname: the NFS-over-TLS cert names it
    export: /tank-proxmox
    content: "snippets,backup,vztmpl,iso"
    options: "vers=4.2,xprtsec=tls"

  - id: ssd                  # the id guest disks are allocated from
    type: zfspool
    pool: ssd/pve            # create-fixed: the dataset new disks inherit from
    content: "images,rootdir"
    nodes: node-01           # comma-separated; keeps a local pool off other nodes
    mountpoint: /mnt/ssd/pve
    sparse: false

proxmox_backup_vzdump_jobs:
  - id: backup-10f33360-a177  # matches the live jobs.cfg id -> the role ADOPTS the job
    storage: tank-proxmox
    schedule: "03:30"
    mode: snapshot
    compress: zstd
    all: true
    bwlimit: 30720                # optional: cap per-node backup I/O (KiB/s)
    prune_backups: "keep-daily=7,keep-weekly=4,keep-monthly=3"
    notification_mode: notification-system
    enabled: true
    script: /usr/local/bin/vzdump-metrics-hook.sh  # deployed by node_exporter_host
```

**Adopting a GUI-created job**: set the entry's `id` to the live job id
(`pvesh get /cluster/backup --output-format json`) and mirror the live values
— the role then reconciles instead of creating a duplicate.

**Changing a create-fixed property**: the fixed-property assert blocks the run
until the entry is recreated by hand, outside a backup window:

```bash
ssh <node> "sudo pvesh delete /storage/<id>"  # config entry only, data untouched
# then re-run the role to create it from config
```

An `xprtsec=tls` mount needs a TLS-enabled export and tlshd on the mounting
host (see the nfs_tls role). Mount by the hostname the server cert names.

## Dependencies

- Proxmox VE node with `pvesh` (cluster quorate for /etc/pve writes)
- For NFS-over-TLS targets: nfs_tls role deployed on server and mounting host

## Molecule

`molecule test -s default` from the role directory (CI runs the same scenario).
`pvesh` is stubbed: GETs answer from state files under
`/tmp/molecule-stub-state`, every mutation is logged, and the cases assert the
exact invocations for absent, already-correct, drifted and create-fixed-drift
storage entries and vzdump jobs.
