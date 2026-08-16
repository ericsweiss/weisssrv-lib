# weisssrv.infra.nextcloud

Deploys [Nextcloud](https://nextcloud.com/) on a Debian guest as a single
docker-compose project (web + PostgreSQL + Redis + cron + exporter), fronted by
a host nginx that terminates TLS and proxies to the loopback-bound web port.

## What it manages

1. **Storage** — optional extra block devices mounted first
   (`nextcloud_additional_disks`, `zvol_mount` schema), then the app dir, the
   `html` + `data` trees (pre-chowned to `www-data`) and the compose dir.
2. **Docker Engine** — pinned engine + plugins with a `dpkg` hold, via
   `compose_app` → `docker_engine`; container logs go to journald.
3. **The compose stack** — `docker-compose.yml` plus a mode-`0600` `.env` that
   holds every secret, run by the shared `compose_app` systemd unit
   (`nextcloud-compose.service`), which orders on the data mounts. An opt-in
   `postgres-exporter` sidecar adds database-level metrics alongside the
   application-level `nextcloud-exporter`.
4. **Host nginx** — the shared `compose_app` nginx flow with this role's site
   template (TLS 1.3 only, unlimited body size, websockets, DAV discovery
   redirects, `real_ip` resolution). A self-signed placeholder is seeded until
   the site's cert distributor (`acme_certs`) delivers the real certificate.
5. **`occ` configuration** — the serverinfo token the exporter authenticates
   with, outgoing SMTP (when a relay host is set), and OIDC SSO (when enabled).
6. **Backups** — a nightly `pg_dump` timer whose wrapper emits
   `nextcloud_backup_*` node_exporter textfile metrics through `compose_app`'s
   shared `write_prom_metrics` helper.

## Variables

| Variable | Meaning | Required |
|---|---|---|
| `nextcloud_version` / `_postgres_version` / `_redis_version` / `_exporter_version` | Image pins (`_redis_version` aliases `redis_version`) | yes |
| `nextcloud_external_host` / `nextcloud_internal_host` | User-facing names; default to `cloud.<external_domain>` / `cloud.<internal_domain>` | at least one |
| `nextcloud_external_domain` / `nextcloud_internal_domain` | Alias the inventory-wide `external_domain` / `internal_domain` | no (`""`) |
| `nextcloud_admin_user` / `_admin_password` | Break-glass admin (env `NEXTCLOUD_ADMIN_PASSWORD`) | yes |
| `nextcloud_db_name` / `_db_user` / `_db_password` | PostgreSQL role (env `NEXTCLOUD_POSTGRES_PASSWORD`) | password yes |
| `nextcloud_serverinfo_token` | serverinfo app token the exporter uses (env `NEXTCLOUD_SERVERINFO_TOKEN`) | yes |
| `nextcloud_app_dir` / `_postgres_dir` / `_data_mount` | Volume roots (compose+html+local backups, PGDATA, bulk data) | no (`/mnt/nextcloud-*`) |
| `nextcloud_additional_disks` | Block devices to mount first; aliases `vm_additional_disks` | no (`[]`) |
| `nextcloud_uid` / `_gid` | Owner of `html` + `data` (the image's `www-data`) | no (`33`) |
| `nextcloud_docker_subnet` / `_trusted_proxies` | Compose bridge subnet and the proxies Nextcloud trusts | no |
| `nextcloud_nginx_real_ip_trusted_addresses` | Proxy sources whose `X-Forwarded-For` nginx trusts | no (`[]`) |
| `nextcloud_nginx_enabled` / `_nginx_cert_dir` / `_nginx_ssl_certificate(_key)` / `_nginx_server_names` | Host TLS front end | no |
| `nextcloud_http_bind_address` / `_http_bind_port` / `_exporter_port` | Published ports | no (`127.0.0.1:8080`, `9205`) |
| `nextcloud_exporter_bind_address` | Interface the app exporter publishes on (unauthenticated — narrow it, or scope it at the firewall) | no (`0.0.0.0`) |
| `nextcloud_postgres_exporter_enabled` | Add a `postgres-exporter` sidecar (DB-level metrics) | no (`false`) |
| `nextcloud_postgres_exporter_version` / `_image` | Its image pin; the image derives from the version, or override it outright | when enabled |
| `nextcloud_postgres_exporter_port` | Host port for that exporter (unauthenticated — scope it at the firewall) | no (`9187`) |
| `nextcloud_postgres_exporter_bind_address` | Interface that exporter publishes on | no (tracks `nextcloud_exporter_bind_address`) |
| `nextcloud_php_memory_limit` / `_php_upload_limit` | PHP tuning | no (`1024M`, `16G`) |
| `nextcloud_oidc_enabled` | Wire OIDC SSO through the `user_oidc` app | no (`false`) |
| `nextcloud_oidc_discovery_uri` / `_client_id` / `_client_secret` | Provider discovery + credentials | when OIDC |
| `nextcloud_oidc_provider_id` / `_scope` / `_mapping_*` / `_group_provisioning` / `_unique_uid` / `_sso_only` | Provider details; `_sso_only` hides the local login form | no |
| `nextcloud_oidc_allow_local_remote_servers` | Permit server-side fetches to private addresses (see below) | no (`true`) |
| `nextcloud_smtp_host` | Relay host; empty skips the whole mail pass | no (`""`) |
| `nextcloud_smtp_port` / `_smtp_secure` / `_mail_from_address` / `_mail_domain` | Outgoing mail details | when SMTP |
| `nextcloud_backup_enabled` / `_backup_keep_days` / `_backup_oncalendar` | Nightly dump timer | no (`true`, `3`, `02:30`) |
| `nextcloud_backup_metrics_dir` | Where the wrapper writes `nextcloud_backup.prom`; aliases `node_exporter_host_textfile_dir` | no |
| `nextcloud_backup_lib_path` | Where `compose_app`'s `write_prom_metrics` helper lands | no |
| `nextcloud_backup_nfs_enabled` / `_nfs_server` / `_nfs_export` / `_mountpoint` / `_nfs_options` | NFS-backed backup landing | server+export when enabled |
| `nextcloud_skip_install` | Render-only mode (alias: `skip_nextcloud_deploy`) | no (`false`) |
| `nextcloud_install_wait_retries` / `_install_wait_delay` | Readiness wait before `occ` runs — covers first install **and** the post-version-bump migration | no (`60`, `10s`) |

### Worked example

```yaml
external_domain: example.com          # -> cloud.example.com
internal_domain: example.internal     # -> cloud.example.internal
nextcloud_version: "34.0.1"
nextcloud_postgres_version: "18.4-trixie"
redis_version: "8.8.0-alpine"
nextcloud_exporter_version: "0.9.1"

# Only the ingress/proxy nodes may set X-Forwarded-For. Derive them from the
# inventory instead of hand-copying addresses:
nextcloud_nginx_real_ip_trusted_addresses: >-
  {{ (groups['k3s_servers'] + groups['k3s_agents'])
     | map('extract', hostvars, 'ansible_host') | list }}

nextcloud_oidc_enabled: true
nextcloud_oidc_discovery_uri: "https://auth.example.com/application/o/cloud/.well-known/openid-configuration"

nextcloud_smtp_host: smtp-relay.example.internal
nextcloud_mail_domain: example.com

nextcloud_backup_nfs_enabled: true
nextcloud_backup_nfs_server: fileserver.example.internal
nextcloud_backup_nfs_export: /backups-apps/nextcloud
```

## Client-IP trust chain

Behind a cluster ingress the pod source is SNAT'd to the node address before it
reaches this guest, so nginx trusts **only**
`nextcloud_nginx_real_ip_trusted_addresses` as `real_ip` sources, resolves the
true client from their `X-Forwarded-For`, and then **replaces** the header with
that single value. A directly connected client (a firewall exception for
debugging) is not a trusted source, so its inbound header is ignored.
Consequently the container only has to trust its immediate hop — loopback plus
the compose bridge gateway (`nextcloud_trusted_proxies`). Trusting a whole LAN
there would let a directly connected client forge the audited client IP.

## SSRF toggle (accepted risk)

`nextcloud_oidc_allow_local_remote_servers` sets `allow_local_remote_servers`,
without which Nextcloud refuses every server-side fetch to a private address —
including OIDC discovery whenever split-horizon DNS resolves the provider
internally. The toggle is **global**: Nextcloud has no per-URL allowlist, so it
widens every server-side fetch surface (federation "add remote share", the
`text` app's link previews), not just discovery. That is low risk while
provisioning is SSO-only and `files_external` is disabled; with untrusted
accounts, pair it with a default-deny egress policy on the guest.

The value is converged in both directions on every run, independent of
`nextcloud_oidc_enabled`: the guard is widened only when OIDC is on **and** the
toggle is `true`, so turning either off restores the guard on a host where an
earlier run widened it.

## Backups

The nightly wrapper dumps PostgreSQL from the db container, gzips on the host,
promotes only when **both** pipeline stages succeeded and the file is non-empty,
prunes older dumps after a success, and writes success/duration/size/timestamp
metrics. A failure preserves the previous success timestamp (so staleness
measures time-since-success) and reports the newest existing dump's size, so
"no artefact at all" stays distinguishable from "tonight's dump failed".

With `nextcloud_backup_nfs_enabled` the dump lands on a fileserver export
instead of the local volume, so a file-walking offsite job picks it up. Both the
systemd unit (`RequiresMountsFor`) and the wrapper (`mountpoint -q`) fail closed
if that export is not mounted. The default mount options require **kernel TLS**
(`xprtsec=tls`), which is why the server must be named by **hostname** — a
wildcard certificate has no IP SAN — and the guest needs the `nfs_tls` role.

## Testing

`molecule/default` runs with `nextcloud_skip_install: true`: no Docker daemon,
no `occ`. It asserts the rendered compose file, `.env` permissions, systemd
units, nginx site and backup wrapper, and runs the wrapper end to end against a
mocked `docker` on both the success and failure paths.
