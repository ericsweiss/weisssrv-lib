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
  the pinned `gitlab-ee` package, and its apt hold. A vendor key rotation is
  delete-the-keyring-first — see that role's README § Key rotation
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
- Git SSH: a `gitlab_ssh_port` -> 22 REDIRECT in both NAT chains, re-applied at
  boot by `gitlab-ssh-redirect.service`, plus a fail2ban jail and an sshd
  `AllowUsers` drop-in
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
| `gitlab_ssh_redirect_chains` | NAT chains carrying the managed REDIRECT rule | no (`PREROUTING`, `OUTPUT` on `lo`) |
| `gitlab_git_data_dir` | Repository storage root | no (Omnibus default) |
| `gitlab_additional_disks` | Extra block devices to mount first (aliases `vm_additional_disks`) | no (`[]`) |
| `gitlab_registry_enabled` / `_registry_external_url` / `_registry_data_dir` | Container Registry | no (`false`) |
| `gitlab_pages_enabled` / `_pages_external_url` | GitLab Pages | no (`false`) |
| `gitlab_smtp_enabled`, `gitlab_smtp_address`, `gitlab_smtp_port`, `gitlab_smtp_user`, `gitlab_smtp_password`, `gitlab_smtp_domain`, `gitlab_smtp_authentication`, `gitlab_smtp_enable_starttls_auto`, `gitlab_email_display_name` | SMTP relay and the From display name | no (`false`) |
| `gitlab_email_from` / `_display_name` / `_reply_to` | Notification identities; empty omits the line | no (`""`) |
| `gitlab_saml_enabled` + `_idp_sso_url` / `_idp_cert_fingerprint` | SAML SSO | no (`false`) |
| `gitlab_saml_label`, `gitlab_saml_icon_url`, `gitlab_saml_groups_attribute` | Sign-in button and claim mapping | no |
| `gitlab_saml_required_groups`, `gitlab_saml_admin_groups`, `gitlab_saml_external_groups` | Group-based access control | no (`[]`) |
| `gitlab_saml_allow_all_users` | Accept an empty `required_groups` deliberately | no (`false`) |
| `gitlab_nginx_listen_https`, `gitlab_nginx_listen_port`, `gitlab_nginx_ssl_certificate`, `gitlab_nginx_ssl_certificate_key`, `gitlab_nginx_ssl_protocols` | Web-UI TLS | no |
| `gitlab_nginx_real_ip_trusted_addresses` | Proxy CIDRs whose `X-Forwarded-For` is trusted. An empty list fails the play unless `gitlab_nginx_trust_no_proxy` is true | no (`[]`) |
| `gitlab_nginx_trust_no_proxy` | Accept an empty trust list, for an nginx reached directly | no (`false`) |
| `gitlab_monitoring_whitelist` | Sources allowed on the unauthenticated monitoring endpoints | no (`["127.0.0.1"]`) |
| `gitlab_postgres_exporter_enabled`, `gitlab_postgres_exporter_listen_address` | Omnibus's bundled `postgres_exporter`; empty keeps the Omnibus defaults. See [Monitoring endpoints](#monitoring-endpoints) | no (`""` / `""`) |
| `gitlab_bundled_prometheus_enabled` / `gitlab_bundled_alertmanager_enabled` | Omnibus's bundled Prometheus and Alertmanager; empty keeps them running. See [Monitoring endpoints](#monitoring-endpoints) | no (`""` / `""`) |
| `gitlab_backup_path`, `gitlab_backup_keep_time`, `gitlab_backup_skip` | Landing zone, retention seconds, `SKIP=` list | no |
| `gitlab_backup_nfs_enabled`, `gitlab_backup_nfs_server`, `gitlab_backup_nfs_export`, `gitlab_backup_nfs_options`, `gitlab_backup_mountpoint` | NFS-backed landing zone | no (`false`) |
| `gitlab_backup_oncalendar`, `gitlab_backup_timer_random_delay`, `gitlab_backup_service_timeout` | Backup schedule, timer jitter and run ceiling | no |
| `gitlab_effective_rails_config` | Rendered `gitlab.yml` the role reads back to confirm a reconfigure took | no (`/var/opt/gitlab/gitlab-rails/etc/gitlab.yml`) |
| `gitlab_backup_lib_path` / `gitlab_textfile_dir` | Metrics library path and textfile collector dir | no |
| `gitlab_puma_workers` / `gitlab_sidekiq_concurrency` | Sizing | no (`3` / `15`) |
| `gitlab_fail2ban_enabled` | Write the Git-SSH jail when fail2ban is installed | no (`true`) |
| `gitlab_ssh_allowusers_enabled` / `gitlab_ssh_allowed_users` | sshd login restriction | no (`false` / `[]`) |
| `gitlab_ssh_service_name` | sshd unit to restart | no (`ssh`) |
| `gitlab_kernel_tuning_enabled` | Redis sysctl + THP unit | no (`true`) |
| `gitlab_timezone` | Rails time zone (alias: `timezone`) | no (`UTC`) |
| `gitlab_web_ide_extension_host_domain` | Extension-host parent domain; setting it enables the settings pass | no (`""`) |
| `gitlab_web_ide_settings_enabled`, `gitlab_web_ide_marketplace_enabled`, `gitlab_web_ide_single_origin_fallback`, `gitlab_api_token` | Web IDE Application Settings | no |

