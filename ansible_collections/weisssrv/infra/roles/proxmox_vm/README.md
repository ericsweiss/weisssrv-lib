# weisssrv.infra.proxmox_vm

Provisions VMs on Proxmox VE. The default (`proxmox_vm_guest_type: linux`) path builds
Debian VMs using cloud-init; the `proxmox_vm_guest_type: windows` path builds a
Windows 11 VM shell (OVMF/UEFI + TPM 2.0 + q35 + VirtIO + install/driver ISOs).
Handles networking, storage selection, resource pool assignment, and optional
persistent ZFS zvol disks.

## What it manages

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

Windows-guest vars (see `defaults/main.yml`): `proxmox_vm_install_iso` (REQUIRED — the
Win11 ISO the operator downloads manually to the ISO store; the role asserts
it), `proxmox_vm_virtio_iso`/`proxmox_vm_virtio_win_url`/`proxmox_vm_virtio_win_checksum` (the VirtIO driver
ISO the role downloads + checksum-verifies), `proxmox_vm_iso_storage`
(default `local`, Proxmox's stock ISO store),
`proxmox_vm_windows_machine`/`proxmox_vm_windows_ostype`/`proxmox_vm_windows_vga`.

## Variables

Every knob and its default is in `defaults/main.yml`; the inputs with no
default are:

| Variable | Needed by | Why there is no default |
|---|---|---|
| `proxmox_host` | every path | which node provisions the guest |
| `vmid` | every path | falls back to the last octet of `proxmox_vm_target_ip`, which is not a contract |
| `proxmox_vm_cloudinit_gateway` | Linux create (`proxmox_vm_skip_create: false`) | no generic value; a wrong one silently strands the guest |
| `proxmox_vm_install_iso` | Windows create | media cannot be redistributed; asserted |
| `SSH_PUBLIC_KEY` (env) | Linux create | asserted before create — an empty key provisions an unreachable VM |
| `_proxmox_vm_probe_hosts` | `tasks_from: probe-cluster-resources` | the ordered Proxmox hosts to ask; the caller owns the order |

The cloud-init assert carries the create-path gate (`proxmox_vm_exists.rc != 0`
plus `proxmox_vm_skip_create`), so a run that only reconciles disks, HA or pool
membership on an existing guest never fails on values it does not read.

`proxmox_vm_disk_size` accepts a GiB value with or without the `G` suffix on
**both** paths, and `M`/`T` on the Linux path only (it goes to `qm resize`).
The Windows boot disk is allocated as a bare GiB count, so a non-GiB unit is
rejected by an assert on that path rather than reaching `qm set` as a
nonsensical number.

`proxmox_vm_cloudinit_user` and `proxmox_vm_cloudinit_dns` default to the
inventory-wide `admin_user` and `dns_servers`. `proxmox_vm_additional_disks`
defaults to the inventory-wide `vm_additional_disks`, the same name
`weisssrv.infra.k3s` aliases for its `zvol_mount` pass — one host_vars block
feeds both zvol creation (here) and mounting (there).

Knobs the sections below do not otherwise mention:

| Variable | Default | Purpose |
|---|---|---|
| `proxmox_vm_cloud_image_url` | Debian 13 (trixie) generic qcow2 on `cloud.debian.org` | Cloud image downloaded to the node on first run |
| `proxmox_vm_cloud_image_name` | `debian-13-generic-amd64.qcow2` | Filename it is staged under |
| `proxmox_vm_cloud_image_dir` | `/var/lib/vz/template/iso` | Where it is staged on the node |
| `proxmox_vm_cloud_image_checksum` | `sha512:` URL of the release `SHA512SUMS` | Checksum the download is verified against |
| `proxmox_vm_bridge` | `vmbr0` | Bridge `net0` attaches to |
| `proxmox_vm_iso_storage_path` | derived from `proxmox_vm_iso_storage` | Filesystem path ISOs are fetched into (Windows path) |
| `proxmox_vm_virtio_win_version` | `0.1.285` | virtio-win ISO release (Windows path); bump with its checksum |
| `proxmox_vm_ssh_wait_delegate` | `localhost` | Host that runs the post-create SSH reachability wait |
| `proxmox_vm_additional_disk_backend` | `zfs` | Backend the additional disks are created on; ZFS is the only one implemented |

Remaining knobs: see `defaults/main.yml`.

The following are read straight from inventory and keep neutral names (they are
the role's input contract, not role-owned tunables): `proxmox_host`, `vmid`,
`proxmox_storage`, `proxmox_storage_defaults`, `proxmox_resource_pool`,
`proxmox_resource_pools`, `proxmox_autostart_enabled`, `proxmox_startup_order`,
`proxmox_startup_delay`, `proxmox_role` (on the Proxmox host).

## Storage selection

Storage is automatically selected based on the Proxmox host's role:

`proxmox_storage_defaults` maps the Proxmox host's `proxmox_role` to a storage
id; `local-ssd` is used when the role is unknown. Override per-VM with
`proxmox_storage`.

## Configuration

```yaml
# In hosts.yml
k3s-agent-01:
  vmid: 202
  proxmox_host: hypervisor-01
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
      scsi_slot: 1          # REQUIRED, unique
  proxmox_autostart_enabled: true
  proxmox_startup_order: 40
  proxmox_startup_delay: 10
```

Each `vm_additional_disks` entry takes these keys:

| Key | Required | Default | Meaning |
|---|:-:|---|---|
| `name` | ● | — | Disk label; also the mount unit / fstab identity |
| `size` | ● | — | zvol volsize, e.g. `10G` |
| `zvol` | ● | — | Full dataset path, e.g. `ssd/appdata/authentik/postgres` |
| `mount_point` | ● | — | In-guest mount path (consumed by `weisssrv.infra.zvol_mount`) |
| `fstype` | ● | — | Filesystem made inside the guest, e.g. `ext4` |
| `scsi_slot` | ● | — | Stable SCSI slot, unique per VM. The role refuses to remap a slot already holding a different live zvol — never reuse or reorder one |
| `allow_remap` | | `false` | Overrides that refusal, on purpose |
| `sparse` | | `false` | Thin-provisions the zvol (`zfs create -s`), so `volsize` is a ceiling rather than a reservation |
| `vzdump_backup` | | `true` | `false` sets `backup=0` on the disk so the nightly vzdump skips it — use for app-data zvols already covered by dataset replication |

## Memory ballooning (`proxmox_vm_balloon`)

Optional. When set, the VM is created with `--balloon <proxmox_vm_balloon>` (and existing
VMs are reconciled live via `qm set --balloon`), letting Proxmox reclaim idle guest
RAM down to this floor under host memory pressure: the guest boots at `proxmox_vm_memory`
and returns everything above `proxmox_vm_balloon` when the host is tight. Requires the
virtio balloon driver in the guest (built into Linux; the VirtIO Balloon
driver + service on Windows). **Do not set it on k3s nodes** — the kubelet accounts
for the full node RAM and schedules pods to it, so reclaiming underneath causes pod
OOMs. Leave `proxmox_vm_balloon` unset (the default) for a fixed allocation.

## CPU type (`proxmox_vm_cpu_type`)

Defaults to `Penryn` — a conservative live-migration baseline (SSE3/SSSE3/SSE4.1,
no SSE4.2/AVX) so a guest can migrate across heterogeneous hosts. Set it to
`host` for any guest pinned to one node; that exposes the full host CPU
(AVX/AVX2) and is required for a PCI-passthrough guest, which cannot migrate.

## PCI passthrough (`proxmox_vm_hostpci`)

Optional list of Proxmox `hostpci` device specs; each entry becomes
`--hostpci<index> <entry>` on a **create-time** `qm set` (Linux guests only).
Used to pass a GPU (or other PCI device) into a VM via VFIO. Because attaching a
PCI device requires the guest stopped, this is applied only when the VM is first
provisioned — an already-existing guest that gains `proxmox_vm_hostpci` is attached by
the operator with a manual `qm set <id> --hostpci0 …` in a stop/start
maintenance window.

```yaml
# hosts.yml — pass the whole multifunction GPU on i440fx (no pcie=1)
proxmox_vm_hostpci:
  - "0000:01:00"
```

The entry is passed verbatim, so it carries any Proxmox options (`0000:01:00,pcie=1`
for PCIe passthrough on q35). A passthrough guest cannot live-migrate and its RAM
is host-pinned/mlock'd, so pin it (`proxmox_vm_cpu_type: host`) and leave it non-ballooned.

## Dependencies

Environment: the delegate Proxmox host must be reachable, and the cloud-init
image (`proxmox_vm_cloud_image_url`) downloadable from it on first run.

Every task delegated to the Proxmox node declares `become: true` itself, so the
calling play does not have to. A group driving Proxmox over
`ansible_connection: local` can set `ansible_become: false` and still get the
`qm` / `pveum` writes as root.

Companion roles — none is a `meta` dependency, all are composed by the play:

- `weisssrv.infra.zvol_mount` — formats and mounts the extra zvols this role
  creates from `proxmox_vm_additional_disks`, inside the guest
- `weisssrv.infra.proxmox_firewall` — renders the per-guest `.fw` rules this
  role enables with `firewall=1` on `net0`
- `weisssrv.infra.k3s` — the usual consumer of the VMs, and the role whose
  `zvol_mount` pass reuses the same `host_vars` disk block

## Reconciliation vs. create-time-only

The role distinguishes settings it converges on **every** run from settings
applied **only at VM creation**:

| Setting | Behaviour |
|---------|-----------|
| `onboot` / `startup` (order, delay) | **Reconciled** on existing VMs — editing `proxmox_autostart_enabled` / `proxmox_startup_order` / `proxmox_startup_delay` and re-running applies them via an idempotent `qm set`. These are metadata-only (next-boot), so converging a live VM is safe. |
| QEMU guest-agent flag (`proxmox_vm_agent_enabled`) | **Reconciled** on existing VMs (metadata-only `qm set --agent`). |
| NIC `firewall=1` flag | **Reconciled** on existing VMs (one-time repair of legacy NICs). |
| `proxmox_vm_memory` | **Reconciled** on existing VMs via `qm set --memory`, which writes the config and takes effect at the guest's **next start** — the task never restarts anything. Draining and restarting the guest stays an operator step. The role **defaults** this to 2048 MiB and the reconcile participates, so a guest that loses its inventory key would be resized down to the default: a shrink fails the task unless `proxmox_vm_memory_shrink_ok: true` names it. |
| `proxmox_vm_balloon` | **Reconciled** on existing VMs when defined; `qm set --balloon` takes effect live. |
| Cloud-init network — `ipconfig0` (`proxmox_vm_target_ip`/`proxmox_vm_cloudinit_prefix_len`/`proxmox_vm_cloudinit_gateway`) and `nameserver` (`proxmox_vm_cloudinit_dns`) | **Reconciled** on existing Linux VMs via an idempotent `qm set --ipconfig0 --nameserver` (skipped for Windows guests and under `proxmox_vm_skip_create`). Metadata-only: it regenerates the cloud-init drive, applied once per instance, so a running guest is untouched. |
| Cloud-init user + SSH key, cores, disk size, `proxmox_vm_cpu_type`, boot disk, `proxmox_vm_hostpci` | **Create-time only.** Changing these in inventory does not reconcile onto an existing VM — recreate the VM (or `qm set …` by hand in a stop/start window for PCI). Persistent zvols are matched idempotently by stable SCSI slot and survive recreation. |

Why reconcile these at all: Proxmox stores the guest's DNS and network config
itself and re-applies it on start, so an inventory address change that is not
converged is silently re-applied from the stale stored value.

## Notes

- `proxmox_vm_ssh_wait_delegate` (default `localhost`) names the host that runs
  the post-create SSH reachability wait; set it to a Proxmox host when the
  controller is off-LAN.
- Additional disks are ZFS-only today: the backend is selected by
  `proxmox_vm_additional_disk_backend` (`zfs`), and another backend means adding
  `tasks/disks-<backend>.yml`.
- The cloud-init user is `proxmox_vm_cloudinit_user` (defaults to the
  inventory-wide `admin_user`), authorized with `SSH_PUBLIC_KEY`.
- Networking is static via cloud-init `--ipconfig0`
  (`proxmox_vm_target_ip`/`proxmox_vm_cloudinit_prefix_len`/`proxmox_vm_cloudinit_gateway`),
  with `--nameserver` from `proxmox_vm_cloudinit_dns`. Both are reconciled on
  existing VMs (see the table above), so an inventory change to the address or
  resolvers is written back to the guest's stored cloud-init config rather than
  applying only at first create.
- Persistent zvols survive VM recreation.
- The cloud-init SSH public key is staged on the Proxmox host in a private
  `tempfile` (mode 0600, random name) and removed after `qm set`, never a
  predictable `/tmp` path.

## Finding a guest's current node

`tasks_from: probe-cluster-resources` asks each host in `_proxmox_vm_probe_hosts`
for `pvesh get /cluster/resources` and stops at the first answer. The API
answers cluster-wide, so the caller gets the guest's real node after a
migration or an HA failover, not the static inventory value. It sets
`_proxmox_vm_cluster_json` (the raw JSON) and `_proxmox_vm_probe_results` (one
`host: rc=N` entry per host tried), and fails terminally when nothing answered.

The per-host query lives in `tasks/probe-cluster-resources-one.yml` and stays a
looped include around one delegated task. A looped `delegate_to` that is
unreachable ends the host's task sequence with rc 0, and the caller then reports
green having reconciled nothing.

## Shared with proxmox_lxc

`tasks/guest-nic-firewall.yml` (the `firewall=1` NIC repair),
`tasks/guest-startup.yml` (the onboot/startup reconcile with its startup-string
canonicalisation), `tasks/assert-target-pools.yml` (the create-path storage and
resource-pool guards) and the `probe-cluster-resources` pair are guest-type
agnostic: `proxmox_lxc` includes them with `tasks_from:`. Edit them here, and
keep both roles' molecule scenarios green.

They stay here rather than moving into a shared `proxmox_common` role. A new
role costs a CI matrix entry and its own scenario, and these files already have
one owner and two callers, so there is no drift for it to prevent.

## Storage the role does not own

Reconciliation touches only the disks declared in `vm_additional_disks`. A
volume attached by hand stays attached and is never reported, so an operator
auditing a host reads `qm config` rather than expecting this role to list it.

## Molecule

`molecule test -s default` from the role directory (CI runs the same scenario).
`qm`, `pvesh`, `pvesm` and `zfs` are stubbed and every mutation is logged, so
the cases assert the exact commands issued: storage selection, the pool and
slot asserts, the existence guard, the Windows and PCI-passthrough create
paths, zvol creation, and each reconcile converging once and then no-op'ing.
