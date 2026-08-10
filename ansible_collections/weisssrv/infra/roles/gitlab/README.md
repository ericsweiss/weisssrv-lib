# weisssrv.infra.gitlab

Installs and configures GitLab EE (Omnibus) on a dedicated Debian guest: TLS on
an externally distributed certificate, optional Container Registry, Pages, SMTP
and SAML SSO, a metered daily backup, and hardening for the WAN-exposed Git SSH
port.

Site data is always an input. Every optional block is off by default and asserts
its own inputs when switched on, so a half-configured feature fails at the top
of the play rather than mid-Chef-run.

## What it manages

- the fingerprint-verified GitLab EE apt repo (via `weisssrv.infra.apt_signed_repo`),
  the pinned `gitlab-ee` package, and its apt hold
- `/etc/gitlab/gitlab.rb`, syntax-checked with the Omnibus embedded ruby before
  it lands, plus a convergence guard that re-runs `gitlab-ctl reconfigure` when
  the rendered Rails config disagrees with it
- optional repository-storage and registry-blob-store relocation onto a
  dedicated volume (the blob store is moved once, then reconfigure repoints it)
- the Redis kernel prerequisites: `vm.overcommit_memory=1` and a systemd oneshot
  that disables Transparent Huge Pages
- `gitlab-backup.timer`/`.service` plus `/usr/local/sbin/gitlab-backup-run.sh`,
  which emits node_exporter textfile metrics
- an optional NFS-backed backup landing zone, mounted and fail-closed guarded
- Git SSH: a `gitlab_ssh_port` -> 22 REDIRECT in both NAT chains, a fail2ban
  jail, and an sshd `AllowUsers` drop-in
- the Web IDE extension-host Application Settings (API-driven; no Omnibus key
  exists for them on the pinned release)

Ordering is the playbook's job — run a base role (SSH, packages, users) and a
local mail relay first. The role does not install fail2ban or node_exporter; it
writes into both when they are present.

## Variables

| Variable | Meaning | Required |
|---|---|---|
| `gitlab_version` | Pinned `gitlab-ee` package version | yes (unless skipping install) |
| `gitlab_external_url` | Canonical `https://` URL | yes |
| `gitlab_root_password` | Initial root password (secret) | yes |
| `gitlab_skip_install` | Converge host config without touching the package | no (`false`) |
| `gitlab_ssh_host` / `gitlab_ssh_port` | Clone-URL host and port | no (host derived from the external URL; port `2222`) |
| `gitlab_git_data_dir` | Repository storage root | no (Omnibus default) |
| `gitlab_additional_disks` | Extra block devices to mount first (aliases `vm_additional_disks`) | no (`[]`) |
| `gitlab_registry_enabled` / `_registry_external_url` / `_registry_data_dir` | Container Registry | no (`false`) |
| `gitlab_pages_enabled` / `_pages_external_url` | GitLab Pages | no (`false`) |
| `gitlab_smtp_enabled` + `_address` / `_port` / `_user` / `_password` / `_domain` / `_authentication` / `_enable_starttls_auto` | SMTP relay | no (`false`) |
| `gitlab_email_from` / `_display_name` / `_reply_to` | Notification identities; empty omits the line | no (`""`) |
| `gitlab_saml_enabled` + `_idp_sso_url` / `_idp_cert_fingerprint` | SAML SSO | no (`false`) |
| `gitlab_saml_label` / `_icon_url` / `_groups_attribute` | Sign-in button and claim mapping | no |
| `gitlab_saml_required_groups` / `_admin_groups` / `_external_groups` | Group-based access control | no (`[]`) |
| `gitlab_saml_allow_all_users` | Accept an empty `required_groups` deliberately | no (`false`) |
| `gitlab_nginx_listen_https` / `_listen_port` / `_ssl_certificate` / `_ssl_certificate_key` / `_ssl_protocols` | Web-UI TLS | no |
| `gitlab_nginx_real_ip_trusted_addresses` | Proxy CIDRs whose `X-Forwarded-For` is trusted; empty omits the directive | no (`[]`) |
| `gitlab_monitoring_whitelist` | Sources allowed on the unauthenticated monitoring endpoints | no (`["127.0.0.1"]`) |
| `gitlab_postgres_exporter_enabled` / `_listen_address` | Omnibus's bundled `postgres_exporter`; **empty omits the line**, leaving the Omnibus defaults (on, `localhost:9187`). Set the listen address (e.g. `0.0.0.0:9187`) to publish unauthenticated DB metrics — scope them at the firewall | no (`""` / `""`) |
| `gitlab_backup_path` / `_keep_time` / `_skip` | Landing zone, retention, `SKIP=` list | no |
| `gitlab_backup_nfs_enabled` + `_nfs_server` / `_nfs_export` / `_nfs_options` / `_mountpoint` | NFS-backed landing zone | no (`false`) |
| `gitlab_backup_oncalendar` / `_timer_random_delay` / `_service_timeout` | Backup schedule and ceiling | no |
| `gitlab_backup_lib_path` / `gitlab_textfile_dir` | Metrics library path and textfile collector dir | no |
| `gitlab_puma_workers` / `gitlab_sidekiq_concurrency` | Sizing | no (`3` / `15`) |
| `gitlab_fail2ban_enabled` | Write the Git-SSH jail when fail2ban is installed | no (`true`) |
| `gitlab_ssh_allowusers_enabled` / `gitlab_ssh_allowed_users` | sshd login restriction | no (`false` / `[]`) |
| `gitlab_ssh_service_name` | sshd unit to restart | no (`ssh`) |
| `gitlab_kernel_tuning_enabled` | Redis sysctl + THP unit | no (`true`) |
| `gitlab_timezone` | Rails time zone (alias: `timezone`) | no (`UTC`) |
| `gitlab_web_ide_extension_host_domain` | Extension-host parent domain; setting it enables the settings pass | no (`""`) |
| `gitlab_web_ide_settings_enabled` / `_marketplace_enabled` / `_single_origin_fallback` / `gitlab_api_token` | Web IDE Application Settings | no |