## TLS

Omnibus's own Let's Encrypt client is hardcoded off: the certificate at
`gitlab_nginx_ssl_certificate`/`_key` is delivered by an external distributor
(`weisssrv.infra.acme_certs` in this collection), which reloads nginx with
`gitlab-ctl hup nginx`.

Registry and Pages nginx always terminate TLS with the same pair, regardless of
`gitlab_nginx_listen_https`. The role asserts both files exist before the
reconfigure. On a brand-new guest whose cert has not been pushed yet, set
`gitlab_nginx_listen_https`, `gitlab_registry_enabled` and `gitlab_pages_enabled`
false for the first deploy, then flip them back.

## Web IDE extension host

`gitlab_web_ide_extension_host_domain` must be a **different origin** from
`gitlab_external_url` so the browser's same-origin policy isolates extension
code from the GitLab session cookie (CVE-2026-5816). Set the bare parent
hostname — GitLab generates `<ext-id>.<domain>` per extension, and the
Application Settings API rejects a wildcard with HTTP 400. DNS, a wildcard
certificate and an ingress for those generated names are the site's to provide,
and the role probes `https://probe.<domain>/-/health` before it removes the
single-origin fallback.

Leaving the domain empty skips the settings pass entirely. The pass is API-only,
so it converges under `gitlab_skip_install` too.

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

- The secrets file gets its own metric pair because the tarball glob does not
  match it. Without them, nothing would notice a landing zone holding an
  un-restorable backup.
- Timestamps are not preserved on the copy, so its mtime is the freshness
  signal.

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

The rules survive a reboot through `gitlab-ssh-redirect.service`, a oneshot the
role owns that re-applies exactly the rules in `gitlab_ssh_redirect_chains`.
Saving the live ruleset instead would freeze fail2ban's active bans into
`/etc/iptables/rules.v4`, where they outlive their `bantime` and collide with
fail2ban's own chain setup on restart.

**Before the first deploy**, check that nothing else on the guest depends on
`netfilter-persistent` restoring `/etc/iptables/rules.v4` at boot. The role's
oneshot re-applies only the Git SSH redirect, so another service that relied on
that file for its own rules loses them on the next reboot.

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

gitlab_nginx_real_ip_trusted_addresses: [10.0.0.0/24, 10.42.0.0/16, 10.43.0.0/16]
gitlab_monitoring_whitelist: [127.0.0.1, 10.0.0.0/24, 10.42.0.0/16]

