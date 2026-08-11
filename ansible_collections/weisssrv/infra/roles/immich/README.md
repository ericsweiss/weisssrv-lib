# weisssrv.infra.immich

Deploys the [Immich](https://immich.app) photo-management stack as a Docker
Compose project on a dedicated Debian guest, fronted by a host nginx that
terminates TLS. Persistent state lives on block devices attached to the guest
(`immich_additional_disks`, mounted by `weisssrv.infra.zvol_mount`); the shared
compose scaffolding is `weisssrv.infra.compose_app`.

## What it does

1. **Persistent storage** — mounts the attached volumes via `zvol_mount`
   (app / postgres / photo library).
2. **Docker Engine** (`compose_app` → `docker_engine`) — pinned, `dpkg`-held
   engine + compose plugin, journald log driver.
3. **Compose stack** — `immich-server`, `immich-machine-learning` (CPU),
   `database` (Immich's release-pinned Postgres image with the vector
   extensions) and `redis` (Valkey). The app binds loopback only; native
   Prometheus metrics are published on `immich_api_metrics_port` /
   `immich_microservices_metrics_port`, and an opt-in `postgres-exporter`
   sidecar adds database-level metrics. Lifecycle: `immich-compose.service`.
4. **System config** (`immich-config.json`) — the `IMMICH_CONFIG_FILE`
   declaring OIDC, `machineLearning.urls` and `backup.database.enabled: false`.
   With this file present, the admin UI settings become read-only.
5. **Host nginx** — terminates 443 (**TLS 1.3 only**), `client_max_body_size 0`,
   websockets, long upload timeouts, proxies to `127.0.0.1:2283`. Seeds a
   self-signed placeholder so a fresh guest starts before the first cert push.
6. **Backups** — nightly `pg_dumpall` with node_exporter textfile metrics
   (`immich_backup_*`) via the shared `write_prom_metrics` helper.

## Required inputs

| Variable | Meaning |
|---|---|
| `immich_version` | Immich release tag (server + CPU ML images) |
| `immich_postgres_version` / `immich_postgres_digest` | Immich's release-coupled Postgres image — take the tag from the pinned release's own `docker-compose.yml`, never bump it independently |
| `immich_valkey_version` / `immich_valkey_digest` | Valkey image pin |
| `immich_external_url` | canonical `https://` URL (`server.externalDomain`) |
| `immich_oauth_issuer_url` | OIDC issuer (the provider's application URL) |
| `immich_db_password` | Postgres password; defaults to `$IMMICH_DB_PASSWORD` |
| `immich_oauth_client_id` / `immich_oauth_client_secret` | OIDC client; default to `$IMMICH_OAUTH_CLIENT_ID` / `$IMMICH_OAUTH_CLIENT_SECRET` |

All seven non-secret inputs are asserted at the top of the role; the three
credentials are asserted `no_log`.

## Parameters

| Variable | Meaning | Default |
|---|---|---|
| `immich_skip_install` | Render-only: skip Docker/nginx install and all service management | `false` |
| `immich_additional_disks` | Volumes to mount first (`zvol_mount` schema) | `vm_additional_disks` |
| `immich_app_dir` / `immich_db_data_location` / `immich_data_dir` | Mount points for the compose+config volume, Postgres, and the library | `/mnt/immich-app`, `/mnt/immich-postgres`, `/mnt/immich-data` |
| `immich_upload_location` | Library dir inside the data volume | `<data_dir>/library` |
| `immich_machine_learning_image` | In-guest CPU ML image | `…/immich-machine-learning:<version>` |
| `immich_ml_urls` | `machineLearning.urls`, tried in order | in-guest CPU container only |
| `immich_compose_subnet` / `immich_compose_gateway` | Fixed compose bridge (the gateway is `IMMICH_TRUSTED_PROXIES`) | `172.28.0.0/16`, `172.28.0.1` |
| `immich_db_username` / `immich_db_database_name` | Postgres superuser + database | `postgres`, `immich` |
| `immich_telemetry_include`, `immich_api_metrics_port`, `immich_microservices_metrics_port` | Native Prometheus telemetry | `all`, `8081`, `8082` |
| `immich_postgres_exporter_enabled` | Add the `postgres-exporter` sidecar (DB-level metrics) | `false` |
| `immich_postgres_exporter_version` / `_digest` / `_image` | Its image pin; the image derives from the version + optional digest, or override it outright | `""`, `""`, `quay.io/prometheuscommunity/postgres-exporter:<version>` |
| `immich_postgres_exporter_port` | Host port for the exporter (unauthenticated — scope it at the firewall) | `9187` |
| `immich_oauth_scope` / `_button_text` / `_storage_label_claim` | OIDC presentation + claims | `openid email profile`, `Sign in with SSO`, `preferred_username` |
| `immich_oauth_auto_register` / `_auto_launch` | Provision on first login / skip the login page | `true` / `true` |
| `immich_oauth_default_storage_quota` | Per-user quota in GiB for new accounts; empty = unlimited | `""` |
| `immich_bootstrap_mode` | One-time password-login escape hatch (see below) | `false` |
| `immich_builtin_db_backup_enabled` | Immich's own nightly dump (off: the timer below is the single path) | `false` |
| `immich_server_listen_port` | Loopback port nginx proxies to | `2283` |
| `immich_nginx_ssl_cert` / `_key` | Cert material the site's distribution writes | `/etc/nginx/ssl/{fullchain,privkey}.pem` |
| `immich_nginx_self_signed_subj` / `_san` | Placeholder identity until that first push | `/CN=<inventory_hostname>` / none |
| `immich_nginx_real_ip_groups` | Inventory groups whose members are trusted proxies | `[k3s_servers, k3s_agents]` |
| `immich_nginx_real_ip_from` | Resolved trust list — override to set addresses directly | derived from the groups |
| `immich_timezone` | Container `TZ` | `timezone` or `UTC` |
| `immich_backup_hour` / `_minute` / `_keep_days` | Dump schedule + local retention | `02:30`, `3` |
| `immich_backup_metrics_dir` / `immich_backup_lib_path` | textfile dir + sourced metrics helper | `node_exporter_host_textfile_dir`, `/usr/local/lib/immich-backup-lib.sh` |
| `immich_backup_nfs_enabled` / `_server` / `_export` / `_options` / `_mountpoint` | Offsite landing zone (below) | `false`, `""`, `""`, `vers=4.2,…,xprtsec=tls`, `/mnt/backups-offsite` |
| `immich_backup_dir` | Resolved landing dir | the mountpoint when NFS-backed, else `<app_dir>/backups` |

## Real client IP

nginx walks `X-Forwarded-For` back past every address in
`immich_nginx_real_ip_from`, so that list must contain **only** genuine proxy
hops. Trusting a range that can also connect directly (an admin LAN, say) lets
such a client forge the client IP Immich logs and rate-limits on. The default
derives the list from inventory —

```yaml
immich_nginx_real_ip_from: >-
  {{ immich_nginx_real_ip_groups | map('extract', groups) | select('defined')
     | flatten | map('extract', hostvars, 'ansible_host')
     | select('defined') | list }}
```

— so a renamed or renumbered proxy node cannot silently drop out. When no listed
group exists the list is empty and no `set_real_ip_from` is emitted: every
client then appears as the proxy's address, which is quiet rather than loud.

## Offsite backup landing

With `immich_backup_nfs_enabled`, the nightly dump lands on an NFS export
instead of the app volume, so a file-walking offsite backup can see the compact
logical dump (a file walk cannot read a block-device volume). Two rules:

- **Mount by hostname** when the export requires TLS (`xprtsec=tls`, the default
  option string). A wildcard certificate has no IP SAN, so an IP mount fails the
  handshake. The guest also needs a running tlshd — `weisssrv.infra.nfs_tls`.
- The wrapper **fails closed**: if the landing dir is NFS-backed but not
  mounted, it records `success=0` and exits non-zero *before* dumping, rather
  than writing to the mountpoint's underlying directory.

## SSO bootstrap (one-time)

The first Immich account (admin/owner) must be created through the password
Admin-Registration page — OIDC auto-register only ever creates regular users.
For the very first deploy set `immich_bootstrap_mode: true` (password login on,
OIDC auto-launch off), then register the admin **before** the instance is
publicly reachable, since Immich makes the first registered account the owner.
Use the same email as the SSO account, then flip back to `false` and redeploy
for SSO-only; OIDC links to the admin user by email.

## Molecule

`molecule/default` is a render/contract scenario: `immich_skip_install: true`
skips the Docker/nginx install and all service management, so the compose file,
system config (both the SSO-only and bootstrap branches), `.env`, nginx site
(including the inventory-derived real-IP trust list, seeded with fixture proxy
hosts), systemd units and the backup wrapper (success + failure metric paths,
against a mocked `docker`) are rendered and asserted without a container
runtime.

## Related

- `weisssrv.infra.immich_ml` — GPU inference endpoint to put first in
  `immich_ml_urls`.
- `weisssrv.infra.compose_app` — Docker install, compose systemd unit, nginx
  task flow, `write_prom_metrics` helper.
- `weisssrv.infra.zvol_mount` — mounts the attached volumes.
- `weisssrv.infra.acme_certs` — distributes the real certificate to
  `immich_nginx_ssl_cert`.
- `weisssrv.infra.nfs_tls` — tlshd, required for the TLS-mounted backup landing.
- `weisssrv.infra.node_exporter_host` — scrapes the backup textfile metrics.