## TLS

Omnibus's own Let's Encrypt client is hardcoded off: the certificate at
`gitlab_nginx_ssl_certificate`/`_key` is delivered by an external distributor
(`weisssrv.infra.acme_certs` in this collection), which reloads nginx with
`gitlab-ctl hup nginx`.

Registry and Pages nginx **always** terminate TLS with the same pair, regardless
of `gitlab_nginx_listen_https`. The role asserts both files exist before the
reconfigure, so a brand-new guest whose cert has not been pushed yet must set
`gitlab_nginx_listen_https`, `gitlab_registry_enabled` and `gitlab_pages_enabled`
all false for the first deploy, then flip them back.

## Web IDE extension host

`gitlab_web_ide_extension_host_domain` must be a **different origin** from
`gitlab_external_url` so the browser's same-origin policy isolates extension
code from the GitLab session cookie (CVE-2026-5816). Set the bare parent
hostname — GitLab generates `<ext-id>.<domain>` per extension, and the
Application Settings API rejects a wildcard with HTTP 400. DNS, a wildcard
certificate and an ingress for those generated names are the site's to provide,
and the role probes `https://probe.<domain>/-/health` before it removes the
single-origin fallback.

Leaving the domain empty skips the settings pass entirely.

## Backups

`gitlab-backup.timer` runs `/usr/local/sbin/gitlab-backup-run.sh`, which:

1. refuses to run when the landing zone is NFS-backed but not mounted (writing
   into an unmounted mountpoint would put the tarball on the root disk,
   un-offsited and filling that disk);
2. runs `gitlab-backup create CRON=1 SKIP=<gitlab_backup_skip>`;
3. copies `gitlab-secrets.json` + `gitlab.rb` alongside the tarball on success —
   without them a restore cannot decrypt CI variables, 2FA or runner tokens, so
   a copy failure demotes the run to `success=0`;
4. writes textfile metrics through the shared `write_prom_metrics` library
   (`weisssrv.infra.compose_app`, `tasks_from: backup_lib.yml`), so the metric
   shape is identical across every backup wrapper in the collection.

| Metric | Meaning |
|---|---|
| `gitlab_backup_last_run_success` | 1/0 for the last run |
| `gitlab_backup_last_run_duration_seconds` | Duration of the last run |
| `gitlab_backup_last_success_timestamp_seconds` | Preserved across failures, so staleness measures time-since-last-**success** |
| `gitlab_backup_last_size_bytes` | Newest tarball in the landing zone (0 = none at all) |
| `gitlab_backup_secrets_present` | 1/0 for `gitlab-secrets.json` in the landing zone |
| `gitlab_backup_secrets_size_bytes` | Its size (0 = absent) |