gitlab_backup_nfs_enabled: true
gitlab_backup_nfs_server: nas-01.example.internal
gitlab_backup_nfs_export: /backups-apps/gitlab
gitlab_backup_path: /mnt/backups-offsite   # must equal gitlab_backup_mountpoint

gitlab_ssh_allowusers_enabled: true
gitlab_ssh_allowed_users:
  - git
  - "admin@10.0.0.0/24"
  - "admin@100.64.0.0/10"   # the full Tailscale CGNAT range, not 100.64.*

gitlab_web_ide_extension_host_domain: ide.git.example.com
gitlab_api_token: "{{ lookup('ansible.builtin.env', 'GITLAB_API_TOKEN') }}"
```

## Monitoring endpoints

`/-/metrics`, `/-/readiness` and `/-/liveness` are unauthenticated and reachable
only from `gitlab_monitoring_whitelist`. GitLab matches the real IP from
`X-Forwarded-For`, so a probe behind the reverse proxy is matched on its own
source address. For an in-cluster probe that is the pod CIDR.

`prometheus_monitoring['enable']` is always true, because that is what serves
`/-/metrics` to an external scraper. It also starts Omnibus's own Prometheus
server and Alertmanager. Where the site already scrapes the guest and runs its
own alerting, set `gitlab_bundled_prometheus_enabled` and
`gitlab_bundled_alertmanager_enabled` to false; the data directory under
`/var/opt/gitlab/prometheus` is left behind and can be reclaimed by hand.

Omnibus's bundled `postgres_exporter` runs on `localhost:9187` by default and is
unauthenticated. Setting `gitlab_postgres_exporter_listen_address` to a routable
address publishes database metrics to every host the firewall lets through, so
scope it there.

## Redis prerequisites

Omnibus Redis needs two kernel settings Omnibus does not manage, both applied
when `gitlab_kernel_tuning_enabled` is true:

- `vm.overcommit_memory=1`. An RDB `BGSAVE` forks the server, and under the
  kernel default the copy-on-write child's allocation can be refused. That kills
  the child and trips `stop-writes-on-bgsave-error`, turning every GitLab write
  into a 500.
- Transparent Huge Pages off. Under `THP=always` a copy-on-write fault in the
  forked child can promote to a 2MB page and segfault inside libc. THP has no
  sysctl, so a systemd oneshot writes the sysfs file at boot.

## Package install

The install environment (`EXTERNAL_URL`, `GITLAB_ROOT_PASSWORD`) is staged in a
root-only file and sourced by the install shell, so the root password never
reaches `/proc/<pid>/cmdline`. Both apply to the package's first install only.

Downgrades are not enabled: Omnibus does not support them, because older code
against a newer schema can corrupt the database. A `gitlab_version` lower than
the installed one fails instead.

## Template safety

`gitlab.rb` is evaluated as Ruby by `gitlab-ctl reconfigure`, so the template
renders every operator-supplied value through its `rb()` macro. `to_json` covers
quote and backslash break-out, because JSON string escaping is also valid Ruby
escaping. It does not cover Ruby interpolation: `to_json` leaves `#` alone, so a
value containing `#{...}` would be evaluated at reconfigure time, and `ruby -c`
cannot catch that because the result is syntactically valid. The macro therefore
escapes every `#` as `\#` after `to_json`, which is a plain `#` inside a Ruby
double-quoted string. Arrays go through the same macro.

## Testing

Run the scenario as described in the collection README § Testing.

The scenario runs with `gitlab_skip_install: true` against a mocked GitLab tree,
so it covers rendering and the backup/firewall/SSH logic without the Omnibus
package. It sets `gitlab_web_ide_settings_enabled: false`, because the settings
pass needs a live instance.

It also drives both arms of the real-IP trust guard: an empty
`gitlab_nginx_real_ip_trusted_addresses` must fail the role, and the same empty
list must pass once `gitlab_nginx_trust_no_proxy` is set. Those deliberate
failures are declared in `molecule/default/expected-junit-failures.txt`.
