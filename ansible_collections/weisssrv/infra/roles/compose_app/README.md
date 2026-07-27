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
| Compose unit | `tasks/main.yml` + `templates/compose.service.j2` | default entry point — deploys `<name>.service`, enables, flushes, starts. |
| Backup metrics | `tasks/backup_lib.yml` + `templates/write_prom_metrics.sh.j2` | `tasks_from: backup_lib.yml` — deploys the sourceable `write_prom_metrics` lib. |
| Host nginx | `tasks/nginx.yml` | `tasks_from: nginx.yml` — install/cert/deploy-site/`nginx -t`/reload. |

One compose systemd unit template serves every guest, with `RemainAfterExit`
standardised to `yes` (systemd treats `yes` and `true` identically). The
backup-metrics `.prom` output is per-app only in its metric-name prefix, `.prom`
path and the size-gauge HELP noun. The nginx `.conf` stays in the caller's role
(only the **task flow** is shared) — the site template is passed in by absolute path
(captured eagerly with `set_fact` in the caller, since `role_path` in an
`include_role`'s `vars:` resolves to the *included* role).

## Key parameters

- `compose_app_skip_install` — the caller's own render-only flag.
- `compose_app_service_name`, `compose_app_description`,
  `compose_app_working_directory` — the compose unit.
- `compose_app_requires_mounts_for` (list; **empty ⇒ the unit omits
  `RequiresMountsFor` and its comment**), `compose_app_requires_mounts_comment`,
  `compose_app_reconcile_comment` — the per-app comment/mount lines, passed so
  the rendered unit stays byte-identical.
- `compose_app_backup_lib_dest`, `compose_app_backup_size_help_object` — the
  helper library.
- `compose_app_nginx_*` — cert dir/paths/mode, self-signed placeholder subj/SAN/
  days, site name + template path, and `compose_app_nginx_bootstrap_cert`.

Full defaults + inline docs: `defaults/main.yml`.

## Molecule

`molecule/default` is a render/contract scenario (`compose_app_skip_install:
true`): it renders the compose unit for **both** the `RequiresMountsFor`-present
and `-absent` cases, renders the `write_prom_metrics` lib and runs it end-to-end
(success + failure/preserve-timestamp), and asserts the emitted metric series.

## Related

- `weisssrv.infra.docker_engine` — the pinned Docker Engine install (included
  from `tasks/docker.yml`).
