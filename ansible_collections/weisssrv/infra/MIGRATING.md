# Migrating to weisssrv.infra

Every role variable in this collection carries its role's name as a prefix. That
is consumer-visible API, so the rename from an un-prefixed in-tree role is a
breaking change — and a **silent** one: each alias and each default is
`| default(...)`, so a name you miss does not raise `AnsibleUndefinedVariable`,
it quietly takes the role default. `adguard_tls_server_name` left behind in
`group_vars` renders an empty DoT SNI on both resolvers, on every deploy, with a
green play.

This file is the complete old -> new map: every renamed variable, every
externalized default (same name, weisssrv value now empty) and every required
input. It is mechanical on purpose: work through it once per adopted role rather
than trusting a grep.

## How to check a migration

```bash
# 1. Every old name still set anywhere in your inventory:
grep -rnE '^\s*(adguard_|fail2ban_|lxc_|vm_|pve_|ha_|smtp_|nas_|acme_|dns01_|omz_|nvim_|media_mover_|smartd_|zfs_scrub_)' \
  ansible/inventories/

# 2. Nothing in the collection reads it — prove the rename landed:
ANSIBLE_COLLECTIONS_PATH=$PWD:~/.ansible/collections \
  ansible-playbook site.yml --check --diff --limit <one-host>
```

A `--check` run exercises every **required-input assert** (see the last section),
which is the loud half of the contract. The silent half — a renamed *tunable*
that falls back to a role default — is only caught by diffing rendered config,
so diff one host's rendered files (or `pve-firewall compile`, `sshd -T`,
`unbound-checkconf`) before and after adoption.

Neither step finds the third class: a variable whose **name is unchanged** but
whose weisssrv-specific default is now empty. The grep has no old prefix to match
and a defaults diff shows the key on both sides. Those are enumerated in
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty)
below — work that table on its own.

## Names that do NOT need renaming

