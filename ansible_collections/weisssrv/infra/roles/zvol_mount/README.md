# weisssrv.infra.zvol_mount

Mounts zvol-backed block devices inside a guest via UUID-based fstab entries —
for any guest that needs persistent host-side storage attached as a virtual
disk (database volumes, repository storage, hostPath PV backing).

`/dev/sdX` is not deterministic across reboots, so the role addresses each disk
by `zvol_mount_device_id_prefix` + its SCSI slot, formats it once (only when
`lsblk -no FSTYPE` shows no filesystem), then writes `UUID=<id>` entries to
`/etc/fstab` so mounts survive device renumbering. It also detects and corrects
disks mounted at the wrong location (`zvol_mount_fix_wrong_locations`, default
`true`).

## Inputs

The role consumes a single per-host list `zvol_mount_disks`, typically
populated alongside `proxmox_vm_additional_disks` in `host_vars`:

```yaml
# Per-host: declared in inventory (host_vars), consumed here.
zvol_mount_disks:
  - name: app-postgres
    mount_point: /mnt/postgres-data
    fstype: ext4
    scsi_slot: 1     # optional; defaults to loop index + 1. Set it explicitly
                     # whenever VM hardware edits could reorder slots.
  - name: app-data
    mount_point: /mnt/app-data
    fstype: ext4
    scsi_slot: 2
```

Per entry:

| Key          | Required | Meaning                                                          |
|--------------|----------|------------------------------------------------------------------|
| `name`       | yes      | Human label used in task output                                  |
| `mount_point`| yes      | Absolute path to mount under                                     |
| `fstype`     | yes      | Expected filesystem (ext4, xfs, ...); enforced by assert         |
| `scsi_slot`  | no       | Explicit QEMU SCSI slot; defaults to list index + 1              |

> **Contract:** alias `zvol_mount_disks` to the same list
> `proxmox_vm_additional_disks` carries, where every entry has an explicit
> `scsi_slot`. proxmox_vm requires and asserts a unique slot per disk because
> attaching by list position can remap a live zvol. The `idx + 1` fallback above
> is only for standalone / molecule use with sequential slots — do not rely on
> it for guests holding persistent data.

`zvol_mount_device_id_prefix` (default the QEMU/KVM SCSI by-id prefix) is what
`scsi_slot` is appended to; override it for a different hypervisor.

## Safety

- Refuses to format a disk that already has a filesystem (unless overridden)
- Writes fstab entries by UUID, never by `/dev/sdX`
- Runs the format step only when not in `--check` mode
- Refuses to continue when two attached disks share a filesystem UUID (common
  after a zvol clone or `zfs send | receive` from a formatted snapshot), because
  `UUID=<x>` would then be an ambiguous mount source

## See also

`weisssrv.infra.proxmox_vm` creates the zvols and attaches them to the VM; this
role mounts them from inside the guest.
