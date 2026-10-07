# weisssrv.infra.compose_app

Shared scaffolding for single-project **docker-compose app guests** (a VM or LXC
running one compose project behind host nginx). It owns the parts such app roles
otherwise duplicate, while every app-specific detail stays a parameter — or
stays in the caller's role.

This role is **not** run standalone: an app role includes the task file it needs
at the right point in its own flow.

## What it shares

| Seam | File | How the caller uses it |
|---|---|---|
| Docker Engine | `tasks/docker.yml` | `tasks_from: docker.yml` — installs `weisssrv.infra.docker_engine` (gated). |
| Compose unit | `tasks/main.yml` + `templates/compose.service.j2` | default entry point — deploys `<name>.service`, enables, flushes handlers, starts. |
| Backup metrics | `tasks/backup_lib.yml` + `templates/write_prom_metrics.sh.j2` | `tasks_from: backup_lib.yml` — deploys the sourceable `write_prom_metrics` lib. |
| Host nginx | `tasks/nginx.yml` | `tasks_from: nginx.yml` — install/cert/validate+deploy-site/reload. |

`meta: flush_handlers` is play-scoped, so including `tasks/main.yml` flushes
every handler pending in the play, not only this role's. A caller must have its
own config tasks (and the notifies they queue) complete before including it.

One compose systemd unit template serves every guest, with `RemainAfterExit`
standardised to `yes` (systemd treats `yes` and `true` identically). The nginx
`.conf` stays in the caller's role (only the **task flow** is shared) — the site
template is passed in by absolute path, which the caller must capture eagerly
with `set_fact`, because `role_path` inside an `include_role`'s `vars:` resolves
to the *included* role.

### nginx: validated before install

The site is rendered to a temp dir and checked with `nginx -t` against a copy of
`nginx.conf` whose `sites-enabled` include points at that temp dir; only a
candidate that passes is copied into `sites-available`. Validating after install
would leave a broken `.conf` enabled, so the next nginx start (a reboot, or a
coordinated reboot controller) could not bring the guest back. A whole-config
`nginx -t` still runs after the site is enabled and the default vhost removed.

Under `compose_app_skip_install` the validation step is skipped (no nginx
binary), but the candidate is still rendered and installed.

### Backup metrics library

`tasks/backup_lib.yml` installs the backup-freshness metric emitter used by the
compose app guests: `gitlab`, `immich` and `nextcloud` include it. Roles whose
wrappers emit a richer or differently-shaped metric set (`restic_offsite`,
`nas_storage`, `k3s`, `acme_certs`) keep their own emitter. The library is
sourced, and everything variable is a call argument:

```bash
source /usr/local/lib/<app>-backup-lib.sh
write_prom_metrics <prefix> <success> <duration> <size> <prom_file> [artifact_glob]
require_mounted_landing_zone <prefix> <path> <nfs_enabled> <prom_file>
```

- On failure it re-emits the previous `_last_success_timestamp_seconds`, so
  staleness alerts measure time-since-last-success.
- When `size` is 0 and `artifact_glob` is given, the newest existing artefact is
  sized instead, so `_last_size_bytes 0` keeps one meaning: no artefact exists at
  all. A failed run with good artefacts still on disk is carried by
  `_last_run_success 0`.
- Only the size-gauge HELP noun (`compose_app_backup_size_help_object`) is
  rendered per app.
- `require_mounted_landing_zone` is the fail-closed gate the three wrappers open
  with. When the landing zone is NFS-backed and not mounted it writes
  `<prefix>_last_run_success 0` and returns 1, so the artefact never lands on the
  root disk un-offsited; otherwise it returns 0 and writes nothing.
- A `.prom` write or rename that fails never fails the backup, but it does log
  to `daemon.err` under the tag `<prefix>-metrics`. The old file survives, so
  the previous run's `_last_run_success 1` stays published until the next
  successful write.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `compose_app_skip_install` | `false` | Render-only mode: skip every step needing a container runtime, systemd service management or the nginx binary. Callers pass their own flag through. |
| `compose_app_service_name` | `compose` | Unit basename (`<name>.service`). |
| `compose_app_description` | `Docker Compose stack` | Unit `Description=`. |
| `compose_app_working_directory` | `/opt/compose` | Unit `WorkingDirectory=` (where the compose project lives). |
| `compose_app_requires_mounts_for` | `[]` | List for `RequiresMountsFor=`; **empty omits the line and its comment**. |
| `compose_app_requires_mounts_comment` | `[]` | Comment lines rendered above `RequiresMountsFor=`. |
| `compose_app_reconcile_comment` | `[]` | Comment lines rendered above `ExecStart=`. |
| `compose_app_remain_after_exit` | `"yes"` | Unit `RemainAfterExit=`. |
| `compose_app_backup_lib_dest` | `/usr/local/lib/compose-app-backup-lib.sh` | Where the sourceable metrics library is installed. |
| `compose_app_backup_size_help_object` | `dump` | Noun in the size-gauge HELP line. |
| `compose_app_nginx_cert_dir` | `/etc/nginx/ssl` | TLS material directory. |
| `compose_app_nginx_cert_dir_mode` | `"0750"` | Mode of that directory. |
| `compose_app_nginx_ssl_cert` | `/etc/nginx/ssl/fullchain.pem` | Cert path the site references. |
| `compose_app_nginx_ssl_key` | `/etc/nginx/ssl/privkey.pem` | Key path (forced to `0600`). |
| `compose_app_nginx_site_name` | `site.conf` | Filename under `sites-available`/`sites-enabled`. |
| `compose_app_nginx_site_template` | `""` | **Absolute** path to the caller's site template. |
| `compose_app_nginx_bootstrap_cert` | `true` | Seed the self-signed placeholder + secure its key. |
| `compose_app_nginx_self_signed_days` | `3650` | Placeholder cert lifetime. |
| `compose_app_nginx_self_signed_subj` | `/CN={{ inventory_hostname }}` | Placeholder subject — replaced by the real cert on first distribution. |
| `compose_app_nginx_self_signed_san` | `""` | Falsy ⇒ no `-addext`; a value ⇒ `-addext "subjectAltName=<value>"`. |

A site that terminates TLS for a wildcard hostname should set
`compose_app_nginx_self_signed_subj` / `_san` to that name, e.g.
`/CN=app.example.com` and `DNS:*.example.com`.

## Molecule

`molecule/default` is a contract scenario. Under `compose_app_skip_install:
true` it renders the compose unit for **both** the `RequiresMountsFor`-present
and `-absent` cases, renders the `write_prom_metrics` lib and runs it end-to-end
(success, failure/preserve-timestamp, newest-artefact fallback, unwritable
path), and runs the host-nginx flow to assert the `creates:` cert guard, the
deployed + enabled site and the removed default vhost.

It then installs nginx and runs the host-nginx flow again with validation on: a
valid candidate must pass `nginx -t` and land in `sites-available`, and a
candidate with an unknown directive must fail the play and leave the installed
site untouched. `expected-junit-failures.txt` declares that deliberate failure.

## Related

- `weisssrv.infra.docker_engine` — the pinned Docker Engine install (included
  from `tasks/docker.yml`).