Values that are conventionally inventory-wide keep their bare names; the roles
alias them with a `default()`. Setting either the bare or the prefixed form
works. The table lives in [README.md](README.md#use) — currently `admin_user`,
`admin_email`, the `ssh_*` quintet, `timezone`, `dns_servers`, `internal_domain`,
`zfs_arc_max_bytes`, `host_dns_servers`, `vm_additional_disks` and the four
`nvidia_*` GPU pins.

Two consequences worth stating explicitly:

- `admin_user`, `timezone`, `ssh_port`, `ssh_permit_root_login`,
  `ssh_password_authentication`, `ssh_pubkey_authentication`,
  `zfs_arc_max_bytes` and `internal_domain` appear in the per-role tables below
  **because the role-owned name changed**, but the bare name still works through
  the alias. They are the only rows you may skip.
- `vm_additional_disks` is read by both `proxmox_vm` (creates and attaches the
  zvols) and `k3s` (mounts them), so one `host_vars` block drives both.

## Externalized defaults (name unchanged, value now empty)

The per-role tables below only record **renames**. This section records the other
half: variables that kept their name while their *value* changed from a
weisssrv-specific default to an empty one the site must now supply. Nothing looks
renamed, so the grep recipe above returns nothing and a defaults diff shows the
key on both sides — and most of these are not asserted, so the play stays green
while the behaviour degrades quietly (an offsite backup with no paths, a
root-equivalent key with no source pin, a `/etc/hosts` pin that never lands).

The table is generated mechanically: for every role, each key present in **both**
defaults files whose weisssrv value was non-empty and whose collection default is
`""` or `[]`.

| Role | Variable | New default | Asserted | Effect if left empty |
|---|---|---|---|---|
| `acme_certs` | `acme_certs_key_from` | `""` | no | no `from="…"` clause on the distribution key in each target's `authorized_keys` — the root-equivalent key becomes usable from any source address |
| `alloy_host` | `alloy_host_loki_url` | `""` | yes | role fails at entry |
| `alloy_host` | `alloy_host_loki_user` | `""` | `https://` only | push runs unauthenticated |
| `alloy_host` | `alloy_host_loki_password` | `""` | `https://` only | push runs unauthenticated |
| `compose_app` | `compose_app_nginx_self_signed_san` | `""` | no | the placeholder cert is generated with no `subjectAltName` |
| `k3s` | `k3s_api_vip` | `""` | yes | role fails at entry |
| `k3s` | `k3s_etcd_snapshot_nfs_server` | `""` | no | the off-node snapshot mount has no server half (read only when `k3s_etcd_snapshot_offnode_enabled`) |
| `k3s` | `k3s_registry_host_pins` | `[]` | no | no `/etc/hosts` pin for the registry — image pulls fall back to cluster DNS |
| `k3s` | `k3s_storage_host_pins` | `[]` | no | no `/etc/hosts` pin for the NFS server — PV mounts fall back to cluster DNS |
| `restic_offsite` | `restic_offsite_repo` | `""` | yes | role fails at entry |
| `restic_offsite` | `restic_offsite_sources` | `[]` | no | the nightly `restic backup` runs with an empty path set |
| `restic_offsite` | `restic_offsite_zvol_sources` | `[]` | no | zvol-backed data (Immich, Nextcloud) is never clone-mounted, so it is never offsited |
| `restic_offsite` | `restic_offsite_excludes` | `[]` | no | churn/cache paths (Prometheus, Loki, the Plex cache) ride into the repo |

The weisssrv values, ready to move into inventory:

```yaml
# acme_certs — the dns-01 resolver the distribution key is pinned to
acme_certs_key_from: 192.168.0.150

# alloy_host — the credentials were env lookups in the role's defaults
alloy_host_loki_url: https://loki.esweiss.com/loki/api/v1/push
alloy_host_loki_user: "{{ lookup('ansible.builtin.env', 'LOKI_PUSH_USER') | default('', true) }}"
alloy_host_loki_password: "{{ lookup('ansible.builtin.env', 'LOKI_PUSH_PASSWORD') | default('', true) }}"

# compose_app
compose_app_nginx_self_signed_san: DNS:*.esweiss.com

# k3s
k3s_api_vip: 192.168.0.161
k3s_etcd_snapshot_nfs_server: "pve-nas-01.{{ internal_domain | default('esweiss.com') }}"
k3s_registry_host_pins:
  - name: "registry.git.{{ external_domain | default('ericsweiss.com') }}"
    ip: 192.168.0.101
k3s_storage_host_pins:
  - name: "pve-nas-01.{{ internal_domain | default('esweiss.com') }}"
    ip: 192.168.0.102

# restic_offsite
restic_offsite_repo: rclone:b2:weisssrv-backup/restic
restic_offsite_sources:
  - name: backups
    mountpoint: /mnt/tank/backups
  - name: share
    mountpoint: /mnt/tank/share
  - name: appdata
    mountpoint: /mnt/ssd/appdata
  - name: databases
    mountpoint: /mnt/ssd/databases
  - name: k3s-etcd
    mountpoint: /mnt/ssd/k3s-etcd
restic_offsite_zvol_sources:
  - name: immich-data
    zvol: tank/immich-data/disk
    fstype: ext4
    mount_opts: ro,noload
  - name: nextcloud-data
    zvol: tank/nextcloud-data/disk
    fstype: ext4
    mount_opts: ro,noload
restic_offsite_excludes:
  - /mnt/restic-src/appdata/prometheus/**
  - /mnt/restic-src/appdata/loki/**
  - /mnt/restic-src/appdata/authentik/postgres/**
  - /mnt/restic-src/appdata/mealie/postgres/**
  - /mnt/restic-src/appdata/gitlab/**
  - /mnt/restic-src/appdata/immich/**
  - /mnt/restic-src/appdata/nextcloud/**
  - "/mnt/restic-src/appdata/plex/Library/Application Support/Plex Media Server/Cache/**"
  - "/mnt/restic-src/appdata/plex/Library/Application Support/Plex Media Server/Metadata/**"
  - "/mnt/restic-src/appdata/plex/Library/Application Support/Plex Media Server/Media/**"
```

### Same class, different name

Four more values were externalized *and* renamed (or promoted out of a template),
so they do appear in the tables below — they are listed here too because the
migration step is identical: supply the value or lose the behaviour.

- `nas_storage` **archive backup**: the dataset inventory was literal in
  `archive-backupctl.sh.j2` (`SRC_LIST`, `POOL_DST`, `VZDUMP_TARGET`) and is now
  `nas_storage_archive_backup_pool: archive`,
  `nas_storage_archive_backup_vzdump_target: tank/proxmox` and
  `nas_storage_archive_backup_sources: [tank/share, tank/backups,
  tank/nextcloud-data, tank/proxmox, tank/immich-data, ssd/appdata,
  ssd/databases, ssd/k3s-etcd]`, behind `nas_storage_archive_backup_enabled`
  (default false). Pool and sources are asserted when the opt-in is on; leaving
  the opt-in off on a host that already runs the timer **removes** the units and
  the script rather than orphaning them.
- `restic_offsite_cache_dir`: same name, but no longer a default at all — it is a
  required input, asserted alongside `restic_offsite_repo`. weisssrv's value was
  `/mnt/ssd/appdata/.restic-cache`.
- `k3s_tls_sans`: the apiserver SAN list was hardcoded as
  `k3s.{{ internal_domain }}`; it is now an input that defaults to
  `['k3s.' ~ k3s_internal_domain]` and collapses to `[]` when neither
  `k3s_internal_domain` nor the inventory-wide `internal_domain` is set. The VIP,
  `inventory_hostname` and `ansible_host` are still added by the template.
- `proxmox_firewall` address data: the CIDR lists and the seven per-application
  `[group ...]` blocks were literal in the template and are now empty-by-default
  inputs — see [proxmox_firewall](#proxmox_firewall) below for the full list.

## Per-role renames

Rows marked (inv) were never role defaults — they are names a site set directly
in `group_vars`/`host_vars`, so they will not show up in a defaults diff.

### acme_certs

| Old | New |
|---|---|
| `acme_email` | `acme_certs_email` |
| `acme_local_cert_group` | `acme_certs_local_cert_group` |
| `acme_sh_tarball_sha256` | `acme_certs_sh_tarball_sha256` |
| `acme_sh_version` | `acme_certs_sh_version` |
| `internal_domain` | `acme_certs_domain` (the cert's base domain — a dedicated required input, no longer the shared global) |
| `local_cert_dir` | `acme_certs_local_cert_dir` |
| `cert_distribution_targets` (inv) | `acme_certs_distribution_targets` |
| `dns01_ssh_private_key` (inv) | `acme_certs_ssh_private_key` (asserted) |
| `dns01_ssh_public_key` (inv) | `acme_certs_ssh_public_key` (asserted) |

`acme_certs_key_from` keeps its name but is now empty by default — see
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

### adguard_home

| Old | New |
|---|---|
| `adguard_admin_user` | `adguard_home_admin_user` |
| `adguard_cache_enabled` | `adguard_home_cache_enabled` |
| `adguard_cache_optimistic` | `adguard_home_cache_optimistic` |
| `adguard_cache_size` | `adguard_home_cache_size` |
| `adguard_cache_ttl_max` | `adguard_home_cache_ttl_max` |
| `adguard_cache_ttl_min` | `adguard_home_cache_ttl_min` |
| `adguard_cert_path` | `adguard_home_cert_path` |
| `adguard_dhcp_enabled` | `adguard_home_dhcp_enabled` |
| `adguard_disable_ipv6` | `adguard_home_disable_ipv6` |
| `adguard_dns_port` | `adguard_home_dns_port` |
| `adguard_doq_port` | `adguard_home_doq_port` |
| `adguard_dot_port` | `adguard_home_dot_port` |
| `adguard_enable_dnssec` | `adguard_home_enable_dnssec` |
| `adguard_fallback_dns` | `adguard_home_fallback_dns` |
| `adguard_group` | `adguard_home_group` |
| `adguard_http_port` | `adguard_home_http_port` |
| `adguard_https_port` | `adguard_home_https_port` |
| `adguard_install_path` | `adguard_home_install_path` |
| `adguard_protection_enabled` | `adguard_home_protection_enabled` |
| `adguard_ratelimit` | `adguard_home_ratelimit` |
| `adguard_ratelimit_whitelist` | `adguard_home_ratelimit_whitelist` |
| `adguard_resolve_clients` | `adguard_home_resolve_clients` |
| `adguard_tls_enabled` | `adguard_home_tls_enabled` |
| `adguard_tls_server_name` | `adguard_home_tls_server_name` |
| `adguard_upstream_dns` | `adguard_home_upstream_dns` |
| `adguard_upstream_mode` | `adguard_home_upstream_mode` |
| `adguard_use_private_ptr_resolvers` | `adguard_home_use_private_ptr_resolvers` |
| `adguard_use_private_tmp` | `adguard_home_use_private_tmp` |
| `adguard_use_protect_system` | `adguard_home_use_protect_system` |
| `adguard_user` | `adguard_home_user` |
| `skip_adguard_api_config` | `adguard_home_skip_api_config` |
| `adguard_admin_password` (inv) | `adguard_home_admin_password` |
| `adguard_rewrites` (inv) | `adguard_home_rewrites` |
| `adguard_user_rules` (inv) | `adguard_home_user_rules` |

New gates with no predecessor: `adguard_home_is_primary` (the rewrite/filtering
API pass runs on the primary only) and `adguard_home_skip_resolv_conf_update`.

### adguard_sync

| Old | New |
|---|---|
| `adguardhome_sync_features` | `adguard_sync_features` |
| `adguardhome_sync_schedule` | `adguard_sync_schedule` |
| `adguardhome_sync_origin` (inv) | `adguard_sync_origin` |
| `adguardhome_sync_replica` (inv) | `adguard_sync_replica` |
| `adguardhome_sync_version` (inv) | `adguard_sync_version` |
| `adguard_admin_user` (inv) | `adguard_sync_admin_user` |
| `adguard_admin_password` (inv) | `adguard_sync_admin_password` |

The role is now gated on `adguard_sync_enabled` (default false) — set it true on
the primary only.

### alloy_host

No renames. Four values that used to default to site data are now **required
inputs** with an empty default: `alloy_host_version`, `alloy_host_loki_url`,
`alloy_host_loki_user`, `alloy_host_loki_password` (the last two only for an
`https://` endpoint). The env lookups that used to sit in the role's defaults
(`LOKI_PUSH_USER` / `LOKI_PUSH_PASSWORD`) move to the caller. The three that were
role defaults are listed with their weisssrv values under
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty);
`alloy_host_version` was always inventory-supplied.

### base

| Old | New |
|---|---|
| `admin_user` | `base_admin_user` (alias: `admin_user`) |
| `common_packages` | `base_common_packages` |
| `fail2ban_default_bantime` | `base_fail2ban_default_bantime` |
| `fail2ban_default_findtime` | `base_fail2ban_default_findtime` |
| `fail2ban_default_maxretry` | `base_fail2ban_default_maxretry` |
| `fail2ban_email_action` | `base_fail2ban_email_action` |
| `fail2ban_email_dest` | `base_fail2ban_email_dest` |
| `fail2ban_email_enabled` | `base_fail2ban_email_enabled` |
| `fail2ban_email_sender` | `base_fail2ban_email_sender` |
| `fail2ban_enabled` | `base_fail2ban_enabled` |
| `fail2ban_ignoreip` | `base_fail2ban_ignoreip` |
| `fail2ban_pveproxy_bantime` | `base_fail2ban_pveproxy_bantime` |
| `fail2ban_pveproxy_enabled` | `base_fail2ban_pveproxy_enabled` |
| `fail2ban_pveproxy_findtime` | `base_fail2ban_pveproxy_findtime` |
| `fail2ban_pveproxy_maxretry` | `base_fail2ban_pveproxy_maxretry` |
| `fail2ban_pveproxy_port` | `base_fail2ban_pveproxy_port` |
| `fail2ban_recidive_bantime` | `base_fail2ban_recidive_bantime` |
| `fail2ban_recidive_enabled` | `base_fail2ban_recidive_enabled` |
| `fail2ban_recidive_findtime` | `base_fail2ban_recidive_findtime` |
| `fail2ban_recidive_maxretry` | `base_fail2ban_recidive_maxretry` |
| `fail2ban_sshd_bantime` | `base_fail2ban_sshd_bantime` |
| `fail2ban_sshd_enabled` | `base_fail2ban_sshd_enabled` |
| `fail2ban_sshd_findtime` | `base_fail2ban_sshd_findtime` |
| `fail2ban_sshd_maxretry` | `base_fail2ban_sshd_maxretry` |
| `fail2ban_sshd_port` | `base_fail2ban_sshd_port` |
| `ssh_password_authentication` | `base_ssh_password_authentication` (alias) |
| `ssh_permit_root_login` | `base_ssh_permit_root_login` (alias) |
| `ssh_port` | `base_ssh_port` (alias) |
| `ssh_pubkey_authentication` | `base_ssh_pubkey_authentication` (alias) |
| `ssh_service_name` | `base_ssh_service_name` |
| `timezone` | `base_timezone` (alias: `timezone`) |
| `vm_packages` | `base_vm_packages` |

New: `base_ssh_authorized_keys` (alias `ssh_authorized_keys`), the
`base_skip_{ssh,dns,timezone}_config` / `base_skip_sudoers_validation` gates, and
the resolver-host knobs `base_is_resolver_host` / `base_resolver_probe_name` /
`base_bootstrap_dns_servers`.

### docker_engine

| Old | New |
|---|---|
| `docker_ce_version` (inv) | `docker_engine_ce_version` |
| `containerd_version` (inv) | `docker_engine_containerd_version` |
| `docker_buildx_plugin_version` (inv) | `docker_engine_buildx_plugin_version` |
| `docker_compose_plugin_version` (inv) | `docker_engine_compose_plugin_version` |

### k3s

| Old | New |
|---|---|
| `kube_vip_interface` | `k3s_kube_vip_interface` |
| `kube_vip_version` | `k3s_kube_vip_version` |
| `skip_k3s_gpu_install` | `k3s_skip_gpu_install` |

`k3s_api_vip`, `k3s_registry_host_pins`, `k3s_storage_host_pins` and
`k3s_etcd_snapshot_nfs_server` keep their names but are now empty by default —
see [Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

New: `k3s_internal_domain` / `k3s_tls_sans` (the apiserver SAN list is now an
input rather than a hardcoded `k3s.<internal_domain>`), `k3s_additional_disks`
(aliases `vm_additional_disks`), `k3s_server_group`, `k3s_skip_install`, and the
four GPU pins `k3s_gpu_driver_version`, `k3s_gpu_container_toolkit_version`,
`k3s_gpu_cuda_keyring_version`, `k3s_gpu_cuda_keyring_sha256` (each aliases the
inventory-wide `nvidia_*` name of the same suffix).

### nas_storage

| Old | New |
|---|---|
| `media_mover_bwlimit` | `nas_storage_media_mover_bwlimit` |
| `media_mover_cpu_weight` | `nas_storage_media_mover_cpu_weight` |
| `media_mover_io_class` | `nas_storage_media_mover_io_class` |
| `media_mover_io_priority` | `nas_storage_media_mover_io_priority` |
| `media_mover_io_weight` | `nas_storage_media_mover_io_weight` |
| `media_mover_nice` | `nas_storage_media_mover_nice` |
| `nas_appdata_base` | `nas_storage_appdata_base` |
| `nas_appdata_dirs` | `nas_storage_appdata_dirs` |
| `nas_appdata_group` | `nas_storage_appdata_group` |
| `nas_appdata_mode` | `nas_storage_appdata_mode` |
| `nas_appdata_owner` | `nas_storage_appdata_owner` |
| `nas_backup_apps_base` | `nas_storage_backup_apps_base` |
| `nas_backup_artifact_apps` | `nas_storage_backup_artifact_apps` |
| `nas_backup_artifact_metrics_enabled` | `nas_storage_backup_artifact_metrics_enabled` |
| `zfs_arc_max_bytes` | `nas_storage_zfs_arc_max_bytes` (alias: `zfs_arc_max_bytes`) |
| `media_mover_enabled` (inv) | `nas_storage_media_mover_enabled` |
| `media_mover_src` (inv) | `nas_storage_media_mover_src` |
| `media_mover_dst` (inv) | `nas_storage_media_mover_dst` |
| `media_mover_schedule` (inv) | `nas_storage_media_mover_schedule` |
| `mergerfs_mounts` (inv) | `nas_storage_mergerfs_mounts` |
| `nfs_exports` (inv) | `nas_storage_exports` |
| `samba_shares` (inv) | `nas_storage_samba_shares` |
| `zfs_pools` (inv) | `nas_storage_zfs_pools` |
| `zfs_scrub_enabled` (inv) | `nas_storage_zfs_scrub_enabled` |
| `zfs_scrub_schedule` (inv) | `nas_storage_zfs_scrub_schedule` |
| `smartd_enabled` (inv) | `nas_storage_smartd_enabled` |
| `smartd_archive_disks` (inv) | `nas_storage_smartd_archive_disks` |
| `smartd_nvme_disks` (inv) | `nas_storage_smartd_nvme_disks` |
| `smartd_ssd_disks` (inv) | `nas_storage_smartd_ssd_disks` |
| `smartd_tank_disks` (inv) | `nas_storage_smartd_tank_disks` |
| `nas_encrypted_bind_sources` (inv) | `nas_storage_encrypted_bind_sources` |
| `nas_swap_clean_enabled` (inv) | `nas_storage_swap_clean_enabled` |
| `nas_swap_clean_schedule` (inv) | `nas_storage_swap_clean_schedule` |
| `nas_swap_clean_stop_guests` (inv) | `nas_storage_swap_clean_stop_guests` |

The archive backup is now **opt-in and site-supplied**: it was an in-role dataset
inventory, and is now `nas_storage_archive_backup_enabled` (default false) plus
the required `_pool` / `_sources` (and optional `_vzdump_target`). Leaving the
opt-in unset on a host that already runs the timer **removes** the units and the
script rather than orphaning them. weisssrv's literal values are under
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

`samba_nas_password` is no longer a variable — the role reads the
`SAMBA_NAS_PASSWORD` environment variable, and warns (does not fail) when unset.

### node_exporter_host

No renames. New: `node_exporter_host_proxmox` gates the Proxmox-only textfile
collectors.

### postfix_null_client

| Old | New |
|---|---|
| `mail_aliases` | `postfix_null_client_aliases` |
| `postfix_config` | `postfix_null_client_config` |
| `smtp_relay_host` | `postfix_null_client_relay_host` |
| `smtp_relay_port` | `postfix_null_client_relay_port` |
| `postfix_sasl_user` (inv) | `postfix_null_client_sasl_user` |
| `postfix_sasl_password` (inv) | `postfix_null_client_sasl_password` |
| `root_email_alias` (inv) | `postfix_null_client_root_alias` |

New required input: `postfix_null_client_mail_domain` (appended to
`inventory_hostname` to form `myhostname`).

### prometheus_exporter / textfile_collector / apt_signed_repo / compose_app / encrypted_swap / nfs_tls / nic_tuning / vfio_passthrough / zfs_arc_cap

No renames — these roles were already prefixed or are new.
`compose_app_nginx_self_signed_san` keeps its name but is now empty by default —
see [Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

### proxmox_backup

| Old | New |
|---|---|
| `pve_storage` | `proxmox_backup_storage` |
| `pve_vzdump_jobs` | `proxmox_backup_vzdump_jobs` |

### proxmox_firewall

| Old | New |
|---|---|
| `pve_firewall_aliases` | `proxmox_firewall_extra_aliases` (host-backed aliases now derive from a per-host `firewall_alias` / `firewall_alias_comment`; this list is for addresses that are not inventory hosts) |
| `pve_firewall_config_dir` | `proxmox_firewall_config_dir` |
| `pve_firewall_enabled` | `proxmox_firewall_enabled` |
| `pve_firewall_log_level_in` | `proxmox_firewall_log_level_in` |
| `pve_firewall_node_dir` | `proxmox_firewall_node_dir` |
| `pve_firewall_skip_pveum` | `proxmox_firewall_skip_pveum` |
| `pve_firewall_staging_dir` | `proxmox_firewall_staging_dir` |

Address data that used to be literal in the template is now input, and **empty by
default** — a missed value silently drops rules:
`proxmox_firewall_admin_lan_cidrs` (required, asserted),
`proxmox_firewall_admin_ts_cidrs`, `proxmox_firewall_smb_client_cidrs`,
`proxmox_firewall_immich_ml_clients`, `proxmox_firewall_wan_wireguard_vips`.
`proxmox_firewall_security_groups` replaces the seven literal per-application
`[group ...]` blocks; it ships an example set (see
[README.md](README.md)) that a site is expected to replace wholesale.

### proxmox_ha

| Old | New |
|---|---|
| `ha_resources` | `proxmox_ha_resources` |
| `ha_rules` | `proxmox_ha_rules` |
| `storage_replication_jobs` | `proxmox_ha_replication_jobs` |

### proxmox_lxc

| Old | New |
|---|---|
| `lxc_admin_user` | `proxmox_lxc_admin_user` |
| `lxc_bridge` | `proxmox_lxc_bridge` |
| `lxc_cores` | `proxmox_lxc_cores` |
| `lxc_disk_size` | `proxmox_lxc_disk_size` |
| `lxc_gateway` | `proxmox_lxc_gateway` (required on the create path; no default) |
| `lxc_keyctl` | `proxmox_lxc_keyctl` |
| `lxc_memory` | `proxmox_lxc_memory` |
| `lxc_nameserver` | `proxmox_lxc_nameserver` |
| `lxc_nesting` | `proxmox_lxc_nesting` |
| `lxc_onboot` | `proxmox_lxc_onboot` |
| `lxc_searchdomain` | `proxmox_lxc_searchdomain` |
| `lxc_ssh_public_keys` | `proxmox_lxc_ssh_public_keys` |
| `lxc_startup_delay` | `proxmox_lxc_startup_delay` |
| `lxc_startup_order` | `proxmox_lxc_startup_order` |
| `lxc_swap` | `proxmox_lxc_swap` |
| `lxc_template` | `proxmox_lxc_template` |
| `lxc_template_storage` | `proxmox_lxc_template_storage` |
| `lxc_unprivileged` | `proxmox_lxc_unprivileged` |
| `lxc_bind_mounts` (inv) | `proxmox_lxc_bind_mounts` |
| `lxc_storage` (inv) | `proxmox_lxc_storage` |
| `lxc_gpu_passthrough` (inv) | `proxmox_lxc_gpu_passthrough` |

New: `proxmox_lxc_internal_domain` (aliases `internal_domain`; feeds
`proxmox_lxc_searchdomain`), `proxmox_lxc_bootstrap_fallback_dns`, and the
`proxmox_lxc_idmap_*` quartet.

### proxmox_vm

| Old | New |
|---|---|
| `cloud_image_name` | `proxmox_vm_cloud_image_name` |
| `cloud_image_url` | `proxmox_vm_cloud_image_url` |
| `cloudinit_dns` | `proxmox_vm_cloudinit_dns` (alias: `dns_servers`) |
| `cloudinit_gateway` | `proxmox_vm_cloudinit_gateway` (required on the Linux create path; no default) |
| `cloudinit_user` | `proxmox_vm_cloudinit_user` (alias: `admin_user`) |
| `virtio_win_url` | `proxmox_vm_virtio_win_url` |
| `vm_agent_enabled` | `proxmox_vm_agent_enabled` |
| `vm_bridge` | `proxmox_vm_bridge` |
| `vm_cores` | `proxmox_vm_cores` |
| `vm_cpu_type` | `proxmox_vm_cpu_type` |
| `vm_disk_size` | `proxmox_vm_disk_size` |
| `vm_guest_type` | `proxmox_vm_guest_type` |
| `vm_hostpci` | `proxmox_vm_hostpci` |
| `vm_install_iso` | `proxmox_vm_install_iso` |
| `vm_iso_storage` | `proxmox_vm_iso_storage` |
| `vm_iso_storage_path` | `proxmox_vm_iso_storage_path` |
| `vm_memory` | `proxmox_vm_memory` |
| `vm_ostype` | `proxmox_vm_ostype` |
| `vm_virtio_iso` | `proxmox_vm_virtio_iso` |
| `vm_windows_machine` | `proxmox_vm_windows_machine` |
| `vm_windows_ostype` | `proxmox_vm_windows_ostype` |
| `vm_windows_vga` | `proxmox_vm_windows_vga` |
| `vm_balloon` (inv) | `proxmox_vm_balloon` |
| `virtio_win_version` (inv) | `proxmox_vm_virtio_win_version` |
| `virtio_win_checksum` (inv) | `proxmox_vm_virtio_win_checksum` |
| `vm_storage` (inv) | `proxmox_storage` (kept neutral — it is the role's inventory contract, not a role tunable) |

`vm_additional_disks` is **not** renamed: `proxmox_vm_additional_disks` aliases
it, exactly as `k3s_additional_disks` does, so one `host_vars` block still feeds
both zvol creation and zvol mounting. New: `proxmox_vm_cloud_image_checksum`,
`proxmox_vm_cloud_image_dir`, `proxmox_vm_cloudinit_prefix_len`.

### qol

| Old | New |
|---|---|
| `admin_user` | `qol_admin_user` (alias: `admin_user`) |
| `nvim_colorscheme` | `qol_nvim_colorscheme` |
| `nvim_plugins` | `qol_nvim_plugins` |
| `omz_commit` | `qol_omz_commit` |
| `omz_plugins` | `qol_omz_plugins` |
| `omz_theme` | `qol_omz_theme` |

### resolv_conf

No renames. New: `resolv_conf_internal_domain` (aliases `internal_domain`) drives
`resolv_conf_search_domains`; `resolv_conf_nameservers` is a required input;
`resolv_conf_unsafe_writes` covers the bind-mounted-file case.

### restic_offsite

| Old | New |
|---|---|
| `rclone_deb_sha256` | `restic_offsite_rclone_deb_sha256` |
| `rclone_version` | `restic_offsite_rclone_version` |
| `restic_version` | `restic_offsite_restic_version` |
| `restic_repo_password` (inv) | `restic_offsite_repo_password` |
| `b2_key_id` / `restic_key_id` (inv) | `restic_offsite_b2_key_id` |
| `b2_application_key` / `restic_application_key` (inv) | `restic_offsite_b2_application_key` |

`restic_offsite_cache_dir` keeps its name but is no longer a default: it is a
required input, asserted alongside `restic_offsite_repo`. `restic_offsite_repo`,
`_sources`, `_zvol_sources` and `_excludes` also keep their names and are now
empty — the weisssrv values are under
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

### smtp_relay

| Old | New |
|---|---|
| `mail_aliases` | `smtp_relay_aliases` |
| `smtp_tls_cert_dir` | `smtp_relay_tls_cert_dir` |
| `smtp_relay_host` (inv) | `smtp_relay_upstream` (the smarthost `[host]:port` the relay forwards to) |
| `smtp_gmail_user` (inv) | `smtp_relay_upstream_user` |
| `smtp_gmail_password` (inv) | `smtp_relay_upstream_password` |
| `smtp_relay_user` (inv) | `smtp_relay_sasl_user` |
| `smtp_relay_password` (inv) | `smtp_relay_sasl_password` |
| `smtp_submission_config` (inv) | `smtp_relay_submission_config` |
| `smtp_submission_enabled` (inv) | `smtp_relay_submission_enabled` |

`smtp_relay_hostname` and `smtp_relay_origin` derive from
`smtp_relay_internal_domain` (alias: `internal_domain`); both stay empty when it
is unset, and the effective-config assert names them rather than rendering an
empty `relayhost`.

### tailscale

No renames. `tailscale_auth_key` is gone: the key is read from the
`TAILSCALE_AUTH_KEY` **environment variable** so it never reaches argv or a fact.
New: `tailscale_version` and `tailscale_gpg_fingerprint` are now role defaults
(pinned) rather than site values.

### unbound

No renames. The managed drop-in moved from `<site>.conf` to
`unbound_dropin_name` (default `managed.conf`); `unbound_legacy_dropins` lists
names removed on convergence, so a site that used a differently named drop-in
adds it there. New: `unbound_use_caps_for_id`.

### unbound_exporter / zfs_exporter

No renames. Each now carries its own `*_version` + `*_checksum` defaults instead
of reading a shared inventory pin.

### zfs_encryption

No renames. New: `zfs_encryption_internal_domain` (aliases `internal_domain`)
derives `zfs_encryption_connect_url`; set the URL directly to decouple.

### zvol_mount

No renames. New: `zvol_mount_device_id_prefix`.

## Required inputs (asserted at role entry)

A value with no safe generic default is asserted by name rather than failing
inside a template or shell command. These are the loud failures — everything else
falls back silently, which is why the tables above matter.

| Role | Asserted | Condition |
|---|---|---|
| `acme_certs` | `acme_certs_domain`, `acme_certs_email`, `acme_certs_ssh_private_key`, `acme_certs_ssh_public_key`, plus the dnsapi hook | always |
| `adguard_home` | `adguard_home_admin_password` | always (and again before the API pass) |
| `adguard_sync` | `adguard_sync_version`, `_origin`, `_replica`, `_admin_user`, `_admin_password` | when `adguard_sync_enabled` |
| `alloy_host` | `alloy_host_version`, `alloy_host_loki_url`; `_loki_user`/`_loki_password` | credentials only for an `https://` endpoint |
| `base` | a surviving SSH login path (`base_admin_user` + `base_ssh_authorized_keys`, or `base_ssh_permit_root_login`, or `base_ssh_password_authentication`) | when SSH config is not skipped |
| `docker_engine` | `docker_engine_ce_version`, `_containerd_version`, `_buildx_plugin_version`, `_compose_plugin_version` | unless `docker_engine_skip_install` |
| `k3s` | `k3s_version`, `k3s_api_vip`, `k3s_token` (servers) / `k3s_agent_token` (agents) | always |
| `k3s` | `k3s_gpu_driver_version`, `_container_toolkit_version`, `_cuda_keyring_version`, `_cuda_keyring_sha256` | when `k3s_gpu_node` and not `k3s_skip_gpu_install` |
| `nas_storage` | `nas_storage_archive_backup_pool`, `_sources` | when `nas_storage_archive_backup_enabled` |
| `nas_storage` | `nas_storage_media_mover_src`, `_dst` | when `nas_storage_media_mover_enabled` |
| `postfix_null_client` | `postfix_null_client_mail_domain`, `_relay_host`, `_sasl_user`, `_sasl_password` | always |
| `proxmox_firewall` | `proxmox_firewall_admin_lan_cidrs` | always (an empty set locks :22 and :8006 out on every node) |
| `proxmox_lxc` | `proxmox_lxc_gateway`, `proxmox_lxc_nameserver`, `SSH_PUBLIC_KEY` (env) | unless `proxmox_lxc_skip_create` |
| `proxmox_vm` | `proxmox_vm_cloudinit_gateway`, `proxmox_vm_cloudinit_dns`, `SSH_PUBLIC_KEY` (env) | Linux guests, unless `proxmox_vm_skip_create` |
| `proxmox_vm` | `proxmox_vm_install_iso` | Windows guests |
| `resolv_conf` | `resolv_conf_nameservers` | always |
| `restic_offsite` | `restic_offsite_repo`, `restic_offsite_cache_dir`, `restic_offsite_repo_password`, the rclone pin pair | when enabled |
| `smtp_relay` | `smtp_relay_config` identity (`relayhost`/`myhostname`/`myorigin`), `_upstream_user`, `_upstream_password`, `_sasl_user`, `_sasl_password` | always |
| `vfio_passthrough` | `vfio_passthrough_pci_ids` | when passthrough is enabled |
| `zfs_encryption` | `zfs_encryption_connect_url`, Connect token | when `zfs_encryption_pools` is non-empty |

Values read from the environment rather than a variable, because they must not
reach argv or a fact: `SSH_PUBLIC_KEY` (proxmox_vm, proxmox_lxc),
`TAILSCALE_AUTH_KEY` (tailscale), `SAMBA_NAS_PASSWORD` (nas_storage).
