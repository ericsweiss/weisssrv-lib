# zfs_arc_cap

Caps the ZFS ARC on the **compute** Proxmox hosts (`proxmox_role: compute`).

This is the one implementation of the ARC cap in the collection:
`nas_storage` includes this role on the NAS host rather than carrying its own
copy (same `zfs_arc_max_bytes` knob, different playbook lifecycle). Two cases
make it load-bearing on a compute host:

- **VFIO passthrough**: a GPU guest's RAM is host-pinned/mlock'd and therefore
  non-reclaimable, so the ~½-RAM default ARC collides with the pinned VM and
  thrashes (e.g. ~31 GiB ARC + a 30 GiB pinned guest on a 62 GiB host).
- **Small hosts**: on a 14–16 GiB node the default ceiling is *not* harmless —
  the ARC will happily sit at several GiB while the host swaps. Override the
  group value per host (2 GiB is a workable floor for a `local-ssd` root pool).

## What it does (only when `zfs_arc_cap_max_bytes` is set)

1. Renders `/etc/modprobe.d/zfs.conf` with `options zfs zfs_arc_max=<bytes>`,
   cleanly superseding any prior manual cap in that file. The directory is
   created first — kmod ships it on a full host, a minimal image may not.
2. Notifies a **Rebuild initramfs** handler so the load-time module parameter is
   baked into the initramfs (the root pool imports at early boot).
3. Writes the running kernel's `/sys/module/zfs/parameters/zfs_arc_max`
   (compare-then-set) so a changed cap is live immediately, no reboot.

Empty (`zfs_arc_cap_max_bytes: ""`, the default) → the role manages nothing.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `zfs_arc_cap_max_bytes` | `"{{ zfs_arc_max_bytes \| default('') }}"` | Byte count for `zfs_arc_max`. Empty = no-op. Aliases the inventory-wide `zfs_arc_max_bytes` (the same knob `nas_storage` reads); set the prefixed name to decouple. Usually set once on the compute group, overridden per host where RAM is tight. |
| `zfs_arc_cap_skip_initramfs` | `false` | Molecule/check-mode: render the modprobe.d file but skip `update-initramfs` (no real `/boot`). |

## Where it runs

Compute hosts, directly from the play. On a NAS host it is included by
`weisssrv.infra.nas_storage`, which passes `nas_storage_zfs_arc_max_bytes`
through — do not also list it in the play there, or the cap is converged twice
per run.

## Runtime cleanup note

If a host carried a manual `zfs_arc_max` in a **differently-named**
`/etc/modprobe.d/*.conf`, remove it by hand once this role owns `zfs.conf` —
otherwise two files set the parameter and the *load-time* value is ambiguous
(the running value is still made authoritative by the sysfs task).