The secrets file gets its own pair because the tarball glob does not match it,
so nothing else would notice a landing zone holding an un-restorable backup.
Timestamps are deliberately **not** preserved on the copy, which makes its mtime
a freshness signal.

For any of this to be scraped, a node_exporter with the textfile collector
pointed at `gitlab_textfile_dir` must run on the guest.

`SKIP=registry,artifacts` is the default scope: the registry blob store and CI
artifacts dominate the tarball and are both reproducible, so a restore does not
bring them back. Clear `gitlab_backup_skip` only with a dedicated, off-root-disk
landing zone.

## Git SSH

`gitlab_ssh_port` is advertised in clone URLs and REDIRECTed to 22 in both
`nat/PREROUTING` and `nat/OUTPUT`, so Git SSH terminates on the **system sshd**.
Two consequences the role handles:

- every local account would otherwise accept internet pubkey attempts on that
  port, bypassing whatever source restriction the firewall applies to 22 — hence
  `gitlab_ssh_allowed_users`, installed with `sshd -t` validation;
- the redirect must be exactly one rule per chain, so the role deletes drifted
  variants (legacy `-m comment` rules, an OUTPUT rule missing `-o lo`) by line
  number before re-adding the managed rule.

## Worked example

```yaml
gitlab_version: "19.2.1-ee.0"
gitlab_external_url: "https://git.example.com"
gitlab_root_password: "{{ lookup('ansible.builtin.env', 'GITLAB_ROOT_PASSWORD') }}"
gitlab_timezone: America/Los_Angeles

gitlab_git_data_dir: /mnt/gitlab-repos/git-data
gitlab_registry_enabled: true
gitlab_registry_external_url: "https://registry.git.example.com"
gitlab_registry_data_dir: /mnt/gitlab-repos/registry
gitlab_pages_enabled: true
gitlab_pages_external_url: "https://pages.git.example.com"

gitlab_smtp_enabled: true
gitlab_smtp_address: smtp-relay.example.internal
gitlab_smtp_user: "{{ lookup('ansible.builtin.env', 'SMTP_RELAY_USER') }}"
gitlab_smtp_password: "{{ lookup('ansible.builtin.env', 'SMTP_RELAY_PASSWORD') }}"
gitlab_smtp_domain: example.com
gitlab_email_from: gitlab@example.com
gitlab_email_reply_to: noreply@example.com

gitlab_saml_enabled: true
gitlab_saml_label: Authentik
gitlab_saml_icon_url: "https://auth.example.com/static/dist/assets/icons/icon.svg"
gitlab_saml_idp_sso_url: "https://auth.example.com/application/saml/git/sso/binding/redirect/"
gitlab_saml_idp_cert_fingerprint: "{{ lookup('ansible.builtin.env', 'GITLAB_SAML_CERT_FINGERPRINT') }}"
gitlab_saml_required_groups: [gitlab-users, gitlab-admins]
gitlab_saml_admin_groups: [gitlab-admins]

gitlab_nginx_real_ip_trusted_addresses: [192.168.0.0/24, 10.42.0.0/16, 10.43.0.0/16]
gitlab_monitoring_whitelist: [127.0.0.1, 192.168.0.0/24, 10.42.0.0/16]

gitlab_backup_nfs_enabled: true
gitlab_backup_nfs_server: nas-01.example.internal
gitlab_backup_nfs_export: /backups-apps/gitlab
gitlab_backup_path: /mnt/backups-offsite   # must equal gitlab_backup_mountpoint

gitlab_ssh_allowusers_enabled: true
gitlab_ssh_allowed_users:
  - git
  - "admin@192.168.0.0/24"
  - "admin@100.64.0.0/10"   # the full Tailscale CGNAT range, not 100.64.*

gitlab_web_ide_extension_host_domain: ide.git.example.com
gitlab_api_token: "{{ lookup('ansible.builtin.env', 'GITLAB_API_TOKEN') }}"
```

A monitoring probe that runs **behind** the reverse proxy is matched on its own
source address (GitLab reads the real IP from `X-Forwarded-For`), so its network
must be in `gitlab_monitoring_whitelist` — for an in-cluster probe that is the
pod CIDR, whose exposure ceiling is unauthenticated `/-/metrics` to in-cluster
workloads.

## Testing

```bash
cd roles/gitlab
molecule -c ../../molecule-shared/base.yml test
```

The scenario runs with `gitlab_skip_install: true` against a mocked GitLab tree,
so it covers rendering and the backup/firewall/SSH logic without the Omnibus
package.
