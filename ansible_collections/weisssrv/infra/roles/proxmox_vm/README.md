# Proxmox VM Role

Provisions VMs on Proxmox VE. The default (`proxmox_vm_guest_type: linux`) path builds
Debian VMs using cloud-init; the `proxmox_vm_guest_type: windows` path builds a
Windows 11 VM shell (OVMF/UEFI + TPM 2.0 + q35 + VirtIO + install/driver ISOs).
Handles networking, storage selection, resource pool assignment, and optional
persistent ZFS zvol disks.

## What This Role Manages

- Automatic storage selection based on Proxmox host role
- Resource pool creation and validation
- Cloud-init template download (Debian Trixie) — Linux guests
- VM creation with proper VMID, CPU, memory, disk
- Cloud-init configuration (user, SSH keys, networking) — Linux guests
- Windows 11 firmware/media (OVMF EFI vars, TPM 2.0, install + VirtIO CDROMs) —
  Windows guests
- Additional persistent disks (ZFS zvols for databases)
- Autostart configuration (order, delay)
- VM start after provisioning (Linux); Windows guests are created STOPPED

## Guest types

`proxmox_vm_guest_type` selects the provisioning path (default `linux`):

| Var | `linux` (default) | `windows` |
|-----|-------------------|-----------|
| Firmware | SeaBIOS (i440fx) | **OVMF/UEFI**, `--machine q35`, `--efidisk0 <storage>:1,efitype=4m,pre-enrolled-keys=1` (Secure Boot), `--tpmstate0 <storage>:1,version=v2.0` |
| ostype | `l26` (`proxmox_vm_ostype`) | `win11` (`proxmox_vm_windows_ostype`) |
| Boot disk | imported Debian cloud image | **empty** zvol `<storage>:<GiB>,discard=on,ssd=1` |
| Provisioning | cloud-init (`--ciuser`, `--sshkeys`, `--ipconfig0`) | none — interactive install |
| Media | cloud-init drive | `--ide2 <iso-store>:iso/<install>.iso` + `--ide0 <iso-store>:iso/virtio-win.iso` (both `media=cdrom`), `--vga std` |
| Boot order | `--boot c --bootdisk scsi0` | `--boot 'order=ide2;scsi0'` (install CD first; flip to `order=scsi0` post-install by hand) |
| Post-create | `qm start` + wait for SSH:22 | **created stopped** — no start, no SSH wait |
| Backwards-compat | unchanged for every existing Linux VM | new path, fully guarded |

