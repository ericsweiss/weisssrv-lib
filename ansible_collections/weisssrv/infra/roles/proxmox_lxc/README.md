# Proxmox LXC Role

Provisions unprivileged LXC containers on Proxmox VE: bind mounts, GPU
passthrough, UID/GID mapping, and an admin user bootstrapped for Ansible.

## What This Role Manages

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

## Storage Selection

Storage is automatically selected based on the Proxmox host's role:

`proxmox_storage_defaults` maps the Proxmox host's `proxmox_role` to a storage
id; `local-ssd` is used when the role is unknown. Override per-container with
`proxmox_lxc_storage`.

## Required inputs (no defaults)

| Variable | Why there is no default |
|---|---|
| `proxmox_host` | which node provisions the container |
| `vmid` | falls back to the last octet of `proxmox_lxc_target_ip`, which is not a contract |
| `proxmox_lxc_gateway` | no generic value; a wrong one silently strands the container |
| `internal_domain` | the search domain (`proxmox_lxc_searchdomain` defaults to it) |
| `SSH_PUBLIC_KEY` (env) | asserted before create — an empty key provisions an unreachable container |

`proxmox_lxc_gateway`/`proxmox_lxc_nameserver` are asserted only when
`proxmox_lxc_skip_create` is false — the create tasks are the sole readers, so a
run that just reconciles bind mounts or startup order on an existing container
needs neither.

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
  proxmox_host: pve-nas-01
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
| Admin SSH `authorized_keys` | **Reconciled** on every run — a rotated `SSH_PUBLIC_KEY` propagates idempotently (atomic temp-file swap, only rewrites on content change). No longer a one-shot create-time `>` overwrite. |
| NIC `firewall=1` flag | **Reconciled** on existing containers. |
| Bind mounts (`proxmox_lxc_bind_mounts`), UID/GID `lxc.idmap` (`proxmox_lxc_idmap_*`), GPU `/dev/dri` passthrough | **Create-time only.** Changing them in inventory does **not** reconcile onto an existing container — live idmap/mount changes are risky and out of scope. Recreate the container, or edit `/etc/pve/lxc/<id>.conf` and `pct restart <id>` manually. |

## Files

- `tasks/main.yml` - Main orchestration
- `defaults/main.yml` - Defaults

## Dependencies

- Proxmox host must be accessible
- For bind mounts: host paths must exist

## Security

- Unprivileged containers (mapped UIDs)
- UID/GID mapping for file access
- Admin user with sudo for Ansible
