# weisssrv.infra.adguard_sync

One-way sync of AdGuard Home settings from a **primary** instance to a
**replica**, on a systemd timer. Install it on the primary only —
`adguard_sync_enabled` gates the whole role.

## What it manages

- the `adguardhome-sync` binary, installed through the shared
  `weisssrv.infra.prometheus_exporter` pipeline (version probe → conditional
  download with `checksums.txt` verification → extract → install)
- `/etc/adguardhome-sync.yaml` (mode `0600`, handed to the service through
  systemd `LoadCredential`)
- a sandboxed `DynamicUser` oneshot service + its timer
- a node_exporter textfile writer that runs on every sync (success or failure)

## Variables

| Variable | Meaning | Required |
|---|---|---|
| `adguard_sync_enabled` | Run the role on this host (the primary) | no (`false`) |
| `adguard_sync_version` | Pinned adguardhome-sync release | yes |
| `adguard_sync_origin` | Primary AdGuard API URL | yes |
| `adguard_sync_replica` | Replica AdGuard API URL | yes |
| `adguard_sync_admin_user` / `_admin_password` | Credentials, valid on **both** endpoints | yes |
| `adguard_sync_schedule` | Timer `OnCalendar` | no (`*:0/5`) |
| `adguard_sync_features` | Which config sections to sync | no (see `defaults/main.yml`) |

```yaml
adguard_sync_enabled: true
adguard_sync_origin: "https://dns-01.{{ internal_domain }}"
adguard_sync_replica: "https://dns-02.{{ internal_domain }}"
```

Fronting the two endpoints with an ingress proxy gives you TLS, but it also
makes the sync timer depend on that proxy being up. DNS resolution itself is
unaffected (each instance serves port 53 directly); what stops during such an
outage is drift correction, and the next tick after recovery catches up. Point
the URLs at the instances' HTTP ports directly to avoid the dependency.

## What is and is not synced

Synced: general settings (upstreams, cache, rate limits), filter lists, DNS
rewrites, custom rules, clients, services, DHCP config and static leases.

Not synced: the admin password (Ansible manages it per host) and `tlsConfig` —
cert files and paths are host-local and reconciled per host by
`weisssrv.infra.adguard_home`, so syncing them would fight Ansible.

The sync is **one-way**: edits made on the replica are overwritten on the next
tick, so make every change on the primary.

## Failure alerting

The tool's own metrics API is disabled (`api.port: 0`), so the unit writes a
node_exporter textfile on every run (`ExecStopPost=+…-metrics.sh`, under
`node_exporter_host_textfile_dir`):

- `adguardhome_sync_last_run_success` — `1` on a clean run, `0` on failure
- `adguardhome_sync_last_success_timestamp_seconds` — preserved across failures, so
  a staleness alert measures time-since-last-success

(The metric names keep the upstream tool's `adguardhome_sync_` prefix; only the
role variables are `adguard_sync_`.)

Wire a `Failed`/`Stale` alert pair to those, or a silently broken sync (revoked
password, unreachable replica, schema change) leaves the replica serving stale
rewrites and blocklists indefinitely.

## Operations

```bash
systemctl status adguardhome-sync.timer
journalctl -u adguardhome-sync.service -n 20
systemctl start adguardhome-sync.service      # sync now

# Reset state (DynamicUser state lives under /var/lib/private; the
# /var/lib/adguardhome-sync path is systemd's managed symlink to it)
systemctl stop adguardhome-sync.timer
rm -rf /var/lib/private/adguardhome-sync/*
systemctl start adguardhome-sync.timer

# Compare the two instances
curl -u "$user:$pass" "$origin/control/dns_info"
curl -u "$user:$pass" "$replica/control/dns_info"
```

## Security

- config is `0600` and reaches the unprivileged service via `LoadCredential`
- `DynamicUser` plus the standard sandbox block (`NoNewPrivileges`,
  `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, restricted address
  families)
- credentials come from the site's secret store, never from git; the tasks that
  touch them use `no_log`