Windows-guest vars (see `defaults/main.yml`): `proxmox_vm_install_iso` (REQUIRED — the
Win11 ISO the operator downloads manually to the ISO store; the role asserts
it), `proxmox_vm_virtio_iso`/`proxmox_vm_virtio_win_url`/`proxmox_vm_virtio_win_checksum` (the VirtIO driver
ISO the role downloads + checksum-verifies), `proxmox_vm_iso_storage`
(default `local`, Proxmox's stock ISO store),
`proxmox_vm_windows_machine`/`proxmox_vm_windows_ostype`/`proxmox_vm_windows_vga`.

## Required inputs (no defaults)

| Variable | Needed by | Why there is no default |
|---|---|---|
| `proxmox_host` | every path | which node provisions the guest |
| `vmid` | every path | falls back to the last octet of `proxmox_vm_target_ip`, which is not a contract |
| `proxmox_vm_cloudinit_gateway` | Linux create (`proxmox_vm_skip_create: false`) | no generic value; a wrong one silently strands the guest |
| `proxmox_vm_install_iso` | Windows create | media cannot be redistributed; asserted |
| `SSH_PUBLIC_KEY` (env) | Linux create | asserted before create — an empty key provisions an unreachable VM |

`proxmox_vm_cloudinit_user` and `proxmox_vm_cloudinit_dns` default to the
inventory-wide `admin_user` and `dns_servers`. `proxmox_vm_additional_disks`
defaults to the inventory-wide `vm_additional_disks`, the same name
`weisssrv.infra.k3s` aliases for its `zvol_mount` pass — one host_vars block
feeds both zvol creation (here) and mounting (there).

The following are read straight from inventory and keep neutral names (they are
the role's input contract, not role-owned tunables): `proxmox_host`, `vmid`,
`proxmox_storage`, `proxmox_storage_defaults`, `proxmox_resource_pool`,
`proxmox_resource_pools`, `proxmox_autostart_enabled`, `proxmox_startup_order`,
`proxmox_startup_delay`, `proxmox_role` (on the Proxmox host).

## Storage Selection

Storage is automatically selected based on the Proxmox host's role:

`proxmox_storage_defaults` maps the Proxmox host's `proxmox_role` to a storage
id; `local-ssd` is used when the role is unknown. Override per-VM with
`proxmox_storage`.

## Configuration

```yaml
# In hosts.yml
k3s-agt-nas-01:
  vmid: 202
  proxmox_host: pve-nas-01
  # proxmox_storage: ssd  # Optional: auto-selected based on host role
  proxmox_resource_pool: platform
  proxmox_vm_cpu_type: host
  proxmox_vm_cores: 4
  proxmox_vm_memory: 8192
  proxmox_vm_disk_size: 64G
  # Conventional inventory-wide name; weisssrv.infra.k3s reads the same block to
  # mount what is created here. Set proxmox_vm_additional_disks to decouple.
  vm_additional_disks:
    - name: postgres-data
      size: 10G
      zvol: ssd/appdata/authentik/postgres
      mount_point: /mnt/postgres-data
      fstype: ext4
      scsi_slot: 1          # REQUIRED, unique. Pins the zvol to a stable SCSI
                            # slot; the role refuses to remap a slot already
                            # holding a different live zvol. NEVER reuse/reorder
                            # a slot (set allow_remap: true to override on purpose).
  proxmox_autostart_enabled: true
  proxmox_startup_order: 40
  proxmox_startup_delay: 10
```

## Memory ballooning (`proxmox_vm_balloon`)

Optional. When set, the VM is created with `--balloon <proxmox_vm_balloon>` (and existing
VMs are reconciled live via `qm set --balloon`), letting Proxmox reclaim idle guest
RAM down to this floor under host memory pressure: the guest boots at `proxmox_vm_memory`
and returns everything above `proxmox_vm_balloon` when the host is tight. Requires the
virtio balloon driver in the guest (built into Linux; the VirtIO Balloon
driver + service on Windows). **Do not set it on k3s nodes** — the kubelet accounts
for the full node RAM and schedules pods to it, so reclaiming underneath causes pod
OOMs. Leave `proxmox_vm_balloon` unset (the default) for a fixed allocation.

## PCI passthrough (`proxmox_vm_hostpci`)

Optional list of Proxmox `hostpci` device specs; each entry becomes
`--hostpci<index> <entry>` on a **create-time** `qm set` (Linux guests only).
Used to pass a GPU (or other PCI device) into a VM via VFIO. Because attaching a
PCI device requires the guest stopped, this is applied only when the VM is first
provisioned — an already-existing guest that gains `proxmox_vm_hostpci` is attached by
the operator with a manual `qm set <id> --hostpci0 …` in a stop/start
maintenance window (the same apply gap as `proxmox_vm_memory`).

```yaml
# hosts.yml — pass the whole multifunction GPU on i440fx (no pcie=1)
proxmox_vm_hostpci:
  - "0000:01:00"
```

The entry is passed verbatim, so it carries any Proxmox options (`0000:01:00,pcie=1`
for PCIe passthrough on q35). A passthrough guest cannot live-migrate and its RAM
is host-pinned/mlock'd, so pin it (`proxmox_vm_cpu_type: host`) and leave it non-ballooned.

## Files

- `tasks/main.yml` - Main orchestration
- `defaults/main.yml` - Default values

## Dependencies

- Proxmox host must be accessible
- Cloud-init template must be downloadable

## Reconciliation vs. create-time-only

The role distinguishes settings it converges on **every** run from settings
applied **only at VM creation**:

| Setting | Behaviour |
|---------|-----------|
| `onboot` / `startup` (order, delay) | **Reconciled** on existing VMs — editing `proxmox_autostart_enabled` / `proxmox_startup_order` / `proxmox_startup_delay` and re-running applies them via an idempotent `qm set`. These are metadata-only (next-boot), so converging a live VM is safe. |
| QEMU guest-agent flag (`proxmox_vm_agent_enabled`) | **Reconciled** on existing VMs (metadata-only `qm set --agent`). |
| NIC `firewall=1` flag | **Reconciled** on existing VMs (one-time repair of legacy NICs). |
| CPU/memory/cores, disk size, cloud-init (user, SSH key, IP), boot disk | **Create-time only.** Changing these in inventory does not reconcile onto an existing VM — recreate the VM (or `qm set …` by hand). Persistent zvols are matched idempotently by stable SCSI slot and survive recreation. |

## Notes

- Cloud-init user: eric (with SSH key)
- Network: DHCP by default, then set static via cloud-init
- Persistent zvols survive VM recreation
- The cloud-init SSH public key is staged on the Proxmox host in a private
  `tempfile` (mode 0600, random name) and removed after `qm set`, never a
  predictable `/tmp` path.
