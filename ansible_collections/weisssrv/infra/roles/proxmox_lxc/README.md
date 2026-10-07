# weisssrv.infra.proxmox_lxc

Provisions unprivileged LXC containers on Proxmox VE: bind mounts, GPU
passthrough, UID/GID mapping, and an admin user bootstrapped for Ansible.

## What it manages

- Automatic storage selection based on Proxmox host role
- Resource pool creation and validation
- LXC container creation (Debian Trixie)
- Unprivileged containers with security
- Bind mounts (media directories)
- GPU passthrough (/dev/dri for transcoding)
- UID/GID mapping for host file access
- Admin-user bootstrap for Ansible (`proxmox_lxc_admin_user`; its home is
  `proxmox_lxc_admin_home`, which resolves to `/root` for root and
  `/home/<user>` otherwise)
- Autostart configuration

## Storage selection

Storage is automatically selected based on the Proxmox host's role:

`proxmox_storage_defaults` maps the Proxmox host's `proxmox_role` to a storage
id; `local-ssd` is used when the role is unknown. Override per-container with
`proxmox_lxc_storage`.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `proxmox_lxc_skip_create` | `false` | Skips the real-Proxmox-only tasks (`pveam download`, the SSH probe) and, for a missing container, the `pct config` reconciles. |
| `proxmox_lxc_target_ip` | `ansible_host` | Address written into `--net0` and waited on over SSH. |
| `proxmox_lxc_cores` | `2` | vCPU count. |
| `proxmox_lxc_memory` | `2048` | RAM in MiB. |
| `proxmox_lxc_swap` | `512` | Swap in MiB. |
| `proxmox_lxc_disk_size` | `8G` | Rootfs size; a GiB value (`8` or `8G`), asserted on the create path. |
| `proxmox_lxc_bridge` | `vmbr0` | Proxmox bridge the container's NIC attaches to. |
| `proxmox_lxc_unprivileged` | `true` | Unprivileged container, which is what makes the idmap below apply. |
| `proxmox_lxc_template` | `debian-13-standard_13.6-1_amd64.tar.zst` | Appliance template name; Proxmox rotates builds out of the pveam index, so a 404 means bumping it. |
| `proxmox_lxc_template_storage` | `local` | Storage holding the template cache. |
| `proxmox_lxc_netmask_bits` | `24` | Prefix length in `--net0`; must be set on a LAN that is not a /24. |
| `proxmox_lxc_nameserver` | `dns_servers` | Resolvers written into the container. |
| `proxmox_lxc_internal_domain` | `internal_domain` | Aliases the inventory-wide name; `proxmox_lxc_searchdomain` defaults to it. |
| `proxmox_lxc_searchdomain` | `proxmox_lxc_internal_domain` | Container search domain. |
| `proxmox_lxc_bind_mounts` | `[]` | Host paths bound into the container (create-time only). |
| `proxmox_lxc_gpu_passthrough` | `false` | Maps the host's video/render gids through (create-time only). |
| `proxmox_lxc_idmap_uid` | `1000` | Host uid mapped through 1:1 so bind-mounted files stay accessible. |
| `proxmox_lxc_idmap_gid` | `2000` | Host gid mapped through 1:1. |
| `proxmox_lxc_idmap_base` | `100000` | First host id of the unprivileged shift range. |
| `proxmox_lxc_idmap_range` | `65536` | Size of that range. |
| `proxmox_lxc_admin_user` | `admin_user` | User created inside the container with sudo and SSH key access. |
| `proxmox_lxc_admin_home` | derived | `/root` for root, `/home/<user>` otherwise. |
| `proxmox_lxc_ssh_public_keys` | `$SSH_PUBLIC_KEY` | Authorized keys for that user; asserted non-empty before create. |
| `proxmox_lxc_onboot` | `true` | Start the container when the node boots. |
| `proxmox_lxc_startup_order` | `100` | Proxmox `startup` order value. |
| `proxmox_lxc_startup_delay` | `0` | Seconds waited after this container starts. |
| `proxmox_lxc_nesting` | `false` | `features=nesting=1`, for running containers inside. |
| `proxmox_lxc_keyctl` | `false` | `features=keyctl=1`, which systemd-based images need. |
| `proxmox_lxc_bootstrap_fallback_dns` | `1.1.1.1` | Public resolver appended only to complete the bootstrap `apt-get update`, then removed. |

Remaining knobs: see `defaults/main.yml`.

The inputs with no default are:

| Variable | Why there is no default |
|---|---|
| `proxmox_host` | which node provisions the container |
| `vmid` | falls back to the last octet of `proxmox_lxc_target_ip`, which is not a contract |
| `proxmox_lxc_gateway` | no generic value; a wrong one silently strands the container |
| `internal_domain` | the search domain (`proxmox_lxc_searchdomain` defaults to it) |
| `SSH_PUBLIC_KEY` (env) | asserted before create — an empty key provisions an unreachable container |

`proxmox_lxc_gateway`, `proxmox_lxc_nameserver`, `proxmox_lxc_searchdomain`,
`proxmox_lxc_netmask_bits` and `proxmox_lxc_disk_size` are asserted on the
create path only — when the container does not already exist. A run that just
reconciles bind mounts or startup order on an existing container reads none of
them. `proxmox_lxc_disk_size` must be a GiB value (`8` or `8G`): `pct create`
takes the rootfs size as a bare GiB count.

The container address is written as
`{{ proxmox_lxc_target_ip }}/{{ proxmox_lxc_netmask_bits }}`;
`proxmox_lxc_netmask_bits` defaults to `24` and must be set on any LAN that is
not a /24 (`proxmox_vm_cloudinit_prefix_len` is the VM counterpart). It is
create-time only, and a wrong prefix does not fail the deploy — the SSH probe
still succeeds from a controller inside the accidental /24 — so it surfaces
later as hosts that are reachable only through the gateway.

`proxmox_lxc_nameserver` defaults to the inventory-wide `dns_servers`, and
`proxmox_lxc_admin_user` to `admin_user`. `proxmox_host`, `vmid`,
`proxmox_storage_defaults`, `proxmox_resource_pool(s)` and the
`proxmox_autostart_enabled` / `proxmox_startup_*` trio are read straight from
inventory and keep neutral names.

## Configuration

```yaml
# In hosts.yml
plex:
  vmid: 152
  proxmox_host: hypervisor-01
  # proxmox_lxc_storage: ssd  # Optional: auto-selected based on host role
  proxmox_lxc_cores: 4
  proxmox_lxc_memory: 4096
  proxmox_lxc_disk_size: 32G
  proxmox_lxc_bind_mounts:
    - host_path: /mnt/media
      container_path: /media
      options: "mp=/media,ro=0"
    - host_path: /mnt/ssd/appdata/plex
      container_path: /config
      options: "mp=/config,backup=1"
  proxmox_lxc_gpu_passthrough: true
  proxmox_autostart_enabled: true
```

## Reconciliation vs. create-time-only

| Setting | Behaviour |
|---------|-----------|
| `onboot` / `startup` (order, delay) | **Reconciled** on existing containers — editing `proxmox_autostart_enabled` / `proxmox_startup_order` / `proxmox_startup_delay` and re-running applies them via an idempotent `pct set` (metadata-only, next-boot). |
| Admin SSH `authorized_keys` | **Reconciled** on every run — a rotated `SSH_PUBLIC_KEY` propagates idempotently (atomic temp-file swap, only rewrites on content change). |
| Per-container DNS — `nameserver` (`proxmox_lxc_nameserver`) and `searchdomain` (`proxmox_lxc_searchdomain`) | **Reconciled** on existing containers via an idempotent `pct set --nameserver --searchdomain`, only when `proxmox_lxc_nameserver` is non-empty. `pct set` stages the change as pending; Proxmox applies it on the container's next restart, so nothing is disrupted or rebooted. |
| NIC `firewall=1` flag | **Reconciled** on existing containers. Skipped when the container does not exist and `proxmox_lxc_skip_create` is set. |
| Bind mounts (`proxmox_lxc_bind_mounts`), UID/GID `lxc.idmap` (`proxmox_lxc_idmap_*`), GPU `/dev/dri` passthrough | **Create-time only.** Changing them in inventory does **not** reconcile onto an existing container — live idmap/mount changes are risky and out of scope. Recreate the container, or edit `/etc/pve/lxc/<id>.conf` and `pct restart <id>` manually. |

Why reconcile these at all: Proxmox stores the guest's DNS and network config
itself and re-applies it on start, so an inventory address change that is not
converged is silently re-applied from the stale stored value.

## Bootstrap DNS fallback

The create path installs `openssh-server`/`sudo` over apt. If the configured
nameservers cannot answer that `apt-get update`, `proxmox_lxc_bootstrap_fallback_dns`
(default `1.1.1.1`) is appended to the container's `/etc/resolv.conf` for the
duration of the bootstrap. The restore runs in an `always`, so package-install
failure still returns the container to the configured internal resolvers — a
container left on a public resolver cannot see split-horizon internal names.

## Dependencies

- Proxmox host must be accessible
- For bind mounts: host paths must exist

Every task delegated to the Proxmox node declares `become: true` itself, so the
calling play does not have to. A group driving Proxmox over
`ansible_connection: local` can set `ansible_become: false` and still get the
`pct` / `pveum` writes as root.

## Security

- Unprivileged containers (mapped UIDs)
- UID/GID mapping for file access
- Admin user with sudo for Ansible

## Shared with proxmox_vm

The `firewall=1` NIC repair, the onboot/startup reconcile and the target-node
storage and resource-pool guards are guest-type agnostic. They live in
`proxmox_vm/tasks/guest-nic-firewall.yml`, `proxmox_vm/tasks/guest-startup.yml`
and `proxmox_vm/tasks/assert-target-pools.yml`; this role includes them with
`tasks_from:`. Change them there, and keep both scenarios green.

## Molecule

`molecule test -s default` from the role directory (CI runs the same scenario).
`pct`, `pvesh` and `pvesm` are stubbed and every mutation is logged, so the
cases assert the exact commands issued: storage selection, the pool-validation
and create-input failures, the existence guard, the idmap rewrites, and the DNS
and firewall reconciles converging once and then no-op'ing.
